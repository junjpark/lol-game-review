"""Phase 4 baseline tests — synthetic data, no corpus needed."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from lolreview.baselines import Baselines, compute_baselines, holdout_check
from lolreview.baselines.compute import MIN_ROWS, QUANTILES, split_games
from lolreview.baselines.lookup import place_many, place_on_curve
from lolreview.baselines.report import checkpoints, render, share_better_worse
from lolreview.baselines.stats import BY_NAME, STATS, stats_for


def frames(n_games: int = 400, seed: int = 0) -> pd.DataFrame:
    """Two junglers per game, minutes 0–20. gold ~ Normal; deaths ~ Poisson."""
    rng = np.random.default_rng(seed)
    rows = []
    for g in range(n_games):
        for slot in range(2):
            for minute in range(21):
                rows.append({
                    "match_id": f"NA1_{g}", "role": "JUNGLE", "minute": minute,
                    "game_has_afk": False,
                    **{s.name: np.nan for s in STATS},
                    "gold": 500 + 380 * minute + rng.normal(0, 400),
                    "deaths": int(rng.poisson(0.1 * minute)),
                })
    return pd.DataFrame(rows)


# ---- percentile math -------------------------------------------------------------------

def test_place_on_continuous_curve():
    q = np.linspace(0, 100, 101)  # value k is exactly the k-th percentile
    assert place_on_curve(q, 25.0) == (24.5, 1.0)   # lands on a grid point
    assert place_on_curve(q, 25.5) == (25.5, 0.0)
    assert place_on_curve(q, -5) == (0.0, 0.0)
    assert place_on_curve(q, 500) == (100.0, 0.0)


def test_ties_are_reported_not_hidden():
    data = np.array([0] * 330 + [1] * 370 + [2] * 210 + [3] * 70 + [4] * 20, dtype=float)
    q = np.quantile(data, QUANTILES)
    lower, tied = place_on_curve(q, 0.0)
    assert lower == 0.0 and tied == pytest.approx(33, abs=1.5)   # a third tie on 0
    lower, tied = place_on_curve(q, 3.0)
    assert lower == pytest.approx(91, abs=1.5) and tied == pytest.approx(7, abs=1.5)


def test_vectorised_matches_scalar():
    rng = np.random.default_rng(1)
    for data in (rng.normal(size=3000), rng.poisson(1.2, size=3000).astype(float)):
        q = np.quantile(data, QUANTILES)
        probes = np.concatenate([q, data[:300], [-1e9, 1e9]])
        lo, tie = place_many(q, probes)
        exp = [place_on_curve(q, float(v)) for v in probes]
        assert np.allclose(lo, [e[0] for e in exp]) and np.allclose(tie, [e[1] for e in exp])


# ---- building --------------------------------------------------------------------------

def test_compute_shape_and_values():
    table = compute_baselines(frames(), "emerald")
    assert set(table.stat) == {"gold", "deaths"}            # all-NaN stats are skipped
    cell = table[(table.minute == 10) & (table.stat == "gold")].iloc[0]
    assert cell.n == 800 and len(cell.q) == 101
    assert cell.q[50] == pytest.approx(4300, abs=80)          # 500 + 380*10
    assert np.all(np.diff(cell.q) >= 0)


def test_afk_games_and_missing_roles_excluded():
    df = frames(50)
    df.loc[df.match_id == "NA1_0", "game_has_afk"] = True
    df.loc[df.match_id == "NA1_1", "role"] = None
    table = compute_baselines(df, "emerald")
    assert table.loc[(table.minute == 5) & (table.stat == "gold"), "n"].iloc[0] == 2 * 48


def test_thin_cells_refuse_to_answer():
    b = Baselines(compute_baselines(frames(100), "emerald"))   # 200 rows per cell
    assert b.place("JUNGLE", 10, "gold", 4000) is None
    assert b.median("JUNGLE", 10, "gold") is None
    loose = Baselines(compute_baselines(frames(100), "emerald"), min_rows=100)
    assert loose.place("JUNGLE", 10, "gold", 4000) is not None
    assert MIN_ROWS == 300


def test_unknown_cell_or_missing_value_returns_none():
    b = Baselines(compute_baselines(frames(), "emerald"))
    assert b.place("TOP", 10, "gold", 4000) is None           # no top laners in the data
    assert b.place("JUNGLE", 99, "gold", 4000) is None
    assert b.place("JUNGLE", 10, "gold", None) is None
    assert b.last_reliable_minute("JUNGLE") == 20


def test_split_is_by_game_and_deterministic():
    ids = pd.Series([f"NA1_{i}" for i in range(2000)])
    a, b = split_games(ids), split_games(ids)
    assert (a == b).all() and 0.15 < a.mean() < 0.25


def test_holdout_check_is_centred():
    chk = holdout_check(frames(1000), "emerald", max_minute=20).set_index("stat")
    assert abs(chk.loc["gold", "mean_percentile"] - 50) < 3
    assert 7 < chk.loc["gold", "bottom_tenth_%"] < 13


# ---- report ----------------------------------------------------------------------------

def test_supports_dont_get_cs_stats():
    names = {s.name for s in stats_for("UTILITY")}
    assert "cs" not in names and "cs_diff" not in names and "wards_placed" in names
    assert "cs" in {s.name for s in stats_for("JUNGLE")}


def test_better_worse_counts_ties_on_both_sides():
    from lolreview.baselines.lookup import Placement
    p = Placement(value=0, n=1000, pct_lower=0, pct_tied=33, pct_higher=67, median=1)
    top, bottom = share_better_worse(BY_NAME["deaths"], p)     # fewer deaths is better
    assert (top, bottom) == (33, 100)
    top, bottom = share_better_worse(BY_NAME["kills"], p)      # more kills is better
    assert (top, bottom) == (100, 33)


def test_checkpoints():
    assert checkpoints(35) == [10, 15, 20, 35]
    assert checkpoints(17) == [10, 15, 17]
    assert checkpoints(20) == [10, 15, 20]


def test_render_runs_and_flags_extremes():
    b = Baselines(compute_baselines(frames(), "emerald"))
    rows = [{"minute": m, "t_ms": m * 60_000, **{s.name: None for s in STATS},
             "gold": 500 + 380 * m + 1500, "deaths": 0} for m in range(21)]
    text = render(rows, "JUNGLE", b)
    assert "How you compare with Emerald junglers" in text
    assert "end 20:00" in text
    assert "gold earned at" in text.split("What stands out")[1]   # +1500 gold is top few %


# ---- parquet round trip (needs pyarrow) ------------------------------------------------

def test_save_and_load_round_trip():
    pytest.importorskip("pyarrow")
    from lolreview.baselines import load, save
    d = Path(tempfile.mkdtemp())
    table = compute_baselines(frames(), "emerald")
    save(table, {"games": 400, "patches": ["16.20"]}, baseline_dir=d)
    b = load("emerald", baseline_dir=d)
    assert b.meta["games"] == 400 and b.meta["patches"] == ["16.20"]
    assert b.median("JUNGLE", 10, "gold") == pytest.approx(Baselines(table).median("JUNGLE", 10, "gold"))
