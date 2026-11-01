"""Provider-agnostic domain types consumed by the engine.

Providers (API-Football, Sportmonks, Opta...) map their payloads into these
types. Optional fields are None when the provider has no data: the engine
treats None as *missing* and lowers data quality / blocks markets instead of
substituting a guess.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class TeamMatchRecord:
    date: datetime
    opponent_id: str
    opponent_rating: Optional[float]  # opponent Elo at the time of the match; None if no rating store
    venue: str                      # "H" or "A"
    competition: str
    goals_for: int
    goals_against: int
    xg_for: Optional[float] = None
    xg_against: Optional[float] = None
    npxg_for: Optional[float] = None
    shots: Optional[int] = None
    shots_on_target: Optional[int] = None
    big_chances: Optional[int] = None
    big_chances_conceded: Optional[int] = None
    corners_for: Optional[int] = None
    corners_against: Optional[int] = None
    cards: Optional[int] = None

    @property
    def points(self) -> int:
        if self.goals_for > self.goals_against:
            return 3
        return 1 if self.goals_for == self.goals_against else 0


@dataclass
class StyleProfile:
    """Tactical/style metrics. Every field optional."""
    formation: Optional[str] = None
    possession: Optional[float] = None          # %
    field_tilt: Optional[float] = None          # %
    ppda: Optional[float] = None                # lower = more pressing
    press_resistance_pct: Optional[float] = None  # percentile 0-100, retention under pressure
    set_piece_xg_share: Optional[float] = None  # share of xG from set pieces
    set_piece_xga_per_game: Optional[float] = None
    counter_xg_per_game: Optional[float] = None
    transition_xga_per_game: Optional[float] = None
    defensive_line_height: Optional[float] = None  # metres from own goal
    crosses_per_game: Optional[float] = None
    wide_attack_share: Optional[float] = None   # 0-1


@dataclass
class TeamProfile:
    team_id: str
    name: str
    short_name: str
    elo: Optional[float]            # None when no rating source exists (never defaulted)
    recent: list[TeamMatchRecord] = field(default_factory=list)  # newest first
    style: StyleProfile = field(default_factory=StyleProfile)
    league_position: Optional[int] = None
    league_size: Optional[int] = None
    badge_url: Optional[str] = None


@dataclass
class Absence:
    player: str
    position: str           # GK, CB, FB, DM, CM, AM, W, ST
    importance: Optional[float]  # 0-1 share of team minutes x contribution; None if minutes data missing
    reason: str             # injury, suspension, international, unknown
    status: str = "out"     # out | doubtful


@dataclass
class LineupInfo:
    status: str = "unknown"   # confirmed | predicted | unknown
    formation: Optional[str] = None
    starters: list[str] = field(default_factory=list)
    rotation_risk: Optional[float] = None   # 0-1 estimate from congestion/provider


@dataclass
class MatchContext:
    rest_days_home: Optional[int] = None
    rest_days_away: Optional[int] = None
    european_midweek_home: bool = False
    european_midweek_away: bool = False
    travel_km_away: Optional[float] = None
    derby: bool = False
    manager_change_days_home: Optional[int] = None   # days since appointment
    manager_change_days_away: Optional[int] = None
    knockout: bool = False
    leg: Optional[int] = None            # 1 or 2 for two-legged ties
    aggregate_home: Optional[int] = None
    aggregate_away: Optional[int] = None
    stakes_home: Optional[str] = None    # title | ucl | relegation | mid
    stakes_away: Optional[str] = None
    neutral_venue: bool = False


@dataclass
class Referee:
    name: str
    matches: int
    cards_per_game: float


@dataclass
class OddsQuote:
    market_key: str          # see engine.markets for key grammar
    odds: float
    bookmaker: str
    captured_at: datetime


@dataclass
class OddsSeries:
    market_key: str
    opening: float
    current: float
    history: list[OddsQuote] = field(default_factory=list)  # oldest first

    @property
    def high(self) -> float:
        return max([q.odds for q in self.history] + [self.opening, self.current])

    @property
    def low(self) -> float:
        return min([q.odds for q in self.history] + [self.opening, self.current])


@dataclass
class FixtureInput:
    fixture_id: str
    competition_id: str
    competition_name: str
    competition_type: str     # league | cup | continental
    kickoff: datetime
    home: TeamProfile
    away: TeamProfile
    context: MatchContext = field(default_factory=MatchContext)
    absences_home: list[Absence] = field(default_factory=list)
    absences_away: list[Absence] = field(default_factory=list)
    injury_feed_available: bool = False
    lineup_home: LineupInfo = field(default_factory=LineupInfo)
    lineup_away: LineupInfo = field(default_factory=LineupInfo)
    referee: Optional[Referee] = None
    odds: dict[str, OddsSeries] = field(default_factory=dict)
    h2h: list[TeamMatchRecord] = field(default_factory=list)  # from home side's view
    league_avg_goals: Optional[float] = None   # avg goals (or xG) per team per match in this competition
    data_updated_at: Optional[datetime] = None
    status: str = "scheduled"   # scheduled | live | finished
    # Set only by services.assembly from a VerifiedFixture: provider name,
    # provider fixture id and verification time. The engine refuses inputs without it.
    provenance: Optional[dict] = None
