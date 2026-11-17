"""PostgreSQL compatibility (Phase 1, cloud-deployment preparation).

* SQL translation unit tests: always run, no server needed.
* Everything else runs the SAME synthetic workflow on a fresh SQLite file and on a fresh database
  on a LOCAL PostgreSQL test server, and compares the results. Skipped when no local server or
  driver is available (see tests/pg_support.py). Only synthetic data is written; the real
  SQLite database is never opened.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from core.db import postgres as PG
from core.db.connection import Database
from core.db.errors import (ConstraintViolationError, DuplicateRecordError, HistoryProtectedError,
                            PersonalDataError)
from tests import pg_support
from tests.conftest import make_event, make_student

ROOT = Path(__file__).resolve().parent.parent
TODAY = date.today()


# =============================================================================== 1. translation units
@pytest.mark.parametrize("src, out", [
    ("SELECT * FROM t WHERE a = ? AND b = ?", "SELECT * FROM t WHERE a = %s AND b = %s"),
    ("SELECT '?' , \"q?\" FROM t WHERE x = ?", "SELECT '?' , \"q?\" FROM t WHERE x = %s"),
    ("SELECT 'it''s ?' WHERE a LIKE ?", "SELECT 'it''s ?' WHERE a LIKE %s"),
    ("SELECT 1 -- what?\nWHERE a = ?", "SELECT 1 -- what?\nWHERE a = %s"),
    ("SELECT /* ? */ a FROM t WHERE b = ?", "SELECT /* ? */ a FROM t WHERE b = %s"),
    ("SELECT a FROM t WHERE b LIKE 'EV%' AND c = ?", "SELECT a FROM t WHERE b LIKE 'EV%%' AND c = %s"),
    ("SELECT a % 2 FROM t WHERE c = ?", "SELECT a %% 2 FROM t WHERE c = %s"),
])
def test_placeholder_translation(src, out):
    assert PG.to_pyformat(src) == out


def test_statement_rewrites():
    assert PG.rewrite("PRAGMA foreign_keys = ON") == (None, False)
    assert PG.rewrite("BEGIN IMMEDIATE") == ("BEGIN", False)
    assert PG.rewrite("INSERT INTO t(a) VALUES (?)") == ("INSERT INTO t(a) VALUES (?) RETURNING *", True)
    assert PG.rewrite("INSERT INTO t(a) VALUES (?) RETURNING a")[1] is False
    assert PG.rewrite("  insert into t values (1);")[0].endswith("values (1) RETURNING *")
    assert PG.rewrite("UPDATE t SET a = 1") == ("UPDATE t SET a = 1", False)


def test_parameter_normalisation():
    np = pytest.importorskip("numpy")
    assert PG._param(True) == 1 and PG._param(False) == 0
    assert PG._param(np.int64(5)) == 5 and type(PG._param(np.int64(5))) is int
    assert PG._param(np.float64(2.5)) == 2.5 and PG._param(13.0) == 13 and type(PG._param(13.0)) is int
    assert PG._param(float("nan")) is None and PG._param(np.bool_(True)) == 1
    assert PG._param(date(2026, 1, 2)) == "2026-01-02"


def test_row_behaves_like_sqlite_row():
    cur = PG.Cursor(["a", "b"], [(1, "x")], 1)
    row = cur.fetchone()
    assert row[0] == 1 and row["b"] == "x" and row.keys() == ["a", "b"] and dict(row) == {"a": 1, "b": "x"}
    assert cur.fetchone() is None


def test_url_redaction_hides_credentials():
    url = "postgresql://user.secretname:Sup3rS3cret@db.example.supabase.co:6543/postgres?sslmode=require"
    shown = PG.redact(url)
    assert "Sup3rS3cret" not in shown and "secretname" not in shown and "sslmode" not in shown
    assert shown == "postgresql://db.example.supabase.co:6543/postgres"


def test_remote_test_servers_are_refused():
    assert pg_support._is_local("postgresql://u@/postgres?host=/tmp/pg&port=5")
    assert pg_support._is_local("postgresql://u@localhost:5432/x")
    assert not pg_support._is_local("postgresql://u:p@db.abc.supabase.co:5432/postgres")
    assert pg_support._is_local("postgresql://u@pg-prodlike.test:5/x?hostaddr=127.0.0.1")   # named, but local
    assert not pg_support._is_local("postgresql://u@pg-prodlike.test:5/x?hostaddr=10.0.0.9")


# --------------------------------------------------------------------- Step A: SSL, timeout, name, schema
SUPA = "postgresql://eventpulse_app.ref:secret@aws-0-ap-south-1.pooler.supabase.com:6543/postgres"


def _q(url):
    from urllib.parse import parse_qs, urlsplit
    return {k: v[-1] for k, v in parse_qs(urlsplit(url).query).items()}


def test_remote_urls_get_ssl_timeout_and_name():
    q = _q(PG.prepare_url(SUPA, env={}))
    assert q == {"sslmode": "require", "connect_timeout": "10", "application_name": "eventpulse"}
    assert PG.prepare_url(SUPA, env={}).startswith(SUPA + "?")                # credentials/host untouched
    q = _q(PG.prepare_url(SUPA + "?sslmode=verify-full&sslrootcert=/ca.crt", env={}))
    assert q["sslmode"] == "verify-full" and q["sslrootcert"] == "/ca.crt"    # stronger modes kept
    q = _q(PG.prepare_url(SUPA + "?connect_timeout=3&application_name=mine", env={}))
    assert (q["connect_timeout"], q["application_name"]) == ("3", "mine")    # explicit values kept
    # a named host is remote even if hostaddr points to this machine
    assert _q(PG.prepare_url("postgresql://u@db.test/x?hostaddr=127.0.0.1", env={}))["sslmode"] == "require"


@pytest.mark.parametrize("mode", ["disable", "allow", "prefer", "nonsense"])
def test_remote_urls_refuse_weak_sslmode(mode):
    with pytest.raises(PG.PgError, match=f"Refusing sslmode={mode}"):
        PG.prepare_url(SUPA + f"?sslmode={mode}", env={})


def test_local_urls_get_no_forced_ssl():
    for url in ("postgresql://u@/db?host=/tmp/pg&port=5", "postgresql://u@localhost/db",
                "postgresql://u@127.0.0.1:5432/db", "postgresql://u@[::1]:5432/db"):
        q = _q(PG.prepare_url(url, env={}))
        assert "sslmode" not in q and q["connect_timeout"] == "10", url
    assert "sslmode=disable" in PG.prepare_url("postgresql://u@localhost/db?sslmode=disable", env={})
    assert not PG.is_local_url("postgresql://u@/db?host=/tmp/pg,db.remote.test")   # any remote host -> remote


def test_timeout_and_name_settings_are_sanitised():
    t = lambda v: _q(PG.prepare_url(SUPA, env={"EVENTPULSE_DB_CONNECT_TIMEOUT": v}))["connect_timeout"]  # noqa: E731
    assert (t("5"), t("0"), t("999"), t("abc"), t("")) == ("5", "1", "120", "10", "10")
    name = _q(PG.prepare_url(SUPA, env={"EVENTPULSE_DB_APP_NAME": "web app; DROP"}))["application_name"]
    assert name == "web-app--DROP"
    assert len(_q(PG.prepare_url(SUPA, env={"EVENTPULSE_DB_APP_NAME": "x" * 200}))["application_name"]) == 63


def test_schema_safety_rules():
    local = "postgresql://u@/db?host=/tmp/pg"
    PG.check_schema("public", local, env={})                                  # local tests may use public
    PG.check_schema("eventpulse", SUPA, env={})
    with pytest.raises(PG.PgError, match="Refusing to use the 'public' schema"):
        PG.check_schema("public", SUPA, env={})
    PG.check_schema("public", SUPA, env={"EVENTPULSE_DB_ALLOW_PUBLIC_SCHEMA": "1"})
    with pytest.raises(PG.PgError, match="names no existing schema"):
        PG.check_schema(None, SUPA, env={})
    with pytest.raises(PG.PgError, match="EVENTPULSE_DB_SCHEMA is 'eventpulse'"):
        PG.check_schema("other", local, env={"EVENTPULSE_DB_SCHEMA": "eventpulse"})


def test_unreachable_server_gives_friendly_error():
    """A connection failure (or a missing driver) surfaces as the app's DatabaseError, never a
    raw driver exception. No server needed."""
    from core.db.errors import DatabaseError
    with pytest.raises(DatabaseError):
        Database("postgresql://nobody@/nodb?host=/nonexistent-socket-dir&port=1", migrate=False)


# =============================================================================== 2. desktop compatibility
def test_desktop_path_is_unchanged_sqlite(tmp_path):
    """The desktop opener (no URL) still produces SQLite core schema v3, never loads the PostgreSQL
    module, and EVENTPULSE_DB_URL is ignored by Database() itself."""
    code = (
        "import sys, os; os.environ['EVENTPULSE_DB_URL']='postgresql://nobody@remote.invalid/x';"
        "from core.db.connection import Database;"
        f"d = Database(r'{tmp_path / 'desk.db'}');"
        "print(d.dialect, d.scalar('SELECT MAX(version) FROM schema_migrations'),"
        " d.scalar('PRAGMA user_version'), d.scalar('PRAGMA journal_mode'), d.scalar('PRAGMA foreign_keys'),"
        " 'core.db.postgres' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert out.split() == ["sqlite", "3", "3", "wal", "1", "False"]


def test_open_database_honours_env_only_when_set(tmp_path, monkeypatch):
    from core.db.connection import open_database
    monkeypatch.delenv("EVENTPULSE_DB_URL", raising=False)
    with open_database(tmp_path / "a.db") as d:
        assert d.is_sqlite and d.path == tmp_path / "a.db"


# =============================================================================== fixtures (both engines)
def _fresh(kind: str, tmp_path: Path, request) -> Database:
    if kind == "sqlite":
        d = Database(tmp_path / f"parity_{len(list(tmp_path.iterdir()))}.db")
    else:
        url, name = pg_support.create_database()
        request.addfinalizer(lambda: pg_support.drop_database(name))
        d = Database(url)
        d.test_url = url                                     # full local test URL (tests only)
    request.addfinalizer(d.close)
    return d


@pytest.fixture
def pair(tmp_path, request):
    pg_support.server()                                      # skip early when no local server
    return _fresh("sqlite", tmp_path, request), _fresh("postgresql", tmp_path, request)


@pytest.fixture
def pgdb(tmp_path, request):
    pg_support.server()
    return _fresh("postgresql", tmp_path, request)


def _world(db: Database, small_synthetic, trained_models) -> Database:
    """Same SYNTHETIC world on either engine: imported dataset, models, a live event today."""
    from core.db import services
    from core.db.repositories import EventRepository, RegistrationRepository
    services.import_dataset(db, small_synthetic, "SYNTHETIC", "synthetic (parity)")
    services.sync_models(db, trained_models[0])
    ev = EventRepository(db)
    ev.create(event_id="EVQR", name="QR day", event_type="Workshop", event_date=TODAY.isoformat(),
              time_slot="Morning", duration_hours=2, capacity=3, popularity=70,
              registration_deadline=TODAY.isoformat())               # fictional, "real use" event
    for s in ("STU900", "STU901", "STU902"):
        make_student(db, s)                                     # fictional students (tests/conftest.py)
        RegistrationRepository(db).register(s, "EVQR", 3, 0)
    ev.create(event_id="EVF", name="Future", event_type="Seminar", event_date=(TODAY + timedelta(days=3)).isoformat(),
              time_slot="Evening", duration_hours=1, capacity=10)
    return db


@pytest.fixture
def worlds(pair, small_synthetic, trained_models):
    return tuple(_world(d, small_synthetic, trained_models) for d in pair)


def _norm(v):
    if isinstance(v, float):
        return None if math.isnan(v) else round(v, 9)
    if isinstance(v, bool):
        return int(v)
    return v


def _rows(db, sql, params=()):
    return [{k: _norm(v) for k, v in r.items()} for r in db.query(sql, params)]


def _frame(df: pd.DataFrame) -> list[dict]:
    df = df.reset_index(drop=True)
    return [{k: _norm(v.item() if hasattr(v, "item") else v) for k, v in r.items()} for r in df.to_dict("records")]


# =============================================================================== 3. schema
def _sqlite_columns(db, table):
    return [r["name"] for r in db.query(f'PRAGMA table_info("{table}")')]


def _pg_columns(db, table):
    return [r["column_name"] for r in db.query(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() "
        "AND table_name = ? ORDER BY ordinal_position", (table,))]


def test_schema_parity(pair):
    lite, pg = pair
    assert lite.list_tables() == pg.list_tables()
    assert pg.scalar("SELECT MAX(version) FROM schema_migrations") == 3
    for t in lite.list_tables():
        assert _sqlite_columns(lite, t) == _pg_columns(pg, t), t
    lite_views = {r["name"] for r in lite.query("SELECT name FROM sqlite_master WHERE type='view'")}
    pg_views = {r["table_name"] for r in pg.query(
        "SELECT table_name FROM information_schema.views WHERE table_schema = current_schema()")}
    assert lite_views == pg_views == {"v_latest_forecast", "v_registration_features", "v_latest_predictions",
                                      "v_event_participation"}
    for v in lite_views:                                     # identical column names, incl. mixed case
        assert [c[0] for c in lite.execute(f"SELECT * FROM {v} LIMIT 0").description] == \
               [c[0] for c in pg.execute(f"SELECT * FROM {v} LIMIT 0").description], v
    real_cols = pg.query("SELECT data_type FROM information_schema.columns WHERE table_schema = current_schema() "
                         "AND data_type = 'real'")
    assert real_cols == []                                    # no 4-byte floats


def test_no_personal_data_columns_on_postgresql(pgdb):
    pii = {"name_of_student", "student_name", "email", "email_address", "mobile", "mobile_no", "phone",
           "roll_number", "roll_no"}
    core_tables = [t for t in pgdb.list_tables() if t not in ("users", "auth_sessions", "auth_audit")]
    for t in core_tables:
        assert not set(_pg_columns(pgdb, t)) & pii, t


def test_reopen_postgresql_is_idempotent(pgdb):
    from core.db.migrations import migrate
    assert migrate(pgdb) == []
    url = pgdb.conn.url_redacted
    assert "@" not in url


def test_pg_upgrade_of_existing_data_requires_backup_confirmation(tmp_path, request, monkeypatch):
    from core.db.errors import MigrationError
    from core.db.migrations import PG_MIGRATIONS_DIR, migrate
    pg_support.server()
    url, name = pg_support.create_database()
    request.addfinalizer(lambda: pg_support.drop_database(name))
    d = Database(url, migrate=False)
    request.addfinalizer(d.close)
    assert migrate(d, target=1) == [1]
    make_student(d, "STU001")
    monkeypatch.delenv("EVENTPULSE_PG_BACKUP_CONFIRMED", raising=False)
    with pytest.raises(MigrationError, match="pg_dump"):
        migrate(d)
    assert d.scalar("SELECT MAX(version) FROM schema_migrations") == 1            # nothing applied
    monkeypatch.setenv("EVENTPULSE_PG_BACKUP_CONFIRMED", "1")
    assert migrate(d) == [2, 3] and d.scalar("SELECT COUNT(*) FROM students") == 1
    assert PG_MIGRATIONS_DIR.is_dir()


def test_failed_pg_migration_rolls_back_completely(tmp_path, request):
    from core.db.errors import MigrationError
    from core.db.migrations import migrate
    pg_support.server()
    mig = tmp_path / "mig"
    mig.mkdir()
    (mig / "0001_ok.sql").write_text("CREATE TABLE a (x INTEGER);")
    (mig / "0002_bad.sql").write_text("CREATE TABLE b (x INTEGER);\nINSERT INTO nope VALUES (1);")
    url, name = pg_support.create_database()
    request.addfinalizer(lambda: pg_support.drop_database(name))
    d = Database(url, migrate=False)
    request.addfinalizer(d.close)
    with pytest.raises(MigrationError, match="rolled back"):
        migrate(d, directory=mig)
    assert d.table_exists("a") and not d.table_exists("b")
    assert d.scalar("SELECT MAX(version) FROM schema_migrations") == 1


# =============================================================================== 4. constraints
def _attempts(db):
    """Each item: (label, callable) that must be refused."""
    from core.db.repositories import RegistrationRepository
    make_event(db, "EV01", event_date="2026-10-14")
    make_student(db, "STU001")
    rid = RegistrationRepository(db).register("STU001", "EV01", 3)
    ins_student = ("INSERT INTO students(student_id, course_id, year, currently_uba_volunteer, "
                   "participated_uba_event_before, experience_level, area_of_interest, role, "
                   "previous_event_attendance, data_source) "
                   "VALUES (?, (SELECT MIN(course_id) FROM courses), 2, 1, 1, 'Some experience', 'x', 'y', 1, 'MANUAL')")
    ins_event = ("INSERT INTO events(event_id, event_type, event_date, event_day, start_time, time_slot, "
                 "duration_hours, data_source) VALUES (?, 'Workshop', ?, ?, ?, 'Morning', 2, 'MANUAL')")
    return [
        ("email as id", lambda: db.execute(ins_student, ("a@b.c",))),
        ("phone as id", lambda: db.execute(ins_student, ("ID9876543210",))),
        ("roll number as id", lambda: db.execute(ins_student, ("21CS12345",))),
        ("bad calendar date", lambda: db.execute(ins_event, ("EVB1", "2026-02-30", "Monday", None))),
        ("non-ISO date", lambda: db.execute(ins_event, ("EVB2", "14/10/2026", "Wednesday", None))),
        ("weekday mismatch", lambda: db.execute(ins_event, ("EVB3", "2026-10-14", "Monday", None))),
        ("bad start time", lambda: db.execute(ins_event, ("EVB4", "2026-10-14", "Wednesday", "25:61"))),
        ("duplicate registration", lambda: db.execute(
            "INSERT INTO registrations(student_id, event_id, registration_days_before, has_exam_near_event, "
            "data_source) VALUES ('STU001','EV01',1,0,'MANUAL')")),
        ("unknown student", lambda: db.execute(
            "INSERT INTO registrations(student_id, event_id, registration_days_before, has_exam_near_event, "
            "data_source) VALUES ('STU404','EV01',1,0,'MANUAL')")),
        ("raw sheet registered", lambda: db.execute(
            "INSERT INTO datasets(name, kind, source_sheet) VALUES ('x','ORIGINAL','Raw_Merged_Data')")),
        ("boolean out of range", lambda: db.execute(
            "UPDATE registrations SET has_exam_near_event = 2 WHERE registration_id = ?", (rid,))),
        ("event in use deleted", lambda: db.execute("DELETE FROM events WHERE event_id = 'EV01'")),
    ]


def test_constraints_refuse_the_same_bad_data(pair):
    lite, pg = pair
    for (label, bad_l), (_, bad_p) in zip(_attempts(lite), _attempts(pg)):
        with pytest.raises(Exception) as el:
            bad_l()
        with pytest.raises(Exception) as ep:
            bad_p()
        assert type(el.value) is type(ep.value), (label, type(el.value), type(ep.value), str(ep.value))
    assert pg.scalar("SELECT COUNT(*) FROM events") == lite.scalar("SELECT COUNT(*) FROM events") == 1


def test_valid_dates_and_times_accepted(pgdb):
    ins = ("INSERT INTO events(event_id, event_type, event_date, event_day, start_time, time_slot, duration_hours, "
           "data_source) VALUES (?, 'Workshop', ?, ?, ?, 'Morning', 2, 'MANUAL')")
    pgdb.execute(ins, ("EVA", "2024-02-29", "Thursday", "09:30"))
    pgdb.execute(ins, ("EVB", "2026-11-16", "Monday", "18:05:00"))
    pgdb.execute(ins, ("EVC", None, "Friday", None))
    assert pgdb.scalar("SELECT COUNT(*) FROM events") == 3


def test_prediction_history_is_append_only_on_postgresql(worlds):
    from core.db import services
    for db in worlds:
        services.predict_event(db, "EVQR")
        pid = db.scalar("SELECT MIN(prediction_id) FROM predictions")
        with pytest.raises(HistoryProtectedError):
            db.execute("UPDATE predictions SET probability = 0.5 WHERE prediction_id = ?", (pid,))
        with pytest.raises(ConstraintViolationError):          # stored decision must match the threshold
            db.execute("INSERT INTO predictions(run_id, model_version_id, probability, predicted_attend, threshold, "
                       "certainty, forecast_used, input_snapshot) SELECT run_id, model_version_id, 0.9, 0, 0.5, "
                       "'Clear', 'Clear', '{}' FROM predictions WHERE prediction_id = ?", (pid,))


# =============================================================================== 5. transactions
def test_transactions_and_savepoints(pair):
    for db in pair:
        make_student(db, "STU001")
        with db.transaction():
            db.execute("UPDATE students SET year = 3 WHERE student_id = 'STU001'")
            with pytest.raises(DuplicateRecordError):
                with db.transaction():                        # inner failure -> savepoint rolled back
                    db.execute("UPDATE students SET year = 4 WHERE student_id = 'STU001'")
                    make_student(db, "STU001")
        assert db.scalar("SELECT year FROM students WHERE student_id = 'STU001'") == 3
        with pytest.raises(RuntimeError):
            with db.transaction():                            # outer failure -> everything rolled back
                db.execute("UPDATE students SET year = 5 WHERE student_id = 'STU001'")
                make_student(db, "STU002")
                raise RuntimeError("boom")
        assert db.scalar("SELECT year FROM students WHERE student_id = 'STU001'") == 3
        assert db.scalar("SELECT COUNT(*) FROM students") == 1
        assert db._depth == 0


def test_failed_import_rolls_back_on_postgresql(pgdb, small_synthetic):
    from core.db import services
    bad = small_synthetic.copy()
    bad.loc[bad.index[-1], "Student_ID"] = "someone@example.com"
    with pytest.raises(PersonalDataError):
        services.import_dataset(pgdb, bad, "SYNTHETIC", "bad")
    assert pgdb.scalar("SELECT COUNT(*) FROM students") == 0
    assert pgdb.scalar("SELECT COUNT(*) FROM datasets") == 0


# =============================================================================== 6. data, predictions, analytics
def test_import_parity(worlds, small_synthetic):
    lite, pg = worlds
    for sql in ("SELECT COUNT(*) n FROM students", "SELECT COUNT(*) n FROM events",
                "SELECT COUNT(*) n, SUM(attended) a FROM attendance_records",
                "SELECT data_source, COUNT(*) n FROM students GROUP BY data_source ORDER BY data_source",
                "SELECT forecast, COUNT(*) n FROM weather_forecasts GROUP BY forecast ORDER BY forecast"):
        assert _rows(lite, sql) == _rows(pg, sql), sql
    assert lite.scalar("SELECT COUNT(*) FROM students") == small_synthetic.Student_ID.nunique() + 3
    view = 'SELECT * FROM v_registration_features ORDER BY "Student_ID", "Event_ID"'
    a, b = _rows(lite, view), _rows(pg, view)
    drop = {"registration_id", "forecast_id"}                    # surrogate keys may be assigned differently
    assert [{k: v for k, v in r.items() if k not in drop} for r in a] == \
           [{k: v for k, v in r.items() if k not in drop} for r in b]


def test_case_insensitive_search_parity(worlds):
    from core import workflow as W
    from core.db.repositories import EventRepository, StudentRepository
    lite, pg = worlds
    for db in worlds:
        assert len(StudentRepository(db).list(search="stu90")) == 3
        assert {e["event_id"] for e in EventRepository(db).list(search="qr DAY")} == {"EVQR"}
    assert len(W.registration_table(lite, search="evqr")) == len(W.registration_table(pg, search="evqr")) == 3
    assert _frame(W.registration_table(lite, search="STU90").drop(columns=["created_utc"])) == \
           _frame(W.registration_table(pg, search="STU90").drop(columns=["created_utc"]))
    assert _frame(W.department_participation(lite)) == _frame(W.department_participation(pg))


def test_predictions_parity(worlds):
    from core.db import services
    from core.db.repositories import PredictionRepository
    lite, pg = worlds
    ev = lite.scalar("SELECT event_id FROM events WHERE event_id LIKE 'SYN-EV-%' ORDER BY event_id LIMIT 1")
    sl, sp = services.predict_event(lite, ev), services.predict_event(pg, ev)
    assert sl["registrations"] == sp["registrations"] > 0
    assert sl["expected_attendance"] == pytest.approx(sp["expected_attendance"], abs=1e-9)
    hl = sorted(PredictionRepository(lite).history(event_id=ev), key=lambda r: r["registration_id"])
    hp = sorted(PredictionRepository(pg).history(event_id=ev), key=lambda r: r["registration_id"])
    assert [r["probability"] for r in hl] == pytest.approx([r["probability"] for r in hp], abs=1e-12)
    assert [r["predicted_attend"] for r in hl] == [r["predicted_attend"] for r in hp]
    assert all(r["predicted_utc"].endswith("Z") and len(r["predicted_utc"]) == 20 for r in hp)
    assert json.loads(hp[0]["input_snapshot"]).keys() == json.loads(hl[0]["input_snapshot"]).keys()
    services.predict_event(pg, ev)                                # history kept, latest view de-duplicates
    assert len(PredictionRepository(pg).history(event_id=ev)) == 2 * sp["registrations"]
    assert pg.scalar("SELECT COUNT(*) FROM v_latest_predictions JOIN registrations USING(registration_id) "
                     "WHERE event_id = ?", (ev,)) == sp["registrations"]


def test_analytics_parity(worlds):
    from core import analytics as A
    from core.db import services
    lite, pg = worlds
    ev = lite.scalar("SELECT event_id FROM events WHERE event_id LIKE 'SYN-EV-%' ORDER BY event_id LIMIT 1")
    for db in worlds:
        services.predict_event(db, ev)
    kl, kp = A.overview_kpis(lite), A.overview_kpis(pg)
    for f in ("students", "events", "registrations", "predicted_registrations", "predicted_attendees",
              "recorded_attendance_simulated"):
        assert getattr(kl, f) == getattr(kp, f), f
    assert kl.expected_attendance == pytest.approx(kp.expected_attendance, abs=1e-6)
    for dim in ("Department", "Year", "Event_Type", "Event_Day", "Event_Time", "Weather_Forecast", "Course"):
        assert _frame(A.participation_by(lite, dim)) == _frame(A.participation_by(pg, dim)), dim
    cols = ["event_id", "registrations", "expected_attendance_active_model"]
    assert _frame(A.event_trends(lite)[cols]) == _frame(A.event_trends(pg)[cols])
    assert A.probability_distribution(lite) == A.probability_distribution(pg)
    mc_cols = ["model", "version", "active", "baseline", "threshold", "cv_roc_auc", "accuracy", "brier"]
    assert _frame(A.model_comparison(lite)[mc_cols]) == _frame(A.model_comparison(pg)[mc_cols])
    assert _frame(A.recent_predictions(lite)[["probability", "outcome"]].sort_values(["probability", "outcome"])) == \
           _frame(A.recent_predictions(pg)[["probability", "outcome"]].sort_values(["probability", "outcome"]))


def test_insights_and_what_if_parity(worlds):
    from core import intelligence as I
    lite, pg = worlds
    il, ip = I.insights(lite), I.insights(pg)
    assert il["counts"] == ip["counts"]
    assert [(i.title, i.n) for i in il["insights"]] == [(i.title, i.n) for i in ip["insights"]]
    ev = lite.scalar("SELECT event_id FROM events WHERE event_id LIKE 'SYN-EV-%' ORDER BY event_id LIMIT 1")
    wl = I.what_if(lite, ev, {"Has_Exam_Near_Event": 1, "Weather_Forecast": "Rainy"}, registrations=50)
    wp = I.what_if(pg, ev, {"Has_Exam_Near_Event": 1, "Weather_Forecast": "Rainy"}, registrations=50)
    assert wl.modified.mean_probability == pytest.approx(wp.modified.mean_probability, abs=1e-9)


# =============================================================================== 7. attendance / check-in
def test_qr_checkin_finalise_and_correct_on_postgresql(worlds):
    from core import checkin as C
    from core.db import services
    from core.db.errors import NotFoundError
    from core.db.repositories import AttendanceRepository, EventRepository, RegistrationRepository
    results = []
    for db in worlds:
        services.predict_event(db, "EVQR")
        tok = C.get_or_create_token(db, "EVQR")
        assert C.get_or_create_token(db, "EVQR") == tok
        with pytest.raises(ConstraintViolationError):
            C.check_in(db, "EVQR", "STU900", "wrong-token")
        C.check_in(db, "EVQR", "STU900", tok)
        with pytest.raises(DuplicateRecordError):
            C.check_in(db, "EVQR", "STU900", tok)
        with pytest.raises(NotFoundError):
            C.check_in(db, "EVQR", "STU404", tok)
        C.check_in(db, "EVQR", "STU901", method="DESK")
        res = C.finalise_attendance(db, "EVQR", walk_ins=1)
        assert (res["present"], res["marked_absent"]) == (2, 1)
        assert EventRepository(db).get("EVQR")["status"] == "Completed"
        rid = RegistrationRepository(db).find("STU902", "EVQR")["registration_id"]
        C.correct_attendance(db, rid, 1, "Organiser", "arrived late")
        log = C.correction_log(db, "EVQR")
        assert log.iloc[0][["old", "new", "by", "reason"]].tolist() == [0, 1, "Organiser", "arrived late"]
        with pytest.raises(ConstraintViolationError, match="event day"):
            RegistrationRepository(db).register("STU900", "EVF", 1, 0)
            C.check_in(db, "EVF", "STU900", method="DESK")
        results.append((AttendanceRepository(db).outcome("EVQR")["actual_attendees"],
                        sorted(C.attendance_table(db, "EVQR")["outcome"]),
                        _norm(res["evaluations"][0]["absolute_error"])))
    assert results[0] == results[1] and results[0][0] == 3


def test_simulated_outcomes_protected_on_postgresql(worlds):
    from core import checkin as C
    from core.db import services
    from core.db.repositories import AttendanceRepository
    pg = worlds[1]
    ev = pg.scalar("SELECT event_id FROM events WHERE event_id LIKE 'SYN-EV-%' ORDER BY event_id LIMIT 1")
    att = {r["student_id"]: 1 - r["attended"] for r in AttendanceRepository(pg).for_event(ev)}
    with pytest.raises(ConstraintViolationError, match="SIMULATED"):
        services.record_actual_attendance(pg, ev, attended=att)
    with pytest.raises(ConstraintViolationError, match="SIMULATED"):
        C.get_or_create_token(pg, ev)


# =============================================================================== 8. reports
def test_reports_generate_on_postgresql(worlds, tmp_path):
    from openpyxl import load_workbook

    from core import reports as R
    from core.db import services
    pg = worlds[1]
    ev = pg.scalar("SELECT event_id FROM events WHERE event_id LIKE 'SYN-EV-%' ORDER BY event_id LIMIT 1")
    services.predict_event(pg, ev)
    pdf = R.generate_pdf(pg, tmp_path / "r.pdf").read_bytes()
    assert pdf[:5] == b"%PDF-" and pdf.count(b"/Type /Page") - pdf.count(b"/Type /Pages") >= 4
    wb = load_workbook(R.generate_excel(pg, tmp_path / "r.xlsx"))
    assert {"Summary", "Event Analysis", "Prediction History"} <= set(wb.sheetnames)
    assert wb["Event Analysis"].max_row == pg.scalar("SELECT COUNT(*) FROM events") + 1
    assert any("SIMULATED" in s for s in R.collect(pg).findings)


# =============================================================================== 9. auth schema
def test_auth_schema_on_postgresql(pgdb):
    from webapp import auth as A
    assert A.ensure_auth_schema(pgdb) == [1]
    assert A.ensure_auth_schema(pgdb) == []
    assert pgdb.scalar("SELECT MAX(version) FROM schema_migrations") == 3        # core schema untouched
    uid = A.create_admin(pgdb, "admin@eventpulse.test", "Test Admin", "Pw" + "a1b2c3d4e5f6" + "7")
    assert isinstance(uid, int)
    with pytest.raises(PermissionError):
        A.create_admin(pgdb, "second@eventpulse.test", "Second", "Pw" + "a1b2c3d4e5f6" + "8")
    with pytest.raises(Exception):                                               # single-admin index
        pgdb.execute("INSERT INTO users(email, full_name, role, password_hash) VALUES ('x@y.z','X Y','admin',?)",
                     ("h" * 60,))
    pgdb.execute("INSERT INTO users(email, full_name, role, student_id, link_status, password_hash) "
                 "VALUES ('Student@Uni.test','Stu Dent','student','STU029','pending',?)", ("h" * 60,))
    with pytest.raises(DuplicateRecordError):                                    # e-mail unique, any case
        pgdb.execute("INSERT INTO users(email, full_name, role, student_id, link_status, password_hash) "
                     "VALUES ('student@uni.TEST','Other','student','STU030','pending',?)", ("h" * 60,))
    with pytest.raises(DuplicateRecordError):                                    # student id unique, any case
        pgdb.execute("INSERT INTO users(email, full_name, role, student_id, link_status, password_hash) "
                     "VALUES ('o@ncuindia.edu','Other','student','stu029','pending',?)", ("h" * 60,))
    with pytest.raises(ConstraintViolationError, match="roles cannot be changed"):
        pgdb.execute("UPDATE users SET role = 'admin', student_id = NULL, link_status = NULL "
                     "WHERE email = 'Student@Uni.test'")
    hashes = [r["password_hash"] for r in pgdb.query("SELECT password_hash FROM users WHERE role = 'admin'")]
    assert hashes and hashes[0].startswith(("scrypt$", "$argon2"))
    with pytest.raises(RuntimeError, match="pg_dump"):
        A.rollback_auth_schema(pgdb)
    assert pgdb.table_exists("users")


def test_backup_endpoint_refused_on_postgresql_settings_redacted(pgdb, monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import checkin_app
    import webapp.server as S
    from webapp import auth as A
    url, full = pgdb.conn.url_redacted, pgdb.test_url
    monkeypatch.setattr(S, "DB_URL", full)
    monkeypatch.setattr(checkin_app, "DB_PATH", full)
    monkeypatch.setattr(S, "EXPORT_DIR", tmp_path / "exports")
    A.reset_rate_limits()
    pw = "Pw" + "0f1e2d3c4b5a" + "7"
    A.ensure_auth_schema(pgdb)
    A.create_admin(pgdb, "admin@eventpulse.test", "Test Admin", pw)
    with TestClient(S.app) as c:
        r = c.post("/api/auth/login", json={"email": "ADMIN@eventpulse.test", "password": pw})   # case-insensitive
        assert r.status_code == 200
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        assert c.get("/api/health").json() == {"status": "ok"}
        h = c.get("/api/health/details").json()
        assert h["database"] == url and "eptest" not in json.dumps(h)
        s = c.get("/api/settings").json()
        assert s["database"]["engine"] == "postgresql" and s["database"]["integrity"] == "ok"
        assert "eptest" not in json.dumps(s["database"]) and s["backups"] == []
        assert c.post("/api/settings/backup").status_code == 400
        assert c.post("/api/events", json={"event_type": "Seminar", "event_day": "Friday", "time_slot": "Evening",
                                           "duration_hours": 1}).json()["event_id"] == "EV01"
        assert c.post("/api/events", json={"event_type": "Seminar", "event_day": "Friday", "time_slot": "Evening",
                                           "duration_hours": 1}).json()["event_id"] == "EV02"
    A.reset_rate_limits()


# =============================================================================== 10. synthetic demo seeder
def test_demo_seeder_builds_synthetic_only_database_on_both_engines(pair, tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import seed_demo_database as SD
    from core.db import services
    out = []
    for i, db in enumerate(pair):
        r = SD.seed(db, tmp_path / f"models_{i}", students=80, events=8, predict_events=2, progress=lambda m: None)
        assert r["created"] and r["predictions"] > 0 and services.is_demo_database(db)
        assert db.query("SELECT DISTINCT data_source FROM students") == [{"data_source": "SYNTHETIC"}]
        assert db.query("SELECT DISTINCT data_source FROM events") == [{"data_source": "SYNTHETIC"}]
        assert db.query("SELECT DISTINCT source FROM attendance_records") == [{"source": "SYNTHETIC"}]
        assert db.scalar("SELECT COUNT(*) FROM students WHERE student_id NOT LIKE 'SYN-STU-%'") == 0
        assert not db.table_exists("users")                     # no accounts are created
        assert SD.seed(db, tmp_path / f"models_{i}", progress=lambda m: None) == {"created": False}
        out.append(_rows(db, "SELECT COUNT(*) n, SUM(attended) a FROM attendance_records"))
    assert out[0] == out[1]


def test_demo_seeder_refuses_real_database_and_existing_data(tmp_path, db):
    sys.path.insert(0, str(ROOT / "scripts"))
    import seed_demo_database as SD
    from core.db.connection import DEFAULT_DB_PATH
    with pytest.raises(SystemExit, match="database folder"):
        SD.main(["--sqlite", str(DEFAULT_DB_PATH)])
    make_student(db, "STU001")
    with pytest.raises(SystemExit, match="already holds data"):
        SD.seed(db, tmp_path / "m", progress=lambda m: None)


def test_driver_in_use_is_reported(pgdb):
    """Records which driver this run used; with EVENTPULSE_TEST_REQUIRE_PSYCOPG=1 it must be psycopg."""
    import os
    raw = type(pgdb.conn._raw).__name__
    if os.environ.get("EVENTPULSE_TEST_REQUIRE_PSYCOPG") == "1":
        import psycopg
        assert raw == "_PsycopgRaw", raw
        assert pgdb.conn._raw._conn.info.server_version >= 160000
        print(f"\nDRIVER psycopg {psycopg.__version__} ({psycopg.pq.__impl__}, libpq {psycopg.pq.version()}), "
              f"server {pgdb.conn._raw._conn.info.server_version}")
    assert raw in ("_PsycopgRaw", "LibpqRaw")
