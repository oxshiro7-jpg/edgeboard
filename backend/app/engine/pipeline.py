"""End-to-end fixture analysis: features -> ensemble -> markets -> value,
confidence, risk, pick score -> recommendations / no-bet decision.

`analyse_fixture` returns a JSON-serialisable dict that is stored verbatim in
model_predictions (with model version, config and input hash) so historical
predictions can never be silently rewritten.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Optional

from . import analysis as A
from . import explain, integrity
from .config import RISK_ORDER, EngineConfig
from .ensemble import half_matrices, predict
from .goals import LeagueFit, expected_goals, top_scores
from .markets import (MarketInputs, complete_market, family, label, market_type, settlement,
                      standard_keys)
from .odds import expected_value, implied_probability, remove_margin
from .scoring import (confidence, data_quality, odds_volatility, pick_score, risk, risk_at_most,
                      team_inconsistency)
from .types import FixtureInput

class UnverifiedFixtureError(ValueError):
    """Raised when the engine is handed a fixture that did not pass verification."""


GOAL_FAMILIES = {"goals", "btts", "first_half", "second_half"}
RESULT_FAMILIES = {"result", "handicap"}


def _input_hash(fx: FixtureInput) -> str:
    d = asdict(fx)
    if d.get("provenance"):
        d["provenance"] = {k: v for k, v in d["provenance"].items() if k != "verified_at"}
    payload = json.dumps(d, default=str, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _cards_mean(fx: FixtureInput) -> Optional[float]:
    if fx.referee is None or fx.referee.matches < 5:
        return None
    rates = []
    for team in (fx.home, fx.away):
        c = [r.cards for r in team.recent[:10] if r.cards is not None]
        if len(c) < 5:
            return None
        rates.append(sum(c) / len(c))
    mean = 0.5 * fx.referee.cards_per_game + 0.5 * sum(rates)
    if fx.context.derby:
        mean *= 1.08
    return mean


def _corners_mean(fx: FixtureInput) -> Optional[float]:
    vals = []
    for team, opp in ((fx.home, fx.away), (fx.away, fx.home)):
        f = [r.corners_for for r in team.recent[:10] if r.corners_for is not None]
        a = [r.corners_against for r in opp.recent[:10] if r.corners_against is not None]
        if len(f) < 5 or len(a) < 5:
            return None
        vals.append(0.5 * (sum(f) / len(f) + sum(a) / len(a)))
    return sum(vals)


def _movement(fx: FixtureInput) -> list[dict]:
    out = []
    for key, s in fx.odds.items():
        change = (s.current - s.opening) / s.opening
        prob_shift = implied_probability(s.current) - implied_probability(s.opening)
        steps = [s.opening] + [q.odds for q in s.history] + [s.current]
        max_step = max((abs(b - a) / a for a, b in zip(steps, steps[1:])), default=0.0)
        flags = []
        if abs(prob_shift) >= 0.05:
            flags.append("significant")
        if abs(change) >= 0.10:
            flags.append("sharp")
        if max_step >= 0.06:
            flags.append("sudden")
        recent = steps[-4:]
        if len(recent) >= 4 and all(b < a for a, b in zip(recent, recent[1:])) and abs(change) >= 0.06:
            flags.append("steam")
        out.append({
            "market_key": key, "opening": s.opening, "current": s.current, "high": s.high, "low": s.low,
            "change_pct": round(change * 100, 1), "implied_shift_pp": round(prob_shift * 100, 1),
            "flags": flags,
            "history": [{"t": q.captured_at.isoformat(), "odds": q.odds} for q in s.history],
        })
    out.sort(key=lambda m: abs(m["implied_shift_pp"]), reverse=True)
    return out


def _no_vig(fx: FixtureInput, key: str, method: str) -> Optional[float]:
    group = complete_market(key)
    if group and all(k in fx.odds for k in group):
        probs = remove_margin([fx.odds[k].current for k in group], method)  # type: ignore[arg-type]
        return probs[group.index(key)]
    t = market_type(key)
    if t in ("DC", "FHDC"):   # derive from the matching 1X2 book
        base = "1X2" if t == "DC" else "FH1X2"
        trio = [f"{base}:H", f"{base}:D", f"{base}:A"]
        if all(k in fx.odds for k in trio):
            ph, pd, pa = remove_margin([fx.odds[k].current for k in trio], method)  # type: ignore[arg-type]
            return {"1X": ph + pd, "X2": pd + pa, "12": ph + pa}[key.split(":")[1]]
    return None


def analyse_fixture(fx: FixtureInput, cfg: Optional[EngineConfig] = None, now: Optional[datetime] = None,
                    league_fit: Optional[LeagueFit] = None, accuracy: Optional[dict[str, float]] = None,
                    ml_model=None) -> dict[str, Any]:
    if not fx.provenance or fx.provenance.get("fixture_id") != fx.fixture_id or not fx.provenance.get("provider"):
        raise UnverifiedFixtureError(f"NO VERIFIED FIXTURE: {fx.fixture_id!r} has no provider verification")
    cfg = cfg or EngineConfig()
    now = now or datetime.now(timezone.utc)
    th = cfg.thresholds

    # ---------------- features ----------------
    ws = {
        "home_last5": A.window_stats(fx.home.recent[:5]),
        "home_last10": A.window_stats(fx.home.recent[:10]),
        "home_home": A.window_stats([r for r in fx.home.recent if r.venue == "H"][:10]),
        "away_last5": A.window_stats(fx.away.recent[:5]),
        "away_last10": A.window_stats(fx.away.recent[:10]),
        "away_away": A.window_stats([r for r in fx.away.recent if r.venue == "A"][:10]),
    }
    imp_h = A.absence_impact(fx.absences_home)
    imp_a = A.absence_impact(fx.absences_away)
    ctx_notes, fat_h, fat_a, ctx_unc = A.context_notes(fx)
    flags = A.performance_flags(fx.home, ws["home_last5"], ws["home_last10"]) + \
        A.performance_flags(fx.away, ws["away_last5"], ws["away_last10"])
    tactics = A.tactical_notes(fx.home, fx.away)

    # ---------------- model ----------------
    ens = predict(fx, cfg, imp_h, imp_a, fat_h, fat_a, league_fit)
    dq = data_quality(fx, now, ens.data_gaps)
    if ml_model is not None and cfg.ensemble.ml > 0:
        _apply_ml(ens, ml_model, cfg.ensemble.ml, fx, dq.score)
    fh_m, sh_m = half_matrices(ens.lambdas, cfg)
    mi = MarketInputs(ens.matrix, fh_m, sh_m, _cards_mean(fx), _corners_mean(fx))
    if mi.cards_mean is None:
        dq.gaps.append("Cards markets disabled: referee or team card data missing")
    if mi.corners_mean is None:
        dq.gaps.append("Corners markets disabled: corner data missing")
    inconsistency = team_inconsistency(fx)
    absence_unc = max(imp_h.uncertainty, imp_a.uncertainty)
    eh, ea = expected_goals(ens.matrix)

    # Market-specific disagreement: spread across component matrices
    comp_mats = [c for c in ens.components if c.matrix is not None and c.name not in ("market", "ml")]

    def spread(key: str) -> float:
        if family(key) in RESULT_FAMILIES and market_type(key) == "1X2":
            idx = {"H": 0, "D": 1, "A": 2}[key.split(":")[1]]
            vals = [c.probs[idx] for c in ens.components if c.name not in ("market", "ml")]
            return max(vals) - min(vals)
        vals = []
        for c in comp_mats:
            fh_c, sh_c = half_matrices(c.lambdas, cfg)  # type: ignore[arg-type]
            s = settlement(key, MarketInputs(c.matrix, fh_c, sh_c, mi.cards_mean, mi.corners_mean))  # type: ignore[arg-type]
            if s:
                vals.append(s.effective_probability())
        base = max(vals) - min(vals) if len(vals) > 1 else 0.0
        if family(key) in RESULT_FAMILIES:
            base = max(base, ens.disagreement * 0.8)
        return base

    keys = [k for k in dict.fromkeys(standard_keys() + list(fx.odds.keys()))
            if market_type(k) in cfg.enabled_markets]
    markets: list[dict] = []
    for key in keys:
        st = settlement(key, mi)
        if st is None:
            continue
        prob = st.effective_probability()
        fair = st.fair_odds()
        series = fx.odds.get(key)
        odds = series.current if series else None
        vol = odds_volatility(series)
        dis = spread(key)
        nv = _no_vig(fx, key, cfg.params.margin_method) if odds else None
        edge = (prob - nv) if nv is not None else ((prob - implied_probability(odds)) if odds else None)
        suspicious = edge is not None and edge > th.suspicious_edge_pp
        conf = confidence(key, fx, cfg, dq, dis, absence_unc, ctx_unc, vol, suspicious)
        rk = risk(key, prob, odds, fx, dis, vol, inconsistency)
        ev = st.ev(odds) if odds else None
        m: dict[str, Any] = {
            "key": key, "label": label(key, fx.home.name, fx.away.name), "family": family(key),
            "model_probability": round(prob, 4),
            "settlement": {"win": round(st.win, 4), "half_win": round(st.half_win, 4), "push": round(st.push, 4),
                           "half_loss": round(st.half_loss, 4), "loss": round(st.loss, 4)},
            "fair_odds": round(fair, 3) if math.isfinite(fair) else None,
            "bookmaker_odds": odds,
            "implied_probability": round(implied_probability(odds), 4) if odds else None,
            "no_vig_probability": round(nv, 4) if nv is not None else None,
            "edge_pp": round(edge * 100, 1) if edge is not None else None,
            "ev": round(ev, 4) if ev is not None else None,
            "confidence": conf.score, "confidence_level": conf.level, "confidence_factors": conf.factors,
            "risk_score": rk.score, "risk": rk.category, "risk_factors": rk.factors,
            "model_disagreement": round(dis, 3),
            "warnings": (["Model-market divergence above threshold: check for news the model may not have."]
                         if suspicious else []),
        }
        if odds is None:
            m.update(status="no_price", label_tag="No Price", pick_score=None,
                     reasons_rejected=["No bookmaker price available"])
            markets.append(m)
            continue
        ps, parts = pick_score(prob, ev, conf.score, vol, dq.score, family(key), cfg, accuracy)  # type: ignore[arg-type]
        m["pick_score"] = ps
        m["pick_score_parts"] = parts
        rejected = _gate(m, prob, odds, ev, conf.score, dq.score, dis, rk.category, fx, absence_unc, cfg)  # type: ignore[arg-type]
        m["reasons_rejected"] = rejected
        if rejected:
            m["status"] = "avoid"
            m["label_tag"] = "Insufficient Edge" if any(r.startswith(("No statistical edge", "Likely outcome")) for r in rejected) else "Avoid"
        else:
            m["status"] = "recommended"
            m["label_tag"] = _label_tag(prob, ev, conf.score, ps, rk.category, cfg)  # type: ignore[arg-type]
            m["why"] = explain.reasons(key, prob, fx, ws, ens.lambdas, ens.probs,
                                       {c.name: c.probs for c in ens.components})
            m["risks"] = _risk_notes(m, fx, ctx_notes, imp_h, imp_a, flags)
        markets.append(m)

    integrity.enforce(markets, fx.fixture_id)   # recompute every displayed number; reject on mismatch
    ranking = _rank(markets)
    rec = [m for m in markets if m["status"] == "recommended"]
    no_bet = _no_bet(markets, dq.score, ens.disagreement, cfg)
    best = ranking.get("best_overall")
    status = ("Avoid" if dq.score < th.min_data_quality
              else "Strong opportunity" if best and best["pick_score"] >= th.strong_pick_score
              else "Moderate opportunity" if rec else "No edge")

    def team_block(team, last5, last10, venue_ws, imp: A.AbsenceImpact, side: str) -> dict:
        w = last10
        return {
            "id": team.team_id, "name": team.name, "short_name": team.short_name, "badge_url": team.badge_url,
            "elo": round(team.elo) if team.elo is not None else None, "league_position": team.league_position,
            "last5": asdict(last5) if last5 else None, "last10": asdict(last10) if last10 else None,
            "venue": asdict(venue_ws) if venue_ws else None,
            "style": asdict(team.style),
            "absences": [asdict(a) for a in (fx.absences_home if side == "H" else fx.absences_away)],
            "absence_impact": {"attack_multiplier": round(imp.attack_multiplier, 3),
                               "defence_multiplier": round(imp.defence_multiplier, 3),
                               "uncertainty": round(imp.uncertainty, 3), "lines": imp.lines},
            "lineup": asdict(fx.lineup_home if side == "H" else fx.lineup_away),
            "ratings": {
                "attack": A.rating_0_100(w.adj_xg_for, 1.4, 0.6) if w and w.adj_xg_for is not None else None,
                "defence": A.rating_0_100(-w.adj_xg_against, -1.4, 0.6) if w and w.adj_xg_against is not None else None,
                "form": A.rating_0_100(last5.weighted_points_per_game, 1.4, 0.9) if last5 else None,
                "venue_strength": A.rating_0_100(venue_ws.xgd, 0.0, 0.8) if venue_ws and venue_ws.xgd is not None else None,
                "squad_availability": round(imp.availability_score),
                "model_rating": A.rating_0_100(team.elo, 1500, 220) if team.elo is not None else None,
            },
        }

    h2h = [{"date": r.date.date().isoformat(), "venue": r.venue, "score": f"{r.goals_for}-{r.goals_against}",
            "xg": f"{r.xg_for:.2f}-{r.xg_against:.2f}" if r.xg_for is not None else None} for r in fx.h2h]

    return {
        "fixture_id": fx.fixture_id, "provenance": fx.provenance,
        "competition": {"id": fx.competition_id, "name": fx.competition_name, "type": fx.competition_type},
        "kickoff": fx.kickoff.isoformat(), "status": fx.status,
        "data_updated_at": fx.data_updated_at.isoformat() if fx.data_updated_at else None,
        "model": {"version": cfg.model_version, "generated_at": now.isoformat(), "input_hash": _input_hash(fx),
                  "config": cfg.to_dict()},
        "home": team_block(fx.home, ws["home_last5"], ws["home_last10"], ws["home_home"], imp_h, "H"),
        "away": team_block(fx.away, ws["away_last5"], ws["away_last10"], ws["away_away"], imp_a, "A"),
        "prediction": {
            "probabilities": {"home": round(ens.probs[0], 4), "draw": round(ens.probs[1], 4), "away": round(ens.probs[2], 4)},
            "expected_goals": {"home": round(eh, 2), "away": round(ea, 2), "total": round(eh + ea, 2)},
            "expected_score_range": _score_range(ens.matrix),
            "score_distribution": [{"score": s, "p": round(p, 4)} for s, p in top_scores(ens.matrix, 12)],
            "score_matrix": [[round(x, 4) for x in row[:7]] for row in ens.matrix[:7]],
            "score_matrix_full": [[round(x, 7) for x in row] for row in ens.matrix],
            "lambdas": [round(ens.lambdas[0], 4), round(ens.lambdas[1], 4)],
            "first_half_share": cfg.params.first_half_goal_share,
            "components": [{"name": c.name, "weight": round(ens.weights[c.name], 3),
                            "home": round(c.probs[0], 4), "draw": round(c.probs[1], 4), "away": round(c.probs[2], 4),
                            "lambdas": [round(x, 3) for x in c.lambdas] if c.lambdas else None, "note": c.note}
                           for c in ens.components],
            "model_disagreement": round(ens.disagreement, 3),
            "goals_disagreement": round(ens.goals_disagreement, 3),
            "cards_mean": round(mi.cards_mean, 2) if mi.cards_mean else None,
            "corners_mean": round(mi.corners_mean, 2) if mi.corners_mean else None,
        },
        "data_quality": {"score": dq.score, "factors": dq.factors, "gaps": dq.gaps},
        "context": {"notes": ctx_notes, "uncertainty": round(ctx_unc, 3)},
        "flags": [asdict(f) for f in flags],
        "tactics": tactics,
        "h2h": {"matches": h2h, "weight_in_model": 0.0,
                "note": "Head-to-head is shown for context only and carries no weight in the probabilities."},
        "markets": markets,
        "ranking": ranking,
        "summary": {
            "status": status,
            "recommended_count": len(rec),
            "best_market": best["label"] if best else None,
            "best_pick_score": best["pick_score"] if best else None,
            "value_score": round(max([m["ev"] for m in rec], default=0.0) * 100, 1),
            "confidence": best["confidence"] if best else max((m["confidence"] for m in markets if m["bookmaker_odds"]), default=None),
            "risk": best["risk"] if best else None,
            "max_edge_pp": max((m["edge_pp"] for m in rec), default=None),
        },
        "no_bet": no_bet,
        "movement": _movement(fx),
    }


def _apply_ml(ens, model, w: float, fx: FixtureInput, dq_score: float) -> None:
    """Blend the promoted ML component into the ensemble 1X2 and reshape the
    score matrix so every derived market stays consistent."""
    from .ensemble import Component
    from .goals import reweight_to_outcomes
    from .ml import features
    comps = {c.name: c.probs for c in ens.components}
    elo_diff = (fx.home.elo - fx.away.elo) if fx.home.elo is not None and fx.away.elo is not None else 0.0
    x = features(comps, elo_diff, sum(expected_goals(ens.matrix)), dq_score, ens.disagreement)
    q = model.predict_proba(x)
    probs = tuple((1 - w) * a + w * b for a, b in zip(ens.probs, q))
    ens.weights = {k: v * (1 - w) for k, v in ens.weights.items()}
    ens.weights["ml"] = w
    ens.components.append(Component("ml", q, None, None, f"{model.version}: stacked logistic regression"))
    ens.probs = probs
    ens.matrix = reweight_to_outcomes(ens.matrix, probs)


def _gate(m: dict, prob: float, odds: float, ev: float, conf: float, dq: float, dis: float,
          risk_cat: str, fx: FixtureInput, absence_unc: float, cfg: EngineConfig) -> list[str]:
    th = cfg.thresholds
    r: list[str] = []
    if odds < th.min_odds:
        r.append(f"Odds too short ({odds:.2f} < {th.min_odds:.2f} minimum)")
    if odds > th.max_odds:
        r.append(f"Odds above configured maximum ({odds:.2f} > {th.max_odds:.2f})")
    if ev < th.min_ev:
        if prob >= 0.65:
            r.append(f"Likely outcome ({prob * 100:.0f}%) but the price offers no value (EV {ev * 100:+.1f}%) - PASS")
        else:
            r.append(f"No statistical edge (EV {ev * 100:+.1f}%, minimum {th.min_ev * 100:+.0f}%)")
    if dq < th.min_data_quality:
        r.append(f"Poor data quality ({dq:.0f}/100)")
    if conf < th.min_confidence:
        r.append(f"Confidence {conf:.0f} below {th.min_confidence:.0f} threshold")
    if dis > th.max_model_disagreement:
        r.append(f"Conflicting models (spread {dis * 100:.0f} pp)")
    if m.get("pick_score") is not None and m["pick_score"] < th.min_pick_score:
        r.append(f"Pick score {m['pick_score']:.0f} below {th.min_pick_score:.0f} threshold")
    edge = m.get("edge_pp")
    if edge is not None and edge / 100 > 2 * th.suspicious_edge_pp:
        r.append(f"Model-market divergence too large ({edge:+.0f} pp): likely information the model lacks")
    if not risk_at_most(risk_cat, th.max_risk):
        r.append(f"Excessive variance (risk {risk_cat})")
    if fx.lineup_home.status == "unknown" and fx.lineup_away.status == "unknown" and absence_unc > 0.3:
        r.append("Uncertain lineups with significant absences")
    return r


def _label_tag(prob: float, ev: float, conf: float, ps: float, risk_cat: str, cfg: EngineConfig) -> str:
    th = cfg.thresholds
    if ps >= th.highest_confidence_score and conf >= 80:
        return "Highest Confidence"
    if RISK_ORDER.index(risk_cat) >= RISK_ORDER.index("High"):
        return "Higher Risk"
    if ps >= th.strong_pick_score:
        return "Strong Pick"
    if prob >= 0.70 and RISK_ORDER.index(risk_cat) <= RISK_ORDER.index("Low"):
        return "Conservative Pick"
    if ev >= th.value_pick_ev:
        return "Value Pick"
    return "Optional"


def _risk_notes(m: dict, fx: FixtureInput, ctx: list[str], imp_h: A.AbsenceImpact, imp_a: A.AbsenceImpact,
                flags: list[A.Flag]) -> list[str]:
    notes = list(ctx)
    notes += [f"{fx.home.name}: {l}" for l in imp_h.lines] + [f"{fx.away.name}: {l}" for l in imp_a.lines]
    notes += [f.text for f in flags]
    for side, lu in ((fx.home.name, fx.lineup_home), (fx.away.name, fx.lineup_away)):
        if lu.status != "confirmed":
            notes.append(f"{side} lineup {lu.status}; prediction will be recalculated when XIs are confirmed.")
    notes += m["warnings"]
    if m["model_disagreement"] > 0.08:
        notes.append(f"Component models differ by {m['model_disagreement'] * 100:.0f} pp on this market.")
    return notes


def _rank(markets: list[dict]) -> dict[str, Any]:
    rec = [m for m in markets if m["status"] == "recommended"]
    by_ps = sorted(rec, key=lambda m: m["pick_score"], reverse=True)

    def first(items):
        return items[0] if items else None

    ranking = {
        "best_overall": first(by_ps),
        "best_conservative": first(sorted([m for m in rec if m["risk"] in ("Very Low", "Low")],
                                          key=lambda m: m["model_probability"], reverse=True)),
        "best_value": first(sorted(rec, key=lambda m: m["ev"], reverse=True)),
        "best_higher_odds": first([m for m in by_ps if m["bookmaker_odds"] >= 2.2]),
        "best_goals": first([m for m in by_ps if m["family"] in GOAL_FAMILIES]),
        "best_result": first([m for m in by_ps if m["family"] in RESULT_FAMILIES]),
    }
    used = {m["key"] for m in ranking.values() if m}
    ranking["optional"] = [m for m in by_ps if m["key"] not in used][:6]
    priced_rejects = [m for m in markets if m["status"] == "avoid"]
    likely = sorted([m for m in priced_rejects if m["model_probability"] >= 0.6],
                    key=lambda m: m["model_probability"], reverse=True)[:3]
    tempting = sorted([m for m in priced_rejects if m not in likely and (m["ev"] or 0) > 0],
                      key=lambda m: m["ev"], reverse=True)[:3]
    ranking["avoided"] = likely + tempting
    return ranking


def _no_bet(markets: list[dict], dq: float, disagreement: float, cfg: EngineConfig) -> dict:
    rec = [m for m in markets if m["status"] == "recommended"]
    if rec:
        return {"no_bet": False, "reasons": []}
    reasons: dict[str, int] = {}
    for m in markets:
        for r in m.get("reasons_rejected", []):
            head = r.split(" (")[0].split(" - ")[0]
            if head.startswith("Likely outcome"):
                head = "Odds too short for the likely outcomes"
            elif head.startswith("Confidence"):
                head = "Confidence below threshold"
            elif head.startswith("Pick score"):
                head = "Pick score below threshold"
            reasons[head] = reasons.get(head, 0) + 1
    ordered = sorted(reasons.items(), key=lambda kv: kv[1], reverse=True)
    summary = [k for k, _ in ordered if k != "No bookmaker price available"][:4]
    if dq < cfg.thresholds.min_data_quality:
        summary.insert(0, "Poor data quality")
    if disagreement > cfg.thresholds.max_model_disagreement:
        summary.insert(0, "Conflicting models")
    return {"no_bet": True, "reasons": list(dict.fromkeys(summary))}


def _score_range(m) -> str:
    cells = sorted(((p, i, j) for i, row in enumerate(m) for j, p in enumerate(row)), reverse=True)
    acc, hs, as_ = 0.0, [], []
    for p, i, j in cells:
        acc += p
        hs.append(i)
        as_.append(j)
        if acc >= 0.5:
            break
    return f"{min(hs)}-{min(as_)} to {max(hs)}-{max(as_)}"
