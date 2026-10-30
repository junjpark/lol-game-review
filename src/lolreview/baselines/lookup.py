"""Place a value against a baseline: what share of players at this rank, role and minute
had less, the same, or more?

Works from the stored 101-point percentile curve, so answers are accurate to about one
percentage point — plenty for "bottom 15%" style statements.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .compute import MIN_ROWS


@dataclass(frozen=True)
class Placement:
    value: float
    n: int              # player-rows in the baseline cell
    pct_lower: float    # % of players with a lower value
    pct_tied: float     # % with the same value (matters for count stats)
    pct_higher: float
    median: float

    @property
    def percentile(self) -> float:
        """Single-number percentile (ties split evenly)."""
        return self.pct_lower + self.pct_tied / 2


def place_on_curve(q: np.ndarray, value: float) -> tuple[float, float]:
    """(% lower, % tied) for `value` against a 101-point percentile curve."""
    lo = int(np.searchsorted(q, value, side="left"))
    hi = int(np.searchsorted(q, value, side="right"))
    last = len(q) - 1  # 100
    if hi > lo:  # value sits on a flat run of the curve: a tie (count stats)
        lower = 0.0 if lo == 0 else lo - 0.5
        at_or_below = 100.0 if hi > last else hi - 0.5
        return lower, at_or_below - lower
    if lo == 0:
        return 0.0, 0.0
    if lo > last:
        return 100.0, 0.0
    k = lo - 1  # q[k] < value < q[lo]
    return k + (value - q[k]) / (q[lo] - q[k]), 0.0


def place_many(q: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Vectorised place_on_curve: (% lower, % tied) for many values against one curve."""
    v = np.asarray(values, dtype="float64")
    last = len(q) - 1
    lo = np.searchsorted(q, v, side="left")
    hi = np.searchsorted(q, v, side="right")
    tie = hi > lo
    k = np.clip(lo - 1, 0, last)
    nxt = np.clip(lo, 0, last)
    span = q[nxt] - q[k]
    with np.errstate(divide="ignore", invalid="ignore"):
        interp = np.where(span > 0, k + (v - q[k]) / span, k)
    lower_cont = np.where(lo == 0, 0.0, np.where(lo > last, 100.0, interp))
    lower_tie = np.where(lo == 0, 0.0, lo - 0.5)
    upper_tie = np.where(hi > last, 100.0, hi - 0.5)
    lower = np.where(tie, lower_tie, lower_cont)
    tied = np.where(tie, upper_tie - lower_tie, 0.0)
    return lower, tied


class Baselines:
    """Lookup over a baseline table (as built by compute_baselines / read from disk)."""

    def __init__(self, table: pd.DataFrame, min_rows: int = MIN_ROWS):
        self.min_rows = min_rows
        self.tier = str(table["tier"].iloc[0]) if len(table) else None
        self._cells = {
            (r.role, int(r.minute), r.stat): (int(r.n), np.asarray(r.q, dtype="float64"))
            for r in table.itertuples(index=False)
        }

    def _cell(self, role: str, minute: int, stat: str):
        cell = self._cells.get((role, minute, stat))
        if cell is None or cell[0] < self.min_rows:
            return None  # not enough games: refuse rather than guess
        return cell

    def median(self, role: str, minute: int, stat: str) -> float | None:
        cell = self._cell(role, minute, stat)
        return float(cell[1][50]) if cell else None

    def place(self, role: str, minute: int, stat: str, value: float | None) -> Placement | None:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return None
        cell = self._cell(role, minute, stat)
        if cell is None:
            return None
        n, q = cell
        lower, tied = place_on_curve(q, float(value))
        return Placement(float(value), n, lower, tied, max(0.0, 100.0 - lower - tied), float(q[50]))

    def last_reliable_minute(self, role: str, stat: str = "gold") -> int | None:
        minutes = [m for (r, m, s), (n, _) in self._cells.items()
                   if r == role and s == stat and n >= self.min_rows]
        return max(minutes) if minutes else None
