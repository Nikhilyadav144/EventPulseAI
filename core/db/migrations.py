"""Versioned, non-destructive schema migrations.

* Migrations are SQL files ``core/db/sql_migrations/NNNN_name.sql``, applied in order.
* Each migration runs inside one transaction: it applies completely or not at all.
* Applied migrations are recorded in ``schema_migrations`` with a SHA-256 checksum.
  If an applied migration file is later edited, startup fails loudly instead of
  silently diverging.
* Before migrating a database that already holds data, an automatic backup is
  taken (``database/backups/pre_migration_...``).
* A database created by a NEWER app version is refused (no downgrade).
* Rule for future migrations: only additive changes (CREATE, ALTER TABLE ADD
  COLUMN) or the create-copy-swap pattern that copies every row. Never DROP data.
* PostgreSQL (cloud deployment only) uses its own equivalent scripts in
  ``sql_migrations/postgres/`` with the same version numbers. PostgreSQL has no file to
  copy, so instead of an automatic backup, migrating a PostgreSQL database that already
  holds data requires ``EVENTPULSE_PG_BACKUP_CONFIRMED=1`` (take a pg_dump first).
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from core.db.connection import Database, utc_now
from core.db.errors import DB_ERRORS, MigrationError

MIGRATIONS_DIR = Path(__file__).resolve().parent / "sql_migrations"
PG_MIGRATIONS_DIR = MIGRATIONS_DIR / "postgres"
_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def available_migrations(directory: Path | None = None) -> list[Migration]:
    directory = Path(directory or MIGRATIONS_DIR)
    out = []
    for p in sorted(directory.glob("*.sql")):
        m = _NAME.match(p.name)
        if not m:
            raise MigrationError(f"Badly named migration file: {p.name}")
        out.append(Migration(int(m.group(1)), m.group(2), p))
    versions = [m.version for m in out]
    if versions != list(range(1, len(out) + 1)):
        raise MigrationError(f"Migration versions must be 1..N without gaps, found {versions}")
    return out


def _ensure_table(db: Database) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY, name TEXT NOT NULL,
                    checksum TEXT NOT NULL, applied_utc TEXT NOT NULL)""")


def current_version(db: Database) -> int:
    _ensure_table(db)
    return int(db.scalar("SELECT COALESCE(MAX(version), 0) FROM schema_migrations"))


def _has_user_data(db: Database) -> bool:
    tables = [t for t in db.list_tables() if t != "schema_migrations"]
    return any(db.scalar(f'SELECT EXISTS(SELECT 1 FROM "{t}")') for t in tables)


def migrate(db: Database, directory: Path | None = None, target: int | None = None,
            backup_dir: Path | None = None) -> list[int]:
    """Apply pending migrations. Returns the versions applied."""
    if directory is None and not db.is_sqlite:
        directory = PG_MIGRATIONS_DIR
    migrations = available_migrations(directory)
    _ensure_table(db)
    applied = {r["version"]: r for r in db.query("SELECT * FROM schema_migrations")}
    known = {m.version: m for m in migrations}

    newest_known = max(known) if known else 0
    if applied and max(applied) > newest_known:
        raise MigrationError(
            f"This database uses schema version {max(applied)}, newer than this app supports "
            f"({newest_known}). Update EventPulse AI instead of opening it with an older version.")
    for v, row in applied.items():
        if known[v].checksum != row["checksum"]:
            raise MigrationError(f"Migration {v:04d} was modified after it was applied; refusing to continue.")

    target = newest_known if target is None else target
    pending = [m for m in migrations if m.version not in applied and m.version <= target]
    if not pending:
        return []

    if applied and not db.is_sqlite and _has_user_data(db):
        if os.environ.get("EVENTPULSE_PG_BACKUP_CONFIRMED") != "1":
            raise MigrationError(
                "This PostgreSQL database already holds data and needs a schema upgrade. Take a backup "
                "(pg_dump) first, then set EVENTPULSE_PG_BACKUP_CONFIRMED=1 and start again.")
    elif applied and _has_user_data(db):
        from core.db.backup import backup_database
        backup_database(db, label=f"pre_migration_v{max(applied)}_to_v{pending[-1].version}",
                        backup_dir=backup_dir)

    done = []
    for m in pending:
        if not db.is_sqlite:
            _apply_pg(db, m)
            done.append(m.version)
            continue
        try:
            # executescript() would auto-commit, so statements are run one by one in a transaction.
            db.conn.execute("BEGIN IMMEDIATE")
            for stmt in _split_sql(m.sql):
                db.conn.execute(stmt)
            db.conn.execute("INSERT INTO schema_migrations(version, name, checksum, applied_utc) VALUES (?,?,?,?)",
                            (m.version, m.name, m.checksum, utc_now()))
            db.conn.execute(f"PRAGMA user_version = {m.version}")
            db.conn.execute("COMMIT")
        except sqlite3.Error as exc:
            db.conn.execute("ROLLBACK")
            raise MigrationError(f"Migration {m.version:04d}_{m.name} failed and was rolled back: {exc}") from exc
        done.append(m.version)
    return done


def _apply_pg(db: Database, m: Migration) -> None:
    """PostgreSQL DDL is transactional: the whole script and its record commit together."""
    try:
        db.conn.execute("BEGIN")
        db.conn.executescript(m.sql)
        db.conn.execute("INSERT INTO schema_migrations(version, name, checksum, applied_utc) VALUES (?,?,?,?)",
                        (m.version, m.name, m.checksum, utc_now()))
        db.conn.execute("COMMIT")
    except DB_ERRORS as exc:
        try:
            db.conn.execute("ROLLBACK")
        except DB_ERRORS:
            pass
        raise MigrationError(f"Migration {m.version:04d}_{m.name} failed and was rolled back: {exc}") from exc


def _split_sql(script: str) -> list[str]:
    """Split a script into complete statements (handles CREATE TRIGGER ... END;)."""
    statements, buf = [], ""
    for line in script.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("--") and not buf.strip():
            continue
        buf += line
        if sqlite3.complete_statement(buf):
            s = buf.strip()
            if s and s != ";":
                statements.append(s)
            buf = ""
    if buf.strip():
        raise MigrationError("Migration ends with an incomplete SQL statement.")
    return statements
