"""Phase 3 tests: hosted PostgreSQL initialisation, migrations, admin provisioning, synthetic demo
seed and hosted models, backup/restore, IST check-in dates, production settings and log redaction.

PostgreSQL-only tests run against FRESH local test databases (never a real or hosted database) and
are skipped - with the reason - in SQLite mode. The real local SQLite database is never opened.
"""
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from tests import pg_support  # noqa: E402
from tests.test_auth import _pw, admin_client, env, login, register  # noqa: E402,F401
from webapp import security as SEC  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PG_ONLY = pytest.mark.skipif(pg_support.engine() != "postgresql",
                             reason="hosted-database test: runs in PostgreSQL test mode (the desktop uses SQLite)")
BACKUP_TOOLS = all(shutil.which(t) for t in ("pg_dump", "pg_restore", "psql"))


# ============================================================================ helpers / fixtures
@pytest.fixture()
def fresh_pg(request):
    """A brand-new, empty PostgreSQL test database (application URL, name)."""
    url, name = pg_support.create_database()
    request.addfinalizer(lambda: pg_support.drop_database(name))
    return url, name


@pytest.fixture(scope="module")
def hosted_models(tmp_path_factory):
    """Models built exactly as the deployment build step does (synthetic data only)."""
    if pg_support.engine() != "postgresql":
        pytest.skip("hosted models are only used with the hosted PostgreSQL database")
    from webapp import hosted_setup
    out = tmp_path_factory.mktemp("hosted") / "models_hosted"
    assert hosted_setup.main(["build-models", "--out", str(out)]) == 0
    return out


def frozen_clock(monkeypatch, instant: datetime):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant if tz is None else instant.astimezone(tz)
    monkeypatch.setattr(SEC, "datetime", Frozen)


def hosted_app(monkeypatch, url, extra_env=None):
    """The web app pointed at a hosted (PostgreSQL) database, with bootstrap variables set."""
    import webapp.server as S
    from webapp import auth as A
    monkeypatch.setattr(S, "DB_URL", url)
    for k in ("EVENTPULSE_ADMIN_EMAIL", "EVENTPULSE_ADMIN_NAME", "EVENTPULSE_ADMIN_PASSWORD",
              "EVENTPULSE_SEED_SYNTHETIC_DEMO", "EVENTPULSE_HOSTED_MODELS_DIR"):
        monkeypatch.delenv(k, raising=False)
    for k, v in (extra_env or {}).items():
        monkeypatch.setenv(k, v)
    A._ENSURED.clear()
    A.reset_rate_limits()
    return S


# ============================================================================ 1. configuration
def test_postgres_scheme_is_accepted_and_sqlite_stays_default(monkeypatch):
    from core.db.connection import web_database_url
    assert web_database_url({}) is None                                       # unset -> SQLite (desktop)
    for u in ("postgresql://u@h/db", "postgres://u@h/db"):
        assert web_database_url({"EVENTPULSE_DB_URL": u}) == u
    with pytest.raises(ValueError):
        web_database_url({"EVENTPULSE_DB_URL": "database/eventpulse.db"})


@pytest.mark.parametrize("change, message", [
    ({"WEB_CONCURRENCY": "2"}, "WEB_CONCURRENCY"),
    ({"EVENTPULSE_TIMEZONE": "Mars/Olympus"}, "EVENTPULSE_TIMEZONE"),
])
def test_production_refuses_multi_worker_and_bad_timezone(change, message):
    env = {"EVENTPULSE_ENV": "production", "EVENTPULSE_PUBLIC_BASE_URL": "https://x.onrender.com",
           "EVENTPULSE_TRUSTED_PROXIES": "*", "EVENTPULSE_DB_URL": "postgresql://u@db.example/x", **change}
    with pytest.raises(SEC.ConfigError) as e:
        SEC.load_config(env)
    assert message in str(e.value)
    ok = {k: v for k, v in env.items() if k not in change}
    assert SEC.load_config({**ok, "WEB_CONCURRENCY": "1"}).timezone_name == "Asia/Kolkata"


def test_check_config_command(monkeypatch, capsys):
    from webapp import hosted_setup
    for k in list(os.environ):
        if k.startswith("EVENTPULSE_") and k not in ("EVENTPULSE_TEST_ENGINE",):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("EVENTPULSE_ENV", "production")
    assert hosted_setup.main(["check-config"]) == 1
    assert "EVENTPULSE_PUBLIC_BASE_URL" in capsys.readouterr().err
    monkeypatch.setenv("EVENTPULSE_PUBLIC_BASE_URL", "https://eventpulse-demo.onrender.com")
    monkeypatch.setenv("EVENTPULSE_TRUSTED_PROXIES", "*")
    monkeypatch.setenv("EVENTPULSE_DB_URL", "postgresql://u:secret-pw@db.example/x")
    assert hosted_setup.main(["check-config"]) == 0
    out = capsys.readouterr().out
    assert '"production": true' in out and "secret-pw" not in out


# ============================================================================ 2. timezone (IST)
def test_local_date_is_ist_whatever_the_server_zone():
    cfg = SEC.load_config({})
    assert SEC.local_today(cfg, datetime(2026, 11, 17, 18, 29, 59, tzinfo=timezone.utc)) == date(2026, 11, 17)
    assert SEC.local_today(cfg, datetime(2026, 11, 17, 18, 30, 0, tzinfo=timezone.utc)) == date(2026, 11, 18)
    assert SEC.local_now(cfg, datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)).utcoffset().total_seconds() == 19800


def test_ist_fallback_without_tz_database(monkeypatch):
    import zoneinfo
    def missing(name):
        raise zoneinfo.ZoneInfoNotFoundError(name)
    monkeypatch.setattr(zoneinfo, "ZoneInfo", missing)
    assert SEC.zone_for("Asia/Kolkata") is SEC.IST
    with pytest.raises(SEC.ConfigError):
        SEC.zone_for("Europe/Paris")


def test_qr_and_desk_checkin_follow_ist_midnight(env, monkeypatch):
    """Server clock in UTC: 18:29:59Z is still 17 Nov in India, 18:30:00Z is 18 Nov."""
    import re as _re
    from tests.test_auth import student_client
    admin = admin_client(env)
    ev = admin.post("/api/events", json={"event_type": "Workshop", "event_date": "2026-11-18", "time_slot": "Morning",
                                         "duration_hours": 1, "name": "Midnight test"}).json()
    eid = ev["event_id"]
    for sid in ("STU029", "STU089"):
        assert admin.post(f"/api/events/{eid}/registrations",
                          json={"student_id": sid, "registration_days_before": 3}).status_code == 201
    token = admin.post(f"/api/attendance/{eid}/qr").json()["url"].split("t=")[1]
    stu = student_client(env, "midnight@ncuindia.edu", "STU029", verify_with=admin)
    csrf = _re.search(r'name="csrf_token" value="([^"]+)"', stu.get(f"/checkin/{eid}?t={token}").text).group(1)

    frozen_clock(monkeypatch, datetime(2026, 11, 17, 18, 29, 59, tzinfo=timezone.utc))   # 23:59:59 IST, 17 Nov
    early = stu.post(f"/checkin/{eid}", data={"t": token, "csrf_token": csrf})
    assert early.status_code == 400 and "event day only (2026-11-18)" in early.text
    assert admin.post(f"/api/attendance/{eid}/checkin", json={"student_id": "STU089"}).status_code == 400

    frozen_clock(monkeypatch, datetime(2026, 11, 17, 18, 30, 0, tzinfo=timezone.utc))    # 00:00:00 IST, 18 Nov
    ok = stu.post(f"/checkin/{eid}", data={"t": token, "csrf_token": csrf})
    assert ok.status_code == 200 and "Checked in" in ok.text and "(IST)" in ok.text   # time shown in IST
    assert admin.post(f"/api/attendance/{eid}/checkin", json={"student_id": "STU089"}).status_code == 200

    frozen_clock(monkeypatch, datetime(2026, 11, 18, 18, 30, 0, tzinfo=timezone.utc))    # 00:00 IST, 19 Nov
    stu3 = student_client(env, "late.night@ncuindia.edu", "STU001", verify_with=admin)
    assert admin.post(f"/api/events/{eid}/registrations",
                      json={"student_id": "STU001", "registration_days_before": 3}).status_code == 201
    csrf3 = _re.search(r'name="csrf_token" value="([^"]+)"', stu3.get(f"/checkin/{eid}?t={token}").text).group(1)
    assert stu3.post(f"/checkin/{eid}", data={"t": token, "csrf_token": csrf3}).status_code == 400   # day is over


def test_apply_process_timezone_sets_ist():
    import time
    original = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "UTC"
        time.tzset()
        assert time.localtime(0).tm_gmtoff == 0
        tz = SEC.apply_process_timezone(SEC.load_config({}))
        assert tz in ("Asia/Kolkata", "IST-5:30")
        assert time.localtime(0).tm_gmtoff == 19800
    finally:                                   # restore the test process's own zone exactly
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()


# ============================================================================ 3. logs never hold secrets
def test_redaction_patterns():
    line = ('203.0.113.7:5555 - "GET /checkin/EV01?t=Zx9_secretQR HTTP/1.1" 200 '
            "postgresql://eventpulse:VerySecret1@db.internal/eventpulse password=hunter22 ep_session=abc.def")
    out = SEC.redact(line)
    for secret in ("Zx9_secretQR", "VerySecret1", "hunter22", "abc.def"):
        assert secret not in out
    assert "/checkin/EV01?t=[redacted]" in out and "postgresql://eventpulse:[redacted]@db.internal" in out


def test_uvicorn_access_records_are_redacted_and_still_format(caplog):
    SEC.install_log_redaction()
    SEC.install_log_redaction()                                   # idempotent
    lg = logging.getLogger("uvicorn.access")
    assert lg.filters.count(SEC._FILTER) == 1
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:5", "GET", "/checkin/EV01?t=SECRETTOKEN&x=1", "1.1", 200), None)
    assert SEC._FILTER.filter(rec)
    msg = rec.getMessage()
    assert "SECRETTOKEN" not in msg and "/checkin/EV01?t=[redacted]&x=1" in msg and rec.args[4] == 200


def test_bootstrap_errors_and_logs_never_contain_the_password(caplog, tmp_path):
    from core.db.connection import Database
    from webapp import auth as A
    from webapp import bootstrap as BOOT
    weak = "short1"
    with Database(tmp_path / "scratch.db") as db:                  # throwaway file, not the desktop DB
        A.ensure_auth_schema(db)
        with pytest.raises(BOOT.BootstrapError) as e:
            BOOT.bootstrap_admin(db, {"EVENTPULSE_ADMIN_EMAIL": "admin@ncuindia.edu", "EVENTPULSE_ADMIN_PASSWORD": weak})
        assert not A.admin_exists(db)
    assert weak not in str(e.value) and weak not in caplog.text


# ============================================================================ 4. fresh database initialisation
@PG_ONLY
def test_fresh_hosted_database_initialisation_is_complete_and_idempotent(fresh_pg, capsys):
    from core.db.connection import Database
    from webapp import hosted_setup
    url, _ = fresh_pg
    assert hosted_setup.main(["init-db", "--db-url", url]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["core_schema"] == 3 and first["web_auth_schema"] == 1 and first["admin_exists"] is False
    with Database(url=url) as db:
        mig = db.query("SELECT version, checksum FROM schema_migrations ORDER BY version")
        auth = db.query("SELECT version, checksum FROM web_auth_migrations ORDER BY version")
        counts = {t: db.scalar(f"SELECT COUNT(*) FROM {t}") for t in ("students", "events", "registrations",
                                                                     "users", "model_versions", "predictions")}
    assert counts == dict.fromkeys(counts, 0)                       # fresh start: no records imported
    assert hosted_setup.main(["init-db", "--db-url", url]) == 0      # second run changes nothing
    capsys.readouterr()
    with Database(url=url) as db:
        assert db.query("SELECT version, checksum FROM schema_migrations ORDER BY version") == mig
        assert db.query("SELECT version, checksum FROM web_auth_migrations ORDER BY version") == auth
        fn = db.scalar("SELECT array_to_string(proconfig, ',') FROM pg_proc WHERE proname = 'ep_users_role_immutable'")
    assert fn and fn.startswith("search_path=")                     # trigger function search_path pinned
    assert hosted_setup.main(["status", "--db-url", url]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["students"] == 0 and st["admin_exists"] is False and st["core_schema"] == 3


@PG_ONLY
def test_init_db_refuses_without_explicit_postgres_url(monkeypatch):
    from webapp import hosted_setup
    monkeypatch.delenv("EVENTPULSE_DB_URL", raising=False)
    with pytest.raises(SystemExit) as e:
        hosted_setup.main(["init-db"])
    assert "EVENTPULSE_DB_URL" in str(e.value)


@PG_ONLY
def test_failed_web_auth_migration_rolls_back_completely(fresh_pg, tmp_path, monkeypatch):
    from core.db.connection import Database
    from webapp import auth as A
    url, _ = fresh_pg
    with Database(url=url) as db:
        A.ensure_auth_schema(db)
        assert A.ensure_auth_schema(db) == []                       # idempotent
    mig = tmp_path / "auth_migrations"
    shutil.copytree(A.MIGRATIONS_DIR, mig)
    (mig / "postgres" / "0002_broken.sql").write_text(
        "CREATE TABLE half_done (x INTEGER);\nINSERT INTO no_such_table VALUES (1);\n")
    monkeypatch.setattr(A, "MIGRATIONS_DIR", mig)
    monkeypatch.setenv("EVENTPULSE_PG_BACKUP_CONFIRMED", "1")
    with Database(url=url) as db:
        with pytest.raises(RuntimeError, match="rolled back"):
            A.ensure_auth_schema(db)
        assert not db.table_exists("half_done")
        assert db.scalar("SELECT MAX(version) FROM web_auth_migrations") == 1


# ============================================================================ 5. admin provisioning
@PG_ONLY
def test_admin_bootstrap_from_secret_environment(fresh_pg, caplog):
    from core.db.connection import Database
    from webapp import auth as A
    from webapp import bootstrap as BOOT
    url, _ = fresh_pg
    pw = _pw()
    env = {"EVENTPULSE_ADMIN_EMAIL": "organiser@ncuindia.edu", "EVENTPULSE_ADMIN_NAME": "Event Organiser",
           "EVENTPULSE_ADMIN_PASSWORD": pw}
    with Database(url=url) as db:
        A.ensure_auth_schema(db)
        assert BOOT.run_startup_bootstrap(db, {}) == {"admin": "not-configured"}
        with pytest.raises(BOOT.BootstrapError):
            BOOT.bootstrap_admin(db, {"EVENTPULSE_ADMIN_PASSWORD": pw})                # missing e-mail
        assert not A.admin_exists(db)
        assert BOOT.bootstrap_admin(db, env) == "created"
        h1 = db.scalar("SELECT password_hash FROM users WHERE role = 'admin'")
        assert BOOT.bootstrap_admin(db, {**env, "EVENTPULSE_ADMIN_PASSWORD": _pw()}) == "exists"   # never reset
        assert db.scalar("SELECT password_hash FROM users WHERE role = 'admin'") == h1
        assert db.scalar("SELECT COUNT(*) FROM users WHERE role = 'admin'") == 1
        assert A.verify_password(pw, h1) and pw not in h1
    assert pw not in caplog.text and "Delete EVENTPULSE_ADMIN_PASSWORD" in caplog.text


@PG_ONLY
def test_weak_bootstrap_password_stops_startup(fresh_pg, monkeypatch):
    url, _ = fresh_pg
    S = hosted_app(monkeypatch, url, {"EVENTPULSE_ADMIN_EMAIL": "organiser@ncuindia.edu",
                                      "EVENTPULSE_ADMIN_PASSWORD": "weakpass"})
    from webapp.bootstrap import BootstrapError
    with pytest.raises(BootstrapError) as e:
        with TestClient(S.app):
            pass
    assert "weakpass" not in str(e.value)


@PG_ONLY
def test_provision_admin_cli_on_postgres(fresh_pg, monkeypatch, capsys):
    from webapp import provision_admin
    url, _ = fresh_pg
    pw = _pw()
    monkeypatch.setenv("EVENTPULSE_ADMIN_EMAIL", "cli.admin@ncuindia.edu")
    monkeypatch.setenv("EVENTPULSE_ADMIN_NAME", "CLI Admin")
    monkeypatch.setenv("EVENTPULSE_ADMIN_PASSWORD", pw)
    assert provision_admin.main(["--db-url", url, "--from-env"]) == 0
    assert provision_admin.main(["--db-url", url, "--from-env"]) == 3          # second admin refused
    out = capsys.readouterr().out
    assert pw not in out and "already exists" in out


def test_startup_bootstrap_never_touches_sqlite(tmp_path):
    from core.db.connection import Database
    from webapp import bootstrap as BOOT
    with Database(tmp_path / "desk.db") as db:
        res = BOOT.run_startup_bootstrap(db, {"EVENTPULSE_ADMIN_EMAIL": "a@ncuindia.edu",
                                              "EVENTPULSE_ADMIN_PASSWORD": _pw(), "EVENTPULSE_SEED_SYNTHETIC_DEMO": "1"})
        assert "skipped" in res and db.scalar("SELECT COUNT(*) FROM students") == 0
        assert not db.table_exists("users")


# ============================================================================ 6. demo seed + hosted models
def test_build_models_refuses_desktop_folders(tmp_path):
    from config import settings
    from webapp import hosted_setup
    for bad in (settings.MODELS_DIR, settings.MODELS_DIR / "x", settings.PROJECT_ROOT / "model", settings.DATABASE_DIR):
        with pytest.raises(SystemExit, match="Refusing"):
            hosted_setup.main(["build-models", "--out", str(bad)])
    (tmp_path / "busy").mkdir()
    (tmp_path / "busy" / "f").write_text("x")
    with pytest.raises(SystemExit, match="not empty"):
        hosted_setup.main(["build-models", "--out", str(tmp_path / "busy")])


@PG_ONLY
def test_hosted_end_to_end_fresh_db_seed_models_registration_approval_isolation(fresh_pg, hosted_models, monkeypatch):
    """Fresh hosted DB -> app start (migrations, admin from secrets, synthetic demo seed, synthetic
    models) -> students register with college e-mail -> pending -> admin approves -> each student
    sees only their own records; admin sees research/admin pages and predictions work."""
    url, _ = fresh_pg
    admin_pw = _pw()
    S = hosted_app(monkeypatch, url, {"EVENTPULSE_ADMIN_EMAIL": "organiser@ncuindia.edu",
                                      "EVENTPULSE_ADMIN_PASSWORD": admin_pw,
                                      "EVENTPULSE_SEED_SYNTHETIC_DEMO": "1",
                                      "EVENTPULSE_HOSTED_MODELS_DIR": str(hosted_models)})
    with TestClient(S.app) as admin, TestClient(S.app) as a, TestClient(S.app) as b:
        assert login(admin, "organiser@ncuindia.edu", admin_pw).status_code == 200
        details = admin.get("/api/health/details").json()
        assert details["demo"] is True and details["active_model"]
        studs = admin.get("/api/students").json()
        assert studs and all(s["student_id"].startswith("SYN-") for s in studs)      # synthetic only
        sid_a, sid_b = studs[0]["student_id"], studs[1]["student_id"]
        pa, pb = _pw(), _pw()
        assert register(a, "student.a@gmail.com", sid_a, pa).status_code == 422          # college e-mail only
        assert register(a, "student.a@ncuindia.edu", sid_a, pa).status_code == 201
        assert register(b, "student.b@ncuindia.edu", sid_b, pb).status_code == 201
        assert login(a, "student.a@ncuindia.edu", pa).status_code == 200
        assert login(b, "student.b@ncuindia.edu", pb).status_code == 200
        assert a.get("/api/student/registrations").json()["rows"] == []                # pending: nothing
        accts = {u["email"]: u for u in admin.get("/api/admin/accounts").json()}
        assert {accts["student.a@ncuindia.edu"]["link_status"], accts["student.b@ncuindia.edu"]["link_status"]} == {"pending"}
        for e in ("student.a@ncuindia.edu", "student.b@ncuindia.edu"):
            assert admin.post(f"/api/admin/accounts/{accts[e]['user_id']}", json={"action": "verify"}).status_code == 200
        ra, rb = a.get("/api/student/registrations").json(), b.get("/api/student/registrations").json()
        assert ra["rows"] and rb["rows"]
        assert sid_b not in json.dumps(ra) and sid_a not in json.dumps(rb)              # own records only
        assert a.get(f"/api/admin/students/{sid_b}").status_code == 403
        assert a.get("/api/overview").status_code == 403
        assert admin.get(f"/api/admin/students/{sid_b}").status_code == 200            # admin: authorised
        for p in ("/api/overview", "/api/models", "/api/analytics?dimension=Department", "/api/insights"):
            assert admin.get(p).status_code == 200, p
        ev = admin.get("/api/events").json()
        eid = (ev["rows"] if isinstance(ev, dict) else ev)[0]["event_id"]
        assert admin.post(f"/api/events/{eid}/predict", json={}).status_code == 200    # model works
    # restart: everything is idempotent (no second seed, no new model versions, admin unchanged)
    from core.db.connection import Database
    with Database(url=url) as db:
        before = (db.scalar("SELECT COUNT(*) FROM students"), db.scalar("SELECT COUNT(*) FROM model_versions"),
                  db.scalar("SELECT password_hash FROM users WHERE role = 'admin'"))
    with TestClient(S.app):
        pass
    with Database(url=url) as db:
        after = (db.scalar("SELECT COUNT(*) FROM students"), db.scalar("SELECT COUNT(*) FROM model_versions"),
                 db.scalar("SELECT password_hash FROM users WHERE role = 'admin'"))
    assert before == after


@PG_ONLY
def test_seed_refuses_non_synthetic_data_and_non_empty_databases(fresh_pg, tmp_path):
    import pandas as pd
    from config import settings
    from core.db.connection import Database
    from webapp import bootstrap as BOOT
    url, _ = fresh_pg
    df = pd.read_csv(settings.SYNTHETIC_DATASET).head(20)
    df.loc[0, "Data_Source"] = "ORIGINAL"
    bad = tmp_path / "mixed.csv"
    df.to_csv(bad, index=False)
    with Database(url=url) as db:
        with pytest.raises(BOOT.BootstrapError):
            BOOT.seed_synthetic_demo(db, bad)
        assert db.scalar("SELECT COUNT(*) FROM students") == 0
        assert BOOT.seed_synthetic_demo(db)["seeded"] is True
        assert BOOT.seed_synthetic_demo(db)["seeded"] is False                   # already has records


@PG_ONLY
def test_hosted_models_must_be_synthetic(fresh_pg, trained_models, tmp_path):
    from core.db.connection import Database
    from webapp import bootstrap as BOOT
    url, _ = fresh_pg
    fake = tmp_path / "m"
    shutil.copytree(trained_models[0], fake)
    for meta in fake.glob("*/metadata.json"):
        m = json.loads(meta.read_text())
        m["training_data"] = {"label": "ORIGINAL survey data"}
        meta.write_text(json.dumps(m))
    with Database(url=url) as db:
        with pytest.raises(BOOT.BootstrapError, match="synthetic"):
            BOOT.register_hosted_models(db, fake)


# ============================================================================ 7. backup and restore
def _pg_backup():
    sys.path.insert(0, str(ROOT / "scripts" / "postgres"))
    try:
        import pg_backup
    finally:
        sys.path.pop(0)
    return pg_backup


def test_backups_are_refused_inside_the_project():
    pb = _pg_backup()
    with pytest.raises(pb.BackupError, match="project folder"):
        pb._private_dir(ROOT / "backups")
    with pytest.raises(pb.BackupError, match="sslmode"):
        pb.pg_env("postgresql://u:p@db.example.com/x?sslmode=disable")
    env = pb.pg_env("postgresql://u:s3cret%40x@db.example.com:6543/evdb")
    assert env["PGPASSWORD"] == "s3cret@x" and env["PGSSLMODE"] == "require" and env["PGPORT"] == "6543"


@PG_ONLY
@pytest.mark.skipif(not BACKUP_TOOLS, reason="PostgreSQL client tools (pg_dump, pg_restore, psql) not installed")
def test_backup_verify_restore_round_trip(fresh_pg, tmp_path, monkeypatch):
    from core.db.connection import Database
    from webapp import auth as A
    from webapp import bootstrap as BOOT
    pb = _pg_backup()
    url, _ = fresh_pg
    pw = _pw()
    with Database(url=url) as db:
        A.ensure_auth_schema(db)
        BOOT.seed_synthetic_demo(db)
        A.create_admin(db, "organiser@ncuindia.edu", "Event Organiser", pw)
        with db.transaction():
            db.execute("INSERT INTO users(email, full_name, role, student_id, link_status, password_hash) "
                       "VALUES ('s1@ncuindia.edu', 'Student One', 'student', 'SYN-STU-00001', 'verified', ?)",
                       (A.hash_password(_pw()),))
    out = tmp_path / "backups"
    dump = pb.backup(url, out)
    files = sorted(p.name for p in out.iterdir())
    assert len(files) == 3 and all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in out.iterdir())
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    v = pb.verify(dump)
    assert v["ok"] and v["rows"] > 0
    # damaged copy is detected
    bad = tmp_path / "bad"
    bad.mkdir()
    for p in out.iterdir():
        shutil.copy(p, bad / p.name)
    damaged = bad / dump.name
    data = bytearray(damaged.read_bytes())
    data[len(data) // 2] ^= 0xFF
    damaged.write_bytes(bytes(data))
    with pytest.raises(pb.BackupError, match="Checksum mismatch"):
        pb.verify(damaged)
    # restore into a NEW empty database (the way a replacement hosted database would be filled)
    target_url, target = pg_support.create_database(private_schema=False)
    try:
        if pg_support.prodlike():
            pg_support._admin_exec(f'GRANT CREATE ON DATABASE "{target}" TO eventpulse_app')
        res = pb.restore(dump, target_url)
        assert res["ok"] and res["rows"] == v["rows"]
        with pytest.raises(pb.BackupError, match="not empty"):
            pb.restore(dump, target_url)                                          # never over existing data
        # the restored database works for the application: same admin password, same records
        with Database(url=target_url) as db:
            assert db.scalar("SELECT COUNT(*) FROM users WHERE role = 'admin'") == 1
            assert A.verify_password(pw, db.scalar("SELECT password_hash FROM users WHERE role = 'admin'"))
            assert db.scalar("SELECT MAX(version) FROM schema_migrations") == 3
            assert A.ensure_auth_schema(db) == []
    finally:
        pg_support.drop_database(target)


@PG_ONLY
@pytest.mark.skipif(not BACKUP_TOOLS, reason="PostgreSQL client tools (pg_dump, pg_restore, psql) not installed")
def test_backup_command_line_keeps_password_out_of_arguments(fresh_pg, tmp_path, monkeypatch):
    pb = _pg_backup()
    url, _ = fresh_pg
    from core.db.connection import Database
    from webapp import auth as A
    with Database(url=url) as db:
        A.ensure_auth_schema(db)
    seen = []
    real = subprocess.run

    def spy(args, **kw):
        seen.append(" ".join(map(str, args)))
        return real(args, **kw)
    monkeypatch.setattr(pb.subprocess, "run", spy)
    monkeypatch.setenv("EVENTPULSE_BACKUP_DB_URL", url)
    assert pb.main(["backup", "--out-dir", str(tmp_path / "b")]) == 0
    pw = re.search(r"://[^:/@]+:([^@]+)@", url)
    if pw:
        assert all(pw.group(1) not in s for s in seen)
    assert all("postgresql://" not in s for s in seen)


# ============================================================================ 8. Render-like database account
RENDERLIKE = os.environ.get("EVENTPULSE_TEST_PG_RENDERLIKE_ROLE")


@PG_ONLY
@pytest.mark.skipif(not (RENDERLIKE and pg_support.prodlike()),
                    reason="needs the production-like server with a Render-like owner role "
                           "(EVENTPULSE_TEST_PG_RENDERLIKE_ROLE)")
def test_render_like_owner_role_on_public_schema(monkeypatch, request):
    """Render's free database gives ONE non-superuser owner account and the public schema, over SSL.
    Without the explicit opt-in the app refuses 'public' on a remote host; with it, a fresh database
    initialises and the app works end to end (TLS, SCRAM, non-superuser)."""
    import secrets as _s
    from urllib.parse import quote
    from core.db.connection import Database
    from core.db.errors import DatabaseError
    from webapp import hosted_setup
    name = "ep_test_" + _s.token_hex(6)
    pg_support._admin_exec(f'CREATE DATABASE "{name}" OWNER {RENDERLIKE}')
    request.addfinalizer(lambda: pg_support.drop_database(name))
    pw = Path(os.environ["EVENTPULSE_TEST_PG_RENDERLIKE_PASSWORD_FILE"]).read_text().strip()
    url = f"postgresql://{RENDERLIKE}:{quote(pw, safe='')}@pg-prodlike.test:55434/{name}?hostaddr=127.0.0.1"
    monkeypatch.delenv("EVENTPULSE_DB_ALLOW_PUBLIC_SCHEMA", raising=False)
    with pytest.raises(DatabaseError, match="public"):
        Database(url=url)
    monkeypatch.setenv("EVENTPULSE_DB_ALLOW_PUBLIC_SCHEMA", "1")
    assert hosted_setup.main(["init-db", "--db-url", url]) == 0
    with Database(url=url) as db:
        assert db.scalar("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()") in (True, 1)
        assert db.scalar("SELECT rolsuper FROM pg_roles WHERE rolname = current_user") in (False, 0)
        assert db.scalar("SELECT current_schema()") == "public"
        assert db.scalar("SELECT MAX(version) FROM schema_migrations") == 3
    admin_pw = _pw()
    S = hosted_app(monkeypatch, url, {"EVENTPULSE_ADMIN_EMAIL": "organiser@ncuindia.edu",
                                      "EVENTPULSE_ADMIN_PASSWORD": admin_pw})
    with TestClient(S.app) as c:
        assert c.get("/api/health").json() == {"status": "ok"}
        assert login(c, "organiser@ncuindia.edu", admin_pw).status_code == 200
        assert c.get("/api/overview").status_code == 200


PROD_TZ_SCRIPT = r'''
import json, time
from datetime import date
from fastapi.testclient import TestClient
import webapp.server as S
from webapp import security as SEC
before = time.localtime().tm_gmtoff
with TestClient(S.app) as c:
    out = {"before": before, "after": time.localtime().tm_gmtoff, "core_today": date.today().isoformat(),
           "app_today": SEC.local_today(S.CONFIG).isoformat(), "health": c.get("/api/health",
           headers={"X-Forwarded-Proto": "https"}).json()}
print("RESULT" + json.dumps(out))
'''


def test_production_server_on_a_utc_host_runs_in_ist(tmp_path):
    """Render runs in UTC. In production the web process switches to Asia/Kolkata at start-up, so
    the core's own date.today() (finalising events, 'upcoming' lists) agrees with check-in."""
    from tests.test_auth import pre_auth_copy, REAL_DB
    if not REAL_DB.exists():
        pytest.skip("database/eventpulse.db not present")
    db = pre_auth_copy(tmp_path / "eventpulse.db")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("EVENTPULSE_DB_URL", "EVENTPULSE_ENV", "WEB_CONC"))}
    env.update({"TZ": "UTC", "EVENTPULSE_ENV": "production", "EVENTPULSE_PUBLIC_BASE_URL": "https://ep.onrender.com",
                "EVENTPULSE_TRUSTED_PROXIES": "*", "EVENTPULSE_ALLOW_SQLITE_IN_PRODUCTION": "1",
                "EVENTPULSE_DB": str(db)})
    r = subprocess.run([sys.executable, "-c", PROD_TZ_SCRIPT], cwd=ROOT, env=env, capture_output=True, text=True,
                       timeout=180)
    assert r.returncode == 0, r.stderr[-2000:]
    out = json.loads(r.stdout.split("RESULT", 1)[1])
    assert out["before"] == 0 and out["after"] == 19800                       # UTC host -> IST process
    assert out["core_today"] == out["app_today"] and out["health"] == {"status": "ok"}
