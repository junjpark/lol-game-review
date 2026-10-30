"""Phase 4 — build baselines from the extracted frames tables.

    python scripts/build_baselines.py --tier all
    python scripts/build_baselines.py --tier emerald --no-check

Needs frames_<tier>.parquet from scripts/extract.py. Takes a few seconds per tier, plus a
few more for the holdout check (build on 80% of games, verify on the other 20%).
"""

from __future__ import annotations

import argparse
import json
import sys

from lolreview.baselines import (
    Baselines, compute_baselines, holdout_check, ROLES, save,
)
from lolreview.baselines.compute import COLUMNS
from lolreview.config import PROCESSED_DIR
from lolreview.extract.build import load as load_table, table_path

SHOW = [(10, ["cs", "gold", "level", "deaths", "wards_placed"]),
        (20, ["cs", "gold", "kills", "deaths", "assists", "kill_participation", "wards_placed"])]


def fmt(v, stat):
    if v is None:
        return "—"
    if stat == "kill_participation":
        return f"{100 * v:.0f}%"
    return f"{v:,.0f}"


def build(tier: str, check: bool) -> Baselines:
    if not table_path(PROCESSED_DIR, "frames", tier).exists():
        sys.exit(f"No frames table for {tier}. Run: python scripts/extract.py --tier {tier}")
    frames = load_table("frames", tier, columns=COLUMNS + ["patch"])
    table = compute_baselines(frames, tier)
    extract_meta = PROCESSED_DIR / f"extract_{tier}.json"
    meta = {
        "games": int(frames.loc[~frames.game_has_afk.astype(bool), "match_id"].nunique()),
        "afk_games_excluded": int(frames.loc[frames.game_has_afk.astype(bool), "match_id"].nunique()),
        "patches": sorted(frames["patch"].dropna().unique().tolist()),
        "extract_version": json.loads(extract_meta.read_text()).get("extract_version")
        if extract_meta.exists() else None,
    }
    if check:
        chk = holdout_check(frames, tier)
        meta["holdout_check"] = chk.to_dict(orient="records")
    path = save(table, meta)
    base = Baselines(table)

    print(f"\n{tier}: {meta['games']:,} games ({meta['afk_games_excluded']} AFK games left out), "
          f"patches {', '.join(meta['patches'])}")
    print(f"  {len(table):,} cells -> {path}")
    print(f"  reliable through minute {base.last_reliable_minute('TOP')} "
          f"(cells need {base.min_rows}+ player-rows)")
    if check:
        cont = chk[~chk.count_stat]
        print(f"  holdout check: average percentile {chk.mean_percentile.min():.1f}–{chk.mean_percentile.max():.1f} "
              f"(target 50); continuous stats put {cont['bottom_tenth_%'].min():.1f}–{cont['bottom_tenth_%'].max():.1f}% "
              f"in the bottom tenth (target ~10)")
    for minute, stats in SHOW:
        print(f"\n  typical at {minute}:00" + "".join(f"{r:>10}" for r in ROLES))
        for s in stats:
            print(f"  {s:20}" + "".join(f"{fmt(base.median(r, minute, s), s):>10}" for r in ROLES))
    return base


def main() -> None:
    ap = argparse.ArgumentParser(description="Build Phase 4 baselines")
    ap.add_argument("--tier", default="all", help="emerald, diamond, or all")
    ap.add_argument("--no-check", action="store_true", help="skip the 80/20 holdout check")
    args = ap.parse_args()
    for tier in (["emerald", "diamond"] if args.tier == "all" else [args.tier.lower()]):
        build(tier, check=not args.no_check)


if __name__ == "__main__":
    main()
