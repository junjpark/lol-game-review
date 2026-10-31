"""Phase 2 — open a match as an interactive HTML replay.

Three ways to pick the match:

    python scripts/replay.py NA1_5628109779            # a specific match
    python scripts/replay.py --random --tier emerald     # random game from a tier's corpus
    python scripts/replay.py --riot-id "Name#TAG"        # your most recent ranked game

Cached matches open instantly and need no API key. Anything not cached is fetched (and
then cached), which does need a current key in .env.

Output goes to data/processed/replays/<matchId>.html and opens in your browser.
Once scripts/train_winprob.py has run, the chart also draws P(Blue wins) per minute
(green) and the clock shows it; --tier says which corpus the game counts as (default emerald).
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import webbrowser
from pathlib import Path

from lolreview.config import MODEL_DIR, PROCESSED_DIR, RAW_DIR, ensure_dirs
from lolreview.model.predict import MODEL_FILE
from lolreview.riot.cache import JsonCache
from lolreview.riot.client import ExpiredKey
from lolreview.riot.ddragon import item_names
from lolreview.riot.games import GameNotFound, find_game
from lolreview.viz.replay import build_replay_data, fetch_map_image, render_html

log = logging.getLogger("replay")


def win_probability(match: dict, timeline: dict, args) -> list[float] | None:
    """P(Blue wins) per frame from the Phase 5 model, or None if it isn't trained yet."""
    if args.no_winprob or not (MODEL_DIR / MODEL_FILE).exists():
        return None
    try:
        from lolreview.extract import extract_game
        from lolreview.model.predict import WinProb

        curve = WinProb.load().for_game(extract_game(match, timeline, args.tier.lower()))
    except Exception as exc:  # the replay should still render without the overlay
        log.warning("win probability skipped: %s", exc)
        return None
    by_minute = dict(zip(curve["minute"], curve["p_blue"]))
    return [float(by_minute[i]) if i in by_minute else None
            for i in range(len(timeline["info"]["frames"]))]


def main() -> None:
    ap = argparse.ArgumentParser(description="Render a match as an HTML replay")
    ap.add_argument("match_id", nargs="?")
    ap.add_argument("--random", action="store_true", help="pick a random match from a tier's corpus")
    ap.add_argument("--tier", default="emerald")
    ap.add_argument("--riot-id", help='your latest ranked game, e.g. "Name#NA1"')
    ap.add_argument("--no-open", action="store_true", help="write the file but don't open it")
    ap.add_argument("--no-map", action="store_true", help="skip the map image (schematic only)")
    ap.add_argument("--offline", action="store_true",
                    help="no Data Dragon downloads: schematic map, item IDs instead of names "
                         "(anything already cached is still used)")
    ap.add_argument("--no-winprob", action="store_true",
                    help="don't draw the win-probability curve even if a model is trained")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    ensure_dirs()
    cache = JsonCache(RAW_DIR)

    mid = args.match_id
    if args.random:
        manifest = PROCESSED_DIR / f"matches_{args.tier.lower()}.json"
        if not manifest.exists():
            sys.exit(f"No corpus for tier {args.tier} ({manifest} missing)")
        mid = random.choice(json.loads(manifest.read_text())["kept"])
    elif not mid and not args.riot_id:
        ap.error("give a match ID, --random, or --riot-id")

    try:
        game = find_game(cache, match_id=mid, riot_id=args.riot_id)
    except (GameNotFound, ExpiredKey) as exc:
        sys.exit(str(exc))
    mid, match, timeline = game.match_id, game.match, game.timeline

    ddragon_dir = RAW_DIR / "ddragon"
    patch = ".".join(match["info"].get("gameVersion", "").split(".")[:2])
    if args.offline:
        cached = sorted(ddragon_dir.glob(f"items_{patch}.*.json"))
        items = item_names(patch, ddragon_dir) if cached else {}
    else:
        items = item_names(patch, ddragon_dir)
    if items:
        log.info("item names: patch %s (%d items)", patch, len(items))

    data = build_replay_data(match, timeline, winprob=win_probability(match, timeline, args),
                             item_names=items)
    want_map = not (args.no_map or (args.offline and not (ddragon_dir / "map11.png").exists()))
    map_png = fetch_map_image(ddragon_dir) if want_map else None

    out = PROCESSED_DIR / "replays" / f"{mid}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(data, map_png), encoding="utf-8")
    log.info("wrote %s (%.0f KB)", out, out.stat().st_size / 1e3)

    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()
