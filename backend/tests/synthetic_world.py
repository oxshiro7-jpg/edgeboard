"""TEST-ONLY synthetic world for unit tests of the engine's maths.

This module lives under tests/ and is never imported by the app. Club names
are deliberately "Test Club XXX" so they can never be mistaken for real
teams. The app's provider registry has no way to load it.
"""
from __future__ import annotations

import math
import random
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from app.engine.elo import update as elo_update
from app.engine.goals import score_matrix
from app.engine.markets import settlement, MarketInputs
from app.engine.ensemble import half_matrices
from app.engine.config import EngineConfig
from app.engine.types import (Absence, LineupInfo, MatchContext, OddsQuote, OddsSeries, Referee,
                              StyleProfile, TeamMatchRecord)

from app.providers.base import (CompetitionRef, FixtureRef, FootballDataProvider, InjuryProvider, LineupProvider,
                   OddsProvider, TeamRef)

COMPETITIONS = [
    CompetitionRef("epl", "Premier League", "England", "league"),
    CompetitionRef("laliga", "La Liga", "Spain", "league"),
    CompetitionRef("seriea", "Serie A", "Italy", "league"),
    CompetitionRef("bundesliga", "Bundesliga", "Germany", "league"),
    CompetitionRef("ligue1", "Ligue 1", "France", "league"),
    CompetitionRef("eredivisie", "Eredivisie", "Netherlands", "league"),
    CompetitionRef("primeira", "Primeira Liga", "Portugal", "league"),
    CompetitionRef("ucl", "UEFA Champions League", "Europe", "continental"),
    CompetitionRef("facup", "FA Cup", "England", "cup"),
]

# Fictional clubs: (id, name, short, competition, attack, defence)  defence <1 = good
CLUBS = [
    ("nba", "Test Club NBA", "NBA", "epl", 1.42, 0.72),
    ("vlm", "Test Club VLM", "VLM", "epl", 1.18, 0.95),
    ("pta", "Test Club PTA", "PTA", "epl", 0.88, 1.18),
    ("hlw", "Test Club HLW", "HLW", "epl", 1.05, 1.02),
    ("csc", "Test Club CAS", "CAS", "laliga", 1.35, 0.78),
    ("rso", "Test Club RSO", "RSO", "laliga", 0.95, 1.00),
    ("abz", "Test Club ABZ", "ABZ", "laliga", 0.82, 0.86),
    ("luc", "Test Club LUC", "LUC", "seriea", 1.22, 0.80),
    ("vcl", "Test Club VCL", "VCL", "seriea", 0.90, 1.12),
    ("trv", "Test Club TRV", "TRV", "seriea", 1.00, 0.98),
    ("rhf", "Test Club RHF", "RHF", "bundesliga", 1.48, 0.92),
    ("kbh", "Test Club KBH", "KBH", "bundesliga", 1.10, 1.10),
    ("osw", "Test Club OSW", "OSW", "bundesliga", 1.02, 1.22),
    ("srr", "Test Club SAR", "SAR", "ligue1", 1.30, 0.84),
    ("vrd", "Test Club VRD", "VRD", "ligue1", 0.80, 1.05),
    ("lnd", "Test Club LND", "LND", "eredivisie", 1.50, 1.06),
    ("hvv", "Test Club HVV", "HVV", "eredivisie", 1.12, 1.20),
    ("alg", "Test Club ALG", "ALG", "primeira", 1.25, 0.82),
    ("rbm", "Test Club RBM", "RBM", "primeira", 0.86, 1.08),
    ("wks", "Test Club WXT", "WXT", "facup", 0.70, 1.35),
]

FIXTURE_PLAN = [
    # home, away, competition, hour (local UTC), scenario tags
    ("nba", "pta", "epl", 15, {"lineup": "confirmed", "referee": True}),
    ("vlm", "hlw", "epl", 17, {"derby": True, "referee": True}),
    ("csc", "abz", "laliga", 19, {"lineup": "predicted"}),
    ("rso", "luc", "ucl", 20, {}),
    ("trv", "vcl", "seriea", 18, {"manager_home": 12}),
    ("rhf", "osw", "bundesliga", 14, {"lineup": "confirmed", "referee": True, "corners": True}),
    ("kbh", "rhf", "bundesliga", 16, {"rest_away": 3, "euro_away": True}),
    ("srr", "vrd", "ligue1", 20, {"absence_home_star": True, "lineup": "confirmed"}),
    ("lnd", "hvv", "eredivisie", 13, {"corners": True}),
    ("alg", "rbm", "primeira", 21, {"lineup": "predicted", "steam": "1X2:A"}),
    ("wks", "nba", "facup", 12, {"cup": True, "thin_data": True}),
]

FIRST = ["Mateo", "Jonas", "Theo", "Rafael", "Lukas", "Ibrahim", "Kai", "Nico", "Elias", "Tomas", "Andre", "Leon"]
LAST = ["Varga", "Moreau", "Kessler", "Okafor", "Lindqvist", "Duarte", "Brenner", "Calloway", "Moretti", "Haas",
        "Ferreira", "Novak"]


class SyntheticFootballProvider(FootballDataProvider):
    name = "synthetic-test"

    WEEKS = 30

    def __init__(self, day: Optional[date] = None, seed: int = 7):
        self.day = day or datetime.now(timezone.utc).date()
        self.rng = random.Random(seed)
        self.clubs = {c[0]: c for c in CLUBS}
        self.history: dict[str, list[TeamMatchRecord]] = {}
        self.elo: dict[str, float] = {}
        self.elo_log: dict[str, list[tuple[datetime, float]]] = {}
        self.matches: dict[str, dict] = {}       # played fixtures (for backtests and settlement)
        self._simulate()

    # --- synthetic world -------------------------------------------------
    def _simulate(self) -> None:
        rng = self.rng
        for cid, *_rest in CLUBS:
            att, dfn = self.clubs[cid][4], self.clubs[cid][5]
            self.elo[cid] = 1500 + 260 * math.log(att / dfn)
            self.history[cid] = []
            self.elo_log[cid] = []
        ids = list(self.clubs)
        start = datetime.combine(self.day, time(15), tzinfo=timezone.utc) - timedelta(days=7 * (self.WEEKS + 1))
        for week in range(self.WEEKS):
            rng.shuffle(ids)
            when = start + timedelta(days=7 * week + rng.randint(0, 2))
            for h, a in zip(ids[0::2], ids[1::2]):
                self._play(h, a, when)
        for cid in self.history:
            self.history[cid].sort(key=lambda r: r.date, reverse=True)

    def _play(self, h: str, a: str, when: datetime) -> None:
        rng = self.rng
        _, _, _, comp_h, att_h, def_h = self.clubs[h]
        _, _, _, _, att_a, def_a = self.clubs[a]
        xg_h = max(0.15, 1.38 * att_h * def_a + 0.14 + rng.gauss(0, 0.35))
        xg_a = max(0.10, 1.38 * att_a * def_h - 0.14 + rng.gauss(0, 0.30))
        g_h = _poisson(rng, xg_h * rng.uniform(0.75, 1.3))
        g_a = _poisson(rng, xg_a * rng.uniform(0.75, 1.3))
        ht_h = sum(1 for _ in range(g_h) if rng.random() < 0.45)
        ht_a = sum(1 for _ in range(g_a) if rng.random() < 0.45)
        corners = {h: int(3 + xg_h * 2.4 + rng.randint(0, 3)), a: int(3 + xg_a * 2.4 + rng.randint(0, 3))}
        cards = {h: rng.choice([0, 1, 1, 2, 2, 2, 3, 3, 4]), a: rng.choice([0, 1, 1, 2, 2, 2, 3, 3, 4])}
        elo_h, elo_a = self.elo[h], self.elo[a]
        common = dict(date=when, competition=comp_h)
        thin = h == "wks" or a == "wks"
        for team, opp, venue, gf, ga, xf, xa, opp_elo in (
            (h, a, "H", g_h, g_a, xg_h, xg_a, elo_a), (a, h, "A", g_a, g_h, xg_a, xg_h, elo_h)
        ):
            has_xg = not (team == "wks")
            self.history[team].append(TeamMatchRecord(
                opponent_id=opp, opponent_rating=opp_elo, venue=venue, goals_for=gf, goals_against=ga,
                xg_for=round(xf, 2) if has_xg else None, xg_against=round(xa, 2) if has_xg else None,
                npxg_for=round(xf * 0.9, 2) if has_xg else None,
                shots=int(xf * 8 + rng.randint(2, 6)) if has_xg else None,
                shots_on_target=int(xf * 3 + rng.randint(0, 3)) if has_xg else None,
                big_chances=int(xf * 1.6 + rng.random()) if has_xg else None,
                big_chances_conceded=int(xa * 1.6 + rng.random()) if has_xg else None,
                corners_for=None if thin else corners[team],      # world mean 4.5 + 2.4 xG per team
                corners_against=None if thin else corners[opp],
                cards=cards[team],
                **common,
            ))
        self.elo_log[h].append((when, elo_h))
        self.elo_log[a].append((when, elo_a))
        self.elo[h], self.elo[a] = elo_update(elo_h, elo_a, g_h, g_a)
        fid = f"test-hist-{when.date().isoformat()}-{h}-{a}"
        self.matches[fid] = {"id": fid, "kickoff": when, "home": h, "away": a, "competition": comp_h,
                             "home_goals": g_h, "away_goals": g_a, "home_goals_ht": ht_h, "away_goals_ht": ht_a,
                             "cards": cards[h] + cards[a], "corners": None if thin else corners[h] + corners[a]}

    def historical_fixtures(self, start: datetime, end: datetime) -> list[FixtureRef]:
        return [FixtureRef(m["id"], m["competition"], m["kickoff"], m["home"], m["away"], status="finished",
                           home_name=self.clubs[m["home"]][1], away_name=self.clubs[m["away"]][1], status_code="FT",
                           provider=self.name)
                for m in sorted(self.matches.values(), key=lambda m: (m["kickoff"], m["id"])) if start <= m["kickoff"] < end]

    def result(self, fixture_id: str) -> Optional[dict]:
        return self.matches.get(fixture_id)

    # --- interface -------------------------------------------------------
    def competitions(self):
        return COMPETITIONS

    def teams(self, competition_id):
        return [TeamRef(c[0], c[1], c[2], c[3]) for c in CLUBS if c[3] == competition_id]

    def team(self, team_id: str):
        c = self.clubs.get(team_id)
        return TeamRef(c[0], c[1], c[2], c[3]) if c else None

    def fixture(self, fixture_id: str):
        day_refs = {f.id: f for f in self.fixtures(self.day, [])}
        if fixture_id in day_refs:
            return day_refs[fixture_id]
        m = self.matches.get(fixture_id)
        if m:
            return FixtureRef(m["id"], m["competition"], m["kickoff"], m["home"], m["away"], status="finished",
                              home_name=self.clubs[m["home"]][1], away_name=self.clubs[m["away"]][1],
                              status_code="FT", provider=self.name)
        return None

    def fixtures(self, day, competition_ids):
        out = []
        for i, (h, a, comp, hour, tags) in enumerate(FIXTURE_PLAN):
            if competition_ids and comp not in competition_ids:
                continue
            ko = datetime.combine(day, time(hour), tzinfo=timezone.utc)
            out.append(FixtureRef(f"test-{day.isoformat()}-{i:02d}", comp, ko, h, a, home_name=self.clubs[h][1],
                                  away_name=self.clubs[a][1], status_code="NS", provider=self.name))
        return out

    def tags(self, fixture: FixtureRef) -> dict:
        if fixture.id.startswith("test-hist-"):
            return {"lineup": "confirmed", "referee": hash_seed(fixture.id) % 2 == 0}
        idx = int(fixture.id.rsplit("-", 1)[1])
        return FIXTURE_PLAN[idx][4]

    def team_history(self, team_id, before, limit=20):
        recs = [r for r in self.history[team_id] if r.date < before]
        if team_id == "wks":
            recs = recs[:5]
        return recs[:limit]

    def team_rating(self, team_id, as_of):
        before = [e for d, e in self.elo_log[team_id] if d >= as_of]
        return before[0] if before else self.elo[team_id]   # rating before the first match on/after as_of

    def team_style(self, team_id):
        rng = random.Random(team_id)
        att, dfn = self.clubs[team_id][4], self.clubs[team_id][5]
        if team_id == "wks":
            return StyleProfile()
        return StyleProfile(
            formation=rng.choice(["4-3-3", "4-2-3-1", "3-4-2-1", "4-4-2"]),
            possession=round(40 + 15 * (att - 0.8) + rng.uniform(-4, 4), 1),
            field_tilt=round(38 + 22 * (att - 0.8) + rng.uniform(-5, 5), 1),
            ppda=round(14 - 5 * (att - 0.8) + rng.uniform(-1.5, 1.5), 1),
            press_resistance_pct=round(min(99, max(3, 50 + 60 * (att - 1.05) + rng.uniform(-15, 15))), 0),
            set_piece_xg_share=round(rng.uniform(0.18, 0.34), 2),
            set_piece_xga_per_game=round(0.25 * dfn + rng.uniform(0, 0.15), 2),
            counter_xg_per_game=round(rng.uniform(0.12, 0.38), 2),
            transition_xga_per_game=round(0.30 * dfn + rng.uniform(0, 0.12), 2),
            defensive_line_height=round(36 + 10 * (att - 0.8) + rng.uniform(-2, 2), 1),
            crosses_per_game=round(rng.uniform(11, 22), 1),
            wide_attack_share=round(rng.uniform(0.35, 0.55), 2),
        )

    def standings_position(self, team_id):
        comp = self.clubs[team_id][3]
        peers = sorted([c for c in CLUBS if c[3] == comp], key=lambda c: self.elo[c[0]], reverse=True)
        return [c[0] for c in peers].index(team_id) + 1, 20

    def head_to_head(self, home_id, away_id, limit=6):
        return [r for r in self.history[home_id] if r.opponent_id == away_id][:limit]

    def data_timestamp(self, fixture):
        if fixture.id.startswith("test-hist-"):
            return fixture.kickoff - timedelta(hours=2)
        return self.last_updated()

    def match_context(self, fixture):
        t = self.tags(fixture)
        return MatchContext(
            rest_days_home=6, rest_days_away=t.get("rest_away", 6),
            european_midweek_away=t.get("euro_away", False), derby=t.get("derby", False),
            manager_change_days_home=t.get("manager_home"), knockout=t.get("cup", False),
            stakes_home="title" if fixture.home_id in ("nba", "rhf") else None,
            stakes_away="relegation" if fixture.away_id in ("osw", "pta") else None,
        )

    def referee(self, fixture_id):
        if fixture_id.startswith("test-hist-"):
            if hash_seed(fixture_id) % 2:
                return None
            rng = random.Random(fixture_id)
        else:
            idx = int(fixture_id.rsplit("-", 1)[1])
            if not FIXTURE_PLAN[idx][4].get("referee"):
                return None
            rng = random.Random(idx)
        return Referee(f"{rng.choice(FIRST)} {rng.choice(LAST)} (test)", rng.randint(18, 60), round(rng.uniform(3.4, 5.2), 2))

    def league_average_goals(self, competition_id, as_of=None):
        xs = [r.xg_for for recs in self.history.values() for r in recs
              if r.xg_for is not None and (as_of is None or r.date < as_of)]
        if not xs:
            return None
        return sum(xs) / len(xs)

    def last_updated(self):
        return datetime.combine(self.day, time(8), tzinfo=timezone.utc)


class SyntheticInjuryProvider(InjuryProvider):
    name = "synthetic-test"

    def __init__(self, football: SyntheticFootballProvider):
        self.f = football

    def absences(self, fixture, team_id):
        t = self.f.tags(fixture)
        if t.get("thin_data"):
            return []
        rng = random.Random(f"{fixture.id}{team_id}")
        out = []
        if t.get("absence_home_star") and team_id == fixture.home_id:
            out.append(Absence(f"{rng.choice(FIRST)} {rng.choice(LAST)}", "ST", 0.30, "injury"))
            out.append(Absence(f"{rng.choice(FIRST)} {rng.choice(LAST)}", "CB", 0.22, "suspension"))
        for _ in range(rng.choice([0, 0, 1, 1, 2])):
            out.append(Absence(f"{rng.choice(FIRST)} {rng.choice(LAST)}", rng.choice(["FB", "CM", "W", "DM", "CB"]),
                               round(rng.uniform(0.04, 0.14), 2), rng.choice(["injury", "injury", "suspension"]),
                               rng.choice(["out", "out", "doubtful"])))
        return out


class SyntheticLineupProvider(LineupProvider):
    name = "synthetic-test"

    def __init__(self, football: SyntheticFootballProvider):
        self.f = football

    def lineup(self, fixture, team_id):
        t = self.f.tags(fixture)
        status = t.get("lineup", "unknown" if t.get("thin_data") else "predicted")
        return LineupInfo(status=status, formation=self.f.team_style(team_id).formation)


class SyntheticOddsProvider(OddsProvider):
    """Prices = a noisy 'true' model + bookmaker margin, with synthetic history."""
    name = "synthetic-test"

    MARKETS = ["1X2:H", "1X2:D", "1X2:A", "DC:1X", "DC:X2", "DC:12", "DNB:H", "DNB:A", "BTTS:yes", "BTTS:no",
               "OU:0.5:over", "OU:0.5:under", "OU:1.5:over", "OU:1.5:under", "OU:2.5:over", "OU:2.5:under",
               "OU:3.5:over", "OU:3.5:under", "OU:4.5:over", "OU:4.5:under",
               "TT:H:0.5:over", "TT:H:0.5:under", "TT:H:1.5:over", "TT:H:1.5:under",
               "TT:A:0.5:over", "TT:A:0.5:under", "TT:A:1.5:over", "TT:A:1.5:under",
               "AH:H:-0.5", "AH:A:+0.5", "AH:H:-1", "AH:A:+1", "AH:H:+0.25", "AH:A:-0.25",
               "FH1X2:H", "FH1X2:D", "FH1X2:A", "FHOU:0.5:over", "FHOU:0.5:under", "FHOU:1.5:over", "FHOU:1.5:under"]

    def __init__(self, football: SyntheticFootballProvider):
        self.f = football
        self.cfg = EngineConfig()

    def odds(self, fixture, home_name="", away_name=""):
        f = self.f
        t = f.tags(fixture)
        rng = random.Random(fixture.id)
        _, _, _, _, ah, dh = f.clubs[fixture.home_id]
        _, _, _, _, aa, da = f.clubs[fixture.away_id]
        lam_h = max(0.2, 1.38 * ah * da + 0.13 + rng.gauss(0, 0.12))
        lam_a = max(0.2, 1.38 * aa * dh - 0.13 + rng.gauss(0, 0.12))
        if t.get("absence_home_star"):
            lam_h *= 0.95   # books only partially price the absence
        ft = score_matrix(lam_h, lam_a, rho=-0.06)
        fh, sh = half_matrices((lam_h, lam_a), self.cfg)
        mi = MarketInputs(ft, fh, sh)
        out: dict[str, OddsSeries] = {}
        for key in self.MARKETS:
            if t.get("thin_data") and key.split(":")[0] not in ("1X2", "OU", "BTTS"):
                continue
            st = settlement(key, mi)
            if st is None:
                continue
            p_true = st.effective_probability()
            p_book = min(0.97, max(0.02, p_true * (1 + rng.gauss(0, 0.03))))
            margin = 1.055 if key.startswith(("1X2", "FH")) else 1.06
            current = round(max(1.01, 1 / (p_book * margin)), 2)
            drift = rng.gauss(0, 0.03)
            if t.get("steam") == key:
                drift = 0.16
            opening = round(max(1.01, current * (1 + drift)), 2)
            base = datetime.combine(f.day, time(8), tzinfo=timezone.utc) - timedelta(days=3)
            hist = []
            for i in range(6):
                frac = (i + 1) / 7
                o = opening + (current - opening) * frac + rng.gauss(0, 0.01 * current)
                hist.append(OddsQuote(key, round(max(1.01, o), 2), "TestBook", base + timedelta(hours=12 * i)))
            out[key] = OddsSeries(key, opening, current, hist)
        ref = f.referee(fixture.id)
        if ref:
            cards_mean = (0.5 * ref.cards_per_game + 0.5 * 4.0) * rng.uniform(0.92, 1.08)
            self._totals(out, "CARDS", ("3.5", "4.5"), cards_mean, rng)
        if t.get("corners"):
            corners_mean = (8.0 + 2.4 * (lam_h + lam_a)) * rng.uniform(0.94, 1.06)
            line = f"{round(corners_mean) - 0.5:g}"
            self._totals(out, "CORNERS", (line,), corners_mean, rng)
        return out

    @staticmethod
    def _totals(out: dict, market: str, lines, mean: float, rng: random.Random) -> None:
        from app.engine.goals import poisson_over_prob
        for line in lines:
            p_over = poisson_over_prob(mean, float(line))
            for side, p in (("over", p_over), ("under", 1 - p_over)):
                p_book = min(0.95, max(0.05, p * (1 + rng.gauss(0, 0.05))))
                o = round(max(1.01, 1 / (p_book * 1.07)), 2)
                key = f"{market}:{line}:{side}"
                out[key] = OddsSeries(key, o, o, [])


def _poisson(rng: random.Random, lam: float) -> int:
    l, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p < l:
            return k
        k += 1


def hash_seed(text: str) -> int:
    """Stable small hash (Python's hash() is salted per process)."""
    return sum((i + 1) * ord(c) for i, c in enumerate(text))


def synthetic_providers(day=None):
    """Test helper mirroring app.providers.registry.Providers."""
    from app.providers.registry import Providers
    f = SyntheticFootballProvider(day)
    return Providers(f, SyntheticOddsProvider(f), SyntheticInjuryProvider(f), SyntheticLineupProvider(f))
