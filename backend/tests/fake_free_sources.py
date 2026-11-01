"""Fake football-data.org and The Odds API transports, in their real response
shapes, so the free stack runs end to end offline. Clubs are 'Test ...'."""
import random
from datetime import datetime, timedelta, timezone

from app.providers.web_cache import WebCache

TEAMS = {501: "Test Rovers FC", 502: "Test United AFC", 503: "Test Athletic", 504: "Test City FC",
         505: "Test Town", 506: "Test Albion FC"}
KO = datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc)


def match(mid, home, away, when, status="TIMED", score=(None, None), code="PL"):
    return {"id": mid, "utcDate": when.isoformat().replace("+00:00", "Z"), "status": status,
            "competition": {"code": code, "name": "Premier League"},
            "homeTeam": {"id": home, "name": TEAMS[home], "shortName": TEAMS[home].replace(" FC", "").replace(" AFC", ""),
                         "crest": None},
            "awayTeam": {"id": away, "name": TEAMS[away], "shortName": TEAMS[away].replace(" FC", "").replace(" AFC", ""),
                         "crest": None},
            "score": {"fullTime": {"home": score[0], "away": score[1]}}}


def season(rounds=12, seed=7):
    rnd = random.Random(seed)
    ids = list(TEAMS)
    out, mid = [], 9000
    for r in range(rounds):
        rnd.shuffle(ids)
        when = KO - timedelta(days=7 * (rounds - r))
        for i in range(0, len(ids), 2):
            mid += 1
            out.append(match(mid, ids[i], ids[i + 1], when, "FINISHED", (rnd.randint(0, 3), rnd.randint(0, 2))))
    return out


def fd_transport(today, finished=None):
    finished = season() if finished is None else finished
    calls = []

    def fetch(path, params):
        calls.append((path, params))
        if path == "/matches":
            return {"matches": today}
        if path.startswith("/competitions/PL/matches"):
            return {"matches": finished + today}
        if path.startswith("/competitions/"):
            return {"matches": []}
        if path.startswith("/matches/"):
            mid = int(path.rsplit("/", 1)[1])
            return next((m for m in today if m["id"] == mid), {})
        return {}
    w = WebCache("fd-test", "", {}, lambda p, q: 3600, fetch=fetch)
    w.calls_log = calls
    return w


def odds_transport(events):
    return WebCache("odds-test", "", {}, lambda p, q: 3600, fetch=lambda path, params: events)


def odds_event(home, away, when, h=2.1, d=3.4, a=3.5, over=1.9, under=1.95):
    def bm(key):
        return {"key": key, "last_update": "2026-10-04T08:00:00Z", "markets": [
            {"key": "h2h", "outcomes": [{"name": home, "price": h}, {"name": away, "price": a}, {"name": "Draw", "price": d}]},
            {"key": "totals", "outcomes": [{"name": "Over", "price": over, "point": 2.5},
                                           {"name": "Under", "price": under, "point": 2.5}]}]}
    return {"id": "ev1", "sport_key": "soccer_epl", "commence_time": when.isoformat().replace("+00:00", "Z"),
            "home_team": home, "away_team": away, "bookmakers": [bm("pinnacle"), bm("bet365")]}
