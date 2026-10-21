"""Summoner's Rift geometry: bounds, symmetry, and landmark positions.

Shared by the replay visualizer (Phase 2) and the semantic fields (Phase 7: map regions,
distance to objectives, lockout measurement).

Where the coordinates come from — and how far to trust them:

* **Turrets and inhibitors: exact.** Median position of every BUILDING_KILL in the 5,000-game
  Emerald corpus (patches 16.18–16.20). Each structure showed zero spread across 1,000–4,000
  kills, so these are the true positions.
* **Dragon and Baron pits: exact enough.** Median ELITE_MONSTER_KILL position. Baron has zero
  spread; Dragon spreads ~200–300 units because the fight drags it around the pit. Voidgrubs
  and Rift Herald die in the Baron pit.
* **Jungle camps: approximate (±~300 units).** No event records a camp's position. Blue
  Sentinel, Red Brambleback and Raptors come from where junglers stand at 1:00 waiting for
  their first camp (thousands of samples each); Murk Wolves agrees with the standard layout
  within 150 units; Gromp and Krugs come from the noisier 2:00 snapshot; Scuttle moves along
  the river, so its points are nominal spawn spots.

Turret plates are NOT recorded at their turret — plate events scatter by thousands of units —
so never use a TURRET_PLATE_DESTROYED position as a tower location.

The map is point-symmetric: red-side positions are blue-side positions mirrored through
MIRROR / 2, with top and bottom lanes swapping. Paired structures agree within ~70 units.
"""

from __future__ import annotations

from math import dist

# Coordinate bounds (Riot docs). Every position in the corpus falls inside these.
MIN_X, MIN_Y, MAX_X, MAX_Y = -120, -120, 14870, 14980

# Point-symmetry constant: (x, y) on one side <-> (MIRROR_X - x, MIRROR_Y - y) on the other.
MIRROR_X, MIRROR_Y = 14800, 14890

LANE_MIRROR = {"top": "bot", "mid": "mid", "bot": "top"}


def mirror(x: float, y: float) -> tuple[float, float]:
    return MIRROR_X - x, MIRROR_Y - y


def normalize(x: float, y: float) -> tuple[float, float]:
    """Game coordinates -> [0, 1] with y up (blue base bottom-left)."""
    return (
        round((x - MIN_X) / (MAX_X - MIN_X), 4),
        round((y - MIN_Y) / (MAX_Y - MIN_Y), 4),
    )


# ---- structures (exact) ---------------------------------------------------------------
# (team, kind, tier, lane, x, y). Nexus turrets have no lane; "a"/"b" distinguishes them.
_STRUCTURES = [
    (100, "turret", "outer", "top", 981, 10441),
    (100, "turret", "inner", "top", 1512, 6699),
    (100, "turret", "base", "top", 1169, 4287),
    (100, "inhibitor", "inhib", "top", 1172, 3583),
    (100, "turret", "outer", "mid", 5846, 6396),
    (100, "turret", "inner", "mid", 5048, 4812),
    (100, "turret", "base", "mid", 3651, 3696),
    (100, "inhibitor", "inhib", "mid", 3210, 3217),
    (100, "turret", "outer", "bot", 10504, 1029),
    (100, "turret", "inner", "bot", 6919, 1483),
    (100, "turret", "base", "bot", 4281, 1253),
    (100, "inhibitor", "inhib", "bot", 3468, 1230),
    (100, "turret", "nexus", "a", 1748, 2270),
    (100, "turret", "nexus", "b", 2177, 1807),
    (200, "turret", "outer", "top", 4318, 13875),
    (200, "turret", "inner", "top", 7943, 13411),
    (200, "turret", "base", "top", 10481, 13650),
    (200, "inhibitor", "inhib", "top", 11275, 13657),
    (200, "turret", "outer", "mid", 8955, 8510),
    (200, "turret", "inner", "mid", 9767, 10113),
    (200, "turret", "base", "mid", 11134, 11207),
    (200, "inhibitor", "inhib", "mid", 11593, 11669),
    (200, "turret", "outer", "bot", 13866, 4505),
    (200, "turret", "inner", "bot", 13327, 8226),
    (200, "turret", "base", "bot", 13624, 10572),
    (200, "inhibitor", "inhib", "bot", 13599, 11319),
    (200, "turret", "nexus", "a", 12611, 13084),
    (200, "turret", "nexus", "b", 13052, 12612),
]

STRUCTURES: list[dict] = [
    {
        "id": f"{team}_{lane}_{tier}" if tier != "nexus" else f"{team}_nexus_{lane}",
        "team": team,
        "kind": kind,
        "tier": tier,
        "lane": None if tier == "nexus" else lane,
        "x": x,
        "y": y,
    }
    for team, kind, tier, lane, x, y in _STRUCTURES
]

INHIBITOR_RESPAWN_MS = 5 * 60 * 1000  # inhibitors respawn 5 minutes after being destroyed

# ---- objective pits ---------------------------------------------------------------------
PITS: list[dict] = [
    {"id": "dragon", "name": "Dragon pit", "x": 9837, "y": 4405},
    {"id": "baron", "name": "Baron pit (Voidgrubs, Herald)", "x": 5007, "y": 10471},
]

# ---- jungle camps (approximate) -------------------------------------------------------
# Blue side measured; red side is the mirror image.
_BLUE_CAMPS = [
    ("blue_buff", "Blue Sentinel", 3352, 7981),
    ("gromp", "Gromp", 2548, 8291),
    ("wolves", "Murk Wolves", 3650, 6475),
    ("raptors", "Raptors", 6903, 5080),
    ("red_buff", "Red Brambleback", 7669, 3624),
    ("krugs", "Krugs", 8270, 2425),
]
CAMPS: list[dict] = []
for _kind, _name, _x, _y in _BLUE_CAMPS:
    CAMPS.append({"id": f"100_{_kind}", "kind": _kind, "name": _name, "side": 100, "x": _x, "y": _y})
    _mx, _my = mirror(_x, _y)
    CAMPS.append({"id": f"200_{_kind}", "kind": _kind, "name": _name, "side": 200, "x": _mx, "y": _my})
CAMPS += [
    {"id": "scuttle_bot", "kind": "scuttle", "name": "Scuttle Crab (bot river)", "side": None, "x": 10500, "y": 5170},
    {"id": "scuttle_top", "kind": "scuttle", "name": "Scuttle Crab (top river)", "side": None, "x": 4400, "y": 9600},
]


def structure_for_event(event: dict) -> dict | None:
    """Which landmark a BUILDING_KILL refers to.

    `teamId` on the event is the team that OWNED the structure. Positions are exact, so the
    nearest same-team, same-kind structure is the match; lane/tier is the fallback when an
    event has no position.
    """
    team = event.get("teamId")
    kind = "inhibitor" if event.get("buildingType") == "INHIBITOR_BUILDING" else "turret"
    candidates = [s for s in STRUCTURES if s["team"] == team and s["kind"] == kind]
    if not candidates:
        return None
    if "position" in event:
        p = (event["position"]["x"], event["position"]["y"])
        return min(candidates, key=lambda s: dist(p, (s["x"], s["y"])))
    lane = (event.get("laneType") or "").split("_")[0].lower()
    tier = {"OUTER_TURRET": "outer", "INNER_TURRET": "inner", "BASE_TURRET": "base",
            "NEXUS_TURRET": "nexus"}.get(event.get("towerType"), "inhib")
    for s in candidates:
        if s["tier"] == tier and (tier == "nexus" or s["lane"] == lane):
            return s
    return None
