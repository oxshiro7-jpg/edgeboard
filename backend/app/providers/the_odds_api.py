"""The Odds API (v4) adapter. Maps bookmaker outcomes into engine market keys.

Docs: https://the-odds-api.com/liveapi/guides/v4/
Odds history (opening/high/low) is accumulated by our own odds_sync job into
odds_history, because the live endpoint only returns current prices.
"""
from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Iterable, Optional

from app.engine.types import OddsQuote, OddsSeries

from .base import FixtureRef, OddsProvider

BASE_URL = "https://api.the-odds-api.com/v4"

# competition id (ours) -> sport key (verify against /v4/sports for your plan)
SPORT_KEYS = {
    "epl": "soccer_epl",
    "laliga": "soccer_spain_la_liga",
    "seriea": "soccer_italy_serie_a",
    "bundesliga": "soccer_germany_bundesliga",
    "ligue1": "soccer_france_ligue_one",
    "eredivisie": "soccer_netherlands_eredivisie",
    "primeira": "soccer_portugal_primeira_liga",
    "ucl": "soccer_uefa_champs_league",
    "uel": "soccer_uefa_europa_league",
    "uecl": "soccer_uefa_europa_conference_league",
    "facup": "soccer_fa_cup",
}


def normalise(name: str) -> str:
    """Case, accents and spacing only. No token stripping or similarity:
    a different spelling needs an explicit alias, it is never guessed."""
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return " ".join(n.split())


def map_event(event: dict, home_name: str, away_name: str,
              bookmakers: Optional[Iterable[str]] = None) -> dict[str, list[OddsQuote]]:
    """Convert one The Odds API event into {market_key: [quotes per bookmaker]}.

    h2h      -> 1X2:H/D/A
    totals   -> OU:<point>:over/under
    spreads  -> AH:H|A:<point>
    btts     -> BTTS:yes/no (where the plan includes it)
    """
    out: dict[str, list[OddsQuote]] = {}
    h, a = normalise(home_name), normalise(away_name)
    allowed = set(bookmakers) if bookmakers else None
    for bm in event.get("bookmakers", []):
        if allowed and bm["key"] not in allowed:
            continue
        ts = datetime.fromisoformat(bm.get("last_update", event.get("commence_time")).replace("Z", "+00:00"))
        for market in bm.get("markets", []):
            mk = market["key"]
            for o in market.get("outcomes", []):
                name, price, point = o["name"], float(o["price"]), o.get("point")
                key = None
                nn = normalise(name)
                if mk == "h2h":
                    key = "1X2:D" if name == "Draw" else "1X2:H" if nn == h else "1X2:A" if nn == a else None
                elif mk == "totals" and point is not None:
                    key = f"OU:{point:g}:{name.lower()}"
                elif mk == "spreads" and point is not None:
                    side = "H" if nn == h else "A" if nn == a else None
                    key = f"AH:{side}:{point:+g}" if side and point != 0 else (f"AH:{side}:0" if side else None)
                elif mk == "btts":
                    key = f"BTTS:{name.lower()}"
                if key:
                    out.setdefault(key, []).append(OddsQuote(key, price, bm["key"], ts))
    return out


def best_price_series(quotes: dict[str, list[OddsQuote]], history: dict[str, list[OddsQuote]]) -> dict[str, OddsSeries]:
    """Use the best available price per selection; opening = first stored quote."""
    out = {}
    for key, qs in quotes.items():
        best = max(qs, key=lambda q: q.odds)
        hist = history.get(key, [])
        opening = hist[0].odds if hist else best.odds
        out[key] = OddsSeries(key, opening, best.odds, hist)
    return out


class TheOddsApiProvider(OddsProvider):
    name = "the_odds_api"

    def __init__(self, api_key: str, regions: str = "uk,eu", bookmakers: Optional[list[str]] = None,
                 history_loader=None, aliases: Optional[dict[str, list[str]]] = None):
        self.api_key = api_key
        self.regions = regions
        self.bookmakers = bookmakers
        self.history_loader = history_loader   # callable(fixture_id) -> {key: [OddsQuote]} from odds_history
        self.aliases = aliases or {}           # provider team id -> exact odds-feed names (teams.aliases)
        self._cache: dict[str, list[dict]] = {}

    def _events(self, sport: str) -> list[dict]:
        if sport not in self._cache:
            import httpx
            r = httpx.get(f"{BASE_URL}/sports/{sport}/odds", timeout=20, params={
                "apiKey": self.api_key, "regions": self.regions, "markets": "h2h,totals,spreads",
                "oddsFormat": "decimal", "dateFormat": "iso"})
            r.raise_for_status()
            self._cache[sport] = r.json()
        return self._cache[sport]

    def odds(self, fixture: FixtureRef, home_name: str, away_name: str) -> dict[str, OddsSeries]:
        """Exact match only: the event's team names must equal the verified
        team names (or an alias explicitly registered for that provider team
        id) and kick off within 3 hours of the verified kickoff. Otherwise
        nothing is returned and the fixture shows ODDS UNAVAILABLE."""
        sport = SPORT_KEYS.get(fixture.competition_id)
        if not sport or fixture.kickoff is None:
            return {}
        hs = {normalise(home_name), *(normalise(x) for x in self.aliases.get(fixture.home_id, []))}
        as_ = {normalise(away_name), *(normalise(x) for x in self.aliases.get(fixture.away_id, []))}
        matches = []
        for ev in self._events(sport):
            ko = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            if normalise(ev["home_team"]) in hs and normalise(ev["away_team"]) in as_ \
                    and abs((ko - fixture.kickoff).total_seconds()) <= 3 * 3600:
                matches.append(ev)
        if len(matches) != 1:
            return {}          # none, or ambiguous: never pick one
        ev = matches[0]
        quotes = map_event(ev, ev["home_team"], ev["away_team"], self.bookmakers)
        hist = self.history_loader(fixture.id) if self.history_loader else {}
        return best_price_series(quotes, hist)
