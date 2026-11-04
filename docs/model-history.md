# Win-probability model: history and gate (Phase 5)

Read the gate history before touching the gate, thresholds or holdouts. Moved verbatim from
CLAUDE.md's Status section on Nov 4, 2026.

- **Phase 5 done (Nov 2).** Exit check: the owner played several games, noted during each
  how it felt, and found the win-probability curve matched which team was ahead and roughly by
  how much. Qualitative, and on TW2 games (the owner's current server), not NA. (`src/lolreview/model/`, `scripts/build_winprob_dataset.py`,
  `scripts/train_winprob.py`, `scripts/check_holdout.py`). Training runs on the owner's Mac
  (the sandbox has no xgboost/sklearn).
  - **Dataset:** one row per game-minute from Blue's side, 29 features (`dataset.FEATURES`, with
    monotone constraints). 13,654 games / 408,740 rows (Emerald 12,000 on 16.18–16.21, Diamond
    2,000 on 16.18–16.20; AFK games and each final post-nexus snapshot dropped; 5 games in
    both corpora kept once).
  - **Shipped model:** XGBoost (depth 4, min_child_weight 50, 446 trees) trained on all
    games, plus `models.TimeCalibrator`: logit(p') = a + b·m + (c + d·m)·logit(p). Smooth in
    time, because a per-phase correction would jump at 14:00/25:00 and fake a "moment". It
    never reorders predictions. Trust by minute: 0:1.10, 15:1.00, 25:0.93, 35:0.86, 45:0.79;
    late game was really overconfident by up to ~4 pts before it.
  - **Side by side** (test split, all trained on the same 9.5k games, log loss): gold-diff
    logistic 0.5702 · XGBoost 0.5544 · MLP 0.5522 · XGBoost + time calibration 0.5534. The MLP's
    best epoch was 1. A 9-setting regularisation search moved val log loss by < 0.001: the
    model is at the limit of these features, not overfitting.
  - **Calibration gate history (read before touching the gate):**
    - Thresholds were fixed before training (`metrics.GateSpec`): ECE ≤ 2 pts overall, ≤ 3 per
      phase/tier/patch; bins with ≥ 200 games within ±4 pts. They have never been loosened.
    - v1 scored overall+phase on the 1,971 test games. Every variant failed by 4.1–5.0 pts in
      bins whose noise was ~±3.5, and *which* bins failed moved between variants. A
      well-calibrated stand-in failed too. The gate's sample size was the problem.
    - **Gate v2 (owner's explicit post-hoc decision):** (1) development gate = every check on
      out-of-fold predictions over all games, calibration cross-fitted
      (`models.cross_fit_time_calibration`): passed 88/88, worst bin +2.0, noise ±1.1.
      (2) **release gate = the same unchanged gate on a fresh holdout**: newest patch, players
      not in the corpus, no shared matches.
    - **Holdout result:** `emerald_holdout`, 5,739 games after AFK removal (5,885 collected),
      all 16.21, 0 shared matches/players. **Passed all 55 checks.** Log loss 0.5426, ECE 0.78
      (early 1.01, mid 1.06, late 0.49), worst bin −3.0 (early 90–100%, 368 games, noise
      −4.6…−1.5). Recorded in `winprob_meta.json` (`holdout`, `released: true`).
    - **Run a holdout check once per fresh set.** Re-checking the same games after changing
      the model turns it into training signal. A 16.22 set (live Nov 4) would test patch drift.
  - **Known mild bias:** very lopsided *early* states (> 90% before 14:00) are slightly
    underconfident (−3.0 pts on the holdout, outside noise but inside the limit). Early stomps
    read a little less decisive than they are. Watch for it in Phase 6; don't patch it on the
    holdout.
  - **Owner's requirement: late game must not drift toward 50% by default.** A lopsided late
    state should read lopsided, and the curve must swing hard on decisive events. Measured by
    `metrics.volatility_profile` (share of predictions above 90%/below 10%; mean move toward
    the team that just took Baron or Elder, took an inhibitor, or won a fight by 3+ kills, via
    `decisive_events`, where that team wins 81% of the time late). Holdout: late >90/<10 30%,
    decisive move +9.5 pts vs quiet 5.7. Watch out: `min_child_weight` sums p(1−p), so large
    values block splits mostly in lopsided states.
  - `model/predict.py`: `WinProb.load().for_game(extract_game(...))`, `WinProb.released`; the
    replay draws the curve automatically. `wpa_distribution.json` = per-minute quantiles of
    |ΔP| from calibrated out-of-fold predictions over all games: Phase 6's yardstick for
    "unusual" swings.
  - **v1 blind spot: gold distribution (owner's hypothesis, confirmed Nov 1).** Every gold
    feature is a team total, so a lead held by one fed carry and a lead spread evenly look the
    same. Test on 11,704 Emerald corpus games (holdout untouched), logistic, 5-fold CV by
    game, minute 8+:
    - At even team gold (±500) in minutes 15–30, Blue wins **57.4%** when it has the richest
      player by 1.5k+ (600 games) vs **45.8%** when Red has the richest player (2,584 games).
      Team-gold-only predicts ~49–50% for both. Down 2–4k: 36.8% vs 23.9%.
    - Per-role gold matters: 1k of support gold is worth about 40% of 1k of carry gold at
      10:00 (logit 0.18 vs ~0.5 per 1k); bot gold holds its value best late.
    - CV log-loss gain vs team gold only: per-role diffs +0.0026; + richest-player gap +0.0031;
      + concentration +0.0035 (90% range +0.0024…+0.0048). Small on average because these
      states are uncommon, but they're exactly the moments a review cares about. With both
      added, the even-gold case is predicted 57.3% / 45.6%, matching the actual outcomes.
    - Proposed v2 features: 5 per-role gold diffs, richest-player gold gap, gold-concentration
      shares. Per-player gold comes from the frames table, no re-extraction needed. Release
      needs a fresh holdout (16.22); the 16.21 holdout can join training.
    - **XP/level distribution tested too (Nov 1): adds nothing beyond the gold features. Don't
      add it.** On top of v2 gold + team XP/level (same 11,704 games, minute 3+): per-role XP
      +0.0001 (−0.0003…+0.0004), level-6/11/16 spike counts +0.0001, highest-XP-player gap
      −0.0000, all three +0.0001. Per-player XP mostly duplicates per-player gold. The v2 gold
      features still add +0.0038 (+0.0027…+0.0049) even with team XP/level present.
      Level-6 race (2+ more players at 6, minutes 6–9, gold and XP even): 55.6% vs 44.7%, but
      only ~150 games per side. Real-looking but too rare to matter. A player 2+ levels above
      everyone, at even team XP, goes with *losing* (45.4%), and the gold features already
      predict that (45.1%).
  - **v2 built (Nov 1), not yet trained.** `dataset.FEATURES` now has 37 features (v1's 29 +
    `gold_diff_top/jungle/mid/bot/support`, `richest_gap` [unconstrained], `blue_/red_gold_top_share`
    [unconstrained]); role diffs are monotone +1 and NaN when a role is missing or doubled (0 of
    1,468 sampled games). `V1_FEATURE_NAMES` = the first 29. Per-player gold comes from
    `frames_<corpus>.parquet` (`dataset.gold_spread`). Default corpora: emerald, diamond,
    emerald_holdout (~19.4k games). The tier comes from the corpus name (`tier_of`). The training
    report adds an "XGBoost, v1 features" side-by-side model and a **fed-carry check** table
    (`metrics.gold_spread_check`). `train_winprob.py` archives the current model to
    `models/archive/<version>_<built>/` before overwriting. `WinProb` predicts with the feature list
    in the model's own metadata, so archived v1 models still load. `check_holdout.py --name` is now
    required and refuses any corpus the model trained on. **v1 stays the released model until v2
    passes the gate on a fresh 16.22 set.** Sandbox end-to-end dry run (stand-in XGBoost, 4.5k
    games): pipeline OK.
  - **v2 trained (Nov 1): development gate PASSED.** Now the current model in `models/` (v1 archived
    to `models/archive/v1_2026-10-30T184751/`); `released` is unset until the 16.22 check. Test-split
    log loss: XGBoost on v1 features 0.5549 → v2 0.5521 (**+0.0028**, as the logistic analysis
    predicted) → v2 + time calibration 0.5512; MLP 0.5525; gold-diff logistic 0.5712.
    Fed-carry check, out-of-fold over all games, minutes 15–30: at even team gold, Blue-richest
    actual 58.6% vs v2 55.7% (970 games); Red-richest 45.7% vs 46.1% (4,296 games). Down 2–4k:
    36.4% vs 33.0%. v2 captures most of the effect but **still underrates the fed-carry side by
    ~3–4 pts in every band**. That's a known residual; don't tune it away on the holdout.
  - Effective sample size is games, not rows: small slices need large samples. With ~300
    games per bin, coin-flip noise alone gives ECE ≈ 3 pts. Size any future check so per-bin
    noise is well inside its limit.

## Current model (retrained Nov 2)

  - Current model (retrained Nov 2, identical metrics to the Nov 1 v2): XGBoost **depth 5,
    min_child_weight 800, 707 trees** (tuning picks it; v1 was depth 4/50/446), trained on
    19,393 games; calibrator trust 5:1.02, 15:0.98, 25:0.94, 35:0.91, 45:0.87. Test log loss
    0.5512 (v1 features 0.5549, MLP 0.5525). Dev gate passed; `released` unset until the 16.22
    holdout.
