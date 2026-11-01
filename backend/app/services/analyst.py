"""Phase 7: AI analyst.

Every answer is assembled from tool results over stored analyses; the
analyst has no other source of numbers. Two modes share one tool layer:

* rule-based router (always available, deterministic, no API key)
* Claude tool-use (when ANTHROPIC_API_KEY is set): the model chooses tools
  and writes the prose, with an instruction to use only tool output. Tool
  results are returned alongside the answer so the UI can show the sources.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Optional

from app.engine.multiples import build_multiple, evaluate_multiple, leg_from_market
from app.services.board import FilterError, all_picks, apply_filter, parse_filter

ANALYST_MODEL = os.getenv("ANALYST_MODEL", "claude-sonnet-5-5")


def _pct(p: Optional[float]) -> str:
    return "n/a" if p is None else f"{p * 100:.0f}%"


class AnalystTools:
    def __init__(self, analyses: list[dict]):
        self.analyses = analyses
        self.by_id = {a["fixture_id"]: a for a in analyses}

    # ---------------------------------------------------------- lookups
    def find_match(self, text: str) -> Optional[dict]:
        found = self.find_matches(text)
        return found[0] if len(found) == 1 else None

    def find_matches(self, text: str) -> list[dict]:
        """Exact names only: a verified team's full provider name must appear
        in the question (case-insensitive, whole words). No partial words, no
        similarity, no substitution of a different club."""
        found = []
        for a in self.analyses:
            if any(_contains_name(text, a[side]["name"]) for side in ("home", "away")):
                found.append(a)
        return found

    def team_side(self, a: dict, text: str) -> Optional[str]:
        h, w = _contains_name(text, a["home"]["name"]), _contains_name(text, a["away"]["name"])
        return "H" if h and not w else "A" if w and not h else None

    # ---------------------------------------------------------- tools
    def strongest_edge(self) -> dict:
        rec = [p for p in all_picks(self.analyses) if p["status"] == "recommended"]
        if not rec:
            return {"found": False}
        best = max(rec, key=lambda p: p["edge_pp"] or -99)
        top_score = max(rec, key=lambda p: p["pick_score"] or 0)
        return {"found": True, "largest_edge": _slim(best), "best_pick_score": _slim(top_score)}

    def filter_picks(self, query: str, limit: int = 10) -> dict:
        try:
            picks = apply_filter(all_picks(self.analyses), parse_filter(query))
        except FilterError as e:
            return {"error": str(e)}
        return {"query": query, "count": len(picks), "picks": [_slim(p) for p in picks[:limit]]}

    def build(self, legs: int = 3, mode: str = "balanced") -> dict:
        r = build_multiple(self.by_id, legs, mode)
        if r.get("built"):
            m = r["multiple"]
            r["multiple"] = {k: m[k] for k in ("combined_odds", "probability", "naive_probability", "ev", "fair_odds",
                                               "confidence", "risk", "health", "correlations")} | {
                "legs": [{k: l[k] for k in ("fixture_id", "key", "label", "match", "odds", "model_probability", "ev",
                                            "confidence", "risk")} for l in m["legs"]]}
        return r

    def explain(self, fixture_id: str, market_key: str) -> dict:
        """Verified payload for one market: {fixture, stats, odds, model_prediction}.
        Everything comes from the stored analysis of a verified fixture."""
        a = self.by_id.get(fixture_id)
        if not a:
            return {"error": f"NO VERIFIED FIXTURE: {fixture_id}"}
        m = next((x for x in a["markets"] if x["key"] == market_key), None)
        if not m:
            return {"error": f"{market_key} is not priced for this fixture"}
        side = lambda k: {kk: a[k][kk] for kk in ("id", "name")}
        stats = {k: {"last10": a[k]["last10"], "absences": a[k]["absence_impact"]["lines"], "lineup": a[k]["lineup"]["status"]}
                 for k in ("home", "away")}
        return {
            "fixture": {"fixture_id": fixture_id, "provider": (a.get("provenance") or {}).get("provider"),
                        "competition": a["competition"]["name"], "kickoff": a["kickoff"],
                        "home": side("home"), "away": side("away"),
                        "verification_status": (a.get("verification") or {}).get("status")},
            "stats": stats,
            "odds": {"market": market_key, "label": m["label"], "bookmaker_odds": m["bookmaker_odds"],
                     "implied_probability": m["implied_probability"], "no_vig_probability": m["no_vig_probability"]},
            "model_prediction": {"model_probability": m["model_probability"], "fair_odds": m["fair_odds"], "ev": m["ev"],
                                 "edge_pp": m["edge_pp"], "confidence": m["confidence"], "confidence_level": m["confidence_level"],
                                 "risk": m["risk"], "status": m["status"], "why": m.get("why", []), "risks": m.get("risks", []),
                                 "reasons_rejected": m.get("reasons_rejected", [])},
        }

    def remove_riskiest(self, legs: list[dict]) -> dict:
        objs = [leg_from_market(self.by_id[l["fixture_id"]], l["market_key"], l.get("odds")) for l in legs]
        if len(objs) < 2:
            return {"error": "Need at least two selections"}
        worst = max(objs, key=lambda l: l.risk_score)
        before = evaluate_multiple(objs, self.by_id)
        after = evaluate_multiple([l for l in objs if l is not worst], self.by_id)
        return {"removed": {"label": worst.label, "match": worst.match, "risk": worst.risk, "risk_score": worst.risk_score},
                "before": {"odds": round(before.combined_odds, 2), "probability": round(before.probability, 4), "ev": round(before.ev, 4)},
                "after": {"odds": round(after.combined_odds, 2), "probability": round(after.probability, 4), "ev": round(after.ev, 4)},
                "remaining": [{"fixture_id": l.fixture_id, "market_key": l.key, "label": l.label} for l in objs if l is not worst]}

    def match_overview(self, fixture_id: str) -> dict:
        a = self.by_id.get(fixture_id)
        if not a:
            return {"error": "Unknown fixture"}
        best = a["ranking"].get("best_overall")
        return {"match": f"{a['home']['name']} vs {a['away']['name']}", "competition": a["competition"]["name"],
                "probabilities": a["prediction"]["probabilities"], "expected_goals": a["prediction"]["expected_goals"],
                "status": a["summary"]["status"], "best": _slim(best) if best else None, "no_bet": a["no_bet"],
                "data_quality": a["data_quality"]["score"]}


def _contains_name(text: str, name: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(name.lower()) + r"(?!\w)", text.lower()) is not None


NAMED_TEAM = re.compile(r"(?i:why is|why isn't|why are|how does|how do|about|for|prefer [\w .]+ for)\s+(?i:the\s+)?"
                        r"([A-Z][\w.'&-]*(?:\s+[A-Z][\w.'&-]*)*)")


def named_team(question: str) -> Optional[str]:
    """The capitalised team name a question refers to, if any (used only to
    report TEAM NOT VERIFIED; never to look anything up)."""
    m = NAMED_TEAM.search(question)
    if not m:
        return None
    name = re.sub(r"\s+(ML|Moneyline|To|Win|Not|Under|Over|BTTS)$", "", m.group(1).strip())
    return name or None


def _slim(m: dict) -> dict:
    keys = ("fixture_id", "match", "key", "label", "bookmaker_odds", "fair_odds", "model_probability", "ev",
            "edge_pp", "confidence", "risk", "pick_score", "label_tag")
    return {k: m.get(k) for k in keys if k in m}


# ------------------------------------------------------------------ rule-based router

MARKET_PHRASES = [
    (r"under\s*(\d\.5)", lambda g: f"OU:{g[0]}:under"), (r"over\s*(\d\.5)", lambda g: f"OU:{g[0]}:over"),
    (r"btts\s*(yes|no)|both teams to score\s*(yes|no)?", lambda g: f"BTTS:{(g[0] or g[1] or 'yes').lower()}"),
    (r"\bdraw\b", lambda g: "1X2:D"),
]


def route(question: str, tools: AnalystTools, context: Optional[dict] = None) -> dict[str, Any]:
    q = question.strip()
    ql = q.lower()
    ctx = context or {}
    calls: list[dict] = []

    def call(name: str, fn: Callable, *args, **kw):
        out = fn(*args, **kw)
        calls.append({"tool": name, "args": [*args] + ([kw] if kw else []), "result": out})
        return out

    # build multiple
    m = re.search(r"(conservative|balanced|value|aggressive|safe(?:st)?)?\s*(\d+)[\s-]*(?:leg|fold|selection)", ql)
    if "multiple" in ql or "acca" in ql or "accumulator" in ql or "parlay" in ql or m and "build" in ql:
        mode = (m.group(1) if m and m.group(1) else re.search(r"conservative|balanced|value|aggressive|safe", ql) or "balanced")
        mode = mode if isinstance(mode, str) else mode.group(0)
        mode = "conservative" if mode.startswith("safe") else mode
        n = int(m.group(2)) if m else 3
        r = call("build_multiple", tools.build, n, mode)
        if not r.get("built"):
            return _answer(r["reason"], calls)
        mm = r["multiple"]
        lines = [f"{l['label']} ({l['match']}) at {l['odds']:.2f}, model {_pct(l['model_probability'])}" for l in mm["legs"]]
        txt = (f"{mode.capitalize()} {n}-leg multiple:\n- " + "\n- ".join(lines) +
               f"\nCombined odds {mm['combined_odds']:.2f}; estimated hit probability {_pct(mm['probability'])} "
               f"(fair odds {mm['fair_odds']:.2f}); EV {mm['ev'] * 100:+.1f}%; risk {mm['risk']}.")
        if mm["correlations"]:
            txt += "\nCorrelation: " + " ".join(c["text"] for c in mm["correlations"])
        return _answer(txt, calls)

    # remove riskiest from slip
    if "riskiest" in ql or ("remove" in ql and "risk" in ql):
        legs = ctx.get("slip") or []
        if len(legs) < 2:
            return _answer("Add at least two selections to the slip first; then I can remove the riskiest one.", calls)
        r = call("remove_riskiest", tools.remove_riskiest, legs)
        if "error" in r:
            return _answer(r["error"], calls)
        return _answer(f"Removed {r['removed']['label']} ({r['removed']['match']}, risk {r['removed']['risk']}). "
                       f"Odds {r['before']['odds']} to {r['after']['odds']}, hit probability "
                       f"{_pct(r['before']['probability'])} to {_pct(r['after']['probability'])}, EV "
                       f"{r['before']['ev'] * 100:+.1f}% to {r['after']['ev'] * 100:+.1f}%.", calls, slip=r["remaining"])

    # strongest edge
    if "edge" in ql or "strongest" in ql or "best bet" in ql:
        r = call("strongest_edge", tools.strongest_edge)
        if not r["found"]:
            return _answer("No selection passes the thresholds today, so there is no edge to point to. No bet is the call.", calls)
        e, b = r["largest_edge"], r["best_pick_score"]
        txt = (f"Largest model edge: {e['label']} in {e['match']}: model {_pct(e['model_probability'])} vs "
               f"bookmaker {e['bookmaker_odds']:.2f} (fair {e['fair_odds']:.2f}), edge {e['edge_pp']:+.1f} pp, "
               f"EV {e['ev'] * 100:+.1f}%, confidence {e['confidence']:.0f}, risk {e['risk']}.")
        if b["key"] != e["key"] or b["fixture_id"] != e["fixture_id"]:
            txt += (f" Highest overall pick score: {b['label']} ({b['match']}), score {b['pick_score']:.0f}. "
                    "A big edge is not automatically the best bet; confidence and risk count too.")
        return _answer(txt, calls)

    # odds range / confidence filters
    rng = re.search(r"between\s*(\d+(?:\.\d+)?)\s*(?:and|-|to)\s*(\d+(?:\.\d+)?)", ql)
    conf = re.search(r"confidence\s*(?:above|over|>|greater than|of at least|>=)\s*(\d+)", ql)
    ev = re.search(r"ev\s*(?:above|over|>)\s*(\d+(?:\.\d+)?)", ql)
    limit = re.search(r"\b(\d+)\s*(?:picks|bets|selections)", ql)
    if rng or conf or ev:
        parts = ["Status = recommended"]
        if rng:
            parts.append(f"Odds {rng.group(1)}-{rng.group(2)}")
        if conf:
            parts.append(f"Confidence > {conf.group(1)}")
        if ev:
            parts.append(f"EV > {ev.group(1)}%")
        n = int(limit.group(1)) if limit else 10
        r = call("filter_picks", tools.filter_picks, "; ".join(parts), n)
        if r.get("error"):
            return _answer(r["error"], calls)
        if not r["count"]:
            return _answer(f"Nothing matches ({'; '.join(parts)}). Loosen a condition or accept that today has no such bet.", calls)
        lines = [f"{p['label']} ({p['match']}) {p['bookmaker_odds']:.2f}, model {_pct(p['model_probability'])}, "
                 f"EV {p['ev'] * 100:+.1f}%, confidence {p['confidence']:.0f}, risk {p['risk']}" for p in r["picks"]]
        head = f"{r['count']} selection{'s' if r['count'] != 1 else ''} match" + (f"; top {len(lines)}" if r["count"] > len(lines) else "")
        return _answer(head + ":\n- " + "\n- ".join(lines), calls)

    # why / why not for a market
    if ql.startswith("why") or "why" in ql or "explain" in ql:
        a = tools.by_id.get(ctx.get("fixture_id", "")) or tools.find_match(q)
        if not a:
            cands = tools.find_matches(q)
            if len(cands) > 1:
                names = "; ".join(f"{c['home']['name']} vs {c['away']['name']}" for c in cands)
                return _answer(f"That club appears in more than one fixture ({names}). Which match do you mean?", calls)
            team = named_team(q)
            if team:
                return _answer(f"TEAM NOT VERIFIED: {team}. No verified fixture today involves a team with that exact name.", calls)
            return _answer("Which match? Name a team exactly as listed on today's verified fixtures, or open the match first.", calls)
        key = None
        for pat, fn in MARKET_PHRASES:
            mm = re.search(pat, ql)
            if mm:
                key = fn(mm.groups())
                break
        if key is None and re.search(r"\b(ml|moneyline|to win|win)\b", ql):
            side = tools.team_side(a, q)
            if side is None:
                return _answer(f"Which team's win do you mean in {a['home']['name']} vs {a['away']['name']}? "
                               "Use the team's exact name.", calls)
            key = f"1X2:{side}"
        if key is None:
            r = call("match_overview", tools.match_overview, a["fixture_id"])
            return _answer(_overview_text(r), calls)
        r = call("explain", tools.explain, a["fixture_id"], key)
        if "error" in r:
            return _answer(r["error"], calls)
        return _answer(_explain_text(r), calls)

    # match overview fallback
    a = tools.find_match(q)
    if not a and len(tools.find_matches(q)) > 1:
        names = "; ".join(f"{c['home']['name']} vs {c['away']['name']}" for c in tools.find_matches(q))
        return _answer(f"That club appears in more than one fixture ({names}). Which match do you mean?", calls)
    if a:
        r = call("match_overview", tools.match_overview, a["fixture_id"])
        return _answer(_overview_text(r), calls)
    team = named_team(q)
    if team:
        return _answer(f"TEAM NOT VERIFIED: {team}. No verified fixture today involves a team with that exact name.", calls)
    return _answer("I can answer from today's stored analysis: ask why a market is or isn't recommended, for the "
                   "strongest edge, for picks in an odds or confidence range, or to build or trim a multiple.", calls)


def _explain_text(r: dict) -> str:
    f, o, m = r["fixture"], r["odds"], r["model_prediction"]
    match = f"{f['home']['name']} vs {f['away']['name']}"
    head = (f"{o['label']} ({match}): model {_pct(m['model_probability'])}, fair odds {m['fair_odds']:.2f}" +
            (f", bookmaker {o['bookmaker_odds']:.2f}" if o.get("bookmaker_odds") else ", ODDS UNAVAILABLE") +
            (f", market {_pct(o['no_vig_probability'])} after margin" if o.get("no_vig_probability") else "") +
            (f", EV {m['ev'] * 100:+.2f}%" if m.get("ev") is not None else "") +
            f", confidence {m['confidence']:.0f} ({m['confidence_level']}), risk {m['risk']}.")
    if m["status"] == "recommended":
        body = "Recommended because:\n- " + "\n- ".join(m["why"])
        if m["risks"]:
            body += "\nRisks:\n- " + "\n- ".join(m["risks"][:5])
    else:
        body = "Not recommended:\n- " + "\n- ".join(m["reasons_rejected"])
    return head + "\n" + body


def _overview_text(r: dict) -> str:
    if "error" in r:
        return r["error"]
    p = r["probabilities"]
    txt = (f"{r['match']} ({r['competition']}): home {_pct(p['home'])}, draw {_pct(p['draw'])}, away {_pct(p['away'])}; "
           f"expected goals {r['expected_goals']['home']:.2f}-{r['expected_goals']['away']:.2f}. Status: {r['status']}.")
    if r["best"]:
        b = r["best"]
        txt += f" Best bet: {b['label']} at {b['bookmaker_odds']:.2f} (EV {b['ev'] * 100:+.1f}%, confidence {b['confidence']:.0f})."
    elif r["no_bet"]["no_bet"]:
        txt += " No bet: " + "; ".join(r["no_bet"]["reasons"]) + "."
    return txt


def _answer(text: str, calls: list[dict], **extra) -> dict:
    return {"answer": text, "mode": "rules", "tool_calls": calls, **extra}


# ------------------------------------------------------------------ Claude tool-use mode

TOOL_SPECS = [
    {"name": "strongest_edge", "description": "Largest model edge and highest pick score among today's recommended selections.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "filter_picks", "description": "Filter priced selections. Query grammar: 'Confidence > 70; EV > 5%; Odds 1.40-1.80; Risk <= Moderate; Market = Over; Competition = La Liga; Status = recommended'.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]}},
    {"name": "build_multiple", "description": "Build a correlation-aware multiple.",
     "input_schema": {"type": "object", "properties": {"legs": {"type": "integer", "minimum": 2, "maximum": 8},
                      "mode": {"type": "string", "enum": ["conservative", "balanced", "value", "aggressive"]}}, "required": ["legs", "mode"]}},
    {"name": "explain", "description": "Verified payload {fixture, stats, odds, model_prediction} for one market on a verified fixture, "
     "including stored reasons. Market keys: 1X2:H|D|A, DC:1X|X2|12, DNB:H|A, OU:<line>:over|under, "
     "TT:H|A:<line>:over|under, BTTS:yes|no, AH:H|A:<line>.",
     "input_schema": {"type": "object", "properties": {"fixture_id": {"type": "string"}, "market_key": {"type": "string"}}, "required": ["fixture_id", "market_key"]}},
    {"name": "list_matches", "description": "Today's fixtures with ids, teams and status.", "input_schema": {"type": "object", "properties": {}}},
]

SYSTEM = (
    "You are an explanation layer only. You may only use the data supplied to you. Never invent teams, matches, "
    "players, statistics, odds, injuries, lineups, probabilities or betting markets. If information is missing, state "
    "that the data is unavailable.\n\n"
    "All data comes from tools and has already been verified against the fixtures API; every number (probabilities, "
    "odds, EV, fair odds, confidence) was calculated by backend code. Quote those numbers exactly and do not compute "
    "new ones. Never describe a bet as certain or guaranteed. 'No bet' is a valid answer. If a team named by the user "
    "is not in list_matches, reply 'TEAM NOT VERIFIED: <name>' and do not suggest a similarly named club."
)


def claude_answer(question: str, tools: AnalystTools, context: Optional[dict] = None, api_key: Optional[str] = None) -> dict:
    """Claude Messages API with tool use (raw HTTP, so no SDK dependency).
    Falls back to the rule-based router when no key is configured."""
    key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return route(question, tools, context)
    import httpx

    impl = {
        "strongest_edge": lambda i: tools.strongest_edge(),
        "filter_picks": lambda i: tools.filter_picks(i["query"], i.get("limit", 10)),
        "build_multiple": lambda i: tools.build(i["legs"], i["mode"]),
        "explain": lambda i: tools.explain(i["fixture_id"], i["market_key"]),
        "list_matches": lambda i: [{"fixture_id": a["fixture_id"], "match": f"{a['home']['name']} vs {a['away']['name']}",
                                    "status": a["summary"]["status"]} for a in tools.analyses],
    }
    msgs: list[dict] = [{"role": "user", "content": question + (f"\n\nContext: {json.dumps(context)}" if context else "")}]
    calls = []
    for _ in range(6):
        r = httpx.post("https://api.anthropic.com/v1/messages", timeout=60, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": ANALYST_MODEL, "max_tokens": 1200, "system": SYSTEM, "tools": TOOL_SPECS, "messages": msgs})
        r.raise_for_status()
        body = r.json()
        msgs.append({"role": "assistant", "content": body["content"]})
        uses = [b for b in body["content"] if b["type"] == "tool_use"]
        if not uses:
            text = "".join(b.get("text", "") for b in body["content"] if b["type"] == "text")
            return {"answer": text, "mode": "claude", "tool_calls": calls}
        results = []
        for u in uses:
            out = impl[u["name"]](u["input"]) if u["name"] in impl else {"error": "unknown tool"}
            calls.append({"tool": u["name"], "args": [u["input"]], "result": out})
            results.append({"type": "tool_result", "tool_use_id": u["id"], "content": json.dumps(out, default=str)[:20000]})
        msgs.append({"role": "user", "content": results})
    return {"answer": "The analyst stopped after too many tool calls. Try a narrower question.", "mode": "claude", "tool_calls": calls}
