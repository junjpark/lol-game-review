"""Timeline events -> one normalized row per event.

Also owns the event category rules shared with the replay visualizer.
"""

from __future__ import annotations

from ..rift import structure_for_event

# Level-ups are dropped from the events table: level is already a FRAMES column, and the
# two types are ~20% of all events. They remain in the raw cache if ever needed.
SKIPPED_TYPES = {"LEVEL_UP", "SKILL_LEVEL_UP"}

# WARD_PLACED / WARD_KILL also fire for champion ability objects: wardType UNDEFINED
# (Zyra seeds, Jhin traps, Yorick graves, Illaoi tentacles, Warwick, Shen, Bard, ...) and
# TEEMO_MUSHROOM. Counting only these types matches Riot's wardsPlaced for 99.5% of players
# and wardsKilled for 100%; counting every event matches only ~89% / ~94%.
REAL_WARD_TYPES = frozenset({"YELLOW_TRINKET", "SIGHT_WARD", "CONTROL_WARD", "BLUE_TRINKET"})

CATEGORY = {
    "CHAMPION_KILL": "kill",
    "ELITE_MONSTER_KILL": "special",
    "DRAGON_SOUL_GIVEN": "special",
    "BUILDING_KILL": "special",
    "TURRET_PLATE_DESTROYED": "plate",
    "WARD_PLACED": "ward",
    "WARD_KILL": "ward",
    "ITEM_PURCHASED": "item",
    "ITEM_SOLD": "item",
    "ITEM_DESTROYED": "item",
    "ITEM_UNDO": "item",
    "LEVEL_UP": "level",
    "SKILL_LEVEL_UP": "level",
}


def category(e: dict) -> str:
    """Event category. "special" = first blood, 3+ multikills, aces, epic monsters, dragon
    soul, and turrets/inhibitors destroyed. Double kills stay ordinary kills (there are
    ~6 a game); turret plates get their own category since they're chip damage."""
    if e["type"] == "CHAMPION_SPECIAL_KILL":
        if e.get("killType") == "KILL_MULTI" and (e.get("multiKillLength") or 0) < 3:
            return "kill"
        return "special"
    if e["type"] == "DRAGON_SOUL_GIVEN" and e.get("teamId") not in (100, 200):
        return "other"  # teamId 0 = soul type revealed after the 2nd dragon, not earned
    return CATEGORY.get(e["type"], "other")


def is_scoreboard_execution(e: dict, team_of: dict[int, int]) -> bool:
    """A CHAMPION_KILL with no champion killer that still counts as a team kill.

    Deaths to turrets or minions count for the victim's enemy on the scoreboard; deaths to
    neutral monsters (dragon, Baron, camps) count for nobody. The killing blow's source is
    the last entry of victimDamageReceived.
    """
    if e["type"] != "CHAMPION_KILL" or e.get("killerId") in team_of or e.get("victimId") not in team_of:
        return False
    received = e.get("victimDamageReceived") or []
    return not (received and received[-1].get("type") == "MONSTER")


def other_team(team: int | None) -> int | None:
    return {100: 200, 200: 100}.get(team)


def acting_team(e: dict, team_of: dict[int, int]) -> int | None:
    """The team that did the thing. Note BUILDING_KILL/plate `teamId` is the OWNER."""
    t = e["type"]
    if t == "CHAMPION_KILL":
        k = e.get("killerId")
        return team_of[k] if k in team_of else other_team(team_of.get(e.get("victimId")))
    if t == "CHAMPION_SPECIAL_KILL":
        return team_of.get(e.get("killerId"))
    if t == "ELITE_MONSTER_KILL":
        return e.get("killerTeamId")
    if t == "DRAGON_SOUL_GIVEN":
        # teamId 0 fires after the game's 2nd dragon to reveal the soul type; only
        # 100/200 mean a team earned it (always at its 4th dragon).
        return e.get("teamId") if e.get("teamId") in (100, 200) else None
    if t in ("BUILDING_KILL", "TURRET_PLATE_DESTROYED"):
        return other_team(e.get("teamId"))
    if t == "WARD_PLACED":
        return team_of.get(e.get("creatorId"))
    if t == "WARD_KILL":
        return team_of.get(e.get("killerId"))
    if t.startswith("ITEM_"):
        return team_of.get(e.get("participantId"))
    if t == "GAME_END":
        return e.get("winningTeam")
    return None


def _actor(e: dict) -> int | None:
    for key in ("killerId", "creatorId", "participantId"):
        if key in e:
            return e[key]
    return None


def _structure_id(e: dict) -> str | None:
    if e["type"] == "BUILDING_KILL":
        s = structure_for_event(e)
        return s["id"] if s else None
    if e["type"] == "TURRET_PLATE_DESTROYED":
        # Plates come off the outer turret; event positions are NOT at the turret.
        lane = (e.get("laneType") or "").split("_")[0].lower()
        return f"{e.get('teamId')}_{lane}_outer" if lane else None
    return None


def _item(e: dict) -> int | None:
    if e["type"] == "ITEM_UNDO":
        return e.get("beforeId") or e.get("afterId") or None
    return e.get("itemId")


def extract_events(match: dict, timeline: dict, tier: str) -> list[dict]:
    """One row per event in timeline order (level-ups excluded)."""
    mid = match["metadata"]["matchId"]
    team_of = {p["participantId"]: p["teamId"] for p in match["info"]["participants"]}
    rows: list[dict] = []
    idx = 0
    for minute, fr in enumerate(timeline["info"]["frames"]):
        for e in fr.get("events", []):
            if e["type"] in SKIPPED_TYPES:
                continue
            pos = e.get("position") or {}
            sub = e.get("monsterSubType")
            rows.append({
                "match_id": mid, "tier": tier, "event_idx": idx,
                "minute": minute, "t_ms": e["timestamp"],
                "type": e["type"], "cat": category(e),
                "actor_id": _actor(e),
                "victim_id": e.get("victimId"),
                "team_id": acting_team(e, team_of),
                "assists": list(e.get("assistingParticipantIds") or []),
                "x": pos.get("x"), "y": pos.get("y"),
                "structure_id": _structure_id(e),
                "monster": sub or e.get("monsterType"),
                "ward_type": e.get("wardType"),
                "item_id": _item(e),
                "kill_type": e.get("killType"),
                "multikill": e.get("multiKillLength"),
                "bounty": e.get("bounty"), "shutdown_bounty": e.get("shutdownBounty"),
            })
            idx += 1
    return rows
