"""Provider selection. Only real data APIs can be configured; there is no demo,
synthetic or AI-backed provider in the application."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from typing import Optional

from .base import FootballDataProvider, InjuryProvider, LineupProvider, OddsProvider

FOOTBALL_PROVIDERS = ("api_football",)
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
