"""A holdout is evaluated once (docs/model-history.md). This module enforces it.

Re-checking the same games after changing the model turns the holdout into training signal, so
`check_holdout.py` refuses a set that was already used unless the owner gives a reason. A reuse is
recorded and can never release a model.

A set counts as used if it's in data/processed/holdouts_used.json, or if the current model or any
archived model's metadata names it as its holdout (covers checks run before this lock existed).
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

USED_FILE = "holdouts_used.json"


class HoldoutAlreadyUsed(RuntimeError):
    pass


def _meta_holdout_names(model_dir: Path) -> set[str]:
    names = set()
    for path in [model_dir / "winprob_meta.json", *sorted((model_dir / "archive").glob("*/winprob_meta.json"))]:
        try:
            h = json.loads(path.read_text()).get("holdout") or {}
        except (OSError, ValueError):
            continue
        if h.get("name"):
            names.add(str(h["name"]).lower())
    return names


def load_used(processed_dir: Path) -> dict:
    path = Path(processed_dir) / USED_FILE
    return json.loads(path.read_text()) if path.exists() else {}


def used_names(processed_dir: Path, model_dir: Path) -> set[str]:
    return set(load_used(processed_dir)) | _meta_holdout_names(Path(model_dir))


def check_unused(name: str, processed_dir: Path, model_dir: Path, reuse_reason: str | None = None) -> bool:
    """Returns True if this is a reuse (allowed only with a reason). Raises if used and no reason."""
    name = name.lower()
    if name not in used_names(processed_dir, model_dir):
        return False
    if not (reuse_reason or "").strip():
        raise HoldoutAlreadyUsed(
            f"{name} was already used as a holdout. A holdout is evaluated once: re-checking it after "
            "changing the model turns it into training signal. Collect a fresh set, or (owner's "
            "decision only) pass --reuse-reason \"why\"; a reuse is recorded and can't release a model.")
    return True


def record_use(name: str, processed_dir: Path, info: dict, reuse_reason: str | None = None) -> Path:
    name = name.lower()
    path = Path(processed_dir) / USED_FILE
    used = load_used(processed_dir)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    entry = used.get(name) or {"first_used": now, "reuses": []}
    if reuse_reason:
        entry["reuses"].append({"at": now, "reason": reuse_reason, **info})
    else:
        entry.update({"first_used": entry.get("first_used", now), **info})
    used[name] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(used, indent=2, default=str))
    return path
