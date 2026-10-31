"""Phase 5.2 — the models.

  * SimpleLogistic — benchmark the real model must beat: logistic regression on gold
    diff, minute, and gold diff × minute (a gold lead matters more as the game goes on).
    4 numbers. Pure numpy.
  * XGBoost — the production model. Gradient-boosted trees, with monotone constraints
    from dataset.MONOTONE: e.g. more gold can never lower Blue's win probability.
  * Neural net — a side-by-side comparison only (sklearn MLP, 2 hidden layers). No
    monotone constraints; that's part of what the comparison shows.
  * Isotonic — the one allowed calibration fix if XGBoost fails the gate. Fitted on
    validation games only, stored as (x, y) knots, applied with np.interp. Pure numpy.

xgboost and scikit-learn are imported inside the functions that need them, so the
benchmark, the calibrator, and inference on a saved calibrator work without them.
"""

from __future__ import annotations

import copy
import time
import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .dataset import FEATURE_NAMES, MONOTONE
from .metrics import log_loss

EPS = 1e-6


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


# ---- benchmark -----------------------------------------------------------

class SimpleLogistic:
    """P(Blue) = sigmoid(w0 + w1·gold + w2·minute + w3·gold·minute)."""

    def _design(self, df: pd.DataFrame) -> np.ndarray:
        g = df["gold_diff"].to_numpy(float) / 1000.0
        m = df["minute"].to_numpy(float) / 10.0
        return np.column_stack([np.ones_like(g), g, m, g * m])

    def fit(self, df: pd.DataFrame, y, iters: int = 50, l2: float = 1e-6) -> "SimpleLogistic":
        X, y = self._design(df), np.asarray(y, float)
        w = np.zeros(X.shape[1])
        for _ in range(iters):  # Newton's method; converges in a handful of steps
            p = _sigmoid(X @ w)
            grad = X.T @ (p - y) + l2 * w
            H = (X * (p * (1 - p))[:, None]).T @ X + l2 * np.eye(len(w))
            step = np.linalg.solve(H, grad)
            w -= step
            if np.abs(step).max() < 1e-10:
                break
        self.w = w
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        return _sigmoid(self._design(df) @ self.w)

    @property
    def n_params(self) -> int:
        return len(self.w)


# ---- XGBoost -------------------------------------------------------------

XGB_PARAMS = dict(
    n_estimators=3000,         # upper bound; early stopping picks the real number
    learning_rate=0.03,
    max_depth=4,
    min_child_weight=50,       # ≈ 200+ rows per leaf: no leaf memorises a few games
    subsample=0.8,
    colsample_bytree=0.8,
    reg_lambda=1.0,
    tree_method="hist",
    eval_metric="logloss",
    early_stopping_rounds=100,
)


def train_xgb(train: pd.DataFrame, val: pd.DataFrame, params: dict | None = None):
    """Returns a Booster cut at the best validation round."""
    import xgboost as xgb

    p = {**XGB_PARAMS, **(params or {})}
    model = xgb.XGBClassifier(
        **p, monotone_constraints="(" + ",".join(str(c) for c in MONOTONE) + ")")
    model.fit(train[FEATURE_NAMES], train["blue_win"],
              eval_set=[(val[FEATURE_NAMES], val["blue_win"])], verbose=False)
    booster = model.get_booster()
    best = int(model.best_iteration)
    return booster[: best + 1]


# Fix for v1's late-game overconfidence (Oct 31): regularise harder for game-clustered rows.
# v1 (depth 4, min_child_weight 50) is in the grid, so it competes on equal terms.
#
# min_child_weight is a sum of p(1−p) over the leaf's rows, NOT a row count: a row near
# 50% adds 0.25, a row near 95% adds ~0.05. A large value therefore blocks splits mostly in
# lopsided states — the decisive late-game states the owner wants kept sharp. Hence a
# moderate range, and the volatility profile reported for every candidate.
XGB_GRID = [{"max_depth": d, "min_child_weight": w} for d in (3, 4, 5) for w in (50, 200, 800)]


def tune_xgb(train: pd.DataFrame, val: pd.DataFrame, grid=None, log=print):
    """Train each candidate (early stopping on val) and pick the lowest VALIDATION log loss.
    Log loss is a proper scoring rule: it punishes hedging toward 50% when the state is
    decisive as much as it punishes overconfidence. Test games are never touched here.

    Returns (booster, chosen params, table of every candidate)."""
    from .metrics import ece, volatility_profile

    grid = grid or XGB_GRID
    late = val["minute"].to_numpy() >= 25
    y = val["blue_win"].to_numpy()
    rows, best = [], None
    for params in grid:
        booster, t = timed(train_xgb, train, val, params)
        p = predict_xgb(booster, val)
        vol = volatility_profile(val, p).set_index("phase").loc["late (25+)"]
        row = {**params, "trees": int(booster.num_boosted_rounds()), "train_s": t,
               "val_log_loss": log_loss(y, p),
               "val_late_log_loss": log_loss(y[late], p[late]) if late.any() else float("nan"),
               "val_late_ece": ece(y[late], p[late]),
               "late_extreme": vol["extreme"], "late_swing_q90": vol["swing_q90"],
               "late_event_move": vol["event_move"]}
        rows.append(row)
        log(f"    depth {params['max_depth']} min_child_weight {params['min_child_weight']:>4}: "
            f"{row['trees']:4d} trees  val log loss {row['val_log_loss']:.4f}  "
            f"late ECE {100 * row['val_late_ece']:.1f}  late event move {100 * row['late_event_move']:+.1f} pts")
        if best is None or row["val_log_loss"] < best[0]:
            best = (row["val_log_loss"], booster, params)
    table = pd.DataFrame(rows)
    table["chosen"] = [r["val_log_loss"] == best[0] for r in rows]
    return best[1], best[2], table


def out_of_fold_xgb(df: pd.DataFrame, n_rounds: int, k: int = 5, log=print,
                    params: dict | None = None) -> np.ndarray:
    """Score every game with an XGBoost model that never trained on it.

    Games are split into k folds; for each fold, train on the other k−1 with the
    production model's settings and its tree count (no early stopping, so no
    validation set needed), then predict the held-out fold. Used for the tier and patch
    calibration checks, where the test split alone has too few Diamond games.
    """
    from .dataset import fold_of

    folds = df["match_id"].map(lambda m: fold_of(m, k)).to_numpy()
    out = np.full(len(df), np.nan)
    params = {**XGB_PARAMS, **(params or {}), "n_estimators": n_rounds, "early_stopping_rounds": None}
    import xgboost as xgb

    for f in range(k):
        hold = folds == f
        model = xgb.XGBClassifier(
            **params, monotone_constraints="(" + ",".join(str(c) for c in MONOTONE) + ")")
        model.fit(df.loc[~hold, FEATURE_NAMES], df.loc[~hold, "blue_win"], verbose=False)
        out[hold] = model.predict_proba(df.loc[hold, FEATURE_NAMES])[:, 1]
        log(f"    fold {f + 1}/{k}: {int(hold.sum()):,} rows scored")
    return out


def train_xgb_fixed(df: pd.DataFrame, n_rounds: int, params: dict | None = None):
    """Train on every row of `df` with a fixed tree count (no early stopping) — the final
    model, fit on all games with the settings the out-of-fold gate certified."""
    import xgboost as xgb

    p = {**XGB_PARAMS, **(params or {}), "n_estimators": n_rounds, "early_stopping_rounds": None}
    model = xgb.XGBClassifier(
        **p, monotone_constraints="(" + ",".join(str(c) for c in MONOTONE) + ")")
    model.fit(df[FEATURE_NAMES], df["blue_win"], verbose=False)
    return model.get_booster()


def cross_fit_time_calibration(p_oof, df: pd.DataFrame, k: int = 5) -> np.ndarray:
    """Calibrate out-of-fold predictions without any game being scored by a calibrator that
    saw it: fold f is calibrated by a TimeCalibrator fitted on the other folds."""
    from .dataset import fold_of

    folds = df["match_id"].map(lambda m: fold_of(m, k)).to_numpy()
    y, minute = df["blue_win"].to_numpy(), df["minute"].to_numpy()
    p_oof = np.asarray(p_oof, float)
    out = np.empty(len(df))
    for f in range(k):
        hold = folds == f
        cal = TimeCalibrator().fit(p_oof[~hold], y[~hold], minute[~hold])
        out[hold] = cal.apply(p_oof[hold], minute[hold])
    return out


def predict_xgb(booster, df: pd.DataFrame) -> np.ndarray:
    import xgboost as xgb

    return booster.predict(xgb.DMatrix(df[FEATURE_NAMES], feature_names=FEATURE_NAMES))


def xgb_size(booster) -> dict:
    """Trees, split nodes and leaves. Each split stores a feature + threshold, each leaf a
    value — 'learned numbers' ≈ 2·splits + leaves."""
    t = booster.trees_to_dataframe()
    leaves = int((t["Feature"] == "Leaf").sum())
    splits = int(len(t) - leaves)
    return {"trees": int(t["Tree"].nunique()), "splits": splits, "leaves": leaves,
            "learned_numbers": 2 * splits + leaves}


def xgb_importance(booster) -> dict[str, float]:
    """Total gain per feature, as a share of the sum."""
    g = booster.get_score(importance_type="total_gain")
    tot = sum(g.values()) or 1.0
    return {f: g.get(f, 0.0) / tot for f in FEATURE_NAMES}


# ---- neural net (comparison) ---------------------------------------------

@dataclass
class MLPResult:
    scaler: object
    net: object
    epochs: int
    curve: list[tuple[int, float, float]]   # (epoch, train log loss, val log loss)

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        return self.net.predict_proba(self.scaler.transform(df[FEATURE_NAMES].to_numpy()))[:, 1]

    @property
    def n_params(self) -> int:
        return int(sum(w.size for w in self.net.coefs_) + sum(b.size for b in self.net.intercepts_))


def train_mlp(train: pd.DataFrame, val: pd.DataFrame, hidden=(64, 32), max_epochs: int = 60,
              patience: int = 8, seed: int = 0, log=print) -> MLPResult:
    """One epoch at a time; keep the epoch with the best validation log loss (the same
    early-stopping rule XGBoost gets)."""
    from sklearn.neural_network import MLPClassifier
    from sklearn.preprocessing import StandardScaler

    scaler = StandardScaler().fit(train[FEATURE_NAMES].to_numpy())
    Xtr = scaler.transform(train[FEATURE_NAMES].to_numpy())
    Xva = scaler.transform(val[FEATURE_NAMES].to_numpy())
    ytr, yva = train["blue_win"].to_numpy(), val["blue_win"].to_numpy()
    # sub-sample of train for a cheap train-loss readout
    rs = np.random.default_rng(seed).choice(len(Xtr), size=min(50_000, len(Xtr)), replace=False)

    net = MLPClassifier(hidden_layer_sizes=hidden, alpha=1e-4, batch_size=512,
                        learning_rate_init=1e-3, random_state=seed)
    best, best_ll, since, curve = None, np.inf, 0, []
    for epoch in range(1, max_epochs + 1):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            net.partial_fit(Xtr, ytr, classes=np.array([0, 1]))
        tr_ll = log_loss(ytr[rs], net.predict_proba(Xtr[rs])[:, 1])
        va_ll = log_loss(yva, net.predict_proba(Xva)[:, 1])
        curve.append((epoch, tr_ll, va_ll))
        if va_ll < best_ll - 1e-5:
            best, best_ll, since = copy.deepcopy(net), va_ll, 0
        else:
            since += 1
        if epoch % 5 == 0:
            log(f"    epoch {epoch:3d}  train {tr_ll:.4f}  val {va_ll:.4f}")
        if since >= patience:
            break
    best_epoch = min(curve, key=lambda c: c[2])[0]
    return MLPResult(scaler, best, best_epoch, curve)


# ---- isotonic calibration (fallback) -------------------------------------

def fit_isotonic(p, y) -> tuple[list[float], list[float]]:
    """Pool-adjacent-violators: the closest non-decreasing map from prediction to observed
    rate. Returns knots (x, y) for np.interp."""
    order = np.argsort(p, kind="mergesort")
    x = np.asarray(p, float)[order]
    t = np.asarray(y, float)[order]
    # start from one block per distinct x
    ux, start = np.unique(x, return_index=True)
    sums = np.add.reduceat(t, start)
    cnts = np.diff(np.append(start, len(x))).astype(float)
    xs = np.add.reduceat(x, start)
    blocks: list[list[float]] = []   # [sum_y, count, sum_x]
    for s, c, sx in zip(sums, cnts, xs):
        blocks.append([s, c, sx])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            s2, c2, x2 = blocks.pop()
            blocks[-1][0] += s2
            blocks[-1][1] += c2
            blocks[-1][2] += x2
    kx = [b[2] / b[1] for b in blocks]
    ky = [min(max(b[0] / b[1], EPS), 1 - EPS) for b in blocks]
    return kx, ky


def apply_isotonic(p, knots) -> np.ndarray:
    kx, ky = knots
    return np.interp(np.asarray(p, float), kx, ky)


# ---- calibrators (stored in winprob_meta.json, applied at inference) ----------

class IsotonicCalibrator:
    """The originally agreed correction: one map for all minutes."""

    def __init__(self, knots):
        self.knots = (list(knots[0]), list(knots[1]))

    @classmethod
    def fit(cls, p, y):
        return cls(fit_isotonic(p, y))

    def apply(self, p, minute=None) -> np.ndarray:
        return apply_isotonic(p, self.knots)

    @property
    def n_params(self) -> int:
        return 2 * len(self.knots[0])

    def to_dict(self) -> dict:
        return {"kind": "isotonic", "x": self.knots[0], "y": self.knots[1]}


class TimeCalibrator:
    """Fallback for v1's late-game overconfidence (proposed Oct 31; opt-in via --time-calibration).

        logit(p') = a + b·m + (c + d·m)·logit(p),   m = min(minute, 45) / 10

    (c + d·m) is how much to trust the model's confidence at minute m: below 1 = soften,
    above 1 = sharpen. Because it scales the logit, a lopsided state stays lopsided
    (90% softened by 0.9 is still ~88%) and the ORDER of predictions at any minute never
    changes. Smooth in time: a per-phase correction would make the curve jump by itself
    at 14:00 and 25:00, which Phase 6 would read as a swing.

    Fitted on OUT-OF-FOLD predictions for train + val games (test games excluded), so
    ~11.7k games inform it instead of val's 2.1k.
    """

    MAX_MINUTE = 45

    def __init__(self, w=None):
        self.w = None if w is None else np.asarray(w, float)

    def _design(self, p, minute):
        z = np.log(np.clip(p, EPS, 1 - EPS) / (1 - np.clip(p, EPS, 1 - EPS)))
        m = np.minimum(np.asarray(minute, float), self.MAX_MINUTE) / 10.0
        return np.column_stack([np.ones_like(z), m, z, z * m])

    def fit(self, p, y, minute, iters: int = 50) -> "TimeCalibrator":
        X, y = self._design(np.asarray(p, float), minute), np.asarray(y, float)
        w = np.array([0.0, 0.0, 1.0, 0.0])   # start at "no change"
        for _ in range(iters):
            q = _sigmoid(X @ w)
            H = (X * (q * (1 - q))[:, None]).T @ X + 1e-6 * np.eye(4)
            step = np.linalg.solve(H, X.T @ (q - y))
            w -= step
            if np.abs(step).max() < 1e-10:
                break
        self.w = w
        if self.trust(np.arange(0, self.MAX_MINUTE + 1)).min() <= 0:
            raise ValueError(f"time calibration would reverse predictions: {w}")
        return self

    def trust(self, minute) -> np.ndarray:
        """Multiplier on the model's logit at each minute (1 = unchanged)."""
        m = np.minimum(np.asarray(minute, float), self.MAX_MINUTE) / 10.0
        return self.w[2] + self.w[3] * m

    def apply(self, p, minute) -> np.ndarray:
        return _sigmoid(self._design(np.asarray(p, float), minute) @ self.w)

    n_params = 4

    def to_dict(self) -> dict:
        return {"kind": "time", "w": self.w.tolist(), "max_minute": self.MAX_MINUTE}


def calibrator_from_dict(d: dict | None):
    if not d:
        return None
    if d.get("kind", "isotonic") == "isotonic":
        return IsotonicCalibrator((d["x"], d["y"]))
    if d["kind"] == "time":
        return TimeCalibrator(d["w"])
    raise ValueError(f"unknown calibrator kind {d['kind']!r}")


def timed(fn, *args, **kwargs):
    t0 = time.perf_counter()
    out = fn(*args, **kwargs)
    return out, time.perf_counter() - t0
