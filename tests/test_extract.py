"""Phase 3 extraction tests on the synthetic game (see tests/synthetic.py)."""

from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path

import pytest
from synthetic import add_events, make_game

from lolreview.extract import extract_game
from lolreview.extract.lanes import lane_opponents
from lolreview.extract.schema import KEYS, TABLES, check_rows
from lolreview.extract.validate import game_checks, summarize


def game(*extra: tuple[int, dict]) -> dict[str, list[dict]]:
    match, timeline = make_game()
    for frame, e in extra:
        add_events(timeline, frame, e)
    return extract_game(match, timeline, "emerald")


def frame(out, minute, pid):
    return next(r for r in out["frames"] if r["minute"] == minute and r["participant_id"] == pid)


def team(out, minute, team_id):
    return next(r for r in out["teams"] if r["minute"] == minute and r["team_id"] == team_id)


# ---- shape -----------------------------------------------------------------------------

def test_row_counts_and_columns_match_schema():
    out = game()
    assert len(out["frames"]) == 3 * 10 and len(out["teams"]) == 3 * 2 and len(out["events"]) == 6
    for name, rows in out.items():
        assert check_rows(rows, TABLES[name]) == [], name


def test_keys_unique_and_sorted():
    out = game()
    for name, rows in out.items():
        keys = [tuple(r[k] for k in KEYS[name]) for r in rows]
        assert len(keys) == len(set(keys)), name
        assert keys == sorted(keys), name


def test_check_rows_catches_bad_values():
    rows = copy.deepcopy(game()["frames"])
    rows[0]["level"] = 500          # out of int8 range
    rows[1]["gold"] = "lots"        # wrong type
    problems = check_rows(rows, TABLES["frames"])
    assert any("level" in p and "range" in p for p in problems)
    assert any("gold" in p for p in problems)


# ---- frames ----------------------------------------------------------------------------

def test_state_fields():
    out = game()
    r = frame(out, 1, 1)
    assert (r["gold"], r["cs"], r["cs_lane"], r["cs_jungle"], r["role"], r["phase"]) == (900, 4, 3, 1, "TOP", "early")
    assert r["cs_per_min"] == pytest.approx(4.0)
    assert frame(out, 2, 4)["hp_pct"] == pytest.approx(0.25)


def test_previous_minute_never_crosses_players_or_minute_zero():
    out = game()
    assert all(r["gold_delta_1m"] is None for r in out["frames"] if r["minute"] == 0)
    assert frame(out, 1, 1)["gold_delta_1m"] == 400 and frame(out, 1, 6)["gold_delta_1m"] == 100
    assert frame(out, 2, 1)["gold_delta_1m"] == -400 and frame(out, 2, 6)["gold_delta_1m"] == -100


def test_lane_opponent_diffs_are_antisymmetric():
    out = game()
    blue, red = frame(out, 1, 1), frame(out, 1, 6)
    assert (blue["opp_id"], red["opp_id"]) == (6, 1)
    assert blue["gold_diff"] == 300 and red["gold_diff"] == -300


def test_cumulative_and_per_minute_kda():
    out = game()
    assert (frame(out, 0, 1)["kills"], frame(out, 1, 1)["kills"], frame(out, 2, 1)["kills"]) == (0, 1, 1)
    assert (frame(out, 1, 1)["kills_m"], frame(out, 2, 1)["kills_m"]) == (1, 0)
    assert frame(out, 1, 6)["deaths"] == 1 and frame(out, 1, 2)["assists"] == 1
    assert frame(out, 2, 3)["deaths"] == 1  # executed — a death, but nobody's kill


def test_kill_participation_counts_executions_in_team_total():
    out = game()
    assert frame(out, 1, 1)["kill_participation"] == pytest.approx(1.0)
    assert frame(out, 1, 3)["kill_participation"] == pytest.approx(0.0)
    assert frame(out, 1, 6)["kill_participation"] is None          # red had no kills yet
    assert frame(out, 2, 6)["kill_participation"] == pytest.approx(0.0)  # red got the execution


def test_only_real_wards_count():
    out = game((2, {"type": "WARD_PLACED", "timestamp": 105_000, "creatorId": 5, "wardType": "UNDEFINED"}),
               (2, {"type": "WARD_PLACED", "timestamp": 106_000, "creatorId": 5, "wardType": "TEEMO_MUSHROOM"}))
    r = frame(out, 2, 5)
    assert (r["wards_placed"], r["control_wards"], r["wards_placed_m"]) == (1, 1, 1)


def test_objective_participation():
    out = game()
    assert frame(out, 2, 2)["epic_participation"] == 1 and frame(out, 2, 7)["epic_participation"] == 1
    assert frame(out, 2, 1)["structure_participation"] == 1


def test_unknown_or_duplicate_roles_get_no_opponent():
    parts = [{"participantId": i, "teamId": 100 if i <= 5 else 200, "teamPosition": r}
             for i, r in enumerate(["TOP", "TOP", "MIDDLE", "", "UTILITY",
                                    "TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"], start=1)]
    opp = lane_opponents(parts)
    assert opp[1] is None and opp[2] is None   # two blue TOPs: ambiguous
    assert opp[6] is None                      # their opponent is ambiguous too
    assert opp[4] is None                      # empty role
    assert (opp[3], opp[8]) == (8, 3) and (opp[5], opp[10]) == (10, 5)


def test_afk_flags():
    match, timeline = make_game()
    next(p for p in match["info"]["participants"] if p["participantId"] == 8)["wasAfk"] = True
    out = extract_game(match, timeline, "emerald")
    assert frame(out, 0, 8)["was_afk"] and not frame(out, 0, 1)["was_afk"]
    assert all(r["game_has_afk"] for r in out["frames"])
    assert team(out, 0, 200)["afk_players"] == 1 and team(out, 0, 100)["afk_players"] == 0


# ---- teams -----------------------------------------------------------------------------

def test_team_rows():
    out = game()
    b1, r1 = team(out, 1, 100), team(out, 1, 200)
    assert (b1["gold"], b1["gold_diff"], b1["kills"], b1["win"]) == (4500, 1500, 1, True)
    assert r1["win"] is False
    b2, r2 = team(out, 2, 100), team(out, 2, 200)
    assert (b2["towers"], b2["tower_diff"], b2["dragons"], r2["voidgrubs"]) == (1, 1, 1, 1)
    assert (b2["kills"], b2["deaths"], r2["kills"], r2["deaths"]) == (1, 1, 1, 1)  # red's kill = execution


def test_team_kills_always_mirror_enemy_deaths_except_monster_deaths():
    monster_death = {"type": "CHAMPION_KILL", "timestamp": 115_000, "killerId": 0, "victimId": 7,
                     "victimDamageReceived": [{"type": "MONSTER"}]}
    out = game((2, monster_death))
    b2, r2 = team(out, 2, 100), team(out, 2, 200)
    assert r2["deaths"] == 2 and b2["kills"] == 1  # dying to a monster is nobody's kill


def test_no_future_leakage_first_blood_and_first_tower():
    fb = {"type": "CHAMPION_SPECIAL_KILL", "timestamp": 30_000, "killerId": 1, "killType": "KILL_FIRST_BLOOD"}
    out = game((1, fb))
    assert [team(out, m, 100)["first_blood"] for m in (0, 1, 2)] == [False, True, True]
    assert [team(out, m, 100)["first_tower"] for m in (0, 1, 2)] == [False, False, True]
    assert not any(team(out, m, 200)["first_blood"] for m in (0, 1, 2))


def test_soul_reveal_is_not_a_soul():
    reveal = {"type": "DRAGON_SOUL_GIVEN", "timestamp": 85_000, "teamId": 0, "name": "Mountain"}
    out = game((2, reveal))
    assert not team(out, 2, 100)["soul"] and not team(out, 2, 200)["soul"]
    ev = next(e for e in out["events"] if e["type"] == "DRAGON_SOUL_GIVEN")
    assert ev["team_id"] is None and ev["cat"] == "other"
    earned = {"type": "DRAGON_SOUL_GIVEN", "timestamp": 86_000, "teamId": 100, "name": "Mountain"}
    assert team(game((2, earned)), 2, 100)["soul"]


def test_inhibitor_down_then_respawns():
    match, timeline = make_game()
    add_events(timeline, 2, {"type": "BUILDING_KILL", "timestamp": 100_000, "teamId": 200, "killerId": 1,
                             "buildingType": "INHIBITOR_BUILDING", "laneType": "MID_LANE",
                             "position": {"x": 11593, "y": 11669}})
    later = copy.deepcopy(timeline["info"]["frames"][0])
    later["timestamp"] = 100_000 + 5 * 60_000 + 1  # just past the 5-minute respawn
    timeline["info"]["frames"].append(later)
    out = extract_game(match, timeline, "emerald")
    assert team(out, 2, 100)["enemy_inhibs_down"] == 1
    assert team(out, 3, 100)["enemy_inhibs_down"] == 0
    assert team(out, 3, 100)["inhibs_killed"] == 1


# ---- events ----------------------------------------------------------------------------

def test_events_table():
    lvl = {"type": "LEVEL_UP", "timestamp": 50_000, "participantId": 1, "level": 2}
    out = game((1, lvl))
    assert not any(e["type"] == "LEVEL_UP" for e in out["events"])
    tower = next(e for e in out["events"] if e["type"] == "BUILDING_KILL")
    assert (tower["team_id"], tower["structure_id"], tower["actor_id"], tower["cat"]) == (100, "200_mid_outer", 1, "special")
    execute = next(e for e in out["events"] if e["type"] == "CHAMPION_KILL" and e["victim_id"] == 3)
    assert (execute["actor_id"], execute["team_id"]) == (0, 200)
    assert [e["event_idx"] for e in out["events"]] == list(range(len(out["events"])))


def test_plate_maps_to_outer_turret_not_event_position():
    plate = {"type": "TURRET_PLATE_DESTROYED", "timestamp": 75_000, "teamId": 200, "killerId": 1,
             "laneType": "TOP_LANE", "position": {"x": 7000, "y": 7000}}
    ev = next(e for e in game((2, plate))["events"] if e["type"] == "TURRET_PLATE_DESTROYED")
    assert (ev["structure_id"], ev["team_id"], ev["cat"]) == ("200_top_outer", 100, "plate")


# ---- validation ------------------------------------------------------------------------

def test_cross_check_against_match_summary():
    match, timeline = make_game()
    p1 = next(p for p in match["info"]["participants"] if p["participantId"] == 1)
    p1.update(kills=1, deaths=0, assists=0, champLevel=1, totalMinionsKilled=3, neutralMinionsKilled=1)
    p6 = next(p for p in match["info"]["participants"] if p["participantId"] == 6)
    p6.update(kills=5)  # wrong on purpose
    s = summarize(game_checks(match, extract_game(match, timeline, "emerald")))
    assert s["kills"]["ok"] == 1 and s["kills"]["off"] == 1
    assert s["cs"]["ok"] == 1 and s["level"]["ok"] == 1
    assert s["lane_symmetry"]["pct_ok"] == 100.0


# ---- parquet round trip (needs pyarrow; runs on any machine with the project installed) ---

def test_build_writes_and_reads_back():
    pytest.importorskip("pyarrow")
    pd = pytest.importorskip("pandas")
    from lolreview.extract.build import build_tier, load
    from lolreview.riot.cache import JsonCache

    root = Path(tempfile.mkdtemp())
    cache = JsonCache(root / "raw")
    match, timeline = make_game()
    cache.put("matches", "NA1_1", match)
    cache.put("timelines", "NA1_1", timeline)
    processed = root / "processed"
    processed.mkdir()
    (processed / "matches_emerald.json").write_text(json.dumps({"kept": ["NA1_1"], "seen": ["NA1_1"]}))

    meta = build_tier("emerald", cache, processed, log=lambda *_: None)
    assert meta["rows"] == {"frames": 30, "events": 6, "teams": 6}

    frames = load("frames", "emerald", processed)
    assert len(frames) == 30 and list(frames.columns) == list(TABLES["frames"])
    assert frames.duplicated(list(KEYS["frames"])).sum() == 0
    assert pd.isna(frames.loc[frames.minute == 0, "gold_delta_1m"]).all()
    events = load("events", "emerald", processed)
    assert events.loc[events.type == "CHAMPION_KILL", "assists"].iloc[0].tolist() == [2]
    teams = load("teams", "emerald", processed, filters=[("minute", "==", 2)])
    assert sorted(teams.team_id) == [100, 200]
