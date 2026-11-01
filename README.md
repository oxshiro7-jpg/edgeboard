# EdgeBoard

Football match analysis and betting-market intelligence. All eight phases of the build order:

1–3. UI, fixtures, team stats, odds, match analysis; the prediction ensemble (Poisson, Dixon-Coles,
     Elo, xG, form, market); EV, confidence, risk, pick score, no-bet engine, explanations.
4.   Multiple builder (4 modes, 5 named alternatives), exact correlation engine, Optimise Multiple,
     bet slip analyser with Fix My Slip.
5.   Settlement (incl. Asian quarter lines), performance metrics and breakdowns, CLV, streaks,
     challenges, flat-stake bankroll.
6.   Walk-forward backtesting lab, calibration buckets with confidence intervals, drift monitoring,
     per-market calibration factors.
7.   AI analyst grounded in tool calls over stored data (rule router; Claude tool-use when
     ANTHROPIC_API_KEY is set); alerts and lineup recalculation ("PREDICTION UPDATED").
8.   Stacked ML component (multinomial logistic regression) with chronological
     train/validation/test and champion/challenger promotion.

## Data integrity rules (read first)

- **Every match comes from the fixtures API.** `PredictionService` fetches the date's fixtures from
  API-Football, keeps supported competitions, and runs each provider `fixture_id` through
  `app/verification.py`: fixture exists, both team ids resolve to the same names, competition is
  supported, kickoff exists and falls on the selected date (in `APP_TIMEZONE`), and the status is not
  postponed or cancelled. Failures return `NO VERIFIED FIXTURE` or `TEAM NOT VERIFIED: <name>`.
- **The engine takes a fixture id, nothing else.** `predict(fixture_id, date)` is the only entry point.
  `analyse_fixture` raises `UnverifiedFixtureError` for any input without provider provenance, and
  `build_fixture_input` only accepts a `VerifiedFixture`.
- **No fuzzy matching anywhere.** Teams are compared by provider id and exact name. Odds come from
  API-Football by fixture id; if The Odds API is used instead, team names must match exactly (or via an
  explicit alias for that team id) and kick off within 3 hours, otherwise `ODDS UNAVAILABLE`.
- **No invented numbers.** Missing statistics stay missing: no default Elo, no placeholder player
  importance, no guessed odds. Too little history gives `DATA INCOMPLETE` and no prediction.
- **Backend arithmetic is re-checked.** `app/engine/integrity.py` recomputes EV
  (`p × odds − 1`, e.g. 0.57 × 2.15 − 1 = +22.55%), fair odds, implied probability and edge for every
  market; any mismatch rejects the recommendation and logs an error.
- **The AI only explains.** The analyst receives verified `{fixture, stats, odds, model_prediction}`
  payloads and runs under the explanation-only system rule. Unknown teams get `TEAM NOT VERIFIED`.
- **Statuses** shown everywhere: VERIFIED, UNVERIFIED, ODDS UNAVAILABLE, DATA INCOMPLETE, POSTPONED,
  CANCELLED. Only VERIFIED fixtures can produce recommendations.
- **There is no demo provider.** The synthetic world used for unit tests lives in
  `backend/tests/synthetic_world.py` (clubs named "Test Club XXX") and cannot be loaded by the app.
  `migrations/002_invalidate_unverified.sql` invalidates any stored prediction not produced from a
  verified provider fixture and deletes its recommendations.

Check it live (needs your key; this build environment cannot reach football APIs):

```bash
cd backend
export API_FOOTBALL_KEY=...  APP_TIMEZONE=Africa/Lagos
python scripts/verify_today.py          # every displayed match vs a direct /fixtures?id= lookup; exit 1 on mismatch
```

No server to hand? Record a snapshot instead (Python 3.9+, no installs; ~90 calls, fits the free 100/day):

```bash
cd backend
python scripts/fetch_snapshot.py --key YOUR_KEY --date 2026-10-05   # saves snapshot-2026-10-05.json (raw API responses)
python scripts/run_snapshot.py snapshot-2026-10-05.json             # full pipeline offline, checks every fixture vs the listing
API_FOOTBALL_SNAPSHOT=snapshot-2026-10-05.json uvicorn app.api.main:app   # the whole app on that snapshot
```

Anything not in the snapshot is reported as NO VERIFIED FIXTURE, never filled in.

Core rule carried through the code: **find good decisions, not guaranteed winners.**
Probability, confidence, value and risk are four separate numbers, and "no bet" is a normal output.

```
edgeboard/
  backend/                 Python 3.11+, FastAPI, SQLAlchemy, Celery
    app/engine/            pure-Python prediction engine (stdlib only, fully unit-tested)
      odds.py              implied prob, margin removal (proportional/power/Shin), fair odds, EV, push/half settlement
      goals.py             Poisson, Dixon-Coles, matrix reweighting, time-decayed league fit
      elo.py               Elo updates and Elo -> 1X2
      analysis.py          opponent-adjusted form, xG/xPts diagnostics, absence impact, context, tactics
      ensemble.py          5-component ensemble (xG Poisson, Dixon-Coles, form, Elo, market incl. implied totals)
      markets.py           1X2, DC, DNB, O/U 0.5–4.5, team totals, BTTS, AH (incl. quarter lines), EH, 1H/2H, cards, corners
      scoring.py           data quality, confidence, risk, pick score
      pipeline.py          analyse_fixture(): gates, labels, ranking, no-bet, movement, versioned output
      explain.py           "why this bet" built only from stored values
      multiples.py         joint same-match pricing, builder, optimiser, slip analyser (phase 4)
      tracking.py          settlement, metrics, streaks, challenges, bankroll (phase 5)
      backtest.py          walk-forward backtest runner (phase 6)
      calibration.py       buckets, Wilson intervals, ECE/Brier/log loss, drift (phase 6)
      ml.py                stacked softmax regression + champion/challenger (phase 8)
    app/providers/         FootballDataProvider / OddsProvider / InjuryProvider / LineupProvider / WeatherProvider
      api_football.py      API-Football v3 adapter
      the_odds_api.py      The Odds API v4 adapter (tested event mapping)
    app/services/          assembly, daily board, summary, pick filter, history (walk-forward cases),
                           lab (phases 4-8), analyst (phase 7), alerts + lineup recalculation
    app/api/main.py        REST API (+ auth, rate limiting, admin config, audit log)
    app/db/                PostgreSQL schema (all 21 tables + audit_logs, app_settings), append-only predictions
    app/workers/tasks.py   Celery beat schedule: history/fixtures/stats/odds/injuries/lineups/recalc/evaluation
    migrations/            TimescaleDB hypertables + DB-level immutability triggers
    tests/                 74 tests (engine, phases 4-8, verification gate on API-shaped payloads)
  frontend/                Next.js 15, TypeScript, Tailwind
  prototype/               static status page (no fixtures: it is not connected to an API)
```

## Put it online (Render)

One Docker service runs both halves (`Dockerfile`, `start.sh`, `render.yaml`). On render.com:
New > Blueprint > this repo, then enter `API_FOOTBALL_KEY` and `APP_PASSWORD`. Free-plan settings:
`API_DAILY_CALL_LIMIT=100`, `MAX_ANALYSED_FIXTURES=2` (other matches are listed and analysed when
opened), `API_HISTORY_MATCHES=8`. API responses are cached (`app/providers/http_cache.py`); the
transport stops at the daily limit and affected fixtures show NO VERIFIED FIXTURE. On a paid
API plan raise the limit and the cap. `/api/health` on the backend reports the day's API usage.

## Run it

Backend (in-memory stores; `API_FOOTBALL_KEY` is required, without it every page reports NO VERIFIED FIXTURE):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests      # or: pytest
uvicorn app.api.main:app --reload         # http://localhost:8000/docs
```

Frontend:

```bash
cd frontend
cp .env.example .env.local                # API_URL=http://localhost:8000
npm install && npm run dev                # http://localhost:3000
```

Full stack with Postgres/Redis: set `DATABASE_URL` and `REDIS_URL` from `.env.example`, run
`python -m app.db.init`, then `psql -f migrations/001_timescale.sql` (optional), then start
`celery -A app.workers.tasks worker -Q ingest,predict` and `celery -A app.workers.tasks beat`.

## Connecting real data

1. Set `FOOTBALL_PROVIDER=api_football`, `ODDS_PROVIDER=the_odds_api` and the two keys in `.env`.
   Keys are read only by the backend; the browser never sees them.
2. Implement the four `sync_*` Celery tasks against your plan's endpoints (stubs document what each stores),
   and an Elo/ratings job writing `team_stats.elo_after` (API-Football does not supply Elo).
3. Verify league ids in `api_football.LEAGUE_IDS` and sport keys in `the_odds_api.SPORT_KEYS` for your season/plan.
4. `production` mode refuses to start without `JWT_SECRET`/`DATABASE_URL`.

Data the model never invents: if xG, lineups, injuries, referee or corner data are missing, the engine
lowers data quality, caps confidence, and disables the affected markets (cards need a referee with 5+ matches;
corners need 5+ matches of corner data per side). Player-absence importance from API-Football defaults to a
flat 0.08 placeholder weight until `player_stats` minutes are synced; replace it with the minutes-share lookup.

## Engine decisions worth knowing

- **Ensemble**: weights 30% Dixon-Coles, 20% xG Poisson, 15% Elo, 10% form, 25% market (admin-configurable).
  The final score matrix is reshaped to match the ensemble 1X2, so every derived market is internally consistent.
  The market component also contributes an implied total from the 2.5 line, so goals markets are shrunk toward
  prices too. "Model disagreement" is measured across the models only, never against the market.
- **Margin removal**: power method by default (`proportional` and `shin` available).
- **Gates** (defaults, all in `engine/config.py`): EV ≥ +4%, confidence ≥ 60, data quality ≥ 60, pick score ≥ 60,
  odds 1.20–6.00, risk ≤ High, model spread ≤ 12 pp. An edge above 15 pp vs the no-vig price cuts confidence;
  above 30 pp the selection is rejected as "information the model lacks".
- **Confidence** is a product of factors (data quality, model agreement, lineup, injuries, context, sample,
  calibration, market stability, market-family reliability, divergence), capped at data quality + 10.
  Calibration is a neutral 0.92 prior until the evaluation job has settled picks.
- **"Must win"** context is reported but never converted into a probability boost.
- **H2H** is displayed with weight 0.
- **Versioning**: every analysis carries model version, timestamp and an input hash; `save_prediction`
  inserts a new snapshot (linked to the one it supersedes) and the migration blocks UPDATE/DELETE.
  `ensure_model_version` refuses a config change under an existing version name.

## Phases 4–8: how they work

- **Correlation is exact, not a fudge factor.** Legs on the same match are evaluated over that match's
  score distribution (first-half goals split binomially from full-time goals, exact for a Poisson
  process). Pushes are voids, quarter lines settle half stakes, so multiple EV and fair odds are exact
  under the model. Cards/corners legs are flagged as assumed independent of the scoreline.
- **Optimise Multiple** removes only legs with a named problem (negative EV, low confidence, high risk,
  conflicting/correlated, engine passed) and only while slip health improves; every removal is explained.
- **Performance** is the walk-forward record of every recommended selection on verified finished
  fixtures. It needs stored match history (API-Football adapter: wire `historical_fixtures` to your
  `fixtures`/`team_stats` tables); until then the page says no verified history is available. When the
  realised profit falls well short of the EV claimed at pick time, the app flags overstated edges.
- **Calibration** uses the spec's buckets plus <50%, with 95% Wilson intervals; a bucket is flagged only
  with n ≥ 30 and a gap outside both the tolerance and the interval. Per-family ECE becomes the
  confidence calibration factor (`calibration_factors`).
- **ML** is stacked on the component outputs. The blend weight is picked on a validation window and
  promotion is decided on a later untouched test window (log-loss gain ≥ 0.003 and no worse ECE). On
  the synthetic test history it is correctly rejected. Admins promote via `POST /api/admin/ml/promote` with a new
  model version. LightGBM/XGBoost can be dropped in behind the same interface once history is large.
- **Bankroll** supports flat units only (fixed % of starting bankroll, or % of the day's opening
  bankroll); units are capped at 5% and never rise after losses. There is no Martingale code path.
- **Analyst** answers only from tool output and returns the tool calls with each answer. It asks which
  match you mean when a club plays twice.

New endpoints: `/api/multiples/{build,evaluate,optimise,alternatives}`, `/api/slip/analyse`,
`/api/bets` (+ `/settle`), `/api/bankroll`, `/api/challenges`, `/api/performance`, `/api/backtest`,
`/api/models`, `/api/admin/ml/{train,promote}`, `/api/analyst`, `/api/matches/{id}/lineup-update`.
User bets, challenges and bankroll are held per process without a database; the `bets`, `bet_slips`,
`challenges`, `challenge_entries` and `bankroll_history` tables are ready for the repository layer.

## Verified vs. not yet verified

Run here: 72 backend tests, including the verification gate end to end through the real API-Football
adapter on API-shaped payloads (unknown fixture id, unknown team, name mismatch, postponed, cancelled,
wrong date, unsupported league, missing odds, short history, EV integrity, and the four named fake
fixtures). Not run here: a live call to API-Football (blocked by this environment's network policy),
FastAPI/SQLAlchemy/Celery and the Next.js build (could not be installed; syntax- and type-checked only).
Run `scripts/verify_today.py` with your key to confirm today's slate against the API.
