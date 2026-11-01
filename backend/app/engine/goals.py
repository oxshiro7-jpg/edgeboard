"""Goal models: independent Poisson, Dixon-Coles low-score correction, and a
time-decayed league-wide attack/defence fit (Maher / Dixon-Coles style)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

ScoreMatrix = list[list[float]]   # m[home_goals][away_goals]


def poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * lam ** k / math.factorial(k)


def dixon_coles_tau(h: int, a: int, lam_h: float, lam_a: float, rho: float) -> float:
    if h == 0 and a == 0:
        return 1 - lam_h * lam_a * rho
    if h == 0 and a == 1:
        return 1 + lam_h * rho
    if h == 1 and a == 0:
        return 1 + lam_a * rho
    if h == 1 and a == 1:
        return 1 - rho
    return 1.0


def score_matrix(lam_h: float, lam_a: float, rho: float = 0.0, max_goals: int = 10) -> ScoreMatrix:
    """Joint score distribution. rho=0 gives independent Poisson. The tail
    beyond max_goals is folded back by renormalisation."""
    ph = [poisson_pmf(i, lam_h) for i in range(max_goals + 1)]
    pa = [poisson_pmf(j, lam_a) for j in range(max_goals + 1)]
    m = [[ph[i] * pa[j] * dixon_coles_tau(i, j, lam_h, lam_a, rho)
          for j in range(max_goals + 1)] for i in range(max_goals + 1)]
    total = sum(sum(r) for r in m)
    return [[x / total for x in r] for r in m]


def outcome_probs(m: ScoreMatrix) -> tuple[float, float, float]:
    h = d = a = 0.0
    for i, row in enumerate(m):
        for j, p in enumerate(row):
            if i > j:
                h += p
            elif i == j:
                d += p
            else:
                a += p
    return h, d, a


def expected_goals(m: ScoreMatrix) -> tuple[float, float]:
    eh = sum(i * p for i, row in enumerate(m) for p in row)
    ea = sum(j * p for row in m for j, p in enumerate(row))
    return eh, ea


def reweight_to_outcomes(m: ScoreMatrix, target: tuple[float, float, float]) -> ScoreMatrix:
    """Scale home-win / draw / away-win regions so the matrix agrees with an
    ensemble 1X2 while keeping the within-region score shape. This keeps every
    derived market (totals, BTTS, handicaps) consistent with the final 1X2."""
    cur = outcome_probs(m)
    f = [t / c if c > 0 else 0.0 for t, c in zip(target, cur)]
    out = []
    for i, row in enumerate(m):
        out.append([p * (f[0] if i > j else f[1] if i == j else f[2]) for j, p in enumerate(row)])
    s = sum(sum(r) for r in out)
    return [[x / s for x in r] for r in out]


def top_scores(m: ScoreMatrix, n: int = 10) -> list[tuple[str, float]]:
    cells = [(f"{i}-{j}", p) for i, row in enumerate(m) for j, p in enumerate(row)]
    return sorted(cells, key=lambda c: c[1], reverse=True)[:n]


# --------------------------------------------------------------------------
# League-wide fit (used by ingestion jobs and the backtesting lab)
# --------------------------------------------------------------------------

@dataclass
class MatchResult:
    date: datetime
    home: str
    away: str
    home_goals: float   # may be xG instead of goals for an xG-based fit
    away_goals: float


@dataclass
class LeagueFit:
    attack: dict[str, float]
    defence: dict[str, float]
    home_advantage: float
    base: float
    rho: float

    def lambdas(self, home: str, away: str) -> tuple[float, float]:
        lh = self.base * self.attack[home] * self.defence[away] * self.home_advantage
        la = self.base * self.attack[away] * self.defence[home]
        return lh, la


def fit_league(results: Iterable[MatchResult], as_of: datetime,
               half_life_days: float = 180.0, iterations: int = 100,
               fit_rho: bool = True) -> LeagueFit:
    """Multiplicative Poisson ratings fitted by iterative proportional scaling
    with exponential time decay (only matches before `as_of` are used, so the
    fit is safe to use in walk-forward backtests)."""
    games = [r for r in results if r.date < as_of]
    if not games:
        raise ValueError("No matches before as_of")
    w = [0.5 ** ((as_of - g.date).days / half_life_days) for g in games]
    teams = sorted({g.home for g in games} | {g.away for g in games})
    att = {t: 1.0 for t in teams}
    dfn = {t: 1.0 for t in teams}
    total_w = sum(w)
    base = sum(wi * (g.home_goals + g.away_goals) for wi, g in zip(w, games)) / (2 * total_w)
    hg = sum(wi * g.home_goals for wi, g in zip(w, games))
    ag = sum(wi * g.away_goals for wi, g in zip(w, games))
    home_adv = math.sqrt(hg / ag) if ag > 0 else 1.0
    for _ in range(iterations):
        for t in teams:
            scored = expected = 0.0
            for wi, g in zip(w, games):
                if g.home == t:
                    scored += wi * g.home_goals
                    expected += wi * base * dfn[g.away] * home_adv
                elif g.away == t:
                    scored += wi * g.away_goals
                    expected += wi * base * dfn[g.home] / home_adv
            att[t] = scored / expected if expected > 0 else 1.0
        for t in teams:
            conceded = expected = 0.0
            for wi, g in zip(w, games):
                if g.home == t:
                    conceded += wi * g.away_goals
                    expected += wi * base * att[g.away] / home_adv
                elif g.away == t:
                    conceded += wi * g.home_goals
                    expected += wi * base * att[g.home] * home_adv
            dfn[t] = conceded / expected if expected > 0 else 1.0
        mean_att = sum(att.values()) / len(att)
        att = {t: v / mean_att for t, v in att.items()}
        dfn = {t: v * mean_att for t, v in dfn.items()}
    fit = LeagueFit(att, dfn, home_adv, base, 0.0)
    if fit_rho:
        fit.rho = _fit_rho(fit, games, w)
    return fit


def _fit_rho(fit: LeagueFit, games: list[MatchResult], w: list[float]) -> float:
    best_rho, best_ll = 0.0, -float("inf")
    for step in range(-25, 11):
        rho = step / 100
        ll = 0.0
        ok = True
        for wi, g in zip(w, games):
            lh, la = fit.lambdas(g.home, g.away)
            h, a = int(round(g.home_goals)), int(round(g.away_goals))
            tau = dixon_coles_tau(h, a, lh, la, rho)
            if tau <= 0:
                ok = False
                break
            ll += wi * (math.log(tau) + math.log(poisson_pmf(h, lh)) + math.log(poisson_pmf(a, la)))
        if ok and ll > best_ll:
            best_rho, best_ll = rho, ll
    return best_rho


def poisson_over_prob(lam: float, line: float) -> float:
    """P(X > line) for a Poisson count with a half-integer line."""
    k = math.floor(line)
    return 1 - sum(poisson_pmf(i, lam) for i in range(k + 1))


def safe_mean(values: Iterable[Optional[float]]) -> Optional[float]:
    v = [x for x in values if x is not None]
    return sum(v) / len(v) if v else None
