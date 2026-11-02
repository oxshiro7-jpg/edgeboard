"""Small persistent document store for app state that must survive restarts:
tracked bets, the model's forward pick log, challenges, bankroll plan, and the
API response caches (so a restart does not spend free API allowance again).

DATABASE_URL=postgresql://...   -> Postgres (e.g. Neon free tier), via psycopg
DATABASE_URL=sqlite:///path     -> SQLite file (local use, tests)
unset                           -> process memory only (lost on restart)

One table, `edgeboard_kv(k text primary key, v text, updated_at)`, JSON values.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from typing import Any, Optional

log = logging.getLogger("edgeboard.store")


class Store:
    kind = "memory"

    def __init__(self):
        self._mem: dict[str, str] = {}
        self._lock = threading.Lock()

    @property
    def persistent(self) -> bool:
        return self.kind != "memory"

    def get(self, key: str, default: Any = None) -> Any:
        raw = self._read(key)
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except ValueError:
            log.error("unreadable stored value for %s; ignoring it", key)
            return default

    def put(self, key: str, value: Any) -> None:
        self._write(key, json.dumps(value, default=str, separators=(",", ":")))

    # memory backend
    def _read(self, key: str) -> Optional[str]:
        return self._mem.get(key)

    def _write(self, key: str, raw: str) -> None:
        self._mem[key] = raw


class SqliteStore(Store):
    kind = "sqlite"

    def __init__(self, path: str):
        super().__init__()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS edgeboard_kv (k TEXT PRIMARY KEY, v TEXT NOT NULL, "
                         "updated_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        self._db.commit()

    def _read(self, key):
        with self._lock:
            row = self._db.execute("SELECT v FROM edgeboard_kv WHERE k = ?", (key,)).fetchone()
        return row[0] if row else None

    def _write(self, key, raw):
        with self._lock:
            self._db.execute("INSERT INTO edgeboard_kv (k, v, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
                             "ON CONFLICT(k) DO UPDATE SET v = excluded.v, updated_at = CURRENT_TIMESTAMP", (key, raw))
            self._db.commit()


class PostgresStore(Store):
    kind = "postgres"

    def __init__(self, url: str):
        super().__init__()
        import psycopg   # only needed when a Postgres URL is configured
        self._psycopg = psycopg
        self._url = url
        self._conn = None
        self._exec("CREATE TABLE IF NOT EXISTS edgeboard_kv (k TEXT PRIMARY KEY, v TEXT NOT NULL, "
                   "updated_at TIMESTAMPTZ NOT NULL DEFAULT now())")

    def _connection(self):
        if self._conn is None or self._conn.closed:
            self._conn = self._psycopg.connect(self._url, autocommit=True, connect_timeout=15)
        return self._conn

    def _exec(self, sql: str, args: tuple = (), fetch: bool = False):
        with self._lock:
            for attempt in (1, 2):      # serverless Postgres drops idle connections: reconnect once
                try:
                    with self._connection().cursor() as cur:
                        cur.execute(sql, args)
                        return cur.fetchone() if fetch else None
                except self._psycopg.OperationalError:
                    self._conn = None
                    if attempt == 2:
                        raise

    def _read(self, key):
        row = self._exec("SELECT v FROM edgeboard_kv WHERE k = %s", (key,), fetch=True)
        return row[0] if row else None

    def _write(self, key, raw):
        self._exec("INSERT INTO edgeboard_kv (k, v, updated_at) VALUES (%s, %s, now()) "
                   "ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v, updated_at = now()", (key, raw))


_store: Optional[Store] = None
_store_lock = threading.Lock()


def open_store(url: Optional[str]) -> Store:
    if not url:
        return Store()
    if url.startswith("sqlite:///"):
        return SqliteStore(url[len("sqlite:///"):])
    if url.startswith(("postgres://", "postgresql://")):
        return PostgresStore(url)
    raise ValueError("DATABASE_URL must start with postgresql:// or sqlite:///")


def get_store() -> Store:
    """Process-wide store from DATABASE_URL. Falls back to memory (logged) if the database is unreachable."""
    global _store
    with _store_lock:
        if _store is None:
            url = os.getenv("DATABASE_URL", "")
            try:
                _store = open_store(url)
            except Exception as e:     # never take the site down over storage; say so in /api/health
                log.error("database unavailable, using memory only: %s", e)
                _store = Store()
                _store.error = str(e)   # type: ignore[attr-defined]
        return _store


def set_store(s: Store) -> None:     # tests
    global _store
    _store = s
