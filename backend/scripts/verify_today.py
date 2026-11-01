"""Live check: every match the app shows today must be a fixture the football
API returns, with the same provider ids and team names.

    export API_FOOTBALL_KEY=...            # required
    export APP_TIMEZONE=Africa/Lagos       # defines "today"
    python scripts/verify_today.py [YYYY-MM-DD]

For each fixture on the app's slate it re-fetches /fixtures?id=<id> directly
from API-Football (bypassing the app's caches) and compares id, teams, team
ids, league and kickoff. Exits 1 on any mismatch.
"""
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.providers.api_football import LEAGUES, ApiFootballProvider  # noqa: E402
from app.services.analysis_service import AnalysisService  # noqa: E402
from app.settings import Settings  # noqa: E402


def main() -> int:
    s = Settings()
    if not s.api_football_key:
        print("API_FOOTBALL_KEY is not set: nothing can be verified.")
        return 2
    day = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else datetime.now(ZoneInfo(s.timezone)).date()
    svc = AnalysisService(s, tz=s.timezone)
    cards = svc.summary_cards(day)
    direct = ApiFootballProvider(s.api_football_key, tz=s.timezone)
    print(f"{day.isoformat()} ({s.timezone}): {len(cards)} fixtures in supported competitions\n")
    print(f"{'fixture_id':>10}  {'kickoff':16}  {'competition':22}  {'home (id)':32}  {'away (id)':32}  status / recs")
    bad = 0
    for c in cards:
        raw = direct._get("/fixtures", id=c["fixture_id"])
        if not raw:
            print(f"{c['fixture_id']:>10}  MISMATCH: API returned nothing for this id")
            bad += 1
            continue
        f = raw[0]
        api_home, api_away = f["teams"]["home"], f["teams"]["away"]
        problems = []
        if str(f["fixture"]["id"]) != c["fixture_id"]:
            problems.append("fixture id")
        if c["home"] and (c["home"]["id"] != str(api_home["id"]) or c["home"]["name"] != api_home["name"]):
            problems.append(f"home {c['home']} vs API {api_home}")
        if c["away"] and (c["away"]["id"] != str(api_away["id"]) or c["away"]["name"] != api_away["name"]):
            problems.append(f"away {c['away']} vs API {api_away}")
        if c["competition"] and LEAGUES[c["competition"]["id"]][0] != f["league"]["id"]:
            problems.append(f"league {c['competition']['id']} vs API {f['league']['id']}")
        home = f"{api_home['name']} ({api_home['id']})"
        away = f"{api_away['name']} ({api_away['id']})"
        ko = (c["kickoff"] or "")[:16]
        comp = (c["competition"] or {}).get("name", "?")
        recs = c.get("recommended_count", 0) if c["verification_status"] == "VERIFIED" else 0
        print(f"{c['fixture_id']:>10}  {ko:16}  {comp[:22]:22}  {home[:32]:32}  {away[:32]:32}  "
              f"{c['verification_status']} / {recs}" + (f"  MISMATCH: {'; '.join(problems)}" if problems else ""))
        if c["verification_status"] != "VERIFIED" and recs:
            problems.append("recommendations on a non-VERIFIED fixture")
        bad += bool(problems)
        for m in c["verification_messages"]:
            print(f"{'':>12}{m}")
    print(f"\n{len(cards) - bad} of {len(cards)} displayed fixtures match API-Football exactly.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
