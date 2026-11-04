"""The run-once rule for holdouts (docs/model-history.md) is enforced in code."""

import json

import pytest

from lolreview.model import holdouts


def test_fresh_set_is_allowed(tmp_path):
    assert holdouts.check_unused("emerald_holdout_1622", tmp_path / "p", tmp_path / "m") is False


def test_used_set_is_refused_without_reason(tmp_path):
    holdouts.record_use("emerald_holdout_1622", tmp_path, {"passed": True})
    with pytest.raises(holdouts.HoldoutAlreadyUsed):
        holdouts.check_unused("EMERALD_HOLDOUT_1622", tmp_path, tmp_path / "m")
    with pytest.raises(holdouts.HoldoutAlreadyUsed):
        holdouts.check_unused("emerald_holdout_1622", tmp_path, tmp_path / "m", reuse_reason="  ")


def test_reuse_needs_a_reason_and_is_recorded(tmp_path):
    holdouts.record_use("h1", tmp_path, {"passed": True})
    assert holdouts.check_unused("h1", tmp_path, tmp_path / "m", reuse_reason="debugging a crash") is True
    holdouts.record_use("h1", tmp_path, {"passed": False}, reuse_reason="debugging a crash")
    entry = holdouts.load_used(tmp_path)["h1"]
    assert entry["passed"] is True                       # the first (real) result is kept
    assert entry["reuses"][0]["reason"] == "debugging a crash"


def test_sets_named_in_model_metadata_count_as_used(tmp_path):
    (tmp_path / "archive" / "v1_x").mkdir(parents=True)
    (tmp_path / "archive" / "v1_x" / "winprob_meta.json").write_text(json.dumps({"holdout": {"name": "emerald_holdout"}}))
    (tmp_path / "winprob_meta.json").write_text(json.dumps({"holdout": None}))
    with pytest.raises(holdouts.HoldoutAlreadyUsed):
        holdouts.check_unused("emerald_holdout", tmp_path / "processed", tmp_path)
