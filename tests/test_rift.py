"""Map geometry tests. Positions themselves were verified against all 69,865
BUILDING_KILL events in the Emerald corpus; these guard the structure of the tables."""

from __future__ import annotations

from collections import Counter
from math import dist

from lolreview.rift import (
    CAMPS,
    LANE_MIRROR,
    MAX_X,
    MAX_Y,
    MIN_X,
    MIN_Y,
    PITS,
    STRUCTURES,
    mirror,
    normalize,
    structure_for_event,
)


def test_eleven_turrets_and_three_inhibitors_per_team():
    counts = Counter((s["team"], s["kind"]) for s in STRUCTURES)
    assert counts == {(100, "turret"): 11, (100, "inhibitor"): 3, (200, "turret"): 11, (200, "inhibitor"): 3}
    assert len({s["id"] for s in STRUCTURES}) == len(STRUCTURES)


def test_structures_are_point_symmetric():
    for s in (s for s in STRUCTURES if s["team"] == 100):
        lane = s["lane"] and LANE_MIRROR[s["lane"]]
        twins = [t for t in STRUCTURES if t["team"] == 200 and t["tier"] == s["tier"]
                 and (s["tier"] == "nexus" or t["lane"] == lane)]
        assert min(dist(mirror(s["x"], s["y"]), (t["x"], t["y"])) for t in twins) < 100, s["id"]


def test_dragon_pit_mirrors_onto_baron_pit():
    dragon, baron = (next(p for p in PITS if p["id"] == i) for i in ("dragon", "baron"))
    assert dist(mirror(dragon["x"], dragon["y"]), (baron["x"], baron["y"])) < 100


def test_blue_side_structures_in_blue_half():
    for s in STRUCTURES:
        blue_half = s["x"] + s["y"] < MIRROR_SUM
        assert blue_half == (s["team"] == 100), s["id"]


MIRROR_SUM = (14800 + 14890) / 2


def test_everything_inside_map_bounds():
    for d in STRUCTURES + PITS + CAMPS:
        assert MIN_X <= d["x"] <= MAX_X and MIN_Y <= d["y"] <= MAX_Y, d
        x, y = normalize(d["x"], d["y"])
        assert 0 <= x <= 1 and 0 <= y <= 1


def test_camps_come_in_mirrored_pairs_plus_two_scuttles():
    kinds = Counter(c["kind"] for c in CAMPS)
    assert kinds.pop("scuttle") == 2
    assert set(kinds.values()) == {2}  # every other camp once per side


def test_building_kill_matches_by_position():
    e = {"type": "BUILDING_KILL", "teamId": 200, "buildingType": "TOWER_BUILDING",
         "towerType": "OUTER_TURRET", "laneType": "MID_LANE", "position": {"x": 8955, "y": 8510}}
    assert structure_for_event(e)["id"] == "200_mid_outer"


def test_building_kill_falls_back_to_lane_and_tier():
    e = {"type": "BUILDING_KILL", "teamId": 100, "buildingType": "INHIBITOR_BUILDING",
         "laneType": "BOT_LANE"}
    assert structure_for_event(e)["id"] == "100_bot_inhib"
    e = {"type": "BUILDING_KILL", "teamId": 100, "buildingType": "TOWER_BUILDING",
         "towerType": "INNER_TURRET", "laneType": "TOP_LANE"}
    assert structure_for_event(e)["id"] == "100_top_inner"


def test_nexus_turrets_distinguished_by_position():
    a = next(s for s in STRUCTURES if s["id"] == "200_nexus_a")
    b = next(s for s in STRUCTURES if s["id"] == "200_nexus_b")
    for target in (a, b):
        e = {"type": "BUILDING_KILL", "teamId": 200, "buildingType": "TOWER_BUILDING",
             "towerType": "NEXUS_TURRET", "laneType": "MID_LANE",
             "position": {"x": target["x"] + 20, "y": target["y"] - 20}}
        assert structure_for_event(e)["id"] == target["id"]
