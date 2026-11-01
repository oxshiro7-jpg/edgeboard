"""Ensemble prediction: several independent component models produce 1X2
probabilities (and, where they are goal models, expected goals). The final
1X2 is a weighted average; the final score matrix is a Dixon-Coles matrix on
weighted expected goals, reshaped to agree with the ensemble 1X2."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import elo as elo_model
from .analysis import AbsenceImpact, decayed_adjusted_xg, window_stats
from .config import EngineConfig
from .goals import LeagueFit, ScoreMatrix, outcome_probs, reweight_to_outcomes, score_matrix
from .odds import remove_margin
from .types import FixtureInput, TeamMatchRecord


@dataclass
class Component:
    name: str
    probs: tuple[float, float, float]
    lambdas: Optional[tuple[float, float]] = None
    matrix: Optional[ScoreMatrix] = None
    note: str = ""


@dataclass
class EnsembleResult:
    components: list[Component]
    weights: dict[str, float]
    probs: tuple[float, float, float]
    lambdas: tuple[float, float]
    matrix: ScoreMatrix
    disagreement: float               # max-min of component home-win probabilities
    goals_disagreement: float         # max-min of component expected total goals
    data_gaps: list[str] = field(default_factory=list)


def _venue_records(records: list[TeamMatchRecord], venue: str) -> list[TeamMatchRecord]:
    return [r for r in records if r.venue == venue]


def _team_rates(records: list[TeamMatchRecord], venue: str, n: int, use_goals_blend: float,
                gaps: list[str], team: str) -> tuple[float, float]:
    """Opponent-adjusted attack/defence rates (goals-per-game scale).
    Venue-specific records are blended in at 30% when there are enough."""
    recent = records[:n]
    ws = window_stats(recent)
    if ws is None:
        raise ValueError(f"No match history for {team}")
    if ws.adj_xg_for is None:
        gaps.append(f"No xG history for {team}; goals used instead")
        f, a = ws.goals_for, ws.goals_against
    else:
        f = (1 - use_goals_blend) * ws.adj_xg_for + use_goals_blend * ws.goals_for
        a = (1 - use_goals_blend) * ws.adj_xg_against + use_goals_blend * ws.goals_against
    vrec = _venue_records(records, venue)[: max(4, n // 2)]
    vws = window_stats(vrec) if len(vrec) >= 4 else None
    if vws and vws.adj_xg_for is not None:
        f = 0.7 * f + 0.3 * vws.adj_xg_for
        a = 0.7 * a + 0.3 * vws.adj_xg_against
    return max(f, 0.15), max(a, 0.15)


def _lambdas(fh: float, ah: float, fa: float, aa: float, cfg: EngineConfig, neutral: bool,
             imp_h: AbsenceImpact, imp_a: AbsenceImpact, fat_h: float, fat_a: float,
             L: float) -> tuple[float, float]:
    ha = 0.0 if neutral else cfg.params.home_advantage_goals
    lam_h = L * (fh / L) * (aa / L) + ha / 2
    lam_a = L * (fa / L) * (ah / L) - ha / 2
    lam_h *= imp_h.attack_multiplier * imp_a.defence_multiplier * fat_h
    lam_a *= imp_a.attack_multiplier * imp_h.defence_multiplier * fat_a
    return max(lam_h, 0.1), max(lam_a, 0.1)


def predict(fx: FixtureInput, cfg: EngineConfig, imp_h: AbsenceImpact, imp_a: AbsenceImpact,
            fat_h: float = 1.0, fat_a: float = 1.0, league_fit: Optional[LeagueFit] = None) -> EnsembleResult:
    p = cfg.params
    gaps: list[str] = []
    neutral = fx.context.neutral_venue
    L = fx.league_avg_goals or p.league_avg_goals_per_team
    if fx.league_avg_goals is None:
        gaps.append("No competition scoring baseline; default league average used")
    n = p.recent_matches
    comps: list[Component] = []

    # 1) xG Poisson (flat window, opponent adjusted, pure xG)
    fh, ah = _team_rates(fx.home.recent, "H", n, 0.0, gaps, fx.home.name)
    fa, aa = _team_rates(fx.away.recent, "A", n, 0.0, gaps, fx.away.name)
    lx = _lambdas(fh, ah, fa, aa, cfg, neutral, imp_h, imp_a, fat_h, fat_a, L)
    mx = score_matrix(*lx, rho=0.0, max_goals=p.max_goals)
    comps.append(Component("xg_poisson", outcome_probs(mx), lx, mx, "Independent Poisson on opponent-adjusted xG"))

    # 2) Dixon-Coles (league fit when available, else xG/goals blend) with low-score correction
    if league_fit and fx.home.team_id in league_fit.attack and fx.away.team_id in league_fit.attack:
        ld = league_fit.lambdas(fx.home.team_id, fx.away.team_id)
        ld = (ld[0] * imp_h.attack_multiplier * imp_a.defence_multiplier * fat_h,
              ld[1] * imp_a.attack_multiplier * imp_h.defence_multiplier * fat_a)
        rho = league_fit.rho
        note = "Dixon-Coles league fit (time-decayed MLE)"
    else:
        dgaps: list[str] = []
        fh2, ah2 = _team_rates(fx.home.recent, "H", n, 0.4, dgaps, fx.home.name)
        fa2, aa2 = _team_rates(fx.away.recent, "A", n, 0.4, dgaps, fx.away.name)
        ld = _lambdas(fh2, ah2, fa2, aa2, cfg, neutral, imp_h, imp_a, fat_h, fat_a, L)
        rho = p.dixon_coles_rho
        note = "Dixon-Coles on 60/40 xG-goals blend (no league fit loaded)"
    md = score_matrix(*ld, rho=rho, max_goals=p.max_goals)
    comps.append(Component("dixon_coles", outcome_probs(md), ld, md, note))

    # 3) Recent-form model: exponentially decayed, opponent-adjusted xG
    dfh, dah = decayed_adjusted_xg(fx.home.recent[:n], p.form_half_life_matches)
    dfa, daa = decayed_adjusted_xg(fx.away.recent[:n], p.form_half_life_matches)
    if None not in (dfh, dah, dfa, daa):
        lf = _lambdas(dfh, dah, dfa, daa, cfg, neutral, imp_h, imp_a, fat_h, fat_a, L)
        mf = score_matrix(*lf, rho=p.dixon_coles_rho, max_goals=p.max_goals)
        comps.append(Component("form", outcome_probs(mf), lf, mf,
                               f"Decayed xG, half-life {p.form_half_life_matches:g} matches"))

    # 4) Elo (squad absences shift effective rating). Skipped, never defaulted, without ratings.
    if fx.home.elo is not None and fx.away.elo is not None:
        elo_h = fx.home.elo - 120 * (1 - imp_h.attack_multiplier) - 120 * (imp_h.defence_multiplier - 1)
        elo_a = fx.away.elo - 120 * (1 - imp_a.attack_multiplier) - 120 * (imp_a.defence_multiplier - 1)
        pe = elo_model.outcome_probs(elo_h, elo_a, p.elo_home_advantage, p.elo_draw_base, neutral)
        comps.append(Component("elo", pe, None, None, f"Elo {fx.home.elo:.0f} vs {fx.away.elo:.0f}"))
    else:
        gaps.append("No team ratings available; Elo component excluded")

    # 5) Market model: de-margined prices. 1X2 gives the result split; the
    #    2.5 goals line (when priced) gives the expected total, so the market
    #    also shrinks goals markets, not only the 1X2.
    trio = [fx.odds.get(k) for k in ("1X2:H", "1X2:D", "1X2:A")]
    if all(trio):
        pm = tuple(remove_margin([s.current for s in trio], p.margin_method))  # type: ignore[union-attr]
        ou = [fx.odds.get("OU:2.5:over"), fx.odds.get("OU:2.5:under")]
        lm = None
        mm = None
        note = f"No-vig 1X2 ({p.margin_method} method)"
        if all(ou):
            p_over = remove_margin([s.current for s in ou], p.margin_method)[0]  # type: ignore[union-attr]
            lm = market_lambdas(pm, p_over, p.max_goals)  # type: ignore[arg-type]
            mm = score_matrix(*lm, rho=p.dixon_coles_rho, max_goals=p.max_goals)
            note += " + implied total from 2.5 line"
        comps.append(Component("market", pm, lm, mm, note))  # type: ignore[arg-type]
    else:
        gaps.append("No complete 1X2 market; market component excluded")

    raw_w = cfg.ensemble.normalised()
    present = {c.name for c in comps}
    w = {k: v for k, v in raw_w.items() if k in present}
    s = sum(w.values())
    w = {k: v / s for k, v in w.items()}

    probs = tuple(sum(w[c.name] * c.probs[i] for c in comps) for i in range(3))
    goal_comps = [c for c in comps if c.lambdas]
    gw = sum(w[c.name] for c in goal_comps)
    lam = (sum(w[c.name] * c.lambdas[0] for c in goal_comps) / gw,
           sum(w[c.name] * c.lambdas[1] for c in goal_comps) / gw)
    base = score_matrix(*lam, rho=p.dixon_coles_rho, max_goals=p.max_goals)
    final = reweight_to_outcomes(base, probs)  # type: ignore[arg-type]

    models = [c for c in comps if c.name != "market"]   # disagreement is among models, not vs the market
    homes = [c.probs[0] for c in models]
    totals = [sum(c.lambdas) for c in goal_comps if c.name != "market"]
    return EnsembleResult(
        components=comps,
        weights=w,
        probs=probs,  # type: ignore[arg-type]
        lambdas=lam,
        matrix=final,
        disagreement=max(homes) - min(homes),
        goals_disagreement=max(totals) - min(totals) if totals else 0.0,
        data_gaps=gaps,
    )


def market_lambdas(probs: tuple[float, float, float], p_over_25: float, max_goals: int = 10) -> tuple[float, float]:
    """Invert market prices into (home, away) expected goals: the total T
    matches P(goals > 2.5); the split matches P(home) - P(away)."""
    from .goals import poisson_over_prob
    lo, hi = 0.2, 7.0
    for _ in range(60):
        t = (lo + hi) / 2
        if poisson_over_prob(t, 2.5) < p_over_25:
            lo = t
        else:
            hi = t
    total = (lo + hi) / 2
    target = probs[0] - probs[2]
    lo, hi = 0.02, 0.98
    for _ in range(50):
        s = (lo + hi) / 2
        h, _d, a = outcome_probs(score_matrix(total * s, total * (1 - s), max_goals=max_goals))
        if h - a < target:
            lo = s
        else:
            hi = s
    s = (lo + hi) / 2
    return total * s, total * (1 - s)


def half_matrices(lam: tuple[float, float], cfg: EngineConfig) -> tuple[ScoreMatrix, ScoreMatrix]:
    s = cfg.params.first_half_goal_share
    fh = score_matrix(lam[0] * s, lam[1] * s, rho=0.0, max_goals=8)
    sh = score_matrix(lam[0] * (1 - s), lam[1] * (1 - s), rho=0.0, max_goals=8)
    return fh, sh
