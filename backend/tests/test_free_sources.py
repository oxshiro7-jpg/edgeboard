"""Free stack: football-data.org fixtures/results + The Odds API, end to end on fake transports."""
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fake_free_sources as F  # noqa: E402
from app.providers.football_data import FootballDataOrgProvider  # noqa: E402
from app.providers.registry import ProviderNotConfigured, Providers, build_providers  # noqa: E402
from app.providers.the_odds_api import TheOddsApiProvider, canonical  # noqa: E402
from app.providers.web_cache import SourceError, WebCache  # noqa: E402
from app.services.analysis_service import AnalysisService  # noqa: E402
from app.settings import Settings  # noqa: E402

DAY = date(2026, 10, 4)


def service(today, events=(), finished=None):
    fd = F.fd_transport(today, finished)
    od = F.odds_transport(list(events))
    def factory(day):
        return Providers(FootballDataOrgProvider("t", http=fd), TheOddsApiProvider("k", http=od), None, None)
    return AnalysisService(Settings(football_provider="football_data"), providers_factory=factory), fd


class FreeStack(unittest.TestCase):
    def test_slate_is_exactly_the_provider_listing(self):
        today = [F.match(1, 501, 502, F.KO), F.match(2, 503, 504, F.KO + timedelta(hours=2)),
                 F.match(3, 505, 506, F.KO + timedelta(days=1))]                  # tomorrow: not on today's slate
        svc, _ = service(today, [F.odds_event("Test Rovers", "Test United", F.KO)])
        slate = {e["fixture_id"]: e for e in svc.slate(DAY)}
        self.assertEqual(set(slate), {"1", "2"})
        self.assertEqual(slate["1"]["status"], "VERIFIED")
        self.assertEqual(slate["2"]["status"], "ODDS UNAVAILABLE")
        a = slate["1"]["analysis"]
        self.assertEqual(a["provenance"]["provider"], "football_data")
        self.assertEqual(a["model"]["version"], "FootballModel_v0.3.1-scores-only")
        self.assertIn("Injury feed unavailable", a["data_quality"]["gaps"])
        self.assertFalse([m for m in slate["2"]["analysis"]["markets"] if m["status"] == "recommended"])

    def test_states_and_unknowns(self):
        today = [F.match(1, 501, 502, F.KO, status="POSTPONED"), F.match(2, 503, 504, F.KO, status="FINISHED", score=(1, 0))]
        svc, _ = service(today)
        st = {e["fixture_id"]: e["status"] for e in svc.slate(DAY)}
        self.assertEqual(st, {"1": "POSTPONED", "2": "UNVERIFIED"})
        self.assertEqual(svc.match(DAY, "424242")["status"], "UNVERIFIED")    # id the provider never returned

    def test_team_name_must_match_provider(self):
        m = F.match(1, 501, 502, F.KO)
        m["awayTeam"]["name"] = "Real Sotavento"           # listing disagrees with the provider's own team record
        svc, _ = service([m], finished=F.season())
        e = svc.slate(DAY)[0]
        self.assertEqual(e["status"], "UNVERIFIED")
        self.assertIn("TEAM NOT VERIFIED: Real Sotavento", e["messages"][0])

    def test_one_call_per_competition(self):
        today = [F.match(1, 501, 502, F.KO), F.match(2, 503, 504, F.KO)]
        svc, fd = service(today)
        svc.slate(DAY)
        paths = [p for p, _ in fd.calls_log]
        self.assertEqual(paths.count("/competitions/PL/matches"), 1)
        self.assertLessEqual(len(paths), 3)

    def test_registry_requires_token(self):
        with self.assertRaises(ProviderNotConfigured):
            build_providers("football_data", "the_odds_api", DAY, {})


class OddsMatching(unittest.TestCase):
    def setUp(self):
        self.fx = FootballDataOrgProvider("t", http=F.fd_transport([F.match(1, 501, 502, F.KO)]))
        self.ref = self.fx.fixtures(DAY, ["epl"])[0]

    def odds(self, events):
        return TheOddsApiProvider("k", http=F.odds_transport(events)).odds(self.ref, "Test Rovers FC", "Test United AFC")

    def test_legal_form_names_match(self):
        o = self.odds([F.odds_event("Test Rovers", "Test United", F.KO)])
        self.assertEqual(o["1X2:H"].current, 2.1)
        self.assertEqual(o["OU:2.5:over"].current, 1.9)

    def test_no_match_cases(self):
        self.assertEqual(self.odds([F.odds_event("Test Rovers Reserves", "Test United", F.KO)]), {})
        self.assertEqual(self.odds([F.odds_event("Test Rovers", "Test United", F.KO + timedelta(hours=5))]), {})
        both = [F.odds_event("Test Rovers", "Test United", F.KO), F.odds_event("Test Rovers", "Test United", F.KO)]
        self.assertEqual(self.odds(both), {})                                   # ambiguous: never pick one
        self.assertEqual(self.odds([F.odds_event("Test United", "Test Rovers", F.KO)]), {})   # sides swapped

    def test_canonical_is_not_similarity(self):
        self.assertNotEqual(canonical("Real Sotavento"), canonical("Real Madrid"))
        self.assertEqual(canonical("Real Madrid CF"), canonical("Real Madrid"))
        self.assertEqual(canonical("Brighton & Hove Albion FC"), canonical("Brighton and Hove Albion"))
        self.assertEqual(canonical("FC Internazionale Milano"), canonical("Inter Milan"))
        self.assertNotEqual(canonical("Manchester City FC"), canonical("Manchester United FC"))


class Cache(unittest.TestCase):
    def test_throttle_reserve_and_secret_key(self):
        t = [0.0]
        slept = []
        seen = []
        def fetch(path, params):
            seen.append(params)
            return {"n": len(seen)}
        w = WebCache("x", "", {}, lambda p, q: 60, min_interval=6.5, fetch=fetch, clock=lambda: t[0],
                     sleep=lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)))
        w.get("/a", {"q": 1}, secrets={"apiKey": "SECRET"})
        w.get("/b")
        self.assertEqual(round(slept[0], 1), 6.5)                    # spaced out for 10 requests/minute
        self.assertEqual(w.get("/a", {"q": 1})["n"], 1)               # cached; key has no secret in it
        self.assertFalse(any("SECRET" in k for k in w._cache))
        w.remaining, w.reserve = 5, 10
        with self.assertRaises(SourceError):
            w.get("/c")


if __name__ == "__main__":
    unittest.main()


class ScoresOnlyModel(unittest.TestCase):
    """Regression for the Lorient v Brest report: a scores-only model 0.7 goals below the market
    produced a +22% 'Strong Pick' on Under 2.5."""

    def low_scoring_season(self):
        out, mid = [], 9500
        pairs = [(501, 503), (502, 504), (505, 506), (503, 502), (504, 501), (506, 505)]
        for r in range(10):
            when = F.KO - timedelta(days=7 * (10 - r))
            for i, (h, a) in enumerate(pairs[r % 2 * 3: r % 2 * 3 + 3]):
                mid += 1
                lo = h in (501, 502) or a in (501, 502)
                out.append(F.match(mid, h, a, when, "FINISHED", (1, 0) if lo else (2, 2)))
        return out

    def analysis(self, over, under):
        today = [F.match(1, 501, 502, F.KO)]
        svc, _ = service(today, [F.odds_event("Test Rovers", "Test United", F.KO, 2.6, 3.1, 2.9, over, under)],
                         finished=self.low_scoring_season())
        e = svc.slate(DAY)[0]
        return e["analysis"]

    def test_goal_count_disagreement_rejects_totals(self):
        a = self.analysis(over=1.72, under=2.20)              # market expects ~2.6 goals
        names = [c["name"] for c in a["prediction"]["components"]]
        self.assertNotIn("xg_poisson", names)                 # no duplicate goals model faking agreement
        under = next(m for m in a["markets"] if m["key"] == "OU:2.5:under")
        self.assertEqual(under["status"], "avoid")
        self.assertTrue(any("disagree on expected goals" in r for r in under["reasons_rejected"]),
                        under["reasons_rejected"])
        self.assertFalse([m for m in a["markets"] if m["status"] == "recommended" and m["label_tag"] == "Strong Pick"])

    def test_shrinkage_tempers_extremes(self):
        from app.engine.ensemble import _team_rates
        from app.engine.types import TeamMatchRecord
        recs = [TeamMatchRecord(F.KO - timedelta(days=7 * i), "x", None, "H", "epl", 4, 0) for i in range(10)]
        f, a = _team_rates(recs, "H", 10, 0.4, [], "T", L=1.4, shrink=6.0)
        self.assertAlmostEqual(f, (10 * 4 + 6 * 1.4) / 16, places=6)    # 3.03, not 4.0
        self.assertAlmostEqual(a, (0 + 6 * 1.4) / 16, places=6)         # 0.53, not 0.15
