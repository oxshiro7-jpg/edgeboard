"""Forward track record: every selection the app recommends is logged before
kickoff, at the price shown, and is later given its closing price and result.
Nothing is back-filled or re-priced after the fact.

Stored (see app.store):
  record:model   picks the app recommended (one per fixture + market + model version)
  record:user    picks the user chose to track

`run_tasks` is called by an external scheduler (cron-job.org hitting
/api/tasks/run every ~15 min) because the free host sleeps when idle:
  * closing price: for picks kicking off within the next 45 minutes, refresh
    that league's odds once and record the price for the same market;
  * settlement: for picks that kicked off 2+ hours ago, read the final score
    from the fixtures provider and settle.
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.engine import tracking as tk
from app.engine.calibration import calibration_table
from app.store import Store

log = logging.getLogger("edgeboard.record")

EXTRA = ("competition_id", "home", "away", "home_alt", "away_alt", "logged_price_at")
CLOSING_WINDOW = timedelta(minutes=45)
SETTLE_AFTER = timedelta(hours=2)


def _bet(d: dict) -> tk.TrackedBet:
    return tk.from_iter([{k: v for k, v in d.items() if k not in EXTRA}])[0]


def _row(b: tk.TrackedBet, extra: dict) -> dict:
    return {**tk.to_dict(b), **{k: extra.get(k) for k in EXTRA}}


class Record:
    def __init__(self, store: Store):
        self.store = store
        self._lock = threading.Lock()

    # ------------------------------------------------------------ reads
    def rows(self, which: str) -> list[dict]:
        return list(self.store.get(f"record:{which}", []))

    def bets(self, which: str) -> list[tk.TrackedBet]:
        return [_bet(r) for r in self.rows(which)]

    def _save(self, which: str, rows: list[dict]) -> None:
        self.store.put(f"record:{which}", rows)

    # ------------------------------------------------------------ writes
    def log_model_picks(self, entries: list[dict], now: Optional[datetime] = None) -> int:
        """Log recommended selections from verified analyses. First sighting wins: the
        price, probability and EV are the ones shown when the pick first appeared."""
        now = now or datetime.now(timezone.utc)
        new = []
        with self._lock:
            rows = self.rows("model")
            seen = {r["id"] for r in rows}
            for e in entries:
                a = e.get("analysis")
                if not a or e.get("status") != "VERIFIED":
                    continue
                kickoff = datetime.fromisoformat(a["kickoff"])
                if kickoff <= now:
                    continue                       # never log a pick after kickoff
                for m in a["markets"]:
                    if m["status"] != "recommended":
                        continue
                    bid = f"{a['fixture_id']}:{m['key']}:{a['model']['version']}"
                    if bid in seen:
                        continue
                    b = tk.TrackedBet(
                        id=bid, placed_at=now, kickoff=kickoff, fixture_id=a["fixture_id"],
                        competition=a["competition"]["name"], market_key=m["key"], family=m["family"],
                        label=f"{m['label']} ({a['home']['short_name']} v {a['away']['short_name']})",
                        odds=m["bookmaker_odds"], model_probability=m["model_probability"], ev=m["ev"],
                        confidence=m["confidence"], risk=m["risk"], model_version=a["model"]["version"],
                        side=tk.side_of(m["key"]), stake_units=1.0, source="model")
                    new.append(_row(b, self._extra(a, now)))
                    seen.add(bid)
            if new:
                self._save("model", rows + new)
        return len(new)

    @staticmethod
    def _extra(a: dict, now: datetime) -> dict:
        return {"competition_id": a["competition"]["id"], "home": a["home"]["name"], "away": a["away"]["name"],
                "home_alt": a["home"].get("short_name"), "away_alt": a["away"].get("short_name"),
                "logged_price_at": now.isoformat()}

    def add_user_bet(self, b: tk.TrackedBet, analysis: dict) -> dict:
        with self._lock:
            rows = self.rows("user")
            row = _row(b, self._extra(analysis, b.placed_at))
            self._save("user", rows + [row])
        return tk.to_dict(b)

    def update_user_bet(self, bet_id: str, fn) -> dict:
        with self._lock:
            rows = self.rows("user")
            for i, r in enumerate(rows):
                if r["id"] == bet_id:
                    b = _bet(r)
                    fn(b)
                    rows[i] = _row(b, r)
                    self._save("user", rows)
                    return tk.to_dict(b)
        raise KeyError(bet_id)

    # ------------------------------------------------------------ scheduled work
    def run_tasks(self, providers, now: Optional[datetime] = None) -> dict:
        """Closing prices and settlement for both record books. Safe to call often."""
        now = now or datetime.now(timezone.utc)
        out: dict[str, Any] = {"closing_prices": 0, "settled": 0, "leagues_refreshed": [], "errors": []}
        with self._lock:
            books = {w: self.rows(w) for w in ("model", "user")}
            # 1) closing prices: refresh each league once, only if a pick in it kicks off soon
            due = [(w, i) for w, rows in books.items() for i, r in enumerate(rows)
                   if r.get("closing_odds") is None and not r.get("result")
                   and timedelta(minutes=-5) <= datetime.fromisoformat(r["kickoff"]) - now <= CLOSING_WINDOW]
            leagues = sorted({books[w][i]["competition_id"] for w, i in due if books[w][i].get("competition_id")})
            odds = providers.odds
            for comp in leagues:
                try:
                    if hasattr(odds, "refresh") and odds.refresh(comp, max_age=20 * 60):
                        out["leagues_refreshed"].append(comp)
                except Exception as e:
                    out["errors"].append(f"odds {comp}: {e}")
            for w, i in due:
                r = books[w][i]
                if r.get("competition_id") not in out["leagues_refreshed"]:
                    continue
                try:
                    ref = providers.football.fixture(r["fixture_id"])
                    if ref is None:
                        continue
                    series = odds.odds(ref, r.get("home") or ref.home_name or "", r.get("away") or ref.away_name or "",
                                       (r.get("home_alt"), r.get("away_alt")))
                    s = series.get(r["market_key"])
                    if s is not None:
                        r["closing_odds"] = s.current
                        out["closing_prices"] += 1
                except Exception as e:
                    out["errors"].append(f"closing {r['id']}: {e}")
            # 2) settlement from the final score
            for w, rows in books.items():
                for r in rows:
                    if r.get("result") or datetime.fromisoformat(r["kickoff"]) > now - SETTLE_AFTER:
                        continue
                    try:
                        res = providers.football.result(r["fixture_id"]) if hasattr(providers.football, "result") else None
                    except Exception as e:
                        out["errors"].append(f"result {r['id']}: {e}")
                        continue
                    if res is None:
                        continue
                    if res == "void":
                        r.update(result="void", profit_units=0.0, settled_at=now.isoformat())
                    else:
                        b = _bet(r)
                        tk.settle(b, res, now)
                        r.update({k: v for k, v in tk.to_dict(b).items() if k in ("result", "profit_units", "settled_at")})
                    out["settled"] += 1
            for w, rows in books.items():
                self._save(w, rows)
        return out

    # ------------------------------------------------------------ reporting
    def calibration(self, bets: list[tk.TrackedBet]) -> dict:
        y = {"win": 1.0, "half_win": 0.75, "half_loss": 0.25, "loss": 0.0}
        preds = [(b.model_probability, y[b.result]) for b in bets if b.result in y]
        return calibration_table(preds, min_n=30)

    def coverage(self, bets: list[tk.TrackedBet]) -> dict:
        settled = [b for b in bets if b.result]
        return {"logged": len(bets), "settled": len(settled), "pending": len(bets) - len(settled),
                "with_closing_price": sum(1 for b in bets if b.closing_odds),
                "by_model_version": dict(sorted(_count(b.model_version for b in bets).items()))}


def _count(xs) -> dict:
    d: dict = defaultdict(int)
    for x in xs:
        d[x] += 1
    return d
