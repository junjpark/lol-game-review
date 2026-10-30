"""Compute baselines: for each role × minute × stat, what's normal at this rank.

Each cell stores the row count, mean, spread, and the full percentile curve (the 0th,
1st, ..., 100th percentile). Percentiles are the primary measure for every stat — they
need no assumption about the shape of the data. The mean and spread are kept for the
Phase 5 model, which may want them.

Rows used: AFK games dropped (they're effectively decided), players with no role dropped,
missing values (e.g. no lane opponent) skipped per stat.
"""

from __future__ import annotations

import zlib

import numpy as np
import pandas as pd

from .stats import STATS

QUANTILES = np.linspace(0.0, 1.0, 101)  # 0th..100th percentile

# A cell needs this many player-rows before its percentiles are trusted. In the Emerald
# corpus that holds through minute 42; Diamond through minute 38.
MIN_ROWS = 300

COLUMNS = ["match_id", "role", "minute", "game_has_afk", *(s.name for s in STATS)]


def prepare(frames: pd.DataFrame) -> pd.DataFrame:
    """Rows that belong in a baseline."""
    keep = (~frames["game_has_afk"].astype(bool)) & frames["role"].notna()
    return frames.loc[keep]


def compute_baselines(frames: pd.DataFrame, tier: str) -> pd.DataFrame:
    """One row per (role, minute, stat): n, mean, std, q (101 percentile values)."""
    df = prepare(frames)
    parts = []
    for stat in STATS:
        sub = df[["role", "minute", stat.name]].dropna()
        if sub.empty:
            continue
        sub = sub.astype({stat.name: "float64"})
        grouped = sub.groupby(["role", "minute"])[stat.name]
        desc = grouped.agg(n="count", mean="mean", std="std")
        curve = grouped.quantile(QUANTILES).unstack()  # columns = the 101 quantiles
        desc["q"] = [row.to_numpy(dtype="float64") for _, row in curve.iterrows()]
        desc["stat"] = stat.name
        parts.append(desc.reset_index())
    out = pd.concat(parts, ignore_index=True)
    out.insert(0, "tier", tier.lower())
    out["n"] = out["n"].astype("int32")
    return out[["tier", "role", "minute", "stat", "n", "mean", "std", "q"]] \
        .sort_values(["role", "minute", "stat"], ignore_index=True)


def split_games(match_ids: pd.Series, holdout: float = 0.2) -> pd.Series:
    """Deterministic train/holdout split by game (never by row: rows within a game are
    not independent). True = holdout."""
    return match_ids.map(lambda m: zlib.crc32(m.encode()) % 1000 < holdout * 1000)


def holdout_check(frames: pd.DataFrame, tier: str, max_minute: int = 30) -> pd.DataFrame:
    """Build baselines on 80% of games, place the other 20%.

    If the baselines are right, held-out players' percentiles spread evenly: the average
    should sit near 50, and for continuous stats about 10% should land in the bottom
    tenth and 10% in the top tenth. Count stats can't be perfectly even (ties), but their
    average should still be near 50.
    """
    from .lookup import place_many

    df = prepare(frames)
    held = split_games(df["match_id"])
    train = compute_baselines(df.loc[~held], tier)
    cells = {(r.role, int(r.minute), r.stat): (r.n, np.asarray(r.q)) for r in train.itertuples(index=False)}
    test = df.loc[held & (df["minute"] <= max_minute)]
    rows = []
    for stat in STATS:
        sub = test[["role", "minute", stat.name]].dropna()
        chunks = []
        for (role, minute), grp in sub.groupby(["role", "minute"]):
            cell = cells.get((role, int(minute), stat.name))
            if cell is None or cell[0] < MIN_ROWS:
                continue
            lower, tied = place_many(cell[1], grp[stat.name].to_numpy())
            chunks.append(lower + tied / 2)
        mids = np.concatenate(chunks) if chunks else np.array([])
        rows.append({
            "stat": stat.name, "count_stat": stat.count, "placed": len(mids),
            "mean_percentile": round(float(mids.mean()), 1) if len(mids) else None,
            "bottom_tenth_%": round(100 * float((mids < 10).mean()), 1) if len(mids) else None,
            "top_tenth_%": round(100 * float((mids > 90).mean()), 1) if len(mids) else None,
        })
    return pd.DataFrame(rows)
