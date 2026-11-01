"""Riot API routing values.

Riot uses two different addressing schemes and mixing them up produces 404s that
look mysterious:

* **Platform routing** (``na1``, ``euw1``, ``kr``, ...) — the per-server endpoints.
  Used by LEAGUE-V4, SUMMONER-V4, SPECTATOR, CHAMPION-MASTERY.
      https://na1.api.riotgames.com/lol/league/v4/entries/...

* **Regional routing** (``americas``, ``europe``, ``asia``, ``sea``) — macro clusters.
  Used by MATCH-V5 and ACCOUNT-V1.
      https://americas.api.riotgames.com/lol/match/v5/matches/...

A single collection run touches both: platform routing to enumerate summoners,
regional routing to fetch their matches.

Note: the platform -> cluster mapping has shifted over the years, particularly for
the OCE/SEA servers. Verify against current Riot docs before trusting anything
outside the major regions.
"""

from __future__ import annotations

PLATFORM_TO_REGION: dict[str, str] = {
    # Americas
    "na1": "americas",
    "br1": "americas",
    "la1": "americas",
    "la2": "americas",
    # Europe
    "euw1": "europe",
    "eun1": "europe",
    "tr1": "europe",
    "ru": "europe",
    "me1": "europe",
    # Asia
    "kr": "asia",
    "jp1": "asia",
    # SEA / OCE. ph2 and th2 merged into sg2 in January 2025 (patch 15.1); they stay here
    # so older match IDs still route. tw2 = Taiwan, Hong Kong and Macao.
    "oc1": "sea",
    "ph2": "sea",
    "sg2": "sea",
    "th2": "sea",
    "tw2": "sea",
    "vn2": "sea",
}

# ACCOUNT-V1 (Riot ID -> PUUID) is only served from these three clusters, but any of them
# can look up any account. SEA servers use ASIA, the nearest.
ACCOUNT_REGIONS = frozenset({"americas", "asia", "europe"})

VALID_PLATFORMS = frozenset(PLATFORM_TO_REGION)
VALID_REGIONS = frozenset(PLATFORM_TO_REGION.values())


def region_for(platform: str) -> str:
    """Return the regional routing cluster for a platform routing value.

    >>> region_for("na1")
    'americas'
    """
    key = platform.lower()
    try:
        return PLATFORM_TO_REGION[key]
    except KeyError:
        raise ValueError(
            f"Unknown platform routing value {platform!r}. "
            f"Expected one of: {', '.join(sorted(VALID_PLATFORMS))}"
        ) from None


def platform_host(platform: str) -> str:
    """Base URL for per-server endpoints (LEAGUE-V4 and friends)."""
    if platform.lower() not in VALID_PLATFORMS:
        raise ValueError(f"Unknown platform routing value {platform!r}")
    return f"https://{platform.lower()}.api.riotgames.com"


def regional_host(platform: str) -> str:
    """Base URL for cluster endpoints (MATCH-V5, ACCOUNT-V1), derived from platform."""
    return f"https://{region_for(platform)}.api.riotgames.com"


def account_host(platform: str) -> str:
    """Base URL for ACCOUNT-V1. Not every cluster serves it (SEA doesn't)."""
    region = region_for(platform)
    return f"https://{region if region in ACCOUNT_REGIONS else 'asia'}.api.riotgames.com"


def match_host(match_id: str, fallback_platform: str) -> str:
    """Base URL for a specific match: the cluster of the server it was played on, read
    from its ID prefix (TW2_... -> sea), so a game from another server can be fetched
    without editing .env. Falls back to the configured platform if the prefix is unknown."""
    try:
        return regional_host(platform_of_match_id(match_id))
    except ValueError:
        return regional_host(fallback_platform)


def platform_of_match_id(match_id: str) -> str:
    """Extract the platform from a match ID.

    Match IDs carry their origin as a prefix (``NA1_4567890123``), which is how you
    can tell where a match came from even though it was fetched from a cluster host.

    >>> platform_of_match_id("NA1_4567890123")
    'na1'
    """
    prefix, _, rest = match_id.partition("_")
    if not rest:
        raise ValueError(f"Malformed match ID {match_id!r} (expected e.g. 'NA1_123456')")
    return prefix.lower()
