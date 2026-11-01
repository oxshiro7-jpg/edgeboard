"""Cached, rate-limited JSON GETs for the free data sources.

football-data.org free tier: 10 requests a minute, so live calls are spaced
out. The Odds API free tier: 500 credits a month, so responses are kept for
a long time and calls stop when the remaining credits reported by the API
fall to a reserve. A refused or failed call raises; the fixture it was for is
shown with its status (never filled in), and nothing failed is cached.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

log = logging.getLogger("edgeboard.web_cache")


class SourceError(RuntimeError):
    """The data source refused or failed the request."""


class WebCache:
    def __init__(self, name: str, base_url: str, headers: dict[str, str], ttl: Callable[[str, dict], int],
                 min_interval: float = 0.0, cache_path: Optional[str] = None,
                 remaining_header: Optional[str] = None, reserve: int = 0,
                 fetch: Optional[Callable[[str, dict], dict]] = None, clock=time.time, sleep=time.sleep,
                 secret_params: tuple[str, ...] = ()):
        self.name, self.base_url, self.headers = name, base_url, headers
        self.ttl, self.min_interval, self.cache_path = ttl, min_interval, cache_path
        self.remaining_header, self.reserve = remaining_header, reserve
        self.secret_params = secret_params        # sent, but never part of the cache key
        self._fetch, self._clock, self._sleep = fetch, clock, sleep
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, object]] = {}
        self._last_call = 0.0
        self.calls = 0
        self.remaining: Optional[int] = None
        self._load()

    # ------------------------------------------------------------ persistence
    def _load(self) -> None:
        if not self.cache_path or not os.path.exists(self.cache_path):
            return
        try:
            with open(self.cache_path, encoding="utf-8") as f:
                data = json.load(f)
            self._cache = {k: (t, b) for k, (t, b) in data.get("cache", {}).items()}
            self.remaining = data.get("remaining")
        except Exception:
            log.warning("discarding unreadable cache %s", self.cache_path)
            self._cache = {}

    def _save(self) -> None:
        if not self.cache_path:
            return
        try:
            tmp = self.cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"cache": self._cache, "remaining": self.remaining}, f)
            os.replace(tmp, self.cache_path)
        except OSError:
            log.warning("could not write cache %s", self.cache_path)

    # ------------------------------------------------------------ calls
    @staticmethod
    def key(path: str, params: dict) -> str:
        q = urllib.parse.urlencode(sorted((k, str(v)) for k, v in params.items() if v is not None))
        return f"{path}?{q}" if q else path

    def status(self) -> dict:
        return {"source": self.name, "live_calls_this_process": self.calls,
                "remaining_reported": self.remaining, "cached_responses": len(self._cache)}

    def get(self, path: str, params: Optional[dict] = None, secrets: Optional[dict] = None):
        params = {k: v for k, v in (params or {}).items() if v is not None}
        key = self.key(path, params)
        with self._lock:                       # one live call at a time keeps the spacing honest
            hit = self._cache.get(key)
            if hit and self._clock() - hit[0] < self.ttl(path, params):
                return hit[1]
            if self.remaining is not None and self.remaining <= self.reserve:
                raise SourceError(f"{self.name}: request allowance used up ({self.remaining} left, "
                                  f"reserve {self.reserve})")
            wait = self.min_interval - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)
            self._last_call = self._clock()
            self.calls += 1
            body = self._fetch(path, params) if self._fetch else self._live(path, {**params, **(secrets or {})})
            self._cache[key] = (self._clock(), body)
            if len(self._cache) > 3000:
                now = self._clock()
                self._cache = {k: v for k, v in self._cache.items() if now - v[0] < 14 * 86400}
            self._save()
            return body

    def _live(self, path: str, params: dict):
        q = urllib.parse.urlencode(params)
        req = urllib.request.Request(f"{self.base_url}{path}" + (f"?{q}" if q else ""), headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                if self.remaining_header:
                    rem = r.headers.get(self.remaining_header)
                    if rem is not None and rem.strip().lstrip("-").isdigit():
                        self.remaining = int(rem.strip())
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode("utf-8"))
                msg = detail.get("message") or detail
            except Exception:
                msg = e.reason
            raise SourceError(f"{self.name} refused {path} (HTTP {e.code}): {msg}") from None
        except urllib.error.URLError as e:
            raise SourceError(f"{self.name} unreachable: {e.reason}") from None
