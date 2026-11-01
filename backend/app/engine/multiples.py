"""Phase 4: accumulators.

Correlation is handled exactly rather than with a fudge factor: legs on the
same fixture are evaluated jointly over that fixture's score distribution
(the ensemble full-time matrix, with first-half goals split binomially from
full-time goals, which is exact for a Poisson scoring process). Legs on
different fixtures are independent. Cards and corners legs use their own
count models and are assumed independent of the scoreline (flagged).

Settlement uses real bookmaker rules: a pushed leg is void (odds 1), quarter
lines settle half-stakes, so EV and fair odds stay exact for Asian lines.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Iterable, Optional

from .config import RISK_ORDER
from .goals import poisson_pmf
from .markets import family, market_type
from .scoring import risk_category

HALF_TYPES = {"FH1X2", "FHDC", "FHOU", "SHOU"}
COUNT_TYPES = {"CARDS", "CORNERS"}


# --------------------------------------------------------------- settlement

def _settle(adj: float) -> str:
    return "W" if adj > 1e-9 else "L" if adj < -1e-9 else "P"


def _ret(outcome: str, odds: float) -> float:
    return odds if outcome == "W" else 1.0 if outcome == "P" else 0.0


def leg_return(key: str, odds: float, h: int, a: int, h1: int = 0, a1: int = 0, count: int = 0) -> float:
    """Payout multiplier per unit stake for one leg in a given match state."""
    p = key.split(":")
    t = p[0]

    def win(cond: bool) -> float:
        return odds if cond else 0.0

    if t in ("1X2", "FH1X2"):
        x, y = (h, a) if t == "1X2" else (h1, a1)
        return win({"H": x > y, "D": x == y, "A": x < y}[p[1]])
    if t in ("DC", "FHDC"):
        x, y = (h, a) if t == "DC" else (h1, a1)
        return win({"1X": x >= y, "X2": x <= y, "12": x != y}[p[1]])
    if t in ("DNB", "AH"):
        d = h - a if p[1] == "H" else a - h
        line = 0.0 if t == "DNB" else float(p[2])
        if abs(line * 4) % 2 == 1:   # quarter line: half stake on each neighbour
            return 0.5 * _ret(_settle(d + line - 0.25), odds) + 0.5 * _ret(_settle(d + line + 0.25), odds)
        return _ret(_settle(d + line), odds)
    if t == "EH":
        d = h - a + int(float(p[2]))
        return win({"H": d > 0, "D": d == 0, "A": d < 0}[p[1]])
    if t in ("OU", "FHOU", "SHOU"):
        g = h + a if t == "OU" else h1 + a1 if t == "FHOU" else (h - h1) + (a - a1)
        line = float(p[1])
        return win(g > line if p[2] == "over" else g < line)
    if t == "TT":
        g = h if p[1] == "H" else a
        line = float(p[2])
        return win(g > line if p[3] == "over" else g < line)
    if t == "BTTS":
        both = h > 0 and a > 0
        return win(both if p[1] == "yes" else not both)
    if t in COUNT_TYPES:
        line = float(p[1])
        return win(count > line if p[2] == "over" else count < line)
    raise ValueError(f"Unsupported market for multiples: {key}")


# --------------------------------------------------------------- joint evaluation

@dataclass
class Leg:
    fixture_id: str
    key: str
    odds: float
    label: str = ""
    match: str = ""
    model_probability: float = 0.0
    ev: Optional[float] = None
    confidence: float = 0.0
    risk: str = "Moderate"
    risk_score: float = 30.0
    pick_score: Optional[float] = None
    data_quality: float = 0.0
    status: str = ""
    family: str = ""

    @property
    def ident(self) -> str:
        return f"{self.fixture_id}|{self.key}"


@dataclass
class GroupResult:
    p_all_win: float
    e_return: float
    p_win: dict[str, float]
    pair_win: dict[tuple[str, str], float]
    notes: list[str] = field(default_factory=list)


def _states(analysis: dict, need_half: bool) -> Iterable[tuple[float, int, int, int, int]]:
    m = analysis["prediction"]["score_matrix_full"]
    s = analysis["prediction"].get("first_half_share", 0.45)
    for h, row in enumerate(m):
        for a, p in enumerate(row):
            if p < 1e-10:
                continue
            if not need_half:
                yield p, h, a, 0, 0
                continue
            for h1 in range(h + 1):
                bh = math.comb(h, h1) * s ** h1 * (1 - s) ** (h - h1)
                for a1 in range(a + 1):
                    ba = math.comb(a, a1) * s ** a1 * (1 - s) ** (a - a1)
                    yield p * bh * ba, h, a, h1, a1


def _count_states(mean: float, max_k: int = 40) -> Iterable[tuple[float, int]]:
    for k in range(max_k):
        yield poisson_pmf(k, mean), k


def evaluate_group(analysis: dict, legs: list[Leg]) -> GroupResult:
    """Joint distribution of returns for legs on one fixture."""
    goal_legs = [l for l in legs if market_type(l.key) not in COUNT_TYPES]
    notes: list[str] = []
    sub_results: list[GroupResult] = []
    if goal_legs:
        need_half = any(market_type(l.key) in HALF_TYPES for l in goal_legs)
        sub_results.append(_evaluate(goal_legs, ((p, h, a, h1, a1, 0) for p, h, a, h1, a1 in _states(analysis, need_half))))
        if need_half and len(goal_legs) > 1:
            notes.append("Half-time legs evaluated with goals split between halves at the model's first-half share.")
    for ctype, mean_key in (("CARDS", "cards_mean"), ("CORNERS", "corners_mean")):
        cl = [l for l in legs if market_type(l.key) == ctype]
        if not cl:
            continue
        mean = analysis["prediction"].get(mean_key)
        if mean is None:
            raise ValueError(f"No {ctype.lower()} model for {analysis['fixture_id']}")
        sub_results.append(_evaluate(cl, ((p, 0, 0, 0, 0, k) for p, k in _count_states(mean))))
        if goal_legs:
            notes.append(f"{ctype.capitalize()} legs are assumed independent of the scoreline.")
    out = GroupResult(1.0, 1.0, {}, {}, notes)
    for r in sub_results:
        out.p_all_win *= r.p_all_win
        out.e_return *= r.e_return
        out.p_win.update(r.p_win)
        out.pair_win.update(r.pair_win)
    # cross sub-group pairs are independent by assumption
    ids = [l.ident for l in legs]
    for x, y in combinations(ids, 2):
        if (x, y) not in out.pair_win:
            out.pair_win[(x, y)] = out.p_win[x] * out.p_win[y]
    return out


def _evaluate(legs: list[Leg], states) -> GroupResult:
    n = len(legs)
    pairs = list(combinations(range(n), 2))
    p_all = e_ret = 0.0
    p_win = [0.0] * n
    pair = [0.0] * len(pairs)
    for p, h, a, h1, a1, k in states:
        rets = [leg_return(l.key, l.odds, h, a, h1, a1, k) for l in legs]
        wins = [r == l.odds for r, l in zip(rets, legs)]
        e_ret += p * math.prod(rets)
        if all(wins):
            p_all += p
        for i in range(n):
            if wins[i]:
                p_win[i] += p
        for j, (x, y) in enumerate(pairs):
            if wins[x] and wins[y]:
                pair[j] += p
    return GroupResult(p_all, e_ret, {l.ident: p_win[i] for i, l in enumerate(legs)},
                       {(legs[x].ident, legs[y].ident): pair[j] for j, (x, y) in enumerate(pairs)})


# --------------------------------------------------------------- multiple

@dataclass
class MultipleResult:
    legs: list[Leg]
    combined_odds: float
    probability: float                 # correlation-aware P(every leg wins)
    naive_probability: float           # product of leg probabilities
    ev: float                          # exact under the model, pushes as voids
    fair_odds: float
    correlations: list[dict]
    confidence: float
    risk_score: float
    risk: str
    data_quality: float
    health: float
    notes: list[str]

    def to_dict(self) -> dict:
        return {
            "legs": [vars(l) for l in self.legs],
            "combined_odds": round(self.combined_odds, 3),
            "probability": round(self.probability, 4),
            "naive_probability": round(self.naive_probability, 4),
            "ev": round(self.ev, 4),
            "fair_odds": round(self.fair_odds, 3) if math.isfinite(self.fair_odds) else None,
            "correlations": self.correlations,
            "confidence": round(self.confidence),
            "risk_score": round(self.risk_score, 1),
            "risk": self.risk,
            "data_quality": round(self.data_quality, 1),
            "health": round(self.health),
            "notes": self.notes,
        }


def leg_from_market(analysis: dict, key: str, odds: Optional[float] = None) -> Leg:
    m = next((x for x in analysis["markets"] if x["key"] == key), None)
    if m is None:
        raise KeyError(f"{key} is not priced for {analysis['fixture_id']}")
    o = odds if odds is not None else m["bookmaker_odds"]
    if o is None:
        raise ValueError(f"No bookmaker price for {m['label']}; enter the odds you were offered")
    st = m["settlement"]
    ev = (st["win"] * o + st["half_win"] * (o + 1) / 2 + st["push"] + st["half_loss"] * 0.5 - 1
          if odds is not None or m["ev"] is None else m["ev"])
    return Leg(
        fixture_id=analysis["fixture_id"], key=key, odds=float(o), label=m["label"],
        match=f"{analysis['home']['name']} vs {analysis['away']['name']}",
        model_probability=m["model_probability"], ev=ev, confidence=m["confidence"], risk=m["risk"],
        risk_score=m["risk_score"], pick_score=m["pick_score"], data_quality=analysis["data_quality"]["score"],
        status=m["status"], family=family(key),
    )


def evaluate_multiple(legs: list[Leg], analyses: dict[str, dict]) -> MultipleResult:
    if not legs:
        raise ValueError("A multiple needs at least one leg")
    groups: dict[str, list[Leg]] = {}
    for l in legs:
        groups.setdefault(l.fixture_id, []).append(l)
    prob = e_ret = 1.0
    correlations: list[dict] = []
    notes: list[str] = []
    p_win: dict[str, float] = {}
    for fid, gl in groups.items():
        g = evaluate_group(analyses[fid], gl)
        prob *= g.p_all_win
        e_ret *= g.e_return
        p_win.update(g.p_win)
        notes += g.notes
        for x, y in combinations(gl, 2):
            joint = g.pair_win[(x.ident, y.ident)]
            indep = g.p_win[x.ident] * g.p_win[y.ident]
            lift = joint / indep if indep > 0 else 0.0
            if joint < 1e-9:
                kind = "conflicting"
                text = f"{x.label} and {y.label} cannot both win."
            elif lift >= 1.08:
                kind = "positive"
                text = (f"{x.label} and {y.label} tend to win together: {joint * 100:.0f}% jointly vs "
                        f"{indep * 100:.0f}% if they were independent. The combined probability uses the joint figure.")
            elif lift <= 0.92:
                kind = "negative"
                text = (f"{x.label} and {y.label} work against each other: {joint * 100:.0f}% jointly vs "
                        f"{indep * 100:.0f}% if they were independent.")
            else:
                continue
            correlations.append({"a": x.ident, "b": y.ident, "a_label": x.label, "b_label": y.label,
                                 "joint": round(joint, 4), "independent": round(indep, 4), "lift": round(lift, 3),
                                 "kind": kind, "text": text})
    naive = math.prod(p_win[l.ident] for l in legs)
    combined = math.prod(l.odds for l in legs)
    ev = e_ret - 1
    fair = combined / e_ret if e_ret > 0 else float("inf")
    conf = min(l.confidence for l in legs) - 1.5 * (len(legs) - 1)
    if correlations:
        conf -= 3
    dq = min(l.data_quality for l in legs)
    rs = (1 - prob) * 50 + 2.5 * len(legs) + sum(l.risk_score for l in legs) / len(legs) * 0.35
    rs += max(0.0, (combined - 4) * 1.5)
    res = MultipleResult(legs, combined, prob, naive, ev, fair, correlations, max(conf, 0), rs, risk_category(rs),
                         dq, 0.0, list(dict.fromkeys(notes)))
    res.health = health_score(res)
    return res


def health_score(r: MultipleResult) -> float:
    """0-100. Rewards positive EV, penalises weak legs, correlation, low data
    quality and long-shot structure. Not a probability."""
    ev_part = max(0.0, min(1.0, (r.ev + 0.10) / 0.30)) * 35
    weak = [l for l in r.legs if (l.ev or 0) < 0 or l.confidence < 60]
    leg_part = 25 * (1 - len(weak) / len(r.legs))
    conf_part = max(0.0, min(1.0, (min(l.confidence for l in r.legs) - 40) / 45)) * 15
    corr_pen = sum(5 if c["kind"] != "positive" else 3 for c in r.correlations)
    dq_part = r.data_quality / 100 * 10
    struct = max(0.0, 15 - max(0, len(r.legs) - 3) * 3 - max(0.0, math.log(max(r.combined_odds, 1)) - 1.5) * 4)
    longshot = max(0.0, 0.35 - r.probability) * 40          # rarely-landing slips are not healthy, whatever the EV
    risk_pen = {"High": 5, "Very High": 10}.get(r.risk, 0)
    return max(0.0, min(100.0, ev_part + leg_part + conf_part + dq_part + struct - corr_pen - longshot - risk_pen))


# --------------------------------------------------------------- diagnostics & optimisation

def diagnose(r: MultipleResult) -> dict[str, Any]:
    legs = r.legs
    corr_count: dict[str, int] = {l.ident: 0 for l in legs}
    for c in r.correlations:
        corr_count[c["a"]] += 1
        corr_count[c["b"]] += 1
    weakest = min(legs, key=lambda l: (l.pick_score if l.pick_score is not None else -1))
    return {
        "weakest_leg": weakest.ident,
        "lowest_ev": min(legs, key=lambda l: l.ev if l.ev is not None else -9).ident,
        "highest_risk": max(legs, key=lambda l: l.risk_score).ident,
        "lowest_confidence": min(legs, key=lambda l: l.confidence).ident,
        "most_correlated": max(corr_count, key=corr_count.get) if any(corr_count.values()) else None,
    }


def _leg_problems(l: Leg, r: MultipleResult, min_conf: float) -> list[str]:
    out = []
    if l.ev is not None and l.ev < 0:
        out.append(f"negative expected value ({l.ev * 100:+.1f}%)")
    elif l.ev is not None and l.ev < 0.02:
        out.append(f"almost no value ({l.ev * 100:+.1f}%)")
    if l.confidence < min_conf:
        out.append(f"confidence {l.confidence:.0f} is below {min_conf:.0f}")
    if RISK_ORDER.index(l.risk) >= RISK_ORDER.index("High"):
        out.append(f"{l.risk.lower()} risk")
    for c in r.correlations:
        if l.ident in (c["a"], c["b"]):
            other = c["b_label"] if c["a"] == l.ident else c["a_label"]
            out.append({"conflicting": f"cannot win together with {other}",
                        "negative": f"works against {other}",
                        "positive": f"correlated with {other}"}[c["kind"]])
    if l.status == "avoid":
        out.append("the engine passed on this selection")
    return out


def optimise(legs: list[Leg], analyses: dict[str, dict], min_legs: int = 2, min_conf: float = 60) -> dict[str, Any]:
    """Greedy removal of the leg whose removal most improves health, while
    the improvement is meaningful. Every removal records why."""
    original = evaluate_multiple(legs, analyses)
    current = original
    removed: list[dict] = []
    while len(current.legs) > min_legs:
        best = None
        for l in current.legs:
            trial = evaluate_multiple([x for x in current.legs if x is not l], analyses)
            gain = trial.health - current.health
            problems = _leg_problems(l, current, min_conf)
            # remove only legs with a concrete problem; never cut a clean leg just to shorten the slip
            if problems and (best is None or gain > best[0]):
                best = (gain, l, trial, problems)
        if best is None or best[0] < 1.0:
            break
        gain, l, trial, problems = best
        removed.append({"leg": vars(l), "reasons": problems,
                        "effect": f"Health {current.health:.0f} to {trial.health:.0f}, hit probability "
                                  f"{current.probability * 100:.1f}% to {trial.probability * 100:.1f}%, "
                                  f"EV {current.ev * 100:+.1f}% to {trial.ev * 100:+.1f}%."})
        current = trial
    return {"original": original.to_dict(), "optimised": current.to_dict(), "removed": removed,
            "diagnostics": diagnose(original)}


def suggest_replacements(r: MultipleResult, analyses: dict[str, dict], exclude: set[str],
                         max_suggestions: int = 3) -> list[dict]:
    """Recommended selections not already on the slip, preferring other
    fixtures (no correlation) and similar odds to what was removed."""
    used_fixtures = {l.fixture_id for l in r.legs}
    pool = []
    for a in analyses.values():
        for m in a["markets"]:
            if m["status"] != "recommended" or f"{a['fixture_id']}|{m['key']}" in exclude:
                continue
            pool.append((0 if a["fixture_id"] not in used_fixtures else 1, -(m["pick_score"] or 0), a, m))
    pool.sort(key=lambda x: (x[0], x[1]))
    out, seen = [], set()
    for _, _, a, m in pool:
        if a["fixture_id"] in seen:
            continue
        seen.add(a["fixture_id"])
        out.append(vars(leg_from_market(a, m["key"])))
        if len(out) >= max_suggestions:
            break
    return out


# --------------------------------------------------------------- builder

MODES = {
    #             odds range     max risk     min conf  min EV  utility weights (log p, EV, conf)
    "conservative": ((1.12, 1.70), "Low", 65, 0.02, (1.0, 1.0, 0.4)),
    "balanced": ((1.30, 2.30), "Moderate", 60, 0.04, (0.6, 2.0, 0.3)),
    "value": ((1.50, 3.50), "High", 60, 0.06, (0.3, 3.0, 0.2)),
    "aggressive": ((1.80, 6.00), "High", 58, 0.06, (0.15, 3.5, 0.1)),
}


def _candidates(analyses: dict[str, dict], mode: str, fixtures: Optional[set[str]]) -> list[Leg]:
    (lo, hi), max_risk, min_conf, min_ev, _ = MODES[mode]
    out = []
    for a in analyses.values():
        if fixtures and a["fixture_id"] not in fixtures:
            continue
        for m in a["markets"]:
            o = m["bookmaker_odds"]
            if o is None or not (lo <= o <= hi) or m["ev"] is None or m["ev"] < min_ev:
                continue
            if m["confidence"] < min_conf or RISK_ORDER.index(m["risk"]) > RISK_ORDER.index(max_risk):
                continue
            if m["status"] != "recommended" and mode != "aggressive":
                continue
            out.append(leg_from_market(a, m["key"]))
    return out


def _utility(l: Leg, mode: str) -> float:
    wp, we, wc = MODES[mode][4]
    return wp * math.log(max(l.model_probability, 1e-6)) + we * (l.ev or 0) + wc * l.confidence / 100


def build_multiple(analyses: dict[str, dict], legs: int = 3, mode: str = "balanced",
                   fixtures: Optional[Iterable[str]] = None, allow_same_match: bool = False) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError(f"Mode must be one of {list(MODES)}")
    fx = set(fixtures) if fixtures else None
    cands = sorted(_candidates(analyses, mode, fx), key=lambda l: _utility(l, mode), reverse=True)
    chosen: list[Leg] = []
    for l in cands:
        if len(chosen) == legs:
            break
        if not allow_same_match and any(c.fixture_id == l.fixture_id for c in chosen):
            continue
        if allow_same_match and any(c.fixture_id == l.fixture_id and family(c.key) == family(l.key) for c in chosen):
            continue
        chosen.append(l)
    if len(chosen) < legs:
        return {"mode": mode, "requested_legs": legs, "built": False,
                "reason": f"Only {len(chosen)} selection{'s' if len(chosen) != 1 else ''} meet the {mode} criteria "
                          f"(odds {MODES[mode][0][0]:.2f}-{MODES[mode][0][1]:.2f}, risk at most {MODES[mode][1]}, "
                          f"confidence {MODES[mode][2]}+, EV {MODES[mode][3] * 100:+.0f}%+). "
                          "A shorter multiple or no multiple is the better decision today.",
                "candidates": [vars(c) for c in chosen]}
    # local search: swap legs to improve a mode-weighted objective on the exact joint result
    best = evaluate_multiple(chosen, analyses)

    def objective(r: MultipleResult) -> float:
        wp, we, wc = MODES[mode][4]
        return wp * math.log(max(r.probability, 1e-6)) + we * r.ev + wc * r.confidence / 100

    improved = True
    while improved:
        improved = False
        for i in range(len(chosen)):
            for c in cands:
                if c in chosen or (not allow_same_match and any(x.fixture_id == c.fixture_id for j, x in enumerate(chosen) if j != i)):
                    continue
                trial_legs = chosen[:i] + [c] + chosen[i + 1:]
                trial = evaluate_multiple(trial_legs, analyses)
                if objective(trial) > objective(best) + 1e-9:
                    chosen, best, improved = trial_legs, trial, True
                    break
            if improved:
                break
    return {"mode": mode, "requested_legs": legs, "built": True, "multiple": best.to_dict()}


ALTERNATIVES = [
    ("Safest 3-leg", "conservative", 3),
    ("Best value 3-leg", "value", 3),
    ("Safest 5-leg", "conservative", 5),
    ("Best balanced multiple", "balanced", 4),
    ("High-risk multiple", "aggressive", 4),
]


def alternatives(analyses: dict[str, dict]) -> list[dict]:
    out = []
    for name, mode, n in ALTERNATIVES:
        r = build_multiple(analyses, n, mode)
        out.append({"name": name, **r})
    return out


# --------------------------------------------------------------- slip analyser

def analyse_slip(entries: list[dict], analyses: dict[str, dict], min_conf: float = 60) -> dict[str, Any]:
    """entries: [{fixture_id, market_key, odds?}]. Returns per-leg analysis,
    slip health, the main problem, and a fix (removals + replacements)."""
    legs = [leg_from_market(analyses[e["fixture_id"]], e["market_key"], e.get("odds")) for e in entries]
    r = evaluate_multiple(legs, analyses)
    per_leg = []
    for l in legs:
        per_leg.append({**vars(l), "fair_odds": round(1 / l.model_probability, 3) if l.model_probability else None,
                        "problems": _leg_problems(l, r, min_conf)})
    weak = [p for p in per_leg if p["problems"]]
    if not legs:
        main = "The slip is empty."
    elif r.correlations and any(c["kind"] == "conflicting" for c in r.correlations):
        main = "Two selections cannot both win, so the slip cannot pay out."
    elif sum(1 for p in per_leg if (p["ev"] or 0) < 0) >= 2:
        main = f"{sum(1 for p in per_leg if (p['ev'] or 0) < 0)} selections with negative expected value are destroying the slip's value."
    elif r.ev < 0:
        worst = min(per_leg, key=lambda p: p["ev"] if p["ev"] is not None else 0)
        main = f"The slip is negative EV; the weakest leg is {worst['label']} ({(worst['ev'] or 0) * 100:+.1f}%)."
    elif any(c["kind"] == "negative" for c in r.correlations):
        main = "Some selections work against each other, which lowers the chance the slip lands."
    elif r.correlations:
        main = ("Correlated selections: some legs tend to win together, so the slip is less diversified than it looks. "
                "Bookmakers often reprice or refuse same-match combinations.")
    elif weak:
        main = f"{len(weak)} leg{'s' if len(weak) > 1 else ''} below the quality bar."
    else:
        main = "No structural problems. Remember every extra leg compounds the house margin."
    fix = optimise(legs, analyses, min_legs=1, min_conf=min_conf) if len(legs) > 1 else None
    replacements = []
    if fix and fix["removed"]:
        opt_legs = [Leg(**l) for l in fix["optimised"]["legs"]]
        exclude = {l.ident for l in legs}
        replacements = suggest_replacements(evaluate_multiple(opt_legs, analyses), analyses, exclude,
                                            max_suggestions=len(fix["removed"]))
    return {"slip": r.to_dict(), "legs": per_leg, "health": round(r.health), "main_problem": main,
            "fix": fix, "replacements": replacements}
