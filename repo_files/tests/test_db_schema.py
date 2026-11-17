"""Schema, migrations, transactions and error handling."""
import shutil

import pytest

from core.db.connection import Database
from core.db.errors import (ConstraintViolationError, DuplicateRecordError, HistoryProtectedError,
                            MigrationError, ReferenceError_)
from core.db.migrations import MIGRATIONS_DIR, available_migrations, current_version, migrate
from tests.conftest import make_event, make_student

EXPECTED_TABLES = {"app_settings", "datasets", "courses", "students", "events", "weather_forecasts",
                   "registrations", "model_versions", "model_metrics", "prediction_runs", "predictions",
                   "attendance_records", "event_outcomes", "prediction_evaluations", "prediction_evaluation_items",
                   "audit_log", "schema_migrations"}


@pytest.mark.sqlite_only
def test_fresh_database_schema(db):
    tables = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert EXPECTED_TABLES <= tables
    views = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='view'")}
    assert {"v_latest_forecast", "v_registration_features", "v_latest_predictions", "v_event_participation"} <= views
    assert current_version(db) == len(available_migrations()) == 3
    assert db.scalar("PRAGMA foreign_keys") == 1
    assert db.integrity_check() == []


@pytest.mark.sqlite_only
def test_no_personal_data_columns_anywhere(db):
    pii = {"name_of_student", "student_name", "email", "email_address", "mobile", "mobile_no", "phone",
           "roll_number", "roll_no"}
    for t in EXPECTED_TABLES:
        cols = {r["name"].lower() for r in db.query(f'PRAGMA table_info("{t}")')}
        assert not cols & pii, t


def test_reopen_is_idempotent(tmp_path):
    p = tmp_path / "a.db"
    Database(p).close()
    d = Database(p)
    assert current_version(d) == 3 and migrate(d) == []
    d.close()


def test_migration_preserves_existing_data_and_backs_up(tmp_path):
    backups = tmp_path / "backups"
    d = Database(tmp_path / "old.db", migrate=False)
    migrate(d, target=1)
    make_student(d, "STU001")
    make_event(d, "EV01")
    assert current_version(d) == 1
    applied = migrate(d, backup_dir=backups)
    assert applied == [2, 3]
    assert d.scalar("SELECT COUNT(*) FROM students") == 1 and d.scalar("SELECT COUNT(*) FROM events") == 1
    assert d.query("SELECT * FROM v_event_participation")[0]["event_id"] == "EV01"
    assert len(list(backups.glob("eventpulse_pre_migration_v1_to_v3_*.db"))) == 1
    d.close()


def test_failed_migration_rolls_back(tmp_path):
    mig = tmp_path / "mig"
    shutil.copytree(MIGRATIONS_DIR, mig)
    (mig / "0004_broken.sql").write_text("CREATE TABLE extra_table (x INTEGER);\nTHIS IS NOT SQL;\n")
    d = Database(tmp_path / "x.db", migrate=False)
    migrate(d, directory=mig, target=3)
    with pytest.raises(MigrationError, match="rolled back"):
        migrate(d, directory=mig, backup_dir=tmp_path / "b")
    assert current_version(d) == 3
    assert not d.query("SELECT name FROM sqlite_master WHERE name = 'extra_table'")
    d.close()


def test_modified_migration_detected(tmp_path):
    mig = tmp_path / "mig"
    shutil.copytree(MIGRATIONS_DIR, mig)
    d = Database(tmp_path / "x.db", migrate=False)
    migrate(d, directory=mig)
    (mig / "0002_indexes_and_views.sql").write_text((mig / "0002_indexes_and_views.sql").read_text() + "\n-- edit\n")
    with pytest.raises(MigrationError, match="modified"):
        migrate(d, directory=mig)
    d.close()


def test_newer_database_refused(tmp_path):
    d = Database(tmp_path / "x.db")
    d.execute("INSERT INTO schema_migrations VALUES (99, 'future', 'x', '2030-01-01T00:00:00Z')")
    d.close()
    with pytest.raises(MigrationError, match="newer"):
        Database(tmp_path / "x.db")


def test_transaction_rolls_back_whole_unit(db):
    with pytest.raises(RuntimeError):
        with db.transaction():
            make_student(db, "STU001")
            with db.transaction():
                make_event(db, "EV01")
            raise RuntimeError("boom")
    assert db.scalar("SELECT COUNT(*) FROM students") == 0
    assert db.scalar("SELECT COUNT(*) FROM events") == 0


def test_nested_failure_rolls_back_outer(db):
    with pytest.raises(DuplicateRecordError):
        with db.transaction():
            make_student(db, "STU001")
            make_student(db, "STU001")
    assert db.scalar("SELECT COUNT(*) FROM students") == 0
    make_student(db, "STU002")       # connection still usable afterwards
    assert db.scalar("SELECT COUNT(*) FROM students") == 1


def test_error_translation(db):
    with pytest.raises(ReferenceError_):
        db.execute("INSERT INTO registrations(student_id, event_id, registration_days_before, has_exam_near_event,"
                   " data_source) VALUES ('NOPE','NOPE',1,0,'MANUAL')")
    with pytest.raises(ConstraintViolationError):
        db.execute("INSERT INTO courses(name, department) VALUES (NULL, 'x')")


def test_predictions_are_append_only(seeded_db):
    from core.db import services
    services.predict_event(seeded_db, "EV22")
    with pytest.raises(HistoryProtectedError):
        seeded_db.execute("UPDATE predictions SET probability = 0.5")


def test_database_check_constraints_block_bad_rows(db):
    make_student(db, "STU001")
    make_event(db, "EV01")
    bad = [
        "UPDATE students SET year = 9 WHERE student_id = 'STU001'",
        "UPDATE students SET previous_event_attendance = 99 WHERE student_id = 'STU001'",
        "UPDATE events SET event_day = 'Friday' WHERE event_id = 'EV01'",          # 2026-10-14 is a Wednesday
        "UPDATE events SET event_type = 'Party' WHERE event_id = 'EV01'",
        "INSERT INTO students(student_id, course_id, year, currently_uba_volunteer, participated_uba_event_before, "
        "experience_level, area_of_interest, role, previous_event_attendance, data_source) "
        "VALUES ('26CSU210', 1, 1, 0, 0, 'No experience', 'Not Sure Yet', 'Content', 0, 'MANUAL')",
        "INSERT INTO weather_forecasts(event_id, forecast, source) VALUES ('EV01', 'Rainy', 'MANUAL')",
        "INSERT INTO datasets(name, kind, source_sheet) VALUES ('x', 'UPLOAD', 'Raw_Merged_Data')",
    ]
    for sql in bad:
        with pytest.raises(ConstraintViolationError):
            db.execute(sql)
