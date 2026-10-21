"""Phase 3 — build the tables from a tier's corpus.

    python scripts/extract.py --tier emerald
    python scripts/extract.py --tier all
    python scripts/extract.py --tier emerald --limit 50     # quick trial run

Writes data/processed/{frames,events,teams}_<tier>.parquet plus extract_<tier>.json, and
prints how the extracted numbers compare with Riot's own end-of-game summary.
Takes roughly 15 ms per game (about 2 minutes for all 7,000). No API key needed.
"""

from __future__ import annotations

import argparse
import sys

from lolreview.config import PROCESSED_DIR, RAW_DIR
from lolreview.extract.build import build_tier, table_path
from lolreview.riot.cache import JsonCache

# What "ok" means, and why a few checks can't reach 100%.
NOTES = {
    "team_kills": "deaths to neutral monsters count for nobody; a handful of edge cases remain",
    "wards_placed": "±2 allowed: Riot counts ward-hops (Lee Sin, Jax) differently",
    "lane_symmetry": "no_opponent = player's role was missing in Riot's data",
}


def report(meta: dict) -> None:
    print(f"\n{meta['tier']}: {meta['games']:,} games in {meta['seconds']:.0f}s")
    for name, n in meta["rows"].items():
        path = table_path(PROCESSED_DIR, name, meta["tier"])
        print(f"  {name:7} {n:>10,} rows  {path.stat().st_size / 1e6:6.1f} MB  {path.name}")
    if meta["skipped"]:
        print(f"  skipped {len(meta['skipped'])} games missing from the cache")
    print("\n  cross-check vs Riot's end-of-game summary")
    for label, s in meta["cross_check_vs_riot_summary"].items():
        note = f"  ({NOTES[label]})" if label in NOTES else ""
        print(f"    {label:22} {s['pct_ok']!s:>6}% ok{note}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build Phase 3 tables for a tier")
    ap.add_argument("--tier", default="emerald", help="emerald, diamond, or all")
    ap.add_argument("--limit", type=int, help="only the first N games (trial run)")
    ap.add_argument("--batch-size", type=int, default=200)
    args = ap.parse_args()

    try:
        import pyarrow  # noqa: F401
    except ImportError:
        sys.exit("pyarrow isn't installed. Run: pip install -e .")

    tiers = ["emerald", "diamond"] if args.tier.lower() == "all" else [args.tier.lower()]
    cache = JsonCache(RAW_DIR)
    for tier in tiers:
        try:
            meta = build_tier(tier, cache, PROCESSED_DIR, limit=args.limit, batch_size=args.batch_size)
        except FileNotFoundError as exc:
            sys.exit(str(exc))
        report(meta)


if __name__ == "__main__":
    main()
