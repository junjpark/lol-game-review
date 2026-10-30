"""Phase 3 hand check — print the numbers to verify for one of your games.

    python scripts/inspect_game.py --riot-id "Name#TAG"              # your latest ranked game
    python scripts/inspect_game.py NA1_5660427960 --player Viktor     # any game, any player

Compare section 1 against the League client's post-game Stats tab (or op.gg), and use
your memory of the game for sections 2-4. Extraction runs on the spot, so this works for
games outside the corpus too.
"""

from __future__ import annotations

import argparse
import sys

from lolreview.config import RAW_DIR
from lolreview.extract import extract_game
from lolreview.extract.events import category
from lolreview.extract.validate import PLAYER_CHECKS
from lolreview.riot.cache import JsonCache
from lolreview.riot.client import ExpiredKey
from lolreview.riot.games import GameNotFound, describe_player, find_game, pick_player
from lolreview.viz.replay import describe

TEAM = {100: "Blue", 200: "Red"}
who = describe_player


def mmss(ms: int) -> str:
    s = round(ms / 1000)
    return f"{s // 60}:{s % 60:02d}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Print hand-check numbers for one game")
    ap.add_argument("match_id", nargs="?")
    ap.add_argument("--riot-id", help='e.g. "Name#NA1" — uses your latest ranked game')
    ap.add_argument("--player", help="champion or Riot name to inspect (default: the --riot-id player)")
    args = ap.parse_args()
    if not args.match_id and not args.riot_id:
        ap.error("give a match ID or --riot-id")

    try:
        game = find_game(JsonCache(RAW_DIR), match_id=args.match_id, riot_id=args.riot_id)
    except (GameNotFound, ExpiredKey) as exc:
        sys.exit(str(exc))

    info = game.match["info"]
    parts = {p["participantId"]: p for p in info["participants"]}
    me = pick_player(game.match, game.puuid, args.player)
    if me is None:
        listing = "\n".join(f"  {who(p)}" for p in parts.values())
        sys.exit(f"Which player? Pass --player <champion or name>. Players:\n{listing}")
    pid = me["participantId"]
    tables = extract_game(game.match, game.timeline, tier="adhoc")
    frames = tables["frames"]
    mine = {r["minute"]: r for r in frames if r["participant_id"] == pid}
    last = max(mine)
    winner = next((TEAM[t["teamId"]] for t in info["teams"] if t.get("win")), "?")

    print(f"\n{game.match_id} · patch {'.'.join(info['gameVersion'].split('.')[:2])} · "
          f"{mmss(info['gameDuration'] * 1000)} · {winner} won")
    print(f"Inspecting: {who(me)}  ({'WIN' if me.get('win') else 'LOSS'})")

    # 1 — totals: our timeline-derived number vs Riot's summary; compare both to post-game
    print("\n1. End-of-game totals — compare with the post-game Stats tab / op.gg")
    print(f"   {'':20} {'extracted':>10} {'Riot summary':>13}")
    for label, col, fields, tol in PLAYER_CHECKS:
        if all(f in me for f in fields):
            ours, theirs = mine[last][col], sum(me[f] for f in fields)
            flag = "" if ours is not None and abs(ours - theirs) <= tol else "   <-- differs"
            shown = f"{ours:,}" if ours is not None else "—"
            print(f"   {label:20} {shown:>10} {theirs:>13,}{flag}")

    # 2 — lane opponent
    o = mine[last]["opp_id"]
    print("\n2. Lane opponent — is this who you laned against?")
    print(f"   {who(parts[o]) if o else 'none (your role is missing in Riot data)'}")

    # 3 — lane diffs (positive = you ahead)
    print("\n3. Versus your lane opponent (positive = you ahead) — does the sign match how lane felt?")
    print(f"   {'minute':>6} {'gold':>7} {'CS':>5} {'XP':>6} {'level':>6}")
    for m in (5, 10, 15, 20):
        r = mine.get(m)
        if r and r["gold_diff"] is not None:
            print(f"   {m:>6} {r['gold_diff']:>+7,} {r['cs_diff']:>+5} {r['xp_diff']:>+6,} {r['level_diff']:>+6}")

    # 4 — moments: your kills/deaths/assists plus every objective and structure
    print("\n4. Moments you might remember")
    players = {p: {"champion": q["championName"], "team": q["teamId"]} for p, q in parts.items()}
    for fr in game.timeline["info"]["frames"]:
        for e in fr.get("events", []):
            if e["type"] == "CHAMPION_KILL":
                involved = (e.get("killerId"), e.get("victimId"), *(e.get("assistingParticipantIds") or []))
                if pid not in involved:
                    continue
                tag = "you died" if e.get("victimId") == pid else "your kill" if e.get("killerId") == pid else "assist"
            elif category(e) == "special" and e["type"] != "CHAMPION_SPECIAL_KILL":
                tag = ""
            else:
                continue
            label, _ = describe(e, players)
            print(f"   {mmss(e['timestamp']):>6}  {label}" + (f"   [{tag}]" if tag else ""))

    # 5 — AFK
    print("\n5. AFK flags (Riot's wasAfk; idle = longest run of identical minute snapshots)")
    for p in parts.values():
        r = next(x for x in frames if x["participant_id"] == p["participantId"] and x["minute"] == 0)
        mark = "  <-- AFK" if r["was_afk"] else ""
        print(f"   {who(p):60} idle {r['idle_max']} min{mark}")
    print()


if __name__ == "__main__":
    main()
