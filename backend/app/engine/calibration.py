"""Phase 6: calibration and model monitoring.

A 70% prediction should win about 70% of the time. Buckets follow the spec
(50-55 ... 80+), with a <50 bucket added so nothing is silently dropped.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable, Optional

BUCKETS = [(0.0, 0.50, "<50%"), (0.50, 0.55, "50-55%"), (0.55, 0.60, "55-60%"), (0.60, 0.65, "60-65%"),
           (0.65, 0.70, "65-70%"), (0.70, 0.75, "70-75%"), (0.75, 0.80, "75-80%"), (0.80, 1.01, "80%+")]


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def calibration_table(preds: Iterable[tuple[float, float]], min_n: int = 30,
                      tolerance: float = 0.06) -> dict:
    """preds: (predicted probability, outcome) with outcome 1/0 (0.5 allowed
    for half results). A bucket is flagged when it has min_n predictions and
    its actual rate falls outside both the tolerance and the 95% interval."""
    groups: dict[str, list[tuple[float, float]]] = defaultdict(list)
    all_p = []
    for p, y in preds:
        all_p.append((p, y))
        for lo, hi, name in BUCKETS:
            if lo <= p < hi:
                groups[name].append((p, y))
                break
    rows = []
    ece = 0.0
    n_total = len(all_p)
    for lo, hi, name in BUCKETS:
        g = groups.get(name, [])
        n = len(g)
        if n == 0:
            rows.append({"bucket": name, "n": 0, "predicted": None, "actual": None, "gap": None, "flag": None,
                         "ci_low": None, "ci_high": None})
            continue
        pred = sum(p for p, _ in g) / n
        act = sum(y for _, y in g) / n
        lo_ci, hi_ci = _wilson(round(act * n), n)
        gap = act - pred
        flag = None
        if n >= min_n and abs(gap) > tolerance and not (lo_ci <= pred <= hi_ci):
            flag = "overconfident" if gap < 0 else "underconfident"
        rows.append({"bucket": name, "n": n, "predicted": round(pred, 4), "actual": round(act, 4),
                     "gap": round(gap, 4), "flag": flag, "ci_low": round(lo_ci, 4), "ci_high": round(hi_ci, 4)})
        ece += n / n_total * abs(gap) if n_total else 0
    brier = sum((p - y) ** 2 for p, y in all_p) / n_total if n_total else None
    ll = -sum(y * math.log(max(p, 1e-9)) + (1 - y) * math.log(max(1 - p, 1e-9)) for p, y in all_p) / n_total if n_total else None
    flagged = [r for r in rows if r["flag"]]
    return {
        "rows": rows, "n": n_total, "ece": round(ece, 4) if n_total else None,
        "brier": round(brier, 4) if brier is not None else None, "log_loss": round(ll, 4) if ll is not None else None,
        "degraded": bool(flagged),
        "message": (f"{len(flagged)} bucket{'s' if len(flagged) > 1 else ''} miscalibrated: " +
                    "; ".join(f"{r['bucket']} predicted {r['predicted'] * 100:.0f}% but won {r['actual'] * 100:.0f}% "
                              f"(n={r['n']})" for r in flagged)) if flagged else
                   ("Calibration within tolerance." if n_total >= min_n else
                    f"Only {n_total} settled predictions; calibration is not yet meaningful."),
    }


def multiclass_scores(rows: Iterable[tuple[tuple[float, float, float], int]]) -> dict:
    """Brier and log loss for 1X2 forecasts. outcome index 0/1/2."""
    rows = list(rows)
    if not rows:
        return {"n": 0}
    brier = ll = 0.0
    for probs, y in rows:
        brier += sum((p - (1 if i == y else 0)) ** 2 for i, p in enumerate(probs))
        ll -= math.log(max(probs[y], 1e-9))
    return {"n": len(rows), "brier": round(brier / len(rows), 4), "log_loss": round(ll / len(rows), 4)}


def calibration_factors(by_family: dict[str, dict], floor: float = 0.70) -> dict[str, float]:
    """Turn per-family calibration into the confidence factor the engine
    uses: 1.0 when ECE is ~0, falling with miscalibration, never below floor.
    Families without enough data are omitted so the neutral prior applies."""
    out = {}
    for fam, t in by_family.items():
        if t.get("n", 0) >= 100 and t.get("ece") is not None:
            out[fam] = round(max(floor, 1 - 2.5 * t["ece"]), 3)
    return out


def drift(recent: dict, baseline: dict, threshold: float = 0.02) -> Optional[str]:
    """Flag when recent log loss is worse than the baseline window."""
    if not recent.get("log_loss") or not baseline.get("log_loss"):
        return None
    d = recent["log_loss"] - baseline["log_loss"]
    return (f"Log loss worsened by {d:.3f} in the recent window ({recent['log_loss']:.3f} vs "
            f"{baseline['log_loss']:.3f}). Review inputs before trusting new picks.") if d > threshold else None
