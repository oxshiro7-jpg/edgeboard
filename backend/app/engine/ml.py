"""Phase 8: machine-learning component and champion/challenger promotion.

The ML component is a multinomial logistic regression stacked on the other
components' outputs plus context features (Elo gap, expected goals, data
quality, model disagreement). It is pure Python so it trains anywhere; a
gradient-boosting learner (XGBoost / LightGBM) can replace it through the
same interface once the history is large enough to justify one.

Nothing is promoted automatically on in-sample fit: a challenger enters the
ensemble only if it beats the current ensemble on a later, untouched test
window by a minimum log-loss margin without worsening calibration.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from .calibration import calibration_table, multiclass_scores

COMPONENTS = ["xg_poisson", "dixon_coles", "form", "elo", "market"]


def _logit_pair(h: float, d: float, a: float) -> tuple[float, float]:
    d = max(d, 1e-4)
    return math.log(max(h, 1e-4) / d), math.log(max(a, 1e-4) / d)


def features(comp_probs: dict[str, Sequence[float]], elo_diff: float, total_xg: float, dq: float,
             disagreement: float) -> list[float]:
    x: list[float] = []
    for name in COMPONENTS:
        if name in comp_probs:
            x += [*_logit_pair(*comp_probs[name]), 1.0]
        else:
            x += [0.0, 0.0, 0.0]
    x += [elo_diff / 400.0, total_xg, dq / 100.0, disagreement]
    return x


FEATURE_NAMES = [f"{c}_{k}" for c in COMPONENTS for k in ("home_logit", "away_logit", "present")] + \
    ["elo_diff_400", "total_xg", "data_quality", "disagreement"]


def features_from_analysis(a: dict) -> list[float]:
    comps = {c["name"]: (c["home"], c["draw"], c["away"]) for c in a["prediction"]["components"]}
    eh, ea = a["home"]["elo"], a["away"]["elo"]
    return features(comps, (eh - ea) if eh is not None and ea is not None else 0.0, a["prediction"]["expected_goals"]["total"],
                    a["data_quality"]["score"], a["prediction"]["model_disagreement"])


def outcome_index(result: dict) -> int:
    h, a = result["home_goals"], result["away_goals"]
    return 0 if h > a else 1 if h == a else 2


@dataclass
class SoftmaxRegression:
    l2: float = 0.05
    lr: float = 0.1
    epochs: int = 600
    W: list[list[float]] = field(default_factory=list)      # 3 x (n+1)
    mean: list[float] = field(default_factory=list)
    std: list[float] = field(default_factory=list)
    version: str = "ml-logit-0"

    def _z(self, x: Sequence[float]) -> list[float]:
        return [(v - m) / s for v, m, s in zip(x, self.mean, self.std)] + [1.0]

    def fit(self, X: list[list[float]], y: list[int]) -> "SoftmaxRegression":
        if len(X) < 30:
            raise ValueError("At least 30 matches are needed to train the ML component")
        n = len(X[0])
        self.mean = [sum(r[j] for r in X) / len(X) for j in range(n)]
        self.std = [max(1e-6, math.sqrt(sum((r[j] - self.mean[j]) ** 2 for r in X) / len(X))) for j in range(n)]
        Z = [self._z(r) for r in X]
        self.W = [[0.0] * (n + 1) for _ in range(3)]
        m = len(Z)
        for _ in range(self.epochs):
            grad = [[0.0] * (n + 1) for _ in range(3)]
            for z, t in zip(Z, y):
                p = self._softmax(z)
                for k in range(3):
                    g = p[k] - (1.0 if k == t else 0.0)
                    gk = grad[k]
                    for j, v in enumerate(z):
                        gk[j] += g * v
            for k in range(3):
                for j in range(n + 1):
                    reg = self.l2 * self.W[k][j] if j < n else 0.0
                    self.W[k][j] -= self.lr * (grad[k][j] / m + reg)
        return self

    def _softmax(self, z: list[float]) -> list[float]:
        s = [sum(w * v for w, v in zip(row, z)) for row in self.W]
        mx = max(s)
        e = [math.exp(v - mx) for v in s]
        t = sum(e)
        return [v / t for v in e]

    def predict_proba(self, x: Sequence[float]) -> tuple[float, float, float]:
        p = self._softmax(self._z(x))
        return p[0], p[1], p[2]

    def to_dict(self) -> dict:
        return {"type": "softmax", "version": self.version, "l2": self.l2, "W": self.W, "mean": self.mean,
                "std": self.std, "features": FEATURE_NAMES}

    @classmethod
    def from_dict(cls, d: dict) -> "SoftmaxRegression":
        return cls(l2=d["l2"], W=d["W"], mean=d["mean"], std=d["std"], version=d["version"])

    def top_weights(self, k: int = 6) -> list[dict]:
        rows = []
        for j, name in enumerate(FEATURE_NAMES):
            rows.append({"feature": name, "home": round(self.W[0][j], 3), "draw": round(self.W[1][j], 3),
                         "away": round(self.W[2][j], 3)})
        return sorted(rows, key=lambda r: -max(abs(r["home"]), abs(r["away"]), abs(r["draw"])))[:k]


def _blend(p: Sequence[float], q: Sequence[float], w: float) -> tuple[float, float, float]:
    b = [(1 - w) * a + w * c for a, c in zip(p, q)]
    s = sum(b)
    return b[0] / s, b[1] / s, b[2] / s


def train_and_evaluate(cases: list, splits=(0.5, 0.2), min_improvement: float = 0.003,
                       version: str = "ml-logit-1") -> dict[str, Any]:
    """Chronological train / validation / test. The blend weight is chosen on
    validation; promotion is decided on test only."""
    cases = sorted(cases, key=lambda c: c.analysis["kickoff"])
    n = len(cases)
    n_tr, n_va = int(n * splits[0]), int(n * splits[1])
    tr, va, te = cases[:n_tr], cases[n_tr:n_tr + n_va], cases[n_tr + n_va:]
    if len(tr) < 30 or len(va) < 10 or len(te) < 10:
        return {"status": "insufficient_data", "n": n,
                "message": f"{n} settled matches; need at least 60 for a train/validation/test split."}
    X = [features_from_analysis(c.analysis) for c in tr]
    y = [outcome_index(c.result) for c in tr]
    model = SoftmaxRegression(version=version).fit(X, y)

    def scores(subset, w):
        rows, bins = [], []
        for c in subset:
            e = c.analysis["prediction"]["probabilities"]
            ens = (e["home"], e["draw"], e["away"])
            p = _blend(ens, model.predict_proba(features_from_analysis(c.analysis)), w) if w > 0 else ens
            t = outcome_index(c.result)
            rows.append((p, t))
            for k in range(3):
                bins.append((p[k], 1.0 if k == t else 0.0))
        return multiclass_scores(rows), calibration_table(bins)

    grid = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.75, 1.0]
    val = {w: scores(va, w)[0]["log_loss"] for w in grid}
    best_w = min(grid, key=lambda w: val[w])
    base_te, base_cal = scores(te, 0.0)
    cand_te, cand_cal = scores(te, best_w)
    improvement = base_te["log_loss"] - cand_te["log_loss"]
    cal_ok = (cand_cal["ece"] or 0) <= (base_cal["ece"] or 0) + 0.01
    promote = best_w > 0 and improvement >= min_improvement and cal_ok
    market_rows = []
    for c in te:
        nv = {m["key"]: m["no_vig_probability"] for m in c.analysis["markets"] if m["key"].startswith("1X2:")}
        if all(nv.get(k) for k in ("1X2:H", "1X2:D", "1X2:A")):
            market_rows.append(((nv["1X2:H"], nv["1X2:D"], nv["1X2:A"]), outcome_index(c.result)))
    reason = ("Promoted: lower test log loss without worse calibration." if promote else
              "Not promoted: " + ("validation preferred no ML weight." if best_w == 0 else
                                  f"test log loss improved by only {improvement:.4f} (need {min_improvement})." if improvement < min_improvement
                                  else "calibration got worse on the test window."))
    return {
        "status": "promoted" if promote else "rejected",
        "message": reason,
        "model": model.to_dict(),
        "recommended_weight": best_w if promote else 0.0,
        "validation_log_loss_by_weight": {str(k): v for k, v in val.items()},
        "test": {"ensemble": base_te, "ensemble_plus_ml": cand_te, "market": multiclass_scores(market_rows),
                 "ensemble_ece": base_cal["ece"], "ensemble_plus_ml_ece": cand_cal["ece"]},
        "split": {"train": len(tr), "validation": len(va), "test": len(te)},
        "top_weights": model.top_weights(),
    }


def try_gradient_boosting() -> Optional[str]:
    """Returns the available GBM library name, if any. Wire it in as a
    challenger once there are several thousand settled matches."""
    for lib in ("lightgbm", "xgboost"):
        try:
            __import__(lib)
            return lib
        except ImportError:
            continue
    return None


def dumps(model: SoftmaxRegression) -> str:
    return json.dumps(model.to_dict())
