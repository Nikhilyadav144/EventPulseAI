"""Backup and restore."""
import sqlite3

import pytest

from core.db.backup import (backup_database, list_backups, prune_backups, restore_database, validate_backup)
from core.db.connection import Database
from core.db.errors import BackupError
from tests.conftest import make_event, make_student

pytestmark = pytest.mark.sqlite_only          # SQLite file backups (PostgreSQL uses pg_dump)


def test_backup_creates_verified_copy(db, tmp_path):
    make_student(db, "STU001")
    info = backup_database(db, "manual", backup_dir=tmp_path / "b")
    assert info.path.exists() and info.schema_version == 3
    assert validate_backup(info.path) == 3
    con = sqlite3.connect(info.path)
    assert con.execute("SELECT student_id FROM students").fetchall() == [("STU001",)]
    con.close()
    assert [b.label for b in list_backups(tmp_path / "b")] == ["manual"]


def test_restore_returns_previous_state_with_safety_copy(db, tmp_path):
    bdir = tmp_path / "b"
    make_student(db, "STU001")
    snap = backup_database(db, "before_changes", backup_dir=bdir)
    make_student(db, "STU002")
    make_event(db, "EV01")
    safety = restore_database(db, snap.path, backup_dir=bdir)
    assert [r["student_id"] for r in db.query("SELECT student_id FROM students")] == ["STU001"]
    assert db.scalar("SELECT COUNT(*) FROM events") == 0
    assert db.integrity_check() == [] and db.scalar("PRAGMA foreign_keys") == 1
    con = sqlite3.connect(safety.path)                       # the overwritten state is still recoverable
    assert con.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 2
    con.close()
    make_student(db, "STU003")                               # database remains fully usable
    assert db.scalar("SELECT COUNT(*) FROM students") == 2


def test_restore_older_schema_is_migrated(tmp_path):
    old = Database(tmp_path / "old.db", migrate=False)
    from core.db.migrations import migrate
    migrate(old, target=1)
    make_student(old, "STU001")
    old.close()
    live = Database(tmp_path / "live.db")
    restore_database(live, tmp_path / "old.db", backup_dir=tmp_path / "b")
    assert live.scalar("SELECT MAX(version) FROM schema_migrations") == 3
    assert live.scalar("SELECT COUNT(*) FROM students") == 1
    live.close()


def test_restore_rejects_bad_files(db, tmp_path):
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"definitely not sqlite" * 100)
    other = tmp_path / "other.db"
    sqlite3.connect(other).execute("CREATE TABLE t(x)").connection.close()
    future = tmp_path / "future.db"
    f = Database(future)
    f.execute("INSERT INTO schema_migrations VALUES (99, 'future', 'x', '2030-01-01T00:00:00Z')")
    f.close()
    make_student(db, "STU001")
    for bad, msg in ((junk, "damaged|not a readable"), (other, "not an EventPulse"), (future, "version 99"),
                     (tmp_path / "missing.db", "not found")):
        with pytest.raises(BackupError, match=msg):
            restore_database(db, bad, backup_dir=tmp_path / "b")
    assert db.scalar("SELECT COUNT(*) FROM students") == 1   # untouched after every refusal


def test_prune_keeps_safety_backups(db, tmp_path):
    bdir = tmp_path / "b"
    for i in range(4):
        backup_database(db, f"auto{i}", backup_dir=bdir)
    snap = backup_database(db, "x", backup_dir=bdir)
    restore_database(db, snap.path, backup_dir=bdir)
    removed = prune_backups(keep=2, backup_dir=bdir)
    labels = [b.label for b in list_backups(bdir)]
    assert len(removed) == 3 and "pre_restore" in labels and len(labels) == 3
