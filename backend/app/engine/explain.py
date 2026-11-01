"""Explanation engine. Every sentence is built from a value that exists in the
fixture inputs or model outputs; if the value is missing the sentence is
omitted rather than approximated."""
from __future__ import annotations

from typing import Optional

from .analysis import WindowStats
from .markets import market_type
from .types import FixtureInput, TeamMatchRecord


def _over_rate(records: list[TeamMatchRecord], line: float) -> Optional[tuple[int, int]]:
    if not records:
        return None
    hits = sum(1 for r in records if r.goals_for + r.goals_against > line)
    return hits, len(records)


def _btts_rate(records: list[TeamMatchRecord]) -> tuple[int, int]:
    return sum(1 for r in records if r.goals_for > 0 and r.goals_against > 0), len(records)


def reasons(key: str, prob: float, fx: FixtureInput, ws: dict[str, Optional[WindowStats]],
            lambdas: tuple[float, float], ensemble_probs: tuple[float, float, float],
            components: dict[str, tuple[float, float, float]]) -> list[str]:
    t = market_type(key)
    p = key.split(":")
    H, A = fx.home.name, fx.away.name
    h10, a10 = ws.get("home_last10"), ws.get("away_last10")
    out: list[str] = []
    lam_h, lam_a = lambdas

    def xg_line(team: str, w: Optional[WindowStats], side: str) -> None:
        if w and w.xg_for is not None:
            out.append(f"{team} average {w.xg_for:.2f} xG and {w.xg_against:.2f} xGA over their last {w.matches} matches "
                       f"({w.adj_xg_for:.2f} / {w.adj_xg_against:.2f} opponent-adjusted).")

    if t in ("OU", "TT", "BTTS", "FHOU", "SHOU"):
        xg_line(H, h10, "H")
        xg_line(A, a10, "A")
        out.append(f"Model expects {lam_h:.2f} - {lam_a:.2f} goals ({lam_h + lam_a:.2f} total).")
        if t == "OU":
            line = float(p[1])
            for team, recs in ((H, fx.home.recent[:10]), (A, fx.away.recent[:10])):
                r = _over_rate(recs, line)
                if r:
                    out.append(f"{r[0]} of {team}'s last {r[1]} matches finished with more than {line:g} goals.")
        if t == "BTTS":
            for team, recs in ((H, fx.home.recent[:10]), (A, fx.away.recent[:10])):
                hits, n = _btts_rate(recs)
                if n:
                    out.append(f"Both teams scored in {hits} of {team}'s last {n} matches.")
        for team, w in ((H, h10), (A, a10)):
            if w and w.shots is not None:
                sot = f" ({w.shots_on_target:.1f} on target)" if w.shots_on_target is not None else ""
                out.append(f"{team} average {w.shots:.1f} shots{sot} per game.")
        out.append(f"Projected score distribution gives {prob * 100:.0f}% for this selection.")
    elif t in ("1X2", "DC", "DNB", "AH", "EH", "FH1X2", "FHDC"):
        ph, pd, pa = ensemble_probs
        out.append(f"Ensemble 1X2: {H} {ph * 100:.0f}% / Draw {pd * 100:.0f}% / {A} {pa * 100:.0f}%.")
        agree = ", ".join(f"{k.replace('_', ' ')} {v[0] * 100:.0f}%" for k, v in components.items())
        out.append(f"Home-win estimate by component: {agree}.")
        xg_line(H, h10, "H")
        xg_line(A, a10, "A")
        for team, w in ((H, h10), (A, a10)):
            if w:
                out.append(f"{team} form {w.form_string} - {w.points_per_game:.2f} pts/game raw, "
                           f"{w.weighted_points_per_game:.2f} opponent-weighted.")
        if fx.home.elo is not None and fx.away.elo is not None:
            out.append(f"Elo: {H} {fx.home.elo:.0f}, {A} {fx.away.elo:.0f}.")
    elif t == "CARDS" and fx.referee:
        out.append(f"Referee {fx.referee.name} averages {fx.referee.cards_per_game:.2f} cards/game over "
                   f"{fx.referee.matches} matches.")
    elif t == "CORNERS":
        for team, recs in ((H, fx.home.recent[:10]), (A, fx.away.recent[:10])):
            c = [r.corners_for for r in recs if r.corners_for is not None]
            if c:
                out.append(f"{team} average {sum(c) / len(c):.1f} corners per game.")
    return out
