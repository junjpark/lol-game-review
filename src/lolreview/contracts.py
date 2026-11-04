"""Analysis contracts: the question, metric and pass rule are written down before the run.

An analysis (a feature test, a "does X predict Y" check) starts as a doc in docs/analyses/ with a
fenced ```contract block of `key: value` lines (template: docs/analyses/TEMPLATE.md). The analysis
script loads it with `load()` and writes `stamp()` into its results file. Then
`scripts/check_analysis.py` (code) and the stats-reviewer agent (judgement) review the run.

What this enforces in code, so a reviewer doesn't have to remember it:
- every required field is filled in;
- uncertainty is resampled by game (or coarser), never by row or minute;
- no fresh holdout set is used (a corpus with "holdout" in its name that the current model
  didn't train on): those are reserved for the one-time release check;
- the contract the results were produced under is the one on disk now (hash), so the pass rule
  can't quietly move after the numbers are in.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

REQUIRED = ("question", "hypothesis", "metric", "pass_if", "data", "resample_unit", "script", "results")
RESAMPLE_OK = {"game", "match", "player"}
_BLOCK = re.compile(r"```contract\s*\n(.*?)```", re.S)


class ContractError(ValueError):
    pass


def parse(text: str) -> tuple[dict, str]:
    m = _BLOCK.search(text)
    if not m:
        raise ContractError("no ```contract block found")
    block = m.group(1)
    fields: dict[str, str] = {}
    for line in block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ContractError(f"not a 'key: value' line: {line!r}")
        k, v = line.split(":", 1)
        fields[k.strip()] = v.strip()
    return fields, block


def protected_corpora(processed_dir: Path, model_dir: Path) -> set[str]:
    """Fresh holdout sets: corpora named *holdout* that the current model didn't train on."""
    trained: set[str] = set()
    meta = Path(model_dir) / "winprob_meta.json"
    if meta.exists():
        trained = {c.lower() for c in ((json.loads(meta.read_text()).get("dataset") or {}).get("corpora") or [])}
    names = {p.stem.removeprefix("matches_").lower() for p in Path(processed_dir).glob("matches_*.json")}
    return {n for n in names if "holdout" in n and n not in trained}


def load(path: str | Path, processed_dir: Path | None = None, model_dir: Path | None = None) -> dict:
    """Parse and validate a contract. Returns the fields plus `_sha256` and `_path`."""
    from .config import MODEL_DIR, PROCESSED_DIR

    path = Path(path)
    fields, block = parse(path.read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED if not fields.get(k)]
    if missing:
        raise ContractError(f"{path.name}: missing {', '.join(missing)}")
    unit = fields["resample_unit"].lower()
    if unit not in RESAMPLE_OK:
        raise ContractError(f"{path.name}: resample_unit '{unit}' — resample by game (or match/player), "
                            "never by row or minute: minutes of one game share one outcome")
    data = {d.strip().lower() for d in re.split(r"[,\s]+", fields["data"]) if d.strip()}
    hit = data & protected_corpora(processed_dir or PROCESSED_DIR, model_dir or MODEL_DIR)
    if hit:
        raise ContractError(f"{path.name}: uses fresh holdout set(s) {sorted(hit)}; those are only for "
                            "the one-time release check (scripts/check_holdout.py)")
    fields["_sha256"] = hashlib.sha256(block.strip().encode("utf-8")).hexdigest()
    fields["_path"] = str(path)
    return fields


def stamp(contract: dict) -> dict:
    """Put this in the results file: which contract, exactly, the numbers were produced under."""
    return {"path": contract["_path"], "sha256": contract["_sha256"], "pass_if": contract["pass_if"],
            "metric": contract["metric"]}


def verify(doc: str | Path, results: dict, **dirs) -> list[str]:
    """Problems with a finished run (empty list = the run matches its contract)."""
    problems: list[str] = []
    try:
        c = load(doc, **dirs)
    except ContractError as exc:
        return [str(exc)]
    s = results.get("contract") or {}
    if not s:
        problems.append("results carry no contract stamp (call contracts.stamp() in the script)")
    elif s.get("sha256") != c["_sha256"]:
        problems.append("the contract changed after these results were produced (hash mismatch): "
                        f"results say pass_if '{s.get('pass_if')}', the doc now says '{c['pass_if']}'")
    if "passed" not in results:
        problems.append("results have no 'passed' field: the script must evaluate pass_if itself")
    return problems
