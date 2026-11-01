"""Live API-Football transport with a response cache and a daily request budget.

The free API-Football plan allows 100 requests a day, and a full analysis
costs about 30 per match, so every response is cached for as long as it can
be trusted (finished-match statistics never change; odds and the date listing
go stale within minutes) and the transport stops before the daily limit.
A request refused for budget raises BudgetExhausted; the fixture it was for
is then shown as not verified, never filled in.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from .snapshot import BASE_URL, request_key

log = logging.getLogger("edgeboard.api_cache")

# Seconds each kind of response stays valid.
TTL = {
    "/fixtures/statistics": 30 * 86400,   # statistics of finished matches
    "/teams": 7 * 86400,
    "/fixtures/headtohead": 86400,
    "/fixtures:team": 6 * 3600,           # a team's recent results
    "/fixtures:id": 15 * 60,
    "/fixtures:date": 2 * 3600,         # postponements still caught: /fixtures?id is rechecked on open
    "/odds": 2 * 3600,
    "/injuries": 2 * 3600,
    "/fixtures/lineups": 3600,
}
DEFAULT_TTL = 3600


class BudgetExhausted(RuntimeError):
    """The daily API request budget is used up."""


def _ttl(path: str, params: dict) -> int:
    if path == "/fixtures":
        for p in ("team", "id", "date"):
            if p in params:
                return TTL[f"/fixtures:{p}"]
    return TTL.get(path, DEFAULT_TTL)


class CachedTransport:
    def __init__(self, api_key: str, daily_limit: int = 100, cache_path: Optional[str] = None, fetch=None,
                 clock=time.time):
        self.api_key = api_key
        self.daily_limit = daily_limit
        self.cache_path = cache_path
        self._fetch = fetch
        self._clock = clock
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, dict]] = {}
        self._day = ""
        self.used_today = 0
        self.remaining_reported: Optional[int] = None
        self._load()

    # ------------------------------------------------------------ persistence
    def _load(self) -> None:
        if not self.cache_path or not os.path.exists(self.cache_path):
            return
        try:
            with open(self.cache_path, encoding="utf-8") as f:
                data = json.load(f)
            self._cache = {k: (t, b) for k, (t, b) in data.get("cache", {}).items()}
            self._day, self.used_today = data.get("day", ""), int(data.get("used", 0))
        except Exception:   # a corrupt cache is discarded, never trusted partially
            log.warning("discarding unreadable API cache %s", self.cache_path)
            self._cache = {}

    def _save(self) -> None:
        if not self.cache_path:
            return
        tmp = self.cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"day": self._day, "used": self.used_today, "cache": self._cache}, f)
        os.replace(tmp, self.cache_path)

    # ------------------------------------------------------------ calls
    def status(self) -> dict:
        self._roll_day()
        return {"daily_limit": self.daily_limit, "used_today": self.used_today,
                "remaining_reported_by_api": self.remaining_reported, "cached_responses": len(self._cache)}

    def _roll_day(self) -> None:
        today = datetime.fromtimestamp(self._clock(), timezone.utc).date().isoformat()   # API-Football resets at 00:00 UTC
        if today != self._day:
            self._day, self.used_today, self.remaining_reported = today, 0, None

    def __call__(self, path: str, params: dict) -> dict:
        key = request_key(path, params)
        with self._lock:
            now = self._clock()
            hit = self._cache.get(key)
            if hit and now - hit[0] < _ttl(path, params):
                return hit[1]
            self._roll_day()
            if self.used_today >= self.daily_limit or self.remaining_reported == 0:
                raise BudgetExhausted(f"daily API request budget used ({self.used_today}/{self.daily_limit})")
            self.used_today += 1
        body = self._fetch(path, params) if self._fetch else self._live(key)
        with self._lock:
            if not body.get("errors"):           # never cache an error response
                self._cache[key] = (self._clock(), body)
                self._prune()
            try:
                self._save()
            except OSError:
                log.warning("could not write API cache %s", self.cache_path)
        return body

    def _live(self, key: str) -> dict:
        req = urllib.request.Request(f"{BASE_URL}{key}", headers={"x-apisports-key": self.api_key})
        with urllib.request.urlopen(req, timeout=30) as r:
            rem = r.headers.get("x-ratelimit-requests-remaining")
            if rem is not None and rem.isdigit():
                self.remaining_reported = int(rem)
            return json.loads(r.read().decode("utf-8"))

    def _prune(self) -> None:
        now = self._clock()
        if len(self._cache) > 5000:
            self._cache = {k: v for k, v in self._cache.items() if now - v[0] < 30 * 86400}


_shared: dict[str, CachedTransport] = {}


def shared_transport(api_key: str) -> CachedTransport:
    """One transport (one cache, one budget) per process and key."""
    if api_key not in _shared:
        _shared[api_key] = CachedTransport(
            api_key, daily_limit=int(os.getenv("API_DAILY_CALL_LIMIT", "100")),
            cache_path=os.getenv("API_CACHE_PATH", "/tmp/edgeboard-api-cache.json"))
    return _shared[api_key]
