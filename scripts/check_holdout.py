"""Release check: the unchanged calibration gate on games the model has never seen.

The ~13.6k training games were looked at while the gate was being redesigned (CLAUDE.md,
Oct 31), so the final word comes from a fresh set: newest patch, players not in the
training corpus, no shared matches. Same thresholds (metrics.GateSpec), same checks.

    python scripts/collect_summoners.py --tier EMERALD --target 5000 --name emerald_holdout \\
        --exclude emerald,diamond --seed 1
    python scripts/collect_matches.py --name emerald_holdout --patches 16.21 \\
        --exclude emerald,diamond --since 2026-10-21 --target 6000
    python scripts/extract.py --tier emerald_holdout
    python scripts/check_holdout.py

Records the result in models/winprob_meta.json ("holdout", "released") and writes
data/processed/holdout_report.html. Run it ONCE per fresh set; re-running on the same
games after changing the model would turn the holdout into another training signal.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

import numpy as np
import pandas as pd

from lolreview.config import MODEL_DIR, PROCESSED_DIR
from lolreview.extract.build import load as load_table, table_path
from lolreview.model import metrics, report
from lolreview.model.dataset import EVENT_COLUMNS, EVENT_TYPES, TEAM_COLUMNS, build_features
from lolreview.model.predict import META_FILE, WinProb

OUT_HTML = PROCESSED_DIR / "holdout_report.html"
# Each bin's 90% noise range should be well inside the ±4 limit for a pass/fail to mean
# anything. ~6,000 games gives about ±2 on late-game bins; 1,971 gave about ±3.5.
MIN_GAMES = 4000


def corpus_ids(name: str) -> set[str]:
    path = PROCESSED_DIR / f"matches_{name}.json"
    return set(json.loads(path.read_text())["kept"]) if path.exists() else set()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="emerald_holdout", help="corpus name of the fresh set")
    ap.add_argument("--train-corpora", default="emerald,diamond")
    args = ap.parse_args()
    name = args.name.lower()

    for t in ("teams", "events"):
        if not table_path(PROCESSED_DIR, t, name).exists():
            sys.exit(f"No {t} table for {name}. Run: python scripts/extract.py --tier {name}")
    meta_path = MODEL_DIR / META_FILE
    if not meta_path.exists():
        sys.exit("No trained model. Run: python scripts/train_winprob.py")
    meta = json.loads(meta_path.read_text())
    if meta.get("gate_version") != 2 or not meta.get("gate_passed"):
        sys.exit("The saved model hasn't passed gate v2 on the training games; fix that first.")
    if meta.get("holdout"):
        print(f"NOTE: this model was already checked on a holdout ({meta['holdout'].get('name')}, "
              f"{meta['holdout'].get('checked')}). Re-checking the same games after changing the "
              "model weakens the test.")

    # ---- leakage guards ---------------------------------------------------------
    teams = load_table("teams", name, columns=TEAM_COLUMNS)
    events = load_table("events", name, columns=EVENT_COLUMNS, filters=[("type", "in", EVENT_TYPES)])
    trained = set().union(*(corpus_ids(c.strip()) for c in args.train_corpora.split(",")))
    overlap = set(teams["match_id"].unique()) & trained
    if overlap:
        sys.exit(f"{len(overlap)} holdout matches are in the training corpus — not a fair test.")

    df = build_features(teams, events)
    df["tier"] = name.split("_")[0]         # e.g. emerald_holdout -> emerald (is_diamond stays 0)
    df = df.sort_values(["match_id", "minute"], kind="stable").reset_index(drop=True)
    n_games = df["match_id"].nunique()
    print(f"{name}: {n_games:,} games, {len(df):,} game-minutes, patches "
          f"{', '.join(sorted(df['patch'].unique()))}; 0 shared with the training corpus")
    if n_games < MIN_GAMES:
        print(f"WARNING: under {MIN_GAMES:,} games — per-bin noise will be close to the ±4 limit, so "
              "a fail may be noise (that's what happened with the 1,971 test games).")

    # ---- the gate -----------------------------------------------------------------
    wp = WinProb.load()
    p = wp.predict(df)
    res = metrics.calibration_gate(df, p, source=f"fresh holdout ({name})")
    y, g = df["blue_win"].to_numpy(), df["match_id"].to_numpy()
    summary = metrics.summarize(y, p, g)
    vol = metrics.volatility_profile(df, p)
    bins = res.checks[res.checks["check"] != "ECE"]
    noise = float(np.median((bins["noise_hi"] - bins["noise_lo"]) / 2)) if len(bins) else float("nan")

    print(f"log loss {summary['log_loss']:.4f} · ECE {100 * summary['ece']:.2f} pts · "
          f"accuracy {100 * summary['accuracy']:.1f}% · Blue won {100 * summary['blue_win_rate']:.1f}%")
    print(f"typical per-bin noise: ±{100 * noise:.1f} pts (limit ±{100 * metrics.GATE.bin_tolerance:.0f})")
    late = vol.set_index("phase").loc["late (25+)"]
    print(f"late game: {100 * late['extreme']:.0f}% of predictions above 90/below 10; "
          f"decisive-event move {100 * late['event_move']:+.1f} pts vs quiet {100 * late['quiet_move']:.1f}")
    print(f"\nholdout gate ({len(res.checks)} checks): {'PASSED' if res.passed else 'FAILED'}")
    if not res.passed:
        print(res.checks[~res.checks["passed"]].to_string(index=False))

    # ---- record -------------------------------------------------------------------
    meta["holdout"] = {
        "name": name, "checked": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "games": int(n_games), "rows": int(len(df)),
        "patches": sorted(df["patch"].unique().tolist()),
        "passed": res.passed, "summary": summary, "median_bin_noise": noise,
        "checks": res.checks.to_dict(orient="records"),
        "volatility": vol.to_dict(orient="records"),
    }
    meta["released"] = bool(meta.get("gate_passed") and res.passed)
    meta_path.write_text(json.dumps(meta, indent=2, default=float))

    chk = res.checks.copy()
    chk["value"] = [f"{100 * v:.2f}" if c == "ECE" else f"{100 * v:+.2f}" for c, v in zip(chk["check"], chk["value"])]
    chk["limit"] = [f"≤ {100 * v:.0f}" if c == "ECE" else f"± {100 * v:.0f}" for c, v in zip(res.checks["check"], res.checks["limit"])]
    chk["noise (90%)"] = [f"{100 * a:+.1f} … {100 * b:+.1f}" for a, b in zip(res.checks["noise_lo"], res.checks["noise_hi"])]
    chk["result"] = res.checks["passed"].map({True: "pass", False: "FAIL"})
    charts = "".join(report.svg_reliability([("xgb_time", k[1], v)], size=260, title=f"{k[0]}: {k[1]}")
                     for k, v in res.bins.items())
    OUT_HTML.write_text(
        f"<!doctype html><meta charset='utf-8'><title>Holdout check</title><style>{report.CSS}</style>"
        f"<h1>Release check: fresh holdout</h1><p class='sub'>{n_games:,} games ({name}), patches "
        f"{', '.join(meta['holdout']['patches'])}, none in the training corpus. Same gate thresholds.</p>"
        f"<div class='verdict {'ok' if res.passed else 'bad'}'>{'PASSED' if res.passed else 'FAILED'}: "
        f"{int(res.checks['passed'].sum())} of {len(res.checks)} checks</div>"
        f"<p>log loss {summary['log_loss']:.4f} · ECE {100 * summary['ece']:.2f} pts · typical per-bin noise ±{100 * noise:.1f} pts</p>"
        f"<h2>Calibration</h2><div class='grid'>{charts}</div>"
        f"<h2>Checks</h2>{report.table(chk[['group', 'slice', 'check', 'value', 'limit', 'noise (90%)', 'result']], row_class=lambda r: 'fail' if r['result'] == 'FAIL' else 'pass')}"
    )
    print(f"\nrecorded in {meta_path} (released = {meta['released']})\nreport -> {OUT_HTML}")


if __name__ == "__main__":
    main()
