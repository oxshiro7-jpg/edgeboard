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
    "championship": "soccer_efl_champ",
    "brasileirao": "soccer_brazil_campeonato",
}

# Legal-form and filler tokens that differ between feeds ("Real Madrid CF" / "Real Madrid").
# Removing them is a fixed rule applied to both sides; it is not a similarity measure.
_FORM_TOKENS = {"fc", "afc", "cf", "sc", "ac", "as", "ss", "ssc", "sv", "vfl", "vfb", "tsg", "fk", "cd",
                "ud", "rcd", "rc", "club", "de", "calcio", "and", "1"}

# Explicit aliases for clubs whose names differ beyond legal form between football-data.org and
# The Odds API (canonical football-data name -> canonical odds-feed name). An alias that is wrong
# simply finds no event: matching also requires both teams and the kickoff to agree.
ALIASES = {
    "internazionale milano": "inter milan",
    "bayern munchen": "bayern munich",
    "bayer 04 leverkusen": "bayer leverkusen",
    "athletic": "athletic bilbao",
    "real betis balompie": "real betis",
    "deportivo alaves": "alaves",
    "rayo vallecano madrid": "rayo vallecano",
}


def canonical(name: str) -> str:
    """normalise() plus fixed rules: '&' -> 'and', punctuation removed, legal-form tokens dropped,
    then an explicit alias if one is registered."""
    n = normalise(name.replace("&", " and "))
    n = "".join(ch if ch.isalnum() or ch == " " else " " for ch in n)
    n = " ".join(t for t in n.split() if t not in _FORM_TOKENS)
    return ALIASES.get(n, n)


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

    def __init__(self, api_key: str, regions: str = "eu", markets: str = "h2h,totals",
                 bookmakers: Optional[list[str]] = None, history_loader=None,
                 aliases: Optional[dict[str, list[str]]] = None, http=None, store=None):
        import os
        from .web_cache import WebCache
        self.api_key = api_key
        self.regions = regions
        self.markets = markets
        self.bookmakers = bookmakers
        self.history_loader = history_loader   # callable(fixture_id) -> {key: [OddsQuote]} from odds_history
        self.aliases = aliases or {}           # provider team id -> exact odds-feed names (teams.aliases)
        hours = float(os.getenv("ODDS_CACHE_HOURS", "24"))
        # Free plan: 500 credits a month; each call costs (markets x regions). Keep a reserve.
        self.http = http or WebCache(
            "The Odds API", BASE_URL, {}, lambda path, params: int(hours * 3600),
            cache_path=os.getenv("ODDS_CACHE_PATH", "/tmp/edgeboard-odds.json"),
            remaining_header="x-requests-remaining", reserve=int(os.getenv("ODDS_CREDIT_RESERVE", "10")),
            secret_params=("apiKey",), store=store)

    def _params(self) -> dict:
        return {"regions": self.regions, "markets": self.markets, "oddsFormat": "decimal", "dateFormat": "iso"}

    def _events(self, sport: str, max_age: Optional[float] = None) -> list[dict]:
        body = self.http.get(f"/sports/{sport}/odds", self._params(), secrets={"apiKey": self.api_key},
                             max_age=max_age)
        return body if isinstance(body, list) else []

    def refresh(self, competition_id: str, max_age: float = 20 * 60) -> bool:
        """Re-fetch one league's prices if the cached copy is older than max_age (used for closing
        prices just before kickoff; the refreshed prices also serve the app's pages)."""
        sport = SPORT_KEYS.get(competition_id)
        if not sport:
            return False
        self._events(sport, max_age=max_age)
        return True

    def priced_at(self, competition_id: str) -> Optional[float]:
        sport = SPORT_KEYS.get(competition_id)
        return self.http.cached_at(f"/sports/{sport}/odds", self._params()) if sport else None

    def odds(self, fixture: FixtureRef, home_name: str, away_name: str,
             alt_names: tuple = ()) -> dict[str, OddsSeries]:
        """The event must match BOTH verified teams (provider name or short name, after the fixed
        canonical rules or an explicit alias) and kick off within 3 hours of the verified kickoff,
        and exactly one event may match. Otherwise nothing is returned: ODDS UNAVAILABLE."""
        sport = SPORT_KEYS.get(fixture.competition_id)
        if not sport or fixture.kickoff is None:
            return {}
        alt_h, alt_a = (alt_names + (None, None))[:2]
        hs = {canonical(x) for x in (home_name, alt_h, *self.aliases.get(fixture.home_id, [])) if x}
        as_ = {canonical(x) for x in (away_name, alt_a, *self.aliases.get(fixture.away_id, [])) if x}
        matches = []
        for ev in self._events(sport):
            ko = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            if canonical(ev["home_team"]) in hs and canonical(ev["away_team"]) in as_ \
                    and abs((ko - fixture.kickoff).total_seconds()) <= 3 * 3600:
                matches.append(ev)
        if len(matches) != 1:
            return {}          # none, or ambiguous: never pick one
        ev = matches[0]
        quotes = map_event(ev, ev["home_team"], ev["away_team"], self.bookmakers)
        hist = self.history_loader(fixture.id) if self.history_loader else {}
        return best_price_series(quotes, hist)
