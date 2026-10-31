"""Phase 5.1 — build the win-probability training table.

    python scripts/build_winprob_dataset.py

Needs teams_<tier>.parquet and events_<tier>.parquet from scripts/extract.py.
Writes data/processed/winprob_dataset.parquet: one row per game-minute, Blue's side.
"""

from __future__ import annotations

import argparse
import sys

from lolreview.config import PROCESSED_DIR
from lolreview.extract.build import table_path
from lolreview.model.dataset import FEATURE_NAMES, build_dataset

OUT = PROCESSED_DIR / "winprob_dataset.parquet"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiers", default="emerald,diamond",
                    help="comma-separated; a game in two corpora is kept under the first")
    args = ap.parse_args()
    tiers = tuple(t.strip().lower() for t in args.tiers.split(",") if t.strip())

    for tier in tiers:
        for name in ("teams", "events"):
            if not table_path(PROCESSED_DIR, name, tier).exists():
                sys.exit(f"No {name} table for {tier}. Run: python scripts/extract.py --tier {tier}")

    df = build_dataset(tiers)
    df.to_parquet(OUT, index=False)

    games = df.groupby("match_id").agg(split=("split", "first"), tier=("tier", "first"),
                                       win=("blue_win", "first"))
    print(f"{len(df):,} game-minute rows from {len(games):,} games, "
          f"{len(FEATURE_NAMES)} features -> {OUT}")
    print("games per split:", games["split"].value_counts().reindex(["train", "val", "test"]).to_dict())
    print("games per tier: ", games["tier"].value_counts().to_dict())
    print("rows per patch: ", df["patch"].value_counts().sort_index().to_dict())
    print(f"Blue win rate:   {games['win'].mean():.3f}")
    nans = int(df[FEATURE_NAMES].isna().sum().sum())
    if nans:
        print(f"WARNING: {nans} missing feature values")


if __name__ == "__main__":
    main()
