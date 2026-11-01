"""Routing: which Riot host each kind of request goes to."""

from __future__ import annotations

import pytest

from lolreview.riot.routing import account_host, match_host, platform_host, regional_host


def test_tw2_hong_kong_routes():
    assert platform_host("tw2") == "https://tw2.api.riotgames.com"      # ladder (LEAGUE-V4)
    assert regional_host("tw2") == "https://sea.api.riotgames.com"      # match history (MATCH-V5)
    assert account_host("tw2") == "https://asia.api.riotgames.com"      # Riot ID (ACCOUNT-V1 has no SEA)


def test_account_host_keeps_supported_clusters():
    assert account_host("na1") == "https://americas.api.riotgames.com"
    assert account_host("euw1") == "https://europe.api.riotgames.com"
    assert account_host("kr") == "https://asia.api.riotgames.com"


def test_match_routes_by_its_own_prefix_not_by_env():
    assert match_host("TW2_123456", fallback_platform="na1") == "https://sea.api.riotgames.com"
    assert match_host("NA1_123456", fallback_platform="tw2") == "https://americas.api.riotgames.com"
    assert match_host("EUW1_42", fallback_platform="na1") == "https://europe.api.riotgames.com"
    assert match_host("XX9_1", fallback_platform="na1") == "https://americas.api.riotgames.com"


def test_unknown_platform_is_an_error():
    with pytest.raises(ValueError):
        regional_host("hk1")
