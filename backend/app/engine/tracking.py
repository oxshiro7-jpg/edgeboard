"""Phase 5: settlement, performance metrics, streaks, challenges, bankroll.

Judged on calibration, closing line value, EV and long-run ROI rather than
raw win rate. Staking is flat: a unit is a fixed share of bankroll and never
grows after losses (no Martingale or loss-chasing progression exists here).
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from .multiples import leg_return


@dataclass
class MatchResult:
    home_goals: int
    away_goals: int
    home_goals_ht: int
    away_goals_ht: int
    cards: Optional[int] = None
    corners: Optional[int] = None


@dataclass
class TrackedBet:
    id: str
    placed_at: datetime
    kickoff: datetime
    fixture_id: str
    competition: str
    market_key: str
    family: str
    label: str
    odds: float
    model_probability: float
    ev: float
    confidence: float
    risk: str
    model_version: str
    side: str = "neutral"              # home | away | neutral (for home/away breakdowns)
    stake_units: float = 1.0
    closing_odds: Optional[float] = None
    result: Optional[str] = None       # win | half_win | push | half_loss | loss | void
    profit_units: Optional[float] = None
    settled_at: Optional[datetime] = None
    source: str = "model"              # model | user


def side_of(key: str) -> str:
    p = key.split(":")
    if p[0] in ("1X2", "DNB", "AH", "TT", "FH1X2") and p[1] in ("H", "A"):
        return "home" if p[1] == "H" else "away"
    if p[0] in ("DC", "FHDC") and p[1] in ("1X", "X2"):
        return "home" if p[1] == "1X" else "away"
    return "neutral"


def settle(bet: TrackedBet, r: MatchResult, when: Optional[datetime] = None) -> TrackedBet:
    t = bet.market_key.split(":")[0]
    count = r.cards if t == "CARDS" else r.corners if t == "CORNERS" else 0
    if count is None:
        bet.result, bet.profit_units = "void", 0.0
    else:
        ret = leg_return(bet.market_key, bet.odds, r.home_goals, r.away_goals, r.home_goals_ht, r.away_goals_ht, count)
        o = bet.odds
        if abs(ret - o) < 1e-9:
            bet.result = "win"
        elif ret == 0:
            bet.result = "loss"
        elif abs(ret - 1) < 1e-9:
            bet.result = "push"
        elif abs(ret - (o + 1) / 2) < 1e-9:
            bet.result = "half_win"
        else:
            bet.result = "half_loss"
        bet.profit_units = round(bet.stake_units * (ret - 1), 4)
    bet.settled_at = when or bet.kickoff + timedelta(hours=2)
    return bet


# --------------------------------------------------------------- metrics

ODDS_BUCKETS = [(1.0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 5.0), (5.0, 1000.0)]
CONF_BUCKETS = [(0, 60), (60, 70), (70, 80), (80, 101)]


def _bucket(v: float, buckets) -> str:
    for lo, hi in buckets:
        if lo <= v < hi:
            return f"{lo:g}-{hi:g}" if hi < 100 else f"{lo:g}+"
    return "other"


def summarise(bets: list[TrackedBet]) -> dict[str, Any]:
    s = [b for b in bets if b.result and b.result != "void"]
    n = len(s)
    if n == 0:
        return {"picks": 0}
    wins = sum(1 for b in s if b.result in ("win", "half_win"))
    losses = sum(1 for b in s if b.result in ("loss", "half_loss"))
    pushes = sum(1 for b in s if b.result == "push")
    staked = sum(b.stake_units for b in s)
    profit = sum(b.profit_units or 0 for b in s)
    clv = [b.odds / b.closing_odds - 1 for b in s if b.closing_odds]
    exp_profit = sum(b.ev * b.stake_units for b in s)
    st = streaks(s)
    decided = wins + losses
    return {
        "picks": n, "wins": wins, "losses": losses, "pushes": pushes,
        "win_rate": round(wins / decided, 4) if decided else None,
        "average_odds": round(sum(b.odds for b in s) / n, 3),
        "staked_units": round(staked, 2),
        "profit_units": round(profit, 2),
        "roi": round(profit / staked, 4) if staked else None,          # return on stakes (= yield for flat 1u)
        "yield": round(profit / staked, 4) if staked else None,
        "expected_profit_units": round(exp_profit, 2),
        "average_ev": round(sum(b.ev for b in s) / n, 4),
        "clv": round(sum(clv) / len(clv), 4) if clv else None,
        "beat_closing_pct": round(sum(1 for c in clv if c > 0) / len(clv), 4) if clv else None,
        "longest_win_streak": st["longest_win"], "longest_loss_streak": st["longest_loss"],
    }


def breakdowns(bets: list[TrackedBet]) -> dict[str, list[dict]]:
    keys = {
        "competition": lambda b: b.competition,
        "market": lambda b: b.family,
        "odds_range": lambda b: _bucket(b.odds, ODDS_BUCKETS),
        "confidence_range": lambda b: _bucket(b.confidence, CONF_BUCKETS),
        "risk": lambda b: b.risk,
        "home_away": lambda b: b.side,
        "month": lambda b: b.kickoff.strftime("%Y-%m"),
        "model_version": lambda b: b.model_version,
    }
    out = {}
    for name, fn in keys.items():
        groups: dict[str, list[TrackedBet]] = defaultdict(list)
        for b in bets:
            groups[fn(b)].append(b)
        rows = [{"group": g, **summarise(v)} for g, v in groups.items()]
        out[name] = sorted([r for r in rows if r["picks"]], key=lambda r: r["group"])
    return out


def streaks(bets: list[TrackedBet]) -> dict[str, Any]:
    """Win/loss streaks ignore pushes and voids. EV streak counts consecutive
    picks placed with positive EV; day/week streaks count consecutive periods
    with positive profit."""
    order = sorted([b for b in bets if b.result and b.result != "void"], key=lambda b: (b.settled_at or b.kickoff, b.id))
    cur_w = cur_l = long_w = long_l = 0
    for b in order:
        if b.result in ("win", "half_win"):
            cur_w, cur_l = cur_w + 1, 0
        elif b.result in ("loss", "half_loss"):
            cur_l, cur_w = cur_l + 1, 0
        long_w, long_l = max(long_w, cur_w), max(long_l, cur_l)
    ev_cur = ev_long = 0
    for b in sorted(bets, key=lambda b: (b.placed_at, b.id)):
        ev_cur = ev_cur + 1 if b.ev > 0 else 0
        ev_long = max(ev_long, ev_cur)

    def period_streak(fmt) -> tuple[int, int]:
        pl: dict[str, float] = defaultdict(float)
        for b in order:
            pl[fmt(b.settled_at or b.kickoff)] += b.profit_units or 0
        cur = longest = 0
        for k in sorted(pl):
            cur = cur + 1 if pl[k] > 0 else 0
            longest = max(longest, cur)
        return cur, longest

    day_cur, day_long = period_streak(lambda d: d.strftime("%Y-%m-%d"))
    wk_cur, wk_long = period_streak(lambda d: f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}")
    return {"current_win": cur_w, "current_loss": cur_l, "longest_win": long_w, "longest_loss": long_l,
            "positive_ev_current": ev_cur, "positive_ev_longest": ev_long,
            "profitable_days_current": day_cur, "profitable_days_longest": day_long,
            "profitable_weeks_current": wk_cur, "profitable_weeks_longest": wk_long}


def equity_curve(bets: list[TrackedBet]) -> list[dict]:
    order = sorted([b for b in bets if b.result and b.result != "void"], key=lambda b: (b.settled_at or b.kickoff, b.id))
    total = exp = 0.0
    out = []
    for i, b in enumerate(order, 1):
        total += b.profit_units or 0
        exp += b.ev * b.stake_units
        out.append({"n": i, "t": (b.settled_at or b.kickoff).isoformat(), "profit": round(total, 3), "expected": round(exp, 3)})
    return out


# --------------------------------------------------------------- bankroll

@dataclass
class BankrollPlan:
    starting: float
    unit_pct: float = 0.01                 # 1 unit = 1% of bankroll
    mode: str = "fixed"                    # fixed: % of starting bankroll | percent_current: % of current, recalculated daily

    def __post_init__(self):
        if self.starting <= 0:
            raise ValueError("Starting bankroll must be positive")
        if not 0 < self.unit_pct <= 0.05:
            raise ValueError("Unit size must be between 0% and 5% of bankroll")
        if self.mode not in ("fixed", "percent_current"):
            raise ValueError("Mode must be fixed or percent_current")


def bankroll(plan: BankrollPlan, bets: list[TrackedBet]) -> dict[str, Any]:
    """Unit size depends only on the plan and (for percent_current) the
    bankroll at the start of the day, so it falls after losses and is never
    raised to chase them."""
    order = sorted([b for b in bets if b.result], key=lambda b: (b.settled_at or b.kickoff, b.id))
    bal = peak = plan.starting
    max_dd = 0.0
    wagered = 0.0
    history = [{"t": None, "balance": round(bal, 2)}]
    day_unit: dict[str, float] = {}
    for b in order:
        day = (b.settled_at or b.kickoff).strftime("%Y-%m-%d")
        if day not in day_unit:
            day_unit[day] = plan.starting * plan.unit_pct if plan.mode == "fixed" else bal * plan.unit_pct
        unit = day_unit[day]
        stake = unit * b.stake_units
        if b.result != "void":
            wagered += stake
            bal += (b.profit_units or 0) * unit
        peak = max(peak, bal)
        max_dd = max(max_dd, (peak - bal) / peak if peak else 0)
        history.append({"t": (b.settled_at or b.kickoff).isoformat(), "balance": round(bal, 2)})
    return {
        "starting": plan.starting, "current": round(bal, 2), "peak": round(peak, 2),
        "wagered": round(wagered, 2), "profit": round(bal - plan.starting, 2),
        "roi": round((bal - plan.starting) / wagered, 4) if wagered else None,
        "drawdown": round((peak - bal) / peak, 4) if peak else 0.0, "max_drawdown": round(max_dd, 4),
        "unit_pct": plan.unit_pct, "mode": plan.mode, "current_unit": round((plan.starting if plan.mode == "fixed" else bal) * plan.unit_pct, 2),
        "history": history,
        "staking_note": "Flat staking. Stakes never increase after a loss.",
    }


# --------------------------------------------------------------- challenges

PRESETS = {
    "10 Picks": ("picks", 10), "30 Picks": ("picks", 30), "50 Picks": ("picks", 50), "100 Picks": ("picks", 100),
    "7-Day Challenge": ("days", 7), "30-Day Challenge": ("days", 30),
}


@dataclass
class Challenge:
    id: str
    name: str
    kind: str                     # picks | days
    target: int
    starts_at: datetime
    rules: dict = field(default_factory=dict)   # min_confidence, min_ev, max_odds, families, source

    def ends_at(self) -> Optional[datetime]:
        return self.starts_at + timedelta(days=self.target) if self.kind == "days" else None


def _eligible(c: Challenge, b: TrackedBet) -> bool:
    r = c.rules
    if b.placed_at < c.starts_at:
        return False
    if c.ends_at() and b.placed_at >= c.ends_at():
        return False
    if r.get("min_confidence") is not None and b.confidence < r["min_confidence"]:
        return False
    if r.get("min_ev") is not None and b.ev < r["min_ev"]:
        return False
    if r.get("max_odds") is not None and b.odds > r["max_odds"]:
        return False
    if r.get("families") and b.family not in r["families"]:
        return False
    if r.get("source") and b.source != r["source"]:
        return False
    return True


def challenge_progress(c: Challenge, bets: list[TrackedBet], now: datetime, analysed: Optional[int] = None) -> dict:
    entries = sorted([b for b in bets if _eligible(c, b)], key=lambda b: (b.placed_at, b.id))
    if c.kind == "picks":
        entries = entries[: c.target]
        done = len(entries)
        progress = done / c.target
        complete = done >= c.target and all(b.result for b in entries)
    else:
        elapsed = min(c.target, max(0, (now - c.starts_at).days))
        done = elapsed
        progress = elapsed / c.target
        complete = now >= c.ends_at()   # type: ignore[operator]
    s = summarise(entries)
    st = streaks(entries)
    cells = 20
    filled = round(progress * cells)
    return {
        **asdict(c), "ends_at": c.ends_at().isoformat() if c.ends_at() else None,
        "progress": round(progress, 4), "done": done, "status": "completed" if complete else "active",
        "bar": "█" * filled + "░" * (cells - filled),
        "games_analysed": analysed, "picks_placed": len(entries),
        "settled": s.get("picks", 0), "wins": s.get("wins", 0), "losses": s.get("losses", 0),
        "current_streak": (f"W{st['current_win']}" if st["current_win"] else f"L{st['current_loss']}" if st["current_loss"] else "—"),
        "longest_streak": st["longest_win"],
        "average_odds": s.get("average_odds"), "wagered_units": s.get("staked_units", 0),
        "profit_units": s.get("profit_units", 0), "roi": s.get("roi"),
        "pending": sum(1 for b in entries if not b.result),
    }


def to_dict(b: TrackedBet) -> dict:
    d = asdict(b)
    for k in ("placed_at", "kickoff", "settled_at"):
        if d[k] is not None:
            d[k] = d[k].isoformat()
    return d


def from_iter(rows: Iterable[dict]) -> list[TrackedBet]:
    out = []
    for r in rows:
        r = dict(r)
        for k in ("placed_at", "kickoff", "settled_at"):
            if isinstance(r.get(k), str):
                r[k] = datetime.fromisoformat(r[k])
        out.append(TrackedBet(**r))
    return out


def sharpe_like(bets: list[TrackedBet]) -> Optional[float]:
    p = [b.profit_units or 0 for b in bets if b.result and b.result != "void"]
    if len(p) < 2:
        return None
    m = sum(p) / len(p)
    sd = math.sqrt(sum((x - m) ** 2 for x in p) / (len(p) - 1))
    return round(m / sd, 4) if sd > 0 else None   # mean profit per bet / its standard deviation
