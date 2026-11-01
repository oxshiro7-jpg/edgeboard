"""API-Football (api-sports.io v3) adapter: fixtures, teams, statistics,
injuries, lineups and bookmaker odds, all addressed by provider ids.

Docs: https://www.api-football.com/documentation-v3
Verify LEAGUE_IDS and ODDS_BETS against /leagues and /odds/bets for your plan.

Rules this adapter keeps:
* Every fixture and team comes from an API response; nothing is synthesised.
* Values the API does not return stay None (xG, Elo, player importance...).
  The engine then lowers data quality or skips the component; it never
  substitutes a default number for a missing statistic.
* Odds are fetched by provider fixture id, so no name matching is involved.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Callable, Optional

from app.engine.types import (Absence, LineupInfo, MatchContext, OddsQuote, OddsSeries, Referee, StyleProfile,
                              TeamMatchRecord)

from .base import (CompetitionRef, FixtureRef, FootballDataProvider, InjuryProvider, LineupProvider, OddsProvider,
                   TeamRef)

BASE_URL = "https://v3.football.api-sports.io"

# our slug -> (API-Football league id, display name, country, type)
LEAGUES = {
    "epl": (39, "Premier League", "England", "league"),
    "laliga": (140, "La Liga", "Spain", "league"),
    "seriea": (135, "Serie A", "Italy", "league"),
    "bundesliga": (78, "Bundesliga", "Germany", "league"),
    "ligue1": (61, "Ligue 1", "France", "league"),
    "eredivisie": (88, "Eredivisie", "Netherlands", "league"),
    "primeira": (94, "Primeira Liga", "Portugal", "league"),
    "ucl": (2, "UEFA Champions League", "Europe", "continental"),
    "uel": (3, "UEFA Europa League", "Europe", "continental"),
    "uecl": (848, "UEFA Conference League", "Europe", "continental"),
    "facup": (45, "FA Cup", "England", "cup"),
    "eflcup": (48, "EFL Cup", "England", "cup"),
    "copadelrey": (143, "Copa del Rey", "Spain", "cup"),
    "coppaitalia": (137, "Coppa Italia", "Italy", "cup"),
    "dfbpokal": (81, "DFB-Pokal", "Germany", "cup"),
    "coupedefrance": (66, "Coupe de France", "France", "cup"),
    "knvbcup": (90, "KNVB Cup", "Netherlands", "cup"),
    "tacadeportugal": (96, "Taça de Portugal", "Portugal", "cup"),
    "uefasupercup": (531, "UEFA Super Cup", "Europe", "continental"),
    "clubworldcup": (15, "FIFA Club World Cup", "World", "continental"),
}
LEAGUE_TO_SLUG = {v[0]: k for k, v in LEAGUES.items()}

STATUS = {
    "TBD": "scheduled", "NS": "scheduled",
    "1H": "live", "HT": "live", "2H": "live", "ET": "live", "BT": "live", "P": "live", "LIVE": "live", "INT": "interrupted",
    "SUSP": "suspended", "FT": "finished", "AET": "finished", "PEN": "finished",
    "PST": "postponed", "CANC": "cancelled", "ABD": "abandoned", "AWD": "awarded", "WO": "walkover",
}

POSITION_MAP = {"G": "GK", "D": "CB", "M": "CM", "F": "ST"}


def parse_fixture(f: dict, provider: str = "api_football") -> Optional[FixtureRef]:
    """Map one /fixtures response item. Returns None for leagues we don't support."""
    league_id = f.get("league", {}).get("id")
    slug = LEAGUE_TO_SLUG.get(league_id)
    if slug is None:
        return None
    fx, teams = f["fixture"], f["teams"]
    code = fx.get("status", {}).get("short")
    ko = datetime.fromisoformat(fx["date"]) if fx.get("date") else None
    return FixtureRef(
        id=str(fx["id"]), competition_id=slug, kickoff=ko,
        home_id=str(teams["home"]["id"]) if teams["home"].get("id") else "",
        away_id=str(teams["away"]["id"]) if teams["away"].get("id") else "",
        status=STATUS.get(code, "unknown"), status_code=code,
        home_name=teams["home"].get("name"), away_name=teams["away"].get("name"), provider=provider,
    )


def _stat(stats: list[dict], name: str) -> Optional[float]:
    for s in stats:
        if s.get("type") == name:
            v = s.get("value")
            if v is None:
                return None
            if isinstance(v, str):
                v = v.rstrip("%")
            try:
                return float(v)
            except ValueError:
                return None
    return None


def map_fixture_to_record(fx: dict, stats_by_team: dict[int, list[dict]], team_id: int,
                          opponent_rating: Optional[float]) -> TeamMatchRecord:
    home = fx["teams"]["home"]["id"] == team_id
    opp_id = fx["teams"]["away" if home else "home"]["id"]
    gf = fx["goals"]["home" if home else "away"]
    ga = fx["goals"]["away" if home else "home"]
    mine, theirs = stats_by_team.get(team_id, []), stats_by_team.get(opp_id, [])
    shots, sot = _stat(mine, "Total Shots"), _stat(mine, "Shots on Goal")
    yellow, red = _stat(mine, "Yellow Cards"), _stat(mine, "Red Cards")
    cf, ca = _stat(mine, "Corner Kicks"), _stat(theirs, "Corner Kicks")
    return TeamMatchRecord(
        date=datetime.fromisoformat(fx["fixture"]["date"]), opponent_id=str(opp_id), opponent_rating=opponent_rating,
        venue="H" if home else "A", competition=str(fx["league"]["id"]), goals_for=int(gf), goals_against=int(ga),
        xg_for=_stat(mine, "expected_goals"), xg_against=_stat(theirs, "expected_goals"),
        shots=int(shots) if shots is not None else None, shots_on_target=int(sot) if sot is not None else None,
        corners_for=int(cf) if cf is not None else None, corners_against=int(ca) if ca is not None else None,
        cards=int((yellow or 0) + (red or 0)) if yellow is not None or red is not None else None,
    )


FINISHED = {"FT", "AET", "PEN"}
_NO_LAST_PARAM: set[str] = set()     # API keys whose plan refuses the `last` parameter (free plan)


def _finished_before(items: list[dict], before: datetime) -> list[dict]:
    """Finished fixtures before `before`, newest first, de-duplicated by id."""
    seen, out = set(), []
    for f in items:
        fid = f["fixture"]["id"]
        if fid in seen or (f["fixture"].get("status") or {}).get("short") not in FINISHED:
            continue
        if datetime.fromisoformat(f["fixture"]["date"]) >= before:
            continue
        seen.add(fid)
        out.append(f)
    return sorted(out, key=lambda f: f["fixture"]["date"], reverse=True)


class ApiFootballProvider(FootballDataProvider):
    name = "api_football"

    def __init__(self, api_key: str, tz: str = "UTC",
                 elo_lookup: Optional[Callable[[str, datetime], Optional[float]]] = None,
                 importance_lookup: Optional[Callable[[str, str], Optional[float]]] = None,
                 http_get: Optional[Callable[[str, dict], dict]] = None,
                 history_limit: Optional[int] = None):
        self.api_key = api_key
        # Caps team_history calls (each past match costs one /fixtures/statistics request).
        self.history_limit = history_limit
        self.tz = tz
        # Elo comes from our own ratings job (team_stats.elo_after). Without it: None, never a default.
        self.elo_lookup = elo_lookup or (lambda team_id, as_of: None)
        # Player importance from player_stats minutes share. Without it: None (impact not quantified).
        self.importance_lookup = importance_lookup or (lambda player_name, team_id: None)
        self._http_get = http_get      # injectable for tests (recorded API payloads)
        self._fixture_cache: dict[str, Optional[FixtureRef]] = {}
        self._raw_fixtures: dict[str, dict] = {}

    def _get(self, path: str, **params) -> list[dict]:
        params = {k: v for k, v in params.items() if v is not None}
        if self._http_get:
            body = self._http_get(path, params)
        else:
            import httpx
            r = httpx.get(f"{BASE_URL}{path}", params=params, headers={"x-apisports-key": self.api_key}, timeout=20)
            r.raise_for_status()
            body = r.json()
        if body.get("errors"):
            raise RuntimeError(f"API-Football error on {path}: {body['errors']}")
        return body.get("response", [])

    # ---------------------------------------------------------- lookups
    def competitions(self) -> list[CompetitionRef]:
        return [CompetitionRef(slug, name, country, kind) for slug, (_, name, country, kind) in LEAGUES.items()]

    def teams(self, competition_id: str) -> list[TeamRef]:
        lid = LEAGUES[competition_id][0]
        res = self._get("/teams", league=lid, season=datetime.utcnow().year)
        return [_team_ref(t, competition_id) for t in res]

    def team(self, team_id: str) -> Optional[TeamRef]:
        res = self._get("/teams", id=team_id)
        return _team_ref(res[0], "") if res and str(res[0]["team"]["id"]) == str(team_id) else None

    def fixture(self, fixture_id: str) -> Optional[FixtureRef]:
        if fixture_id not in self._fixture_cache:
            res = self._get("/fixtures", id=fixture_id)
            ref = parse_fixture(res[0], self.name) if res else None
            if res:
                self._raw_fixtures[fixture_id] = res[0]
            self._fixture_cache[fixture_id] = ref if ref and ref.id == str(fixture_id) else None
        return self._fixture_cache[fixture_id]

    def fixtures(self, day: date, competition_ids: list[str]) -> list[FixtureRef]:
        """One call for the date, then keep supported competitions only."""
        out = []
        for f in self._get("/fixtures", date=day.isoformat(), timezone=self.tz):
            ref = parse_fixture(f, self.name)
            if ref is None or (competition_ids and ref.competition_id not in competition_ids):
                continue
            self._fixture_cache[ref.id] = ref
            self._raw_fixtures[ref.id] = f
            out.append(ref)
        return sorted(out, key=lambda r: (r.kickoff is None, r.kickoff.isoformat() if r.kickoff else "", r.id))

    # ---------------------------------------------------------- statistics
    def team_history(self, team_id: str, before: datetime, limit: int = 20) -> list[TeamMatchRecord]:
        if self.history_limit:
            limit = min(limit, self.history_limit)
        out = []
        for f in self._recent_finished(team_id, before, limit):
            when = datetime.fromisoformat(f["fixture"]["date"])
            stats = self._get("/fixtures/statistics", fixture=f["fixture"]["id"])
            by_team = {s["team"]["id"]: s["statistics"] for s in stats}
            opp = f["teams"]["away" if str(f["teams"]["home"]["id"]) == str(team_id) else "home"]["id"]
            out.append(map_fixture_to_record(f, by_team, int(team_id), self.elo_lookup(str(opp), when)))
        return sorted(out, key=lambda r: r.date, reverse=True)

    def _recent_finished(self, team_id: str, before: datetime, limit: int) -> list[dict]:
        """The team's most recent finished matches before `before`.

        Paid plans: one /fixtures?team&last call. The free plan refuses the
        `last` parameter, so there: the team's fixtures for the current season
        (plus the previous one if the season is too young), finished only."""
        if self.api_key not in _NO_LAST_PARAM:
            try:
                res = self._get("/fixtures", team=team_id, last=limit, status="FT")
                return _finished_before(res, before)[:limit]
            except RuntimeError as e:
                if "last" not in str(e).lower():
                    raise
                _NO_LAST_PARAM.add(self.api_key)       # remember: don't spend a request on it again
        season = self._season_for(team_id, before)
        fin = _finished_before(self._get("/fixtures", team=team_id, season=season), before)
        if len(fin) < max(5, limit // 2):
            fin = _finished_before(fin + self._get("/fixtures", team=team_id, season=season - 1), before)
        return fin[:limit]

    def _season_for(self, team_id: str, when: datetime) -> int:
        """Season of the team's fixture on the slate (API-Football gives it); otherwise
        the European season containing `when` (Aug-May)."""
        for f in self._raw_fixtures.values():
            if str(team_id) in (str(f["teams"]["home"]["id"]), str(f["teams"]["away"]["id"])):
                season = (f.get("league") or {}).get("season")
                if season:
                    return int(season)
        return when.year if when.month >= 7 else when.year - 1

    def team_rating(self, team_id: str, as_of: datetime) -> Optional[float]:
        return self.elo_lookup(team_id, as_of)

    def team_style(self, team_id: str) -> StyleProfile:
        return StyleProfile()   # PPDA, field tilt etc. need event data (Opta/StatsBomb); left empty, not guessed

    def standings_position(self, team_id: str):
        return None, None

    def head_to_head(self, home_id: str, away_id: str, limit: int = 6) -> list[TeamMatchRecord]:
        # Display only (weight 0): if the plan refuses it, show no H2H rather than fail the match.
        try:
            if self.api_key in _NO_LAST_PARAM:
                res = self._get("/fixtures/headtohead", h2h=f"{home_id}-{away_id}")
            else:
                res = self._get("/fixtures/headtohead", h2h=f"{home_id}-{away_id}", last=limit, status="FT")
        except RuntimeError:
            return []
        res = _finished_before(res, datetime.now(timezone.utc))[:limit]
        return [map_fixture_to_record(f, {}, int(home_id), None) for f in res]

    def match_context(self, fixture: FixtureRef) -> MatchContext:
        return MatchContext()

    def referee(self, fixture_id: str) -> Optional[Referee]:
        return None   # cards-per-game needs our referee_stats aggregation; without it cards markets stay disabled

    def data_timestamp(self, fixture: FixtureRef) -> Optional[datetime]:
        from datetime import timezone
        return datetime.now(timezone.utc)

    def injury_provider(self) -> InjuryProvider:
        return ApiFootballInjuries(self)

    def lineup_provider(self) -> LineupProvider:
        return ApiFootballLineups(self)


def _team_ref(item: dict, competition_id: str) -> TeamRef:
    t = item["team"]
    return TeamRef(str(t["id"]), t["name"], t.get("code") or t["name"], competition_id, t.get("logo"))


class ApiFootballInjuries(InjuryProvider):
    name = "api_football"

    def __init__(self, p: ApiFootballProvider):
        self.p = p

    def absences(self, fixture: FixtureRef, team_id: str) -> list[Absence]:
        out = []
        for item in self.p._get("/injuries", fixture=fixture.id, team=team_id):
            pl = item["player"]
            status = "doubtful" if "Questionable" in (pl.get("type") or "") else "out"
            out.append(Absence(pl["name"], POSITION_MAP.get((pl.get("position") or "M")[0], "CM"),
                               self.p.importance_lookup(pl["name"], team_id), (pl.get("reason") or "unknown").lower(), status))
        return out


class ApiFootballLineups(LineupProvider):
    name = "api_football"

    def __init__(self, p: ApiFootballProvider):
        self.p = p

    def lineup(self, fixture: FixtureRef, team_id: str) -> LineupInfo:
        res = self.p._get("/fixtures/lineups", fixture=fixture.id, team=team_id)
        if not res:
            return LineupInfo(status="unknown")
        lu = res[0]
        return LineupInfo(status="confirmed", formation=lu.get("formation"),
                          starters=[p["player"]["name"] for p in lu.get("startXI", [])])


# Bet names as returned by /odds (verify with /odds/bets for your plan).
def _map_odds_value(bet: str, value: str) -> Optional[str]:
    v = value.strip()
    if bet == "Match Winner":
        return {"Home": "1X2:H", "Draw": "1X2:D", "Away": "1X2:A"}.get(v)
    if bet == "Double Chance":
        return {"Home/Draw": "DC:1X", "Draw/Away": "DC:X2", "Home/Away": "DC:12"}.get(v)
    if bet == "Both Teams Score":
        return {"Yes": "BTTS:yes", "No": "BTTS:no"}.get(v)
    if bet in ("Goals Over/Under", "Goals Over/Under First Half"):
        side, _, line = v.partition(" ")
        if side in ("Over", "Under") and line.replace(".", "", 1).isdigit():
            pre = "OU" if bet == "Goals Over/Under" else "FHOU"
            return f"{pre}:{float(line):g}:{side.lower()}"
    if bet == "First Half Winner":
        return {"Home": "FH1X2:H", "Draw": "FH1X2:D", "Away": "FH1X2:A"}.get(v)
    if bet == "Asian Handicap":
        side, _, line = v.partition(" ")
        try:
            ln = float(line)
        except ValueError:
            return None
        if side in ("Home", "Away"):
            return f"AH:{'H' if side == 'Home' else 'A'}:{ln:+g}" if ln != 0 else f"AH:{'H' if side == 'Home' else 'A'}:0"
    return None


class ApiFootballOdds(OddsProvider):
    """Pre-match odds by provider fixture id. Best price across bookmakers;
    opening/high/low come from our own odds_history (passed via history_loader)."""
    name = "api_football"

    def __init__(self, p: ApiFootballProvider, bookmakers: Optional[list[int]] = None, history_loader=None):
        self.p = p
        self.bookmakers = bookmakers
        self.history_loader = history_loader

    def odds(self, fixture: FixtureRef, home_name: str = "", away_name: str = "", alt_names: tuple = ()) -> dict[str, OddsSeries]:
        res = self.p._get("/odds", fixture=fixture.id)
        quotes: dict[str, list[OddsQuote]] = {}
        for item in res:
            if str(item.get("fixture", {}).get("id")) != str(fixture.id):
                continue   # never accept prices attached to another fixture
            ts = datetime.fromisoformat(item["update"]) if item.get("update") else datetime.utcnow()
            for bm in item.get("bookmakers", []):
                if self.bookmakers and bm["id"] not in self.bookmakers:
                    continue
                for bet in bm.get("bets", []):
                    for val in bet.get("values", []):
                        key = _map_odds_value(bet["name"], str(val["value"]))
                        try:
                            price = float(val["odd"])
                        except (TypeError, ValueError):
                            continue
                        if key and price > 1.0:
                            quotes.setdefault(key, []).append(OddsQuote(key, price, bm["name"], ts))
        hist = self.history_loader(fixture.id) if self.history_loader else {}
        out = {}
        for key, qs in quotes.items():
            best = max(qs, key=lambda q: q.odds)
            h = hist.get(key, [])
            out[key] = OddsSeries(key, h[0].odds if h else best.odds, best.odds, h)
        return out
