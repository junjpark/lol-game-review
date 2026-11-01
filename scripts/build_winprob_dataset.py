"""Phase 5.1 — build the win-probability training table.

    python scripts/build_winprob_dataset.py
    python scripts/build_winprob_dataset.py --corpora emerald,diamond     # v1's corpora only

Needs teams_/events_/frames_<corpus>.parquet from scripts/extract.py (frames are new in v2:
they give per-player gold for the gold-spread features).
Writes data/processed/winprob_dataset.parquet: one row per game-minute, Blue's side.

Default corpora: emerald, diamond and emerald_holdout. The 16.21 holdout was used for v1's
release check, so it can't test a new model; it joins training instead. The NEXT release
check needs a fresh set (e.g. 16.22).
"""

from __future__ import annotations

import argparse
import sys

from lolreview.config import PROCESSED_DIR
from lolreview.extract.build import table_path
from lolreview.model.dataset import DEFAULT_CORPORA, FEATURE_NAMES, ROLE_FEATURES, build_dataset

OUT = PROCESSED_DIR / "winprob_dataset.parquet"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpora", "--tiers", default=",".join(DEFAULT_CORPORA),
                    help="comma-separated corpus names; a game in two corpora is kept under the first")
    args = ap.parse_args()
    corpora = tuple(t.strip().lower() for t in args.corpora.split(",") if t.strip())

    for corpus in corpora:
        for name in ("teams", "events", "frames"):
            if not table_path(PROCESSED_DIR, name, corpus).exists():
                sys.exit(f"No {name} table for {corpus}. Run: python scripts/extract.py --tier {corpus}")

    df = build_dataset(corpora)
    df.to_parquet(OUT, index=False)

    games = df.groupby("match_id").agg(split=("split", "first"), corpus=("corpus", "first"),
                                       win=("blue_win", "first"))
    print(f"{len(df):,} game-minute rows from {len(games):,} games, "
          f"{len(FEATURE_NAMES)} features -> {OUT}")
    print("games per split: ", games["split"].value_counts().reindex(["train", "val", "test"]).to_dict())
    print("games per corpus:", games["corpus"].value_counts().to_dict())
    print("rows per patch:  ", df["patch"].value_counts().sort_index().to_dict())
    print(f"Blue win rate:    {games['win'].mean():.3f}")
    role_nan = df.loc[df[ROLE_FEATURES].isna().any(axis=1), "match_id"].nunique()
    if role_nan:
        print(f"note: {role_nan} games have a role Riot left unassigned or doubled; their per-role "
              "gold diffs are blank (XGBoost handles that)")
    other = [c for c in FEATURE_NAMES if c not in ROLE_FEATURES]
    nans = int(df[other].isna().sum().sum())
    if nans:
        print(f"WARNING: {nans} missing values in features that should always be present")


if __name__ == "__main__":
    main()
