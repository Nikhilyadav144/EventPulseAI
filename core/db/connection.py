"""Database connection management and transactions.

SQLite (the desktop app and the default everywhere) or PostgreSQL (optional, for a cloud
deployment). PostgreSQL is used only when a ``postgresql://`` URL is given, either as the
``path`` argument or through ``open_database()`` with ``EVENTPULSE_DB_URL`` set. With no URL
the behaviour is exactly the previous SQLite behaviour.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from config import settings
from core.db.errors import DB_ERRORS, DatabaseError, translate

DEFAULT_DB_PATH = settings.DATABASE_DIR / "eventpulse.db"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _configure(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


class Database:
    """Owns a single connection. Use ``transaction()`` for every write.

    ``transaction()`` nests safely: inner blocks join the outer transaction
    (via SAVEPOINTs), so a failure anywhere rolls back the whole unit.
    """

    def __init__(self, path: str | Path | None = None, migrate: bool = True, url: str | None = None):
        if url is None and isinstance(path, str) and path.startswith(("postgresql://", "postgres://")):
            url, path = path, None
        if url is not None and not str(url).startswith(("postgresql://", "postgres://")):
            raise ValueError("Database(url=...) needs a postgresql:// URL; pass a file path for SQLite.")
        self._depth = 0
        if url:
            from core.db import postgres              # never imported on the SQLite (desktop) path
            self.dialect = "postgresql"
            self.path = None
            self.url_redacted = postgres.redact(url)
            self.identity = "pg:" + self.url_redacted
            try:
                self.conn = postgres.connect(url)
            except DB_ERRORS as exc:
                raise translate(exc) from exc
        else:
            self.dialect = "sqlite"
            self.path = Path(path) if path else DEFAULT_DB_PATH
            self.url_redacted = None
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                self.conn = _configure(sqlite3.connect(self.path, isolation_level=None,
                                                       detect_types=0, check_same_thread=False))
            except sqlite3.Error as exc:
                raise translate(exc) from exc
            self.identity = "sqlite:" + str(self.path.resolve())
        if migrate:
            from core.db.migrations import migrate as run_migrations
            run_migrations(self)

    @property
    def is_sqlite(self) -> bool:
        return self.dialect == "sqlite"

    @property
    def display_name(self) -> str:
        """Safe to show on screen: file name for SQLite, host/database (no credentials) for PostgreSQL."""
        return self.path.name if self.is_sqlite else self.url_redacted

    def list_tables(self) -> list[str]:
        if self.is_sqlite:
            sql = "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        else:
            sql = ("SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema() "
                   "AND table_type = 'BASE TABLE' ORDER BY table_name")
        return [r[0] for r in self.execute(sql).fetchall()]

    def table_exists(self, name: str) -> bool:
        return name in self.list_tables()

    # ------------------------------------------------------------------ helpers
    def close(self) -> None:
        try:
            self.conn.close()
        except DB_ERRORS:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        name = f"sp{self._depth}"
        try:
            if self._depth == 0:
                self.conn.execute("BEGIN IMMEDIATE")      # plain BEGIN on PostgreSQL (adapter)
            else:
                self.conn.execute(f"SAVEPOINT {name}")
        except DB_ERRORS as exc:
            raise translate(exc) from exc
        self._depth += 1
        try:
            yield self.conn
        except BaseException as exc:
            self._depth -= 1
            try:
                if self._depth == 0:
                    self.conn.execute("ROLLBACK")
                else:
                    self.conn.execute(f"ROLLBACK TO {name}")
                    self.conn.execute(f"RELEASE {name}")
            except DB_ERRORS:
                pass
            if isinstance(exc, DB_ERRORS):
                raise translate(exc) from exc
            raise
        else:
            self._depth -= 1
            try:
                if self._depth == 0:
                    self.conn.execute("COMMIT")
                else:
                    self.conn.execute(f"RELEASE {name}")
            except DB_ERRORS as exc:
                raise translate(exc) from exc

    def execute(self, sql: str, params=()) -> sqlite3.Cursor:
        try:
            return self.conn.execute(sql, params)
        except DB_ERRORS as exc:
            raise translate(exc) from exc

    def query(self, sql: str, params=()) -> list[dict]:
        return [dict(r) for r in self.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params=()) -> dict | None:
        r = self.execute(sql, params).fetchone()
        return dict(r) if r else None

    def scalar(self, sql: str, params=()):
        r = self.execute(sql, params).fetchone()
        return r[0] if r else None

    def audit(self, action: str, entity: str | None = None, entity_id=None, details: str | None = None) -> None:
        self.execute("INSERT INTO audit_log(ts_utc, action, entity, entity_id, details) VALUES (?,?,?,?,?)",
                     (utc_now(), action, entity, None if entity_id is None else str(entity_id), details))

    def integrity_check(self) -> list[str]:
        if not self.is_sqlite:
            # PostgreSQL enforces foreign keys and checks pages itself; there is no PRAGMA equivalent.
            return []
        problems = [r[0] for r in self.conn.execute("PRAGMA integrity_check").fetchall() if r[0] != "ok"]
        problems += [f"foreign key: {tuple(r)}" for r in self.conn.execute("PRAGMA foreign_key_check").fetchall()]
        return problems


def ensure_ok(db: Database) -> None:
    problems = db.integrity_check()
    if problems:
        raise DatabaseError("Database integrity check failed: " + "; ".join(problems[:5]))


PG_URL_PREFIXES = ("postgresql://", "postgres://")


def web_database_url(env=None) -> str | None:
    """The explicit web database URL (``EVENTPULSE_DB_URL``), or None when it is not set.

    Only the web server and its tools call this; the desktop app never does, so it always uses
    SQLite. A value that is set but is not a PostgreSQL URL is refused (it would otherwise be
    mistaken for a SQLite file path)."""
    env = os.environ if env is None else env
    url = (env.get("EVENTPULSE_DB_URL") or "").strip()
    if not url:
        return None
    if not url.startswith(PG_URL_PREFIXES):
        raise ValueError("EVENTPULSE_DB_URL must be a postgresql:// URL (leave it unset to use SQLite).")
    return url


def open_database(path: str | Path | None = None, migrate: bool = True) -> Database:
    """Database chosen by configuration: ``EVENTPULSE_DB_URL`` (PostgreSQL) if set, else SQLite.

    The desktop app does not set ``EVENTPULSE_DB_URL``, so it always gets SQLite.
    """
    url = web_database_url()
    if url:
        return Database(url=url, migrate=migrate)
    return Database(path, migrate=migrate)
