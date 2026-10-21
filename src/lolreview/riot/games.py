"""Find one game: from disk if we have it, from the API if we don't.

Shared by the replay and inspect scripts. Cached games need no API key; anything fetched
is cached for next time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..config import RAW_DIR, load_config
from .cache import JsonCache


class GameNotFound(RuntimeError):
    pass


@dataclass
class Game:
    match_id: str
    match: dict
    timeline: dict
    puuid: str | None = None  # the player a --riot-id lookup was for, if any


def load_from_disk(cache: JsonCache, match_id: str) -> tuple[dict, dict] | None:
    match, timeline = cache.get("matches", match_id), cache.get("timelines", match_id)
    if match and timeline:
        return match, timeline
    # Phase 0 spike files were saved as plain JSON next to the cache
    m, t = RAW_DIR / f"{match_id}_match.json", RAW_DIR / f"{match_id}_timeline.json"
    if m.exists() and t.exists():
        return json.loads(m.read_text()), json.loads(t.read_text())
    return None


def find_game(cache: JsonCache, match_id: str | None = None, riot_id: str | None = None) -> Game:
    """By match ID (disk first, then API), or a Riot ID's most recent ranked game (API)."""
    if match_id:
        found = load_from_disk(cache, match_id)
        if found:
            return Game(match_id, *found)
    if not match_id and not riot_id:
        raise GameNotFound("give a match ID or a Riot ID")

    from .client import RiotClient  # lazy: only needed when fetching

    client = RiotClient(load_config(), cache)
    puuid = None
    if riot_id:
        name, _, tag = riot_id.partition("#")
        if not tag:
            raise GameNotFound('Riot ID must look like "Name#TAG"')
        account = client.account_by_riot_id(name, tag)
        if not account:
            raise GameNotFound(f"No account found for {riot_id}")
        puuid = account["puuid"]
        if not match_id:
            ids = client.match_ids(puuid, count=1)
            if not ids:
                raise GameNotFound(f"No recent ranked solo/duo games for {riot_id}")
            match_id = ids[0]
            found = load_from_disk(cache, match_id)
            if found:
                return Game(match_id, *found, puuid=puuid)

    match, timeline = client.match(match_id), client.timeline(match_id)
    if not match or not timeline:
        raise GameNotFound(f"Match {match_id} not found")
    return Game(match_id, match, timeline, puuid)
