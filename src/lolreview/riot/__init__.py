"""Riot API access: routing values, rate-limited client, disk cache."""

from .routing import platform_host, regional_host, region_for, platform_of_match_id

__all__ = ["platform_host", "regional_host", "region_for", "platform_of_match_id"]
