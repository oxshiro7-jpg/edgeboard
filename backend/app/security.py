"""Password hashing (scrypt, stdlib) and JWT session tokens (PyJWT)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

import jwt

from .settings import settings

_N, _R, _P = 2 ** 15, 8, 1
_DEV_SECRET = secrets.token_urlsafe(32)   # development only; tokens die on restart


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("Password must be at least 10 characters")
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=64 * 1024 * 1024, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, dk_b64 = stored.split("$")
    except ValueError:
        return False
    dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=_N, r=_R, p=_P,
                        maxmem=64 * 1024 * 1024, dklen=32)
    return hmac.compare_digest(dk, base64.b64decode(dk_b64))


def _secret() -> str:
    if settings.jwt_secret:
        return settings.jwt_secret
    if settings.env == "production":
        raise RuntimeError("JWT_SECRET not set")
    return _DEV_SECRET


def create_token(user_id: int, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "role": role, "iat": now, "exp": now + timedelta(minutes=settings.jwt_ttl_minutes)}
    return jwt.encode(payload, _secret(), algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, _secret(), algorithms=["HS256"])


class RateLimiter:
    """Fixed-window limiter. In-memory for a single instance; swap for Redis
    (INCR + EXPIRE) when running several API replicas."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: dict[str, tuple[int, int]] = {}

    def allow(self, key: str) -> bool:
        window = int(time.time() // 60)
        w, n = self.hits.get(key, (window, 0))
        if w != window:
            w, n = window, 0
        n += 1
        self.hits[key] = (w, n)
        return n <= self.per_minute
