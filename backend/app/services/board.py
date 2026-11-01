"""Daily board, daily summary and the pick filter engine."""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

from app.engine.config import RISK_ORDER, EngineConfig


def all_picks(analyses: list[dict]) -> list[dict]:
    picks = []
    for a in analyses:
        for m in a["markets"]:
            if m["bookmaker_odds"] is None:
                continue
            picks.append({**m, "fixture_id": a["fixture_id"], "match": f"{a['home']['name']} vs {a['away']['name']}",
                          "competition": a["competition"]["name"], "kickoff": a["kickoff"],
                          "home": a["home"]["name"], "away": a["away"]["name"]})
    return picks


def daily_summary(analyses: list[dict], cfg: EngineConfig) -> dict[str, Any]:
    th = cfg.thresholds
    rec = [p for p in all_picks(analyses) if p["status"] == "recommended"]
    screened = [a for a in analyses if a["data_quality"]["score"] >= th.min_data_quality
                and a["prediction"]["model_disagreement"] <= th.max_model_disagreement]
    with_edge = [a for a in analyses if any((m["ev"] or 0) >= th.min_ev for m in a["markets"])]
    best = max(rec, key=lambda p: p["edge_pp"] or -99, default=None)
    return {
        "matches_analysed": len(analyses),
        "passed_screening": len(screened),
        "with_measurable_edge": len(with_edge),
        "matches_with_recommendations": len({p["fixture_id"] for p in rec}),
        "recommendations": len(rec),
        "high_confidence": sum(1 for p in rec if p["confidence"] >= 70),
        "value_opportunities": sum(1 for p in rec if p["ev"] >= th.value_pick_ev),
        "no_bet_matches": sum(1 for a in analyses if a["no_bet"]["no_bet"]),
        "rejected_selections": sum(1 for p in all_picks(analyses) if p["status"] == "avoid"),
        "avg_confidence": round(sum(p["confidence"] for p in rec) / len(rec), 1) if rec else None,
        "avg_ev_pct": round(sum(p["ev"] for p in rec) / len(rec) * 100, 1) if rec else None,
        "best_edge": {"match": best["match"], "selection": best["label"], "model": best["model_probability"],
                      "market": best["no_vig_probability"], "edge_pp": best["edge_pp"]} if best else None,
    }


def daily_board(analyses: list[dict]) -> dict[str, list[dict]]:
    picks = all_picks(analyses)
    rec = [p for p in picks if p["status"] == "recommended"]
    one_per_match: dict[str, dict] = {}
    for p in sorted(rec, key=lambda p: p["pick_score"], reverse=True):
        one_per_match.setdefault(p["fixture_id"], p)
    avoid = sorted([p for p in picks if p["status"] == "avoid" and p["model_probability"] >= 0.6],
                   key=lambda p: p["model_probability"], reverse=True)
    return {
        "best_picks": sorted(one_per_match.values(), key=lambda p: p["pick_score"], reverse=True)[:8],
        "highest_confidence": sorted(rec, key=lambda p: p["confidence"], reverse=True)[:6],
        "best_value": sorted(rec, key=lambda p: p["ev"], reverse=True)[:6],
        "conservative": sorted([p for p in rec if p["risk"] in ("Very Low", "Low")],
                               key=lambda p: p["model_probability"], reverse=True)[:6],
        "high_risk_value": sorted([p for p in rec if p["risk"] in ("High", "Very High")],
                                  key=lambda p: p["ev"], reverse=True)[:6],
        "avoid": avoid[:8],
    }


# ------------------------------------------------------------------ filter

FIELD_ALIASES = {
    "confidence": "confidence", "conf": "confidence", "ev": "ev", "odds": "bookmaker_odds",
    "probability": "model_probability", "prob": "model_probability", "risk": "risk",
    "competition": "competition", "league": "competition", "market": "market", "pickscore": "pick_score",
    "pick score": "pick_score", "score": "pick_score", "edge": "edge_pp", "status": "status",
}
CLAUSE = re.compile(r"^\s*([a-zA-Z ]+?)\s*(>=|<=|!=|>|<|=)\s*(.+?)\s*$")
RANGE = re.compile(r"^\s*([a-zA-Z ]+?)\s+([\d.]+)\s*-\s*([\d.]+)\s*$")


class FilterError(ValueError):
    pass


def parse_filter(query: str) -> list[tuple[str, str, Any]]:
    clauses = [c for c in re.split(r";|\n|\bAND\b|,(?![^()]*\))", query, flags=re.I) if c.strip()]
    out = []
    for c in clauses:
        m = RANGE.match(c)
        if m:
            f = FIELD_ALIASES.get(m.group(1).strip().lower())
            if not f:
                raise FilterError(f"Unknown field '{m.group(1)}'")
            out.append((f, ">=", float(m.group(2))))
            out.append((f, "<=", float(m.group(3))))
            continue
        m = CLAUSE.match(c)
        if not m:
            raise FilterError(f"Cannot parse '{c.strip()}'")
        f = FIELD_ALIASES.get(m.group(1).strip().lower())
        if not f:
            raise FilterError(f"Unknown field '{m.group(1)}'")
        raw = m.group(3).strip()
        if f in ("competition", "market", "status"):
            val: Any = raw
        elif f == "risk":
            match = [r for r in RISK_ORDER if r.lower() == raw.lower()]
            if not match:
                raise FilterError(f"Risk must be one of {RISK_ORDER}")
            val = match[0]
        else:
            pct = raw.endswith("%")
            val = float(raw.rstrip("%"))
            if f == "ev":                       # EV is always entered in percent
                val = val / 100
            elif f == "model_probability" and (pct or val > 1):
                val = val / 100
        out.append((f, m.group(2), val))
    return out


def apply_filter(picks: list[dict], clauses: list[tuple[str, str, Any]]) -> list[dict]:
    def ok(p: dict) -> bool:
        for f, op, v in clauses:
            if f == "risk":
                a, b = RISK_ORDER.index(p["risk"]), RISK_ORDER.index(v)
            elif f in ("competition", "status"):
                a, b = p[f].lower(), v.lower()
                if op == "=" and b not in a:
                    return False
                if op == "!=" and b in a:
                    return False
                continue
            elif f == "market":
                hay = f"{p['label']} {p['key']}".lower()
                hit = v.lower() in hay
                if (op == "=" and not hit) or (op == "!=" and hit):
                    return False
                continue
            else:
                a, b = p.get(f), v
                if a is None:
                    return False
            if not {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b, "=": a == b, "!=": a != b}[op]:
                return False
        return True

    return sorted([p for p in picks if ok(p)], key=lambda p: p.get("pick_score") or 0, reverse=True)
