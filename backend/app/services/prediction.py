"""The only way into the prediction engine.

Today's matches:
    date -> provider fixtures for the date -> supported competitions only
         -> verify each fixture id (fixture, teams, competition, kickoff date, status)
         -> fetch statistics and odds by provider id -> data gate
         -> run models -> integrity checks -> recommendations (VERIFIED only)

A single prediction: `predict(fixture_id, date)`. There is no entry point
that accepts team names, and nothing here asks an LLM for anything.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional

from app.engine import integrity
from app.engine.config import EngineConfig
from app.engine.pipeline import analyse_fixture
from app.providers.registry import Providers
from app.verification import NO_VERIFIED_FIXTURE, Status, VerificationResult, data_status, verify_fixture

from .assembly import build_fixture_input

log = logging.getLogger("edgeboard.prediction")


class PredictionService:
    TTL_SECONDS = 120

    def __init__(self, providers: Callable[[date], Providers], whitelist: Callable[[], list[str]],
                 config: Callable[[], EngineConfig], tz: str = "UTC", ml_model: Callable[[], Any] = lambda: None):
        self._providers = providers
        self._whitelist = whitelist
        self._config = config
        self._ml = ml_model
        self.tz = tz
        self.max_analysed: Optional[int] = None    # None -> MAX_ANALYSED_FIXTURES env (unset = no cap)
        self._cache: dict[tuple, tuple[float, Any]] = {}
        self._on_demand: dict[tuple, dict] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ slate
    def slate(self, day: date, cache_key: tuple = ()) -> list[dict]:
        """Every supported fixture the provider lists for `day`, each with its
        verification status and (only when verified) its analysis."""
        key = ("slate", day, cache_key)
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.time() - hit[0] < self.TTL_SECONDS:
                return self._with_on_demand(day, hit[1])
        p = self._providers(day)
        wl = self._whitelist()
        refs = p.football.fixtures(day, wl)
        chosen = self._chosen(refs, wl)
        out = [self._run(p, ref.id, day, wl, listed=ref) if ref.id in chosen else self._not_checked(ref)
               for ref in refs]
        with self._lock:
            self._cache[key] = (time.time(), out)
        return self._with_on_demand(day, out)

    def _with_on_demand(self, day: date, entries: list[dict]) -> list[dict]:
        return [self._on_demand.get((day, e["fixture_id"]), e) for e in entries]

    def _chosen(self, refs, wl: list[str]) -> set[str]:
        """Which scheduled fixtures get the full (API-costly) check and analysis.
        MAX_ANALYSED_FIXTURES caps it for small API plans; priority follows the
        competition whitelist order, then kickoff."""
        cap = self.max_analysed if self.max_analysed is not None else _env_cap()
        rank = {c: i for i, c in enumerate(wl)}
        live = [r for r in refs if r.status == "scheduled"]
        live.sort(key=lambda r: (rank.get(r.competition_id, len(wl)), r.kickoff.isoformat() if r.kickoff else "", r.id))
        keep = {r.id for r in (live if cap is None else live[:cap])}
        return keep | {r.id for r in refs if r.status != "scheduled"}   # these fail verification at no API cost

    @staticmethod
    def _not_checked(ref) -> dict:
        v = VerificationResult(ref.id, Status.UNVERIFIED,
                               [f"{NO_VERIFIED_FIXTURE}: not checked today (daily API limit; open the match to analyse it)"],
                               None, ref)
        return {"fixture_id": ref.id, "verification": v.to_dict(), "status": v.status.value,
                "messages": list(v.messages), "analysis": None, "not_checked": True}

    def predict(self, fixture_id: str, day: date) -> dict:
        e = self._run(self._providers(day), fixture_id, day, self._whitelist())
        with self._lock:
            self._on_demand[(day, fixture_id)] = e     # shown on the slate too (no second API spend)
        return e

    # ------------------------------------------------------------ core
    def _run(self, p: Providers, fixture_id: str, day: date, wl: list[str], listed=None) -> dict:
        try:
            v: VerificationResult = verify_fixture(p.football, fixture_id, day, wl, self.tz, listed=listed)
        except Exception as e:  # provider lookup failed: the fixture cannot be verified, so it is not analysed
            log.warning("verification lookup failed for %s: %s", fixture_id, e)
            v = VerificationResult(fixture_id, Status.UNVERIFIED, [f"{NO_VERIFIED_FIXTURE}: provider lookup failed ({e})"],
                                   None, listed)
        entry: dict[str, Any] = {"fixture_id": fixture_id, "verification": v.to_dict(), "status": v.status.value,
                                 "messages": list(v.messages), "analysis": None}
        if not v.ok:
            return entry
        try:
            fx = build_fixture_input(v.verified, p)   # type: ignore[arg-type]
        except Exception as e:  # provider failure while fetching stats/odds: show nothing rather than guess
            log.exception("data fetch failed for %s", fixture_id)
            entry.update(status=Status.DATA_INCOMPLETE.value, messages=[f"Data fetch failed: {e}"])
            return entry
        st, msgs = data_status(fx)
        entry["status"], entry["messages"] = st.value, msgs
        if st == Status.DATA_INCOMPLETE:
            return entry                             # no prediction is better than a fabricated one
        a = analyse_fixture(fx, self._config(), now=datetime.now(timezone.utc), ml_model=self._ml())
        if st != Status.VERIFIED:
            _strip_recommendations(a, st)
        bad = integrity.enforce(a["markets"], fixture_id)
        if bad:
            entry["messages"].append(f"{bad} recommendation(s) rejected by the arithmetic integrity check")
        a["verification"] = {**entry["verification"], "status": st.value, "messages": entry["messages"]}
        entry["analysis"] = a
        return entry


def _env_cap() -> Optional[int]:
    v = os.getenv("MAX_ANALYSED_FIXTURES", "").strip()
    return int(v) if v.isdigit() else None


def _strip_recommendations(a: dict, st: Status) -> None:
    """Only VERIFIED fixtures may produce recommendations."""
    for m in a["markets"]:
        if m["status"] == "recommended":
            m["status"] = "avoid"
            m["label_tag"] = "Avoid"
            m["reasons_rejected"] = [f"Fixture status {st.value}: recommendations are only made for VERIFIED fixtures"]
    for k in list(a["ranking"]):
        a["ranking"][k] = [] if isinstance(a["ranking"][k], list) else None
    a["summary"].update(status=st.value, recommended_count=0, best_market=None, best_pick_score=None, risk=None,
                        max_edge_pp=None, value_score=0.0)
    a["no_bet"] = {"no_bet": True, "reasons": [st.value]}
