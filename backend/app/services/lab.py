"""Phases 4-8 application services: multiples, slips, tracking, challenges,
bankroll, backtesting, calibration, model monitoring and the ML challenger.

The model's own track record comes from walk-forward analyses of finished
fixtures (provider history) that pass app.verification; with a database it
comes from `recommendations`. With no verified history the pages say so.
"""
from __future__ import annotations

import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from app.engine import multiples as mx
from app.engine import tracking as tk
from app.engine.backtest import BacktestParams, run_backtest
from app.engine.calibration import calibration_factors, drift, multiclass_scores
from app.engine.ml import SoftmaxRegression, outcome_index, train_and_evaluate, try_gradient_boosting

from .analysis_service import AnalysisService
from .analyst import AnalystTools, claude_answer
from app.providers.registry import ProviderNotConfigured

from .history import build_cases


class LabService:
    def __init__(self, analysis: AnalysisService):
        self.analysis = analysis
        self._cases: dict[tuple, list] = {}
        self._lock = threading.Lock()
        self.record = analysis.record                  # tracked bets live in the persistent store
        self.store = analysis.store
        self.challenges: dict[str, tk.Challenge] = {
            c["id"]: tk.Challenge(c["id"], c["name"], c["kind"], c["target"],
                                  datetime.fromisoformat(c["starts_at"]), c.get("rules") or {})
            for c in self.store.get("challenges", [])}
        plan = self.store.get("bankroll_plan")
        self.plan: Optional[tk.BankrollPlan] = tk.BankrollPlan(**plan) if plan else None
        self.ml_report: Optional[dict] = None
        self.ml_model: Optional[SoftmaxRegression] = None
        self.history_note: Optional[str] = None

    @property
    def user_bets(self) -> list[tk.TrackedBet]:
        return self.record.bets("user")

    def _save_challenges(self) -> None:
        self.store.put("challenges", [{"id": c.id, "name": c.name, "kind": c.kind, "target": c.target,
                                       "starts_at": c.starts_at.isoformat(), "rules": c.rules}
                                      for c in self.challenges.values()])

    # ------------------------------------------------------------ data
    def analyses(self, day: date) -> dict[str, dict]:
        """Verified fixtures only (see services.prediction)."""
        return {a["fixture_id"]: a for a in self.analysis.day(day)}

    def _legs_verified(self, day: date, fixture_ids) -> dict[str, dict]:
        an = self.analyses(day)
        missing = [f for f in fixture_ids if f not in an]
        if missing:
            raise ValueError(f"NO VERIFIED FIXTURE: {', '.join(missing)}")
        return an

    def cases(self, day: date) -> list:
        key = (day, self.analysis.config.version)
        with self._lock:
            if key in self._cases:
                return self._cases[key]
        end = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
        try:
            p = self.analysis.providers(day)
            cs = build_cases(p, end - timedelta(days=400), end, self.analysis.config.get(),
                             whitelist=self.analysis.whitelist)
            self.history_note = None
        except (NotImplementedError, ProviderNotConfigured) as e:
            cs = []
            self.history_note = str(e)
        with self._lock:
            self._cases[key] = cs
        return cs

    # ------------------------------------------------------------ phase 4
    def build(self, day: date, legs: int, mode: str, fixtures: Optional[list[str]] = None,
              allow_same_match: bool = False) -> dict:
        return mx.build_multiple(self.analyses(day), legs, mode, fixtures, allow_same_match)

    def alternatives(self, day: date) -> list[dict]:
        return mx.alternatives(self.analyses(day))

    def evaluate(self, day: date, legs: list[dict]) -> dict:
        an = self._legs_verified(day, [l["fixture_id"] for l in legs])
        objs = [mx.leg_from_market(an[l["fixture_id"]], l["market_key"], l.get("odds")) for l in legs]
        r = mx.evaluate_multiple(objs, an)
        return {**r.to_dict(), "diagnostics": mx.diagnose(r)}

    def optimise(self, day: date, legs: list[dict], min_legs: int = 2) -> dict:
        an = self._legs_verified(day, [l["fixture_id"] for l in legs])
        objs = [mx.leg_from_market(an[l["fixture_id"]], l["market_key"], l.get("odds")) for l in legs]
        return mx.optimise(objs, an, min_legs=min_legs, min_conf=self.analysis.config.get().thresholds.min_confidence)

    def slip(self, day: date, entries: list[dict]) -> dict:
        an = self._legs_verified(day, [e["fixture_id"] for e in entries])
        return mx.analyse_slip(entries, an, self.analysis.config.get().thresholds.min_confidence)

    # ------------------------------------------------------------ phase 5
    def model_record(self, day: date) -> list[tk.TrackedBet]:
        r = run_backtest(self.cases(day), BacktestParams(selection="recommended"))
        return tk.from_iter(r["bets"])

    def performance(self, day: date) -> dict:
        forward = self.record.bets("model")
        if forward:
            bets = forward
            source = ("forward record: every pick the app recommended, logged before kickoff at the price shown, "
                      "then given its closing price and final result")
            note = None if self.store.persistent else \
                "No database configured (DATABASE_URL): this record is lost whenever the server restarts."
        else:
            bets = self.model_record(day)
            source = "walk-forward record of every recommended selection on verified finished fixtures"
            note = self.history_note
        return {
            "source": source, "available": bool(bets), "note": note,
            "coverage": self.record.coverage(bets) if forward else None,
            "calibration": self.record.calibration(bets) if forward else None,
            "summary": tk.summarise(bets),
            "ev_realisation": _ev_realisation(bets),
            "streaks": tk.streaks(bets),
            "breakdowns": tk.breakdowns(bets),
            "equity": tk.equity_curve(bets),
        }

    def track(self, day: date, fixture_id: str, market_key: str, odds: Optional[float] = None,
              stake_units: float = 1.0) -> dict:
        if not 0 < stake_units <= 5:
            raise ValueError("Stake must be between 0 and 5 units")
        a = self._legs_verified(day, [fixture_id])[fixture_id]
        leg = mx.leg_from_market(a, market_key, odds)
        b = tk.TrackedBet(
            id=uuid.uuid4().hex[:12], placed_at=datetime.now(timezone.utc), kickoff=datetime.fromisoformat(a["kickoff"]),
            fixture_id=fixture_id, competition=a["competition"]["name"], market_key=market_key, family=leg.family,
            label=f"{leg.label} ({a['home']['short_name']} v {a['away']['short_name']})", odds=leg.odds,
            model_probability=leg.model_probability, ev=leg.ev or 0.0, confidence=leg.confidence, risk=leg.risk,
            model_version=a["model"]["version"], side=tk.side_of(market_key), stake_units=stake_units, source="user")
        return self.record.add_user_bet(b, a)

    def settle_user_bet(self, bet_id: str, result: dict) -> dict:
        return self.record.update_user_bet(bet_id, lambda b: tk.settle(b, tk.MatchResult(**result), datetime.now(timezone.utc)))

    def set_bankroll(self, starting: float, unit_pct: float, mode: str) -> dict:
        self.plan = tk.BankrollPlan(starting, unit_pct, mode)
        self.store.put("bankroll_plan", {"starting": starting, "unit_pct": unit_pct, "mode": mode})
        return self.bankroll()

    def bankroll(self) -> dict:
        if not self.plan:
            return {"configured": False}
        return {"configured": True, **tk.bankroll(self.plan, self.user_bets)}

    def create_challenge(self, name: str, kind: str, target: int, rules: dict, starts_at: Optional[datetime] = None) -> dict:
        if kind not in ("picks", "days") or not 1 <= target <= 1000:
            raise ValueError("Challenge needs kind picks or days and a target between 1 and 1000")
        c = tk.Challenge(uuid.uuid4().hex[:10], name, kind, target, starts_at or datetime.now(timezone.utc), rules)
        self.challenges[c.id] = c
        self._save_challenges()
        return tk.challenge_progress(c, self.user_bets, datetime.now(timezone.utc))

    def list_challenges(self, day: date, include_model_record: bool = True) -> list[dict]:
        now = datetime.now(timezone.utc)
        out = [tk.challenge_progress(c, self.user_bets, now) | {"source": "you"} for c in self.challenges.values()]
        if include_model_record:
            record = self.model_record(day)
            if record:
                start = sorted(record, key=lambda b: b.placed_at)[-24].placed_at if len(record) >= 24 else record[0].placed_at
                rec_ch = tk.Challenge("model-30", "30 Picks: model's best picks", "picks", 30, start, {"min_confidence": 70})
                out.append(tk.challenge_progress(rec_ch, record, now, analysed=len({b.fixture_id for b in record if b.placed_at >= start}))
                           | {"source": "model record"})
        return out

    # ------------------------------------------------------------ phase 6
    def backtest(self, day: date, params: BacktestParams) -> dict:
        r = run_backtest(self.cases(day), params)
        r["ev_realisation"] = _ev_realisation(tk.from_iter(r["bets"]))
        r["bets"] = r["bets"][-200:]
        return r

    def models(self, day: date) -> dict:
        cases = self.cases(day)
        cfg = self.analysis.config.get()
        r = run_backtest(cases, BacktestParams(selection="all_priced"))
        by_fam = r["calibration_by_family"]
        half = len(cases) // 2
        rows = [((c.analysis["prediction"]["probabilities"]["home"], c.analysis["prediction"]["probabilities"]["draw"],
                  c.analysis["prediction"]["probabilities"]["away"]), outcome_index(c.result)) for c in cases]
        recent, base = multiclass_scores(rows[half:]), multiclass_scores(rows[:half])
        comp_scores = {}
        for name in ("xg_poisson", "dixon_coles", "form", "elo", "market"):
            cr = []
            for c in cases:
                comp = next((x for x in c.analysis["prediction"]["components"] if x["name"] == name), None)
                if comp:
                    cr.append(((comp["home"], comp["draw"], comp["away"]), outcome_index(c.result)))
            comp_scores[name] = multiclass_scores(cr)
        # Which components actually run on today's data, with their effective weights.
        active: dict[str, float] = {}
        for a in self.analyses(day).values():
            for comp in a["prediction"]["components"]:
                active.setdefault(comp["name"], comp["weight"])
            break
        why_not = {"xg_poisson": "not running: no xG data (it would duplicate Dixon-Coles)",
                   "form": "not running: needs xG history", "elo": "not running: no team ratings source",
                   "market": "not running: no odds for this fixture", "dixon_coles": "not running"}
        component_status = {name: ({"active": True, "weight": active[name]} if name in active else
                                   {"active": False, "weight": 0.0, "reason": why_not.get(name, "not running")})
                            for name in ("dixon_coles", "xg_poisson", "form", "elo", "market")} if active else None
        calibration, cal_source, settled = r["calibration_all"], "backtest: every priced selection", len(cases)
        if not cases:
            fwd = [b for b in self.record.bets("model") if b.result]
            calibration = self.record.calibration(fwd)
            cal_source, settled = "forward record: the app's recommended picks, settled", len(fwd)
        return {
            "active_version": cfg.model_version,
            "config": cfg.to_dict(),
            "settled_matches": settled,
            "calibration_source": cal_source,
            "component_status": component_status,
            "result_forecast": r["result_forecast"],
            "components": comp_scores,
            "calibration": calibration,
            "calibration_by_family": {k: {kk: v[kk] for kk in ("n", "ece", "brier", "degraded", "message")} for k, v in by_fam.items()},
            "suggested_calibration_factors": calibration_factors(by_fam),
            "drift": drift(recent, base),
            "windows": {"baseline": base, "recent": recent},
            "ml": self.ml_report,
            "gbm_available": try_gradient_boosting(),
        }

    # ------------------------------------------------------------ phase 8
    def train_ml(self, day: date) -> dict:
        rep = train_and_evaluate(self.cases(day))
        self.ml_report = {k: v for k, v in rep.items() if k != "model"}
        if rep.get("status") == "promoted":
            self.ml_model = SoftmaxRegression.from_dict(rep["model"])
        return self.ml_report

    def promote_ml(self, weight: float, new_version: str) -> dict:
        """Admin action after a 'promoted' report: bump the model version and
        blend the ML component at the validated weight."""
        if not self.ml_model or not self.ml_report or self.ml_report["status"] != "promoted":
            raise ValueError("No promoted challenger to activate; train first")
        if not 0 < weight <= self.ml_report["recommended_weight"] + 1e-9:
            raise ValueError("Weight must be positive and at most the validated weight")
        self.analysis.ml_model = self.ml_model
        ens = dict(self.analysis.config.get().to_dict()["ensemble"], ml=weight)
        self.analysis.config.update({"ensemble": ens, "model_version": new_version})
        return self.analysis.admin_config()

    # ------------------------------------------------------------ phase 7
    def ask(self, day: date, question: str, context: Optional[dict] = None) -> dict[str, Any]:
        return claude_answer(question, AnalystTools(self.analysis.day(day)), context)


def _ev_realisation(bets: list[tk.TrackedBet]) -> dict:
    s = [b for b in bets if b.result and b.result != "void"]
    exp = sum(b.ev * b.stake_units for b in s)
    act = sum(b.profit_units or 0 for b in s)
    ratio = act / exp if exp > 0 else None
    note = None
    if ratio is not None and len(s) >= 100 and ratio < 0.5:
        note = (f"Realised profit is {ratio * 100:.0f}% of the EV the model claimed. Selected edges are overstated "
                "(selection on noisy estimates); raise the EV threshold or shrink harder toward the market.")
    return {"expected_units": round(exp, 2), "actual_units": round(act, 2),
            "ratio": round(ratio, 3) if ratio is not None else None, "note": note}
