"""Celery workers: data ingestion, odds sync and prediction recalculation.

Start:
    celery -A app.workers.tasks worker -Q ingest,predict -l info
    celery -A app.workers.tasks beat -l info
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from celery import Celery
from celery.schedules import crontab

from app.db.repository import latest_prediction, make_session_factory, prediction_change, save_prediction
from app.engine.config import EngineConfig
from app.engine.pipeline import analyse_fixture
from app.providers.registry import build_providers
from app.services.assembly import build_fixture_input
from app.settings import settings
from app.verification import Status, data_status, verify_fixture

celery = Celery("edgeboard", broker=settings.redis_url or "redis://localhost:6379/0")
celery.conf.task_routes = {
    "app.workers.tasks.sync_*": {"queue": "ingest"},
    "app.workers.tasks.recalculate_*": {"queue": "predict"},
}
celery.conf.beat_schedule = {
    "historical-sync-nightly": {"task": "app.workers.tasks.sync_history", "schedule": crontab(hour=3, minute=0)},
    "fixture-sync-hourly": {"task": "app.workers.tasks.sync_fixtures", "schedule": crontab(minute=5)},
    "stats-sync": {"task": "app.workers.tasks.sync_history", "schedule": crontab(hour="*/6", minute=20)},
    "odds-sync": {"task": "app.workers.tasks.sync_odds", "schedule": timedelta(minutes=5)},
    "injury-sync": {"task": "app.workers.tasks.sync_injuries", "schedule": timedelta(minutes=30)},
    "lineup-sync": {"task": "app.workers.tasks.sync_lineups", "schedule": timedelta(minutes=5)},
    "recalculate": {"task": "app.workers.tasks.recalculate_upcoming", "schedule": timedelta(minutes=15)},
    "model-evaluation": {"task": "app.workers.tasks.evaluate_models", "schedule": crontab(hour=4, minute=30)},
}

_Session = make_session_factory(settings.database_url) if settings.database_url else None


def _providers(day):
    return build_providers(settings.football_provider, settings.odds_provider, day, settings.provider_keys(), tz=settings.timezone)


@celery.task
def sync_fixtures(days_ahead: int = 3):
    """Upsert fixtures for whitelisted competitions (implementation per provider)."""


@celery.task
def sync_history():
    """Pull finished matches + team statistics, recompute Elo, update team_stats."""


@celery.task
def sync_odds():
    """Fetch current prices; upsert `odds`, append ticks to `odds_history`.
    Polling cadence tightens inside 2 hours of kickoff."""


@celery.task
def sync_injuries():
    """Refresh injuries/suspensions for fixtures in the next 72 hours."""


@celery.task
def sync_lineups():
    """Poll lineups from 75 minutes before kickoff; on confirmation trigger
    an immediate recalculation for that fixture."""


@celery.task
def recalculate_upcoming(trigger: str = "scheduled", fixture_ids: list[str] | None = None):
    """Re-run the engine for upcoming fixtures and store new snapshots."""
    if _Session is None:
        return {"skipped": "DATABASE_URL not configured"}
    today = datetime.now(timezone.utc).date()
    changes = []
    cfg = EngineConfig()
    with _Session.begin() as s:
        for day in (today, today + timedelta(days=1)):
            p = _providers(day)
            for ref in p.football.fixtures(day, list(settings.competitions)):
                if fixture_ids and ref.id not in fixture_ids:
                    continue
                v = verify_fixture(p.football, ref.id, day, list(settings.competitions), settings.timezone, listed=ref)
                if not v.ok:
                    continue                      # unverified fixtures are never predicted or stored
                fx = build_fixture_input(v.verified, p)
                st, _ = data_status(fx)
                if st != Status.VERIFIED:
                    continue                      # ODDS UNAVAILABLE / DATA INCOMPLETE: nothing to publish
                analysis = analyse_fixture(fx, cfg)
                prev = latest_prediction(s, ref.id)
                new = save_prediction(s, analysis, fx, trigger)
                ch = prediction_change(prev, new)
                if ch and abs(ch["after"]["home"] - ch["before"]["home"]) >= 0.03:
                    changes.append(ch)   # forwarded to the alerts service
    return {"changes": changes}


@celery.task
def evaluate_models():
    """Settle recommendations, compute calibration buckets, CLV and ROI by
    model version; flag degradation (Phase 5-6)."""
