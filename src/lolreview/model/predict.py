"""Phase 5.4 — inference: P(Blue wins) for any game, and how big a 'normal' swing is.

    from lolreview.model.predict import WinProb
    wp = WinProb.load()
    curve = wp.for_game(extract_game(match, timeline, "emerald"))   # minute, t_ms, p_blue

Files (models/, written by scripts/train_winprob.py):
    winprob_xgb.json         the booster, cut at its best validation round
    winprob_meta.json        feature list, calibrator knots (or null), metrics, gate result
    wpa_distribution.json    per-minute quantiles of |ΔP| between consecutive minutes on
                             held-out games — the yardstick Phase 6 uses to say whether a
                             swing is unusual. Reserve 'blunder' for the far tail.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from .dataset import FEATURE_NAMES, features_for_game
from .models import calibrator_from_dict

MODEL_FILE = "winprob_xgb.json"
META_FILE = "winprob_meta.json"
WPA_FILE = "wpa_distribution.json"
SWING_QUANTILES = (0.5, 0.75, 0.9, 0.95, 0.99)


class WinProb:
    def __init__(self, booster, calibrator=None, meta: dict | None = None):
        self.booster = booster
        self.calibrator = calibrator   # models.IsotonicCalibrator / TimeCalibrator, or None
        self.meta = meta or {}

    @classmethod
    def load(cls, model_dir: Path | None = None) -> "WinProb":
        import xgboost as xgb

        from ..config import MODEL_DIR

        d = Path(model_dir or MODEL_DIR)
        if not (d / MODEL_FILE).exists():
            raise FileNotFoundError(f"No model in {d}. Run: python scripts/train_winprob.py")
        meta = json.loads((d / META_FILE).read_text())
        unknown = [f for f in meta.get("features", []) if f not in FEATURE_NAMES]
        if not meta.get("features") or unknown:
            raise ValueError(f"Saved model uses features this code can't build ({unknown}); retrain it.")
        if not meta.get("gate_passed"):
            warnings.warn("This win-probability model FAILED its calibration gate; severity "
                          "labels built on it can't be trusted (see winprob_report.html).")
        booster = xgb.Booster()
        booster.load_model(str(d / MODEL_FILE))
        return cls(booster, calibrator_from_dict(meta.get("calibrator")), meta)

    @property
    def version(self) -> str:
        return self.meta.get("model_version", "v1")

    @property
    def released(self) -> bool:
        """True only once the model passed gate v2 AND the fresh-holdout check."""
        return bool(self.meta.get("released"))

    def predict(self, features: pd.DataFrame) -> np.ndarray:
        import xgboost as xgb

        feats = self.meta.get("features") or FEATURE_NAMES   # v1 models use the first 29
        p = self.booster.predict(xgb.DMatrix(features[feats], feature_names=feats))
        if self.calibrator is None:
            return p
        return self.calibrator.apply(p, features["minute"].to_numpy())

    def for_game(self, extracted: dict) -> pd.DataFrame:
        """Every snapshot of one game (output of extract_game), final one included."""
        f = features_for_game(extracted)
        return pd.DataFrame({"minute": f["minute"].astype(int), "t_ms": f["t_ms"],
                             "p_blue": self.predict(f)})


def swings(df: pd.DataFrame, p) -> pd.DataFrame:
    """ΔP(Blue) between consecutive minutes of the same game. `df` sorted by game, minute."""
    s = pd.DataFrame({"match_id": df["match_id"].to_numpy(),
                      "minute": df["minute"].to_numpy().astype(int), "p": np.asarray(p, float)})
    prev = s.groupby("match_id", sort=False)
    s["delta"] = s["p"] - prev["p"].shift(1)
    s["gap"] = s["minute"] - prev["minute"].shift(1)
    return s[s["gap"] == 1].drop(columns="gap")


def wpa_distribution(df: pd.DataFrame, p, max_minute: int = 40, min_rows: int = 300) -> dict:
    """Quantiles of |ΔP| per minute (absolute swing, in probability points 0–1). Minutes
    with too few rows borrow the last reliable minute's numbers."""
    s = swings(df, p)
    s["abs"] = s["delta"].abs()
    out, last = {}, None
    for minute in range(1, max_minute + 1):
        g = s.loc[s["minute"] == minute, "abs"]
        if len(g) >= min_rows:
            last = {f"q{int(q * 100)}": float(g.quantile(q)) for q in SWING_QUANTILES}
            last["rows"] = int(len(g))
        if last is not None:
            out[str(minute)] = last
    return {"quantiles": list(SWING_QUANTILES), "by_minute": out,
            "overall": {f"q{int(q * 100)}": float(s["abs"].quantile(q)) for q in SWING_QUANTILES}}
