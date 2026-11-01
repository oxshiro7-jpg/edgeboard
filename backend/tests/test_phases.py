"""Tests for phases 4-8: multiples, tracking, backtesting/calibration, analyst, ML."""
import math
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine import multiples as mx
from app.engine import tracking as tk
from app.engine.backtest import BacktestParams, run_backtest
from app.engine.calibration import calibration_table
from app.engine.goals import outcome_probs
from app.engine.ml import SoftmaxRegression, train_and_evaluate
from app.services.analysis_service import AnalysisService
from app.services.analyst import AnalystTools, route
from app.services.lab import LabService
from app.settings import Settings
from synthetic_world import synthetic_providers

DAY = date(2026, 10, 4)


class Shared:
    svc = None
    lab = None

    @classmethod
    def get(cls):
        if cls.svc is None:
            cls.svc = AnalysisService(Settings(), providers_factory=synthetic_providers)
            cls.lab = LabService(cls.svc)
        return cls.svc, cls.lab


class Multiples(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        svc, _ = Shared.get()
        cls.an = {a["fixture_id"]: a for a in svc.day(DAY)}
        cls.fids = list(cls.an)

    def test_single_leg_matches_market_probability(self):
        a = self.an[self.fids[2]]
        for key in ("1X2:H", "OU:2.5:over", "BTTS:yes", "TT:A:0.5:over"):
            leg = mx.leg_from_market(a, key)
            r = mx.evaluate_multiple([leg], self.an)
            self.assertAlmostEqual(r.probability, leg.model_probability, places=3)
            self.assertAlmostEqual(r.ev, leg.model_probability * leg.odds - 1, places=3)

    def test_spec_example_arithmetic(self):
        # Spec 12: odds 2.34, probability 49% -> fair 2.04, edge +14.7%
        self.assertAlmostEqual(2.34 / (0.49 * 2.34), 2.04, places=2)
        self.assertAlmostEqual((0.49 * 2.34 - 1) * 100, 14.7, places=1)

    def test_cross_match_legs_are_independent(self):
        l1 = mx.leg_from_market(self.an[self.fids[1]], "OU:2.5:over")
        l2 = mx.leg_from_market(self.an[self.fids[3]], "BTTS:yes")
        r = mx.evaluate_multiple([l1, l2], self.an)
        self.assertAlmostEqual(r.probability, r.naive_probability, places=6)
        self.assertEqual(r.correlations, [])

    def test_same_match_correlation_detected(self):
        a = self.an[self.fids[2]]
        win = mx.leg_from_market(a, "1X2:H")
        goals = mx.leg_from_market(a, "TT:H:1.5:over")
        r = mx.evaluate_multiple([win, goals], self.an)
        self.assertGreater(r.probability, r.naive_probability)      # positively correlated
        self.assertTrue(any(c["kind"] == "positive" for c in r.correlations))

    def test_conflicting_legs(self):
        a = self.an[self.fids[2]]
        r = mx.evaluate_multiple([mx.leg_from_market(a, "OU:2.5:over"), mx.leg_from_market(a, "OU:1.5:under")], self.an)
        self.assertAlmostEqual(r.probability, 0.0, places=9)
        self.assertTrue(any(c["kind"] == "conflicting" for c in r.correlations))

    def test_joint_matches_score_matrix(self):
        a = self.an[self.fids[2]]
        m = a["prediction"]["score_matrix_full"]
        exact = sum(p for i, row in enumerate(m) for j, p in enumerate(row) if i > j and i + j > 2.5)
        r = mx.evaluate_multiple([mx.leg_from_market(a, "1X2:H"), mx.leg_from_market(a, "OU:2.5:over")], self.an)
        self.assertAlmostEqual(r.probability, exact, places=4)

    def test_push_is_void_in_multiple(self):
        a = self.an[self.fids[2]]
        dnb = mx.leg_from_market(a, "DNB:H", odds=1.5)
        r = mx.evaluate_multiple([dnb], self.an)
        h, d, aw = outcome_probs(a["prediction"]["score_matrix_full"])
        self.assertAlmostEqual(r.ev, h * 1.5 + d * 1.0 - 1, places=3)

    def test_half_time_leg(self):
        a = self.an[self.fids[2]]
        r = mx.evaluate_multiple([mx.leg_from_market(a, "FHOU:0.5:over"), mx.leg_from_market(a, "OU:0.5:over")], self.an)
        fh_over = r.probability   # FH goal implies FT goal, so joint == P(FH over 0.5)
        self.assertLess(fh_over, mx.leg_from_market(a, "OU:0.5:over").model_probability)

    def test_builder_and_alternatives(self):
        alts = mx.alternatives(self.an)
        self.assertEqual(len(alts), 5)
        for alt in alts:
            if alt["built"]:
                legs = alt["multiple"]["legs"]
                self.assertEqual(len({l["fixture_id"] for l in legs}), len(legs))   # one leg per match by default
                self.assertGreater(alt["multiple"]["ev"], 0)
            else:
                self.assertIn("criteria", alt["reason"])

    def test_optimise_explains_every_removal(self):
        legs = [mx.leg_from_market(self.an[self.fids[i]], k) for i, k in
                [(1, "BTTS:yes"), (2, "1X2:H"), (5, "OU:2.5:over"), (7, "OU:3.5:under"), (3, "OU:2.5:under")]]
        res = mx.optimise(legs, self.an, min_legs=2)
        for rem in res["removed"]:
            self.assertTrue(rem["reasons"])
        self.assertGreaterEqual(res["optimised"]["health"], res["original"]["health"])
        self.assertIn("weakest_leg", res["diagnostics"])

    def test_slip_analyser(self):
        r = mx.analyse_slip([{"fixture_id": self.fids[1], "market_key": "BTTS:yes"},
                             {"fixture_id": self.fids[2], "market_key": "OU:2.5:over", "odds": 1.30}], self.an)
        self.assertIn("health", r)
        self.assertTrue(r["main_problem"])
        self.assertEqual(r["legs"][1]["odds"], 1.30)


class Tracking(unittest.TestCase):
    def _bet(self, key, odds, i=0, **kw):
        t = datetime(2026, 5, 1, 15, tzinfo=timezone.utc) + timedelta(days=i)
        return tk.TrackedBet(id=str(i), placed_at=t, kickoff=t, fixture_id=f"f{i}", competition="X", market_key=key,
                             family="goals", label=key, odds=odds, model_probability=0.5, ev=kw.get("ev", 0.05),
                             confidence=70, risk="Low", model_version="v", closing_odds=kw.get("close"))

    def test_settlement_rules(self):
        r = tk.MatchResult(2, 1, 1, 0)
        cases = {"1X2:H": "win", "1X2:D": "loss", "OU:2.5:over": "win", "AH:H:-1": "push", "AH:H:-0.75": "half_win",
                 "AH:A:+0.75": "half_loss", "BTTS:yes": "win", "FHOU:0.5:over": "win", "DNB:A": "loss"}
        for key, want in cases.items():
            self.assertEqual(tk.settle(self._bet(key, 2.0), r).result, want, key)
        self.assertAlmostEqual(tk.settle(self._bet("AH:H:-0.75", 2.0), r).profit_units, 0.5)

    def test_metrics_and_streaks(self):
        bets = []
        outcomes = [(2, 0), (0, 1), (0, 1), (0, 1), (3, 1), (2, 0)]
        for i, (h, a) in enumerate(outcomes):
            bets.append(tk.settle(self._bet("1X2:H", 2.0, i, close=1.9), tk.MatchResult(h, a, 0, 0)))
        s = tk.summarise(bets)
        self.assertEqual((s["wins"], s["losses"]), (3, 3))
        self.assertAlmostEqual(s["profit_units"], 0.0)
        self.assertAlmostEqual(s["clv"], 2.0 / 1.9 - 1, places=4)
        st = tk.streaks(bets)
        self.assertEqual(st["longest_loss"], 3)
        self.assertEqual(st["current_win"], 2)

    def test_bankroll_never_raises_stake_after_losses(self):
        bets = [tk.settle(self._bet("1X2:H", 2.0, i), tk.MatchResult(0, 1, 0, 0)) for i in range(5)]
        r = tk.bankroll(tk.BankrollPlan(1000, 0.02, "percent_current"), bets)
        self.assertLess(r["current_unit"], 20)
        self.assertGreater(r["max_drawdown"], 0)
        with self.assertRaises(ValueError):
            tk.BankrollPlan(1000, 0.25)

    def test_challenge_progress(self):
        bets = [tk.settle(self._bet("1X2:H", 2.0, i), tk.MatchResult(1, 0, 0, 0)) for i in range(12)]
        c = tk.Challenge("c", "10 Picks", "picks", 10, datetime(2026, 4, 1, tzinfo=timezone.utc))
        p = tk.challenge_progress(c, bets, datetime(2026, 6, 1, tzinfo=timezone.utc))
        self.assertEqual(p["picks_placed"], 10)
        self.assertEqual(p["status"], "completed")
        self.assertEqual(p["bar"].count("█"), 20)


class BacktestAndCalibration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, lab = Shared.get()
        cls.cases = lab.cases(DAY)

    def test_walk_forward_has_no_lookahead(self):
        for c in self.cases[:40]:
            ko = datetime.fromisoformat(c.analysis["kickoff"])
            self.assertLessEqual(datetime.fromisoformat(c.analysis["model"]["generated_at"]), ko)

    def test_backtest_outputs(self):
        r = run_backtest(self.cases, BacktestParams(selection="recommended", min_odds=1.3, max_odds=3.0))
        s = r["summary"]
        for k in ("bets", "hit_rate", "roi", "profit_units", "max_drawdown_units", "average_odds",
                  "expected_profit_units", "sharpe_like", "longest_losing_streak"):
            self.assertIn(k, s)
        self.assertTrue(all(1.3 <= b["odds"] <= 3.0 for b in r["bets"]))
        self.assertEqual(len(r["calibration_all"]["rows"]), 8)

    def test_calibration_flags(self):
        good = [(0.7, 1.0)] * 70 + [(0.7, 0.0)] * 30
        bad = [(0.75, 1.0)] * 61 + [(0.75, 0.0)] * 39
        self.assertFalse(calibration_table(good)["degraded"])
        t = calibration_table(bad * 3)
        self.assertTrue(t["degraded"])
        self.assertIn("75-80%", t["message"])


class AnalystAndML(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        svc, lab = Shared.get()
        cls.tools = AnalystTools(svc.day(DAY))
        cls.lab = lab

    def test_answers_use_tool_output(self):
        r = route("Which match has the strongest statistical edge today?", self.tools)
        self.assertTrue(r["tool_calls"])
        e = r["tool_calls"][0]["result"]["largest_edge"]
        self.assertIn(f"{e['bookmaker_odds']:.2f}", r["answer"])

    def test_why_not(self):
        fid = self.tools.analyses[3]["fixture_id"]
        r = route("Why is the draw not recommended?", self.tools, {"fixture_id": fid})
        self.assertTrue(r["answer"])
        self.assertEqual(r["tool_calls"][0]["tool"], "explain")

    def test_filter_question(self):
        r = route("Find 5 picks with confidence above 70", self.tools)
        for p in r["tool_calls"][0]["result"]["picks"]:
            self.assertGreater(p["confidence"], 70)
        self.assertLessEqual(len(r["tool_calls"][0]["result"]["picks"]), 5)

    def test_softmax_learns(self):
        X = [[i / 50.0, 0.0] for i in range(-50, 50)]
        y = [0 if x[0] > 0.2 else 2 if x[0] < -0.2 else 1 for x in X]
        m = SoftmaxRegression(epochs=800, lr=0.5, l2=0.0).fit(X, y)
        self.assertGreater(m.predict_proba([0.9, 0])[0], 0.6)
        self.assertGreater(m.predict_proba([-0.9, 0])[2], 0.6)

    def test_champion_challenger_reports(self):
        rep = train_and_evaluate(self.lab.cases(DAY))
        self.assertIn(rep["status"], ("promoted", "rejected"))
        self.assertIn("ensemble", rep["test"])
        if rep["status"] == "rejected":
            self.assertEqual(rep["recommended_weight"], 0.0)



class LineupAlerts(unittest.TestCase):
    def test_lineup_recalculation_reports_change(self):
        from app.services.analysis_service import lineup_update
        svc, _ = Shared.get()
        sar = next(a for a in svc.day(DAY) if a["home"]["short_name"] == "SAR")
        r = lineup_update(svc, DAY, sar["fixture_id"])
        self.assertTrue(r["updated"])
        self.assertLess(r["after"]["home"], r["before"]["home"])      # star striker and CB out
        kinds = {a["type"] for a in r["alerts"]}
        self.assertIn("player_ruled_out", kinds)
        self.assertIn("prediction_changed", kinds)


if __name__ == "__main__":
    unittest.main()
