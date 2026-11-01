"""Phase 5 tests: features, metrics, calibration gate, models, report.

The XGBoost and neural-net tests are skipped where those packages aren't installed.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from synthetic import add_events, make_game

from lolreview.extract import extract_game
from lolreview.model import metrics, models, report
from lolreview.model.dataset import (
    FEATURE_NAMES, MONOTONE, build_features, features_for_game, split_of,
)
from lolreview.model.predict import swings, wpa_distribution


# ---- dataset ---------------------------------------------------------------

def test_split_is_deterministic_and_roughly_70_15_15():
    ids = [f"NA1_{i}" for i in range(20_000)]
    s = pd.Series([split_of(i) for i in ids]).value_counts(normalize=True)
    assert split_of("NA1_123") == split_of("NA1_123")
    assert abs(s["train"] - 0.70) < 0.02 and abs(s["val"] - 0.15) < 0.02


def test_features_for_synthetic_game():
    match, timeline = make_game()
    add_events(timeline, 1, {"type": "CHAMPION_SPECIAL_KILL", "timestamp": 30_010, "killerId": 1,
                             "killType": "KILL_FIRST_BLOOD", "position": {"x": 0, "y": 0}})
    f = features_for_game(extract_game(match, timeline, "emerald"))
    assert list(f["minute"]) == [0, 1, 2]                      # every frame, final one kept
    assert f.loc[0, ["gold_diff", "kill_diff", "tower_diff", "first_blood"]].abs().sum() == 0
    assert f.loc[1, "gold_diff"] == 5 * (900 - 600)
    assert f.loc[1, "gold_diff_3m"] == f.loc[1, "gold_diff"]   # change since minute 0
    assert f.loc[1, "first_blood"] == 1                        # Blue's kill at 0:30
    assert f.loc[2, "tower_diff"] == 1 and f.loc[2, "blue_dragons"] == 1
    assert f.loc[1, "red_recent_deaths"] == 1                  # died at 0:30, frame at 1:00
    assert f.loc[2, "red_recent_deaths"] == 0                  # more than 60 s ago at 2:00
    assert f.loc[2, "blue_recent_deaths"] == 1                 # executed by tower at 1:30
    assert (f["blue_win"] == 1).all() and f[FEATURE_NAMES].notna().all().all()


def test_gold_spread_features_match_the_timeline():
    match, timeline = make_game()   # minute 1: every Blue player 900 gold, every Red player 600
    f = features_for_game(extract_game(match, timeline, "emerald")).set_index("minute")
    for col in ("gold_diff_top", "gold_diff_jungle", "gold_diff_mid", "gold_diff_bot", "gold_diff_support"):
        assert f.loc[1, col] == 300
    assert f.loc[1, "richest_gap"] == 300
    assert f.loc[1, "blue_gold_top_share"] == pytest.approx(0.2)    # evenly spread: 1/5 each
    assert f.loc[1, ["gold_diff_top", "gold_diff_jungle", "gold_diff_mid", "gold_diff_bot",
                     "gold_diff_support"]].sum() == f.loc[1, "gold_diff"]


def test_fed_carry_shows_up_in_spread_features():
    match, timeline = make_game()
    pf = timeline["info"]["frames"][1]["participantFrames"]
    for pid in range(1, 6):                       # Blue: one fed carry (BOT, pid 4), rest poor
        pf[str(pid)]["totalGold"] = 2300 if pid == 4 else 400
    for pid in range(6, 11):                      # Red: 3,000 total, evenly spread
        pf[str(pid)]["totalGold"] = 600
    f = features_for_game(extract_game(match, timeline, "emerald")).set_index("minute")
    assert f.loc[1, "gold_diff"] == 900
    assert f.loc[1, "richest_gap"] == 1700
    assert f.loc[1, "gold_diff_bot"] == 1700 and f.loc[1, "gold_diff_top"] == -200
    assert f.loc[1, "blue_gold_top_share"] == pytest.approx(2300 / 3900)
    assert f.loc[1, "red_gold_top_share"] == pytest.approx(0.2)


def test_missing_role_blanks_only_that_role():
    from lolreview.model.dataset import ROLE_FEATURES
    match, timeline = make_game()
    for p in match["info"]["participants"]:
        if p["participantId"] == 1:               # Blue TOP lost its role in Riot's data
            p["teamPosition"] = ""
    f = features_for_game(extract_game(match, timeline, "emerald")).set_index("minute")
    assert np.isnan(f.loc[1, "gold_diff_top"])
    assert not f.loc[1, [c for c in ROLE_FEATURES if c != "gold_diff_top"]].isna().any()
    assert f.loc[1, "richest_gap"] == 300         # team-level spread features don't need roles


def test_gold_spread_check_table():
    rows = []
    for i in range(400):
        fed = i % 2 == 0
        rows.append({"match_id": f"G{i}", "minute": 20, "gold_diff": 0.0,
                     "richest_gap": 2000.0 if fed else -500.0, "blue_win": int(fed and i % 4 == 0 or (not fed and i % 8 == 1))})
    df = pd.DataFrame(rows)
    t = metrics.gold_spread_check(df, {"flat": np.full(len(df), 0.5)})
    assert list(t["case"]) == ["Blue has the richest player by 1.5k+", "Red has the richest player"]
    assert t["games"].tolist() == [200, 200] and t["flat"].tolist() == [0.5, 0.5]
    assert t["Blue won"].iloc[0] > t["Blue won"].iloc[1]


def test_baron_buff_window():
    match, timeline = make_game()
    add_events(timeline, 1, {"type": "ELITE_MONSTER_KILL", "timestamp": 50_000, "killerId": 7,
                             "killerTeamId": 200, "monsterType": "BARON_NASHOR",
                             "position": {"x": 5000, "y": 10400}})
    f = features_for_game(extract_game(match, timeline, "emerald"))
    assert list(f["red_baron_active"]) == [0, 1, 1]            # 1:00 and 2:00 within 3:00 of 0:50
    assert f["blue_baron_active"].sum() == 0
    assert list(f["red_barons"]) == [0, 1, 1]


def _team_rows(match_id, minutes, gold_blue, gold_red, win_blue=True, tier="emerald"):
    rows = []
    for m in minutes:
        for team, gold, win in ((100, gold_blue[m], win_blue), (200, gold_red[m], not win_blue)):
            rows.append({"match_id": match_id, "tier": tier, "patch": "16.20", "minute": m,
                         "t_ms": m * 60_000, "team_id": team, "win": win, "game_has_afk": False,
                         "gold": gold, "xp": 0, "cs": 0, "level_avg": 1.0, "kills": 0, "towers": 0,
                         "plates": 0, "enemy_inhibs_down": 0, "dragons": 0, "soul": False,
                         "barons": 0, "heralds": 0, "voidgrubs": 0, "first_blood": False,
                         "first_tower": False})
    return rows


def test_momentum_never_crosses_games_and_final_snapshot_dropped():
    a = _team_rows("A", range(6), [0, 100, 200, 300, 400, 500], [0] * 6)
    b = _team_rows("B", range(6), [0, 0, 0, 0, 0, 0], [0, 50, 100, 150, 200, 250], win_blue=False)
    events = pd.DataFrame(columns=["match_id", "t_ms", "type", "monster", "team_id"])
    f = build_features(pd.DataFrame(a + b), events)
    assert f.groupby("match_id")["minute"].max().tolist() == [4, 4]   # minute 5 dropped
    fb = f[f.match_id == "B"].set_index("minute")
    assert fb.loc[1, "gold_diff_3m"] == -50       # not contaminated by game A's last rows
    assert fb.loc[4, "gold_diff_3m"] == -150 and fb.loc[4, "gold_diff_5m"] == -200


def test_build_dataset_keeps_a_shared_game_once(monkeypatch):
    from lolreview.extract import build as build_mod
    from lolreview.model import dataset

    teams = {
        "emerald": pd.DataFrame(_team_rows("X", range(4), [0] * 4, [0] * 4) + _team_rows("E", range(4), [0] * 4, [0] * 4)),
        "diamond": pd.DataFrame(_team_rows("X", range(4), [0] * 4, [0] * 4, tier="diamond")
                                + _team_rows("D", range(4), [0] * 4, [0] * 4, tier="diamond")),
    }
    empty = pd.DataFrame(columns=["match_id", "t_ms", "type", "monster", "team_id"])
    monkeypatch.setattr(build_mod, "load",
                        lambda name, tier, *a, **k: teams[tier] if name == "teams" else empty)
    df = dataset.build_dataset(("emerald", "diamond"))
    owners = df.groupby("match_id")["tier"].unique().map(list).to_dict()
    assert owners == {"D": ["diamond"], "E": ["emerald"], "X": ["emerald"]}
    assert not df.duplicated(["match_id", "minute"]).any()


# ---- metrics and gate --------------------------------------------------------

def _calibrated_sample(n_games=3000, per_game=20, seed=0, shift=0.0):
    """Each game has a true win chance q; every row of it predicts q (+ shift)."""
    rng = np.random.default_rng(seed)
    q = rng.uniform(0.02, 0.98, n_games)
    y = (rng.uniform(size=n_games) < q).astype(int)
    m = np.tile(np.arange(per_game) + 1, n_games)
    df = pd.DataFrame({"match_id": np.repeat([f"G{i}" for i in range(n_games)], per_game),
                       "minute": m, "blue_win": np.repeat(y, per_game),
                       "tier": "emerald", "patch": "16.20"})
    p = np.clip(np.repeat(q, per_game) + shift, 0.001, 0.999)
    return df, p


def test_point_metrics():
    y, p = np.array([1, 0, 1, 0]), np.array([0.9, 0.1, 0.6, 0.4])
    assert metrics.accuracy(y, p) == 1.0
    assert metrics.brier(y, p) == pytest.approx((0.01 + 0.01 + 0.16 + 0.16) / 4)
    assert metrics.log_loss(y, p) == pytest.approx(-np.mean(np.log([0.9, 0.9, 0.6, 0.6])))


def test_ece_and_reliability_count_games_not_rows():
    df, p = _calibrated_sample()
    rel = metrics.reliability(df.blue_win, p, df.match_id)
    assert rel["rows"].sum() == len(df)
    assert rel["games"].sum() == 3000          # each game's prediction sits in one bin here
    # A perfectly calibrated model still shows ECE from coin-flip noise, and the noise
    # scales with GAMES per bin, not rows: ~300 games/bin -> ~3 pts; 20,000 games -> ~1.
    big, pb = _calibrated_sample(n_games=20_000, per_game=2)
    assert metrics.ece(big.blue_win, pb) < 0.015
    e, lo, hi = metrics.ece_interval(df.blue_win, p, df.match_id)
    assert 0 < lo < hi and e < hi


def test_gate_passes_calibrated_and_fails_shifted():
    df, p = _calibrated_sample(n_games=6000)
    assert metrics.calibration_gate(df, p).passed
    df2, p2 = _calibrated_sample(n_games=6000, shift=0.08)
    res = metrics.calibration_gate(df2, p2)
    assert not res.passed
    assert set(res.checks["group"]) >= {"overall", "phase", "tier"}


def test_folds_are_balanced_and_independent_of_split():
    from lolreview.model.dataset import fold_of

    ids = [f"NA1_{i}" for i in range(20_000)]
    t = pd.DataFrame({"fold": [fold_of(i) for i in ids], "split": [split_of(i) for i in ids]})
    assert t["fold"].value_counts(normalize=True).between(0.18, 0.22).all()
    # every fold holds train, val and test games in roughly the overall proportions
    share = pd.crosstab(t["fold"], t["split"], normalize="index")["test"]
    assert share.between(0.12, 0.18).all()


def test_production_gate_scores_tier_and_patch_out_of_fold():
    test, p_test = _calibrated_sample(n_games=6000, seed=1)
    everything, p_all = _calibrated_sample(n_games=6000, seed=2)
    everything.loc[everything.index % 2 == 0, "tier"] = "diamond"
    g = metrics.production_gate(test, p_test, everything, p_all)
    src = g.checks.groupby("group")["source"].first().to_dict()
    assert src["overall"] == src["phase"] == "test games"
    assert src["tier"] == "all games, out-of-fold" and "patch" not in src   # one patch only
    assert g.passed
    # a fluke in the out-of-fold half fails the gate even if test games are fine
    bad = metrics.production_gate(test, p_test, everything, np.clip(p_all + 0.1, 0, 1))
    assert not bad.passed and bad.checks.loc[~bad.checks.passed, "group"].eq("tier").all()


def _frame(rows):
    base = {f: 0.0 for f in FEATURE_NAMES}
    return pd.DataFrame([{**base, "match_id": g, "minute": m, **kw} for g, m, kw in rows])


def test_decisive_events_signs_and_game_boundaries():
    df = _frame([
        ("A", 30, {}),
        ("A", 31, {"blue_baron_active": 1}),                      # Blue took Baron
        ("A", 32, {"blue_baron_active": 1}),                      # still active: not a new event
        ("A", 33, {"blue_baron_active": 1, "blue_inhibs_down": 1}),   # Red took a Blue inhib
        ("A", 34, {"red_recent_deaths": 4, "blue_recent_deaths": 1}), # Blue won a fight 4-for-1
        ("B", 10, {"red_elder_active": 1}),                       # first row of a game: no previous
        ("B", 11, {"red_elder_active": 1, "blue_recent_deaths": 2}),  # margin 2 < 3: not decisive
    ])
    assert metrics.decisive_events(df).tolist() == [0, 1, 0, -1, 1, 0, 0]


def test_volatility_profile_rewards_moving_on_events():
    df = _frame([("A", 25, {}), ("A", 26, {"blue_baron_active": 1}), ("A", 27, {"blue_baron_active": 1}),
                 ("A", 28, {"blue_baron_active": 1})])
    flat = metrics.volatility_profile(df, [0.5, 0.5, 0.5, 0.5]).set_index("phase").loc["late (25+)"]
    sharp = metrics.volatility_profile(df, [0.5, 0.8, 0.82, 0.95]).set_index("phase").loc["late (25+)"]
    assert flat["event_move"] == 0 and sharp["event_move"] == pytest.approx(0.3)
    assert sharp["extreme"] == pytest.approx(0.25) and sharp["events"] == 1
    assert sharp["quiet_move"] == pytest.approx((0.02 + 0.13) / 2)


def test_paired_gain_sign():
    df, p = _calibrated_sample()
    base = np.full(len(p), 0.5)
    mean, lo, hi = metrics.paired_log_loss_gain(df.blue_win, base, p, df.match_id)
    assert mean > 0 and lo > 0


# ---- models --------------------------------------------------------------

def test_simple_logistic_recovers_signal():
    rng = np.random.default_rng(1)
    n = 20_000
    df = pd.DataFrame({"gold_diff": rng.normal(0, 3000, n), "minute": rng.integers(1, 40, n)})
    z = 0.4 * df.gold_diff / 1000 + 0.05 * (df.gold_diff / 1000) * (df.minute / 10)
    y = (rng.uniform(size=n) < 1 / (1 + np.exp(-z))).astype(int)
    m = models.SimpleLogistic().fit(df, y)
    assert m.w[1] == pytest.approx(0.4, abs=0.05) and m.w[3] == pytest.approx(0.05, abs=0.03)


def test_isotonic_is_monotone_and_fixes_a_shift():
    df, p = _calibrated_sample(n_games=4000, shift=0.1)
    knots = models.fit_isotonic(p, df.blue_win.to_numpy())
    fixed = models.apply_isotonic(p, knots)
    assert np.all(np.diff(knots[1]) >= 0)
    assert metrics.ece(df.blue_win, fixed) < metrics.ece(df.blue_win, p) / 2


def test_time_calibrator_softens_only_where_overconfident():
    rng = np.random.default_rng(3)
    n = 60_000
    minute = rng.integers(1, 45, n)
    true_logit = rng.normal(0, 1.5, n)
    y = (rng.uniform(size=n) < 1 / (1 + np.exp(-true_logit))).astype(int)
    # model is right early, 1.5x overconfident late
    inflate = np.where(minute >= 25, 1.5, 1.0)
    p = 1 / (1 + np.exp(-true_logit * inflate))
    cal = models.TimeCalibrator().fit(p, y, minute)
    t5, t40 = cal.trust([5, 40])
    assert 0.9 < t5 < 1.1 and t40 < 0.85
    fixed = cal.apply(p, minute)
    late = minute >= 25
    assert metrics.ece(y[late], fixed[late]) < metrics.ece(y[late], p[late])
    # never reorders predictions at a given minute, and lopsided stays lopsided
    grid = np.linspace(0.01, 0.99, 99)
    out = cal.apply(grid, np.full(99, 40))
    assert np.all(np.diff(out) > 0) and out[-1] > 0.9
    again = models.calibrator_from_dict(cal.to_dict())
    assert np.allclose(again.apply(grid, np.full(99, 30)), cal.apply(grid, np.full(99, 30)))


def test_cross_fit_calibration_fixes_late_overconfidence_out_of_fold():
    rng = np.random.default_rng(5)
    n_games, per = 4000, 10
    minute = np.tile(np.arange(20, 40, 2), n_games)
    logit = np.repeat(rng.normal(0, 1.5, n_games), per)
    y = np.repeat((rng.uniform(size=n_games) < 1 / (1 + np.exp(-logit[::per]))).astype(int), per)
    p = 1 / (1 + np.exp(-logit * np.where(minute >= 25, 1.5, 1.0)))
    df = pd.DataFrame({"match_id": np.repeat([f"G{i}" for i in range(n_games)], per),
                       "minute": minute, "blue_win": y})
    out = models.cross_fit_time_calibration(p, df)
    late = minute >= 25
    assert out.shape == p.shape
    assert metrics.ece(y[late], out[late]) < metrics.ece(y[late], p[late])


def test_patch_slices_name_newer_patches_correctly():
    df = pd.DataFrame({"minute": [5, 5, 5], "tier": "emerald", "patch": ["16.21", "16.22", "16.21"],
                       "match_id": ["a", "b", "c"], "blue_win": [1, 0, 1]})
    names = [s[1] for s in metrics.subsets(df) if s[0] == "patch"]
    assert names == ["16.21", "other patches (16.22)"]
    df["patch"] = ["16.21", "16.20", "16.21"]
    assert [s[1] for s in metrics.subsets(df) if s[0] == "patch"] == ["16.21", "before 16.21"]


def test_isotonic_calibrator_reads_old_meta_format():
    cal = models.calibrator_from_dict({"x": [0.1, 0.9], "y": [0.2, 0.8]})   # v1 files had no "kind"
    assert np.allclose(cal.apply(np.array([0.1, 0.5, 0.9]), None), [0.2, 0.5, 0.8])


def _synthetic_training(n_games=1500, seed=0):
    """Feature table where Blue's chance depends on gold diff and minute."""
    rng = np.random.default_rng(seed)
    rows = []
    for g in range(n_games):
        trend = rng.normal(0, 150)
        win_logit = trend / 60
        y = int(rng.uniform() < 1 / (1 + np.exp(-win_logit)))
        for m in range(1, 21):
            r = {f: 0.0 for f in FEATURE_NAMES}
            r.update(minute=m, gold_diff=trend * m + rng.normal(0, 500))
            rows.append({"match_id": f"G{g}", "blue_win": y, "tier": "emerald", "patch": "16.20", **r})
    df = pd.DataFrame(rows)
    df["split"] = df["match_id"].map(split_of)
    return df


def test_xgb_trains_respects_monotonicity_and_round_trips(tmp_path):
    xgb = pytest.importorskip("xgboost")
    df = _synthetic_training()
    train, val = df[df.split == "train"], df[df.split != "train"]
    booster = models.train_xgb(train, val, {"n_estimators": 200, "early_stopping_rounds": 20})
    probe = pd.DataFrame([{**{f: 0.0 for f in FEATURE_NAMES}, "minute": 15, "gold_diff": g}
                          for g in np.linspace(-8000, 8000, 41)])
    p = models.predict_xgb(booster, probe)
    assert np.all(np.diff(p) >= -1e-9)                 # more gold never lowers P(Blue)
    assert p[-1] > 0.7 and p[0] < 0.3
    size = models.xgb_size(booster)
    assert size["trees"] >= 1 and size["learned_numbers"] > 0

    from lolreview.model.predict import META_FILE, MODEL_FILE, WinProb
    booster.save_model(str(tmp_path / MODEL_FILE))
    (tmp_path / META_FILE).write_text(json.dumps({"features": FEATURE_NAMES, "calibrator": None,
                                                  "gate_passed": True}))
    wp = WinProb.load(tmp_path)
    assert np.allclose(wp.predict(probe), p)
    match, timeline = make_game()
    curve = wp.for_game(extract_game(match, timeline, "emerald"))
    assert list(curve.columns) == ["minute", "t_ms", "p_blue"] and len(curve) == 3


def test_tune_xgb_picks_lowest_validation_log_loss():
    pytest.importorskip("xgboost")
    df = _synthetic_training(n_games=800)
    train, val = df[df.split == "train"], df[df.split != "train"].sort_values(["match_id", "minute"])
    grid = [{"max_depth": 2, "min_child_weight": 50, "n_estimators": 60, "early_stopping_rounds": 10},
            {"max_depth": 3, "min_child_weight": 200, "n_estimators": 60, "early_stopping_rounds": 10}]
    booster, params, table = models.tune_xgb(train, val, grid, log=lambda *_: None)
    assert len(table) == 2 and table["chosen"].sum() == 1
    assert table.loc[table["chosen"], "val_log_loss"].iloc[0] == table["val_log_loss"].min()
    assert params in grid


def test_archived_v1_model_still_loads_and_predicts(tmp_path):
    pytest.importorskip("xgboost")
    from lolreview.model.dataset import V1_FEATURE_NAMES
    from lolreview.model.predict import META_FILE, MODEL_FILE, WinProb
    df = _synthetic_training(n_games=600)
    train, val = df[df.split == "train"], df[df.split != "train"]
    v1 = models.train_xgb(train, val, {"n_estimators": 50, "early_stopping_rounds": 10}, V1_FEATURE_NAMES)
    assert v1.feature_names == V1_FEATURE_NAMES
    v1.save_model(str(tmp_path / MODEL_FILE))
    (tmp_path / META_FILE).write_text(json.dumps({"features": V1_FEATURE_NAMES, "calibrator": None,
                                                  "gate_passed": True}))
    wp = WinProb.load(tmp_path)
    assert wp.version == "v1"
    p = wp.predict(val)                              # val has all 37 columns; v1 uses its 29
    assert np.allclose(p, models.predict_xgb(v1, val))


def test_monotone_list_matches_features():
    assert len(MONOTONE) == len(FEATURE_NAMES) and set(MONOTONE) <= {-1, 0, 1}


def test_mlp_trains():
    pytest.importorskip("sklearn")
    df = _synthetic_training(n_games=600)
    train, val = df[df.split == "train"], df[df.split != "train"]
    res = models.train_mlp(train, val, hidden=(8,), max_epochs=4, patience=2, log=lambda *_: None)
    p = res.predict(val)
    assert p.shape == (len(val),) and 1 <= res.epochs <= 4 and res.n_params > 0


# ---- swings and report ----------------------------------------------------------

def test_swings_only_between_consecutive_minutes_of_one_game():
    df = pd.DataFrame({"match_id": ["A", "A", "A", "B", "B"], "minute": [1, 2, 4, 1, 2]})
    s = swings(df, [0.5, 0.6, 0.9, 0.2, 0.1])
    assert s["minute"].tolist() == [2, 2]
    assert np.allclose(s["delta"], [0.1, -0.1])


def test_wpa_distribution_shape():
    df, p = _calibrated_sample(n_games=400)
    p = p + np.random.default_rng(0).normal(0, 0.02, len(p))
    w = wpa_distribution(df, p, max_minute=20, min_rows=100)
    assert set(w["by_minute"]["10"]) >= {"q50", "q90", "q99", "rows"}
    assert w["by_minute"]["10"]["q50"] <= w["by_minute"]["10"]["q99"]


def test_report_renders_without_ml_packages():
    df, p = _calibrated_sample(n_games=800)
    preds = {"simple": np.clip(p + 0.05, 0, 1), "xgb": p}
    gates = {k: metrics.calibration_gate(df, v) for k, v in preds.items()}
    y, g = df.blue_win.to_numpy(), df.match_id.to_numpy()
    entries = [{"key": k, "label": k, "n_params": 4, "size_bytes": 32, "train_s": 0.1, "ms_per_game": 0.1,
                "summary": metrics.summarize(y, v, g), "gate_passed": gates[k].passed} for k, v in preds.items()]
    ctx = {"dataset": {"games": 800, "rows": len(df), "tiers": ["emerald"], "patches": ["16.20"],
                       "split_games": {"train": 0, "val": 0, "test": 800}, "built": "test"},
           "models": entries, "production": "xgb",
           "benchmark_gain": {"xgb": metrics.paired_log_loss_gain(y, preds["simple"], p, g)},
           "reliability": {k: gr.bins[("overall", "all")] for k, gr in gates.items()},
           "gate": {"name": "xgb", "result": gates["xgb"], "calibrated": False}, "gates": gates,
           "by_minute": metrics.by_minute(df, preds), "importance": {f: 1 / len(FEATURE_NAMES) for f in FEATURE_NAMES},
           "mlp_curve": [(1, 0.6, 0.61), (2, 0.58, 0.6)], "wpa": wpa_distribution(df, p, min_rows=100)}
    html = report.render(ctx)
    assert "Calibration gate" in html and "<svg" in html and "Side by side" in html
