"""Freeze a game as a golden review fixture (tests/golden/).

    python scripts/make_golden.py NA1_5664339718 --player Janna
    python scripts/make_golden.py TW2_459670149 --player Aatrox --note "owner validation game 1"
    UPDATE_GOLDEN=1 pytest tests/test_golden.py      # then write/refresh its expected output

The game must be in the local cache (run moments.py or replay.py on it first). Player identities
are removed before anything is written. The win-probability curve is computed once with the
current model and frozen in the fixture, so later retrains don't change the golden test.
Pick games whose review the owner has checked, or that exercise something new (a death with a
cost, a multi-part minute, a moment the player wasn't in).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from golden_fixtures import SWING_TABLE, write_fixture  # noqa: E402

from lolreview.config import MODEL_DIR, RAW_DIR  # noqa: E402
from lolreview.extract import extract_game  # noqa: E402
from lolreview.model.predict import WPA_FILE, WinProb  # noqa: E402
from lolreview.riot.cache import JsonCache  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("match_id")
    ap.add_argument("--player", required=True, help="champion name or participant id (1-10)")
    ap.add_argument("--note", default="", help="why this game is a fixture")
    ap.add_argument("--freeze-swing-table", action="store_true",
                    help="(re)copy models/wpa_distribution.json to tests/golden/ — changes every expected output")
    args = ap.parse_args()

    cache = JsonCache(RAW_DIR)
    match, timeline = cache.get("matches", args.match_id), cache.get("timelines", args.match_id)
    if match is None or timeline is None:
        sys.exit(f"{args.match_id} isn't cached. Run: python scripts/replay.py {args.match_id}")
    parts = match["info"]["participants"]
    pick = [p for p in parts if str(p["participantId"]) == args.player
            or p.get("championName", "").lower() == args.player.lower()]
    if len(pick) != 1:
        sys.exit("Which player? " + ", ".join(f"{p['participantId']}={p.get('championName')}" for p in parts))

    curve = WinProb.load().for_game(extract_game(match, timeline, "emerald"))
    path = write_fixture(match, timeline, pick[0]["participantId"], curve, note=args.note)
    print(f"fixture -> {path} ({path.stat().st_size / 1024:.0f} KB)")
    if args.freeze_swing_table or not SWING_TABLE.exists():
        SWING_TABLE.write_text((MODEL_DIR / WPA_FILE).read_text())
        print(f"swing table -> {SWING_TABLE}")
    print("Now: UPDATE_GOLDEN=1 pytest tests/test_golden.py, and read the new expected file.")


if __name__ == "__main__":
    main()
