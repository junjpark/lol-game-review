"""Phase 0.3 spike — pull ONE match and look at it.

Throwaway. The point is to see the shape of the data before designing the real
fetcher in Phase 1. Delete this once you've written your extraction contract.

Usage:
    python scripts/spike_single_match.py "YourName#NA1"
"""

from __future__ import annotations

import json
import sys
from urllib.parse import quote

import requests

from lolreview.config import RAW_DIR, ensure_dirs, load_config
from lolreview.riot.routing import regional_host


def get(url: str, key: str, **params):
    r = requests.get(url, headers={"X-Riot-Token": key}, params=params or None, timeout=15)
    if r.status_code == 403:
        raise SystemExit(
            "403 Forbidden — your key is almost certainly expired.\n"
            "Personal keys last 24h. Grab a fresh one at developer.riotgames.com "
            "and paste it into .env."
        )
    if r.status_code == 404:
        raise SystemExit(f"404 Not Found — check the Riot ID / match ID.\n  {url}")
    if r.status_code == 429:
        raise SystemExit("429 Rate limited — wait 2 minutes and retry.")
    r.raise_for_status()
    return r.json()


def main() -> None:
    if len(sys.argv) < 2 or "#" not in sys.argv[1]:
        raise SystemExit('Usage: python scripts/spike_single_match.py "YourName#NA1"')

    game_name, tag = sys.argv[1].split("#", 1)
    cfg = load_config()
    host = regional_host(cfg.platform)
    ensure_dirs()

    print(f"{cfg.redacted()}\n")

    account = get(
        f"{host}/riot/account/v1/accounts/by-riot-id/{quote(game_name)}/{quote(tag)}",
        cfg.api_key,
    )
    puuid = account["puuid"]
    print(f"puuid: ...{puuid[-8:]}")

    match_ids = get(
        f"{host}/lol/match/v5/matches/by-puuid/{puuid}/ids",
        cfg.api_key,
        queue=cfg.queue,
        count=5,
    )
    if not match_ids:
        raise SystemExit(f"No recent queue-{cfg.queue} matches found for that account.")

    mid = match_ids[0]
    print(f"match: {mid}\n")

    match = get(f"{host}/lol/match/v5/matches/{mid}", cfg.api_key)
    timeline = get(f"{host}/lol/match/v5/matches/{mid}/timeline", cfg.api_key)

    (RAW_DIR / f"{mid}_match.json").write_text(json.dumps(match, indent=2))
    (RAW_DIR / f"{mid}_timeline.json").write_text(json.dumps(timeline, indent=2))
    print(f"wrote both files to {RAW_DIR}\n")

    # --- a map of where to look, not the answers -------------------------
    frames = timeline["info"]["frames"]
    p1 = frames[1]["participantFrames"]
    any_pf = next(iter(p1.values()))
    event_types = sorted({e["type"] for f in frames for e in f["events"]})

    print(f"frames: {len(frames)}")
    print(f"participantFrames keys are {type(next(iter(p1))).__name__}: {list(p1)[:3]}...")
    print(f"\nfields on a participantFrame:\n  {sorted(any_pf)}")
    print(f"\nevent types present:\n  {event_types}")
    print(f"\nfields on a match participant:\n  {sorted(match['info']['participants'][0])}")
    print("\nNow open the JSON files and answer the extraction-contract questions.")


if __name__ == "__main__":
    main()
