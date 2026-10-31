"""Phase 5.2–5.4 — train the win-probability models, check calibration, write the report.

    python scripts/build_winprob_dataset.py      # once, after extract.py
    python scripts/train_winprob.py              # ~2–5 min on a laptop, mostly the neural net
    python scripts/train_winprob.py --no-nn      # skip the comparison net

Side by side, on the same train/val/test games:
  simple   logistic on gold diff × minute: the benchmark the real model must beat
  xgb      gradient-boosted trees with monotone constraints: the production model
  mlp      a 2-layer neural net: comparison only, not saved

Calibration gate, v2 (owner's explicit decision, Oct 31, after v1 and its fallbacks failed
on the 1,971 test games; see CLAUDE.md for why). Thresholds are unchanged (metrics.GateSpec):
  1. Out-of-fold XGBoost predictions for ALL games (5 folds by game).
  2. Time-aware calibration (models.TimeCalibrator), cross-fitted: each fold is calibrated
     by a calibrator fitted on the other four, so no game is scored by anything that saw it.
  3. Every check (overall, phase, tier, patch) is scored on those ~13.6k games.
The shipped model = XGBoost retrained on all games + a calibrator fitted on all
out-of-fold predictions. **Release additionally requires passing the same gate on a fresh
held-out set** (scripts/check_holdout.py); the 13.6k games were looked at while designing
this gate.

Writes:
  models/winprob_xgb.json, models/winprob_meta.json, models/wpa_distribution.json
  data/processed/winprob_report.html
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from dataclasses import asdict

import numpy as np
import pandas as pd

from lolreview.config import MODEL_DIR, PROCESSED_DIR, ensure_dirs
from lolreview.model import metrics, models, report
from lolreview.model.dataset import FEATURE_NAMES, FEATURES, MONOTONE
from lolreview.model.predict import META_FILE, MODEL_FILE, WPA_FILE, wpa_distribution

DATASET = PROCESSED_DIR / "winprob_dataset.parquet"
REPORT = PROCESSED_DIR / "winprob_report.html"


def ms_per_game(predict, df: pd.DataFrame, n_games: int = 50) -> float:
    """Average time to score one whole game (all its minutes) — review-time latency."""
    ids = df["match_id"].drop_duplicates().iloc[:n_games]
    games = [df[df["match_id"] == m] for m in ids]
    predict(games[0])  # warm-up
    t0 = time.perf_counter()
    for g in games:
        predict(g)
    return 1000 * (time.perf_counter() - t0) / len(games)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-nn", action="store_true", help="skip the neural-net comparison")
    ap.add_argument("--nn-epochs", type=int, default=60)
    ap.add_argument("--no-tune", action="store_true",
                    help="skip the settings search; train XGBoost once with models.XGB_PARAMS")
    args = ap.parse_args()

    if not DATASET.exists():
        sys.exit(f"No dataset at {DATASET}. Run: python scripts/build_winprob_dataset.py")
    ensure_dirs()
    df = pd.read_parquet(DATASET).sort_values(["match_id", "minute"], kind="stable").reset_index(drop=True)
    train, val, test = (df[df["split"] == s].reset_index(drop=True) for s in ("train", "val", "test"))
    games = {s: int(d["match_id"].nunique()) for s, d in (("train", train), ("val", val), ("test", test))}
    print(f"{len(df):,} rows · games train {games['train']:,} / val {games['val']:,} / test {games['test']:,}")
    y_test = test["blue_win"].to_numpy()
    g_test = test["match_id"].to_numpy()

    entries: list[dict] = []
    preds: dict[str, np.ndarray] = {}

    # 1. benchmark
    print("simple logistic ...")
    simple, t = models.timed(models.SimpleLogistic().fit, train, train["blue_win"])
    preds["simple"] = simple.predict(test)
    entries.append({"key": "simple", "label": "gold-diff logistic", "n_params": simple.n_params,
                    "size_bytes": 8 * simple.n_params, "train_s": t,
                    "ms_per_game": ms_per_game(simple.predict, test)})

    # 2. XGBoost: pick settings on VALIDATION games only (test games stay unseen)
    if args.no_tune:
        print("XGBoost (default settings, no tuning) ...")
        xgb_params = {k: models.XGB_PARAMS[k] for k in ("max_depth", "min_child_weight")}
        (booster, t), tuning = models.timed(models.train_xgb, train, val), None
    else:
        print(f"XGBoost: {len(models.XGB_GRID)} candidate settings, chosen by validation log loss ...")
        (booster, xgb_params, tuning), t = models.timed(models.tune_xgb, train, val)
    size = models.xgb_size(booster)
    print(f"  chosen: depth {xgb_params['max_depth']}, min_child_weight {xgb_params['min_child_weight']} -> "
          f"{size['trees']} trees, {size['learned_numbers']:,} learned numbers ({t:.0f}s incl. tuning)")
    preds["xgb"] = models.predict_xgb(booster, test)
    model_path = MODEL_DIR / MODEL_FILE
    xgb_label = f"XGBoost (depth {xgb_params['max_depth']}, min leaf {xgb_params['min_child_weight']})"
    entries.append({"key": "xgb", "label": xgb_label, "n_params": size["learned_numbers"],
                    "size_bytes": len(booster.save_raw("json")), "train_s": t,
                    "ms_per_game": ms_per_game(lambda d: models.predict_xgb(booster, d), test)})

    # 3. neural net
    mlp_curve = None
    if not args.no_nn:
        print("neural net ...")
        mlp, t = models.timed(models.train_mlp, train, val, max_epochs=args.nn_epochs)
        print(f"  best epoch {mlp.epochs}, {mlp.n_params:,} weights, {t:.1f}s")
        preds["mlp"] = mlp.predict(test)
        mlp_curve = mlp.curve
        entries.append({"key": "mlp", "label": "neural net (64-32)", "n_params": mlp.n_params,
                        "size_bytes": 8 * mlp.n_params, "train_s": t,
                        "ms_per_game": ms_per_game(mlp.predict, test)})

    # 4. calibration + gate v2: everything out-of-fold over ALL games
    print("calibration gate (v2: every check out-of-fold over all games) ...")
    print(f"  out-of-fold XGBoost, 5 folds x {size['trees']} trees ...")
    oof, oof_s = models.timed(models.out_of_fold_xgb, df, size["trees"], params=xgb_params)
    oof_cal = models.cross_fit_time_calibration(oof, df)
    raw_gate = metrics.calibration_gate(df, oof, source="all games, out-of-fold, uncalibrated")
    final = metrics.calibration_gate(df, oof_cal, source="all games, out-of-fold")
    print(f"  uncalibrated: {'all pass' if raw_gate.passed else f'{int((~raw_gate.checks.passed).sum())} fail'}"
          f" · time-calibrated (cross-fitted): {'all pass' if final.passed else f'{int((~final.checks.passed).sum())} fail'}")
    calibrator = models.TimeCalibrator().fit(oof, df["blue_win"].to_numpy(), df["minute"].to_numpy())
    print("  trust in the model's confidence by minute (1.00 = unchanged): " + "  ".join(
        f"{m}:{v:.2f}" for m, v in zip((5, 15, 25, 35, 45), calibrator.trust([5, 15, 25, 35, 45]))))

    # side-by-side numbers on the test split (comparison only; the gate is above)
    production = "xgb_time"
    gate_name = f"{xgb_label} + time-aware calibration"
    preds[production] = calibrator.apply(preds["xgb"], test["minute"].to_numpy())
    entries.append({**entries[1], "key": production, "label": gate_name, "train_s": None,
                    "n_params": entries[1]["n_params"] + calibrator.n_params})
    gates = {k: metrics.calibration_gate(test, p) for k, p in preds.items()}
    for e in entries:
        e["summary"] = metrics.summarize(y_test, preds[e["key"]], g_test)
        e["gate_passed"] = final.passed if e["key"] == production else gates[e["key"]].passed
    gains = {k: metrics.paired_log_loss_gain(y_test, preds["simple"], p, g_test)
             for k, p in preds.items() if k != "simple"}
    # does each model's curve stay decisive and move on decisive events? (owner's requirement)
    volatility = {k: metrics.volatility_profile(test, p) for k, p in preds.items()}

    # 5. the shipped model: same settings and tree count, trained on ALL games
    print(f"final model: XGBoost on all {df['match_id'].nunique():,} games ...")
    final_booster, t_final = models.timed(models.train_xgb_fixed, df, size["trees"], xgb_params)
    final_booster.save_model(str(model_path))
    print(f"  {t_final:.0f}s -> {model_path}")

    # 6. swing distribution: calibrated out-of-fold predictions, every game honestly held out
    wpa = wpa_distribution(df, oof_cal)
    (MODEL_DIR / WPA_FILE).write_text(json.dumps(wpa, indent=2))
    late_check = final.bins.get(("phase", "late (25+)"))

    # 6. metadata
    built = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    importance = models.xgb_importance(final_booster)
    meta = {
        "built": built,
        "features": FEATURE_NAMES,
        "feature_meanings": {f: m for f, _, m in FEATURES},
        "monotone": dict(zip(FEATURE_NAMES, MONOTONE)),
        "xgb_params": {**models.XGB_PARAMS, **xgb_params},
        "tuning": None if tuning is None else tuning.to_dict(orient="records"),
        "volatility_test": {k: v.to_dict(orient="records") for k, v in volatility.items()},
        "xgb_size": size,
        "final_model": {"trained_on": "all games", "games": int(df["match_id"].nunique()),
                        "trees": size["trees"], "train_s": t_final},
        "calibrator": calibrator.to_dict(),
        "calibrator_trust": {str(m): float(v) for m, v in zip(range(0, 50, 5), calibrator.trust(range(0, 50, 5)))},
        "late_calibration_oof": None if late_check is None else late_check.to_dict(orient="records"),
        "gate_version": 2,
        "gate_passed": final.passed,
        "gate_spec": asdict(metrics.GATE),
        "gate_checks": final.checks.to_dict(orient="records"),
        "gate_checks_uncalibrated": raw_gate.checks.to_dict(orient="records"),
        "gate_scored_on": "all games, out-of-fold (5 folds by game), time calibration cross-fitted",
        "holdout": None,   # filled in by scripts/check_holdout.py; release needs it to pass
        "out_of_fold_seconds": oof_s,
        "test_metrics": {e["key"]: e["summary"] for e in entries},
        "benchmark_gain": gains,
        "dataset": {"rows": len(df), "games": games, "tiers": sorted(df["tier"].unique().tolist()),
                    "patches": sorted(df["patch"].unique().tolist())},
        "importance": importance,
    }
    (MODEL_DIR / META_FILE).write_text(json.dumps(meta, indent=2, default=float))

    # 7. report
    ctx = {
        "dataset": {"games": int(df["match_id"].nunique()), "rows": len(df),
                    "tiers": meta["dataset"]["tiers"], "patches": meta["dataset"]["patches"],
                    "split_games": games, "built": built},
        "models": entries, "benchmark_gain": gains, "production": production,
        "reliability": {k: g.bins[("overall", "all")] for k, g in gates.items()},
        "gate": {"name": gate_name, "result": final, "calibrated": True,
                 "how": "scored out-of-fold on all games; release also needs the fresh holdout"},
        "gates": gates,
        "by_minute": metrics.by_minute(test, preds),
        "tuning": tuning, "volatility": volatility,
        "importance": importance, "mlp_curve": mlp_curve, "wpa": wpa,
    }
    REPORT.write_text(report.render(ctx))

    # 8. console summary
    print()
    w = max(len(e["label"]) for e in entries) + 1
    print(f"{'model':{w}} {'log loss':>9} {'Brier':>7} {'acc':>6} {'ECE':>6} {'gate':>5}"
          f"   late: {'>90/<10':>7} {'event':>6} {'quiet':>6}")
    for e in entries:
        s = e["summary"]
        late = volatility[e["key"]].set_index("phase").loc["late (25+)"]
        print(f"{e['label']:{w}} {s['log_loss']:9.4f} {s['brier']:7.4f} {100*s['accuracy']:5.1f}% "
              f"{100*s['ece']:5.2f} {'pass' if e['gate_passed'] else 'FAIL':>5}"
              f"   {100*late['extreme']:12.0f}% {100*late['event_move']:+5.1f} {100*late['quiet_move']:6.1f}")
    print("late: share of late predictions above 90% or below 10%; mean move (pts) toward the team "
          "that just won a decisive event; mean move in quiet minutes")
    g = gains[production]
    print(f"\n{gate_name} vs benchmark: log loss {g[0]:+.4f} (90% range {g[1]:+.4f} to {g[2]:+.4f})")
    print("(table above: test-split side by side, every model trained on the same 9.5k games)")
    print(f"\ncalibration gate v2 (all {df['match_id'].nunique():,} games, out-of-fold): "
          f"{'PASSED' if final.passed else 'FAILED'}")
    if final.passed:
        print("Phase 6 can build on this model. RELEASE still requires the same gate on fresh games:\n"
              "  python scripts/check_holdout.py")
    else:
        print(final.checks[~final.checks.passed].to_string(index=False))
        print("\nThe gate blocks release (CLAUDE.md). Don't build Phase 6 severity labels on this model.")
    print(f"\nmodel  -> {model_path} (trained on all games)\nreport -> {REPORT}")


if __name__ == "__main__":
    main()
