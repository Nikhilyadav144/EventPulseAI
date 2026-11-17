"""Production-like PostgreSQL checks (Phase 3, Step A + H1). LOCAL test cluster only.

Runs only with EVENTPULSE_TEST_PG_PRODLIKE=1 (see tests/pg_support.py). The cluster is prepared like
the cloud database: SSL-only TCP with SCRAM passwords, en_US.UTF-8 collation, Supabase-like
anon/authenticated/service_role roles, and the application's own role ``eventpulse_app`` with the
private ``eventpulse`` schema (scripts/postgres/*.sql). The application reaches it through a
non-local host NAME (``pg-prodlike.test``) whose ``hostaddr`` is 127.0.0.1, so the code's
"remote database" rules (automatic sslmode=require, schema checks) are exercised for real.

Only synthetic data is written. Nothing outside the local test cluster is contacted.
"""
from __future__ import annotations

import glob
import hashlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core.db import postgres as PG
from core.db.connection import Database
from core.db.errors import ConstraintViolationError, DatabaseError, HistoryProtectedError
from tests import pg_support
from tests.conftest import make_student

pytestmark = pytest.mark.skipif(not pg_support.prodlike(), reason="production-like PostgreSQL mode not enabled "
                                                                   "(EVENTPULSE_TEST_PG_PRODLIKE=1)")
ROOT = Path(__file__).resolve().parent.parent
API_ROLES = ("anon", "authenticated", "service_role")
APP_TABLES = {"students", "events", "registrations", "attendance_records", "predictions", "model_versions",
              "users", "auth_sessions", "auth_audit", "schema_migrations", "web_auth_migrations", "checkins"}


def _bindir() -> str:
    d = os.environ.get("EVENTPULSE_TEST_PG_BINDIR") or next(iter(sorted(glob.glob("/usr/lib/postgresql/*/bin"))), "")
    if not os.path.exists(os.path.join(d, "pg_dump")):
        pytest.skip("pg_dump/pg_restore not found")
    return d


def _admin(name: str):
    """Administrator connection (local Unix socket) to test database ``name``."""
    return PG.connect(pg_support.admin_url(name))


def _one(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()


@pytest.fixture
def appdb(request):
    pg_support.server()
    url, name = pg_support.create_database()
    request.addfinalizer(lambda: pg_support.drop_database(name))
    d = Database(url)
    request.addfinalizer(d.close)
    d.test_url, d.test_name = url, name
    return d


def _swap_user(url: str, user: str, password_file_env: str) -> str:
    from urllib.parse import quote, urlsplit, urlunsplit
    pw = Path(os.environ[password_file_env]).read_text().strip()
    p = urlsplit(url)
    host = p.netloc.split("@", 1)[1]
    return urlunsplit((p.scheme, f"{user}:{quote(pw, safe='')}@{host}", p.path, p.query, ""))


# =============================================================================== connection: SSL, name, timeout
def test_application_connection_is_encrypted_named_and_least_privileged(appdb):
    assert "sslmode=require" in PG.prepare_url(appdb.test_url)              # added by the application
    assert "sslmode" not in appdb.test_url                                   # ... not by the test URL
    r = appdb.query_one(
        "SELECT s.ssl, s.version, current_user AS usr, current_schema() AS sch, "
        "current_setting('application_name') AS app, r.rolsuper, r.rolcreaterole, r.rolcreatedb, "
        "r.rolbypassrls, d.datcollate "
        "FROM pg_stat_ssl s, pg_roles r, pg_database d "
        "WHERE s.pid = pg_backend_pid() AND r.rolname = current_user AND d.datname = current_database()")
    assert r["ssl"] is True and r["version"].startswith("TLSv1."), dict(r)
    assert (r["usr"], r["sch"], r["app"]) == ("eventpulse_app", "eventpulse", "eventpulse")
    assert not any((r["rolsuper"], r["rolcreaterole"], r["rolcreatedb"], r["rolbypassrls"]))
    assert r["datcollate"] == "en_US.UTF-8"
    print(f"\nSSL {r['version']}, user {r['usr']}, schema {r['sch']}, application_name {r['app']}, "
          f"collation {r['datcollate']}")


def test_application_name_and_timeout_come_from_environment(appdb, monkeypatch):
    monkeypatch.setenv("EVENTPULSE_DB_APP_NAME", "eventpulse-web (test)")
    monkeypatch.setenv("EVENTPULSE_DB_CONNECT_TIMEOUT", "7")
    url = PG.prepare_url(appdb.test_url)
    assert "connect_timeout=7" in url and "application_name=eventpulse-web--test-" in url
    with Database(appdb.test_url, migrate=False) as d:
        assert d.scalar("SELECT current_setting('application_name')") == "eventpulse-web--test-"
        assert d.scalar("SELECT count(*) FROM pg_stat_activity WHERE application_name = 'eventpulse-web--test-' "
                        "AND usename = 'eventpulse_app'") >= 1


@pytest.mark.parametrize("mode", ["disable", "allow", "prefer"])
def test_application_refuses_weak_sslmode_for_remote_host(appdb, mode):
    with pytest.raises(DatabaseError, match=f"Refusing sslmode={mode}"):
        Database(appdb.test_url + f"&sslmode={mode}", migrate=False)


def test_server_itself_rejects_unencrypted_connections(appdb):
    psycopg = pytest.importorskip("psycopg")
    with pytest.raises(psycopg.OperationalError, match="no encryption"):
        psycopg.connect(appdb.test_url + "&sslmode=disable", connect_timeout=5)    # bypasses the app's check


def test_certificate_verification(appdb):
    ca = os.environ.get("EVENTPULSE_TEST_PG_CA_FILE")
    if not ca:
        pytest.skip("EVENTPULSE_TEST_PG_CA_FILE not set")
    with Database(appdb.test_url + f"&sslmode=verify-full&sslrootcert={ca}", migrate=False) as d:
        assert d.scalar("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()") is True
    wrong_name = appdb.test_url.replace("pg-prodlike.test", "impostor.test")
    with pytest.raises(DatabaseError):                                       # certificate is for another name
        Database(wrong_name + f"&sslmode=verify-full&sslrootcert={ca}", migrate=False)


def test_connect_timeout_applies(monkeypatch):
    """A server that accepts TCP but never answers: the connection gives up after connect_timeout."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)                                                            # never accept()-ed / answered
    port = srv.getsockname()[1]
    monkeypatch.setenv("EVENTPULSE_DB_CONNECT_TIMEOUT", "2")
    t0 = time.monotonic()
    try:
        with pytest.raises(DatabaseError):
            Database(f"postgresql://eventpulse_app:x@silent-host.test:{port}/x?hostaddr=127.0.0.1", migrate=False)
    finally:
        srv.close()
    elapsed = time.monotonic() - t0
    print(f"\ngave up after {elapsed:.1f} s (connect_timeout=2)")
    assert 1.5 <= elapsed < 10


# =============================================================================== private schema and permissions
def test_everything_lives_in_the_private_schema(appdb):
    from webapp import auth as A
    assert A.ensure_auth_schema(appdb) == [1]
    adm = _admin(appdb.test_name)
    try:
        owned = adm.execute(
            "SELECT n.nspname AS schema, c.relname AS name, c.relkind AS kind FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace JOIN pg_roles r ON r.oid = c.relowner "
            "WHERE r.rolname = 'eventpulse_app'").fetchall()
        assert {row["schema"] for row in owned} - {"pg_toast"} == {"eventpulse"}   # + TOAST storage
        assert APP_TABLES <= {row["name"] for row in owned if row["kind"] == "r"}
        assert _one(adm, "SELECT count(*) FROM pg_class WHERE relnamespace = 'public'::regnamespace")[0] == 0
        funcs = adm.execute("SELECT p.proname, p.proconfig FROM pg_proc p WHERE p.pronamespace = "
                            "'eventpulse'::regnamespace ORDER BY 1").fetchall()
        assert {f["proname"] for f in funcs} >= {"ep_utc_now", "ep_is_iso_date", "ep_is_time", "ep_is_timestamp",
                                                 "ep_weekday", "ep_predictions_immutable"}
        core_funcs = [f for f in funcs if f["proname"] != "ep_users_role_immutable"]
        assert all(f["proconfig"] == ["search_path=eventpulse"] for f in core_funcs), \
            [(f["proname"], f["proconfig"]) for f in funcs]
        setting = _one(adm, "SELECT s.setconfig FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole "
                            "WHERE r.rolname = 'eventpulse_app' AND s.setdatabase = 0")[0]
        assert setting == ["search_path=eventpulse"]
    finally:
        adm.close()


def test_api_roles_have_no_access_to_eventpulse_data(appdb):
    from webapp import auth as A
    A.ensure_auth_schema(appdb)
    adm = _admin(appdb.test_name)
    try:
        for role in API_ROLES + ("public",):
            if role != "public":
                assert _one(adm, "SELECT has_schema_privilege(?, 'eventpulse', 'USAGE')", (role,))[0] is False
            for t in ("users", "auth_sessions", "students", "registrations", "predictions"):
                grantee = role if role != "public" else "eventpulse_public"
                assert _one(adm, "SELECT has_table_privilege(?, ?, 'SELECT')", (grantee, f"eventpulse.{t}"))[0] \
                    is False, (role, t)
        assert _one(adm, "SELECT has_function_privilege('anon', 'eventpulse.ep_utc_now()', 'EXECUTE')")[0] is False
        for role in API_ROLES:                                               # and actually trying it fails
            adm.execute(f"SET ROLE {role}")
            for sql in [f"SELECT count(*) FROM eventpulse.{t}" for t in ("users", "auth_sessions", "students")] \
                    + ["SELECT eventpulse.ep_utc_now()", "INSERT INTO eventpulse.app_settings(key, value) "
                                                         "VALUES ('x', 'y')"]:
                with pytest.raises(PG.PgError) as e:
                    adm.execute(sql)
                assert e.value.sqlstate == "42501", (role, sql, e.value.message)   # insufficient_privilege
            adm.execute("RESET ROLE")
        # Contrast: what the private schema avoids. A table in "public" is readable by anon here,
        # exactly like Supabase's default grants.
        adm.execute("CREATE TABLE public.exposure_probe (x int)")
        assert _one(adm, "SELECT has_table_privilege('anon', 'public.exposure_probe', 'SELECT')")[0] is True
    finally:
        adm.close()


def test_application_role_cannot_leave_its_schema(appdb):
    attempts = ["CREATE TABLE public.x (a int)", "CREATE ROLE intruder", "ALTER ROLE eventpulse_app SUPERUSER",
                "SELECT rolpassword FROM pg_authid", "CREATE DATABASE other_db", "CREATE SCHEMA other_schema"]
    for sql in attempts:
        with pytest.raises(DatabaseError):
            appdb.execute(sql)
    assert appdb.scalar("SELECT has_schema_privilege('eventpulse_app', 'public', 'CREATE')") is False


def test_application_refuses_public_schema_on_remote_host(monkeypatch):
    monkeypatch.delenv("EVENTPULSE_DB_SCHEMA", raising=False)             # test the default rule itself
    pg_support.server()
    url, name = pg_support.create_database(private_schema=False)
    try:
        public_url = _swap_user(url, "eventpulse_public", "EVENTPULSE_TEST_PG_PUBLIC_PASSWORD_FILE")
        with pytest.raises(DatabaseError, match="Refusing to use the 'public' schema"):
            Database(public_url)                                             # would migrate if allowed
        adm = _admin(name)
        try:
            assert _one(adm, "SELECT count(*) FROM pg_class WHERE relnamespace = 'public'::regnamespace")[0] == 0
        finally:
            adm.close()
        monkeypatch.setenv("EVENTPULSE_DB_ALLOW_PUBLIC_SCHEMA", "1")          # explicit opt-out exists
        with Database(public_url, migrate=False) as d:
            assert d.scalar("SELECT current_schema()") == "public"
    finally:
        pg_support.drop_database(name)


def test_missing_or_unexpected_schema_is_refused(appdb, monkeypatch):
    url, name = pg_support.create_database(private_schema=False)              # schema not created
    try:
        with pytest.raises(DatabaseError, match="names no existing schema"):
            Database(url)
    finally:
        pg_support.drop_database(name)
    monkeypatch.setenv("EVENTPULSE_DB_SCHEMA", "something_else")
    with pytest.raises(DatabaseError, match="EVENTPULSE_DB_SCHEMA is 'something_else'"):
        Database(appdb.test_url, migrate=False)
    monkeypatch.setenv("EVENTPULSE_DB_SCHEMA", "eventpulse")
    with Database(appdb.test_url, migrate=False) as d:
        assert d.conn.schema == "eventpulse"


# =============================================================================== backup and restore
def _fingerprint(db: Database) -> dict[str, tuple[int, str]]:
    out = {}
    for t in db.list_tables():
        rows = sorted(repr(tuple(r)) for r in db.query(f'SELECT * FROM "{t}"'))
        out[t] = (len(rows), hashlib.sha256("\n".join(rows).encode()).hexdigest())
    return out


def _dump(name: str, path: Path) -> subprocess.CompletedProcess:
    sock = pg_support.admin_url(name)
    from urllib.parse import parse_qs, urlsplit
    q = parse_qs(urlsplit(sock).query)
    return subprocess.run([f"{_bindir()}/pg_dump", "-h", q["host"][0], "-p", q["port"][0], "-U", "eptest",
                           "-d", name, "-n", "eventpulse", "-Fc", "-f", str(path)], capture_output=True, text=True)


def _restore(name: str, path: Path) -> subprocess.CompletedProcess:
    from urllib.parse import parse_qs, urlsplit
    q = parse_qs(urlsplit(pg_support.admin_url(name)).query)
    return subprocess.run([f"{_bindir()}/pg_restore", "-h", q["host"][0], "-p", q["port"][0], "-U", "eptest",
                           "-d", name, "--exit-on-error", "--single-transaction", str(path)],
                          capture_output=True, text=True)


INS_EVENT = ("INSERT INTO events(event_id, event_type, event_date, event_day, start_time, time_slot, duration_hours, "
             "data_source) VALUES (?, 'Workshop', '2026-01-02', 'Friday', ?, 'Morning', 2, 'MANUAL')")


def test_pg_dump_and_restore_round_trip(tmp_path, request):
    """Synthetic database -> pg_dump (private schema) -> restore into an empty database -> identical
    data, privileges, constraints, triggers, sequences and logins."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import seed_demo_database as SD

    from core import analytics
    from core import checkin as C
    from core.db.repositories import EventRepository, RegistrationRepository
    from webapp import auth as A
    _bindir()
    url_a, a = pg_support.create_database()
    request.addfinalizer(lambda: pg_support.drop_database(a))
    pw = "Pw" + os.urandom(6).hex() + "7"
    with Database(url_a) as da:
        SD.seed(da, tmp_path / "models", students=120, events=10, predict_events=3, progress=lambda m: None)
        A.ensure_auth_schema(da)
        A.create_admin(da, "admin@demo.test", "Demo Admin", pw)
        token, _ = A.create_session(da, da.scalar("SELECT user_id FROM users WHERE role = 'admin'"))
        da.execute(INS_EVENT, ("EVDT", "2026-01-02T09:30"))                 # start_time needs ep_is_timestamp
        from datetime import date
        EventRepository(da).create(event_id="EVLIVE", name="Live", event_type="Workshop",
                                   event_date=date.today().isoformat(), time_slot="Morning", duration_hours=1)
        make_student(da, "STU900")
        RegistrationRepository(da).register("STU900", "EVLIVE", 1, 0)
        C.check_in(da, "EVLIVE", "STU900", method="DESK")
        before, kpi_a = _fingerprint(da), analytics.overview_kpis(da)
        max_reg = da.scalar("SELECT MAX(registration_id) FROM registrations")

    dump = tmp_path / "eventpulse.dump"
    r = _dump(a, dump)
    assert r.returncode == 0, r.stderr
    url_b, b = pg_support.create_database(private_schema=False)              # completely empty database
    request.addfinalizer(lambda: pg_support.drop_database(b))
    r = _restore(b, dump)
    assert r.returncode == 0, r.stderr
    print(f"\ndump {dump.stat().st_size} bytes, {len(before)} tables, "
          f"{sum(n for n, _ in before.values())} rows restored")

    with Database(url_b) as db_:                                             # migrations: nothing to do
        assert _fingerprint(db_) == before
        from core.db.migrations import migrate
        assert migrate(db_) == [] and A.ensure_auth_schema(db_) == []
        hash_ = db_.scalar("SELECT password_hash FROM users WHERE role = 'admin'")
        assert A.verify_password(pw, hash_) and not A.verify_password(pw + "x", hash_)
        assert A.load_session(db_, token) is not None                        # sessions survive too
        k = analytics.overview_kpis(db_)
        assert (k.students, k.events, k.registrations) == (kpi_a.students, kpi_a.events, kpi_a.registrations)
        with pytest.raises(ConstraintViolationError):                        # CHECK constraints still enforced
            db_.execute(INS_EVENT, ("EVBAD", "25:99"))
        with pytest.raises(HistoryProtectedError):                           # trigger still enforced
            db_.execute("UPDATE predictions SET probability = 0.5 WHERE prediction_id = "
                        "(SELECT MIN(prediction_id) FROM predictions)")
        make_student(db_, "STU901")
        assert RegistrationRepository(db_).register("STU901", "EVLIVE", 1, 0) > max_reg   # sequences continue
    adm = _admin(b)
    try:                                                                     # privileges restored as well
        assert _one(adm, "SELECT has_schema_privilege('anon', 'eventpulse', 'USAGE')")[0] is False
        assert _one(adm, "SELECT n.nspname FROM pg_namespace n JOIN pg_roles r ON r.oid = n.nspowner "
                         "WHERE r.rolname = 'eventpulse_app'")[0] == "eventpulse"
    finally:
        adm.close()


def test_restore_would_fail_without_the_search_path_fix(tmp_path, request):
    """Negative control: the same schema WITHOUT 'SET search_path FROM CURRENT' cannot be restored
    once a row relies on a helper calling another helper (ep_is_time -> ep_is_timestamp)."""
    from core.db.migrations import PG_MIGRATIONS_DIR, migrate
    _bindir()
    old = tmp_path / "old_migrations"
    old.mkdir()
    for f in sorted(PG_MIGRATIONS_DIR.glob("*.sql")):
        (old / f.name).write_text(f.read_text().replace(" SET search_path FROM CURRENT", ""))
    assert "FROM CURRENT" not in (old / "0001_initial_schema.sql").read_text()
    url_c, c = pg_support.create_database()
    request.addfinalizer(lambda: pg_support.drop_database(c))
    with Database(url_c, migrate=False) as dc:
        migrate(dc, directory=old)
        dc.execute(INS_EVENT, ("EVDT", "2026-01-02T09:30"))                  # accepted at insert time
    dump = tmp_path / "old.dump"
    assert _dump(c, dump).returncode == 0
    url_d, d = pg_support.create_database(private_schema=False)
    request.addfinalizer(lambda: pg_support.drop_database(d))
    r = _restore(d, dump)
    assert r.returncode != 0 and "check constraint" in r.stderr, r.stderr
    print("\nwithout the fix, pg_restore failed as expected: "
          + next(line for line in r.stderr.splitlines() if "check constraint" in line).strip())
