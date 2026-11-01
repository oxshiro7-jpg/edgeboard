"""Run the app's full pipeline on a snapshot from fetch_snapshot.py (no network).

    python scripts/run_snapshot.py snapshot-2026-10-05.json [--json out.json]

Prints every fixture API-Football listed for the date with its verification
status and, for verified fixtures, the model output and any recommendations.
Then checks that every displayed fixture is one the API listed, with the same
id and team names. Exits 1 on any mismatch.
"""
import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("snapshot")
    ap.add_argument("--json", help="write the full analyses here")
    a = ap.parse_args()
    os.environ["API_FOOTBALL_SNAPSHOT"] = a.snapshot
    snap = json.load(open(a.snapshot, encoding="utf-8"))
    from app.services.analysis_service import AnalysisService
    from app.settings import Settings

    day = date.fromisoformat(snap["date"])
    svc = AnalysisService(Settings(), tz=snap["tz"])
    slate = svc.slate(day)

    # The raw date listing exactly as API-Football returned it.
    raw = next(v for k, v in snap["responses"].items() if k.startswith("/fixtures?date="))
    api = {str(f["fixture"]["id"]): f for f in raw["response"]}

    print(f"{day} ({snap['tz']}) — snapshot recorded {snap['recorded_at'][:16]} UTC from API-Football\n")
    bad = 0
    for e in slate:
        f = api.get(e["fixture_id"])
        shown = e["verification"]["fixture"] or e["verification"].get("listed") or {}
        home = (shown.get("home") or {}).get("name") or shown.get("home_name")
        away = (shown.get("away") or {}).get("name") or shown.get("away_name")
        ok = f is not None and home == f["teams"]["home"]["name"] and away == f["teams"]["away"]["name"]
        bad += not ok
        print(f"[{e['status']}] {e['fixture_id']}  {home} vs {away}"
              + ("" if ok else "   <-- MISMATCH with API listing"))
        for m in e["messages"]:
            print(f"      {m}")
        an = e["analysis"]
        if not an:
            continue
        pr = an["prediction"]["probabilities"]
        print(f"      model 1X2: home {pr['home']:.1%}  draw {pr['draw']:.1%}  away {pr['away']:.1%}"
              f"   xG {an['prediction']['expected_goals']['home']:.2f}-{an['prediction']['expected_goals']['away']:.2f}")
        recs = [m for m in an["markets"] if m["status"] == "recommended"]
        if not recs:
            print(f"      no bet: {'; '.join(an['no_bet'].get('reasons', [])[:3]) or 'no selection passed every gate'}")
        for m in sorted(recs, key=lambda m: -(m.get("pick_score") or 0))[:3]:
            print(f"      PICK {m['label']} @ {m['bookmaker_odds']}  p={m['model_probability']:.1%}"
                  f"  EV {m['ev']:+.1%}  confidence {m.get('confidence')}  risk {m.get('risk')}")
    print(f"\n{len(slate) - bad} of {len(slate)} displayed fixtures match API-Football's listing exactly.")
    if a.json:
        json.dump(slate, open(a.json, "w", encoding="utf-8"), default=str, indent=1)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
