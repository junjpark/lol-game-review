"""Phase 6 — the moments worth reviewing in one game (docs/phase6-spec.md).

    python scripts/moments.py --riot-id "Name#TAG"                    # latest ranked NA game
    python scripts/moments.py --riot-id "Name#TAG" --back 2
    python scripts/moments.py --riot-id "Name#TAG" --platform tw2     # Hong Kong (qualitative)
    python scripts/moments.py NA1_5664500846 --player Aphelios

Prints the ranked moments, writes the review as JSON (the Phase 9 coach's input) to
data/processed/moments/<matchId>_<champion>.json, and opens the replay with the moments marked
on the win-probability chart (click a band or a row to jump there).
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
import webbrowser

from lolreview.config import PROCESSED_DIR, RAW_DIR, ensure_dirs
from lolreview.extract import extract_game
from lolreview.model.predict import WinProb
from lolreview.review.moments import format_table, moments_markers, review_game
from lolreview.riot.cache import JsonCache
from lolreview.riot.client import ExpiredKey
from lolreview.riot.ddragon import item_names
from lolreview.riot.games import GameNotFound, describe_player, find_game, pick_player
from lolreview.riot.queues import RANKED_SOLO, parse_queue, queue_name
from lolreview.riot.routing import platform_of_match_id
from lolreview.viz.replay import build_replay_data, fetch_map_image, render_html


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 6: the moments worth reviewing in one game")
    ap.add_argument("match_id", nargs="?")
    ap.add_argument("--riot-id", help='e.g. "Name#NA1" (uses that player\'s latest ranked game)')
    ap.add_argument("--back", type=int, default=0, help="0 = latest, 1 = the one before, ...")
    ap.add_argument("--platform", help="server for --riot-id lookups, e.g. tw2 (default: .env)")
    ap.add_argument("--queue", help="ranked (default), draft, blind, flex, any, or a number")
    ap.add_argument("--player", help="champion or Riot name (default: the --riot-id player)")
    ap.add_argument("--tier", default="emerald", help="which corpus the game counts as (model input)")
    ap.add_argument("--no-replay", action="store_true", help="skip writing/opening the replay")
    ap.add_argument("--no-open", action="store_true", help="write the replay but don't open it")
    args = ap.parse_args()
    if not args.match_id and not args.riot_id:
        ap.error("give a match ID or --riot-id")

    ensure_dirs()
    cache = JsonCache(RAW_DIR)
    try:
        game = find_game(cache, match_id=args.match_id, riot_id=args.riot_id, back=args.back,
                         platform=args.platform, queue=parse_queue(args.queue, default="config"))
    except (GameNotFound, ExpiredKey, ValueError) as exc:
        sys.exit(str(exc))
    me = pick_player(game.match, game.puuid, args.player)
    if me is None:
        listing = "\n".join(f"  {describe_player(p)}" for p in game.match["info"]["participants"])
        sys.exit(f"Which player? Pass --player <champion or name>. Players:\n{listing}")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        wp = WinProb.load()
    extracted = extract_game(game.match, game.timeline, args.tier.lower())
    curve = wp.for_game(extracted)
    review = review_game(game.match, game.timeline, me["participantId"], extracted=extracted, curve=curve)

    # caveats the review must carry (spec §7 confidence notes)
    qid = game.match["info"].get("queueId")
    if qid != RANKED_SOLO:
        review.notes.append(f"{queue_name(qid)} game: the model is trained on ranked solo/duo only")
    server = platform_of_match_id(game.match_id)
    if server != "na1":
        review.notes.append(f"{server.upper()} game: the model and swing table are NA-only (qualitative here)")
    if not wp.released:
        review.notes.append(f"model {wp.version} hasn't passed its fresh-holdout release check yet")
    for w in caught:
        review.notes.append(str(w.message))

    print(f"\n{game.match_id} · {queue_name(qid)}\n")
    print(format_table(review))

    out_dir = PROCESSED_DIR / "moments"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{game.match_id}_{me.get('championName', me['participantId'])}.json"
    out.write_text(json.dumps(review.to_dict(), indent=2))
    print(f"\nreview -> {out}")

    if args.no_replay:
        return
    patch = ".".join(game.match["info"].get("gameVersion", "").split(".")[:2])
    items = item_names(patch, RAW_DIR / "ddragon")
    by_minute = dict(zip(curve["minute"], curve["p_blue"]))
    winprob = [float(by_minute[i]) if i in by_minute else None
               for i in range(len(game.timeline["info"]["frames"]))]
    note = (f"Your team's win probability swings for {me.get('championName')}. Green = good for your team, "
            "red = bad, grey = game swings you weren't part of. Click to jump there.")
    data = build_replay_data(game.match, game.timeline, winprob=winprob, item_names=items,
                             moments=moments_markers(review), moments_note=note)
    html_path = PROCESSED_DIR / "replays" / f"{game.match_id}_review.html"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(render_html(data, fetch_map_image(RAW_DIR / "ddragon")), encoding="utf-8")
    print(f"replay -> {html_path}")
    if not args.no_open:
        webbrowser.open(html_path.resolve().as_uri())


if __name__ == "__main__":
    main()
