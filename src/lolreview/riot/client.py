"""Rate-limited, caching Riot API client.

Design notes:

* Every outbound call goes through the rate limiter, cache hits do not. That's what
  makes a re-run after a crash nearly free.
* 403 fails fast and loudly — a development key expiring mid-run is the single most likely
  interruption, and it should not look like a transient error.
* 404 returns None rather than raising. An account with no ranked games is normal.
* Match and timeline responses are cached forever; league entries are not, since the
  ladder moves.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any
from urllib.parse import quote

import requests

from ..config import Config
from .cache import JsonCache
from .ratelimit import RateLimiter
from .routing import account_host, match_host, platform_host, regional_host

log = logging.getLogger(__name__)

MAX_RETRIES = 5
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ExpiredKey(RuntimeError):
    """403 from Riot — almost always a development key past its 24h life."""


class RiotAPIError(RuntimeError):
    pass


class RiotClient:
    def __init__(
        self,
        cfg: Config,
        cache: JsonCache,
        limiter: RateLimiter | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.cfg = cfg
        self.cache = cache
        self.limiter = limiter or RateLimiter()
        self.session = session or requests.Session()
        self.session.headers.update({"X-Riot-Token": cfg.api_key})
        self.platform_host = platform_host(cfg.platform)
        self.regional_host = regional_host(cfg.platform)
        self.account_host = account_host(cfg.platform)
        self.stats = {
            "requests": 0,
            "cache_hits": 0,
            "retries": 0,
            "sleep_s": 0.0,
            "net_errors": 0,
            "stalled_s": 0.0,  # time lost to failed connections, timeouts and backoff
        }

    # ---- transport ----------------------------------------------------

    def _request(self, url: str, params: dict[str, Any] | None = None) -> Any | None:
        for attempt in range(MAX_RETRIES):
            self.stats["sleep_s"] += self.limiter.acquire()
            t0 = time.monotonic()
            try:
                resp = self.session.get(url, params=params, timeout=20)
            except requests.RequestException as exc:
                self.stats["net_errors"] += 1
                log.warning("network error (%s), retrying", type(exc).__name__)
                if attempt == MAX_RETRIES - 1:
                    raise RiotAPIError(f"network error for {url}: {exc}") from exc
                self._backoff(attempt)
                self.stats["stalled_s"] += time.monotonic() - t0
                continue

            self.stats["requests"] += 1

            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 404:
                return None
            if resp.status_code in (401, 403):
                # Riot answers 401 "Unknown apikey" or 403 "Forbidden" for expired,
                # regenerated, or mistyped keys.
                raise ExpiredKey(
                    f"{resp.status_code} from Riot: your API key isn't accepted. Development keys "
                    "expire after 24h, and regenerating one kills the previous key.\n"
                    "Get the current key at developer.riotgames.com, paste it into .env as\n"
                    "    RIOT_API_KEY=RGAPI-...\n"
                    "then re-run. Anything already downloaded stays cached."
                )
            if resp.status_code in RETRYABLE_STATUS:
                self.stats["retries"] += 1
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", 1))
                    log.warning("429 rate limited, waiting %.0fs", retry_after)
                    self.limiter.penalize(retry_after)
                else:
                    t1 = time.monotonic()
                    self._backoff(attempt)
                    self.stats["stalled_s"] += time.monotonic() - t1
                continue

            raise RiotAPIError(f"HTTP {resp.status_code} for {url}: {resp.text[:200]}")

        raise RiotAPIError(f"gave up after {MAX_RETRIES} attempts: {url}")

    def _backoff(self, attempt: int) -> None:
        delay = min(2**attempt, 30) + random.uniform(0, 0.5)
        time.sleep(delay)

    def _cached(self, namespace: str, key: str, url: str) -> Any | None:
        hit = self.cache.get(namespace, key)
        if hit is not None:
            self.stats["cache_hits"] += 1
            return hit
        data = self._request(url)
        if data is not None:
            self.cache.put(namespace, key, data)
        return data

    # ---- endpoints ----------------------------------------------------

    def account_by_riot_id(self, game_name: str, tag_line: str) -> Any | None:
        url = (
            f"{self.account_host}/riot/account/v1/accounts/by-riot-id/"
            f"{quote(game_name)}/{quote(tag_line)}"
        )
        return self._request(url)

    def league_entries(self, tier: str, division: str, page: int = 1) -> list[dict]:
        """One page of ranked entries for a tier+division. Not cached — the ladder moves."""
        url = (
            f"{self.platform_host}/lol/league/v4/entries/"
            f"RANKED_SOLO_5x5/{tier.upper()}/{division.upper()}"
        )
        return self._request(url, params={"page": page}) or []

    def match_ids(
        self, puuid: str, count: int = 5, start: int = 0, start_time: int | None = None,
        queue: int | None | str = "config",
    ) -> list[str]:
        """Most recent match IDs for a player, newest first.

        `start_time` is epoch SECONDS. There is no patch filter on this endpoint, but
        patches map to date ranges, so a start time skips old-patch games before they
        cost a download. `queue`: default RIOT_QUEUE (420); an ID; or None for every queue.
        """
        url = f"{self.regional_host}/lol/match/v5/matches/by-puuid/{quote(puuid)}/ids"
        q = self.cfg.queue if queue == "config" else queue
        params: dict[str, int] = {"count": count, "start": start}
        if q is not None:
            params["queue"] = q
        if start_time is not None:
            params["startTime"] = start_time
        return self._request(url, params=params) or []

    def match(self, match_id: str) -> Any | None:
        url = f"{match_host(match_id, self.cfg.platform)}/lol/match/v5/matches/{quote(match_id)}"
        return self._cached("matches", match_id, url)

    def timeline(self, match_id: str) -> Any | None:
        url = f"{match_host(match_id, self.cfg.platform)}/lol/match/v5/matches/{quote(match_id)}/timeline"
        return self._cached("timelines", match_id, url)

    # ---- reporting ----------------------------------------------------

    def report(self) -> str:
        s = self.stats
        return (
            f"requests={s['requests']} cache_hits={s['cache_hits']} "
            f"retries={s['retries']} rate_limit_sleep={s['sleep_s']:.0f}s "
            f"net_errors={s['net_errors']} stalled={s['stalled_s']:.0f}s"
        )
