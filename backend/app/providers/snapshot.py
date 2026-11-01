"""Record API-Football responses to a file, and replay them later.

A snapshot is the raw JSON that API-Football returned, keyed by request
(path + query). Replaying it runs the exact same verification and models as a
live call, with no network. A request that is not in the snapshot raises
SnapshotMiss: nothing is ever filled in or guessed.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

BASE_URL = "https://v3.football.api-sports.io"


class SnapshotMiss(RuntimeError):
    """The request was not recorded, so there is no data for it."""


def request_key(path: str, params: dict) -> str:
    q = urllib.parse.urlencode(sorted((k, str(v)) for k, v in params.items() if v is not None))
    return f"{path}?{q}"


class RecordingTransport:
    """Live calls via urllib (stdlib only), each distinct request made once."""

    def __init__(self, api_key: str, max_calls: Optional[int] = None, fetch=None):
        self.api_key = api_key
        self._fetch = fetch          # injectable for tests; default is a live HTTPS call
        self.max_calls = max_calls
        self.calls = 0
        self.responses: dict[str, dict] = {}
        self.remaining: Optional[str] = None

    def __call__(self, path: str, params: dict) -> dict:
        key = request_key(path, params)
        if key in self.responses:
            return self.responses[key]
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise SnapshotMiss(f"call budget of {self.max_calls} used up before {key}")
        if self._fetch:
            body = self._fetch(path, params)
        else:
            req = urllib.request.Request(f"{BASE_URL}{key}", headers={"x-apisports-key": self.api_key})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.loads(r.read().decode("utf-8"))
                self.remaining = r.headers.get("x-ratelimit-requests-remaining", self.remaining)
        self.calls += 1
        self.responses[key] = body
        return body

    def dump(self, path: str, meta: dict) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"source": "api-football v3", "base_url": BASE_URL,
                       "recorded_at": datetime.now(timezone.utc).isoformat(), **meta,
                       "responses": self.responses}, f)


class ReplayTransport:
    def __init__(self, snapshot: dict):
        if snapshot.get("source") != "api-football v3" or "responses" not in snapshot:
            raise ValueError("Not an API-Football snapshot file")
        self.responses: dict[str, dict] = snapshot["responses"]

    def __call__(self, path: str, params: dict) -> dict:
        key = request_key(path, params)
        if key not in self.responses:
            raise SnapshotMiss(f"not in snapshot: {key}")
        return self.responses[key]
