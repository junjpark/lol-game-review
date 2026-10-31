"""Phase 1.2 — build the sampling frame.

Builds a list of ranked players for one tier, stratified so that:

  * every division gets an equal share of the target (I, II, III, IV), and
  * within a division, only a handful of players are taken from each page, so the
    sample is drawn from many pages instead of the first one or two.

The guiding rule from the plan: many players, few games each. Per-player habits are
the bias being averaged out, so breadth of players matters more than depth.

Usage:
    python scripts/collect_summoners.py --tier EMERALD --target 1000
    python scripts/collect_summoners.py --tier DIAMOND --target 300

    # fresh players for the held-out test set (none shared with the training corpus)
    python scripts/collect_summoners.py --tier EMERALD --target 4000 --name emerald_holdout \\
        --exclude emerald,diamond --seed 1
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from collections import Counter
from typing import Callable

from lolreview.config import PROCESSED_DIR, RAW_DIR, ensure_dirs, load_config
from lolreview.riot.cache import JsonCache
from lolreview.riot.client import ExpiredKey, RiotClient

DIVISIONS = ["I", "II", "III", "IV"]

log = logging.getLogger("collect_summoners")


def split_quota(target: int, n: int) -> list[int]:
    """Split target into n near-equal integer shares. 1000/4 -> [250, 250, 250, 250]."""
    base, extra = divmod(target, n)
    return [base + (1 if i < extra else 0) for i in range(n)]


def sample_division(
    fetch_page: Callable[[int], list[dict]],
    *,
    tier: str,
    division: str,
    quota: int,
    per_page: int,
    max_page: int,
    rng: random.Random,
    seen: set[str],
) -> list[dict]:
    """Take up to `per_page` random players from each of many pages until `quota` is met.

    Pages are visited in random order. Entries without a PUUID are skipped — PUUID is
    the only identifier the rest of the pipeline uses.
    """
    pages = list(range(1, max_page + 1))
    rng.shuffle(pages)

    picked: list[dict] = []
    for page in pages:
        if len(picked) >= quota:
            break

        entries = fetch_page(page)
        candidates = [e for e in entries if e.get("puuid") and e["puuid"] not in seen]
        if not candidates:
            continue

        take = min(per_page, quota - len(picked), len(candidates))
        for e in rng.sample(candidates, take):
            seen.add(e["puuid"])
            picked.append(
                {
                    "puuid": e["puuid"],
                    "tier": tier,
                    "division": division,
                    "leaguePoints": e.get("leaguePoints"),
                    "page": page,
                }
            )
        log.info("%s %-3s page %-3d took %-3d (%d/%d)", tier, division, page, take, len(picked), quota)

    if len(picked) < quota:
        log.warning(
            "%s %s: only %d of %d — raise --max-page or lower --per-page",
            tier, division, len(picked), quota,
        )
    return picked


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", default="EMERALD")
    ap.add_argument("--target", type=int, default=1000, help="distinct players wanted")
    ap.add_argument("--per-page", type=int, default=20, help="max players taken per page")
    # Capacity = per_page x max_page x 4 divisions; pages are only fetched as needed, so a
    # high ceiling costs nothing. (At 40 it silently capped a 6,000 target at 3,200.)
    ap.add_argument("--max-page", type=int, default=150, help="highest page to sample from")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--name", help="output label: summoners_<name>.json (default: the tier, "
                    "e.g. --name emerald_holdout for the fresh test set)")
    ap.add_argument("--exclude", default="",
                    help="comma-separated summoner-list names whose players are skipped, "
                         "e.g. emerald,diamond so a holdout sample shares no players with the corpus")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    rng = random.Random(args.seed)
    tier = args.tier.upper()
    name = (args.name or tier).lower()

    cfg = load_config()
    ensure_dirs()
    client = RiotClient(cfg, JsonCache(RAW_DIR))

    seen: set[str] = set()   # PUUIDs never to pick: already picked, or excluded
    for other in filter(None, (x.strip().lower() for x in args.exclude.split(","))):
        path = PROCESSED_DIR / f"summoners_{other}.json"
        if not path.exists():
            sys.exit(f"--exclude {other}: {path} not found")
        before = len(seen)
        seen.update(r["puuid"] for r in json.loads(path.read_text()))
        log.info("excluding %d players from %s", len(seen) - before, path.name)
    rows: list[dict] = []
    try:
        for division, quota in zip(DIVISIONS, split_quota(args.target, len(DIVISIONS))):
            rows += sample_division(
                lambda p, d=division: client.league_entries(tier, d, p),
                tier=tier,
                division=division,
                quota=quota,
                per_page=args.per_page,
                max_page=args.max_page,
                rng=rng,
                seen=seen,
            )
    except ExpiredKey as exc:
        log.error("%s", exc)
        sys.exit(1)
    except KeyboardInterrupt:
        log.warning("interrupted — writing what we have")

    out = PROCESSED_DIR / f"summoners_{name}.json"
    out.write_text(json.dumps(rows, indent=2))
    if len(rows) < args.target:
        log.warning("only %d of the %d players requested — the ladder ran out of pages "
                    "(raise --max-page) or the division is small", len(rows), args.target)

    by_div = Counter(r["division"] for r in rows)
    pages_used = {d: len({r["page"] for r in rows if r["division"] == d}) for d in DIVISIONS}
    log.info("wrote %d players -> %s", len(rows), out)
    log.info("by division: %s", dict(sorted(by_div.items())))
    log.info("pages used per division: %s", pages_used)
    log.info("%s", client.report())


if __name__ == "__main__":
    main()
