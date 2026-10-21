"""Data Dragon: Riot's static game data (item names, map image).

Data Dragon assets are explicitly permitted for use in developer products. Everything is
cached under data/raw/ddragon/ so each file is downloaded once. Lookups are versioned by
patch, because item IDs and names change between patches.

Everything here degrades gracefully: offline, these return empty results and callers fall
back to raw IDs or the schematic map.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

BASE = "https://ddragon.leagueoflegends.com"


def _get(url: str):
    import requests

    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    return resp


def version_for_patch(patch: str, versions: list[str]) -> str | None:
    """Data Dragon version for a game patch: '16.20' -> '16.20.1'. Newest if no match."""
    want = patch.split(".")[:2]
    for v in versions:  # Data Dragon lists newest first
        if v.split(".")[:2] == want:
            return v
    return versions[0] if versions else None


def parse_item_names(item_json: dict) -> dict[int, str]:
    """item.json -> {item_id: name}."""
    return {int(k): v.get("name", f"item {k}") for k, v in item_json.get("data", {}).items() if k.isdigit()}


def item_names(patch: str, cache_dir: Path) -> dict[int, str]:
    """Item ID -> name for the given patch, cached on disk. {} if unavailable."""
    cache_dir = Path(cache_dir)
    cached = sorted(cache_dir.glob(f"items_{patch}.*.json"))
    if cached:
        return parse_item_names(json.loads(cached[-1].read_text()))
    try:
        version = version_for_patch(patch, _get(f"{BASE}/api/versions.json").json())
        if version is None:
            return {}
        raw = _get(f"{BASE}/cdn/{version}/data/en_US/item.json").json()
    except Exception as exc:  # offline, DNS, etc.
        log.warning("couldn't fetch item names for patch %s (%s); showing item IDs", patch, exc)
        return {}
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"items_{version}.json").write_text(json.dumps(raw))
    return parse_item_names(raw)


def map_image(cache_dir: Path) -> bytes | None:
    """Summoner's Rift minimap (map11.png), cached. None if unavailable."""
    path = Path(cache_dir) / "map11.png"
    if path.exists():
        return path.read_bytes()
    try:
        version = _get(f"{BASE}/api/versions.json").json()[0]
        content = _get(f"{BASE}/cdn/{version}/img/map/map11.png").content
    except Exception as exc:
        log.warning("couldn't fetch map image (%s); using schematic map", exc)
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return content
