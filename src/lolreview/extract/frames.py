"""Timeline frames -> one row per player per minute.

Each row is a player's state at a one-minute snapshot, plus counts of what they did since
the previous snapshot. Everything is computed within a single game, so "previous minute"
values (deltas, per-minute counts) can never leak across game boundaries.
"""

from __future__ import annotations

from collections import Counter

from .events import REAL_WARD_TYPES, is_scoreboard_execution, other_team
from .lanes import lane_opponents

# Phase boundaries (minutes). 14:00 is when turret plates fall, a natural end of laning.
EARLY_END, MID_END = 14, 25


def phase(minute: int) -> str:
    return "early" if minute < EARLY_END else "mid" if minute < MID_END else "late"


def idle_max(frames: list[dict], pid: int) -> int:
    """Longest run of consecutive snapshots (from 2:00) with identical position and XP.

    Secondary AFK signal only — Riot's `wasAfk` is the primary one. In the Emerald corpus
    this catches 63 of 117 `wasAfk` players and flags 12 that Riot didn't.
    """
    best = run = 0
    key = str(pid)
    for a, b in zip(frames[2:], frames[3:]):
        pa, pb = a["participantFrames"].get(key), b["participantFrames"].get(key)
        same = bool(pa and pb) and pa["position"] == pb["position"] and pa.get("xp") == pb.get("xp")
        run = run + 1 if same else 0
        best = max(best, run)
    return best


def _count_events(events: list[dict], team_of: dict[int, int]) -> tuple[dict[int, Counter], Counter]:
    """Per-player counts and per-team scoreboard kills for one frame's events.

    Team kills follow the in-game scoreboard: champion kills PLUS deaths to turrets or
    minions (which count for the other team though no player gets the kill). Deaths to
    neutral monsters count for nobody. Player kills alone match Riot's team totals ~92%.
    """
    per: dict[int, Counter] = {pid: Counter() for pid in team_of}
    team_kills: Counter = Counter()
    for e in events:
        t = e["type"]
        if t == "CHAMPION_KILL":
            k, v = e.get("killerId"), e.get("victimId")
            if k in per:
                per[k]["kills"] += 1
                team_kills[team_of[k]] += 1
            elif is_scoreboard_execution(e, team_of):
                team_kills[other_team(team_of[v])] += 1
            if v in per:
                per[v]["deaths"] += 1
            for a in e.get("assistingParticipantIds") or []:
                if a in per:
                    per[a]["assists"] += 1
        elif t == "WARD_PLACED" and e.get("creatorId") in per and e.get("wardType") in REAL_WARD_TYPES:
            per[e["creatorId"]]["wards_placed"] += 1
            if e.get("wardType") == "CONTROL_WARD":
                per[e["creatorId"]]["control_wards"] += 1
        elif t == "WARD_KILL" and e.get("killerId") in per and e.get("wardType") in REAL_WARD_TYPES:
            per[e["killerId"]]["wards_killed"] += 1
        elif t in ("ELITE_MONSTER_KILL", "BUILDING_KILL"):
            field = "epic_participation" if t == "ELITE_MONSTER_KILL" else "structure_participation"
            for pid in {e.get("killerId"), *(e.get("assistingParticipantIds") or [])}:
                if pid in per:
                    per[pid][field] += 1
    return per, team_kills


CUMULATIVE = ("kills", "deaths", "assists", "wards_placed", "wards_killed", "control_wards",
              "epic_participation", "structure_participation")


def extract_frames(match: dict, timeline: dict, tier: str) -> list[dict]:
    info = match["info"]
    mid = match["metadata"]["matchId"]
    patch = ".".join(info.get("gameVersion", "").split(".")[:2])
    parts = {p["participantId"]: p for p in info["participants"]}
    team_of = {pid: p["teamId"] for pid, p in parts.items()}
    opp = lane_opponents(info["participants"])
    game_has_afk = any(p.get("wasAfk") for p in parts.values())
    frames = timeline["info"]["frames"]
    idle = {pid: idle_max(frames, pid) for pid in parts}

    cum = {pid: Counter() for pid in parts}
    team_kills: Counter = Counter()
    prev: dict[int, tuple[int, int]] = {}
    rows: list[dict] = []

    for minute, fr in enumerate(frames):
        t_ms = fr["timestamp"]
        now, tk = _count_events(fr.get("events", []), team_of)
        team_kills.update(tk)
        for pid in parts:
            cum[pid].update(now[pid])

        snap: dict[int, dict] = {}
        for key, pf in fr["participantFrames"].items():
            pid = int(key)
            stats, dmg = pf.get("championStats") or {}, pf.get("damageStats") or {}
            hp, hp_max = stats.get("health"), stats.get("healthMax")
            cs_lane, cs_jg = pf.get("minionsKilled", 0), pf.get("jungleMinionsKilled", 0)
            snap[pid] = {
                "gold": pf.get("totalGold"), "current_gold": pf.get("currentGold"),
                "xp": pf.get("xp"), "level": pf.get("level"),
                "cs": cs_lane + cs_jg, "cs_lane": cs_lane, "cs_jungle": cs_jg,
                "hp": hp, "hp_max": hp_max,
                "hp_pct": (hp / hp_max) if hp is not None and hp_max else None,
                "x": pf["position"]["x"], "y": pf["position"]["y"],
                "armor": stats.get("armor"), "magic_resist": stats.get("magicResist"),
                "attack_damage": stats.get("attackDamage"), "ability_power": stats.get("abilityPower"),
                "attack_speed": stats.get("attackSpeed"), "movement_speed": stats.get("movementSpeed"),
                "ability_haste": stats.get("abilityHaste"),
                "dmg_to_champs": dmg.get("totalDamageDoneToChampions"),
                "dmg_taken": dmg.get("totalDamageTaken"),
                "cc_time": pf.get("timeEnemySpentControlled"),
            }

        for pid in sorted(snap):
            s, p = snap[pid], parts.get(pid, {})
            team = team_of.get(pid)
            c = cum[pid]
            tkills = team_kills.get(team, 0)
            o = opp.get(pid)
            os_ = snap.get(o) if o else None
            last = prev.get(pid)
            rows.append({
                "match_id": mid, "tier": tier, "patch": patch,
                "minute": minute, "t_ms": t_ms,
                "participant_id": pid, "team_id": team,
                "role": p.get("teamPosition") or None, "champion": p.get("championName"),
                "puuid": p.get("puuid"),
                "win": bool(p.get("win")), "was_afk": bool(p.get("wasAfk")),
                "game_has_afk": game_has_afk, "idle_max": idle.get(pid, 0),
                "phase": phase(minute),
                **s,
                "cs_per_min": s["cs"] / (t_ms / 60_000) if t_ms > 0 else None,
                "gold_delta_1m": s["gold"] - last[0] if last else None,
                "xp_delta_1m": s["xp"] - last[1] if last else None,
                **{k: c[k] for k in CUMULATIVE},
                "kill_participation": (c["kills"] + c["assists"]) / tkills if tkills else None,
                "kills_m": now[pid]["kills"], "deaths_m": now[pid]["deaths"],
                "assists_m": now[pid]["assists"],
                "wards_placed_m": now[pid]["wards_placed"], "wards_killed_m": now[pid]["wards_killed"],
                "opp_id": o,
                "opp_gold": os_["gold"] if os_ else None, "opp_xp": os_["xp"] if os_ else None,
                "opp_level": os_["level"] if os_ else None, "opp_cs": os_["cs"] if os_ else None,
                "gold_diff": s["gold"] - os_["gold"] if os_ else None,
                "xp_diff": s["xp"] - os_["xp"] if os_ else None,
                "level_diff": s["level"] - os_["level"] if os_ else None,
                "cs_diff": s["cs"] - os_["cs"] if os_ else None,
            })
            prev[pid] = (s["gold"], s["xp"])
    return rows
