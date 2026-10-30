"""Phase 4 — v0 percentile report for one of your games. No model involved.

    python scripts/report.py --riot-id "Name#TAG"               # your latest ranked game
    python scripts/report.py --riot-id "Name#TAG" --back 2      # three games ago
    python scripts/report.py NA1_5660427960 --player Viktor      # any game, any player
    python scripts/report.py --riot-id "Name#TAG" --compare none # Emerald only

Shows your stats at 10 / 15 / 20 minutes and game end, placed against Emerald players in
the same role at the same minute, with Diamond typical values alongside.

This is the plan's checkpoint (4.3.2): run it on 3–5 of your own games and judge honestly
whether it tells you anything you didn't already know.
"""

from __future__ import annotations

import argparse
import sys

from lolreview.baselines import load
from lolreview.baselines.report import render
from lolreview.config import RAW_DIR
from lolreview.extract.frames import extract_frames
from lolreview.riot.cache import JsonCache
from lolreview.riot.client import ExpiredKey
from lolreview.riot.games import GameNotFound, describe_player, find_game, pick_player


def main() -> None:
    ap = argparse.ArgumentParser(description="v0 percentile report for one game")
    ap.add_argument("match_id", nargs="?")
    ap.add_argument("--riot-id", help='e.g. "Name#NA1" — uses your latest ranked game')
    ap.add_argument("--back", type=int, default=0,
                    help="with --riot-id: 0 = latest game, 1 = the one before, ...")
    ap.add_argument("--player", help="champion or Riot name (default: the --riot-id player)")
    ap.add_argument("--tier", default="emerald", help="baseline to compare against")
    ap.add_argument("--compare", default="diamond", help="second tier for typical values, or 'none'")
    args = ap.parse_args()
    if not args.match_id and not args.riot_id:
        ap.error("give a match ID or --riot-id")

    try:
        primary = load(args.tier)
        compare = None if args.compare.lower() == "none" else load(args.compare)
    except FileNotFoundError as exc:
        sys.exit(str(exc))
    try:
        game = find_game(JsonCache(RAW_DIR), match_id=args.match_id, riot_id=args.riot_id, back=args.back)
    except (GameNotFound, ExpiredKey) as exc:
        sys.exit(str(exc))

    me = pick_player(game.match, game.puuid, args.player)
    if me is None:
        listing = "\n".join(f"  {describe_player(p)}" for p in game.match["info"]["participants"])
        sys.exit(f"Which player? Pass --player <champion or name>. Players:\n{listing}")
    role = me.get("teamPosition")
    if not role:
        sys.exit("Riot's data has no role for this player in this game, so there's no baseline to compare with.")

    info = game.match["info"]
    rows = [r for r in extract_frames(game.match, game.timeline, args.tier)
            if r["participant_id"] == me["participantId"]]
    patch = ".".join(info["gameVersion"].split(".")[:2])
    mins, secs = divmod(info["gameDuration"], 60)
    header = [
        f"{game.match_id} · patch {patch} · {mins}:{secs:02d} · {'WIN' if me.get('win') else 'LOSS'}",
        f"You: {describe_player(me)}",
    ]
    patches = primary.meta.get("patches", [])
    if patches and patch not in patches:
        header.append(f"Note: baselines are from patches {', '.join(patches)}; this game is {patch}. "
                      "Expect some drift.")
    if any(p.get("wasAfk") for p in info["participants"]):
        header.append("Note: someone was AFK this game, so it isn't a normal game to compare.")
    header.append(f"Baselines compare against your role only, not your champion "
                  f"({primary.meta.get('games', '?'):,} {args.tier.capitalize()} games).")

    print()
    print(render(rows, role, primary, compare, header))
    print()


if __name__ == "__main__":
    main()
