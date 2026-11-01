"""Arithmetic integrity checks. Every number shown for a market is recomputed
from its inputs; if a displayed value disagrees with the recomputation, the
recommendation is rejected and an error is logged.

    implied probability = 1 / decimal odds
    fair odds           = 1 / model probability       (binary markets)
    EV                  = model probability x decimal odds - 1
    EV (push/half)      = win*o + half_win*(o+1)/2 + push + half_loss/2 - 1
    edge (pp)           = (model probability - no-vig probability) x 100
    combined odds       = product of leg odds
"""
from __future__ import annotations

import logging
import math
from typing import Optional

log = logging.getLogger("edgeboard.integrity")

# Values are rounded for display (4 dp probabilities, 3 dp odds), so allow that much.
TOL_EV = 0.0025
TOL_PROB = 0.0006
TOL_ODDS_REL = 0.002
TOL_EDGE_PP = 0.11


def expected_value(p: float, odds: float) -> float:
    return p * odds - 1


def check_market(m: dict) -> list[str]:
    errs: list[str] = []
    p, o, st = m.get("model_probability"), m.get("bookmaker_odds"), m.get("settlement") or {}
    if p is None or not (0 < p < 1):
        return [f"model probability {p!r} outside (0, 1)"]
    binary = not (st.get("push") or st.get("half_win") or st.get("half_loss"))
    if m.get("fair_odds") is not None:
        # fair odds = 1 / model probability (for push/half markets the model
        # probability is the binary equivalent, i.e. also 1 / fair odds).
        # Compared in probability space so 4-dp rounding of p is tolerated.
        if m["fair_odds"] <= 1.0 or abs(1 / m["fair_odds"] - p) > TOL_PROB:
            errs.append(f"fair odds {m['fair_odds']} != 1/{p}")
        if not binary:
            denom = st["win"] + st["half_win"] / 2
            want = (1 - st["half_win"] / 2 - st["push"] - st["half_loss"] / 2) / denom if denom > 0 else math.inf
            if math.isfinite(want) and abs(1 / want - 1 / m["fair_odds"]) > TOL_PROB:
                errs.append(f"fair odds {m['fair_odds']} != settlement fair odds {want:.3f}")
    if o is None:
        if m.get("ev") is not None:
            errs.append("EV shown without bookmaker odds")
        return errs
    if o <= 1.0:
        errs.append(f"bookmaker odds {o} must be above 1.00")
        return errs
    if m.get("implied_probability") is not None and abs(m["implied_probability"] - 1 / o) > TOL_PROB:
        errs.append(f"implied probability {m['implied_probability']} != 1/{o}")
    if m.get("ev") is None:
        errs.append("EV missing for a priced market")
    else:
        want_ev = expected_value(p, o) if binary else (
            st["win"] * o + st["half_win"] * (o + 1) / 2 + st["push"] + st["half_loss"] * 0.5 - 1)
        if abs(m["ev"] - want_ev) > TOL_EV:
            errs.append(f"EV {m['ev']:+.4f} != {want_ev:+.4f} recomputed")
    if m.get("edge_pp") is not None and m.get("no_vig_probability") is not None:
        want_edge = (p - m["no_vig_probability"]) * 100
        if abs(m["edge_pp"] - want_edge) > TOL_EDGE_PP:
            errs.append(f"edge {m['edge_pp']} pp != {want_edge:.2f} pp")
    return errs


def enforce(markets: list[dict], fixture_id: str) -> int:
    """Reject any recommendation whose numbers do not recompute. Returns the
    number of markets rejected."""
    bad = 0
    for m in markets:
        errs = check_market(m)
        if errs:
            log.error("integrity check failed fixture=%s market=%s: %s", fixture_id, m.get("key"), "; ".join(errs))
            if m.get("status") == "recommended":
                bad += 1
            m["status"] = "avoid"
            m["label_tag"] = "Avoid"
            m["integrity_errors"] = errs
            m["reasons_rejected"] = ["Integrity check failed: " + "; ".join(errs)] + list(m.get("reasons_rejected") or [])
    return bad


def check_combined_odds(leg_odds: list[float], combined: float) -> Optional[str]:
    want = math.prod(leg_odds)
    if abs(combined - want) > want * TOL_ODDS_REL:
        return f"combined odds {combined} != product {want:.3f}"
    return None
