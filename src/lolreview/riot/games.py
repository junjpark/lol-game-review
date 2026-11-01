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


def pick_player(match: dict, puuid: str | None = None, name: str | None = None) -> dict | None:
    """The participant a lookup was for: by PUUID (from --riot-id) or by champion / Riot
    name (case-insensitive, '#TAG' optional). None if no match."""
    parts = match["info"]["participants"]
    if puuid:
        for p in parts:
            if p.get("puuid") == puuid:
                return p
    if name:
        key = name.lower().split("#")[0]
        for p in parts:
            if key in ((p.get("championName") or "").lower(), (p.get("riotIdGameName") or "").lower()):
                return p
    return None


def describe_player(p: dict) -> str:
    teams = {100: "Blue", 200: "Red"}
    name, tag = p.get("riotIdGameName") or "", p.get("riotIdTagline")
    return (f"{p.get('championName')} ({teams.get(p.get('teamId'))} {p.get('teamPosition') or '?'})"
            + (f" — {name}#{tag}" if name and tag else ""))


def load_from_disk(cache: JsonCache, match_id: str) -> tuple[dict, dict] | None:
    match, timeline = cache.get("matches", match_id), cache.get("timelines", match_id)
    if match and timeline:
        return match, timeline
    # Phase 0 spike files were saved as plain JSON next to the cache
    m, t = RAW_DIR / f"{match_id}_match.json", RAW_DIR / f"{match_id}_timeline.json"
    if m.exists() and t.exists():
        return json.loads(m.read_text()), json.loads(t.read_text())
    return None


def find_game(
    cache: JsonCache, match_id: str | None = None, riot_id: str | None = None, back: int = 0,
    platform: str | None = None, queue: int | None | str = "config",
) -> Game:
    """By match ID (disk first, then API), or a Riot ID's recent ranked game (API):
    back=0 is the latest, back=1 the one before, and so on.

    `platform` (e.g. "tw2") picks the server whose match history a Riot ID is searched on;
    default RIOT_PLATFORM from .env. A match ID always routes by its own prefix.
    `queue` filters a Riot ID's history: default RIOT_QUEUE (ranked solo/duo), an ID such as
    400 (draft), or None for the most recent game of any kind."""
    if match_id:
        found = load_from_disk(cache, match_id)
        if found:
            return Game(match_id, *found)
    if not match_id and not riot_id:
        raise GameNotFound("give a match ID or a Riot ID")

    from .client import RiotClient  # lazy: only needed when fetching

    cfg = load_config()
    if platform:
        from dataclasses import replace

        from .routing import region_for

        region_for(platform)  # raises a clear error for an unknown server code
        cfg = replace(cfg, platform=platform.lower())
    client = RiotClient(cfg, cache)
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
            from .queues import queue_name

            ids = client.match_ids(puuid, count=1, start=back, queue=queue)
            if not ids:
                q = cfg.queue if queue == "config" else queue
                raise GameNotFound(
                    f"No {queue_name(q)} game #{back + 1} back for {riot_id} on {cfg.platform}. "
                    "Wrong server? Try --platform (e.g. tw2). Different mode? Try --queue any.")
            match_id = ids[0]
            found = load_from_disk(cache, match_id)
            if found:
                return Game(match_id, *found, puuid=puuid)

    match, timeline = client.match(match_id), client.timeline(match_id)
    if not match or not timeline:
        raise GameNotFound(f"Match {match_id} not found")
    return Game(match_id, match, timeline, puuid)
