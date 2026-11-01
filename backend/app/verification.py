"""Fixture verification gate.

Nothing reaches the prediction engine unless it passes `verify_fixture`:
the fixture, both teams and the competition must be returned by the
configured football data provider, by provider id, with a kickoff on the
selected date and a status that is still playable. There is no fuzzy
matching anywhere in this module: identifiers and names are compared
exactly, and an unknown team is reported, never replaced.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

from app.providers.base import CompetitionRef, FixtureRef, FootballDataProvider, TeamRef

log = logging.getLogger("edgeboard.verification")

NO_VERIFIED_FIXTURE = "NO VERIFIED FIXTURE"
TEAM_NOT_VERIFIED = "TEAM NOT VERIFIED"
ODDS_UNAVAILABLE = "ODDS UNAVAILABLE"


class Status(str, Enum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    ODDS_UNAVAILABLE = "ODDS UNAVAILABLE"
    DATA_INCOMPLETE = "DATA INCOMPLETE"
    POSTPONED = "POSTPONED"
    CANCELLED = "CANCELLED"


PLAYABLE = {"scheduled"}
POSTPONED_STATES = {"postponed", "suspended", "interrupted"}
CANCELLED_STATES = {"cancelled", "abandoned", "awarded", "walkover"}


@dataclass(frozen=True)
class VerifiedFixture:
    """Proof that a fixture passed the gate. Only `verify_fixture` builds one."""
    fixture: FixtureRef
    competition: CompetitionRef
    home: TeamRef
    away: TeamRef
    provider: str
    verified_at: datetime


@dataclass
class VerificationResult:
    fixture_id: str
    status: Status
    messages: list[str] = field(default_factory=list)
    verified: Optional[VerifiedFixture] = None
    listed: Optional[FixtureRef] = None     # what the provider returned, for display when verification fails

    @property
    def ok(self) -> bool:
        return self.verified is not None

    def to_dict(self) -> dict:
        v = self.verified
        return {
            "fixture_id": self.fixture_id, "status": self.status.value, "messages": self.messages,
            "fixture": None if v is None else {
                "provider": v.provider, "provider_fixture_id": v.fixture.id,
                "kickoff": v.fixture.kickoff.isoformat() if v.fixture.kickoff else None,
                "status_code": v.fixture.status_code, "competition": asdict(v.competition),
                "home": asdict(v.home), "away": asdict(v.away),
                "verified_at": v.verified_at.isoformat(),
            },
            "listed": None if self.listed is None else {
                "provider_fixture_id": self.listed.id, "competition_id": self.listed.competition_id,
                "kickoff": self.listed.kickoff.isoformat() if self.listed.kickoff else None,
                "home": {"id": self.listed.home_id, "name": self.listed.home_name},
                "away": {"id": self.listed.away_id, "name": self.listed.away_name},
                "status_code": self.listed.status_code,
            },
        }


class TeamLookupCache:
    """Team lookups cached on each provider instance (so a cached answer is
    always from the same source and transport that is being verified)."""

    def get(self, provider: FootballDataProvider, team_id: str) -> Optional[TeamRef]:
        cache = provider.__dict__.setdefault("_verified_team_lookups", {})
        if team_id not in cache:
            cache[team_id] = provider.team(team_id)
        return cache[team_id]


_TEAMS = TeamLookupCache()


def _fail(fixture_id: str, status: Status, msg: str, ref: Optional[FixtureRef] = None) -> VerificationResult:
    log.warning("fixture %s rejected: %s", fixture_id, msg)
    return VerificationResult(fixture_id, status, [msg], listed=ref)


def verify_fixture(provider: FootballDataProvider, fixture_id: Optional[str], selected_date: date,
                   whitelist: list[str], tz: str = "UTC", listed: Optional[FixtureRef] = None,
                   teams: TeamLookupCache = _TEAMS) -> VerificationResult:
    """Verify one fixture by provider id. `listed` may carry the fixture as
    returned by the provider's date listing (saves one API call); it is
    still checked field by field."""
    fid = (fixture_id or "").strip()
    if not fid:
        return _fail("", Status.UNVERIFIED, f"{NO_VERIFIED_FIXTURE}: no provider fixture id supplied")
    ref = listed if listed is not None and listed.id == fid else provider.fixture(fid)
    if ref is None or ref.id != fid:
        return _fail(fid, Status.UNVERIFIED, f"{NO_VERIFIED_FIXTURE}: provider returned no fixture with id {fid}")
    if ref.status in POSTPONED_STATES:
        return _fail(fid, Status.POSTPONED, ref=ref, msg=f"Fixture {fid} is {ref.status} (provider status {ref.status_code})")
    if ref.status in CANCELLED_STATES:
        return _fail(fid, Status.CANCELLED, ref=ref, msg=f"Fixture {fid} is {ref.status} (provider status {ref.status_code})")
    if ref.kickoff is None:
        return _fail(fid, Status.UNVERIFIED, ref=ref, msg=f"{NO_VERIFIED_FIXTURE}: fixture {fid} has no kickoff time")
    local_day = ref.kickoff.astimezone(ZoneInfo(tz)).date()
    if local_day != selected_date:
        return _fail(fid, Status.UNVERIFIED, ref=ref, msg=
                     f"{NO_VERIFIED_FIXTURE}: fixture {fid} kicks off on {local_day.isoformat()}, not {selected_date.isoformat()}")
    if ref.status not in PLAYABLE:
        return _fail(fid, Status.UNVERIFIED, ref=ref, msg=
                     f"{NO_VERIFIED_FIXTURE}: fixture {fid} is {ref.status} (provider status {ref.status_code}); "
                     "only scheduled fixtures are analysed")
    comps = {c.id: c for c in provider.competitions()}
    comp = comps.get(ref.competition_id)
    if comp is None or ref.competition_id not in whitelist:
        return _fail(fid, Status.UNVERIFIED, ref=ref, msg=f"Competition {ref.competition_id!r} is not supported")
    for side, team_id, listed_name in (("home", ref.home_id, ref.home_name), ("away", ref.away_id, ref.away_name)):
        if not team_id:
            return _fail(fid, Status.UNVERIFIED, ref=ref, msg=f"{TEAM_NOT_VERIFIED}: {listed_name or side + ' team'} (no provider team id)")
        t = teams.get(provider, team_id)
        if t is None:
            return _fail(fid, Status.UNVERIFIED, ref=ref, msg=f"{TEAM_NOT_VERIFIED}: {listed_name or team_id}")
        if listed_name is not None and listed_name != t.name:
            return _fail(fid, Status.UNVERIFIED, ref=ref, msg=
                         f"{TEAM_NOT_VERIFIED}: {listed_name} (provider team {team_id} is named {t.name!r})")
    home, away = teams.get(provider, ref.home_id), teams.get(provider, ref.away_id)
    if home.id == away.id:   # type: ignore[union-attr]
        return _fail(fid, Status.UNVERIFIED, ref=ref, msg=f"{NO_VERIFIED_FIXTURE}: home and away team ids are identical")
    v = VerifiedFixture(ref, comp, home, away, provider.name, datetime.now(timezone.utc))  # type: ignore[arg-type]
    return VerificationResult(fid, Status.VERIFIED, [], v, listed=ref)


def data_status(fx, min_matches: int = 5) -> tuple[Status, list[str]]:
    """Second gate, after statistics and odds are fetched for a verified fixture."""
    msgs = []
    n_h, n_a = len(fx.home.recent), len(fx.away.recent)
    if min(n_h, n_a) < min_matches:
        msgs.append(f"Only {min(n_h, n_a)} past matches with statistics for one side (need {min_matches})")
        return Status.DATA_INCOMPLETE, msgs
    if not fx.odds:
        msgs.append(f"{ODDS_UNAVAILABLE}: the odds provider returned no prices for this fixture")
        return Status.ODDS_UNAVAILABLE, msgs
    return Status.VERIFIED, msgs


def verify_historical(provider: FootballDataProvider, ref: FixtureRef, whitelist: list[str],
                      teams: TeamLookupCache = _TEAMS) -> VerificationResult:
    """Gate for finished fixtures used in backtests: same identity checks,
    but the fixture must be finished rather than scheduled."""
    fid = ref.id
    if ref.status != "finished" or ref.kickoff is None:
        return _fail(fid, Status.UNVERIFIED, f"{NO_VERIFIED_FIXTURE}: {fid} is not a finished fixture with a kickoff")
    comps = {c.id: c for c in provider.competitions()}
    comp = comps.get(ref.competition_id)
    if comp is None or ref.competition_id not in whitelist:
        return _fail(fid, Status.UNVERIFIED, f"Competition {ref.competition_id!r} is not supported")
    found = []
    for team_id, listed_name in ((ref.home_id, ref.home_name), (ref.away_id, ref.away_name)):
        t = teams.get(provider, team_id) if team_id else None
        if t is None or (listed_name is not None and listed_name != t.name):
            return _fail(fid, Status.UNVERIFIED, f"{TEAM_NOT_VERIFIED}: {listed_name or team_id}")
        found.append(t)
    return VerificationResult(fid, Status.VERIFIED, [],
                              VerifiedFixture(ref, comp, found[0], found[1], provider.name, datetime.now(timezone.utc)))
