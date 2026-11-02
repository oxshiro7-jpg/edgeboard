"""Engine configuration. Every weight and threshold the admin panel exposes
lives here so a model version can be reproduced exactly from its config."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class EnsembleWeights:
    dixon_coles: float = 0.30
    xg_poisson: float = 0.20
    elo: float = 0.15
    form: float = 0.10
    market: float = 0.25   # de-margined bookmaker prices (shrinkage toward market)
    ml: float = 0.0        # stacked ML blend weight; set only by champion/challenger promotion

    def normalised(self) -> dict[str, float]:
        d = asdict(self)
        d.pop("ml")
        s = sum(d.values())
        return {k: v / s for k, v in d.items()}


@dataclass
class PickScoreWeights:
    probability: float = 0.25
    expected_value: float = 0.25
    confidence: float = 0.20
    market_stability: float = 0.10
    historical_accuracy: float = 0.10
    data_quality: float = 0.10


@dataclass
class Thresholds:
    min_ev: float = 0.04               # +4% EV
    min_pick_score: float = 60
    min_confidence: float = 60
    min_data_quality: float = 60
    max_risk: str = "High"             # Very Low | Low | Moderate | High | Very High
    min_odds: float = 1.20
    max_odds: float = 6.00
    max_model_disagreement: float = 0.12   # spread of component home-win probs
    suspicious_edge_pp: float = 0.15   # edge vs no-vig market that suggests missing info
    strong_pick_score: float = 75
    highest_confidence_score: float = 85
    value_pick_ev: float = 0.08
    max_goals_gap: float = 0.35        # |model total - market total| above this rejects goal-count markets
    strong_label_min_data_quality: float = 60   # "Strong Pick" / "Strong opportunity" need at least this


@dataclass
class ModelParams:
    home_advantage_goals: float = 0.25     # additive xG for home side (league avg)
    league_avg_goals_per_team: float = 1.38
    dixon_coles_rho: float = -0.08
    elo_home_advantage: float = 65.0
    elo_draw_base: float = 0.28
    first_half_goal_share: float = 0.45
    max_goals: int = 10
    recent_matches: int = 10
    form_half_life_matches: float = 4.0
    margin_method: str = "power"
    goals_shrink_matches: float = 6.0       # without xG, team goal rates are shrunk toward the league
                                            # average as if this many average matches were added
    single_model_spread: float = 0.10       # assumed model spread when only one stats model can run


@dataclass
class EngineConfig:
    model_version: str = "FootballModel_v0.3.1"
    ensemble: EnsembleWeights = field(default_factory=EnsembleWeights)
    pick_weights: PickScoreWeights = field(default_factory=PickScoreWeights)
    thresholds: Thresholds = field(default_factory=Thresholds)
    params: ModelParams = field(default_factory=ModelParams)
    # Historical hit-rate calibration factor per market family (1.0 = perfectly
    # calibrated). Populated by the calibration job; neutral until there is data.
    calibration: dict[str, float] = field(default_factory=dict)
    enabled_markets: list[str] = field(default_factory=lambda: [
        "1X2", "DC", "DNB", "OU", "TT", "BTTS", "AH", "FH1X2", "FHOU", "CARDS", "CORNERS",
    ])

    def to_dict(self) -> dict:
        return asdict(self)


RISK_ORDER = ["Very Low", "Low", "Moderate", "High", "Very High"]
