"""Walk-forward dataset builder for backtests, calibration, tracking and ML.

For each finished fixture the analysis is produced exactly as it would have
been two hours before kickoff: team history, Elo and league baselines are
cut at kickoff, and the price used is the last quote before the close. The
closing price is kept separately for CLV.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Optional

from app.engine.backtest import BacktestCase
from app.engine.config import EngineConfig
from app.engine.pipeline import analyse_fixture
from app.engine.types import OddsSeries
from app.providers.registry import Providers

from app.verification import verify_historical

from .assembly import build_fixture_input


def build_cases(p: Providers, start: datetime, end: datetime, cfg: Optional[EngineConfig] = None,
                warmup_matches: int = 6, ml_model=None, whitelist: Optional[list[str]] = None) -> list[BacktestCase]:
    if not hasattr(p.football, "historical_fixtures"):
        raise NotImplementedError("No verified match history is available from this provider yet")
    cfg = cfg or EngineConfig()
    wl = whitelist or [c.id for c in p.football.competitions()]
    cases = []
    for ref in p.football.historical_fixtures(start, end):  # type: ignore[attr-defined]
        v = verify_historical(p.football, ref, wl)
        if not v.ok:
            continue          # unverifiable history never enters backtests or the track record
        fx = build_fixture_input(v.verified, p)  # type: ignore[arg-type]
        if min(len(fx.home.recent), len(fx.away.recent)) < warmup_matches:
            continue
        closing = {k: s.current for k, s in fx.odds.items()}
        pre = {}
        for k, s in fx.odds.items():
            taken = s.history[-1].odds if s.history else s.current
            pre[k] = OddsSeries(k, s.opening, taken, s.history[:-1] if s.history else [])
        fx_pre = copy.copy(fx)
        fx_pre.odds = pre
        a = analyse_fixture(fx_pre, cfg, now=ref.kickoff - timedelta(hours=2), ml_model=ml_model)
        cases.append(BacktestCase(a, p.football.result(ref.id), closing))  # type: ignore[attr-defined]
    return cases
