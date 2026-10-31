"""Phase 5.3 — scoring and the calibration gate.

Calibration = when the model says 70%, Blue wins about 70% of those times. Every severity
label downstream is a difference of two of these numbers, so a miscalibrated model makes
every verdict wrong. The gate was fixed BEFORE training (CLAUDE.md, Phase 5 decisions)
and is not loosened after seeing results:

  * ECE ≤ 2 pts on the whole test set
  * ECE ≤ 3 pts within each phase (early < 14:00, mid 14–25, late 25+), each tier, and
    each patch group (16.21 vs earlier)
  * every probability bin holding ≥ 200 distinct games is within ±4 pts of the observed
    win rate — overall and in every subset above

Where each check is scored (decided with the owner before XGBoost was trained):
  * overall + phase: the production model on TEST games.
  * tier + patch: OUT-OF-FOLD predictions on ALL games (5 folds by game; each game scored
    by a model that never trained on it). The 265 Diamond test games happened to be won by
    Blue only 40.4% of the time (train: 49.6%), a ~3-SE fluke no model can predict.
    Scoring all ~1,950 Diamond games removes that fluke. The thresholds stay the same.

ECE (expected calibration error): sort predictions into 10 bins (0–10%, 10–20%, …), take
|average prediction − actual win rate| in each, average those gaps weighted by row count.

Rows within a game share one outcome, so the honest sample size is games, not rows.
Uncertainty here comes from a bootstrap over GAMES (resample whole games, recompute).
The noise ranges are shown next to the numbers to help read them. They never change
pass/fail.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

N_BINS = 10
PHASES = [("early (<14)", 0, 14), ("mid (14–25)", 14, 25), ("late (25+)", 25, 10_000)]
LATEST_PATCH = "16.21"


@dataclass(frozen=True)
class GateSpec:
    ece_overall: float = 0.02
    ece_subset: float = 0.03
    bin_tolerance: float = 0.04
    bin_min_games: int = 200


GATE = GateSpec()
EPS = 1e-6


# ---- point metrics -------------------------------------------------------

def log_loss(y, p) -> float:
    p = np.clip(np.asarray(p, float), EPS, 1 - EPS)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier(y, p) -> float:
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def accuracy(y, p) -> float:
    return float(np.mean((np.asarray(p) >= 0.5) == (np.asarray(y) == 1)))


def bin_index(p, n_bins: int = N_BINS) -> np.ndarray:
    return np.clip((np.asarray(p, float) * n_bins).astype(int), 0, n_bins - 1)


def _game_bin_sums(p, y, games, n_bins):
    """Per (game, bin): row count, sum of predictions, sum of outcomes. Shape (G, B)."""
    codes, uniq = pd.factorize(np.asarray(games))
    b = bin_index(p, n_bins)
    G = len(uniq)
    flat = codes * n_bins + b
    size = G * n_bins
    n = np.bincount(flat, minlength=size).reshape(G, n_bins).astype(float)
    sp = np.bincount(flat, weights=np.asarray(p, float), minlength=size).reshape(G, n_bins)
    sy = np.bincount(flat, weights=np.asarray(y, float), minlength=size).reshape(G, n_bins)
    return n, sp, sy


def _ece_from_sums(n, sp, sy) -> np.ndarray:
    """ECE from bin totals; works on (B,) or (K, B) arrays."""
    return np.abs(sp - sy).sum(axis=-1) / n.sum(axis=-1)


def ece(y, p, n_bins: int = N_BINS) -> float:
    if len(p) == 0:
        return float("nan")
    b = bin_index(p, n_bins)
    n = np.bincount(b, minlength=n_bins)
    sp = np.bincount(b, weights=np.asarray(p, float), minlength=n_bins)
    sy = np.bincount(b, weights=np.asarray(y, float), minlength=n_bins)
    return float(np.abs(sp - sy).sum() / n.sum())


def _bootstrap_weights(G: int, n_boot: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.multinomial(G, np.full(G, 1.0 / G), size=n_boot).astype(float)  # (K, G)


def reliability(y, p, games, n_bins: int = N_BINS, n_boot: int = 200, seed: int = 0) -> pd.DataFrame:
    """One row per probability bin: rows, distinct games, mean prediction, observed win
    rate, gap, and a 90% game-bootstrap range for the gap."""
    n, sp, sy = _game_bin_sums(p, y, games, n_bins)
    W = _bootstrap_weights(n.shape[0], n_boot, seed)
    bn, bsp, bsy = W @ n, W @ sp, W @ sy
    with np.errstate(invalid="ignore", divide="ignore"):
        boot_gap = (bsp - bsy) / bn
    tot_n, tot_p, tot_y = n.sum(0), sp.sum(0), sy.sum(0)
    rows = []
    for b in range(n_bins):
        if tot_n[b] == 0:
            continue
        lo, hi = np.nanpercentile(boot_gap[:, b], [5, 95])
        rows.append({
            "bin": b, "lo": b / n_bins, "hi": (b + 1) / n_bins,
            "rows": int(tot_n[b]), "games": int((n[:, b] > 0).sum()),
            "mean_pred": tot_p[b] / tot_n[b], "observed": tot_y[b] / tot_n[b],
            "gap": (tot_p[b] - tot_y[b]) / tot_n[b], "gap_lo": lo, "gap_hi": hi,
        })
    return pd.DataFrame(rows)


def ece_interval(y, p, games, n_bins: int = N_BINS, n_boot: int = 200, seed: int = 0):
    """ECE with a 90% game-bootstrap range."""
    n, sp, sy = _game_bin_sums(p, y, games, n_bins)
    W = _bootstrap_weights(n.shape[0], n_boot, seed)
    boot = _ece_from_sums(W @ n, W @ sp, W @ sy)
    point = float(_ece_from_sums(n.sum(0), sp.sum(0), sy.sum(0)))
    lo, hi = np.percentile(boot, [5, 95])
    return point, float(lo), float(hi)


def paired_log_loss_gain(y, p_base, p_new, games, n_boot: int = 500, seed: int = 0):
    """How much lower p_new's log loss is than p_base's, with a 90% game-bootstrap range.
    Positive = p_new is better."""
    y = np.asarray(y, float)
    def ll(p):
        p = np.clip(np.asarray(p, float), EPS, 1 - EPS)
        return -(y * np.log(p) + (1 - y) * np.log(1 - p))
    d = ll(p_base) - ll(p_new)
    codes, uniq = pd.factorize(np.asarray(games))
    per_game_sum = np.bincount(codes, weights=d)
    per_game_n = np.bincount(codes).astype(float)
    W = _bootstrap_weights(len(uniq), n_boot, seed)
    boot = (W @ per_game_sum) / (W @ per_game_n)
    lo, hi = np.percentile(boot, [5, 95])
    return float(d.mean()), float(lo), float(hi)


def summarize(y, p, games=None) -> dict:
    out = {"rows": int(len(y)), "log_loss": log_loss(y, p), "brier": brier(y, p),
           "accuracy": accuracy(y, p), "ece": ece(y, p)}
    if games is not None:
        per_game = pd.Series(np.asarray(y)).groupby(np.asarray(games)).first()
        out["games"] = int(len(per_game))
        out["blue_win_rate"] = float(per_game.mean())   # a slice's base rate; small slices drift
    return out


# ---- subsets and the gate ------------------------------------------------

def subsets(df: pd.DataFrame) -> list[tuple[str, str, np.ndarray]]:
    """(group, name, row mask) for every slice the gate checks. `df` needs minute, tier, patch."""
    m = df["minute"].to_numpy()
    out = [("overall", "all", np.ones(len(df), bool))]
    for name, lo, hi in PHASES:
        out.append(("phase", name, (m >= lo) & (m < hi)))
    for tier in sorted(df["tier"].unique()):
        out.append(("tier", tier, (df["tier"] == tier).to_numpy()))
    latest = (df["patch"] == LATEST_PATCH).to_numpy()
    if latest.any() and (~latest).any():
        others = sorted(df.loc[~latest, "patch"].unique(), key=lambda s: tuple(int(x) for x in s.split(".")))
        newest_trained = tuple(int(x) for x in LATEST_PATCH.split("."))
        if all(tuple(int(x) for x in o.split(".")) < newest_trained for o in others):
            label = f"before {LATEST_PATCH}"
        else:
            label = "other patches (" + ", ".join(others) + ")"
        out.append(("patch", LATEST_PATCH, latest))
        out.append(("patch", label, ~latest))
    return out


@dataclass
class GateResult:
    passed: bool
    checks: pd.DataFrame          # one row per check
    slices: pd.DataFrame          # summary metrics per slice
    bins: dict = field(default_factory=dict)   # (group, name) -> reliability table


TEST_GROUPS = ("overall", "phase")      # scored on test games
OOF_GROUPS = ("tier", "patch")          # scored on out-of-fold predictions, all games


def calibration_gate(df: pd.DataFrame, p, spec: GateSpec = GATE, groups=None,
                     source: str = "test games") -> GateResult:
    """Run the agreed checks on `df` (rows with blue_win, match_id, minute, tier, patch)
    with predictions `p`. `groups` limits which slice groups are checked (default: all);
    `source` labels where the predictions came from."""
    p = np.asarray(p, float)
    y = df["blue_win"].to_numpy()
    games = df["match_id"].to_numpy()
    checks, slices, bins = [], [], {}
    for group, name, mask in subsets(df):
        if not mask.any() or (groups is not None and group not in groups):
            continue
        yy, pp, gg = y[mask], p[mask], games[mask]
        e, e_lo, e_hi = ece_interval(yy, pp, gg)
        limit = spec.ece_overall if group == "overall" else spec.ece_subset
        checks.append({"group": group, "slice": name, "check": "ECE", "value": e,
                       "limit": limit, "noise_lo": e_lo, "noise_hi": e_hi, "passed": e <= limit})
        rel = reliability(yy, pp, gg)
        bins[(group, name)] = rel
        for r in rel[rel["games"] >= spec.bin_min_games].itertuples():
            checks.append({"group": group, "slice": name,
                           "check": f"bin {r.lo:.0%}–{r.hi:.0%} ({r.games} games)",
                           "value": r.gap, "limit": spec.bin_tolerance,
                           "noise_lo": r.gap_lo, "noise_hi": r.gap_hi,
                           "passed": abs(r.gap) <= spec.bin_tolerance})
        slices.append({"group": group, "slice": name, "source": source, **summarize(yy, pp, gg)})
    checks = pd.DataFrame(checks)
    if len(checks):
        checks.insert(0, "source", source)
    return GateResult(bool(checks["passed"].all()) if len(checks) else True,
                      checks, pd.DataFrame(slices), bins)


def combine(*results: GateResult) -> GateResult:
    """One gate from several partial ones (e.g. test-game checks + out-of-fold checks)."""
    bins: dict = {}
    for r in results:
        bins.update(r.bins)
    return GateResult(all(r.passed for r in results),
                      pd.concat([r.checks for r in results], ignore_index=True),
                      pd.concat([r.slices for r in results], ignore_index=True), bins)


def production_gate(test: pd.DataFrame, p_test, everything: pd.DataFrame, p_oof,
                    spec: GateSpec = GATE) -> GateResult:
    """The agreed gate: overall + phase on test games, tier + patch out-of-fold on all games."""
    return combine(
        calibration_gate(test, p_test, spec, TEST_GROUPS, "test games"),
        calibration_gate(everything, p_oof, spec, OOF_GROUPS, "all games, out-of-fold"),
    )


# ---- volatility: does the curve move when something decisive happens? --------
#
# Owner's requirement (Oct 31): late game must NOT drift toward 50% by default. A lopsided
# late state should read lopsided, and the curve should swing hard on decisive events.
# Calibration alone can't see this (a model that never moves can be calibrated), so
# these numbers are reported next to it for every model and every tuning candidate.

FIGHT_MARGIN = 3   # one side lost 3+ more champions than the other in the last 60 s


def decisive_events(df: pd.DataFrame) -> np.ndarray:
    """Per row: +1 if something decisive happened for Blue since the previous minute of the
    same game, −1 for Red, 0 if nothing (or mixed). `df` sorted by match_id, minute.

    Decisive = Baron or Elder taken (buff turned on), an inhibitor taken, or a fight won by
    FIGHT_MARGIN+ kills in the last minute."""
    gid = df["match_id"].to_numpy()
    minute = df["minute"].to_numpy()
    same = np.zeros(len(df), bool)
    same[1:] = (gid[1:] == gid[:-1]) & (minute[1:] - minute[:-1] == 1)

    def rose(col):
        v = df[col].to_numpy(float)
        out = np.zeros(len(df), bool)
        out[1:] = v[1:] > v[:-1]
        return out & same

    score = np.zeros(len(df))
    for blue_good, red_good in (("blue_baron_active", "red_baron_active"),
                                ("blue_elder_active", "red_elder_active"),
                                ("red_inhibs_down", "blue_inhibs_down")):
        score += rose(blue_good).astype(int) - rose(red_good).astype(int)
    margin = df["red_recent_deaths"].to_numpy(float) - df["blue_recent_deaths"].to_numpy(float)
    score += (margin >= FIGHT_MARGIN).astype(int) - (margin <= -FIGHT_MARGIN).astype(int)
    return np.sign(score).astype(int) * same


def volatility_profile(df: pd.DataFrame, p) -> pd.DataFrame:
    """Per phase: how decisive the predictions are and how they move.

      extreme      share of predictions below 10% or above 90%
      swing_q50/90/99   |ΔP| between consecutive minutes (quantiles)
      quiet_move   mean |ΔP| in minutes with no decisive event
      event_move   mean ΔP toward the team that just won something decisive
      events       how many decisive minutes that's averaged over
    """
    p = np.asarray(p, float)
    gid = df["match_id"].to_numpy()
    minute = df["minute"].to_numpy()
    same = np.zeros(len(df), bool)
    same[1:] = (gid[1:] == gid[:-1]) & (minute[1:] - minute[:-1] == 1)
    dp = np.full(len(df), np.nan)
    dp[1:] = p[1:] - p[:-1]
    dp[~same] = np.nan
    ev = decisive_events(df)
    rows = []
    for name, lo, hi in [("all", 0, 10_000), *PHASES]:
        m = (minute >= lo) & (minute < hi)
        moves = np.abs(dp[m & same])
        quiet = m & same & (ev == 0)
        hit = m & same & (ev != 0)
        rows.append({
            "phase": name, "rows": int(m.sum()),
            "extreme": float(np.mean((p[m] < 0.1) | (p[m] > 0.9))) if m.any() else np.nan,
            "swing_q50": float(np.quantile(moves, 0.5)) if len(moves) else np.nan,
            "swing_q90": float(np.quantile(moves, 0.9)) if len(moves) else np.nan,
            "swing_q99": float(np.quantile(moves, 0.99)) if len(moves) else np.nan,
            "quiet_move": float(np.nanmean(np.abs(dp[quiet]))) if quiet.any() else np.nan,
            "event_move": float(np.nanmean(dp[hit] * ev[hit])) if hit.any() else np.nan,
            "events": int(hit.sum()),
        })
    return pd.DataFrame(rows)


def by_minute(df: pd.DataFrame, preds: dict[str, np.ndarray], max_minute: int = 45) -> pd.DataFrame:
    """Accuracy and log loss per game minute for each model (minutes with ≥ 200 games)."""
    y = df["blue_win"].to_numpy()
    m = df["minute"].to_numpy()
    rows = []
    for minute in range(1, max_minute + 1):
        mask = m == minute
        if mask.sum() < 200:
            continue
        row = {"minute": minute, "games": int(mask.sum())}
        for name, p in preds.items():
            row[f"{name}_acc"] = accuracy(y[mask], p[mask])
            row[f"{name}_ll"] = log_loss(y[mask], p[mask])
        rows.append(row)
    return pd.DataFrame(rows)
