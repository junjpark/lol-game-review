"""Phase 6: clustering, percentiles, death timers, involvement, selection, direct cost, replay markers."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from synthetic import make_game

from lolreview.extract import extract_game
from lolreview.review import death_timer as dt
from lolreview.review import moments as mo


def kill(t, x=5000, y=5000, killer=1, victim=6, team=100, assists=()):
    return {"type": "CHAMPION_KILL", "t_ms": t, "x": x, "y": y, "actor_id": killer, "victim_id": victim,
            "team_id": team, "assists": list(assists)}


def obj(t, x=9800, y=4400, team=100, actor=2, monster="FIRE_DRAGON", assists=()):
    return {"type": "ELITE_MONSTER_KILL", "t_ms": t, "x": x, "y": y, "actor_id": actor, "team_id": team,
            "monster": monster, "assists": list(assists)}


# ---- clustering -------------------------------------------------------------------------

def test_kills_close_in_time_and_space_form_one_fight():
    cl = mo.cluster_events([kill(100_000), kill(105_000, x=5600), kill(118_000, x=6000)])
    assert len(cl) == 1 and len(cl[0]) == 3


def test_simultaneous_kills_far_apart_are_separate_fights():
    cl = mo.cluster_events([kill(100_000, x=1000, y=1000), kill(103_000, x=13000, y=13000)])
    assert len(cl) == 2


def test_nearby_events_without_a_shared_player_stay_separate():
    # base siege: two towers ~2,500 units apart, different players, seconds apart
    t1 = {"type": "BUILDING_KILL", "t_ms": 100_000, "x": 3651, "y": 3696, "actor_id": 8, "team_id": 200,
          "assists": [], "structure_id": "100_mid_base"}
    t2 = {"type": "BUILDING_KILL", "t_ms": 106_000, "x": 1169, "y": 4287, "actor_id": 9, "team_id": 200,
          "assists": [], "structure_id": "100_top_base"}
    assert len(mo.cluster_events([t1, t2])) == 2
    t2["assists"] = [8]                                      # same player involved → same push
    assert len(mo.cluster_events([t1, t2])) == 1


def test_parts_keep_their_own_place_and_involvement():
    match, timeline = make_game()
    from lolreview.rift import region_of
    ev_top = kill(70_000, x=1000, y=10000, killer=1, victim=6)                 # top lane 1v1, by player 1
    ev_bot = kill(75_000, x=12000, y=1500, killer=4, victim=9, assists=[5])   # bot lane 2v1, player 1 not there
    groups = mo.merge_by_snapshot(mo.cluster_events([ev_top, ev_bot]), [0, 60_000, 120_000])
    assert len(groups) == 1 and len(groups[0]) == 2
    team_of = {p: 100 if p <= 5 else 200 for p in range(1, 11)}
    names = {p: f"C{p}" for p in range(1, 11)}
    parts = [mo._part(c, 1, 100, 200, {}, [0, 60_000, 120_000], lambda t: False, names, team_of) for c in groups[0]]
    assert parts[0]["where"] == "top lane" and parts[0]["text"] == "Top lane: you killed C6"
    assert parts[1]["where"] == "bot lane" and parts[1]["involvement"] == "none"
    assert parts[1]["text"] == "Bot lane: 2v1 skirmish, your team won 1–0 (you weren't there)"


def test_region_labels():
    from lolreview.rift import CAMPS, STRUCTURES, region_of
    for s in STRUCTURES:
        if s["tier"] in ("outer", "inner"):
            assert region_of(s["x"], s["y"]) == f"{s['lane']} lane"
    assert region_of(9837, 4405) == "Dragon pit" and region_of(10500, 5170) == "river (bot side)"
    assert region_of(1000, 1000, team=100) == "your base" and region_of(1000, 1000, team=200) == "enemy base"
    blue_buff = next(c for c in CAMPS if c["id"] == "100_blue_buff")
    assert region_of(blue_buff["x"], blue_buff["y"], team=200) == "enemy jungle (top side)"


def test_kills_shortly_before_an_objective_join_it():
    cl = mo.cluster_events([kill(60_000, x=9500, y=4300), obj(85_000)])
    assert len(cl) == 1                      # 25 s before, ≤ 3,000 units → fight for the objective
    cl = mo.cluster_events([kill(60_000, x=9500, y=4300), obj(95_000)])
    assert len(cl) == 2                      # 35 s before → separate


def test_long_chains_are_split():
    evs = [kill(60_000 + i * 14_000, x=5000 + (i % 2) * 100) for i in range(20)]   # 4.4 minutes of chained kills
    cl = mo.cluster_events(evs)
    assert len(cl) >= 2
    assert all(c[-1]["t_ms"] - c[0]["t_ms"] <= (mo.SPEC["max_span_min"] - 1) * 60_000 for c in cl)


def test_fights_between_the_same_snapshots_merge_into_one_moment():
    snaps = [0, 60_000, 120_000, 180_000]
    a = [kill(70_000, x=1000, y=1000)]
    b = [kill(80_000, x=13000, y=13000, killer=7, victim=2, team=200)]
    groups = mo.merge_by_snapshot([a, b], snaps)
    assert len(groups) == 1 and len(groups[0]) == 2        # one moment, two separate parts
    c = [kill(150_000, x=13000, y=13000)]
    assert len(mo.merge_by_snapshot([a, c], snaps)) == 2


# ---- percentiles ---------------------------------------------------------------------------

def _table(scale=0.1):
    qs = (0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 0.975, 0.99, 0.995)
    cell = {f"q{q * 100:g}": scale * q for q in qs} | {"rows": 1000}
    return {"quantiles": [0.5, 0.75, 0.9, 0.95, 0.99],
            "by_minute": {str(m): {"q50": 0.05, "q75": 0.075, "q90": 0.09, "q95": 0.095, "q99": 0.099, "rows": 9}
                          for m in range(1, 41)},
            "window_quantiles": list(qs), "windows": {str(k): {str(m): cell for m in range(0, 41)} for k in (1, 2, 3)}}


def test_swing_percentile_interpolates_and_is_symmetric():
    t = _table()
    p, note = mo.swing_percentile(t, 1, 10, 0.09)
    assert note is None and p == pytest.approx(90)
    assert mo.swing_percentile(t, 1, 10, -0.09)[0] == pytest.approx(90)
    assert mo.swing_percentile(t, 1, 10, 0.5)[0] > 99.5
    assert mo.swing_percentile(t, 1, 99, 0.09)[0] == pytest.approx(90)     # minute past the table: last row


def test_swing_percentile_falls_back_without_windows():
    t = _table(); t.pop("windows")
    p, note = mo.swing_percentile(t, 2, 10, 0.075)
    assert p == pytest.approx(75) and "windows" in note


def test_labels():
    assert [mo.label_for(x) for x in (80, 90, 99.5)] == ["moderate", "notable", "major"]


# ---- death timers -----------------------------------------------------------------------------

def test_death_timer_table_is_monotone_and_has_fallbacks():
    assert dt.death_seconds(18, 30) >= dt.death_seconds(10, 30) >= dt.death_seconds(3, 5)
    assert dt.death_seconds(12, 33) >= dt.death_seconds(12, 16)
    assert dt.death_seconds(1, 50) == dt.TABLE[1][0]          # no late-game cell for level 1: nearest bucket
    assert dt.death_seconds(30, 20) == dt.death_seconds(20, 20)   # clamp to highest level measured


def test_fit_table_recovers_a_planted_timer():
    rng = np.random.default_rng(0)
    rows = []
    for lvl, true_t in ((5, 15.0), (10, 33.0)):
        since = rng.uniform(0, 90, 3000)
        dead = since < true_t
        flip = rng.uniform(size=3000) < 0.1               # noisy health signal, like the real one
        rows.append(pd.DataFrame({"minute": 10.0, "level": lvl, "since_s": since, "dead": dead ^ flip}))
    t = dt.fit_table(pd.concat(rows))
    assert t[5][0] == pytest.approx(15, abs=2) and t[10][0] == pytest.approx(33, abs=2)


def test_death_rows_from_tables():
    import sys
    sys.path.insert(0, "scripts")
    from build_death_timers import death_rows
    frames = pd.DataFrame([{"match_id": "A", "minute": m, "t_ms": m * 60_000, "participant_id": 6,
                            "hp": 0 if m == 1 else 500, "level": 5} for m in range(4)])
    events = pd.DataFrame([{"match_id": "A", "t_ms": 50_000, "type": "CHAMPION_KILL", "victim_id": 6}])
    r = death_rows(frames, events)
    assert r["since_s"].tolist() == [10.0, 70.0] and r["dead"].tolist() == [True, False]


# ---- the review on a synthetic game -------------------------------------------------------------

def _review(pid, p_blue=(0.5, 0.8, 0.7), move=None):
    match, timeline = make_game()
    if move:
        for frame, who, x, y in move:
            timeline["info"]["frames"][frame]["participantFrames"][str(who)]["position"] = {"x": x, "y": y}
    ex = extract_game(match, timeline, "emerald")
    curve = pd.DataFrame({"minute": [0, 1, 2], "t_ms": [0, 60_000, 120_000], "p_blue": list(p_blue)})
    return mo.review_game(match, timeline, pid, extracted=ex, curve=curve, swing_table=_table())


def test_killer_gets_a_good_direct_moment():
    r = _review(1)
    assert len(r.moments) == 1
    m = r.moments[0]
    assert m.involvement == "direct" and any("killed C6" in d for d in m.involvement_detail)
    assert m.parts and m.parts[0]["text"].endswith("you killed C6") is False   # 1 kill + 1 assist = 3 people: a skirmish
    assert m.direction == "good" and m.swing == pytest.approx(0.3) and m.label == "major"
    assert m.direct_cost is None


def test_victim_gets_the_same_moment_as_bad_with_a_death_cost():
    r = _review(6)
    m = r.moments[0]
    assert m.direction == "bad" and m.swing == pytest.approx(-0.3) and any("died to C1" in d for d in m.involvement_detail)
    dc = m.direct_cost
    assert dc["seconds_dead"] == pytest.approx(dt.death_seconds(1, 0.5))
    assert dc["lane_opponent"] is not None and "xp_forgone" in dc


def test_far_away_player_sees_it_as_context_not_their_moment():
    far = [(f, 5, 14000, 14000) for f in range(3)]      # Blue support far from the bot-side kill
    r = _review(5, move=far)
    assert r.moments == [] and len(r.context) == 1 and r.context[0].involvement == "none"


def test_small_swings_are_not_shown():
    r = _review(1, p_blue=(0.5, 0.53, 0.53))           # 3 pts < 5-pt floor, even if 'top percentile'
    assert r.moments == []


def test_selection_respects_spacing_and_overlap():
    def m(start, pct):
        return mo.Moment(rank=None, kind="fight", minute_start=start, minute_end=start + 1, t_first_ms=None,
                         t_last_ms=None, swing=0.1, p_start=0.5, p_end=0.6, percentile=pct, label="notable",
                         direction="good", involvement="direct")
    picked = mo._select([m(10, 95), m(11, 99), m(15, 80)], 8, [])
    assert [x.minute_start for x in picked] == [11, 15]          # 10 is within 2 min of the better 11
    assert mo._select([m(15, 99)], 3, [m(15, 50)]) == []          # context can't overlap your moments


def test_review_serialises_and_makes_replay_markers():
    import json
    r = _review(1)
    json.dumps(r.to_dict())
    marks = mo.moments_markers(r)
    assert marks[0]["mine"] and marks[0]["t0"] == 0 and marks[0]["frame"] == 1
    from lolreview.viz.replay import build_replay_data, render_html
    match, timeline = make_game()
    html = render_html(build_replay_data(match, timeline, winprob=[0.5, 0.8, 0.7], moments=marks))
    assert 'id="moments-sec"' in html and '"moments":[{' in html


# ---- swing table windows ---------------------------------------------------------------------

def test_window_swings_stay_inside_one_game():
    from lolreview.model.predict import window_swings
    df = pd.DataFrame({"match_id": ["A"] * 4 + ["B"] * 2, "minute": [0, 1, 2, 3, 0, 1]})
    s = window_swings(df, [0.5, 0.6, 0.8, 0.7, 0.2, 0.3], 2)
    assert s["minute"].tolist() == [0, 1] and np.allclose(s["abs"], [0.3, 0.1])


def test_display_names():
    assert [mo.display_name(n) for n in ("MonkeyKing", "MissFortune", "AurelionSol", "Aatrox", "KogMaw", "C6")] == \
        ["Wukong", "Miss Fortune", "Aurelion Sol", "Aatrox", "Kog'Maw", "C6"]
