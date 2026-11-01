"""Persistence helpers. Predictions are append-only snapshots."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import Optional

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.engine.types import FixtureInput

from .models import MarketPrediction, ModelPrediction, ModelVersion


def make_session_factory(database_url: str) -> sessionmaker:
    engine = create_engine(database_url, pool_pre_ping=True, future=True)
    return sessionmaker(engine, expire_on_commit=False)


def ensure_model_version(s: Session, version: str, config: dict, code_ref: Optional[str] = None) -> None:
    mv = s.get(ModelVersion, version)
    if mv is None:
        s.add(ModelVersion(id=version, config=config, code_ref=code_ref))
    elif mv.config != config:
        raise ValueError(f"Config for {version} differs from the stored one; bump the model version "
                         "instead of changing an existing version's behaviour")


def latest_prediction(s: Session, fixture_id: str) -> Optional[ModelPrediction]:
    q = (select(ModelPrediction).where(ModelPrediction.fixture_id == fixture_id)
         .order_by(ModelPrediction.generated_at.desc()).limit(1))
    return s.scalars(q).first()


def save_prediction(s: Session, analysis: dict, fx: FixtureInput, trigger: str) -> ModelPrediction:
    """Insert a new snapshot (never update). Skips the insert when inputs and
    model version are unchanged since the latest snapshot."""
    if not analysis.get("provenance"):
        raise ValueError("Refusing to store a prediction without provider provenance")
    prev = latest_prediction(s, analysis["fixture_id"])
    if prev and prev.input_hash == analysis["model"]["input_hash"] and prev.model_version == analysis["model"]["version"]:
        return prev
    ensure_model_version(s, analysis["model"]["version"], analysis["model"]["config"])
    pred = ModelPrediction(
        fixture_id=analysis["fixture_id"], model_version=analysis["model"]["version"],
        generated_at=datetime.fromisoformat(analysis["model"]["generated_at"]),
        input_hash=analysis["model"]["input_hash"], trigger=trigger, supersedes_id=prev.id if prev else None,
        provider=(analysis.get("provenance") or {})["provider"],
        verification_status=(analysis.get("verification") or {}).get("status", "VERIFIED"),
        p_home=analysis["prediction"]["probabilities"]["home"], p_draw=analysis["prediction"]["probabilities"]["draw"],
        p_away=analysis["prediction"]["probabilities"]["away"],
        xg_home=analysis["prediction"]["expected_goals"]["home"], xg_away=analysis["prediction"]["expected_goals"]["away"],
        data_quality=analysis["data_quality"]["score"], payload=analysis,
        inputs=json.loads(json.dumps(asdict(fx), default=str)),
    )
    s.add(pred)
    s.flush()
    for m in analysis["markets"]:
        s.add(MarketPrediction(
            prediction_id=pred.id, fixture_id=pred.fixture_id, market_key=m["key"], family=m["family"],
            model_probability=m["model_probability"], fair_odds=m["fair_odds"], bookmaker_odds=m["bookmaker_odds"],
            implied_probability=m["implied_probability"], no_vig_probability=m["no_vig_probability"], ev=m["ev"],
            edge_pp=m["edge_pp"], confidence=m["confidence"], risk_score=m["risk_score"], risk=m["risk"],
            pick_score=m["pick_score"], status=m["status"]))
    return pred


def prediction_change(prev: Optional[ModelPrediction], new: ModelPrediction) -> Optional[dict]:
    """'PREDICTION UPDATED' payload for alerts / lineup recalculation."""
    if not prev or prev.id == new.id:
        return None
    return {
        "fixture_id": new.fixture_id, "trigger": new.trigger,
        "before": {"home": prev.p_home, "draw": prev.p_draw, "away": prev.p_away},
        "after": {"home": new.p_home, "draw": new.p_draw, "away": new.p_away},
        "absences": {
            "home": new.payload["home"]["absence_impact"]["lines"],
            "away": new.payload["away"]["absence_impact"]["lines"],
        },
    }
