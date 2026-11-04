"""Phase 4 — baselines: what normal looks like per rank × role × minute.

    from lolreview.baselines import load
    emerald = load("emerald")
    p = emerald.place("JUNGLE", 10, "cs", 52)   # Placement(pct_lower=12.3, ...) or None

Files (data/baselines/):
    baselines_<tier>.parquet   one row per (role, minute, stat)
    baselines_<tier>.json      what it was built from: games, patches, extract version, date

Baselines go stale with patches (docs/decisions.md: "patch drift invalidates baselines"); the
JSON records the patches so a report can warn when a game is from a different one.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd

from .compute import MIN_ROWS, QUANTILES, compute_baselines, holdout_check
from .lookup import Baselines, Placement
from .stats import BY_NAME, ROLE_NAMES, ROLES, STATS, Stat, stats_for

BASELINE_VERSION = 1


def baseline_path(tier: str, baseline_dir: Path | None = None) -> Path:
    from ..config import BASELINE_DIR

    return Path(baseline_dir or BASELINE_DIR) / f"baselines_{tier.lower()}.parquet"


def save(table: pd.DataFrame, meta: dict, baseline_dir: Path | None = None) -> Path:
    tier = str(table["tier"].iloc[0])
    path = baseline_path(tier, baseline_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = table.copy()
    out["q"] = out["q"].map(lambda a: [float(x) for x in a])
    out.to_parquet(path, index=False)
    full = {
        "tier": tier,
        "baseline_version": BASELINE_VERSION,
        "built": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "min_rows": MIN_ROWS,
        "stats": [s.name for s in STATS],
        **meta,
    }
    path.with_suffix(".json").write_text(json.dumps(full, indent=2))
    return path


def load(tier: str, baseline_dir: Path | None = None) -> Baselines:
    path = baseline_path(tier, baseline_dir)
    if not path.exists():
        raise FileNotFoundError(f"No baselines for {tier}: run  python scripts/build_baselines.py --tier {tier}")
    b = Baselines(pd.read_parquet(path))
    meta_path = path.with_suffix(".json")
    b.meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    return b


__all__ = [
    "Baselines", "Placement", "Stat", "STATS", "BY_NAME", "ROLES", "ROLE_NAMES", "QUANTILES",
    "MIN_ROWS", "compute_baselines", "holdout_check", "stats_for", "baseline_path", "save", "load",
]
