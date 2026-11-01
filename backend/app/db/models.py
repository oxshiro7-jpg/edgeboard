"""PostgreSQL schema (SQLAlchemy 2.0). Time-series tables (odds_history,
bankroll_history) are converted to TimescaleDB hypertables by
migrations/001_timescale.sql when the extension is available.

Immutability: model_predictions and market_predictions are append-only.
A recalculation (e.g. after confirmed lineups) inserts a new row that points
at the previous one via `supersedes_id`; nothing is updated in place.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, Numeric, String,
                        Text, UniqueConstraint, func)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ----------------------------------------------------------------- reference

class Competition(TimestampMixin, Base):
    __tablename__ = "competitions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)          # our slug, e.g. "epl"
    name: Mapped[str] = mapped_column(String(120))
    country: Mapped[str] = mapped_column(String(80))
    type: Mapped[str] = mapped_column(String(20))                           # league | cup | continental
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)            # admin whitelist
    provider_ids: Mapped[dict] = mapped_column(JSON, default=dict)          # {"api_football": 39, ...}
    avg_goals_per_team: Mapped[Optional[float]] = mapped_column(Float)


class Team(TimestampMixin, Base):
    __tablename__ = "teams"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    short_name: Mapped[str] = mapped_column(String(12))
    country: Mapped[Optional[str]] = mapped_column(String(80))
    badge_url: Mapped[Optional[str]] = mapped_column(Text)
    aliases: Mapped[list] = mapped_column(JSON, default=list)               # names used by odds feeds
    provider_ids: Mapped[dict] = mapped_column(JSON, default=dict)


class Player(TimestampMixin, Base):
    __tablename__ = "players"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    team_id: Mapped[Optional[str]] = mapped_column(ForeignKey("teams.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    position: Mapped[Optional[str]] = mapped_column(String(4))
    provider_ids: Mapped[dict] = mapped_column(JSON, default=dict)


class Fixture(TimestampMixin, Base):
    __tablename__ = "fixtures"
    id: Mapped[str] = mapped_column(String(60), primary_key=True)
    competition_id: Mapped[str] = mapped_column(ForeignKey("competitions.id"), index=True)
    season: Mapped[Optional[str]] = mapped_column(String(10))
    kickoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    home_team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    away_team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="scheduled")   # scheduled | live | finished | postponed
    home_goals: Mapped[Optional[int]] = mapped_column(Integer)
    away_goals: Mapped[Optional[int]] = mapped_column(Integer)
    home_goals_ht: Mapped[Optional[int]] = mapped_column(Integer)
    away_goals_ht: Mapped[Optional[int]] = mapped_column(Integer)
    referee_name: Mapped[Optional[str]] = mapped_column(String(120))
    neutral_venue: Mapped[bool] = mapped_column(Boolean, default=False)
    context: Mapped[dict] = mapped_column(JSON, default=dict)               # leg, aggregate, derby, stakes...
    provider_ids: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = (Index("ix_fixtures_comp_kickoff", "competition_id", "kickoff"),)


class TeamStats(Base):
    """One row per team per fixture (the TeamMatchRecord the engine consumes)."""
    __tablename__ = "team_stats"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[str] = mapped_column(ForeignKey("fixtures.id"), index=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    venue: Mapped[str] = mapped_column(String(1))
    goals_for: Mapped[int] = mapped_column(Integer)
    goals_against: Mapped[int] = mapped_column(Integer)
    xg_for: Mapped[Optional[float]] = mapped_column(Float)
    xg_against: Mapped[Optional[float]] = mapped_column(Float)
    npxg_for: Mapped[Optional[float]] = mapped_column(Float)
    shots: Mapped[Optional[int]] = mapped_column(Integer)
    shots_on_target: Mapped[Optional[int]] = mapped_column(Integer)
    big_chances: Mapped[Optional[int]] = mapped_column(Integer)
    big_chances_conceded: Mapped[Optional[int]] = mapped_column(Integer)
    possession: Mapped[Optional[float]] = mapped_column(Float)
    field_tilt: Mapped[Optional[float]] = mapped_column(Float)
    ppda: Mapped[Optional[float]] = mapped_column(Float)
    corners_for: Mapped[Optional[int]] = mapped_column(Integer)
    corners_against: Mapped[Optional[int]] = mapped_column(Integer)
    cards: Mapped[Optional[int]] = mapped_column(Integer)
    elo_before: Mapped[Optional[float]] = mapped_column(Float)
    elo_after: Mapped[Optional[float]] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(40))
    __table_args__ = (UniqueConstraint("fixture_id", "team_id"),)


class PlayerStats(Base):
    __tablename__ = "player_stats"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), index=True)
    fixture_id: Mapped[str] = mapped_column(ForeignKey("fixtures.id"), index=True)
    minutes: Mapped[int] = mapped_column(Integer)
    goals: Mapped[int] = mapped_column(Integer, default=0)
    assists: Mapped[int] = mapped_column(Integer, default=0)
    shots: Mapped[Optional[int]] = mapped_column(Integer)
    shots_on_target: Mapped[Optional[int]] = mapped_column(Integer)
    xg: Mapped[Optional[float]] = mapped_column(Float)
    xa: Mapped[Optional[float]] = mapped_column(Float)
    __table_args__ = (UniqueConstraint("player_id", "fixture_id"),)


class Lineup(Base):
    __tablename__ = "lineups"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[str] = mapped_column(ForeignKey("fixtures.id"), index=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    status: Mapped[str] = mapped_column(String(12))                      # predicted | confirmed
    formation: Mapped[Optional[str]] = mapped_column(String(12))
    starters: Mapped[list] = mapped_column(JSON, default=list)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source: Mapped[str] = mapped_column(String(40))


class Injury(Base):
    __tablename__ = "injuries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[Optional[str]] = mapped_column(ForeignKey("fixtures.id"), index=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    player_id: Mapped[Optional[str]] = mapped_column(ForeignKey("players.id"))
    player_name: Mapped[str] = mapped_column(String(120))
    position: Mapped[Optional[str]] = mapped_column(String(4))
    reason: Mapped[str] = mapped_column(String(40))                      # injury | suspension | ...
    status: Mapped[str] = mapped_column(String(12))                      # out | doubtful
    importance: Mapped[Optional[float]] = mapped_column(Float)            # from player_stats minutes share
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(40))


# ----------------------------------------------------------------- odds

class Odds(Base):
    """Latest price per fixture/market/bookmaker (upserted by odds_sync)."""
    __tablename__ = "odds"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[str] = mapped_column(ForeignKey("fixtures.id"), index=True)
    market_key: Mapped[str] = mapped_column(String(40))
    bookmaker: Mapped[str] = mapped_column(String(40))
    price: Mapped[float] = mapped_column(Numeric(8, 3))
    opening_price: Mapped[float] = mapped_column(Numeric(8, 3))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_closing: Mapped[bool] = mapped_column(Boolean, default=False)     # final pre-kickoff price, for CLV
    __table_args__ = (UniqueConstraint("fixture_id", "market_key", "bookmaker"),)


class OddsHistory(Base):
    """Append-only price ticks; TimescaleDB hypertable on captured_at."""
    __tablename__ = "odds_history"
    fixture_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    market_key: Mapped[str] = mapped_column(String(40), primary_key=True)
    bookmaker: Mapped[str] = mapped_column(String(40), primary_key=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    price: Mapped[float] = mapped_column(Numeric(8, 3))


# ----------------------------------------------------------------- model

class ModelVersion(Base):
    __tablename__ = "model_versions"
    id: Mapped[str] = mapped_column(String(60), primary_key=True)        # FootballModel_v0.3.0
    config: Mapped[dict] = mapped_column(JSON)                           # full EngineConfig
    code_ref: Mapped[Optional[str]] = mapped_column(String(80))          # git sha
    notes: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelPrediction(Base):
    """Immutable snapshot of a full fixture analysis."""
    __tablename__ = "model_predictions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[str] = mapped_column(ForeignKey("fixtures.id"), index=True)
    model_version: Mapped[str] = mapped_column(ForeignKey("model_versions.id"), index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    input_hash: Mapped[str] = mapped_column(String(32))
    trigger: Mapped[str] = mapped_column(String(30))                     # scheduled | lineup_confirmed | odds_move | manual
    supersedes_id: Mapped[Optional[int]] = mapped_column(ForeignKey("model_predictions.id"))
    provider: Mapped[str] = mapped_column(String(40))                    # fixtures API that verified the fixture
    verification_status: Mapped[str] = mapped_column(String(20))         # VERIFIED at publication time
    p_home: Mapped[float] = mapped_column(Float)
    p_draw: Mapped[float] = mapped_column(Float)
    p_away: Mapped[float] = mapped_column(Float)
    xg_home: Mapped[float] = mapped_column(Float)
    xg_away: Mapped[float] = mapped_column(Float)
    data_quality: Mapped[float] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSON)                          # full analyse_fixture output
    inputs: Mapped[dict] = mapped_column(JSON)                           # serialised FixtureInput
    markets: Mapped[list["MarketPrediction"]] = relationship(back_populates="prediction")


class MarketPrediction(Base):
    __tablename__ = "market_predictions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("model_predictions.id"), index=True)
    fixture_id: Mapped[str] = mapped_column(String(60), index=True)
    market_key: Mapped[str] = mapped_column(String(40), index=True)
    family: Mapped[str] = mapped_column(String(20))
    model_probability: Mapped[float] = mapped_column(Float)
    fair_odds: Mapped[Optional[float]] = mapped_column(Float)
    bookmaker_odds: Mapped[Optional[float]] = mapped_column(Float)
    implied_probability: Mapped[Optional[float]] = mapped_column(Float)
    no_vig_probability: Mapped[Optional[float]] = mapped_column(Float)
    ev: Mapped[Optional[float]] = mapped_column(Float)
    edge_pp: Mapped[Optional[float]] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    risk_score: Mapped[float] = mapped_column(Float)
    risk: Mapped[str] = mapped_column(String(12))
    pick_score: Mapped[Optional[float]] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16))                      # recommended | avoid | no_price
    prediction: Mapped[ModelPrediction] = relationship(back_populates="markets")
    __table_args__ = (Index("ix_mp_fixture_market", "fixture_id", "market_key"),)


class PredictionInvalidation(Base):
    """Append-only record that a stored prediction must no longer be used
    (e.g. it was produced from an unverified fixture). Readers exclude any
    prediction that has a row here."""
    __tablename__ = "prediction_invalidations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("model_predictions.id"), index=True)
    reason: Mapped[str] = mapped_column(String(200))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Recommendation(Base):
    """A published pick: frozen at publication so results are tracked against
    the exact odds/probability shown at the time (no hindsight edits)."""
    __tablename__ = "recommendations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_prediction_id: Mapped[int] = mapped_column(ForeignKey("market_predictions.id"))
    fixture_id: Mapped[str] = mapped_column(ForeignKey("fixtures.id"), index=True)
    model_version: Mapped[str] = mapped_column(String(60), index=True)
    market_key: Mapped[str] = mapped_column(String(40))
    label_tag: Mapped[str] = mapped_column(String(30))
    odds_taken: Mapped[float] = mapped_column(Float)
    model_probability: Mapped[float] = mapped_column(Float)
    ev: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    risk: Mapped[str] = mapped_column(String(12))
    pick_score: Mapped[float] = mapped_column(Float)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    closing_odds: Mapped[Optional[float]] = mapped_column(Float)
    result: Mapped[Optional[str]] = mapped_column(String(12))            # win | half_win | push | half_loss | loss | void
    profit_units: Mapped[Optional[float]] = mapped_column(Float)
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


# ----------------------------------------------------------------- users & tracking

class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))              # argon2id
    role: Mapped[str] = mapped_column(String(12), default="user")        # user | admin
    settings: Mapped[dict] = mapped_column(JSON, default=dict)           # odds format, favourites, thresholds
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class BetSlip(TimestampMixin, Base):
    __tablename__ = "bet_slips"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(12))                        # single | multiple
    stake_units: Mapped[float] = mapped_column(Float)
    combined_odds: Mapped[float] = mapped_column(Float)
    est_probability: Mapped[Optional[float]] = mapped_column(Float)      # correlation-adjusted (phase 4)
    health_score: Mapped[Optional[float]] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(12), default="open")
    result: Mapped[Optional[str]] = mapped_column(String(12))
    profit_units: Mapped[Optional[float]] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20), default="app")       # app | manual | import


class Bet(Base):
    __tablename__ = "bets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slip_id: Mapped[int] = mapped_column(ForeignKey("bet_slips.id"), index=True)
    fixture_id: Mapped[Optional[str]] = mapped_column(ForeignKey("fixtures.id"))
    recommendation_id: Mapped[Optional[int]] = mapped_column(ForeignKey("recommendations.id"))
    market_key: Mapped[str] = mapped_column(String(40))
    odds: Mapped[float] = mapped_column(Float)
    model_probability: Mapped[Optional[float]] = mapped_column(Float)
    result: Mapped[Optional[str]] = mapped_column(String(12))


class Challenge(TimestampMixin, Base):
    __tablename__ = "challenges"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(12))                        # picks | days
    target: Mapped[int] = mapped_column(Integer)
    rules: Mapped[dict] = mapped_column(JSON, default=dict)              # min confidence, markets, stake
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(12), default="active")


class ChallengeEntry(Base):
    __tablename__ = "challenge_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    challenge_id: Mapped[int] = mapped_column(ForeignKey("challenges.id"), index=True)
    bet_slip_id: Mapped[int] = mapped_column(ForeignKey("bet_slips.id"))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BankrollHistory(Base):
    """Time series; TimescaleDB hypertable on recorded_at."""
    __tablename__ = "bankroll_history"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    balance: Mapped[float] = mapped_column(Numeric(14, 2))
    unit_size: Mapped[float] = mapped_column(Numeric(14, 2))             # flat staking: % of bankroll
    event: Mapped[str] = mapped_column(String(20))                       # deposit | settle | adjust
    reference_id: Mapped[Optional[int]] = mapped_column(Integer)


class Backtest(Base):
    __tablename__ = "backtests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_version: Mapped[str] = mapped_column(ForeignKey("model_versions.id"))
    params: Mapped[dict] = mapped_column(JSON)                           # date range, filters, thresholds
    results: Mapped[dict] = mapped_column(JSON)                          # ROI, drawdown, calibration...
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(60))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[Optional[str]] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class AppSetting(Base):
    """Admin-editable engine configuration (thresholds, weights, whitelist)."""
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
