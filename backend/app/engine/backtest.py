"""Phase 6: backtesting lab.

Each case is an analysis produced *before kickoff* with only the data
available then (the service builds it walk-forward), plus the result and the
closing prices. Picks are settled with the same rules as live tracking.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from .calibration import calibration_table, multiclass_scores
from .config import RISK_ORDER
from .markets import family
from .tracking import (MatchResult, TrackedBet, breakdowns, equity_curve, settle, sharpe_like, side_of, streaks,
                       summarise, to_dict)


@dataclass
class BacktestCase:
    analysis: dict
    result: dict                         # home_goals, away_goals, home_goals_ht, away_goals_ht, cards, corners
    closing: dict[str, float] = field(default_factory=dict)


@dataclass
class BacktestParams:
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    competitions: list[str] = field(default_factory=list)
    families: list[str] = field(default_factory=list)
    min_odds: float = 1.01
    max_odds: float = 1000.0
    min_confidence: float = 0.0
    min_ev: Optional[float] = None
    max_risk: str = "Very High"
    selection: str = "recommended"       # recommended | best_per_match | all_priced
    stake_units: float = 1.0
    label: str = ""


def _match_result(r: dict) -> MatchResult:
    return MatchResult(r["home_goals"], r["away_goals"], r["home_goals_ht"], r["away_goals_ht"],
                       r.get("cards"), r.get("corners"))


def _passes(m: dict, a: dict, p: BacktestParams) -> bool:
    o = m["bookmaker_odds"]
    if o is None or not (p.min_odds <= o <= p.max_odds):
        return False
    if p.families and m["family"] not in p.families:
        return False
    if m["confidence"] < p.min_confidence:
        return False
    if p.min_ev is not None and (m["ev"] is None or m["ev"] < p.min_ev):
        return False
    if RISK_ORDER.index(m["risk"]) > RISK_ORDER.index(p.max_risk):
        return False
    if p.selection in ("recommended", "best_per_match") and m["status"] != "recommended":
        return False
    return True


def run_backtest(cases: list[BacktestCase], p: BacktestParams) -> dict[str, Any]:
    chosen: list[TrackedBet] = []
    preds_all: list[tuple[float, float]] = []
    preds_by_family: dict[str, list[tuple[float, float]]] = defaultdict(list)
    x12_model, x12_market = [], []
    n_matches = 0
    for c in sorted(cases, key=lambda c: c.analysis["kickoff"]):
        a = c.analysis
        ko = datetime.fromisoformat(a["kickoff"])
        if p.date_from and ko < p.date_from or p.date_to and ko >= p.date_to:
            continue
        if p.competitions and a["competition"]["id"] not in p.competitions:
            continue
        n_matches += 1
        res = _match_result(c.result)
        y = 0 if res.home_goals > res.away_goals else 1 if res.home_goals == res.away_goals else 2
        pr = a["prediction"]["probabilities"]
        x12_model.append(((pr["home"], pr["draw"], pr["away"]), y))
        nv = {m["key"]: m["no_vig_probability"] for m in a["markets"] if m["key"].startswith("1X2:")}
        if all(nv.get(k) for k in ("1X2:H", "1X2:D", "1X2:A")):
            x12_market.append(((nv["1X2:H"], nv["1X2:D"], nv["1X2:A"]), y))
        eligible = []
        for m in a["markets"]:
            if m["bookmaker_odds"] is None:
                continue
            bet = _bet(a, m, ko, c, p.stake_units)
            settle(bet, res)
            if bet.result in ("win", "loss") and m["settlement"]["push"] == 0 and m["settlement"]["half_win"] == 0 \
                    and m["settlement"]["half_loss"] == 0:
                y_bin = 1.0 if bet.result == "win" else 0.0
                preds_all.append((m["model_probability"], y_bin))
                preds_by_family[m["family"]].append((m["model_probability"], y_bin))
            if _passes(m, a, p):
                eligible.append((m, bet))
        if p.selection == "best_per_match" and eligible:
            eligible = [max(eligible, key=lambda x: x[0]["pick_score"] or 0)]
        chosen += [b for _, b in eligible]
    s = summarise(chosen)
    curve = equity_curve(chosen)
    peak, max_dd = 0.0, 0.0
    for pt in curve:
        peak = max(peak, pt["profit"])
        max_dd = max(max_dd, peak - pt["profit"])
    sel_preds = [(b.model_probability, 1.0 if b.result == "win" else 0.0) for b in chosen if b.result in ("win", "loss")]
    st = streaks(chosen)
    return {
        "params": {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in vars(p).items()},
        "matches": n_matches,
        "summary": {
            "bets": s.get("picks", 0), "hit_rate": s.get("win_rate"), "roi": s.get("roi"),
            "profit_units": s.get("profit_units", 0.0), "max_drawdown_units": round(max_dd, 2),
            "average_odds": s.get("average_odds"), "expected_profit_units": s.get("expected_profit_units", 0.0),
            "actual_profit_units": s.get("profit_units", 0.0), "average_ev": s.get("average_ev"),
            "clv": s.get("clv"), "beat_closing_pct": s.get("beat_closing_pct"),
            "sharpe_like": sharpe_like(chosen), "longest_losing_streak": st["longest_loss"],
            "longest_winning_streak": st["longest_win"], "pushes": s.get("pushes", 0),
        },
        "equity": curve,
        "calibration_selected": calibration_table(sel_preds),
        "calibration_all": calibration_table(preds_all),
        "calibration_by_family": {f: calibration_table(v) for f, v in preds_by_family.items()},
        "result_forecast": {"model": multiclass_scores(x12_model), "market": multiclass_scores(x12_market)},
        "breakdowns": breakdowns(chosen),
        "bets": [to_dict(b) for b in chosen],
    }


def _bet(a: dict, m: dict, ko: datetime, c: BacktestCase, stake: float) -> TrackedBet:
    return TrackedBet(
        id=f"{a['fixture_id']}|{m['key']}", placed_at=datetime.fromisoformat(a["model"]["generated_at"]), kickoff=ko,
        fixture_id=a["fixture_id"], competition=a["competition"]["name"], market_key=m["key"], family=family(m["key"]),
        label=f"{m['label']} ({a['home']['short_name']} v {a['away']['short_name']})", odds=m["bookmaker_odds"],
        model_probability=m["model_probability"], ev=m["ev"] or 0.0, confidence=m["confidence"], risk=m["risk"],
        model_version=a["model"]["version"], side=side_of(m["key"]), stake_units=stake,
        closing_odds=c.closing.get(m["key"]),
    )
