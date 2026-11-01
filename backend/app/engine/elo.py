"""Elo ratings: rating updates (for ingestion/backtests) and conversion of a
rating gap into 1X2 probabilities."""
from __future__ import annotations

import math


def expected_score(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def update(rating_home: float, rating_away: float, home_goals: int, away_goals: int,
           k: float = 20.0, home_adv: float = 65.0) -> tuple[float, float]:
    """World-Football-Elo style update with goal-difference multiplier."""
    exp_h = expected_score(rating_home + home_adv, rating_away)
    actual = 1.0 if home_goals > away_goals else 0.5 if home_goals == away_goals else 0.0
    gd = abs(home_goals - away_goals)
    mult = 1.0 if gd <= 1 else 1.5 if gd == 2 else (11 + gd) / 8
    delta = k * mult * (actual - exp_h)
    return rating_home + delta, rating_away - delta


def outcome_probs(rating_home: float, rating_away: float, home_adv: float = 65.0,
                  draw_base: float = 0.28, neutral: bool = False) -> tuple[float, float, float]:
    """Convert an Elo gap to H/D/A. Draw probability peaks at draw_base for
    evenly matched sides and decays as the gap widens; the remaining mass is
    split so that P(H) + 0.5 P(D) equals the Elo expected score."""
    diff = rating_home + (0 if neutral else home_adv) - rating_away
    e = expected_score(diff, 0.0)
    p_draw = draw_base * math.exp(-((diff / 400.0) ** 2) * 1.5)
    p_home = e - p_draw / 2
    p_away = 1 - p_home - p_draw
    p_home = max(p_home, 0.01)
    p_away = max(p_away, 0.01)
    s = p_home + p_draw + p_away
    return p_home / s, p_draw / s, p_away / s
