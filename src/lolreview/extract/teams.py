"""Team-minute rows: the input to the Phase 5 win-probability model.

Every column describes the game AS OF that minute. Facts like "got first blood" only
become true once they happen — a per-game constant would leak the future into early
minutes and teach the model things it couldn't know at the time.

Exceptions, which are filters rather than model features: `win` (the label) and
`game_has_afk` / `afk_players` (describe the whole game).
"""

from __future__ import annotations

from collections import Counter, defaultdict

from ..rift import INHIBITOR_RESPAWN_MS
from .events import is_scoreboard_execution, other_team

MONSTER_FIELD = {
    "BARON_NASHOR": "barons",
    "RIFTHERALD": "heralds",
    "HORDE": "voidgrubs",
    "ATAKHAN": "atakhans",
}


def extract_teams(match: dict, timeline: dict, frame_rows: list[dict], tier: str) -> list[dict]:
    info = match["info"]
    mid = match["metadata"]["matchId"]
    patch = ".".join(info.get("gameVersion", "").split(".")[:2])
    won = {t["teamId"]: bool(t.get("win")) for t in info.get("teams", [])}
    team_of = {p["participantId"]: p["teamId"] for p in info["participants"]}
    afk = Counter(p["teamId"] for p in info["participants"] if p.get("wasAfk"))
    game_has_afk = bool(afk)

    # player rows grouped by minute and team
    by_min: dict[int, dict[int, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in frame_rows:
        by_min[r["minute"]][r["team_id"]].append(r)

    obj = {100: Counter(), 200: Counter()}
    soul: dict[int, bool] = {100: False, 200: False}
    inhib_kills: dict[int, list[int]] = {100: [], 200: []}  # kill times of ENEMY inhibs, by team
    first_blood = first_tower = None
    rows: list[dict] = []

    for minute, fr in enumerate(timeline["info"]["frames"]):
        t_ms = fr["timestamp"]
        for e in fr.get("events", []):
            t = e["type"]
            if is_scoreboard_execution(e, team_of):
                # Death to a turret/minions: no player gets it, but it counts on the
                # scoreboard for the victim's enemy. (Deaths to monsters count for nobody.)
                credited = other_team(team_of.get(e.get("victimId")))
                if credited in obj:
                    obj[credited]["executes"] += 1
            elif t == "CHAMPION_SPECIAL_KILL" and e.get("killType") == "KILL_FIRST_BLOOD":
                first_blood = first_blood or team_of.get(e.get("killerId"))
            elif t == "BUILDING_KILL":
                taker = other_team(e.get("teamId"))
                if taker not in obj:
                    continue
                if e.get("buildingType") == "INHIBITOR_BUILDING":
                    obj[taker]["inhibs_killed"] += 1
                    inhib_kills[taker].append(e["timestamp"])
                else:
                    obj[taker]["towers"] += 1
                    first_tower = first_tower or taker
            elif t == "TURRET_PLATE_DESTROYED":
                taker = other_team(e.get("teamId"))
                if taker in obj:
                    obj[taker]["plates"] += 1
            elif t == "ELITE_MONSTER_KILL":
                team = e.get("killerTeamId")
                if team not in obj:
                    continue
                if e.get("monsterType") == "DRAGON":
                    obj[team]["elders" if e.get("monsterSubType") == "ELDER_DRAGON" else "dragons"] += 1
                elif e.get("monsterType") in MONSTER_FIELD:
                    obj[team][MONSTER_FIELD[e["monsterType"]]] += 1
            elif t == "DRAGON_SOUL_GIVEN" and e.get("teamId") in soul:
                soul[e["teamId"]] = True

        agg = {}
        for team in (100, 200):
            players = by_min[minute][team]
            agg[team] = {
                "gold": sum(r["gold"] or 0 for r in players),
                "xp": sum(r["xp"] or 0 for r in players),
                "cs": sum(r["cs"] or 0 for r in players),
                "level_avg": sum(r["level"] or 0 for r in players) / len(players) if players else None,
                # scoreboard kills: player kills + executions of the enemy
                "kills": sum(r["kills"] for r in players) + obj[team]["executes"],
                "deaths": sum(r["deaths"] for r in players),
            }
        for team in (100, 200):
            enemy = other_team(team)
            a, b, o = agg[team], agg[enemy], obj[team]
            rows.append({
                "match_id": mid, "tier": tier, "patch": patch,
                "minute": minute, "t_ms": t_ms, "team_id": team,
                "win": won.get(team, False), "game_has_afk": game_has_afk, "afk_players": afk.get(team, 0),
                **a,
                "gold_diff": a["gold"] - b["gold"], "xp_diff": a["xp"] - b["xp"],
                "kill_diff": a["kills"] - b["kills"],
                "towers": o["towers"], "tower_diff": o["towers"] - obj[enemy]["towers"],
                "plates": o["plates"],
                "inhibs_killed": o["inhibs_killed"],
                "enemy_inhibs_down": sum(1 for k in inhib_kills[team] if k <= t_ms < k + INHIBITOR_RESPAWN_MS),
                "dragons": o["dragons"], "dragon_diff": o["dragons"] - obj[enemy]["dragons"],
                "elders": o["elders"], "soul": soul[team],
                "barons": o["barons"], "heralds": o["heralds"],
                "voidgrubs": o["voidgrubs"], "atakhans": o["atakhans"],
                "first_blood": first_blood == team, "first_tower": first_tower == team,
            })
    return rows
