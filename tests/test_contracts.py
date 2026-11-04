"""Analysis contracts (src/lolreview/contracts.py): the pass rule is fixed before the numbers exist."""

import json

import pytest

from lolreview import contracts

GOOD = """# Example

```contract
question: does gold concentration predict wins beyond team gold?
hypothesis: at even team gold, the side with the richest player wins more often
metric: 5-fold CV log-loss gain by game, minutes 8+
pass_if: gain > 0 and 90% lower bound > 0
data: emerald, diamond
resample_unit: game
script: scripts/analysis/fed_carry.py
results: data/processed/analyses/fed_carry.json
```
"""


def write(tmp_path, text, name="a.md"):
    p = tmp_path / name
    p.write_text(text)
    return p


def dirs(tmp_path):
    (tmp_path / "processed").mkdir(exist_ok=True)
    (tmp_path / "models").mkdir(exist_ok=True)
    return {"processed_dir": tmp_path / "processed", "model_dir": tmp_path / "models"}


def test_good_contract_loads_and_stamps(tmp_path):
    c = contracts.load(write(tmp_path, GOOD), **dirs(tmp_path))
    s = contracts.stamp(c)
    assert s["pass_if"] == "gain > 0 and 90% lower bound > 0" and len(s["sha256"]) == 64


def test_missing_field_is_refused(tmp_path):
    with pytest.raises(contracts.ContractError, match="pass_if"):
        contracts.load(write(tmp_path, GOOD.replace("pass_if: gain > 0 and 90% lower bound > 0\n", "")),
                       **dirs(tmp_path))


def test_row_resampling_is_refused(tmp_path):
    for unit in ("row", "minute"):
        with pytest.raises(contracts.ContractError, match="never by row"):
            contracts.load(write(tmp_path, GOOD.replace("resample_unit: game", f"resample_unit: {unit}")),
                           **dirs(tmp_path))


def test_fresh_holdout_is_refused_but_trained_holdout_is_fine(tmp_path):
    d = dirs(tmp_path)
    for name in ("emerald_holdout", "emerald_holdout_1622"):
        (d["processed_dir"] / f"matches_{name}.json").write_text("{}")
    (d["model_dir"] / "winprob_meta.json").write_text(json.dumps({"dataset": {"corpora": ["emerald", "emerald_holdout"]}}))
    assert contracts.protected_corpora(**d) == {"emerald_holdout_1622"}
    contracts.load(write(tmp_path, GOOD.replace("data: emerald, diamond", "data: emerald, emerald_holdout")), **d)
    with pytest.raises(contracts.ContractError, match="fresh holdout"):
        contracts.load(write(tmp_path, GOOD.replace("data: emerald, diamond", "data: emerald_holdout_1622")), **d)


def test_moving_the_pass_rule_after_the_run_is_caught(tmp_path):
    d = dirs(tmp_path)
    doc = write(tmp_path, GOOD)
    results = {"contract": contracts.stamp(contracts.load(doc, **d)), "passed": False, "gain": -0.0004}
    assert contracts.verify(doc, results, **d) == []
    doc.write_text(GOOD.replace("gain > 0 and 90% lower bound > 0", "gain > -0.001"))
    problems = contracts.verify(doc, results, **d)
    assert problems and "changed after" in problems[0]


def test_results_must_say_whether_they_passed(tmp_path):
    d = dirs(tmp_path)
    doc = write(tmp_path, GOOD)
    problems = contracts.verify(doc, {"contract": contracts.stamp(contracts.load(doc, **d))}, **d)
    assert any("passed" in p for p in problems)


def test_template_parses():
    from pathlib import Path
    fields, _ = contracts.parse((Path(__file__).resolve().parents[1] / "docs/analyses/TEMPLATE.md").read_text())
    assert set(contracts.REQUIRED) <= set(fields)
