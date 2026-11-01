"""Engine tests. Run with: python -m unittest discover -s tests  (or pytest)."""
import json
import math
import random
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine import elo
from app.engine.config import EngineConfig
from app.engine.goals import (MatchResult, fit_league, outcome_probs, reweight_to_outcomes,
                              score_matrix)
from app.engine.markets import (MarketInputs, asian_handicap, complete_market, goal_diff_distribution,
                                settlement, standard_keys)
from app.engine.odds import (SettlementDistribution, expected_value, fair_odds, implied_probability,
                             overround, remove_margin)
from app.engine.pipeline import analyse_fixture
from app.providers.the_odds_api import map_event
from app.services.assembly import build_fixture_input
from app.verification import TeamLookupCache, verify_fixture
from synthetic_world import synthetic_providers
from app.services.board import FilterError, all_picks, apply_filter, parse_filter

DAY = date(2026, 10, 4)
WHITELIST = ["epl", "laliga", "seriea", "bundesliga", "ligue1", "eredivisie", "primeira", "ucl", "facup"]


def build_providers(day):
    return synthetic_providers(day)


def verified_input(ref, p):
    """Test path mirrors the app: verify first, then assemble."""
    v = verify_fixture(p.football, ref.id, DAY, WHITELIST, listed=ref, teams=TeamLookupCache())
    assert v.ok, v.messages
    return build_fixture_input(v.verified, p)
NOW = datetime(2026, 10, 4, 9, tzinfo=timezone.utc)


class OddsMath(unittest.TestCase):
    def test_spec_worked_example(self):
        # Spec section 6: p=68%, odds 1.80 -> fair 1.47, EV +22.4%, implied 55.6%, edge +12.4pp
        p, o = 0.68, 1.80
        self.assertAlmostEqual(fair_odds(p), 1.47, places=2)
        self.assertAlmostEqual(expected_value(p, o) * 100, 22.4, places=1)
        self.assertAlmostEqual(implied_probability(o) * 100, 55.6, places=1)
        self.assertAlmostEqual((p - implied_probability(o)) * 100, 12.4, places=1)

    def test_margin_removal_sums_to_one(self):
        book = [1.90, 3.60, 4.20]
        self.assertGreater(overround(book), 0)
        for method in ("proportional", "power", "shin"):
            probs = remove_margin(book, method)
            self.assertAlmostEqual(sum(probs), 1.0, places=9)
            self.assertTrue(all(pr < implied_probability(o) for pr, o in zip(probs, book)))

    def test_power_method_takes_more_margin_from_longshot(self):
        book = [1.25, 4.50]
        prop = remove_margin(book, "proportional")
        power = remove_margin(book, "power")
        self.assertGreater(power[0], prop[0])
        self.assertLess(power[1], prop[1])

    def test_invalid_odds(self):
        with self.assertRaises(ValueError):
            implied_probability(1.0)

    def test_settlement_fair_odds_give_zero_ev(self):
        s = SettlementDistribution(win=0.45, half_win=0.10, push=0.05, half_loss=0.10, loss=0.30)
        self.assertAlmostEqual(s.ev(s.fair_odds()), 0.0, places=9)


class GoalModels(unittest.TestCase):
    def test_matrix_normalised_and_dixon_coles(self):
        for rho in (0.0, -0.1):
            m = score_matrix(1.6, 1.1, rho=rho)
            self.assertAlmostEqual(sum(map(sum, m)), 1.0, places=9)
        indep = score_matrix(1.2, 1.0, rho=0.0)
        dc = score_matrix(1.2, 1.0, rho=-0.1)
        self.assertGreater(dc[0][0], indep[0][0])   # negative rho lifts 0-0 and 1-1
        self.assertGreater(dc[1][1], indep[1][1])

    def test_reweight_hits_target(self):
        m = score_matrix(1.5, 1.0)
        target = (0.40, 0.30, 0.30)
        r = reweight_to_outcomes(m, target)
        for a, b in zip(outcome_probs(r), target):
            self.assertAlmostEqual(a, b, places=9)

    def test_fit_league_recovers_strength_order(self):
        rng = random.Random(1)
        true_att = {"A": 1.6, "B": 1.0, "C": 0.6}
        res, start = [], datetime(2026, 1, 1)
        for k in range(300):
            h, a = rng.sample(list(true_att), 2)
            lh, la = 1.35 * true_att[h] * 1.1, 1.35 * true_att[a]
            res.append(MatchResult(start + timedelta(days=k), h, a, _poisson(rng, lh), _poisson(rng, la)))
        fit = fit_league(res, as_of=start + timedelta(days=301), half_life_days=10_000)
        self.assertGreater(fit.attack["A"], fit.attack["B"])
        self.assertGreater(fit.attack["B"], fit.attack["C"])
        self.assertGreater(fit.home_advantage, 1.0)

    def test_fit_league_uses_only_past(self):
        res = [MatchResult(datetime(2026, 5, 1), "A", "B", 3, 0)]
        with self.assertRaises(ValueError):
            fit_league(res, as_of=datetime(2026, 4, 1))

    def test_elo_probabilities(self):
        h, d, a = elo.outcome_probs(1600, 1500)
        self.assertAlmostEqual(h + d + a, 1.0, places=9)
        self.assertGreater(h, a)
        n_h, _, n_a = elo.outcome_probs(1500, 1500, neutral=True)
        self.assertAlmostEqual(n_h, n_a, places=9)


class Markets(unittest.TestCase):
    def setUp(self):
        self.ft = score_matrix(1.7, 1.0, rho=-0.08)
        self.mi = MarketInputs(self.ft, score_matrix(0.8, 0.45), score_matrix(0.9, 0.55))
        self.h, self.d, self.a = outcome_probs(self.ft)

    def test_handicap_identities(self):
        self.assertAlmostEqual(settlement("AH:H:-0.5", self.mi).win, self.h, places=9)
        self.assertAlmostEqual(settlement("AH:A:+0.5", self.mi).win, self.a + self.d, places=9)
        dnb = settlement("DNB:H", self.mi)
        self.assertAlmostEqual(dnb.push, self.d, places=9)
        self.assertAlmostEqual(dnb.effective_probability(), self.h / (self.h + self.a), places=9)

    def test_quarter_line_splits_stake(self):
        gd = goal_diff_distribution(self.ft)
        q = asian_handicap(gd, -0.25)    # half on 0, half on -0.5
        self.assertAlmostEqual(q.win, self.h, places=9)
        self.assertAlmostEqual(q.half_loss, self.d, places=9)
        q2 = asian_handicap(gd, -0.75)   # half on -0.5, half on -1
        self.assertAlmostEqual(q2.half_win, gd[1], places=9)

    def test_complementary_selections_sum_to_one(self):
        for over, under in (("OU:2.5:over", "OU:2.5:under"), ("BTTS:yes", "BTTS:no"),
                            ("TT:H:1.5:over", "TT:H:1.5:under"), ("FHOU:0.5:over", "FHOU:0.5:under")):
            s = settlement(over, self.mi).win + settlement(under, self.mi).win
            self.assertAlmostEqual(s, 1.0, places=9)
        dc = settlement("DC:1X", self.mi).win
        self.assertAlmostEqual(dc, self.h + self.d, places=9)

    def test_cards_need_referee_data(self):
        self.assertIsNone(settlement("CARDS:4.5:over", self.mi))
        mi = MarketInputs(self.ft, self.ft, self.ft, cards_mean=4.2)
        self.assertIsNotNone(settlement("CARDS:4.5:over", mi))

    def test_complete_market_groups(self):
        self.assertEqual(complete_market("AH:A:+0.5"), ["AH:H:-0.5", "AH:A:+0.5"])
        self.assertEqual(len(set(standard_keys())), len(standard_keys()))


class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = build_providers(DAY)
        cls.cfg = EngineConfig()
        cls.refs = cls.p.football.fixtures(DAY, [])
        cls.analyses = [analyse_fixture(verified_input(r, cls.p), cls.cfg, now=NOW) for r in cls.refs]

    def test_serialisable_and_versioned(self):
        for a in self.analyses:
            json.dumps(a)
            self.assertEqual(a["model"]["version"], self.cfg.model_version)
            self.assertEqual(a["provenance"]["fixture_id"], a["fixture_id"])
            probs = a["prediction"]["probabilities"]
            self.assertAlmostEqual(sum(probs.values()), 1.0, places=3)

    def test_deterministic(self):
        again = analyse_fixture(verified_input(self.refs[0], build_providers(DAY)), self.cfg, now=NOW)
        self.assertEqual(again["model"]["input_hash"], self.analyses[0]["model"]["input_hash"])
        self.assertEqual(again["markets"], self.analyses[0]["markets"])

    def test_confidence_is_not_probability(self):
        ms = [m for a in self.analyses for m in a["markets"] if m["bookmaker_odds"]]
        self.assertTrue(any(abs(m["confidence"] - m["model_probability"] * 100) > 10 for m in ms))

    def test_low_data_quality_caps_confidence(self):
        for a in self.analyses:
            for m in a["markets"]:
                self.assertLessEqual(m["confidence"], a["data_quality"]["score"] + 10)

    def test_recommendations_respect_gates(self):
        th = self.cfg.thresholds
        for a in self.analyses:
            for m in a["markets"]:
                if m["status"] == "recommended":
                    self.assertGreaterEqual(m["ev"], th.min_ev)
                    self.assertGreaterEqual(m["confidence"], th.min_confidence)
                    self.assertGreaterEqual(m["bookmaker_odds"], th.min_odds)
                    self.assertTrue(m["why"])
                else:
                    self.assertTrue(m["reasons_rejected"])

    def test_likely_but_no_value_is_passed(self):
        rejected = [r for a in self.analyses for m in a["markets"] for r in m.get("reasons_rejected", [])]
        self.assertTrue(any("offers no value" in r for r in rejected))

    def test_thin_data_fixture_is_no_bet(self):
        thin = next(a for a in self.analyses if a["home"]["short_name"] == "WXT")
        self.assertTrue(thin["no_bet"]["no_bet"])
        self.assertTrue(thin["no_bet"]["reasons"])
        self.assertTrue(any("xG" in g for g in thin["data_quality"]["gaps"]))

    def test_cards_only_with_referee(self):
        for a, ref in zip(self.analyses, self.refs):
            has_ref = self.p.football.referee(ref.id) is not None
            has_cards = any(m["key"].startswith("CARDS") for m in a["markets"])
            self.assertEqual(has_ref, has_cards)

    def test_ev_matches_formula(self):
        for a in self.analyses:
            for m in a["markets"]:
                s = m["settlement"]
                if m["bookmaker_odds"] and s["push"] == s["half_win"] == s["half_loss"] == 0:
                    self.assertAlmostEqual(m["ev"], m["model_probability"] * m["bookmaker_odds"] - 1, delta=0.002)  # rounding


class Filter(unittest.TestCase):
    def test_parse_and_apply(self):
        clauses = parse_filter("Confidence > 70; EV > 5%; Odds 1.30-1.80; Risk <= Moderate; Market = Over")
        self.assertIn(("ev", ">", 0.05), clauses)
        self.assertIn(("bookmaker_odds", ">=", 1.30), clauses)
        p = build_providers(DAY)
        analyses = [analyse_fixture(verified_input(r, p), now=NOW) for r in p.football.fixtures(DAY, [])]
        for pick in apply_filter(all_picks(analyses), clauses):
            self.assertGreater(pick["confidence"], 70)
            self.assertTrue(1.30 <= pick["bookmaker_odds"] <= 1.80)
            self.assertIn("over", pick["label"].lower())

    def test_bad_filter(self):
        with self.assertRaises(FilterError):
            parse_filter("vibes > 3")


class OddsApiMapping(unittest.TestCase):
    def test_map_event(self):
        ev = {"home_team": "Arsenal FC", "away_team": "Chelsea", "commence_time": "2026-10-04T15:00:00Z",
              "bookmakers": [{"key": "bk", "last_update": "2026-10-04T08:00:00Z", "markets": [
                  {"key": "h2h", "outcomes": [{"name": "Arsenal FC", "price": 1.9}, {"name": "Chelsea", "price": 4.1},
                                              {"name": "Draw", "price": 3.6}]},
                  {"key": "totals", "outcomes": [{"name": "Over", "price": 1.85, "point": 2.5},
                                                 {"name": "Under", "price": 2.0, "point": 2.5}]},
                  {"key": "spreads", "outcomes": [{"name": "Arsenal FC", "price": 2.0, "point": -0.5},
                                                  {"name": "Chelsea", "price": 1.9, "point": 0.5}]}]}]}
        q = map_event(ev, "Arsenal FC", "Chelsea")
        self.assertEqual(q["1X2:H"][0].odds, 1.9)
        self.assertEqual(q["1X2:D"][0].odds, 3.6)
        self.assertEqual(q["OU:2.5:over"][0].odds, 1.85)
        self.assertEqual(q["AH:H:-0.5"][0].odds, 2.0)
        self.assertEqual(q["AH:A:+0.5"][0].odds, 1.9)
        # no fuzzy matching: "Arsenal" is not "Arsenal FC"
        self.assertNotIn("1X2:H", map_event(ev, "Arsenal", "Chelsea"))


def _poisson(rng, lam):
    l, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p < l:
            return k
        k += 1


if __name__ == "__main__":
    unittest.main()
