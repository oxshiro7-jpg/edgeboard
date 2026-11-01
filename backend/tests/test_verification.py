"""Verification gate tests.

A fake transport returns payloads in API-Football's response shape so the
real adapter, the verification gate and the prediction service run end to
end without network access. Team names here are deliberately "Test ..."."""
import json
import logging
import re
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine.integrity import check_market, enforce
from app.engine.pipeline import UnverifiedFixtureError, analyse_fixture
from app.engine.types import FixtureInput, TeamProfile
from app.providers.api_football import ApiFootballOdds, ApiFootballProvider
from app.providers.registry import ProviderNotConfigured, Providers, build_providers
from app.services.analysis_service import AnalysisService
from app.services.analyst import AnalystTools, route
from app.settings import Settings
from app.verification import Status, TeamLookupCache, verify_fixture

DAY = date(2026, 10, 4)
WL = ["epl", "laliga", "seriea", "bundesliga", "ligue1", "eredivisie", "primeira", "ucl", "facup"]

TEAMS = {9001: "Test Home FC", 9002: "Test Away FC", 9003: "Test Third FC", 9004: "Test Fourth FC"}


def fixture_item(fid, home, away, when, league=39, status="NS", home_name=None, away_name=None, goals=(None, None)):
    return {"fixture": {"id": fid, "date": when.isoformat(), "status": {"short": status}},
            "league": {"id": league, "season": 2026},
            "teams": {"home": {"id": home, "name": home_name or TEAMS.get(home, f"Unknown {home}")},
                      "away": {"id": away, "name": away_name or TEAMS.get(away, f"Unknown {away}")}},
            "goals": {"home": goals[0], "away": goals[1]}}


class FakeApiFootball:
    """Dispatches API-Football paths to canned responses."""

    def __init__(self, today, history_len=10, odds=True, teams=None):
        self.today = today
        self.teams = dict(TEAMS if teams is None else teams)
        self.history_len = history_len
        self.odds = odds
        self.calls = []

    def __call__(self, path, params):
        self.calls.append((path, dict(params)))
        if path == "/fixtures" and "date" in params:
            return {"response": self.today}
        if path == "/fixtures" and "id" in params:
            return {"response": [f for f in self.today if str(f["fixture"]["id"]) == str(params["id"])]}
        if path == "/teams" and "id" in params:
            tid = int(params["id"])
            return {"response": [{"team": {"id": tid, "name": self.teams[tid], "code": self.teams[tid][5:8].upper()}}]
                    if tid in self.teams else []}
        if path == "/fixtures" and "team" in params:
            tid = int(params["team"])
            out = []
            for i in range(self.history_len):
                opp = 9003 if i % 2 else 9004
                when = datetime(2026, 9, 27, 15, tzinfo=timezone.utc) - timedelta(days=7 * i)
                home = i % 2 == 0
                out.append(fixture_item(80000 + tid * 100 + i, tid if home else opp, opp if home else tid, when,
                                        status="FT", goals=(1 + i % 3, i % 2)))
            return {"response": out}
        if path == "/fixtures/statistics":
            fid = int(params["fixture"])
            tid = (fid - 80000) // 100
            return {"response": [
                {"team": {"id": tid}, "statistics": [{"type": "expected_goals", "value": "1.45"}, {"type": "Total Shots", "value": 13}]},
                {"team": {"id": 9003 if fid % 2 else 9004}, "statistics": [{"type": "expected_goals", "value": "1.10"}, {"type": "Total Shots", "value": 9}]},
            ]}
        if path == "/odds":
            if not self.odds:
                return {"response": []}
            fid = int(params["fixture"])
            vals = lambda pairs: [{"value": v, "odd": str(o)} for v, o in pairs]
            return {"response": [{"fixture": {"id": fid}, "update": "2026-10-04T08:00:00+00:00", "bookmakers": [
                {"id": 8, "name": "Bet365", "bets": [
                    {"name": "Match Winner", "values": vals([("Home", 2.10), ("Draw", 3.40), ("Away", 3.60)])},
                    {"name": "Goals Over/Under", "values": vals([("Over 2.5", 1.95), ("Under 2.5", 1.85), ("Over 1.5", 1.33), ("Under 1.5", 3.25)])},
                    {"name": "Both Teams Score", "values": vals([("Yes", 1.80), ("No", 1.95)])},
                ]}]}]}
        return {"response": []}   # injuries, lineups, h2h: nothing


def make_service(fake):
    def factory(day):
        fp = ApiFootballProvider("test-key", http_get=fake)
        return Providers(fp, ApiFootballOdds(fp), fp.injury_provider(), fp.lineup_provider())
    return AnalysisService(Settings(), providers_factory=factory)


KO = datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc)


class Gate(unittest.TestCase):
    def provider(self, today, **kw):
        return ApiFootballProvider("k", http_get=FakeApiFootball(today, **kw))

    def test_verified(self):
        p = self.provider([fixture_item(1001, 9001, 9002, KO)])
        r = verify_fixture(p, "1001", DAY, WL, teams=TeamLookupCache())
        self.assertEqual(r.status, Status.VERIFIED)
        self.assertEqual(r.verified.home.name, "Test Home FC")
        self.assertEqual(r.verified.fixture.id, "1001")

    def test_unknown_fixture_id(self):
        p = self.provider([fixture_item(1001, 9001, 9002, KO)])
        r = verify_fixture(p, "999999", DAY, WL, teams=TeamLookupCache())
        self.assertEqual(r.status, Status.UNVERIFIED)
        self.assertTrue(r.messages[0].startswith("NO VERIFIED FIXTURE"))

    def test_missing_fixture_id(self):
        p = self.provider([])
        self.assertTrue(verify_fixture(p, "", DAY, WL).messages[0].startswith("NO VERIFIED FIXTURE"))

    def test_team_not_verified(self):
        p = self.provider([fixture_item(1002, 9001, 7777, KO, away_name="Real Sotavento")])
        r = verify_fixture(p, "1002", DAY, WL, teams=TeamLookupCache())
        self.assertEqual(r.messages, ["TEAM NOT VERIFIED: Real Sotavento"])

    def test_name_mismatch_is_not_fixed_up(self):
        # Fixture payload names a team differently from the team endpoint: report it, never substitute.
        p = self.provider([fixture_item(1003, 9001, 9002, KO, home_name="Test Home")])
        r = verify_fixture(p, "1003", DAY, WL, teams=TeamLookupCache())
        self.assertEqual(r.status, Status.UNVERIFIED)
        self.assertIn("TEAM NOT VERIFIED: Test Home", r.messages[0])

    def test_postponed_and_cancelled(self):
        p = self.provider([fixture_item(1004, 9001, 9002, KO, status="PST"), fixture_item(1005, 9001, 9002, KO, status="CANC")])
        self.assertEqual(verify_fixture(p, "1004", DAY, WL, teams=TeamLookupCache()).status, Status.POSTPONED)
        self.assertEqual(verify_fixture(p, "1005", DAY, WL, teams=TeamLookupCache()).status, Status.CANCELLED)

    def test_wrong_date(self):
        p = self.provider([fixture_item(1006, 9001, 9002, KO + timedelta(days=1))])
        r = verify_fixture(p, "1006", DAY, WL, teams=TeamLookupCache())
        self.assertIn("not 2026-10-04", r.messages[0])

    def test_unsupported_competition_is_dropped(self):
        p = self.provider([fixture_item(1007, 9001, 9002, KO, league=999)])
        self.assertEqual(p.fixtures(DAY, WL), [])
        self.assertEqual(verify_fixture(p, "1007", DAY, WL, teams=TeamLookupCache()).status, Status.UNVERIFIED)


class EndToEnd(unittest.TestCase):
    def test_slate_shows_only_provider_fixtures(self):
        today = [fixture_item(2001, 9001, 9002, KO), fixture_item(2002, 9003, 9004, KO + timedelta(hours=2)),
                 fixture_item(2003, 9001, 9004, KO, status="PST"), fixture_item(2004, 9002, 9003, KO, league=999)]
        svc = make_service(FakeApiFootball(today))
        cards = svc.summary_cards(DAY)
        self.assertEqual({c["fixture_id"] for c in cards}, {"2001", "2002", "2003"})   # unsupported league dropped
        status = {c["fixture_id"]: c["verification_status"] for c in cards}
        self.assertEqual(status["2003"], "POSTPONED")
        self.assertEqual(status["2001"], "VERIFIED")
        for a in svc.day(DAY):
            self.assertEqual(a["provenance"]["provider"], "api_football")
            self.assertIn(a["home"]["name"], TEAMS.values())
            for m in a["markets"]:
                self.assertEqual(check_market(m), [], m["key"])   # every displayed number recomputes

    def test_odds_unavailable_blocks_recommendations(self):
        svc = make_service(FakeApiFootball([fixture_item(2101, 9001, 9002, KO)], odds=False))
        e = svc.match(DAY, "2101")
        self.assertEqual(e["status"], "ODDS UNAVAILABLE")
        self.assertTrue(any(m.startswith("ODDS UNAVAILABLE") for m in e["messages"]))
        self.assertFalse([m for m in e["analysis"]["markets"] if m["status"] == "recommended"])
        self.assertTrue(all(m["bookmaker_odds"] is None and m["ev"] is None for m in e["analysis"]["markets"]))

    def test_data_incomplete_gives_no_prediction(self):
        svc = make_service(FakeApiFootball([fixture_item(2201, 9001, 9002, KO)], history_len=3))
        e = svc.match(DAY, "2201")
        self.assertEqual(e["status"], "DATA INCOMPLETE")
        self.assertIsNone(e["analysis"])

    def test_predict_unknown_id(self):
        svc = make_service(FakeApiFootball([fixture_item(2301, 9001, 9002, KO)]))
        e = svc.match(DAY, "424242")
        self.assertIsNone(e["analysis"])
        self.assertTrue(e["messages"][0].startswith("NO VERIFIED FIXTURE"))


class NamedFakeFixtures(unittest.TestCase):
    """The four fixtures named in the bug report can never be predicted unless
    the fixtures API itself returns them with provider ids."""
    FAKE = [("Olympique Sarrant", "AS Verdeline"), ("FC Rheinhafen", "SV Ostwald"),
            ("Northbridge Athletic", "Port Ardley"), ("Real Sotavento", "AC Lucerno")]

    def test_not_in_application_code(self):
        app_dir = Path(__file__).resolve().parents[1] / "app"
        src = "\n".join(p.read_text() for p in app_dir.rglob("*.py"))
        for h, a in self.FAKE:
            self.assertNotIn(h, src)
            self.assertNotIn(a, src)
        self.assertFalse((app_dir / "providers" / "demo.py").exists())

    def test_never_predicted(self):
        svc = make_service(FakeApiFootball([fixture_item(2401, 9001, 9002, KO)]))
        names = {n for a in svc.day(DAY) for n in (a["home"]["name"], a["away"]["name"])}
        for h, a in self.FAKE:
            self.assertNotIn(h, names)
            for fid in (f"demo-{h}", "demo-2026-10-04-07", "test-2026-10-04-07"):
                self.assertIsNone(svc.match(DAY, fid)["analysis"])

    def test_analyst_reports_team_not_verified(self):
        svc = make_service(FakeApiFootball([fixture_item(2501, 9001, 9002, KO)]))
        tools = AnalystTools(svc.day(DAY))
        r = route("Why is Real Sotavento ML not recommended?", tools)
        self.assertTrue(r["answer"].startswith("TEAM NOT VERIFIED: Real Sotavento"), r["answer"])
        self.assertNotIn("Real Madrid", r["answer"])

    def test_engine_refuses_unverified_input(self):
        fx = FixtureInput("x", "epl", "Premier League", "league", KO,
                          TeamProfile("1", "Real Sotavento", "RSO", None), TeamProfile("2", "AC Lucerno", "LUC", None))
        with self.assertRaises(UnverifiedFixtureError):
            analyse_fixture(fx)


class Registry(unittest.TestCase):
    def test_no_demo_or_keyless_provider(self):
        with self.assertRaises(ProviderNotConfigured):
            build_providers("demo", "demo", DAY, {})
        with self.assertRaises(ProviderNotConfigured):
            build_providers("api_football", "api_football", DAY, {})

    def test_service_without_provider_shows_nothing(self):
        svc = AnalysisService(Settings())      # no API key in the test environment
        with self.assertRaises(ProviderNotConfigured):
            svc.summary_cards(DAY)


class Integrity(unittest.TestCase):
    def market(self, p, o, ev):
        return {"key": "1X2:H", "model_probability": p, "bookmaker_odds": o, "ev": ev, "fair_odds": round(1 / p, 3),
                "implied_probability": round(1 / o, 4), "no_vig_probability": None, "edge_pp": None,
                "settlement": {"win": p, "half_win": 0, "push": 0, "half_loss": 0, "loss": 1 - p},
                "status": "recommended", "label_tag": "Value Pick", "reasons_rejected": []}

    def test_spec_ev_example(self):
        self.assertAlmostEqual(0.57 * 2.15 - 1, 0.2255, places=6)
        self.assertEqual(check_market(self.market(0.57, 2.15, 0.2255)), [])

    def test_wrong_ev_is_rejected_and_logged(self):
        m = self.market(0.57, 2.15, 0.30)
        with self.assertLogs("edgeboard.integrity", level="ERROR") as logs:
            self.assertEqual(enforce([m], "f1"), 1)
        self.assertEqual(m["status"], "avoid")
        self.assertIn("EV", logs.output[0])


if __name__ == "__main__":
    unittest.main()


class SnapshotRoundTrip(unittest.TestCase):
    """Record raw API responses, replay them through the full app: same fixtures, nothing added."""

    def test_record_and_replay(self):
        import os, subprocess, tempfile
        from app.engine.config import EngineConfig
        from app.providers.snapshot import RecordingTransport, ReplayTransport, SnapshotMiss
        from app.services.prediction import PredictionService
        today = [fixture_item(1001, 9001, 9002, KO), fixture_item(1002, 9003, 9004, KO + timedelta(hours=2))]
        rec = RecordingTransport("k", fetch=FakeApiFootball(today))
        fp = ApiFootballProvider("k", http_get=rec, history_limit=8)
        p = Providers(fp, ApiFootballOdds(fp), fp.injury_provider(), fp.lineup_provider())
        svc = PredictionService(lambda d: p, lambda: WL, EngineConfig)
        refs = fp.fixtures(DAY, WL)
        e = svc._run(p, refs[0].id, DAY, WL, listed=refs[0])          # only the first is fetched
        self.assertEqual(e["status"], "VERIFIED")
        self.assertFalse(any("last=20" in k for k in rec.responses))     # history cap respected
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "snap.json")
            rec.dump(path, {"date": DAY.isoformat(), "tz": "UTC", "history": 8,
                            "listed_fixture_ids": [r.id for r in refs], "analysed_fixture_ids": ["1001"]})
            with self.assertRaises(SnapshotMiss):
                ReplayTransport(json.load(open(path)))("/odds", {"fixture": "999"})
            out = subprocess.run([sys.executable, "scripts/run_snapshot.py", path], capture_output=True, text=True,
                                 cwd=Path(__file__).resolve().parents[1], env={**os.environ, "API_FOOTBALL_KEY": ""})
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertIn("[VERIFIED] 1001  Test Home FC vs Test Away FC", out.stdout)
            self.assertIn("[UNVERIFIED] 1002  Test Third FC vs Test Fourth FC", out.stdout)   # not fetched: no prediction
            self.assertIn("2 of 2 displayed fixtures match", out.stdout)


class LiveBudget(unittest.TestCase):
    """Cache + daily budget for the live transport, and the analysed-fixture cap."""

    def test_cache_and_budget(self):
        import tempfile, os
        from app.providers.http_cache import BudgetExhausted, CachedTransport
        now = [1_790_000_000.0]
        calls = []
        def fetch(path, params):
            calls.append(path)
            return {"response": [{"n": len(calls)}]}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.json")
            t = CachedTransport("k", daily_limit=2, cache_path=path, fetch=fetch, clock=lambda: now[0])
            t("/teams", {"id": 1}); t("/teams", {"id": 1})
            self.assertEqual(len(calls), 1)                     # second answered from cache
            t("/odds", {"fixture": 5})
            with self.assertRaises(BudgetExhausted):
                t("/odds", {"fixture": 6})                      # budget of 2 used
            t2 = CachedTransport("k", daily_limit=2, cache_path=path, fetch=fetch, clock=lambda: now[0])
            self.assertEqual(t2("/teams", {"id": 1})["response"][0]["n"], 1)   # cache survives restart
            with self.assertRaises(BudgetExhausted):
                t2("/teams", {"id": 2})                         # and so does the day's count
            now[0] += 86400                                     # next UTC day: budget resets
            t2("/teams", {"id": 2})
            now[0] += 3 * 3600                                  # odds expire after 2 h, teams do not
            t2("/odds", {"fixture": 5}); t2("/teams", {"id": 1})
            self.assertEqual(calls.count("/odds"), 2)
            self.assertEqual(calls.count("/teams"), 2)

    def test_cap_then_on_demand(self):
        today = [fixture_item(1001, 9001, 9002, KO), fixture_item(1002, 9003, 9004, KO + timedelta(hours=2))]
        svc = make_service(FakeApiFootball(today))
        svc.predictions.max_analysed = 1
        slate = {e["fixture_id"]: e for e in svc.slate(DAY)}
        self.assertEqual(slate["1001"]["status"], "VERIFIED")
        self.assertEqual(slate["1002"]["status"], "UNVERIFIED")
        self.assertIsNone(slate["1002"]["analysis"])
        e = svc.match(DAY, "1002")                               # opening it runs the full check by id
        self.assertEqual(e["status"], "VERIFIED")
        self.assertEqual({x["fixture_id"]: x["status"] for x in svc.slate(DAY)}["1002"], "VERIFIED")
        self.assertEqual(svc.match(DAY, "9999")["status"], "UNVERIFIED")   # unknown id: still refused
