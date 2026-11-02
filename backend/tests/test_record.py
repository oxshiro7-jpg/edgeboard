"""Persistence, forward pick log, closing prices and settlement (free stack, fake transports)."""
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fake_free_sources as F  # noqa: E402
from app.providers.football_data import FootballDataOrgProvider  # noqa: E402
from app.providers.registry import Providers  # noqa: E402
from app.providers.the_odds_api import TheOddsApiProvider  # noqa: E402
from app.providers.web_cache import WebCache  # noqa: E402
from app.services.analysis_service import AnalysisService  # noqa: E402
from app.services.lab import LabService  # noqa: E402
from app.settings import Settings  # noqa: E402
from app.store import SqliteStore, Store  # noqa: E402

DAY = date(2026, 10, 4)


class World:
    """Mutable fake sources: tests change prices and scores between calls."""

    def __init__(self, store):
        self.today = [F.match(1, 501, 502, F.KO)]
        self.prices = {"over": 1.72, "under": 2.20, "h": 2.6, "d": 3.1, "a": 2.9}
        self.clock = [F.KO.timestamp() - 6 * 3600]
        self.fd = WebCache("fd", "", {}, lambda p, q: 3600, fetch=self._fd, clock=lambda: self.clock[0], store=store)
        self.od = WebCache("odds", "", {}, lambda p, q: 24 * 3600, fetch=self._odds, clock=lambda: self.clock[0], store=store)
        self.odds_calls = 0
        self.season = F.season()

    def _fd(self, path, params):
        if path == "/matches":
            return {"matches": self.today}
        if path.startswith("/competitions/PL"):
            return {"matches": self.season + self.today}
        if path.startswith("/matches/"):
            mid = int(path.rsplit("/", 1)[1])
            return next((m for m in self.today if m["id"] == mid), {})
        return {"matches": []}

    def _odds(self, path, params):
        self.odds_calls += 1
        p = self.prices
        return [F.odds_event("Test Rovers", "Test United", F.KO, p["h"], p["d"], p["a"], p["over"], p["under"])]

    def providers(self, day=None):
        return Providers(FootballDataOrgProvider("t", http=self.fd), TheOddsApiProvider("k", http=self.od), None, None)


def service(world, store):
    return AnalysisService(Settings(football_provider="football_data"), providers_factory=world.providers, store=store)


class StoreRoundTrip(unittest.TestCase):
    def test_sqlite_persists_across_instances(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.db")
            SqliteStore(path).put("k", {"a": [1, 2]})
            self.assertEqual(SqliteStore(path).get("k"), {"a": [1, 2]})
            self.assertTrue(SqliteStore(path).persistent)
            self.assertFalse(Store().persistent)

    def test_api_cache_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            store = SqliteStore(os.path.join(d, "s.db"))
            calls = []
            mk = lambda: WebCache("x", "", {}, lambda p, q: 3600, fetch=lambda p, q: calls.append(p) or {"ok": 1}, store=store)
            mk().get("/a")
            self.assertEqual(mk().get("/a"), {"ok": 1})      # new process: served from the database
            self.assertEqual(len(calls), 1)


class ForwardRecord(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SqliteStore(os.path.join(self.tmp.name, "s.db"))
        self.w = World(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def logged(self, svc):
        svc.slate(DAY)
        return svc.record.rows("model")

    def recommend_something(self, svc):
        """Take a real verified analysis and mark one priced 1X2 market as recommended, so the
        record path is exercised whatever the synthetic season's numbers are."""
        import copy
        entries = copy.deepcopy(svc.predictions.slate(DAY))
        m = next(m for m in entries[0]["analysis"]["markets"] if m["key"] == "1X2:H")
        m["status"] = "recommended"
        with unittest.mock.patch("app.services.record.datetime") as dt:
            dt.fromisoformat = datetime.fromisoformat
            svc.record.log_model_picks(entries, now=F.KO - timedelta(hours=6))
        rows = svc.record.rows("model")
        self.assertEqual(len(rows), 1)
        self.recommended_entries = entries
        return rows

    def test_log_settle_and_closing_price(self):
        svc = service(self.w, self.store)
        rows = self.recommend_something(svc)
        first = rows[0]
        self.assertEqual(first["source"], "model")
        self.assertIsNone(first["closing_odds"])
        # Same pick seen again at a different price: the first sighting is kept.
        again = self.recommended_entries
        next(m for m in again[0]["analysis"]["markets"] if m["key"] == "1X2:H")["bookmaker_odds"] = 9.99
        svc.record.log_model_picks(again, now=F.KO - timedelta(hours=5))
        self.assertEqual([r["odds"] for r in svc.record.rows("model")], [first["odds"]])
        self.w.prices["h"] = 2.45                       # the market moves before kickoff

        # 30 minutes before kickoff: the league is re-priced once and the closing price recorded.
        self.w.clock[0] = F.KO.timestamp() - 30 * 60
        calls_before = self.w.odds_calls
        out = svc.record.run_tasks(self.w.providers(), now=F.KO - timedelta(minutes=30))
        self.assertEqual(out["errors"], [])
        self.assertEqual(self.w.odds_calls, calls_before + 1)
        self.assertGreaterEqual(out["closing_prices"], 1)
        closed = next(r for r in svc.record.rows("model") if r["id"] == first["id"])
        self.assertEqual(closed["closing_odds"], 2.45)

        # Not finished yet: nothing settles. Finished: settles from the final score.
        out = svc.record.run_tasks(self.w.providers(), now=F.KO + timedelta(hours=3))
        self.assertEqual(out["settled"], 0)
        self.w.today[0].update(status="FINISHED", score={"duration": "REGULAR", "fullTime": {"home": 2, "away": 1},
                                                          "halfTime": {"home": 1, "away": 0}})
        self.w.clock[0] = F.KO.timestamp() + 3 * 3600
        out = svc.record.run_tasks(self.w.providers(), now=F.KO + timedelta(hours=3))
        self.assertGreaterEqual(out["settled"], 1)
        done = next(r for r in svc.record.rows("model") if r["id"] == first["id"])
        self.assertIn(done["result"], ("win", "loss", "half_win", "half_loss", "push"))

        # A fresh service (server restart) sees the same record, and Performance uses it.
        svc2 = service(self.w, self.store)
        perf = LabService(svc2).performance(DAY)
        self.assertTrue(perf["source"].startswith("forward record"))
        self.assertGreaterEqual(perf["coverage"]["settled"], 1)
        self.assertIn("rows", perf["calibration"])

    def test_never_logged_after_kickoff(self):
        svc = service(self.w, self.store)
        entries = [{"status": "VERIFIED", "analysis": {"kickoff": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
                                                      "markets": [{"status": "recommended"}]}}]
        self.assertEqual(svc.record.log_model_picks(entries), 0)

    def test_user_bets_bankroll_challenges_persist(self):
        svc = service(self.w, self.store)
        svc.slate(DAY)
        a = svc.day(DAY)[0]
        key = next(m["key"] for m in a["markets"] if m["bookmaker_odds"])
        lab = LabService(svc)
        with unittest.mock.patch("app.services.lab.datetime") as dt:
            dt.now.return_value = F.KO - timedelta(hours=2)
            dt.fromisoformat = datetime.fromisoformat
            bet = lab.track(DAY, a["fixture_id"], key)
        lab.set_bankroll(1000, 0.01, "fixed")
        lab.create_challenge("Ten picks", "picks", 10, {})
        lab2 = LabService(service(self.w, self.store))                 # restart
        self.assertEqual([b.id for b in lab2.user_bets], [bet["id"]])
        self.assertTrue(lab2.bankroll()["configured"])
        self.assertEqual([c.name for c in lab2.challenges.values()], ["Ten picks"])
        lab2.settle_user_bet(bet["id"], {"home_goals": 1, "away_goals": 1, "home_goals_ht": 0, "away_goals_ht": 0})
        self.assertIsNotNone(LabService(service(self.w, self.store)).user_bets[0].result)


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
