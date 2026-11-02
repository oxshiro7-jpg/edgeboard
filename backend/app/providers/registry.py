"""Provider selection. Only real data APIs can be configured; there is no demo,
synthetic or AI-backed provider in the application."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from typing import Optional

from .base import FootballDataProvider, InjuryProvider, LineupProvider, OddsProvider

FOOTBALL_PROVIDERS = ("api_football", "football_data")
ODDS_PROVIDERS = ("api_football", "the_odds_api")


class ProviderNotConfigured(RuntimeError):
    """No fixtures API is configured, so no fixture can be verified."""


@dataclass
class Providers:
    football: FootballDataProvider
    odds: OddsProvider
    injuries: Optional[InjuryProvider]
    lineups: Optional[LineupProvider]


def build_providers(football: str = "api_football", odds: str = "api_football", day: Optional[date] = None,
                    keys: Optional[dict[str, str]] = None, tz: str = "UTC") -> Providers:
    keys = keys or {}
    if football not in FOOTBALL_PROVIDERS:
        raise ProviderNotConfigured(f"Unknown football provider {football!r}; supported: {FOOTBALL_PROVIDERS}")
    if odds not in ODDS_PROVIDERS:
        raise ProviderNotConfigured(f"Unknown odds provider {odds!r}; supported: {ODDS_PROVIDERS}")
    if football == "football_data":
        return _football_data(keys, tz)
    from .api_football import ApiFootballOdds, ApiFootballProvider
    snap = os.getenv("API_FOOTBALL_SNAPSHOT")
    if snap:   # replay of raw API-Football responses recorded by scripts/fetch_snapshot.py
        from .snapshot import ReplayTransport
        with open(snap, encoding="utf-8") as f:
            data = json.load(f)
        fp = ApiFootballProvider("", tz=data.get("tz", tz), http_get=ReplayTransport(data), history_limit=data.get("history"))
        return Providers(fp, ApiFootballOdds(fp), fp.injury_provider(), fp.lineup_provider())
    if not keys.get("API_FOOTBALL_KEY"):
        raise ProviderNotConfigured("API_FOOTBALL_KEY is not set: no fixtures can be fetched or verified")
    from .http_cache import shared_transport
    fp = ApiFootballProvider(keys["API_FOOTBALL_KEY"], tz=tz, http_get=shared_transport(keys["API_FOOTBALL_KEY"]),
                             history_limit=int(os.getenv("API_HISTORY_MATCHES", "10")))
    if odds == "the_odds_api":
        if not keys.get("THE_ODDS_API_KEY"):
            raise ProviderNotConfigured("THE_ODDS_API_KEY is not set")
        from .the_odds_api import TheOddsApiProvider
        op: OddsProvider = TheOddsApiProvider(keys["THE_ODDS_API_KEY"])
    else:
        op = ApiFootballOdds(fp)
    return Providers(fp, op, fp.injury_provider(), fp.lineup_provider())


_shared_http: dict[tuple, object] = {}


def _football_data(keys: dict[str, str], tz: str) -> Providers:
    """Free stack: football-data.org (fixtures, results) + The Odds API (prices).
    No injury or lineup feed: those inputs stay empty and lower data quality."""
    token = keys.get("FOOTBALL_DATA_TOKEN")
    if not token:
        raise ProviderNotConfigured("FOOTBALL_DATA_TOKEN is not set: no fixtures can be fetched or verified")
    from .football_data import FootballDataOrgProvider
    from .the_odds_api import TheOddsApiProvider
    from app.store import get_store
    from .football_data import BASE_URL as FD_URL, _ttl as fd_ttl
    from .web_cache import WebCache
    store = get_store()
    fd = _shared_http.get(("fd", token)) or WebCache(
        "football-data.org", FD_URL, {"X-Auth-Token": token}, fd_ttl, min_interval=6.5,
        cache_path=os.getenv("FOOTBALL_DATA_CACHE_PATH", "/tmp/edgeboard-football-data.json"),
        remaining_header="X-Requests-Available-Minute", store=store)
    fp = FootballDataOrgProvider(token, tz=tz, http=fd)
    _shared_http[("fd", token)] = fp.http          # one cache and one rate limiter per process
    okey = keys.get("THE_ODDS_API_KEY")
    if okey:
        oh = _shared_http.get(("odds", okey))
        op: OddsProvider = TheOddsApiProvider(okey, http=oh, store=store)
        _shared_http[("odds", okey)] = op.http      # type: ignore[attr-defined]
    else:
        op = _NoOdds()
    return Providers(fp, op, None, None)


class _NoOdds(OddsProvider):
    name = "none"

    def odds(self, fixture, home_name="", away_name="", alt_names=()):
        return {}          # -> ODDS UNAVAILABLE on every fixture
