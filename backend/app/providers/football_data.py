"""football-data.org (v4) adapter: free-tier fixtures, results and teams.

Free tier: 12 competitions, current season, 10 requests a minute, no xG, shot
statistics, lineups or injuries. Those inputs are left empty (never guessed);
the engine lowers data quality accordingly.

Request plan (all cached, see TTL):
  /matches?dateFrom&dateTo             today's fixtures, every free competition (1 call)
  /competitions/{code}/matches         the whole current season of one competition (1 call each):
                                       results for team form, team verification, league averages, H2H
  /matches/{id}                        direct fixture lookup (only when a match is opened by id)
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app.engine.types import MatchContext, Referee, StyleProfile, TeamMatchRecord

from .base import CompetitionRef, FixtureRef, FootballDataProvider, TeamRef
from .web_cache import SourceError, WebCache

BASE_URL = "https://api.football-data.org/v4"

# our slug -> (football-data code, display name, country, type)
COMPETITIONS = {
    "epl": ("PL", "Premier League", "England", "league"),
    "laliga": ("PD", "La Liga", "Spain", "league"),
    "seriea": ("SA", "Serie A", "Italy", "league"),
    "bundesliga": ("BL1", "Bundesliga", "Germany", "league"),
    "ligue1": ("FL1", "Ligue 1", "France", "league"),
    "eredivisie": ("DED", "Eredivisie", "Netherlands", "league"),
    "primeira": ("PPL", "Primeira Liga", "Portugal", "league"),
    "championship": ("ELC", "Championship", "England", "league"),
    "brasileirao": ("BSA", "Brasileirão Série A", "Brazil", "league"),
    "ucl": ("CL", "UEFA Champions League", "Europe", "continental"),
}
CODE_TO_SLUG = {v[0]: k for k, v in COMPETITIONS.items()}

STATUS = {
    "SCHEDULED": "scheduled", "TIMED": "scheduled",
    "IN_PLAY": "live", "PAUSED": "live", "LIVE": "live",
    "FINISHED": "finished", "AWARDED": "awarded",
    "POSTPONED": "postponed", "SUSPENDED": "suspended", "CANCELLED": "cancelled",
}


def _ttl(path: str, params: dict) -> int:
    if path.startswith("/competitions/"):
        return 3 * 3600          # season results: new results appear after each match day
    if path == "/matches":
        return 30 * 60           # today's listing: status changes (postponed, kicked off)
    return 15 * 60               # single match lookup


def _when(m: dict) -> Optional[datetime]:
    d = m.get("utcDate")
    return datetime.fromisoformat(d.replace("Z", "+00:00")) if d else None


def parse_match(m: dict) -> Optional[FixtureRef]:
    """One football-data match -> FixtureRef. None for competitions we don't map."""
    slug = CODE_TO_SLUG.get((m.get("competition") or {}).get("code"))
    if slug is None:
        return None
    h, a = m.get("homeTeam") or {}, m.get("awayTeam") or {}
    code = m.get("status")
    return FixtureRef(
        id=str(m["id"]), competition_id=slug, kickoff=_when(m),
        home_id=str(h["id"]) if h.get("id") else "", away_id=str(a["id"]) if a.get("id") else "",
        status=STATUS.get(code, "unknown"), status_code=code,
        home_name=h.get("name"), away_name=a.get("name"), provider="football_data",
    )


def _score(m: dict) -> tuple[Optional[int], Optional[int]]:
    ft = ((m.get("score") or {}).get("fullTime")) or {}
    return ft.get("home"), ft.get("away")


class FootballDataOrgProvider(FootballDataProvider):
    name = "football_data"

    def __init__(self, token: str, tz: str = "UTC", http: Optional[WebCache] = None,
                 elo_lookup=None):
        self.tz = tz
        self.http = http or WebCache(
            "football-data.org", BASE_URL, {"X-Auth-Token": token}, _ttl, min_interval=6.5,
            cache_path=os.getenv("FOOTBALL_DATA_CACHE_PATH", "/tmp/edgeboard-football-data.json"),
            remaining_header="X-Requests-Available-Minute")
        self.elo_lookup = elo_lookup or (lambda team_id, as_of: None)
        self._listed: dict[str, dict] = {}          # fixture id -> raw match from the date listing

    # ---------------------------------------------------------- raw data
    def _season(self, slug: str) -> list[dict]:
        code = COMPETITIONS[slug][0]
        body = self.http.get(f"/competitions/{code}/matches")
        return body.get("matches", []) if isinstance(body, dict) else []

    def _seasons_loaded_first(self) -> list[str]:
        """Competitions to search for a team: those on today's slate first."""
        on_slate = []
        for m in self._listed.values():
            s = CODE_TO_SLUG.get((m.get("competition") or {}).get("code"))
            if s and s not in on_slate:
                on_slate.append(s)
        return on_slate + [s for s in COMPETITIONS if s not in on_slate]

    def _team_matches(self, team_id: str) -> list[dict]:
        """Every match of the team in the competition(s) where it appears, current season."""
        out = []
        for slug in self._comps_of_team(team_id):
            out += [m for m in self._season(slug)
                    if str((m.get("homeTeam") or {}).get("id")) == team_id
                    or str((m.get("awayTeam") or {}).get("id")) == team_id]
        return out

    def _comps_of_team(self, team_id: str) -> list[str]:
        comps = []
        for m in self._listed.values():
            if team_id in (str(m["homeTeam"].get("id")), str(m["awayTeam"].get("id"))):
                s = CODE_TO_SLUG.get(m["competition"]["code"])
                if s:
                    comps.append(s)
        # Continental fixtures: the team's domestic league holds most of its results.
        if not comps or all(COMPETITIONS[c][3] != "league" for c in comps):
            for slug in [s for s in self._seasons_loaded_first() if COMPETITIONS[s][3] == "league"]:
                if any(team_id in (str((m.get("homeTeam") or {}).get("id")), str((m.get("awayTeam") or {}).get("id")))
                       for m in self._season(slug)):
                    comps.append(slug)
                    break
        return list(dict.fromkeys(comps))

    # ---------------------------------------------------------- lookups
    def competitions(self) -> list[CompetitionRef]:
        return [CompetitionRef(slug, name, country, kind) for slug, (_, name, country, kind) in COMPETITIONS.items()]

    def teams(self, competition_id: str) -> list[TeamRef]:
        seen: dict[str, TeamRef] = {}
        for m in self._season(competition_id):
            for side in ("homeTeam", "awayTeam"):
                t = m.get(side) or {}
                if t.get("id"):
                    seen[str(t["id"])] = TeamRef(str(t["id"]), t.get("name"), t.get("shortName") or t.get("name"),
                                                 competition_id, t.get("crest"))
        return list(seen.values())

    def team(self, team_id: str) -> Optional[TeamRef]:
        """A team exists if the provider lists it, with this id, in a supported
        competition's current-season fixtures. Name as the provider gives it."""
        for slug in self._seasons_loaded_first():
            for m in self._season(slug):
                for side in ("homeTeam", "awayTeam"):
                    t = m.get(side) or {}
                    if str(t.get("id")) == str(team_id):
                        return TeamRef(str(t["id"]), t.get("name"), t.get("shortName") or t.get("name"),
                                       slug, t.get("crest"))
        return None

    def fixture(self, fixture_id: str) -> Optional[FixtureRef]:
        if fixture_id in self._listed:
            return parse_match(self._listed[fixture_id])
        try:
            m = self.http.get(f"/matches/{int(fixture_id)}")
        except (ValueError, SourceError):
            return None
        ref = parse_match(m) if isinstance(m, dict) and m.get("id") else None
        if ref and ref.id == str(fixture_id):
            self._listed[ref.id] = m
            return ref
        return None

    def result(self, fixture_id: str):
        """Final score for settlement, read fresh from the provider. Returns a MatchResult,
        "void" (cancelled/awarded), or None while the match is not finished."""
        from app.engine.tracking import MatchResult
        m = self.http.get(f"/matches/{int(fixture_id)}", max_age=10 * 60)
        if not isinstance(m, dict) or str(m.get("id")) != str(fixture_id):
            return None
        if m.get("status") in ("CANCELLED", "AWARDED"):
            return "void"
        if m.get("status") != "FINISHED":
            return None
        sc = m.get("score") or {}
        ft, ht = sc.get("fullTime") or {}, sc.get("halfTime") or {}
        if sc.get("duration") not in (None, "REGULAR"):
            rt = sc.get("regularTime") or {}       # bets settle on 90 minutes
            if rt.get("home") is not None:
                ft = rt
        if None in (ft.get("home"), ft.get("away"), ht.get("home"), ht.get("away")):
            return None
        return MatchResult(int(ft["home"]), int(ft["away"]), int(ht["home"]), int(ht["away"]))

    def fixtures(self, day: date, competition_ids: list[str]) -> list[FixtureRef]:
        # Window wider than the day (UTC vs local time); verification keeps only the selected local date.
        body = self.http.get("/matches", {"dateFrom": (day - timedelta(days=1)).isoformat(),
                                          "dateTo": (day + timedelta(days=1)).isoformat()})
        out = []
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(self.tz)
        for m in body.get("matches", []):
            ref = parse_match(m)
            if ref is None or (competition_ids and ref.competition_id not in competition_ids):
                continue
            if ref.kickoff is None or ref.kickoff.astimezone(tz).date() != day:
                continue
            self._listed[ref.id] = m
            out.append(ref)
        return sorted(out, key=lambda r: (r.kickoff.isoformat(), r.id))

    # ---------------------------------------------------------- statistics
    def team_history(self, team_id: str, before: datetime, limit: int = 20) -> list[TeamMatchRecord]:
        recs = []
        for m in self._team_matches(str(team_id)):
            when = _when(m)
            gh, ga = _score(m)
            if m.get("status") != "FINISHED" or when is None or when >= before or gh is None or ga is None:
                continue
            home = str(m["homeTeam"]["id"]) == str(team_id)
            opp = str(m["awayTeam" if home else "homeTeam"]["id"])
            recs.append(TeamMatchRecord(
                date=when, opponent_id=opp, opponent_rating=self.elo_lookup(opp, when), venue="H" if home else "A",
                competition=CODE_TO_SLUG.get(m["competition"]["code"], m["competition"]["code"]),
                goals_for=gh if home else ga, goals_against=ga if home else gh))
        recs.sort(key=lambda r: r.date, reverse=True)
        return list({(r.date, r.opponent_id): r for r in recs}.values())[:limit]

    def league_average_goals(self, competition_id: str, as_of: Optional[datetime] = None) -> Optional[float]:
        if COMPETITIONS.get(competition_id, ("", "", "", ""))[3] != "league":
            return None
        goals, games = 0, 0
        for m in self._season(competition_id):
            gh, ga = _score(m)
            when = _when(m)
            if m.get("status") == "FINISHED" and gh is not None and ga is not None and (as_of is None or (when and when < as_of)):
                goals += gh + ga
                games += 1
        return round(goals / (2 * games), 3) if games >= 30 else None   # per team per match

    def head_to_head(self, home_id: str, away_id: str, limit: int = 6) -> list[TeamMatchRecord]:
        return [r for r in self.team_history(home_id, datetime.now(timezone.utc), 50) if r.opponent_id == str(away_id)][:limit]

    def team_rating(self, team_id: str, as_of: datetime) -> Optional[float]:
        return self.elo_lookup(team_id, as_of)

    def team_style(self, team_id: str) -> StyleProfile:
        return StyleProfile()

    def standings_position(self, team_id: str):
        return None, None

    def match_context(self, fixture: FixtureRef) -> MatchContext:
        return MatchContext()

    def referee(self, fixture_id: str) -> Optional[Referee]:
        return None

    def data_timestamp(self, fixture: FixtureRef) -> Optional[datetime]:
        return datetime.now(timezone.utc)
