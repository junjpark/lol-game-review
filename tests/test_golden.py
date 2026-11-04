"""Golden reviews: the review of a few real games must not change unless we mean it to.

These catch regressions in clustering, parts, involvement and death cost, like the Phase 6 bug
where a top-lane 1v1 made the player "in" the dragon fight (docs/failures.md).

If a test fails after an intended change, re-freeze and read the diff before committing:
    UPDATE_GOLDEN=1 pytest tests/test_golden.py
"""

from __future__ import annotations

import difflib
import json
import os

import pytest

from golden_fixtures import all_fixtures, expected_path, load_fixture, review_output
from lolreview.review import wording

FIXTURES = all_fixtures()
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"


def _dump(obj) -> str:
    return json.dumps(obj, indent=1, ensure_ascii=False, sort_keys=True)


def test_there_are_golden_games():
    assert len(FIXTURES) >= 3, "expected at least 3 golden games in tests/golden/"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name.split(".")[0])
def test_review_matches_golden(path):
    fx = load_fixture(path)
    got = review_output(fx)
    exp_path = expected_path(fx["match_id"])
    if UPDATE or not exp_path.exists():
        exp_path.write_text(_dump(got) + "\n", encoding="utf-8")
        if not UPDATE:
            pytest.fail(f"wrote missing {exp_path.name}; read it, then rerun")
        return
    expected = json.loads(exp_path.read_text(encoding="utf-8"))
    if got != expected:
        diff = difflib.unified_diff(_dump(expected).splitlines(), _dump(got).splitlines(),
                                    "expected", "got", lineterm="", n=2)
        pytest.fail(f"{fx['match_id']} review changed. If intended: UPDATE_GOLDEN=1 pytest "
                    "tests/test_golden.py and read the diff.\n" + "\n".join(list(diff)[:80]))


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name.split(".")[0])
def test_review_wording(path):
    out = review_output(load_fixture(path))
    bad = wording.check(out)
    assert not bad, f"banned wording in review output: {bad[:5]}"


def test_fixtures_carry_no_identities():
    for path in FIXTURES:
        fx = load_fixture(path)
        for p in fx["match"]["info"]["participants"]:
            assert p["riotIdGameName"] == f"Player{p['participantId']}"
            assert p["puuid"] == f"puuid-{p['participantId']}"


def test_wording_detector():
    assert wording.find_banned("A clear BLUNDER at 23:00") == ["blunder"]
    assert wording.find_banned("you should have warded") == ["should have"]
    assert wording.find_banned("Top lane: you killed Nasus (moderate)") == []
    assert wording.find_banned("mistaken identity") == []  # whole words only
    assert wording.check({"a": ["fine", {"b": "objectively wrong"}]}) == [("objectively", "objectively wrong")]
