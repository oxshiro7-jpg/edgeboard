"""HTTP API (FastAPI). Run: uvicorn app.api.main:app --reload"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field

from app.security import RateLimiter, create_token, decode_token, hash_password, verify_password
from app.providers.registry import ProviderNotConfigured
from app.services.analysis_service import AnalysisService
from app.services.board import FilterError
from app.settings import settings

log = logging.getLogger("edgeboard")
audit = logging.getLogger("edgeboard.audit")

settings.validate()
app = FastAPI(title="EdgeBoard API", version="0.3.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST", "PUT"],
                   allow_headers=["Authorization", "Content-Type"])
service = AnalysisService(settings, tz=settings.timezone)


def _warm_up() -> None:
    """Fetch today's slate in the background at start-up (free sources are rate-limited,
    so the first page view would otherwise wait for every request)."""
    try:
        from zoneinfo import ZoneInfo
        service.slate(datetime.now(ZoneInfo(settings.timezone)).date())
    except Exception as e:   # not configured or a source refused: pages show the reason
        log.warning("warm-up skipped: %s", e)


if os.getenv("WARM_UP", "1") == "1":
    import threading
    threading.Thread(target=_warm_up, daemon=True).start()
limiter = RateLimiter(settings.rate_limit_per_minute)
bearer = HTTPBearer(auto_error=False)

# In-memory user store. With DATABASE_URL set, swap for a repository on `users`.
USERS: dict[str, dict] = {}


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    ip = request.client.host if request.client else "unknown"
    if not limiter.allow(ip):
        return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
    return await call_next(request)


def current_user(cred: Optional[HTTPAuthorizationCredentials] = Depends(bearer)) -> Optional[dict]:
    if cred is None:
        return None
    try:
        claims = decode_token(cred.credentials)
    except Exception:
        raise HTTPException(401, "Invalid or expired token")
    user = next((u for u in USERS.values() if str(u["id"]) == claims["sub"]), None)
    if not user:
        raise HTTPException(401, "Unknown user")
    return user


def require_admin(user: Optional[dict] = Depends(current_user)) -> dict:
    if not user or user["role"] != "admin":
        raise HTTPException(403, "Admin only")
    return user


def _day(d: Optional[date]) -> date:
    from zoneinfo import ZoneInfo
    return d or datetime.now(ZoneInfo(settings.timezone)).date()


@app.exception_handler(ProviderNotConfigured)
async def provider_missing(_: Request, exc: ProviderNotConfigured):
    # No fixtures API means nothing can be verified: say so, never fall back to other data.
    return JSONResponse({"status": "UNVERIFIED", "detail": f"NO VERIFIED FIXTURE: {exc}"}, status_code=503)


# ------------------------------------------------------------------ public read API

@app.api_route("/api/tasks/run", methods=["GET", "POST"])
def run_tasks(token: str = ""):
    """Called by an external scheduler (e.g. cron-job.org every 15 minutes): records closing
    prices and settles finished picks. Also keeps the free host awake. Needs CRON_SECRET."""
    import hmac
    secret = os.getenv("CRON_SECRET", "")
    if not secret or not hmac.compare_digest(token, secret):
        raise HTTPException(403, "Invalid or missing task token")
    p = service.providers(_day(None))
    out = service.record.run_tasks(p)
    import threading      # refresh today's slate (and log new picks) without holding the scheduler's request
    threading.Thread(target=_warm_up, daemon=True).start()
    return out


@app.get("/api/health")
def health():
    try:
        service.providers(_day(None))
        configured, note = True, None
    except ProviderNotConfigured as e:
        configured, note = False, str(e)
    budget = None
    if configured and settings.football_provider == "api_football" and settings.api_football_key:
        from app.providers.http_cache import shared_transport
        budget = shared_transport(settings.api_football_key).status()
    elif configured and settings.football_provider == "football_data":
        from app.providers.registry import _shared_http
        budget = [h.status() for h in _shared_http.values()]   # type: ignore[attr-defined]
    st = service.store
    return {"status": "ok", "providers": {"football": settings.football_provider, "odds": settings.odds_provider},
            "provider_configured": configured, "note": note, "api_budget": budget,
            "storage": {"kind": st.kind, "persistent": st.persistent, "error": getattr(st, "error", None)},
            "scheduler": bool(os.getenv("CRON_SECRET"))}


@app.get("/api/competitions")
def competitions(d: Optional[date] = None):
    p = service.providers(_day(d))
    return [{"id": c.id, "name": c.name, "country": c.country, "type": c.type, "enabled": c.id in service.whitelist}
            for c in p.football.competitions()]


SortKey = Literal["kickoff", "confidence", "ev", "risk", "edge", "pick_score"]
RISK_RANK = {"Very Low": 0, "Low": 1, "Moderate": 2, "High": 3, "Very High": 4, None: 5}


@app.get("/api/matches")
def matches(d: Optional[date] = None, competition: Optional[str] = None, status: Optional[str] = None,
            min_confidence: Optional[float] = Query(None, ge=0, le=100), min_ev: Optional[float] = None,
            max_risk: Optional[str] = None, sort: SortKey = "kickoff"):
    """Every fixture the provider lists for the date in a supported competition,
    with its verification status. Unverified fixtures carry no prediction."""
    cards = service.summary_cards(_day(d))
    if competition:
        cards = [c for c in cards if (c.get("competition") or {}).get("id") == competition]
    if status:   # VERIFIED | UNVERIFIED | ODDS UNAVAILABLE | DATA INCOMPLETE | POSTPONED | CANCELLED
        cards = [c for c in cards if c["verification_status"] == status.upper()]
    if min_confidence is not None:
        cards = [c for c in cards if (c.get("confidence") or 0) >= min_confidence]
    if min_ev is not None:
        cards = [c for c in cards if (c.get("value_score") or 0) >= min_ev]
    if max_risk:
        cards = [c for c in cards if RISK_RANK.get(c.get("risk"), 5) <= RISK_RANK.get(max_risk, 5)]
    keyf = {
        "kickoff": lambda c: c.get("kickoff") or "",
        "confidence": lambda c: -(c.get("confidence") or 0),
        "ev": lambda c: -(c.get("value_score") or 0),
        "risk": lambda c: RISK_RANK.get(c.get("risk"), 5),
        "edge": lambda c: -(c.get("max_edge_pp") or -99),
        "pick_score": lambda c: -(c.get("best_pick_score") or 0),
    }[sort]
    return sorted(cards, key=keyf)


@app.get("/api/matches/{fixture_id}")
def match(fixture_id: str = Path(..., pattern=r"^[A-Za-z0-9_\-]{1,40}$"), d: Optional[date] = None):
    """Prediction by provider fixture id only. Returns the verification status;
    `analysis` is null unless the fixture verified."""
    return service.match(_day(d), fixture_id)


@app.get("/api/matches/{fixture_id}/lineup-update")
def match_lineup_update(fixture_id: str, d: Optional[date] = None):
    from app.services.analysis_service import lineup_update
    r = lineup_update(service, _day(d), fixture_id)
    if r is None:
        raise HTTPException(404, "Fixture not found")
    return r


@app.get("/api/board")
def board(d: Optional[date] = None):
    day = _day(d)
    b = service.board(day)
    try:
        perf = lab.performance(day)
        st = perf["streaks"]
        months = perf["breakdowns"].get("month", [])
        b["hero"]["current_streak"] = f"W{st['current_win']}" if st["current_win"] else (f"L{st['current_loss']}" if st["current_loss"] else None)
        b["hero"]["monthly_roi"] = months[-1]["roi"] if months else None
        b["hero"]["record_source"] = perf["source"]
    except NotImplementedError:
        pass   # provider without history: tracking metrics stay empty rather than invented
    return b


class FilterBody(BaseModel):
    query: str = Field(..., max_length=500)
    date: Optional[date] = None


@app.post("/api/picks/filter")
def filter_picks(body: FilterBody):
    try:
        return service.filter(_day(body.date), body.query)
    except FilterError as e:
        raise HTTPException(422, str(e))


@app.get("/api/search")
def search(q: str = Query(..., min_length=2, max_length=60), d: Optional[date] = None):
    return service.search(_day(d), q)


# ------------------------------------------------------------------ auth & user settings

class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=10, max_length=200)


@app.post("/api/auth/register")
def register(body: Credentials):
    if body.email in USERS:
        raise HTTPException(409, "Email already registered")
    uid = len(USERS) + 1
    USERS[body.email] = {"id": uid, "email": body.email, "password_hash": hash_password(body.password),
                         "role": "admin" if uid == 1 else "user", "settings": UserSettings().model_dump()}
    audit.info("register user=%s", uid)
    return {"token": create_token(uid, USERS[body.email]["role"])}


@app.post("/api/auth/login")
def login(body: Credentials):
    u = USERS.get(body.email)
    if not u or not verify_password(body.password, u["password_hash"]):
        audit.info("login_failed email=%s", body.email)
        raise HTTPException(401, "Invalid credentials")
    audit.info("login user=%s", u["id"])
    return {"token": create_token(u["id"], u["role"])}


class UserSettings(BaseModel):
    odds_format: Literal["decimal", "fractional"] = "decimal"
    favourite_competitions: list[str] = []
    favourite_markets: list[str] = []
    min_odds: float = Field(1.20, ge=1.01, le=100)
    max_odds: float = Field(6.0, ge=1.01, le=1000)
    min_confidence: float = Field(60, ge=0, le=100)
    min_ev_pct: float = Field(4, ge=-50, le=100)
    default_multiple_size: int = Field(3, ge=2, le=10)


@app.get("/api/me/settings")
def get_settings(user: Optional[dict] = Depends(current_user)):
    if not user:
        raise HTTPException(401, "Sign in required")
    return user["settings"]


@app.put("/api/me/settings")
def put_settings(body: UserSettings, user: Optional[dict] = Depends(current_user)):
    if not user:
        raise HTTPException(401, "Sign in required")
    user["settings"] = body.model_dump()
    return user["settings"]


# ------------------------------------------------------------------ admin

class ConfigPatch(BaseModel):
    ensemble: Optional[dict[str, float]] = None
    pick_weights: Optional[dict[str, float]] = None
    thresholds: Optional[dict] = None
    params: Optional[dict] = None
    enabled_markets: Optional[list[str]] = None
    model_version: Optional[str] = None
    competition_whitelist: Optional[list[str]] = None


@app.get("/api/admin/config")
def admin_get(_: dict = Depends(require_admin)):
    return service.admin_config()


@app.put("/api/admin/config")
def admin_put(patch: ConfigPatch, admin: dict = Depends(require_admin)):
    data = patch.model_dump(exclude_none=True)
    wl = data.pop("competition_whitelist", None)
    try:
        if data:
            service.config.update(data)
        if wl is not None:
            service.set_whitelist(wl)
    except (TypeError, ValueError) as e:
        raise HTTPException(422, str(e))
    audit.info("config_update user=%s fields=%s", admin["id"], sorted(patch.model_dump(exclude_none=True)))
    return service.admin_config()


# ================================================================== phases 4-8
from app.engine.backtest import BacktestParams  # noqa: E402
from app.services.lab import LabService  # noqa: E402

lab = LabService(service)


class LegIn(BaseModel):
    fixture_id: str = Field(..., max_length=80)
    market_key: str = Field(..., max_length=40)
    odds: Optional[float] = Field(None, gt=1.0, le=1000)


class BuildIn(BaseModel):
    legs: int = Field(3, ge=2, le=10)
    mode: Literal["conservative", "balanced", "value", "aggressive"] = "balanced"
    fixtures: Optional[list[str]] = None
    allow_same_match: bool = False
    date: Optional[date] = None


class LegsIn(BaseModel):
    legs: list[LegIn] = Field(..., min_length=1, max_length=20)
    min_legs: int = Field(2, ge=1, le=10)
    date: Optional[date] = None


def _lab_call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except (KeyError, ValueError) as e:
        raise HTTPException(422, str(e).strip("'"))


@app.post("/api/multiples/build")
def multiples_build(body: BuildIn):
    return _lab_call(lab.build, _day(body.date), body.legs, body.mode, body.fixtures, body.allow_same_match)


@app.get("/api/multiples/alternatives")
def multiples_alternatives(d: Optional[date] = None):
    return lab.alternatives(_day(d))


@app.post("/api/multiples/evaluate")
def multiples_evaluate(body: LegsIn):
    return _lab_call(lab.evaluate, _day(body.date), [l.model_dump() for l in body.legs])


@app.post("/api/multiples/optimise")
def multiples_optimise(body: LegsIn):
    return _lab_call(lab.optimise, _day(body.date), [l.model_dump() for l in body.legs], body.min_legs)


@app.post("/api/slip/analyse")
def slip_analyse(body: LegsIn):
    return _lab_call(lab.slip, _day(body.date), [l.model_dump() for l in body.legs])


# ---- tracking, challenges, bankroll (per-process store; DB tables bets/challenges/bankroll_history in production)

class TrackIn(LegIn):
    stake_units: float = Field(1.0, gt=0, le=5)
    date: Optional[date] = None


class SettleIn(BaseModel):
    home_goals: int = Field(..., ge=0, le=30)
    away_goals: int = Field(..., ge=0, le=30)
    home_goals_ht: int = Field(..., ge=0, le=30)
    away_goals_ht: int = Field(..., ge=0, le=30)
    cards: Optional[int] = Field(None, ge=0, le=40)
    corners: Optional[int] = Field(None, ge=0, le=40)


class BankrollIn(BaseModel):
    starting: float = Field(..., gt=0, le=1e9)
    unit_pct: float = Field(0.01, gt=0, le=0.05)
    mode: Literal["fixed", "percent_current"] = "fixed"


class ChallengeIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    kind: Literal["picks", "days"] = "picks"
    target: int = Field(30, ge=1, le=1000)
    rules: dict = Field(default_factory=dict)


@app.get("/api/performance")
def performance(d: Optional[date] = None):
    return lab.performance(_day(d))


@app.post("/api/bets")
def track_bet(body: TrackIn, user: Optional[dict] = Depends(current_user)):
    return _lab_call(lab.track, _day(body.date), body.fixture_id, body.market_key, body.odds, body.stake_units)


@app.get("/api/bets")
def list_bets():
    from app.engine.tracking import summarise, to_dict
    return {"bets": [to_dict(b) for b in lab.user_bets], "summary": summarise(lab.user_bets)}


@app.post("/api/bets/{bet_id}/settle")
def settle_bet(bet_id: str, body: SettleIn):
    return _lab_call(lab.settle_user_bet, bet_id, body.model_dump())


@app.get("/api/bankroll")
def get_bankroll():
    return lab.bankroll()


@app.put("/api/bankroll")
def put_bankroll(body: BankrollIn):
    return _lab_call(lab.set_bankroll, body.starting, body.unit_pct, body.mode)


@app.get("/api/challenges")
def challenges(d: Optional[date] = None):
    return lab.list_challenges(_day(d))


@app.post("/api/challenges")
def create_challenge(body: ChallengeIn):
    return _lab_call(lab.create_challenge, body.name, body.kind, body.target, body.rules)


# ---- backtesting, calibration, models, ML

class BacktestIn(BaseModel):
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    competitions: list[str] = []
    families: list[str] = []
    min_odds: float = Field(1.01, ge=1.01)
    max_odds: float = Field(1000, le=1000)
    min_confidence: float = Field(0, ge=0, le=100)
    min_ev: Optional[float] = Field(None, ge=-1, le=5)
    max_risk: Literal["Very Low", "Low", "Moderate", "High", "Very High"] = "Very High"
    selection: Literal["recommended", "best_per_match", "all_priced"] = "recommended"
    stake_units: float = Field(1.0, gt=0, le=5)


@app.post("/api/backtest")
def backtest(body: BacktestIn, d: Optional[date] = None):
    return lab.backtest(_day(d), BacktestParams(**body.model_dump()))


@app.get("/api/models")
def models(d: Optional[date] = None):
    return lab.models(_day(d))


@app.post("/api/admin/ml/train")
def ml_train(d: Optional[date] = None, admin: dict = Depends(require_admin)):
    audit.info("ml_train user=%s", admin["id"])
    return lab.train_ml(_day(d))


class PromoteIn(BaseModel):
    weight: float = Field(..., gt=0, le=1)
    new_version: str = Field(..., pattern=r"^[A-Za-z0-9_.\-]{3,60}$")


@app.post("/api/admin/ml/promote")
def ml_promote(body: PromoteIn, admin: dict = Depends(require_admin)):
    audit.info("ml_promote user=%s version=%s weight=%s", admin["id"], body.new_version, body.weight)
    return _lab_call(lab.promote_ml, body.weight, body.new_version)


# ---- AI analyst

class AskIn(BaseModel):
    question: str = Field(..., min_length=2, max_length=500)
    fixture_id: Optional[str] = None
    slip: Optional[list[LegIn]] = None
    date: Optional[date] = None


@app.post("/api/analyst")
def analyst(body: AskIn):
    ctx = {}
    if body.fixture_id:
        ctx["fixture_id"] = body.fixture_id
    if body.slip:
        ctx["slip"] = [l.model_dump() for l in body.slip]
    return lab.ask(_day(body.date), body.question, ctx or None)
