"""Column layouts for the three Phase 3 tables.

Kept as plain Python (name -> type string) so the extraction logic and its tests don't
depend on pyarrow; `arrow_schema()` converts lazily when writing Parquet.

Every table is keyed and sorted so a row is identified by its key columns alone — never
by its position in the file.
"""

from __future__ import annotations

# One row per player per minute. Key: (match_id, minute, participant_id).
FRAMES: dict[str, str] = {
    # identity
    "match_id": "str", "tier": "str", "patch": "str",
    "minute": "int16", "t_ms": "int32",
    "participant_id": "int8", "team_id": "int16", "role": "str", "champion": "str", "puuid": "str",
    # outcome / data quality
    "win": "bool", "was_afk": "bool", "game_has_afk": "bool", "idle_max": "int8",
    "phase": "str",
    # state at this snapshot
    "gold": "int32", "current_gold": "int32", "xp": "int32", "level": "int8",
    "cs": "int16", "cs_lane": "int16", "cs_jungle": "int16", "cs_per_min": "float32",
    "hp": "int32", "hp_max": "int32", "hp_pct": "float32",
    "x": "int32", "y": "int32",
    "armor": "int32", "magic_resist": "int32", "attack_damage": "int32",
    "ability_power": "int32", "attack_speed": "int32", "movement_speed": "int32",
    "ability_haste": "int32",
    "dmg_to_champs": "int32", "dmg_taken": "int32", "cc_time": "int32",
    # change since the previous snapshot (same game, same player); null at minute 0
    "gold_delta_1m": "int32", "xp_delta_1m": "int32",
    # cumulative counts from events
    "kills": "int16", "deaths": "int16", "assists": "int16", "kill_participation": "float32",
    "wards_placed": "int16", "wards_killed": "int16", "control_wards": "int16",
    "epic_participation": "int16", "structure_participation": "int16",
    # counts since the previous snapshot
    "kills_m": "int8", "deaths_m": "int8", "assists_m": "int8",
    "wards_placed_m": "int8", "wards_killed_m": "int8",
    # lane opponent (null when the role is unknown)
    "opp_id": "int8", "opp_gold": "int32", "opp_xp": "int32", "opp_level": "int8", "opp_cs": "int16",
    "gold_diff": "int32", "xp_diff": "int32", "level_diff": "int8", "cs_diff": "int16",
}

# One row per timeline event (level-ups excluded; level is in FRAMES).
# Key: (match_id, event_idx).
EVENTS: dict[str, str] = {
    "match_id": "str", "tier": "str", "event_idx": "int32",
    "minute": "int16", "t_ms": "int32",
    "type": "str", "cat": "str",
    "actor_id": "int8",          # killer / creator / buyer; 0 = not a champion
    "victim_id": "int8",
    "team_id": "int16",          # team that acted
    "assists": "list<int8>",
    "x": "int32", "y": "int32",  # as recorded by Riot — see docs/riot-gotchas.md for which types are reliable
    "structure_id": "str",       # lolreview.rift id for turret/inhibitor kills and plates
    "monster": "str",            # FIRE_DRAGON, BARON_NASHOR, HORDE, ...
    "ward_type": "str",
    "item_id": "int32",
    "kill_type": "str",          # KILL_FIRST_BLOOD / KILL_MULTI / KILL_ACE
    "multikill": "int8",
    "bounty": "int32", "shutdown_bounty": "int32",
}

# One row per team per minute — what the Phase 5 win-probability model trains on.
# Key: (match_id, minute, team_id).
TEAMS: dict[str, str] = {
    "match_id": "str", "tier": "str", "patch": "str",
    "minute": "int16", "t_ms": "int32", "team_id": "int16",
    "win": "bool", "game_has_afk": "bool", "afk_players": "int8",
    "gold": "int32", "xp": "int32", "cs": "int16", "level_avg": "float32",
    "kills": "int16", "deaths": "int16",
    "gold_diff": "int32", "xp_diff": "int32", "kill_diff": "int16",
    "towers": "int8", "tower_diff": "int8", "plates": "int8",
    "inhibs_killed": "int8", "enemy_inhibs_down": "int8",
    "dragons": "int8", "dragon_diff": "int8", "elders": "int8", "soul": "bool",
    "barons": "int8", "heralds": "int8", "voidgrubs": "int8", "atakhans": "int8",
    "first_blood": "bool", "first_tower": "bool",
}

TABLES = {"frames": FRAMES, "events": EVENTS, "teams": TEAMS}
KEYS = {
    "frames": ("match_id", "minute", "participant_id"),
    "events": ("match_id", "event_idx"),
    "teams": ("match_id", "minute", "team_id"),
}


_INT_RANGE = {"int8": (-128, 127), "int16": (-32768, 32767), "int32": (-(2**31), 2**31 - 1)}


def check_rows(rows: list[dict], spec: dict[str, str], limit: int = 10) -> list[str]:
    """Problems that would make a Parquet write fail: missing/extra columns, wrong Python
    types, integers out of range. Pure Python, so it runs where pyarrow isn't installed."""
    problems: list[str] = []
    for i, r in enumerate(rows):
        if set(r) != set(spec):
            problems.append(f"row {i}: columns differ: missing {set(spec) - set(r)}, extra {set(r) - set(spec)}")
        for name, typ in spec.items():
            v = r.get(name)
            if v is None:
                continue
            ok = (
                isinstance(v, str) if typ == "str"
                else isinstance(v, bool) if typ == "bool"
                else isinstance(v, (int, float)) and not isinstance(v, bool) if typ == "float32"
                else isinstance(v, list) and all(isinstance(x, int) for x in v) if typ.startswith("list")
                else isinstance(v, int) and not isinstance(v, bool)
            )
            if not ok:
                problems.append(f"row {i}: {name}={v!r} is not {typ}")
            elif typ in _INT_RANGE and not (_INT_RANGE[typ][0] <= v <= _INT_RANGE[typ][1]):
                problems.append(f"row {i}: {name}={v} out of {typ} range")
        if len(problems) >= limit:
            break
    return problems


def arrow_schema(spec: dict[str, str]):
    import pyarrow as pa

    simple = {
        "str": pa.string(), "bool": pa.bool_(), "int8": pa.int8(), "int16": pa.int16(),
        "int32": pa.int32(), "float32": pa.float32(), "list<int8>": pa.list_(pa.int8()),
    }
    return pa.schema([(name, simple[t]) for name, t in spec.items()])
