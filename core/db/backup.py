"""Database backup and restore.

* Backups use SQLite's online backup API, so they are consistent even while the
  app holds the database open (including WAL mode).
* Every backup is integrity-checked after it is written.
* Restore validates the candidate file (integrity, EventPulse schema, not newer
  than this app), takes a safety backup of the current database, then copies the
  candidate into the live database in a single backup-API operation.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from config import settings
from core.db.errors import BackupError

BACKUP_DIR = settings.DATABASE_DIR / "backups"
_SAFE = re.compile(r"[^A-Za-z0-9_\-]+")


@dataclass
class BackupInfo:
    path: Path
    size_bytes: int
    created_utc: str
    schema_version: int | None
    label: str


def _inspect(path: Path) -> tuple[list[str], int | None, set[str]]:
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            problems = [r[0] for r in con.execute("PRAGMA integrity_check").fetchall() if r[0] != "ok"]
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            version = None
            if "schema_migrations" in tables:
                version = con.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            return problems, version, tables
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        return [f"not a readable SQLite database ({exc})"], None, set()


def backup_database(db, label: str = "manual", backup_dir: Path | None = None,
                    destination: Path | None = None) -> BackupInfo:
    """Write a consistent copy of ``db`` and verify it."""
    backup_dir = Path(backup_dir or BACKUP_DIR)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = Path(destination) if destination else backup_dir / f"eventpulse_{_SAFE.sub('_', label)}_{stamp}.db"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise BackupError(f"{dest} already exists.")
    try:
        target = sqlite3.connect(dest)
        try:
            db.conn.backup(target)
        finally:
            target.close()
    except sqlite3.Error as exc:
        dest.unlink(missing_ok=True)
        raise BackupError(f"Backup failed: {exc}") from exc
    problems, version, _ = _inspect(dest)
    if problems:
        dest.unlink(missing_ok=True)
        raise BackupError("Backup verification failed: " + "; ".join(problems))
    try:
        db.audit("backup", "database", None, str(dest.name))
    except Exception:
        pass
    return BackupInfo(dest, dest.stat().st_size, stamp, version, label)


def list_backups(backup_dir: Path | None = None) -> list[BackupInfo]:
    backup_dir = Path(backup_dir or BACKUP_DIR)
    out = []
    for p in sorted(backup_dir.glob("eventpulse_*.db"), reverse=True):
        _, version, _ = _inspect(p)
        m = re.match(r"eventpulse_(.+)_(\d{8}T\d{6}\d*Z)\.db$", p.name)
        label, stamp = (m.group(1), m.group(2)) if m else ("?", "?")
        out.append(BackupInfo(p, p.stat().st_size, stamp, version, label))
    return out


def prune_backups(keep: int = 20, backup_dir: Path | None = None) -> list[Path]:
    """Delete the oldest *manual/automatic* backups beyond ``keep``. Safety backups
    taken before a restore are kept."""
    removed = []
    candidates = [b for b in list_backups(backup_dir) if not b.label.startswith("pre_restore")]
    for b in candidates[keep:]:
        b.path.unlink(missing_ok=True)
        removed.append(b.path)
    return removed


def validate_backup(path: Path) -> int:
    """Return the schema version of a backup file, or raise BackupError."""
    from core.db.migrations import available_migrations
    path = Path(path)
    if not path.exists():
        raise BackupError(f"Backup file not found: {path}")
    problems, version, tables = _inspect(path)
    if problems:
        raise BackupError("The backup file is damaged: " + "; ".join(problems[:3]))
    required = {"schema_migrations", "students", "events", "registrations", "predictions", "model_versions"}
    if not required <= tables:
        raise BackupError("The file is not an EventPulse AI database.")
    newest = max(m.version for m in available_migrations())
    if version is None or version > newest:
        raise BackupError(f"The backup uses schema version {version}; this app supports up to {newest}.")
    return int(version)


def restore_database(db, source: Path, backup_dir: Path | None = None) -> BackupInfo:
    """Replace the contents of ``db`` with ``source``. Returns the safety backup
    taken of the database as it was before the restore."""
    from core.db.migrations import migrate
    validate_backup(source)
    if db._depth:
        raise BackupError("Cannot restore while a transaction is open.")
    safety = backup_database(db, label="pre_restore", backup_dir=backup_dir)
    try:
        src = sqlite3.connect(f"file:{Path(source)}?mode=ro", uri=True)
        try:
            src.backup(db.conn)
        finally:
            src.close()
    except sqlite3.Error as exc:
        # put the previous state back
        prev = sqlite3.connect(safety.path)
        try:
            prev.backup(db.conn)
        finally:
            prev.close()
        raise BackupError(f"Restore failed and the previous database was put back: {exc}") from exc
    db.conn.execute("PRAGMA foreign_keys = ON")
    migrate(db, backup_dir=backup_dir)      # bring an older backup up to the current schema
    db.audit("restore", "database", None, f"from {Path(source).name}; safety copy {safety.path.name}")
    return safety
