"""Feature engineering: opponent-adjusted strength, weighted form, xG
diagnostics, squad absence impact, context and tactical notes.

Every number produced here is derived from provider data on the FixtureInput.
When the inputs needed for a metric are missing, the metric is None and a
data gap is reported - nothing is filled in by guesswork.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .config import ModelParams
from .goals import outcome_probs, score_matrix, safe_mean
from .types import Absence, FixtureInput, TeamMatchRecord, TeamProfile

REFERENCE_ELO = 1500.0

ATTACK_WEIGHT = {"ST": 0.55, "W": 0.40, "AM": 0.40, "CM": 0.18, "DM": 0.08, "FB": 0.10, "CB": 0.03, "GK": 0.0}
DEFENCE_WEIGHT = {"GK": 0.45, "CB": 0.40, "FB": 0.22, "DM": 0.28, "CM": 0.12, "AM": 0.04, "W": 0.04, "ST": 0.02}


def opponent_factor(opp_elo: Optional[float]) -> float:
    """>1 for strong opponents, <1 for weak ones (approx. 2x per 400 Elo).
    Without a rating the match is left unadjusted (factor 1)."""
    if opp_elo is None:
        return 1.0
    return 10 ** ((opp_elo - REFERENCE_ELO) / 1300.0)


def expected_points(xg_for: float, xg_against: float) -> float:
    m = score_matrix(max(xg_for, 0.01), max(xg_against, 0.01), max_goals=8)
    w, d, _ = outcome_probs(m)
    return 3 * w + d


@dataclass
class WindowStats:
    matches: int
    points: int
    points_per_game: float
    weighted_points_per_game: float      # points scaled by opponent strength
    goals_for: float
    goals_against: float
    xg_for: Optional[float]
    xg_against: Optional[float]
    xgd: Optional[float]
    npxg_for: Optional[float]
    adj_xg_for: Optional[float]          # opponent-adjusted
    adj_xg_against: Optional[float]
    expected_points: Optional[float]     # total xPts over window
    goals_minus_xg: Optional[float]
    shots: Optional[float]
    shots_on_target: Optional[float]
    big_chances: Optional[float]
    big_chances_conceded: Optional[float]
    form_string: str


def window_stats(records: list[TeamMatchRecord]) -> Optional[WindowStats]:
    if not records:
        return None
    n = len(records)
    pts = sum(r.points for r in records)
    wpts = sum(r.points * opponent_factor(r.opponent_rating) for r in records) / n
    have_xg = [r for r in records if r.xg_for is not None and r.xg_against is not None]
    xgf = safe_mean(r.xg_for for r in have_xg)
    xga = safe_mean(r.xg_against for r in have_xg)
    adj_f = safe_mean(r.xg_for * math.sqrt(opponent_factor(r.opponent_rating)) for r in have_xg) if have_xg else None
    adj_a = safe_mean(r.xg_against / math.sqrt(opponent_factor(r.opponent_rating)) for r in have_xg) if have_xg else None
    xpts = sum(expected_points(r.xg_for, r.xg_against) for r in have_xg) if have_xg else None
    gmx = (sum(r.goals_for for r in have_xg) - sum(r.xg_for for r in have_xg)) if have_xg else None
    return WindowStats(
        matches=n,
        points=pts,
        points_per_game=pts / n,
        weighted_points_per_game=wpts,
        goals_for=sum(r.goals_for for r in records) / n,
        goals_against=sum(r.goals_against for r in records) / n,
        xg_for=xgf,
        xg_against=xga,
        xgd=(xgf - xga) if xgf is not None and xga is not None else None,
        npxg_for=safe_mean(r.npxg_for for r in records),
        adj_xg_for=adj_f,
        adj_xg_against=adj_a,
        expected_points=xpts,
        goals_minus_xg=gmx,
        shots=safe_mean(r.shots for r in records),
        shots_on_target=safe_mean(r.shots_on_target for r in records),
        big_chances=safe_mean(r.big_chances for r in records),
        big_chances_conceded=safe_mean(r.big_chances_conceded for r in records),
        form_string="".join("W" if r.points == 3 else "D" if r.points == 1 else "L" for r in records),
    )


def decayed_adjusted_xg(records: list[TeamMatchRecord], half_life: float) -> tuple[Optional[float], Optional[float]]:
    """Exponentially weighted, opponent-adjusted xG for/against (newest first)."""
    num_f = num_a = den = 0.0
    for i, r in enumerate(records):
        if r.xg_for is None or r.xg_against is None:
            continue
        w = 0.5 ** (i / half_life)
        f = math.sqrt(opponent_factor(r.opponent_rating))
        num_f += w * r.xg_for * f
        num_a += w * r.xg_against / f
        den += w
    if den == 0:
        return None, None
    return num_f / den, num_a / den


@dataclass
class AbsenceImpact:
    attack_multiplier: float
    defence_multiplier: float          # multiplier on xG conceded (>1 = worse)
    uncertainty: float                 # 0-1 extra lineup/defensive uncertainty
    lines: list[str] = field(default_factory=list)
    availability_score: float = 100.0


def absence_impact(absences: list[Absence]) -> AbsenceImpact:
    att = dfn = unc = 0.0
    lines = []
    for a in absences:
        if a.importance is None:
            unc += 0.15 if a.position in ("GK", "CB", "ST") else 0.08
            lines.append(f"{a.player} ({a.position}, {a.reason}, {a.status}): impact not quantified (no minutes data)")
            continue
        share = a.importance * (0.5 if a.status == "doubtful" else 1.0)
        da = share * ATTACK_WEIGHT.get(a.position, 0.1)
        dd = share * DEFENCE_WEIGHT.get(a.position, 0.1)
        att += da
        dfn += dd
        unc += share * (0.6 if a.position in ("GK", "CB") else 0.3) + (0.1 if a.status == "doubtful" else 0)
        parts = []
        if da >= 0.005:
            parts.append(f"attack -{da * 100:.0f}%")
        if dd >= 0.005:
            parts.append(f"xG conceded +{dd * 100:.0f}%")
        status = "doubtful" if a.status == "doubtful" else "out"
        lines.append(f"{a.player} ({a.position}, {a.reason}, {status}): " + (", ".join(parts) or "minor impact"))
    att = min(att, 0.35)
    dfn = min(dfn, 0.40)
    total_importance = sum(a.importance * (0.5 if a.status == "doubtful" else 1) for a in absences if a.importance is not None)
    return AbsenceImpact(
        attack_multiplier=1 - att,
        defence_multiplier=1 + dfn,
        uncertainty=min(unc, 1.0),
        lines=lines,
        availability_score=max(0.0, 100 - total_importance * 100 * 0.6),
    )


@dataclass
class Flag:
    team: str
    kind: str        # results_overperforming | results_underperforming | finishing_hot | finishing_cold
    text: str


def performance_flags(team: TeamProfile, last5: Optional[WindowStats], last10: Optional[WindowStats]) -> list[Flag]:
    flags: list[Flag] = []
    if last5 and last5.expected_points is not None and last5.matches >= 4:
        gap = last5.points - last5.expected_points
        if gap >= 3.5:
            flags.append(Flag(team.name, "results_overperforming",
                              f"{team.name}: results stronger than underlying performances "
                              f"({last5.points} pts vs {last5.expected_points:.1f} xPts in last {last5.matches})."))
        elif gap <= -3.5:
            flags.append(Flag(team.name, "results_underperforming",
                              f"{team.name}: underlying performances stronger than results "
                              f"({last5.points} pts vs {last5.expected_points:.1f} xPts in last {last5.matches})."))
    if last10 and last10.goals_minus_xg is not None and last10.matches >= 6:
        g = last10.goals_minus_xg
        if g >= 4:
            flags.append(Flag(team.name, "finishing_hot",
                              f"{team.name} scored {g:+.1f} goals above xG over {last10.matches} matches; "
                              "finishing at this rate rarely persists."))
        elif g <= -4:
            flags.append(Flag(team.name, "finishing_cold",
                              f"{team.name} scored {g:+.1f} goals versus xG over {last10.matches} matches; "
                              "chance creation is better than the scoreline suggests."))
    return flags


def context_notes(fx: FixtureInput) -> tuple[list[str], float, float, float]:
    """Return (notes, home_fatigue_mult, away_fatigue_mult, extra_uncertainty).

    Stakes ("must win") are reported but never converted into a probability
    boost; they raise uncertainty instead."""
    c = fx.context
    notes: list[str] = []
    fh = fa = 1.0
    unc = 0.0
    for side, rest, euro, mult_name in (
        (fx.home.name, c.rest_days_home, c.european_midweek_home, "h"),
        (fx.away.name, c.rest_days_away, c.european_midweek_away, "a"),
    ):
        if rest is not None and rest <= 3:
            m = 0.97 if euro else 0.985
            notes.append(f"{side} on {rest} days' rest" + (" after European midweek fixture" if euro else "") +
                         f" (attack x{m:.3f}, rotation risk raised).")
            unc += 0.05
            if mult_name == "h":
                fh = m
            else:
                fa = m
    if c.travel_km_away and c.travel_km_away > 1500:
        notes.append(f"{fx.away.name} travelled ~{c.travel_km_away:.0f} km.")
    if c.derby:
        notes.append("Derby: historically higher card counts and variance; no directional adjustment applied.")
        unc += 0.04
    for side, days in ((fx.home.name, c.manager_change_days_home), (fx.away.name, c.manager_change_days_away)):
        if days is not None and days <= 45:
            notes.append(f"{side} changed manager {days} days ago; historical data may not reflect current setup.")
            unc += 0.08
    for side, stakes in ((fx.home.name, c.stakes_home), (fx.away.name, c.stakes_away)):
        if stakes in ("title", "relegation", "ucl"):
            label = {"title": "title race", "relegation": "relegation fight", "ucl": "Champions League race"}[stakes]
            notes.append(f"{side} in a {label}. Treated as context only - 'must win' is not assumed to raise win probability.")
    if c.knockout and c.leg == 2 and c.aggregate_home is not None and c.aggregate_away is not None:
        lead = c.aggregate_home - c.aggregate_away
        if lead != 0:
            leader = fx.home.name if lead > 0 else fx.away.name
            notes.append(f"Second leg, {leader} lead {max(c.aggregate_home, c.aggregate_away)}-"
                         f"{min(c.aggregate_home, c.aggregate_away)} on aggregate: game state may suppress "
                         "the leader's attacking intent; goals markets carry extra uncertainty.")
            unc += 0.06
    elif c.knockout:
        notes.append("Knockout fixture: extra time/penalties do not count for 90-minute markets.")
    return notes, fh, fa, min(unc, 0.4)


def tactical_notes(home: TeamProfile, away: TeamProfile) -> list[dict]:
    """Rule-based matchup observations using only style fields that exist."""
    out: list[dict] = []

    def check(att: TeamProfile, dfn: TeamProfile):
        a, d = att.style, dfn.style
        if a.ppda is not None and d.press_resistance_pct is not None and a.ppda < 9.5 and d.press_resistance_pct < 25:
            out.append({"favours": att.name, "text":
                        f"{att.name}'s high press (PPDA {a.ppda:.1f}) meets a side in the bottom "
                        f"{d.press_resistance_pct:.0f}% for retaining possession under pressure."})
        if d.defensive_line_height is not None and a.counter_xg_per_game is not None \
                and d.defensive_line_height > 44 and a.counter_xg_per_game > 0.30:
            out.append({"favours": att.name, "text":
                        f"{dfn.name} hold a high line ({d.defensive_line_height:.0f} m) while {att.name} generate "
                        f"{a.counter_xg_per_game:.2f} xG/game from counters."})
        if a.set_piece_xg_share is not None and d.set_piece_xga_per_game is not None \
                and a.set_piece_xg_share > 0.28 and d.set_piece_xga_per_game > 0.35:
            out.append({"favours": att.name, "text":
                        f"{att.name} take {a.set_piece_xg_share * 100:.0f}% of their xG from set pieces; "
                        f"{dfn.name} concede {d.set_piece_xga_per_game:.2f} set-piece xG per game."})
        if a.crosses_per_game is not None and a.wide_attack_share is not None \
                and a.wide_attack_share > 0.45 and a.crosses_per_game > 18:
            out.append({"favours": att.name, "text":
                        f"{att.name} attack wide ({a.wide_attack_share * 100:.0f}% of entries, "
                        f"{a.crosses_per_game:.0f} crosses/game)."})
        if d.transition_xga_per_game is not None and d.transition_xga_per_game > 0.40:
            out.append({"favours": att.name, "text":
                        f"{dfn.name} concede {d.transition_xga_per_game:.2f} xG/game in transition."})

    check(home, away)
    check(away, home)
    return out


def rating_0_100(x: float, centre: float, scale: float) -> float:
    return round(max(0.0, min(100.0, 50 + 50 * math.tanh((x - centre) / scale))), 0)
