"""Cross-check extracted tables against Riot's own end-of-game summary.

The match file carries per-player and per-team totals that Riot computes separately from
the minute-by-minute timeline. If the extraction's final-minute values agree with them,
the field mapping, event counting, and team attribution are right. Run on every game
during `build`; the summary lands in extract_<tier>.json.

Some fields legitimately differ by a little — the last snapshot isn't at the exact final
millisecond — so each check has a tolerance.
"""

from __future__ import annotations

from collections import Counter

# (label, frames column, match-summary field(s) summed, tolerance)
PLAYER_CHECKS = [
    ("kills", "kills", ("kills",), 0),
    ("deaths", "deaths", ("deaths",), 0),
    ("assists", "assists", ("assists",), 0),
    ("level", "level", ("champLevel",), 0),
    ("cs", "cs", ("totalMinionsKilled", "neutralMinionsKilled"), 5),
    ("gold", "gold", ("goldEarned",), 250),
    ("damage_to_champs", "dmg_to_champs", ("totalDamageDealtToChampions",), 500),
    # ±2: Riot's summary counts ward-hops differently — the ~0.5% of players off by 1–2
    # are concentrated on Lee Sin and Jax. Unresolvable from the timeline.
    ("wards_placed", "wards_placed", ("wardsPlaced",), 2),
    ("wards_killed", "wards_killed", ("wardsKilled",), 0),
]

# (label, teams column, path into info.teams[].objectives)
TEAM_CHECKS = [
    ("towers", "towers", "tower"),
    ("inhibitors", "inhibs_killed", "inhibitor"),
    ("dragons+elders", ("dragons", "elders"), "dragon"),
    ("barons", "barons", "baron"),
    ("heralds", "heralds", "riftHerald"),
    ("voidgrubs", "voidgrubs", "horde"),
    ("kills", "kills", "champion"),
]


def game_checks(match: dict, tables: dict[str, list[dict]]) -> Counter:
    """Counter of '<check>:ok' / '<check>:off' (and '<check>:n/a') for one game."""
    out: Counter = Counter()
    info = match["info"]
    frames, teams = tables["frames"], tables["teams"]
    if not frames:
        return out
    last_minute = max(r["minute"] for r in frames)
    final = {r["participant_id"]: r for r in frames if r["minute"] == last_minute}

    for p in info["participants"]:
        r = final.get(p["participantId"])
        if r is None:
            continue
        for label, col, fields, tol in PLAYER_CHECKS:
            if any(f not in p for f in fields) or r[col] is None:
                out[f"{label}:n/a"] += 1
                continue
            truth = sum(p[f] for f in fields)
            out[f"{label}:{'ok' if abs(r[col] - truth) <= tol else 'off'}"] += 1

    final_team = {r["team_id"]: r for r in teams if r["minute"] == last_minute}
    for t in info.get("teams", []):
        r = final_team.get(t["teamId"])
        objectives = t.get("objectives") or {}
        if r is None:
            continue
        for label, cols, key in TEAM_CHECKS:
            if key not in objectives:
                out[f"team_{label}:n/a"] += 1
                continue
            mine = sum(r[c] for c in cols) if isinstance(cols, tuple) else r[cols]
            out[f"team_{label}:{'ok' if mine == objectives[key].get('kills') else 'off'}"] += 1

    # lane diffs must be antisymmetric: my gold_diff == -(my opponent's gold_diff)
    by_key = {(r["minute"], r["participant_id"]): r for r in frames}
    for r in frames:
        if r["opp_id"] is not None:
            o = by_key.get((r["minute"], r["opp_id"]))
            good = o is not None and o["opp_id"] == r["participant_id"] and o["gold_diff"] == -r["gold_diff"]
            out[f"lane_symmetry:{'ok' if good else 'off'}"] += 1
        else:
            out["lane_symmetry:no_opponent"] += 1
    return out


def summarize(checks: Counter) -> dict[str, dict]:
    """{'kills': {'ok': n, 'off': m, 'pct_ok': 99.9}, ...}"""
    by: dict[str, Counter] = {}
    for key, n in checks.items():
        label, _, result = key.rpartition(":")
        by.setdefault(label, Counter())[result] += n
    out = {}
    for label, c in sorted(by.items()):
        checked = c["ok"] + c["off"]
        out[label] = {**c, "pct_ok": round(100 * c["ok"] / checked, 2) if checked else None}
    return out
