"""Odds maths: implied probability, margin removal, fair odds, expected value.

Pure Python (stdlib only) so the engine can be unit-tested and backtested
anywhere, independent of the web stack.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

MarginMethod = Literal["proportional", "power", "shin"]


def implied_probability(decimal_odds: float) -> float:
    """Raw bookmaker implied probability: 1 / decimal odds (includes margin)."""
    if decimal_odds <= 1.0:
        raise ValueError(f"Decimal odds must be > 1.0, got {decimal_odds}")
    return 1.0 / decimal_odds


def overround(odds: Sequence[float]) -> float:
    """Book overround, e.g. 0.052 means a 5.2% margin."""
    return sum(implied_probability(o) for o in odds) - 1.0


def remove_margin(odds: Sequence[float], method: MarginMethod = "power") -> list[float]:
    """Return normalised (no-vig) probabilities for a complete market.

    proportional: divide each implied probability by the book sum.
    power:        find k with sum(q_i ** k) == 1. Shifts more margin onto
                  longshots, which matches the favourite-longshot bias better.
    shin:         Shin (1993) insider-trading model, solved for z.
    """
    q = [implied_probability(o) for o in odds]
    book = sum(q)
    if len(q) < 2:
        raise ValueError("A complete market needs at least two outcomes")
    if method == "proportional" or book <= 1.0:
        return [x / book for x in q]
    if method == "power":
        lo, hi = 1.0, 10.0
        for _ in range(200):
            k = (lo + hi) / 2
            s = sum(x ** k for x in q)
            if s > 1.0:
                lo = k
            else:
                hi = k
        k = (lo + hi) / 2
        p = [x ** k for x in q]
        s = sum(p)
        return [x / s for x in p]
    if method == "shin":

        def probs(z: float) -> list[float]:
            return [
                ((z * z + 4 * (1 - z) * (x * x) / book) ** 0.5 - z) / (2 * (1 - z))
                for x in q
            ]

        lo, hi = 0.0, 0.4
        for _ in range(200):
            z = (lo + hi) / 2
            if sum(probs(z)) > 1.0:
                lo = z
            else:
                hi = z
        p = probs((lo + hi) / 2)
        s = sum(p)
        return [x / s for x in p]
    raise ValueError(f"Unknown margin method {method}")


def fair_odds(probability: float) -> float:
    """Fair (zero-margin) decimal odds for a model probability."""
    if probability <= 0:
        return float("inf")
    return 1.0 / probability


def expected_value(probability: float, decimal_odds: float) -> float:
    """EV per unit staked for a binary bet: p * odds - 1."""
    return probability * decimal_odds - 1.0


@dataclass(frozen=True)
class SettlementDistribution:
    """Probabilities of each settlement outcome for a bet that can push or
    half-settle (Asian handicaps, Draw No Bet, quarter goal lines)."""

    win: float
    half_win: float = 0.0
    push: float = 0.0
    half_loss: float = 0.0
    loss: float = 0.0

    def ev(self, decimal_odds: float) -> float:
        o = decimal_odds
        returned = (
            self.win * o
            + self.half_win * (o + 1) / 2
            + self.push * 1.0
            + self.half_loss * 0.5
        )
        return returned - 1.0

    def fair_odds(self) -> float:
        """Odds at which EV == 0 (EV is linear in odds)."""
        denom = self.win + self.half_win / 2
        if denom <= 0:
            return float("inf")
        numer = 1.0 - self.half_win / 2 - self.push - self.half_loss / 2
        return numer / denom

    def effective_probability(self) -> float:
        """Probability-equivalent used for display and scoring: the binary
        probability that would give the same fair odds."""
        fo = self.fair_odds()
        return 0.0 if fo == float("inf") else 1.0 / fo


def decimal_to_fractional(decimal_odds: float, max_denominator: int = 20) -> str:
    from fractions import Fraction

    frac = Fraction(decimal_odds - 1).limit_denominator(max_denominator)
    return f"{frac.numerator}/{frac.denominator}"
