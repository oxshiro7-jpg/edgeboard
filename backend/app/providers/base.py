"""Provider interfaces. Concrete adapters (API-Football, Sportmonks, The Odds
API, Opta, StatsBomb, Sportradar...) implement these and map their payloads
into engine types. Nothing outside app/providers knows which vendor is used.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from app.engine.types import (Absence, LineupInfo, MatchContext, OddsSeries, Referee, StyleProfile,
                              TeamMatchRecord)


@dataclass
class CompetitionRef:
    id: str
    name: str
    country: str
    type: str          # league | cup | continental


@dataclass
class TeamRef:
    id: str
    name: str
    short_name: str
    competition_id: str
    badge_url: Optional[str] = None


@dataclass
class FixtureRef:
    """A fixture exactly as the provider returned it. Never constructed from
    user text or model output: only provider adapters create these."""
    id: str                         # provider fixture id
    competition_id: str             # our competition slug (mapped from the provider league id)
    kickoff: Optional[datetime]
    home_id: str                    # provider team ids
    away_id: str
    status: str = "scheduled"       # scheduled | live | finished | postponed | cancelled | abandoned | unknown
    venue_neutral: bool = False
    home_name: Optional[str] = None  # names as given in the fixture payload
    away_name: Optional[str] = None
    status_code: Optional[str] = None  # raw provider status (e.g. NS, PST, CANC)
    provider: str = ""


class FootballDataProvider(ABC):
    name: str = "base"

    @abstractmethod
    def fixture(self, fixture_id: str) -> Optional[FixtureRef]:
        """Direct lookup by provider fixture id; None if the provider does not return it."""

    @abstractmethod
    def team(self, team_id: str) -> Optional[TeamRef]:
        """Direct lookup by provider team id; None if the provider does not return it."""

    @abstractmethod
    def competitions(self) -> list[CompetitionRef]: ...

    @abstractmethod
    def teams(self, competition_id: str) -> list[TeamRef]: ...

    @abstractmethod
    def fixtures(self, day: date, competition_ids: list[str]) -> list[FixtureRef]: ...

    @abstractmethod
    def team_history(self, team_id: str, before: datetime, limit: int = 20) -> list[TeamMatchRecord]:
        """Most recent first. xG/shots fields left None when the vendor lacks them."""

    @abstractmethod
    def team_rating(self, team_id: str, as_of: datetime) -> float:
        """Elo (or vendor power rating mapped to the Elo scale)."""

    @abstractmethod
    def team_style(self, team_id: str) -> StyleProfile: ...

    @abstractmethod
    def standings_position(self, team_id: str) -> tuple[Optional[int], Optional[int]]: ...

    @abstractmethod
    def head_to_head(self, home_id: str, away_id: str, limit: int = 6) -> list[TeamMatchRecord]: ...

    @abstractmethod
    def match_context(self, fixture: FixtureRef) -> MatchContext: ...

    @abstractmethod
    def referee(self, fixture_id: str) -> Optional[Referee]: ...

    def last_updated(self) -> Optional[datetime]:
        return None

    def data_timestamp(self, fixture: FixtureRef) -> Optional[datetime]:
        """When the data used for this fixture was last refreshed."""
        return self.last_updated()

    def league_average_goals(self, competition_id: str, as_of: Optional[datetime] = None) -> Optional[float]:
        """Average goals (or xG) per team per match, using only matches before
        as_of (no look-ahead in backtests). None if unknown."""
        return None


class OddsProvider(ABC):
    name: str = "base"

    @abstractmethod
    def odds(self, fixture: FixtureRef, home_name: str, away_name: str, alt_names: tuple = ()) -> dict[str, OddsSeries]:
        """Return series keyed by engine market keys (see engine.markets).
        Team names are passed because odds feeds rarely share fixture ids
        with stats feeds; adapters match events on normalised names."""


class InjuryProvider(ABC):
    name: str = "base"
    available: bool = True

    @abstractmethod
    def absences(self, fixture: FixtureRef, team_id: str) -> list[Absence]: ...


class LineupProvider(ABC):
    name: str = "base"

    @abstractmethod
    def lineup(self, fixture: FixtureRef, team_id: str) -> LineupInfo: ...


@dataclass
class Weather:
    temperature_c: float
    wind_kph: float
    precipitation_mm: float


class WeatherProvider(ABC):
    name: str = "base"

    @abstractmethod
    def forecast(self, fixture: FixtureRef) -> Optional[Weather]: ...
