"""Security tests for web authentication and role-based access control (webapp/auth.py).

Every test runs against a temporary copy made from a byte snapshot of database/eventpulse.db
(SQLite never opens the real file, so neither it nor its -wal/-shm is touched). Credentials are generated per run; nothing secret is hard-coded."""
import re
import secrets
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REAL_DB = ROOT / "database" / "eventpulse.db"


def _pw() -> str:
    return "Pw" + secrets.token_hex(8) + "7"


def pre_auth_copy(dst: Path) -> Path:
    """Copy of the real database returned to the state BEFORE the web-auth migration: if it
    already has accounts, the copy's auth tables are rolled back. The real database is never
    opened: the copy is made from the session's byte snapshot (tests/conftest.real_db_snapshot)."""
    from tests.conftest import real_db_snapshot
    src = sqlite3.connect(real_db_snapshot())
    out = sqlite3.connect(dst)
    src.backup(out)
    out.close()
    src.close()
    from core.db.connection import Database
    from webapp import auth as A
    with Database(dst) as db:
        if db.scalar("SELECT COUNT(*) FROM sqlite_master WHERE name = 'web_auth_migrations'"):
            A.rollback_auth_schema(db)
    for b in (dst.parent / "backups").glob("*pre_auth_rollback*"):
        b.unlink()
    return dst


def synthetic_pg_database(trained_models, small_synthetic):
    """PostgreSQL test mode: a fresh local test database holding SYNTHETIC records only, with the
    fictional student / event IDs these tests use (no copy of the real database)."""
    from core.db import services
    from core.db.connection import Database
    from core.db.repositories import RegistrationRepository
    from tests import pg_support
    from tests.conftest import make_event, make_student
    url, name = pg_support.create_database()
    with Database(url) as db:
        services.import_dataset(db, small_synthetic, "SYNTHETIC", "synthetic (auth tests)")
        services.sync_models(db, trained_models[0])
        make_event(db, "EV01", data_source="SYNTHETIC")
        for sid in ("STU001", "STU029", "STU089"):
            make_student(db, sid, data_source="SYNTHETIC")
        RegistrationRepository(db).register("STU029", "EV01", 3, 0)
        RegistrationRepository(db).register("STU089", "EV01", 4, 0)
    return url, name


@pytest.fixture()
def env(tmp_path, monkeypatch, request):
    import checkin_app
    import webapp.server as S
    from core.db.connection import Database
    from tests import pg_support
    from webapp import auth as A
    if pg_support.engine() == "postgresql":
        db_path, pg_name = synthetic_pg_database(request.getfixturevalue("trained_models"),
                                                 request.getfixturevalue("small_synthetic"))
        request.addfinalizer(lambda: pg_support.drop_database(pg_name))
        monkeypatch.setattr(S, "DB_URL", db_path)
    else:
        if not REAL_DB.exists():
            pytest.skip("database/eventpulse.db not present")
        db_path = pre_auth_copy(tmp_path / "eventpulse.db")
    monkeypatch.setattr(S, "DB_PATH", db_path)
    monkeypatch.setattr(checkin_app, "DB_PATH", db_path)
    monkeypatch.setattr(S, "EXPORT_DIR", tmp_path / "exports")
    A.reset_rate_limits()
    admin_pw = _pw()
    with Database(db_path) as db:
        A.create_admin(db, "admin@eventpulse.test", "Test Admin", admin_pw)
    clients = []

    def client():
        c = TestClient(S.app)
        c.__enter__()
        clients.append(c)
        return c

    yield {"S": S, "A": A, "db_path": db_path, "admin_pw": admin_pw, "client": client, "Database": Database}
    for c in clients:
        c.__exit__(None, None, None)
    A.reset_rate_limits()


def login(c, email, password):
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return r


def register(c, email, sid, password, name="Test Student"):
    return c.post("/api/auth/register", json={"full_name": name, "email": email, "student_id": sid,
                                              "password": password, "confirm_password": password})


def app_today():
    """The web app's 'today' (Asia/Kolkata), which differs from a UTC test machine's date late in the UTC day."""
    from webapp import security as SEC
    return SEC.local_today(SEC.load_config({}))


def admin_client(env):
    c = env["client"]()
    assert login(c, "admin@eventpulse.test", env["admin_pw"]).status_code == 200
    return c


def student_client(env, email, sid, verify_with=None):
    pw = _pw()
    c = env["client"]()
    assert register(c, email, sid, pw).status_code == 201
    if verify_with is not None:
        uid = next(u["user_id"] for u in verify_with.get("/api/admin/accounts").json() if u["email"] == email)
        assert verify_with.post(f"/api/admin/accounts/{uid}", json={"action": "verify"}).status_code == 200
    assert login(c, email, pw).status_code == 200
    return c


def admin_routes(S):
    """Every route on the admin-only router, with path parameters filled in."""
    out = []
    for r in list(S.admin.routes) + list(S.auth.admin_router.routes):
        if isinstance(r, APIRoute):
            path = re.sub(r"\{[^}]+\}", "1", r.path)
            for m in r.methods:
                out.append((m, path))
    return sorted(set(out))


# ------------------------------------------------------------------------- 1. registration
def test_student_registration_succeeds_and_hashes_password(env):
    c, pw = env["client"](), _pw()
    r = register(c, "new.student@ncuindia.edu", "STU029", pw, "Asha Verma")
    assert r.status_code == 201
    body = r.json()["user"]
    assert body["role"] == "student" and body["link_status"] == "pending"
    with env["Database"](env["db_path"]) as db:
        row = db.query_one("SELECT role, password_hash, link_status FROM users WHERE email = ?", ("new.student@ncuindia.edu",))
    assert row["role"] == "student" and row["link_status"] == "pending"
    assert pw not in row["password_hash"] and row["password_hash"].startswith(("scrypt$", "$argon2"))
    assert env["A"].verify_password(pw, row["password_hash"]) and not env["A"].verify_password(pw + "x", row["password_hash"])


def test_registration_backend_validation(env):
    c = env["client"]()
    good = {"full_name": "Asha Verma", "email": "v@ncuindia.edu", "student_id": "STU030", "password": "abcdefgh12",
            "confirm_password": "abcdefgh12"}
    for bad in ({"password": "short1", "confirm_password": "short1"},           # too short
                {"password": "onlyletterspassword", "confirm_password": "onlyletterspassword"},
                {"confirm_password": "abcdefgh13"},                              # mismatch
                {"email": "not-an-email"},
                {"student_id": "21CSU123"},                                       # roll-number-like: refused
                {"student_id": "9876543210"},                                     # phone-like: refused
                {"full_name": "<script>"}):
        assert c.post("/api/auth/register", json={**good, **bad}).status_code == 422, bad


# ------------------------------------------------------------------------- 2. duplicates
def test_duplicate_registration_rejected(env):
    c = env["client"]()
    assert register(c, "dup@ncuindia.edu", "STU031", _pw()).status_code == 201
    assert register(c, "DUP@ncuindia.edu", "STU032", _pw()).status_code == 409        # same e-mail, other case
    assert register(c, "other@ncuindia.edu", "stu031", _pw()).status_code == 409      # same Student ID, other case
    with env["Database"](env["db_path"]) as db:
        assert db.scalar("SELECT COUNT(*) FROM users WHERE role = 'student'") == 1


# ------------------------------------------------------------------------- 3. no admin via registration
def test_student_cannot_register_as_admin(env):
    c, pw = env["client"](), _pw()
    for extra in ({"role": "admin"}, {"role": "student"}, {"is_admin": True}):
        r = c.post("/api/auth/register", json={"full_name": "Eve", "email": "eve@ncuindia.edu", "student_id": "STU033",
                                               "password": pw, "confirm_password": pw, **extra})
        assert r.status_code == 422                                                   # unknown fields are rejected
    assert register(c, "eve@ncuindia.edu", "STU033", pw).status_code == 201
    assert login(c, "eve@ncuindia.edu", pw).json()["user"]["role"] == "student"
    with env["Database"](env["db_path"]) as db:
        assert db.scalar("SELECT COUNT(*) FROM users WHERE role = 'admin'") == 1
        # the database itself refuses a second admin and any role change
        with pytest.raises(Exception):
            db.execute("INSERT INTO users(email, full_name, role, password_hash) VALUES ('x@y.z','X','admin',?)", ("h" * 60,))
        with pytest.raises(Exception):
            db.execute("UPDATE users SET role = 'admin', student_id = NULL, link_status = NULL WHERE email = 'eve@ncuindia.edu'")


def test_admin_provisioning_never_overwrites(env):
    A, Database = env["A"], env["Database"]
    with Database(env["db_path"]) as db:
        before = db.query_one("SELECT user_id, email, password_hash FROM users WHERE role = 'admin'")
        with pytest.raises(PermissionError):
            A.create_admin(db, "intruder@eventpulse.test", "Intruder", _pw())
        assert db.query_one("SELECT user_id, email, password_hash FROM users WHERE role = 'admin'") == before
    from webapp.provision_admin import main
    assert main(["--db", str(env["db_path"]), "--status"]) == 0
    assert main(["--db", str(env["db_path"]), "--from-env"]) == 3       # admin exists -> nothing changes


# ------------------------------------------------------------------------- 4/5. login
def test_admin_login_sets_secure_session_cookie(env):
    c = env["client"]()
    r = login(c, "admin@eventpulse.test", env["admin_pw"])
    assert r.status_code == 200 and r.json()["user"]["role"] == "admin"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "ep_session=" in cookie
    assert r.json()["csrf_token"] not in r.headers["set-cookie"]            # token != CSRF value
    assert c.get("/api/auth/me").json()["user"]["email"] == "admin@eventpulse.test"
    assert c.get("/api/overview").status_code == 200
    with env["Database"](env["db_path"]) as db:                              # only a hash of the token is stored
        token = r.cookies.get("ep_session")
        assert token and not db.scalar("SELECT COUNT(*) FROM auth_sessions WHERE token_hash = ?", (token,))


def test_student_login_and_generic_errors(env):
    c, pw = env["client"](), _pw()
    register(c, "s1@ncuindia.edu", "STU034", pw)
    assert login(c, "s1@ncuindia.edu", pw).json()["user"]["role"] == "student"
    wrong = c.post("/api/auth/login", json={"email": "s1@ncuindia.edu", "password": pw + "x"})
    unknown = c.post("/api/auth/login", json={"email": "nobody@ncuindia.edu", "password": pw})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"] == "Invalid email or password."


def test_repeated_failed_logins_are_rate_limited(env):
    c = env["client"]()
    codes = [c.post("/api/auth/login", json={"email": "admin@eventpulse.test", "password": "wrong" + str(i)}).status_code
             for i in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    # even the right password is refused while the account is locked
    assert c.post("/api/auth/login", json={"email": "admin@eventpulse.test", "password": env["admin_pw"]}).status_code == 429


# ------------------------------------------------------------------------- 6. student vs admin endpoints
def test_student_cannot_access_admin_endpoints(env):
    S = env["S"]
    st = student_client(env, "s2@ncuindia.edu", "STU035")
    routes = admin_routes(S)
    assert len(routes) >= 40 and any(p.startswith("/api/admin/") for _, p in routes)
    for method, path in routes:
        r = st.request(method, path, json={})
        assert r.status_code == 403, (method, path, r.status_code)
    # the QR image / reports / exports of the admin are refused too
    assert st.get("/api/export/registrations?fmt=csv").status_code == 403


# ------------------------------------------------------------------------- 7. ownership
def test_student_cannot_access_another_students_data(env):
    admin = admin_client(env)
    a = student_client(env, "a@ncuindia.edu", "STU029", verify_with=admin)
    b = student_client(env, "b@ncuindia.edu", "STU089", verify_with=admin)
    with env["Database"](env["db_path"]) as db:
        a_regs = {r["registration_id"] for r in db.query("SELECT registration_id FROM registrations WHERE student_id='STU029'")}
        b_regs = {r["registration_id"] for r in db.query("SELECT registration_id FROM registrations WHERE student_id='STU089'")}
    assert a_regs and b_regs and not a_regs & b_regs
    got = {r["registration_id"] for r in a.get("/api/student/registrations").json()["rows"]}
    assert got == a_regs
    # tampering with query parameters, paths or IDs does not change whose data is returned
    for path in ("/api/student/registrations?student_id=STU089", "/api/student/registrations?user_id=2",
                 "/api/student/attendance?student_id=STU089"):
        rows = a.get(path).json()["rows"]
        assert all(r.get("registration_id", None) not in b_regs for r in rows)
    assert a.get("/api/student/profile").json()["record"]["student_id"] == "STU029"
    for path in ("/api/admin/students/STU089", "/api/registrations?search=STU089", "/api/students",
                 "/api/attendance?event_id=EV01", "/api/predictions"):
        assert a.get(path).status_code == 403, path
    assert a.post("/api/registrations/1", json={"status": "Cancelled"}).status_code in (403, 405)
    assert a.patch(f"/api/registrations/{min(b_regs)}", json={"status": "Cancelled"}).status_code == 403
    # an unverified student who CLAIMS someone's ID sees nothing until the organiser verifies it
    c = student_client(env, "claim@ncuindia.edu", "STU001")
    assert c.get("/api/student/registrations").json() == {"linked": False, "rows": [],
                                                           "notice": c.get("/api/student/registrations").json()["notice"]}
    assert c.get("/api/student/profile").json()["record"] is None


# ------------------------------------------------------------------------- 8. unauthenticated
def test_unauthenticated_users_cannot_access_protected_endpoints(env):
    S, anon = env["S"], env["client"]()
    for method, path in admin_routes(S):
        assert anon.request(method, path, json={}).status_code == 401, (method, path)
    for path in ("/api/student/profile", "/api/student/summary", "/api/student/registrations",
                 "/api/student/attendance", "/api/student/predictions", "/api/auth/me"):
        assert anon.get(path).status_code == 401, path
    assert anon.get("/api/health").status_code == 200                    # public, no records
    anon.cookies.set("ep_session", "forged-token-value")
    assert anon.get("/api/overview").status_code == 401


def test_csrf_token_required_for_changes(env):
    c = admin_client(env)
    csrf = c.headers.pop("X-CSRF-Token")
    body = {"event_type": "Seminar", "event_day": "Friday", "time_slot": "Evening", "duration_hours": 1}
    assert c.post("/api/events", json=body).status_code == 403
    assert c.post("/api/events", json=body, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert c.post("/api/events", json=body, headers={"X-CSRF-Token": csrf}).status_code == 201


# ------------------------------------------------------------------------- 9. logout / expiry
def test_logout_invalidates_the_session(env):
    c = admin_client(env)
    token = c.cookies.get("ep_session")
    assert c.post("/api/auth/logout").json()["signed_out"]
    assert c.get("/api/overview").status_code == 401
    replay = env["client"]()
    replay.cookies.set("ep_session", token)                               # re-using the old cookie fails
    assert replay.get("/api/overview").status_code == 401
    with env["Database"](env["db_path"]) as db:
        assert db.scalar("SELECT COUNT(*) FROM auth_sessions") == 0


def test_idle_session_expires(env):
    c = admin_client(env)
    with env["Database"](env["db_path"]) as db:
        db.execute("UPDATE auth_sessions SET last_seen_utc = '2000-01-01T00:00:00Z'")
    assert c.get("/api/overview").status_code == 401


def test_deactivated_account_is_signed_out(env):
    admin = admin_client(env)
    st = student_client(env, "gone@ncuindia.edu", "STU036")
    uid = next(u["user_id"] for u in admin.get("/api/admin/accounts").json() if u["email"] == "gone@ncuindia.edu")
    assert admin.post(f"/api/admin/accounts/{uid}", json={"action": "deactivate"}).status_code == 200
    assert st.get("/api/student/profile").status_code == 401


# ------------------------------------------------------------------------- 10. QR check-in
def test_qr_checkin_still_works_and_is_guarded(env):
    pytest.importorskip("segno")
    admin = admin_client(env)
    ev = admin.post("/api/events", json={"event_type": "Workshop", "event_date": app_today().isoformat(),
                                         "time_slot": "Morning", "duration_hours": 1, "name": "QR test"}).json()
    eid = ev["event_id"]
    assert admin.post(f"/api/events/{eid}/registrations", json={"student_id": "STU029"}).status_code == 201
    url = admin.post(f"/api/attendance/{eid}/qr").json()["url"]
    token = url.split("t=")[1]
    assert admin.get(f"/api/attendance/{eid}/qr.png").content[:4] == b"\x89PNG"

    phone = env["client"]()                                               # not signed in
    form = phone.get(f"/checkin/{eid}?t={token}")
    assert form.status_code == 401 and "Sign in to check in" in form.text   # login required (Phase 2)
    assert 'name="student_id"' not in form.text                           # no typed Student ID any more
    bad = phone.get(f"/checkin/{eid}?t=wrong")
    assert bad.status_code == 403 and "QR test" not in bad.text           # nothing revealed without the token
    assert phone.post(f"/checkin/{eid}", data={"student_id": "STU029", "t": token}).status_code == 401
    # a verified student checks in with their own account
    stu = student_client(env, "qr.student@ncuindia.edu", "STU029", verify_with=admin)
    page = stu.get(f"/checkin/{eid}?t={token}")
    assert page.status_code == 200 and "QR test" in page.text and "STU029" in page.text
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    assert stu.post(f"/checkin/{eid}", data={"t": "wrong", "csrf_token": csrf}).status_code == 403
    ok = stu.post(f"/checkin/{eid}", data={"t": token, "csrf_token": csrf})
    assert ok.status_code == 200 and "Checked in" in ok.text
    assert any(r["student_id"] == "STU029" for r in admin.get(f"/api/attendance?event_id={eid}").json()["checkins"])
    # the check-in page cannot be used to reach the API
    assert phone.get("/api/attendance?event_id=" + eid).status_code == 401


def test_checkin_form_is_rate_limited(env, monkeypatch):
    A, phone = env["A"], env["client"]()
    monkeypatch.setattr(A, "CHECKINS_PER_IP", A.RateLimiter(20, 600))   # the real ceiling is 5000 per address
    for _ in range(A.CHECKINS_PER_IP.limit):
        phone.post("/checkin/EV01", data={"t": "x"})
    assert phone.post("/checkin/EV01", data={"t": "x"}).status_code == 429


# ------------------------------------------------------------------------- 11. existing functionality + migration
def test_admin_keeps_full_functionality(env):
    c = admin_client(env)
    for path in ("/api/overview", "/api/datasets", "/api/events", "/api/calendar", "/api/registrations",
                 "/api/models", "/api/analytics?dimension=Department", "/api/insights", "/api/monitoring",
                 "/api/notifications", "/api/reports/summary", "/api/settings", "/api/predictions",
                 "/api/admin/accounts", "/api/admin/security-log", "/api/admin/students/STU029"):
        assert c.get(path).status_code == 200, path
    feats = c.get("/api/registration-features?student_id=STU029&event_id=EV01")
    if feats.status_code == 200:
        p = c.post("/api/predict", json=feats.json()).json()
        assert 0 <= p["probability"] <= 1
    assert c.post("/api/reports/pdf").content[:4] == b"%PDF"


@pytest.mark.sqlite_only
def test_migration_is_additive_backed_up_and_reversible(tmp_path):
    from core.db.connection import Database
    from webapp import auth as A
    db_path = pre_auth_copy(tmp_path / "eventpulse.db")
    assert not list((tmp_path / "backups").glob("*pre_auth_migration*"))
    from tests.conftest import real_db_snapshot
    src = sqlite3.connect(real_db_snapshot())
    tables = ["students", "events", "registrations", "attendance_records", "predictions", "model_versions", "audit_log"]
    before = {t: src.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    src.close()
    with Database(db_path) as db:
        names = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not {"users", "auth_sessions", "auth_audit", "web_auth_migrations"} & names
        assert A.ensure_auth_schema(db) == [1]
        after = {t: db.scalar(f"SELECT COUNT(*) FROM {t}") for t in tables}
        assert {t: after[t] for t in tables if t != "audit_log"} == {t: before[t] for t in tables if t != "audit_log"}
        assert db.scalar("SELECT MAX(version) FROM schema_migrations") == 3   # core schema untouched (desktop app)
        assert db.scalar("SELECT MAX(version) FROM web_auth_migrations") == 1
        assert not db.integrity_check()
        backups = list((tmp_path / "backups").glob("*pre_auth_migration*.db"))
        assert backups                                                     # backup written BEFORE the change
        b = sqlite3.connect(f"file:{backups[0]}?mode=ro", uri=True)
        assert not b.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='users'").fetchone()[0]
        b.close()
        A.rollback_auth_schema(db)
        names = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not {"users", "auth_sessions", "auth_audit"} & names
        assert {t: db.scalar(f"SELECT COUNT(*) FROM {t}") for t in tables if t != "audit_log"} == \
               {t: before[t] for t in tables if t != "audit_log"}
        A.ensure_auth_schema(db)                                           # and it can be applied again
        assert db.scalar("SELECT COUNT(*) FROM users") == 0
    with Database(db_path) as db:                                           # the desktop's own opener still works
        assert db.scalar("SELECT COUNT(*) FROM events") == before["events"]
