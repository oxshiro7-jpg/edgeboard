"""Alerts (spec section 36) derived by diffing two analysis snapshots of the
same fixture. The recalculation worker calls `diff_alerts(prev, new)` after
each run and forwards the result to the user's enabled channels."""
from __future__ import annotations

from typing import Optional

ALERT_TYPES = ["new_strong_pick", "odds_improved", "odds_below_value", "lineups_confirmed", "player_ruled_out",
               "prediction_changed", "market_moved"]


def required_odds(m: dict, min_ev: float) -> Optional[float]:
    """Lowest price at which the selection still clears the EV threshold."""
    if not m.get("fair_odds"):
        return None
    return round(m["fair_odds"] * (1 + min_ev), 2)


def diff_alerts(prev: Optional[dict], new: dict, min_ev: float = 0.04, prob_shift: float = 0.03) -> list[dict]:
    out: list[dict] = []
    match = f"{new['home']['name']} vs {new['away']['name']}"

    def add(kind: str, text: str, **extra):
        out.append({"type": kind, "fixture_id": new["fixture_id"], "match": match, "text": text, **extra})

    pm = {m["key"]: m for m in (prev or {}).get("markets", [])}
    for m in new["markets"]:
        old = pm.get(m["key"])
        strong = m["status"] == "recommended" and m["label_tag"] in ("Strong Pick", "Highest Confidence")
        if strong and (not old or old.get("label_tag") not in ("Strong Pick", "Highest Confidence")):
            add("new_strong_pick", f"New {m['label_tag'].lower()}: {m['label']} at {m['bookmaker_odds']:.2f}.", market_key=m["key"])
        if old and old.get("bookmaker_odds") and m.get("bookmaker_odds"):
            o0, o1 = old["bookmaker_odds"], m["bookmaker_odds"]
            if m["status"] == "recommended" and o1 > o0 * 1.02:
                add("odds_improved", f"{m['label']} improved from {o0:.2f} to {o1:.2f}.", market_key=m["key"])
            req = required_odds(m, min_ev)
            if old.get("status") == "recommended" and req and o1 < req <= o0:
                add("odds_below_value", f"{m['label']} dropped to {o1:.2f}, below the {req:.2f} needed for value.",
                    market_key=m["key"])
    if prev:
        for side in ("home", "away"):
            if prev[side]["lineup"]["status"] != "confirmed" and new[side]["lineup"]["status"] == "confirmed":
                add("lineups_confirmed", f"{new[side]['name']} lineup confirmed; prediction recalculated.")
            before = {a["player"] for a in prev[side]["absences"] if a["status"] == "out"}
            for a in new[side]["absences"]:
                if a["status"] == "out" and a["player"] not in before:
                    add("player_ruled_out", f"{a['player']} ({new[side]['name']}, {a['position']}) ruled out ({a['reason']}).")
        p0, p1 = prev["prediction"]["probabilities"], new["prediction"]["probabilities"]
        if max(abs(p1[k] - p0[k]) for k in ("home", "draw", "away")) >= prob_shift:
            add("prediction_changed",
                f"Prediction updated. Before: {new['home']['short_name']} {p0['home'] * 100:.0f}% / draw {p0['draw'] * 100:.0f}% / "
                f"{new['away']['short_name']} {p0['away'] * 100:.0f}%. After: {p1['home'] * 100:.0f}% / {p1['draw'] * 100:.0f}% / "
                f"{p1['away'] * 100:.0f}%.", before=p0, after=p1)
    for mv in new.get("movement", [])[:5]:
        if "significant" in mv["flags"]:
            add("market_moved", f"{mv['market_key']} moved {mv['opening']:.2f} to {mv['current']:.2f} "
                                f"({mv['implied_shift_pp']:+.1f} pp). Movement is not assumed to be informed money.",
                market_key=mv["market_key"])
    return out
