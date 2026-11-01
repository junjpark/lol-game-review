"""Phase 1.3 — pull the corpus.

Walks the summoner list, fetches a few recent ranked games each, and caches match +
timeline to disk.

Each tier keeps its own match list in data/processed/matches_<tier>.json:
  * "seen" — every match ID encountered for this tier, kept or not
  * "kept" — the matches that passed the filters and have a timeline. This is the corpus
    definition later phases read.
The raw cache is shared across tiers, so a match already downloaded for one tier costs no
request when it turns up in another — but it only counts toward a tier's target if it was
found through that tier's players.

Resuming is cheap and safe:
  * A tier's matches are never re-downloaded, but they ARE re-checked against the current
    filters on every run. Changing TARGET_PATCHES and re-running re-sorts what is already
    on disk instead of ignoring it.
  * Summoners already processed are recorded in progress_<tier>.json and skipped.

Filters, per the plan:
  * queue 420 only (enforced at request time)
  * TARGET_PATCHES, if set in .env — note these are gameVersion numbers (16.x),
    NOT Riot's public year-based patch names (26.x)
  * remakes and early surrenders dropped
  * duplicates dropped — two sampled players often share a game

For long runs on a laptop, stop it sleeping:  caffeinate -i python scripts/collect_matches.py ...

Usage:
    python scripts/collect_matches.py --tier emerald --per-summoner 5 --target 5000

    # held-out test set: newest patch, fresh players, no match shared with the corpus
    python scripts/collect_matches.py --name emerald_holdout --patches 16.21 \\
        --exclude emerald,diamond --since 2026-10-21 --target 6000
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path

from lolreview.config import PROCESSED_DIR, RAW_DIR, ensure_dirs, load_config
from lolreview.riot.cache import JsonCache
from lolreview.riot.client import ExpiredKey, RiotClient
from lolreview.riot.routing import platform_of_match_id

log = logging.getLogger("collect_matches")

MIN_DURATION_S = 15 * 60  # shorter than this is a remake or an early surrender

# If this many matches have been checked and none were kept, the filters are almost
# certainly misconfigured. Stop instead of burning hours of rate limit.
SANITY_CHECK_AFTER = 100


def patch_of(match: dict) -> str:
    """Major.minor from gameVersion, e.g. '16.16.804.9184' -> '16.16'."""
    return ".".join(match["info"].get("gameVersion", "").split(".")[:2])


def is_usable(match: dict, target_patches: tuple[str, ...]) -> tuple[bool, str]:
    info = match.get("info", {})

    if info.get("queueId") != 420:
        return False, "wrong_queue"

    # gameDuration is in SECONDS (timeline timestamps are ms — different units)
    if info.get("gameDuration", 0) < MIN_DURATION_S:
        return False, "too_short"

    parts = info.get("participants", [])
    if any(p.get("gameEndedInEarlySurrender") for p in parts):
        return False, "early_surrender"

    if target_patches and patch_of(match) not in target_patches:
        return False, "wrong_patch"

    return True, "ok"


def sanity_check(
    kept: set[str], rejected: Counter, patches_seen: Counter, target_patches: tuple[str, ...]
) -> None:
    """Abort with a useful message if nothing is passing the filters."""
    if kept or sum(rejected.values()) < SANITY_CHECK_AFTER:
        return
    top = ", ".join(f"{p} ({n})" for p, n in patches_seen.most_common(6))
    log.error("")
    log.error("Checked %d matches and kept none. Rejections: %s", sum(rejected.values()), dict(rejected))
    if rejected.get("wrong_patch"):
        log.error("TARGET_PATCHES is %s", ",".join(target_patches) or "(unset)")
        log.error("Patches actually seen: %s", top)
        log.error("gameVersion uses 16.x numbering, not Riot's public 26.x names.")
    log.error("Fix .env and re-run — everything downloaded so far is cached and will be re-checked.")
    sys.exit(2)


def resolve_start_time(since: str | None, earliest_target_ms: int | None) -> int | None:
    """Epoch seconds to pass as startTime, or None for no time filter.

    `--since YYYY-MM-DD` wins if given. Otherwise use the earliest cached game on a target
    patch, minus a day of slack. The patch filter stays authoritative either way — this
    only avoids downloading games that are obviously too old to pass it.
    """
    if since:
        day = dt.datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
        return int(day.timestamp())
    if earliest_target_ms is None:
        return None
    return earliest_target_ms // 1000 - 86_400


def load_done(path: Path) -> set[str]:
    return set(json.loads(path.read_text())) if path.exists() else set()


def save_done(path: Path, done: set[str]) -> None:
    path.write_text(json.dumps(sorted(done)))


def load_seen(path: Path) -> set[str]:
    """Match IDs previously encountered for this tier."""
    return set(json.loads(path.read_text())["seen"]) if path.exists() else set()


def save_manifest(
    path: Path, seen: set[str], kept: set[str], target_patches: tuple[str, ...]
) -> None:
    path.write_text(
        json.dumps(
            {
                "target_patches": list(target_patches),
                "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "kept": sorted(kept),
                "seen": sorted(seen),
            }
        )
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", default="emerald")
    ap.add_argument("--per-summoner", type=int, default=5)
    ap.add_argument("--target", type=int, default=5000, help="usable matches wanted")
    ap.add_argument(
        "--since",
        help="only request games on/after this date, YYYY-MM-DD (UTC). "
        "Default: derived from this tier's cached matches on TARGET_PATCHES",
    )
    ap.add_argument("--name", help="corpus label for summoners_/progress_/matches_<name>.json "
                    "(default: the tier, e.g. --name emerald_holdout)")
    ap.add_argument("--patches", help="comma-separated gameVersion patches (16.x) for this run; "
                    "overrides TARGET_PATCHES in .env")
    ap.add_argument("--exclude", default="",
                    help="comma-separated corpus names whose matches never count here, "
                         "e.g. emerald,diamond for a held-out test set")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    tier = (args.name or args.tier).lower()

    cfg = load_config()
    if args.patches:
        cfg = replace(cfg, target_patches=tuple(p.strip() for p in args.patches.split(",") if p.strip()))
    excluded: set[str] = set()
    for other in filter(None, (x.strip().lower() for x in args.exclude.split(","))):
        path = PROCESSED_DIR / f"matches_{other}.json"
        if not path.exists():
            sys.exit(f"--exclude {other}: {path} not found")
        m = json.loads(path.read_text())
        excluded.update(m.get("seen", [])), excluded.update(m.get("kept", []))
    if excluded:
        log.info("excluding %d matches already seen by: %s", len(excluded), args.exclude)
    ensure_dirs()
    cache = JsonCache(RAW_DIR)
    client = RiotClient(cfg, cache)

    summoner_file = PROCESSED_DIR / f"summoners_{tier}.json"
    if not summoner_file.exists():
        sys.exit(
            f"No summoner list at {summoner_file}. "
            f"Run: python scripts/collect_summoners.py --tier {tier.upper()}"
        )
    summoners = json.loads(summoner_file.read_text())
    progress_file = PROCESSED_DIR / f"progress_{tier}.json"
    manifest_file = PROCESSED_DIR / f"matches_{tier}.json"
    done = load_done(progress_file)
    seen = load_seen(manifest_file)

    # Baselines must be single-region (CLAUDE.md): never let two servers share a corpus.
    servers = {platform_of_match_id(m) for m in seen}
    sampled_on = {s["platform"] for s in summoners if s.get("platform")}
    if (servers and servers != {cfg.platform}) or (sampled_on and sampled_on != {cfg.platform}):
        sys.exit(
            f"RIOT_PLATFORM is {cfg.platform}, but corpus '{tier}' holds games from "
            f"{', '.join(sorted(servers | sampled_on))}. Each server needs its own corpus name, e.g.\n"
            f"  python scripts/collect_summoners.py --tier EMERALD --name emerald_{cfg.platform} ...\n"
            f"  python scripts/collect_matches.py --name emerald_{cfg.platform} ..."
        )

    log.info("TARGET_PATCHES: %s", ",".join(cfg.target_patches) or "(unset — all patches)")
    log.info("loaded %d summoners, %d already processed", len(summoners), len(done))

    kept: set[str] = set()
    rejected: Counter[str] = Counter()
    patches_seen: Counter[str] = Counter()
    earliest_target_ms: int | None = None

    def consider(mid: str, match: dict) -> None:
        """Apply filters to a match; fetch its timeline if it passes."""
        nonlocal earliest_target_ms
        patch = patch_of(match)
        patches_seen[patch] += 1
        if patch in cfg.target_patches:
            created = match["info"].get("gameCreation")
            if created and (earliest_target_ms is None or created < earliest_target_ms):
                earliest_target_ms = created
        ok, reason = is_usable(match, cfg.target_patches)
        if not ok:
            rejected[reason] += 1
            return
        if client.timeline(mid) is not None:  # cache hit if already on disk
            kept.add(mid)
        else:
            rejected["no_timeline"] += 1

    def checkpoint() -> None:
        save_done(progress_file, done)
        save_manifest(manifest_file, seen, kept, cfg.target_patches)

    try:
        # ---- 1. re-check this tier's cached matches against current filters --------
        mine = sorted(mid for mid in seen - excluded if cache.has("matches", mid))
        if mine:
            log.info("re-checking %d cached %s matches against current filters", len(mine), tier)
        else:
            shared = len(cache.keys("matches"))
            log.info(
                "no cached %s matches yet (%d in the shared cache from other tiers will be "
                "reused if this tier's players turn up in them)", tier, shared,
            )
        for i, mid in enumerate(mine, 1):
            if len(kept) >= args.target:
                break
            match = cache.get("matches", mid)
            if match is None:  # corrupted file was dropped by the cache
                seen.discard(mid)
                continue
            consider(mid, match)
            if i % 250 == 0:
                log.info("  re-checked %d/%d  kept=%d  [%s]", i, len(mine), len(kept), client.report())
        if mine:
            log.info("from cache: kept=%d rejected=%s", len(kept), dict(rejected))
        sanity_check(kept, rejected, patches_seen, cfg.target_patches)

        start_time = resolve_start_time(args.since, earliest_target_ms)
        if start_time is not None:
            when = dt.datetime.fromtimestamp(start_time, dt.timezone.utc)
            log.info("only requesting games since %s UTC", f"{when:%Y-%m-%d %H:%M}")
        else:
            log.info("no time filter — every recent game is downloaded, then filtered")

        # ---- 2. pull new matches from summoners not yet processed ------------------
        kept_from_cache = len(kept)
        pull_started = time.time()
        for n, s in enumerate(summoners, 1):
            if len(kept) >= args.target:
                log.info("hit target of %d matches", args.target)
                break
            if s["puuid"] in done:
                continue

            for mid in client.match_ids(
                s["puuid"], count=args.per_summoner, start_time=start_time
            ):
                if mid in seen:
                    continue
                if mid in excluded:
                    rejected["in_other_corpus"] += 1
                    continue
                seen.add(mid)
                match = client.match(mid)  # cache hit if another tier already has it
                if match is None:
                    rejected["not_found"] += 1
                    continue
                consider(mid, match)

            done.add(s["puuid"])

            if n % 25 == 0:
                checkpoint()
                new = len(kept) - kept_from_cache
                hours = max(time.time() - pull_started, 1) / 3600
                log.info(
                    "summoner %d/%d  kept=%d (+%d this run, ~%.0f/hr)  rejected=%d  [%s]",
                    n, len(summoners), len(kept), new, new / hours,
                    sum(rejected.values()), client.report(),
                )
                sanity_check(kept, rejected, patches_seen, cfg.target_patches)

    except ExpiredKey as exc:
        log.error("%s", exc)
        log.info("progress is saved — update .env and re-run to continue")
        checkpoint()
        sys.exit(1)
    except KeyboardInterrupt:
        log.warning("interrupted — progress is saved, safe to re-run")

    checkpoint()

    mb = (cache.size_bytes("matches") + cache.size_bytes("timelines")) / 1e6
    log.info("")
    log.info("kept %d %s matches -> %s", len(kept), tier, manifest_file)
    log.info("shared cache: %.0f MB on disk", mb)
    log.info("rejected: %s", dict(rejected))
    log.info("patches seen: %s", dict(patches_seen.most_common()))
    log.info("%s", client.report())
    if len(kept) < args.target:
        log.info(
            "short of target by %d — collect more summoners and re-run "
            "(more players beats more games per player)",
            args.target - len(kept),
        )

    stats_path = PROCESSED_DIR / f"collection_stats_{tier}.json"
    stats_path.write_text(
        json.dumps(
            {
                "tier": tier,
                "target_patches": list(cfg.target_patches),
                "kept": len(kept),
                "rejected": dict(rejected),
                "patches_seen": dict(patches_seen),
                "summoners_processed": len(done),
                "shared_cache_mb": round(mb, 1),
                "client": client.stats,
            },
            indent=2,
        )
    )
    log.info("stats -> %s", stats_path)


if __name__ == "__main__":
    main()
