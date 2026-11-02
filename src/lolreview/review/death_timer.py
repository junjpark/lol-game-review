"""How long a death lasts, MEASURED from the corpus (Phase 6 direct cost).

Riot doesn't publish the formula, community sources disagree on its time scaling, and it
changed in 2024. So we measure it: a player counts as dead at a minute snapshot when their
health is 0. For each death we know how many seconds before the next snapshots it happened.
Per (level at death, game-time bucket) we find the cut-off T that best separates "still dead"
from "alive again": T is the death timer.

Single snapshots are noisy (≈13% of snapshots seconds after a death still show health), so the
table is ± a few seconds. Two physical facts are enforced: timers never shrink with level or
with game time (running max along both).

Positions while dead stay at the death location, so respawn can't be read from position.
That's why this table exists.

Rebuild: `python scripts/build_death_timers.py` (reads frames_/events_ Parquet), then paste
the printed TABLE here. Like `rift.py`'s landmarks, the measured numbers live in code.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# start minute of each game-time bucket; the last bucket is open-ended
MINUTE_BUCKETS = (0, 15, 20, 25, 30, 35, 40)
MIN_PAIRS = 150

# level -> {bucket start minute -> seconds dead}. Measured Nov 2, 2026 from 3,000 Emerald games
# (patches 16.18–16.21, ~522k death-snapshot pairs). See MEASURED.
TABLE: dict[int, dict[int, float]] = {
    1: {0: 9.9},
    2: {0: 11.9},
    3: {0: 12.2},
    4: {0: 12.2},
    5: {0: 14.1},
    6: {0: 15.9},
    7: {0: 20.2, 15: 20.2},
    8: {0: 25.0, 15: 25.5},
    9: {0: 27.8, 15: 28.7, 20: 29.5},
    10: {0: 32.7, 15: 33.3, 20: 34.4, 25: 36.7},
    11: {0: 34.8, 15: 35.8, 20: 37.2, 25: 38.5},
    12: {0: 37.4, 15: 38.6, 20: 40.1, 25: 41.1, 30: 44.8},
    13: {15: 41.0, 20: 42.6, 25: 44.4, 30: 45.3},
    14: {15: 43.8, 20: 45.4, 25: 46.6, 30: 48.2, 35: 49.9},
    15: {15: 47.2, 20: 47.8, 25: 49.2, 30: 51.5, 35: 52.4},
    16: {20: 50.9, 25: 52.2, 30: 54.0, 35: 56.0},
    17: {20: 52.2, 25: 55.2, 30: 57.1, 35: 58.0, 40: 60.2},
    18: {25: 58.3, 30: 60.1, 35: 61.2, 40: 61.6},
    19: {30: 60.1, 35: 61.2},
    20: {30: 60.1, 35: 61.2, 40: 63.0},
}
MEASURED: dict = {
    "built": "2026-11-02", "games": 3000, "corpus": "emerald (every 4th game, 2 offsets)",
    "patches": "16.18-16.21", "pairs_used": 359181,
    "misclassified_snapshots": 0.102,   # noise floor of the health==0 signal, not timer error
    "agrees_with": "community formula at levels 7-12 (21/26/32.5/35/37.5 s); longer at levels 1-6 "
                   "(Riot's 2024 early-timer change)",
}


def bucket_of(minute: float) -> int:
    return max(b for b in MINUTE_BUCKETS if minute >= b)


def death_seconds(level: int, minute: float, table: dict | None = None) -> float:
    """Seconds dead for a death at `level` and game `minute`. Missing cells use the nearest
    measured cell: same level, closest bucket (later buckets preferred, timers grow with time);
    then the closest level."""
    table = TABLE if table is None else table
    if not table:
        raise RuntimeError("death-timer TABLE is empty; run scripts/build_death_timers.py")
    level = int(min(max(level, min(table)), max(table)))
    if level not in table:
        level = min(table, key=lambda lv: (abs(lv - level), -lv))
    row = table[level]
    b = bucket_of(minute)
    if b in row:
        return row[b]
    return row[min(row, key=lambda x: (abs(x - b), -x))]


def fit_table(rows: pd.DataFrame, min_pairs: int = MIN_PAIRS) -> dict[int, dict[int, float]]:
    """rows: minute (of death), level (at death), since_s (snapshot time − death time, s),
    dead (health == 0 at that snapshot). Returns {level: {bucket: seconds}}."""
    r = rows.copy()
    r["bucket"] = [bucket_of(m) for m in r["minute"].to_numpy()]
    out: dict[int, dict[int, float]] = {}
    for (lv, b), g in r.groupby(["level", "bucket"]):
        if len(g) < min_pairs:
            continue
        s, d = g["since_s"].to_numpy(float), g["dead"].to_numpy(bool)
        cand = np.unique(np.round(np.quantile(s, np.linspace(0.01, 0.99, 197)), 1))
        err = [np.mean((s < T) != d) for T in cand]
        out.setdefault(int(lv), {})[int(b)] = float(cand[int(np.argmin(err))])
    # monotone: not shorter at a higher level (same bucket) or later bucket (same level)
    for b in MINUTE_BUCKETS:
        best = 0.0
        for lv in sorted(out):
            if b in out[lv]:
                best = max(best, out[lv][b])
                out[lv][b] = best
    for lv in out:
        best = 0.0
        for b in sorted(out[lv]):
            best = max(best, out[lv][b])
            out[lv][b] = round(best, 1)
    return out


def table_source(table: dict) -> str:
    lines = ["TABLE: dict[int, dict[int, float]] = {"]
    for lv in sorted(table):
        cells = ", ".join(f"{b}: {table[lv][b]}" for b in sorted(table[lv]))
        lines.append(f"    {lv}: {{{cells}}},")
    lines.append("}")
    return "\n".join(lines)
