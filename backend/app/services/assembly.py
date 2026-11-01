"""Assemble provider data for a *verified* fixture into engine input.

The only accepted input is a VerifiedFixture produced by app.verification;
teams, competition and kickoff are taken from that verification, and every
statistic and price is fetched from the providers by provider id.
"""
from __future__ import annotations

from datetime import datetime

from app.engine.types import FixtureInput, LineupInfo, TeamProfile
from app.providers.registry import Providers
from app.verification import VerifiedFixture


def build_fixture_input(v: VerifiedFixture, p: Providers) -> FixtureInput:
    if not isinstance(v, VerifiedFixture):
        raise TypeError("build_fixture_input requires a VerifiedFixture from app.verification")
    fx = v.fixture
    teams = {}
    for side, ref in (("home", v.home), ("away", v.away)):
        pos, size = p.football.standings_position(ref.id)
        teams[side] = TeamProfile(
            team_id=ref.id, name=ref.name, short_name=ref.short_name,
            elo=p.football.team_rating(ref.id, fx.kickoff),
            recent=p.football.team_history(ref.id, fx.kickoff, 20),
            style=p.football.team_style(ref.id), league_position=pos, league_size=size, badge_url=ref.badge_url,
        )
    ctx = p.football.match_context(fx)
    ctx.neutral_venue = fx.venue_neutral
    _derive_rest_days(ctx, teams["home"], teams["away"], fx.kickoff)
    injuries_ok = p.injuries is not None and p.injuries.available
    return FixtureInput(
        fixture_id=fx.id, competition_id=v.competition.id, competition_name=v.competition.name,
        competition_type=v.competition.type, kickoff=fx.kickoff, home=teams["home"], away=teams["away"], context=ctx,
        absences_home=p.injuries.absences(fx, v.home.id) if injuries_ok else [],
        absences_away=p.injuries.absences(fx, v.away.id) if injuries_ok else [],
        injury_feed_available=injuries_ok,
        lineup_home=p.lineups.lineup(fx, v.home.id) if p.lineups else LineupInfo(status="unknown"),
        lineup_away=p.lineups.lineup(fx, v.away.id) if p.lineups else LineupInfo(status="unknown"),
        referee=p.football.referee(fx.id),
        odds=p.odds.odds(fx, v.home.name, v.away.name, (v.home.short_name, v.away.short_name)),
        h2h=p.football.head_to_head(v.home.id, v.away.id),
        league_avg_goals=p.football.league_average_goals(v.competition.id, fx.kickoff),
        data_updated_at=p.football.data_timestamp(fx), status=fx.status,
        provenance={"provider": v.provider, "fixture_id": fx.id, "verified_at": v.verified_at.isoformat(),
                    "home_team_id": v.home.id, "away_team_id": v.away.id, "competition_id": v.competition.id},
    )


def _derive_rest_days(ctx, home: TeamProfile, away: TeamProfile, kickoff: datetime) -> None:
    if ctx.rest_days_home is None and home.recent:
        ctx.rest_days_home = (kickoff - home.recent[0].date).days
    if ctx.rest_days_away is None and away.recent:
        ctx.rest_days_away = (kickoff - away.recent[0].date).days
