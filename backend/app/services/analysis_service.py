"""Application service the API calls. Separates HTTP from the prediction
engine; in production the engine runs in the Celery prediction worker and the
API reads persisted snapshots."""
from __future__ import annotations

import copy
import threading
import time
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional

from app.engine.config import EngineConfig, EnsembleWeights, ModelParams, PickScoreWeights, Thresholds
from app.providers.registry import Providers, build_providers
from app.settings import Settings

from .board import all_picks, apply_filter, daily_board, daily_summary, parse_filter
from .prediction import PredictionService


class ConfigStore:
    """Admin-editable engine config. Backed by app_settings in production."""

    def __init__(self):
        self._cfg = EngineConfig()
        self.version = 0
        self.lock = threading.Lock()

    def get(self) -> EngineConfig:
        return self._cfg

    def update(self, patch: dict) -> EngineConfig:
        with self.lock:
            cur = self._cfg.to_dict()
            for section in ("ensemble", "pick_weights", "thresholds", "params"):
                if section in patch:
                    unknown = set(patch[section]) - set(cur[section])
                    if unknown:
                        raise ValueError(f"Unknown {section} fields: {sorted(unknown)}")
                    cur[section].update(patch[section])
            if "enabled_markets" in patch:
                cur["enabled_markets"] = list(patch["enabled_markets"])
            if "model_version" in patch:
                cur["model_version"] = patch["model_version"]
            self._cfg = EngineConfig(
                model_version=cur["model_version"],
                ensemble=EnsembleWeights(**cur["ensemble"]),
                pick_weights=PickScoreWeights(**cur["pick_weights"]),
                thresholds=Thresholds(**cur["thresholds"]),
                params=ModelParams(**cur["params"]),
                calibration=cur["calibration"],
                enabled_markets=cur["enabled_markets"],
            )
            self.version += 1
            return self._cfg


class AnalysisService:
    """Application facade over PredictionService. Every analysis it exposes
    belongs to a fixture the provider returned and the verification gate
    passed; fixtures that fail are listed with their status and no prediction."""

    def __init__(self, settings: Settings, providers_factory: Optional[Callable[[date], Providers]] = None,
                 tz: str = "UTC"):
        self.settings = settings
        self.config = ConfigStore()
        if settings.football_provider == "football_data":
            # Free sources give scores only (no xG, shots, lineups or injuries), so the data-quality
            # score tops out near 52. This profile admits such fixtures under its own model version,
            # with every other gate (EV, odds range, risk, edge sanity checks) unchanged.
            self.config.update({"model_version": "FootballModel_v0.3.0-scores-only",
                                "thresholds": {"min_data_quality": 45, "min_confidence": 50, "min_pick_score": 55}})
        self.whitelist = list(settings.competitions)
        self.ml_model = None     # set when a challenger is promoted (services.lab)
        self._factory = providers_factory
        self.predictions = PredictionService(self.providers, lambda: self.whitelist, self.config.get, tz,
                                             lambda: self.ml_model)

    def providers(self, day: date) -> Providers:
        if self._factory is not None:
            return self._factory(day)
        return build_providers(self.settings.football_provider, self.settings.odds_provider, day,
                               self.settings.provider_keys(), tz=self.predictions.tz)

    def slate(self, day: date) -> list[dict]:
        return self.predictions.slate(day, (self.config.version, tuple(self.whitelist), id(self.ml_model)))

    def day(self, day: date) -> list[dict]:
        """Analyses of verified fixtures only."""
        return [e["analysis"] for e in self.slate(day) if e["analysis"] is not None]

    def match(self, day: date, fixture_id: str) -> dict:
        for e in self.slate(day):
            if e["fixture_id"] == fixture_id and not e.get("not_checked"):
                return e
        return self.predictions.predict(fixture_id, day)   # verified by id like any other; never by name

    def summary_cards(self, day: date) -> list[dict]:
        out = []
        comp_names = {c.id: c.name for c in self.providers(day).football.competitions()}
        for e in self.slate(day):
            f = e["verification"]["fixture"]
            listed = e["verification"].get("listed") or {}
            a = e["analysis"]
            if f:
                head = {"kickoff": f["kickoff"], "competition": {"id": f["competition"]["id"], "name": f["competition"]["name"]},
                        "home": {k: f["home"][k] for k in ("id", "name", "short_name", "badge_url")},
                        "away": {k: f["away"][k] for k in ("id", "name", "short_name", "badge_url")}}
            else:   # shown exactly as the provider listed it, marked unverified, never analysed
                cid = listed.get("competition_id")
                head = {"kickoff": listed.get("kickoff"),
                        "competition": {"id": cid, "name": comp_names.get(cid, cid)} if cid else None,
                        "home": listed.get("home"), "away": listed.get("away")}
            card: dict[str, Any] = {
                "fixture_id": e["fixture_id"], "verification_status": e["status"], "verification_messages": e["messages"],
                "provider": f["provider"] if f else None, **head, "analysed": a is not None,
            }
            if a is not None:
                s = a["summary"]
                card.update({
                    "match_status": a["status"],
                    "odds": {k: next((m["bookmaker_odds"] for m in a["markets"] if m["key"] == f"1X2:{k}"), None)
                             for k in ("H", "D", "A")},
                    "probabilities": a["prediction"]["probabilities"],
                    "market_probabilities": _market_1x2(a),
                    "expected_goals": a["prediction"]["expected_goals"],
                    "expected_score_range": a["prediction"]["expected_score_range"],
                    "data_quality": a["data_quality"]["score"], "data_updated_at": a["data_updated_at"],
                    **s, "no_bet": a["no_bet"],
                })
            out.append(card)
        return out

    def board(self, day: date) -> dict[str, Any]:
        slate = self.slate(day)
        analyses = [e["analysis"] for e in slate if e["analysis"] is not None]
        summ = daily_summary(analyses, self.config.get())
        counts: dict[str, int] = {}
        for e in slate:
            counts[e["status"]] = counts.get(e["status"], 0) + 1
        return {
            "date": day.isoformat(),
            "model_version": self.config.get().model_version,
            "verification": {"fixtures_from_provider": len(slate), "by_status": counts},
            "hero": {
                "todays_games": len(slate),
                "verified_games": counts.get("VERIFIED", 0),
                "recommended_picks": summ["recommendations"],
                "average_confidence": summ["avg_confidence"],
                "average_ev_pct": summ["avg_ev_pct"],
                "active_multiple": None, "current_streak": None, "monthly_roi": None,
            },
            "summary": summ,
            "board": daily_board(analyses),
        }

    def filter(self, day: date, query: str) -> dict[str, Any]:
        clauses = parse_filter(query)
        picks = apply_filter(all_picks(self.day(day)), clauses)
        return {"query": query, "clauses": [list(c) for c in clauses], "count": len(picks), "picks": picks[:100]}

    def search(self, day: date, q: str) -> list[dict]:
        """Searches only what the provider returned for the day; no lookup by guesswork."""
        ql = q.lower().strip()
        if len(ql) < 2:
            return []
        out = []
        for c in self.providers(day).football.competitions():
            if ql in c.name.lower():
                out.append({"type": "competition", "id": c.id, "label": c.name})
        for a in self.day(day):
            if ql in a["home"]["name"].lower() or ql in a["away"]["name"].lower():
                out.append({"type": "match", "id": a["fixture_id"], "label": f"{a['home']['name']} vs {a['away']['name']}"})
            for side in ("home", "away"):
                if ql in a[side]["name"].lower():
                    out.append({"type": "team", "id": a[side]["id"], "label": a[side]["name"]})
                for ab in a[side]["absences"]:
                    if ql in ab["player"].lower():
                        out.append({"type": "player", "id": ab["player"], "label": f"{ab['player']} ({a[side]['name']})"})
        uniq = {(r["type"], r["id"]): r for r in out}
        return list(uniq.values())[:25]

    def admin_config(self) -> dict:
        d = copy.deepcopy(self.config.get().to_dict())
        d["competition_whitelist"] = self.whitelist
        return d

    def set_whitelist(self, ids: list[str]) -> None:
        self.whitelist = list(dict.fromkeys(ids))


def _market_1x2(a: dict):
    nv = {m["key"]: m["no_vig_probability"] for m in a["markets"] if m["key"].startswith("1X2:")}
    if any(nv.get(k) is None for k in ("1X2:H", "1X2:D", "1X2:A")):
        return None
    return {"home": nv["1X2:H"], "draw": nv["1X2:D"], "away": nv["1X2:A"]}


def lineup_update(service: "AnalysisService", day: date, fixture_id: str) -> Optional[dict]:
    """Spec 37: re-run the model when confirmed lineups arrive and report the
    change. The 'before' run uses the same inputs with lineups as predicted
    and without absences that only the confirmed team sheet revealed."""
    import copy
    from app.engine.pipeline import analyse_fixture
    from app.engine.types import LineupInfo
    from .alerts import diff_alerts
    from .assembly import build_fixture_input
    from app.verification import verify_fixture
    p = service.providers(day)
    v = verify_fixture(p.football, fixture_id, day, service.whitelist)
    if not v.ok:
        return {"fixture_id": fixture_id, "updated": False, "reason": "; ".join(v.messages), "status": v.status.value}
    fx = build_fixture_input(v.verified, p)
    if "confirmed" not in (fx.lineup_home.status, fx.lineup_away.status) or not fx.odds:
        return {"fixture_id": fixture_id, "updated": False, "reason": "Lineups not confirmed yet"}
    before_fx = copy.deepcopy(fx)
    for side in ("home", "away"):
        lu = getattr(before_fx, f"lineup_{side}")
        if lu.status == "confirmed":
            setattr(before_fx, f"lineup_{side}", LineupInfo(status="predicted", formation=lu.formation))
            absences = getattr(before_fx, f"absences_{side}")
            setattr(before_fx, f"absences_{side}", [x for x in absences if x.status == "doubtful"] if absences else [])
    now = datetime.now(timezone.utc)
    cfg = service.config.get()
    before = analyse_fixture(before_fx, cfg, now=now, ml_model=service.ml_model)
    after = analyse_fixture(fx, cfg, now=now, ml_model=service.ml_model)
    lines = [f"{fx.home.name}: {l}" for l in after["home"]["absence_impact"]["lines"]] + \
            [f"{fx.away.name}: {l}" for l in after["away"]["absence_impact"]["lines"]]
    return {
        "fixture_id": fixture_id, "updated": True,
        "before": before["prediction"]["probabilities"], "after": after["prediction"]["probabilities"],
        "before_best": (before["ranking"]["best_overall"] or {}).get("label"),
        "after_best": (after["ranking"]["best_overall"] or {}).get("label"),
        "reasons": lines or ["Confirmed lineups match the predicted ones; the change comes from lineup certainty."],
        "alerts": diff_alerts(before, after, cfg.thresholds.min_ev),
    }
