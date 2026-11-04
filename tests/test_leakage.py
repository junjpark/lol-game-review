"""No future information in model features. These used to be review questions; now they're tests.

1. No feature is a whole-game field (labels and filters: who won, AFK flags). Those describe the
   game's end and may only be used to label or filter rows (docs/data-notes.md, Phase 3).
2. Features at minute m don't change when the game is cut off right after minute m. If they did,
   something read the future (the end-of-game summary, later events, the final snapshot).
"""

from __future__ import annotations

import copy

import pytest

from golden_fixtures import all_fixtures, load_fixture
from lolreview.extract import extract_game
from lolreview.model.dataset import FEATURE_NAMES, features_for_game

# describe the whole game: labels/filters only, never features
WHOLE_GAME_FIELDS = {"win", "blue_win", "red_win", "game_has_afk", "afk_players", "was_afk", "idle_max",
                     "game_duration", "gameDuration", "surrender", "early_surrender"}


def test_no_feature_is_a_whole_game_field():
    bad = [f for f in FEATURE_NAMES
           if f in WHOLE_GAME_FIELDS or f.endswith("_win") or "afk" in f or "idle" in f or "duration" in f]
    assert not bad, f"whole-game fields used as features: {bad}"


def cut_after(timeline: dict, minute: int) -> dict:
    """The timeline as it would have looked at minute `minute`: later frames and events removed."""
    tl = copy.deepcopy(timeline)
    frames = tl["info"]["frames"][: minute + 1]
    t_cut = frames[-1]["timestamp"]
    for f in frames:
        f["events"] = [e for e in f.get("events", []) if e.get("timestamp", 0) <= t_cut]
    tl["info"]["frames"] = frames
    return tl


@pytest.mark.parametrize("path", all_fixtures()[:3], ids=lambda p: p.name.split(".")[0])
def test_features_do_not_see_the_future(path):
    pd = pytest.importorskip("pandas")
    fx = load_fixture(path)
    match, timeline = fx["match"], fx["timeline"]
    full = features_for_game(extract_game(match, timeline, "emerald"))
    last = len(timeline["info"]["frames"]) - 1
    for m in sorted({5, 12, last // 2, last - 2}):
        cut = features_for_game(extract_game(match, cut_after(timeline, m), "emerald"))
        a = cut[cut["minute"] <= m][FEATURE_NAMES].reset_index(drop=True)
        b = full[full["minute"] <= m][FEATURE_NAMES].reset_index(drop=True)
        pd.testing.assert_frame_equal(a, b, check_dtype=False,
            obj=f"{fx['match_id']} features up to minute {m} (cut vs full game)")
