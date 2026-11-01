"""Data quality, confidence, risk and pick score.

Probability, confidence, value and risk are deliberately separate numbers:
  probability - how likely the outcome is (model output)
  confidence  - how much we trust that probability (inputs, agreement, calibration)
  value       - EV of the price on offer
  risk        - how volatile/uncertain the bet is as a wager
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .config import RISK_ORDER, EngineConfig
from .markets import family
from .types import FixtureInput, OddsSeries

FAMILY_RELIABILITY = {
    "result": 1.0, "handicap": 1.0, "goals": 1.0, "btts": 0.97,
    "first_half": 0.90, "second_half": 0.90, "cards": 0.82, "corners": 0.82,
}
FAMILY_BASE_RISK = {
    "result": 5, "handicap": 5, "goals": 5, "btts": 8,
    "first_half": 12, "second_half": 12, "cards": 15, "corners": 15,
}
LINEUP_FACTOR = {"confirmed": 1.0, "predicted": 0.93, "unknown": 0.86}
# Neutral prior until the calibration job has enough settled picks.
UNCALIBRATED_FACTOR = 0.92
UNPROVEN_ACCURACY_SCORE = 70.0


@dataclass
class DataQuality:
    score: float
    factors: dict[str, float]
    gaps: list[str] = field(default_factory=list)


def data_quality(fx: FixtureInput, now: datetime, extra_gaps: list[str]) -> DataQuality:
    gaps = list(extra_gaps)
    n_h, n_a = len(fx.home.recent), len(fx.away.recent)
    sample = min(1.0, min(n_h, n_a) / 10) * 100
    if min(n_h, n_a) < 6:
        gaps.append(f"Small sample: {min(n_h, n_a)} recent matches")
    recs = fx.home.recent[:10] + fx.away.recent[:10]
    xg_share = (sum(1 for r in recs if r.xg_for is not None) / len(recs) * 100) if recs else 0
    stat_share = (sum(1 for r in recs if r.shots is not None) / len(recs) * 100) if recs else 0
    lineup = {"confirmed": 100, "predicted": 72, "unknown": 40}
    lu = (lineup[fx.lineup_home.status] + lineup[fx.lineup_away.status]) / 2
    if fx.lineup_home.status == "unknown" or fx.lineup_away.status == "unknown":
        gaps.append("No lineup information for at least one side")
    injury = 100.0 if fx.injury_feed_available else 40.0
    if not fx.injury_feed_available:
        gaps.append("Injury feed unavailable")
    core = ("1X2:H", "1X2:D", "1X2:A", "OU:2.5:over", "OU:2.5:under", "BTTS:yes", "BTTS:no")
    odds_av = sum(1 for k in core if k in fx.odds) / len(core) * 100
    style = fx.home.style, fx.away.style
    style_fields = [v for s in style for v in vars(s).values()]
    player = sum(1 for v in style_fields if v is not None) / len(style_fields) * 100
    if fx.data_updated_at is None:
        recency = 30.0
        gaps.append("Data timestamp unknown")
    else:
        hours = (now - fx.data_updated_at).total_seconds() / 3600
        recency = 100 if hours < 6 else 85 if hours < 24 else 60 if hours < 72 else 30
    comp = {"league": 100, "continental": 88, "cup": 75}.get(fx.competition_type, 70)
    factors = {
        "historical_sample": sample, "xg_coverage": xg_share, "team_statistics": stat_share,
        "lineup_certainty": lu, "injury_information": injury, "odds_availability": odds_av,
        "style_and_player_statistics": player, "recency": recency, "competition_coverage": comp,
    }
    weights = {
        "historical_sample": 0.16, "xg_coverage": 0.16, "team_statistics": 0.08,
        "lineup_certainty": 0.14, "injury_information": 0.12, "odds_availability": 0.10,
        "style_and_player_statistics": 0.06, "recency": 0.10, "competition_coverage": 0.08,
    }
    score = sum(factors[k] * w for k, w in weights.items())
    return DataQuality(round(score, 1), {k: round(v, 1) for k, v in factors.items()}, gaps)


def odds_volatility(series: Optional[OddsSeries]) -> float:
    """Relative movement of the price since opening plus intra-period range.
    0 = static, 0.1 = ~10% total movement."""
    if series is None:
        return 0.0
    drift = abs(math.log(series.current / series.opening))
    rng = math.log(series.high / series.low) if series.low > 0 else 0
    return 0.6 * drift + 0.4 * rng


@dataclass
class ConfidenceResult:
    score: float
    level: str
    factors: dict[str, float]


def confidence_level(score: float) -> str:
    if score >= 90:
        return "Exceptional model agreement"
    if score >= 80:
        return "Very strong"
    if score >= 70:
        return "Strong"
    if score >= 60:
        return "Moderate"
    if score >= 50:
        return "Weak"
    return "Avoid"


def confidence(key: str, fx: FixtureInput, cfg: EngineConfig, dq: DataQuality, disagreement: float,
               absence_unc: float, context_unc: float, volatility: float,
               suspicious_edge: bool) -> ConfidenceResult:
    fam = family(key)
    f = {
        "data_quality": 0.5 + 0.5 * dq.score / 100,
        "model_agreement": 1 - min(1.0, disagreement / 0.30) * 0.5,
        "lineup_certainty": (LINEUP_FACTOR[fx.lineup_home.status] + LINEUP_FACTOR[fx.lineup_away.status]) / 2,
        "injury_uncertainty": 1 - 0.30 * absence_unc,
        "context_uncertainty": 1 - context_unc,
        "sample_size": min(1.0, 0.70 + 0.03 * min(len(fx.home.recent), len(fx.away.recent))),
        "historical_calibration": cfg.calibration.get(fam, UNCALIBRATED_FACTOR),
        "market_stability": 1 - min(0.25, volatility),
        "market_reliability": FAMILY_RELIABILITY.get(fam, 0.85),
        "model_market_divergence": 0.80 if suspicious_edge else 1.0,
    }
    raw = 100 * math.prod(f.values())
    score = min(raw, dq.score + 10)   # low data quality caps confidence
    score = round(max(0.0, score), 0)
    return ConfidenceResult(score, confidence_level(score), {k: round(v, 3) for k, v in f.items()})


@dataclass
class RiskResult:
    score: float
    category: str
    factors: dict[str, float]


def risk_category(score: float) -> str:
    if score < 15:
        return "Very Low"
    if score < 27:
        return "Low"
    if score < 42:
        return "Moderate"
    if score < 55:
        return "High"
    return "Very High"


def team_inconsistency(fx: FixtureInput) -> float:
    vals = []
    for team in (fx.home, fx.away):
        xgd = [r.xg_for - r.xg_against for r in team.recent[:10] if r.xg_for is not None and r.xg_against is not None]
        if len(xgd) >= 3:
            m = sum(xgd) / len(xgd)
            vals.append(math.sqrt(sum((x - m) ** 2 for x in xgd) / len(xgd)))
    return sum(vals) / len(vals) if vals else 1.0


def risk(key: str, prob: float, odds: Optional[float], fx: FixtureInput, disagreement: float,
         volatility: float, inconsistency: float) -> RiskResult:
    fam = family(key)
    c = fx.context
    lineup_pts = {"confirmed": 0, "predicted": 2, "unknown": 4}
    f = {
        "variance": (1 - prob) * 50,
        "market_type": FAMILY_BASE_RISK.get(fam, 10),
        "odds": max(0.0, ((odds or 1 / max(prob, 0.01)) - 3.0) * 2.5),
        "team_inconsistency": min(10.0, inconsistency * 6),
        "lineup_uncertainty": lineup_pts[fx.lineup_home.status] + lineup_pts[fx.lineup_away.status],
        "manager_change": 6.0 if any(d is not None and d <= 45 for d in (c.manager_change_days_home, c.manager_change_days_away)) else 0.0,
        "small_sample": 8.0 if min(len(fx.home.recent), len(fx.away.recent)) < 6 else 0.0,
        "competition": {"cup": 6.0, "continental": 3.0}.get(fx.competition_type, 0.0),
        "model_disagreement": min(10.0, disagreement * 40),
        "market_movement": min(8.0, volatility * 40),
    }
    score = sum(f.values())
    return RiskResult(round(score, 1), risk_category(score), {k: round(v, 1) for k, v in f.items()})


def risk_at_most(category: str, limit: str) -> bool:
    return RISK_ORDER.index(category) <= RISK_ORDER.index(limit)


def pick_score(prob: float, ev: float, conf: float, volatility: float, dq: float, fam: str,
               cfg: EngineConfig, accuracy: Optional[dict[str, float]] = None) -> tuple[float, dict[str, float]]:
    w = cfg.pick_weights
    parts = {
        "probability": prob * 100,
        "expected_value": max(0.0, min(1.0, (ev + 0.05) / 0.25)) * 100,
        "confidence": conf,
        "market_stability": max(0.0, 100 - volatility * 400),
        "historical_accuracy": (accuracy or {}).get(fam, UNPROVEN_ACCURACY_SCORE),
        "data_quality": dq,
    }
    score = (w.probability * parts["probability"] + w.expected_value * parts["expected_value"]
             + w.confidence * parts["confidence"] + w.market_stability * parts["market_stability"]
             + w.historical_accuracy * parts["historical_accuracy"] + w.data_quality * parts["data_quality"])
    total_w = (w.probability + w.expected_value + w.confidence + w.market_stability
               + w.historical_accuracy + w.data_quality)
    return round(score / total_w, 0), {k: round(v, 1) for k, v in parts.items()}
