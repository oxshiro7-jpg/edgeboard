"""Download real API-Football data for one date into a single JSON file.

Needs only Python 3.9+ (no pip installs). Run from the `backend` folder:

    python scripts/fetch_snapshot.py --key YOUR_API_FOOTBALL_KEY
    python scripts/fetch_snapshot.py --key YOUR_KEY --date 2026-10-05 --max-fixtures 3

It lists every fixture API-Football returns for the date in the supported
competitions, then fetches teams, recent results with statistics, injuries,
lineups and odds for up to --max-fixtures upcoming ones, running the app's
own verification on each. Everything API-Football returned is saved,
unmodified, to snapshot-<date>.json. Nothing in the file is generated.

Calls: roughly 2 x (--history + 1) + 8 per fixture, plus 1 for the date list.
The free plan allows 100 calls a day, so the defaults (3 fixtures, 10 past
matches per team) use about 90.
"""
import argparse
import os
import sys
import urllib.error
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine.config import EngineConfig  # noqa: E402
from app.providers.api_football import ApiFootballOdds, ApiFootballProvider  # noqa: E402
from app.providers.registry import Providers  # noqa: E402
from app.providers.snapshot import RecordingTransport, SnapshotMiss  # noqa: E402
from app.services.prediction import PredictionService  # noqa: E402
from app.settings import Settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", default=os.getenv("API_FOOTBALL_KEY"), help="API-Football key (or set API_FOOTBALL_KEY)")
    ap.add_argument("--date", help="YYYY-MM-DD (default: today in --tz)")
    ap.add_argument("--tz", default="Africa/Lagos")
    ap.add_argument("--max-fixtures", type=int, default=3)
    ap.add_argument("--history", type=int, default=10, help="past matches per team (each costs 1 call)")
    ap.add_argument("--fixture", action="append", default=[], help="analyse these fixture ids (repeatable)")
    ap.add_argument("--max-calls", type=int, default=100)
    ap.add_argument("--out")
    a = ap.parse_args()
    if not a.key:
        print("Give your key with --key or set API_FOOTBALL_KEY.")
        return 2
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(a.tz)
    except Exception:
        print(f"Timezone {a.tz!r} not found. On Windows run:  pip install tzdata   then try again.")
        return 2
    day = date.fromisoformat(a.date) if a.date else datetime.now(ZoneInfo(a.tz)).date()

    rec = RecordingTransport(a.key, max_calls=a.max_calls)
    fp = ApiFootballProvider(a.key, tz=a.tz, http_get=rec, history_limit=a.history)
    providers = Providers(fp, ApiFootballOdds(fp), fp.injury_provider(), fp.lineup_provider())
    wl = list(Settings().competitions)
    svc = PredictionService(lambda d: providers, lambda: wl, EngineConfig, tz=a.tz)

    try:
        refs = fp.fixtures(day, wl)
    except urllib.error.HTTPError as e:
        print(f"API-Football refused the request: HTTP {e.code}. Check the key.")
        return 1
    except urllib.error.URLError as e:
        print(f"Could not reach API-Football: {e.reason}. Check your internet connection.")
        return 1
    except RuntimeError as e:          # API-level error, e.g. plan does not cover this season
        print(e)
        return 1
    print(f"{day} ({a.tz}): API-Football lists {len(refs)} fixtures in supported competitions")
    for r in refs:
        ko = r.kickoff.strftime("%H:%M") if r.kickoff else "--:--"
        print(f"  {r.id:>9}  {ko}  {r.competition_id:11} {r.home_name} vs {r.away_name}  [{r.status}]")

    if a.fixture:
        chosen = [r for r in refs if r.id in a.fixture]
        missing = set(a.fixture) - {r.id for r in chosen}
        if missing:
            print(f"Not in API-Football's list for {day}: {', '.join(sorted(missing))}")
    else:
        chosen = [r for r in refs if r.status == "scheduled"][: a.max_fixtures]
    if not chosen:
        print("No upcoming fixtures to analyse for this date. Try tomorrow with --date.")

    done = []
    for r in chosen:
        try:
            e = svc._run(providers, r.id, day, wl, listed=r)
            done.append(r.id)
            print(f"  fetched {r.id} {r.home_name} vs {r.away_name}: {e['status']}")
            if a.max_calls and rec.calls >= a.max_calls:
                print(f"  call budget ({a.max_calls}) reached; stopping")
                break
        except (SnapshotMiss, RuntimeError, urllib.error.URLError) as ex:
            print(f"  stopped at {r.id}: {ex}")
            break

    out = a.out or f"snapshot-{day}.json"
    rec.dump(out, {"date": day.isoformat(), "tz": a.tz, "history": a.history,
                   "listed_fixture_ids": [r.id for r in refs], "analysed_fixture_ids": done})
    print(f"\nSaved {out}  ({rec.calls} API calls used"
          + (f", {rec.remaining} left today" if rec.remaining else "") + ")")
    print("Attach that file in the chat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
