"""Phase 2 security hardening tests (webapp/security.py, webapp/auth.py, webapp/server.py).

Covers: production configuration (fail closed), HTTPS enforcement and Secure/__Host- cookies,
trusted-proxy client addresses, account-based limits with progressive delays (no permanent
lockout), the password-hashing concurrency cap, QR check-in that needs a signed-in, admin-verified
student and records only that student's own ID, admin-only desk check-in, security headers, the
minimal public health endpoint, CSRF on every state-changing request, admin-controlled password
reset, the ncuindia.edu registration rule, and a 150-students-behind-one-address load test.

Same isolation as tests/test_auth.py: SQLite tests run on a copy made from a byte snapshot of the
real database (never opened); PostgreSQL mode uses a fresh synthetic test database.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.routing import APIRoute  # noqa: E402
from starlette.requests import Request  # noqa: E402

from tests.test_auth import (REAL_DB, _pw, admin_client, admin_routes, app_today, env, login,  # noqa: E402,F401
                             pre_auth_copy, register, student_client)
from webapp import security as SEC  # noqa: E402

ROOT = REAL_DB.parent.parent
CAMPUS_IP = "203.0.113.7"          # documentation address: one public NAT address for the whole campus


# ============================================================================ helpers
def fake_request(peer="198.51.100.10", headers=None, scheme="http", host="testserver"):
    hs = [(k.lower().encode(), v.encode()) for k, v in {"host": host, **(headers or {})}.items()]
    return Request({"type": "http", "method": "GET", "path": "/", "query_string": b"", "scheme": scheme,
                    "server": (host, 80), "client": (peer, 5555), "headers": hs})


@pytest.fixture()
def ip_from_header(env, monkeypatch):
    """Lets a test choose the client address per request (X-Test-IP), as the proxy would."""
    A = env["A"]
    monkeypatch.setattr(A, "client_ip", lambda request: request.headers.get("x-test-ip", CAMPUS_IP))
    return A


def make_today_event(admin, name="Phase 2 event"):
    ev = admin.post("/api/events", json={"event_type": "Workshop", "event_date": app_today().isoformat(),
                                         "time_slot": "Morning", "duration_hours": 1, "name": name}).json()
    eid = ev["event_id"]
    token = admin.post(f"/api/attendance/{eid}/qr").json()["url"].split("t=")[1]
    return eid, token


def checkin_csrf(page_text: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', page_text).group(1)


def checkins_of(admin, eid):
    return {r["student_id"] for r in admin.get(f"/api/attendance?event_id={eid}").json()["checkins"]}


# ============================================================================ 1. production configuration
def test_development_defaults_are_unchanged():
    cfg = SEC.load_config({})
    assert not cfg.production and not cfg.secure_cookies and cfg.session_cookie == "ep_session"
    assert cfg.docs_enabled and cfg.hsts_seconds == 0 and cfg.allowed_email_domains == ("ncuindia.edu",)
    assert cfg.hash_concurrency == 2 and not cfg.trust_all_proxies and cfg.trusted_proxies == ()


PROD = {"EVENTPULSE_ENV": "production", "EVENTPULSE_PUBLIC_BASE_URL": "https://events.ncuindia.test",
        "EVENTPULSE_TRUSTED_PROXIES": "*", "EVENTPULSE_DB_URL": "postgresql://u@db.example/x"}


def test_valid_production_configuration():
    cfg = SEC.load_config(PROD)
    assert cfg.production and cfg.secure_cookies and cfg.session_cookie == "__Host-ep_session"
    assert cfg.allowed_hosts == ("events.ncuindia.test",) and cfg.public_base_url == "https://events.ncuindia.test"
    assert cfg.hsts_seconds >= 31536000 and not cfg.docs_enabled and cfg.trust_all_proxies
    assert cfg.allowed_origins == ("https://events.ncuindia.test",)          # no dev origins in production


@pytest.mark.parametrize("change, message", [
    ({"EVENTPULSE_PUBLIC_BASE_URL": None}, "EVENTPULSE_PUBLIC_BASE_URL"),
    ({"EVENTPULSE_PUBLIC_BASE_URL": "http://events.ncuindia.test"}, "https://"),
    ({"EVENTPULSE_PUBLIC_BASE_URL": "https://events.ncuindia.test/app?x=1"}, "no path"),
    ({"EVENTPULSE_SECURE_COOKIES": "0"}, "Secure"),
    ({"EVENTPULSE_TRUSTED_PROXIES": None}, "EVENTPULSE_TRUSTED_PROXIES"),
    ({"EVENTPULSE_TRUSTED_PROXIES": "not-an-ip"}, "CIDR"),
    ({"EVENTPULSE_DB_URL": None}, "EVENTPULSE_DB_URL"),
    ({"EVENTPULSE_ALLOWED_HOSTS": "*.onrender.com"}, "wildcards"),
    ({"EVENTPULSE_HASH_CONCURRENCY": "99"}, "EVENTPULSE_HASH_CONCURRENCY"),
    ({"EVENTPULSE_ENV": "prod-ish"}, "EVENTPULSE_ENV"),
])
def test_production_fails_closed(change, message):
    cfg = {**PROD, **change}
    with pytest.raises(SEC.ConfigError) as e:
        SEC.load_config({k: v for k, v in cfg.items() if v is not None})
    assert message in str(e.value)


def test_sqlite_in_production_needs_explicit_opt_in():
    env = {k: v for k, v in PROD.items() if k != "EVENTPULSE_DB_URL"}
    with pytest.raises(SEC.ConfigError):
        SEC.load_config(env)
    assert SEC.load_config({**env, "EVENTPULSE_ALLOW_SQLITE_IN_PRODUCTION": "1"}).production


def _run_server_script(code: str, extra: dict) -> subprocess.CompletedProcess:
    e = {k: v for k, v in os.environ.items() if not k.startswith(("EVENTPULSE_DB_URL", "EVENTPULSE_ENV",
                                                                   "EVENTPULSE_PUBLIC", "EVENTPULSE_TRUSTED",
                                                                   "EVENTPULSE_SECURE", "EVENTPULSE_ALLOWED"))}
    e.update(extra)
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=e, capture_output=True, text=True, timeout=180)


def test_server_refuses_to_start_with_unsafe_production_config():
    r = _run_server_script("import webapp.server", {"EVENTPULSE_ENV": "production"})
    assert r.returncode != 0 and "Unsafe EventPulse AI web configuration" in r.stderr


PROD_APP_SCRIPT = r'''
import json, sys
from fastapi.testclient import TestClient
import webapp.server as S
from webapp import auth as A
out = {}
with TestClient(S.app, base_url="http://events.ncuindia.test") as c:
    https = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.50"}
    r = c.get("/api/events?x=1", follow_redirects=False)
    out["http_get"] = [r.status_code, r.headers.get("location")]
    r = c.post("/api/auth/login", json={"email": "a@b.c", "password": "x"})
    out["http_post"] = r.status_code
    out["health_http"] = [c.get("/api/health").status_code, c.get("/api/health").json()]
    out["bad_host"] = c.get("/", headers={**https, "Host": "evil.example"}).status_code
    r = c.get("/", headers=https)
    out["https_page"] = [r.status_code, r.headers.get("strict-transport-security"), r.headers.get("content-security-policy")]
    out["docs"] = [any(k in c.get(p, headers=https).text.lower() for k in ("swagger", "redoc", '"openapi"'))
                   for p in ("/docs", "/redoc", "/openapi.json")]
    r = c.post("/api/auth/login", headers=https, json={"email": "admin@eventpulse.test", "password": sys.argv[1]})
    out["login"] = [r.status_code, r.headers.get("set-cookie", "")]
    out["origin_dev"] = c.post("/api/auth/login", headers={**https, "Origin": "http://localhost:5173"},
                               json={"email": "a@b.c", "password": "x"}).status_code
    out["cookie_name"] = A.SESSION_COOKIE
print("RESULT" + json.dumps(out))
'''


def test_production_mode_https_hosts_cookies_and_docs(tmp_path):
    if not REAL_DB.exists():
        pytest.skip("database/eventpulse.db not present")
    from core.db.connection import Database
    from webapp import auth as A
    db = pre_auth_copy(tmp_path / "eventpulse.db")
    pw = _pw()
    with Database(db) as d:
        A.create_admin(d, "admin@eventpulse.test", "Test Admin", pw)
    r = _run_server_script(PROD_APP_SCRIPT.replace("sys.argv[1]", repr(pw)),
                           {**{k: v for k, v in PROD.items() if k != "EVENTPULSE_DB_URL"},
                            "EVENTPULSE_ALLOW_SQLITE_IN_PRODUCTION": "1", "EVENTPULSE_DB": str(db)})
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.split("RESULT", 1)[1])
    assert out["http_get"] == [308, "https://events.ncuindia.test/api/events?x=1"]      # HTTPS enforced
    assert out["http_post"] == 403                                                    # no plain-HTTP writes
    assert out["health_http"] == [200, {"status": "ok"}]                             # platform health check
    assert out["bad_host"] == 400                                                     # Host header validated
    assert out["https_page"][0] in (200, 503) and "max-age=31536000" in out["https_page"][1]
    assert "frame-ancestors 'none'" in out["https_page"][2]
    assert out["docs"] == [False, False, False]              # API docs off (only the web app's page is served)
    status, cookie = out["login"]
    assert status == 200 and cookie.startswith("__Host-ep_session=")
    assert "secure" in cookie.lower() and "httponly" in cookie.lower() and "samesite=strict" in cookie.lower()
    assert "domain=" not in cookie.lower() and "path=/" in cookie.lower()
    assert out["origin_dev"] == 403                                                   # dev origin refused
    assert out["cookie_name"] == "__Host-ep_session"


# ============================================================================ 3. trusted proxy
def test_client_ip_ignores_forwarded_for_from_untrusted_peers():
    cfg = SEC.load_config({})
    req = fake_request(peer="198.51.100.10", headers={"X-Forwarded-For": "1.2.3.4"})
    assert SEC.client_ip(req, cfg) == "198.51.100.10"                      # spoofing does nothing
    assert SEC.request_scheme(fake_request(headers={"X-Forwarded-Proto": "https"}), cfg) == "http"


def test_client_ip_from_trusted_proxy_uses_the_entry_the_proxy_added():
    cfg = SEC.load_config({"EVENTPULSE_TRUSTED_PROXIES": "10.0.0.0/8"})
    # the client sent a fake header; the proxy appended the real address on the right
    req = fake_request(peer="10.1.2.3", headers={"X-Forwarded-For": "6.6.6.6, " + CAMPUS_IP,
                                                 "X-Forwarded-Proto": "https"})
    assert SEC.client_ip(req, cfg) == CAMPUS_IP and SEC.request_scheme(req, cfg) == "https"
    assert SEC.client_ip(fake_request(peer="10.1.2.3"), cfg) == "10.1.2.3"            # no header
    assert SEC.client_ip(fake_request(peer="10.1.2.3", headers={"X-Forwarded-For": "garbage"}), cfg) == "10.1.2.3"
    outside = fake_request(peer="192.0.2.9", headers={"X-Forwarded-For": "1.2.3.4"})
    assert SEC.client_ip(outside, cfg) == "192.0.2.9"                                  # not a trusted proxy
    two = SEC.load_config({"EVENTPULSE_TRUSTED_PROXIES": "*", "EVENTPULSE_PROXY_HOPS": "2"})
    req = fake_request(peer="10.0.0.1", headers={"X-Forwarded-For": "6.6.6.6, 198.51.100.77, 10.9.9.9"})
    assert SEC.client_ip(req, two) == "198.51.100.77"


# ============================================================================ 4/5. limits and progressive delay
def test_progressive_throttle_grows_caps_and_forgets(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(SEC.time, "monotonic", lambda: clock[0])
    t = SEC.ProgressiveThrottle(free=3, base=1, cap=8, forget_after=100)
    for _ in range(3):
        assert t.retry_after("k") == 0
        t.failure("k")
    assert t.retry_after("k") == 1                               # 4th attempt waits 1 s
    t.failure("k")
    assert t.retry_after("k") == 2                               # then 2 s ...
    for _ in range(5):
        t.failure("k")
    assert t.retry_after("k") == 8                               # ... capped, never a permanent lockout
    clock[0] += 8
    assert t.retry_after("k") == 0                               # waiting is enough to try again
    clock[0] += 101
    assert t.failures("k") == 0                                  # forgotten after a quiet period
    t.failure("k")
    t.reset("k")
    assert t.failures("k") == 0


def test_failed_logins_delay_only_that_account_on_a_shared_address(env, ip_from_header):
    A = ip_from_header
    admin = admin_client(env)
    a = student_client(env, "slow.typer@ncuindia.edu", "STU029", verify_with=admin)
    b_pw = _pw()
    assert register(env["client"](), "classmate@ncuindia.edu", "STU089", b_pw).status_code == 201
    c = env["client"]()
    codes = [c.post("/api/auth/login", json={"email": "slow.typer@ncuindia.edu", "password": "wrong" + str(i)}).status_code
             for i in range(6)]
    assert codes == [401] * 5 + [429]
    r = c.post("/api/auth/login", json={"email": "slow.typer@ncuindia.edu", "password": "wrong"})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1 and "Wait" in r.json()["detail"]
    # a classmate behind the same campus address is NOT affected
    assert login(env["client"](), "classmate@ncuindia.edu", b_pw).status_code == 200
    # the same account from another address is not blocked by those failures (no account lockout)
    assert c.post("/api/auth/login", headers={"X-Test-IP": "198.51.100.200"},
                  json={"email": "slow.typer@ncuindia.edu", "password": "wrong"}).status_code == 401
    # the already signed-in session of that student keeps working (no lockout of the account)
    assert a.get("/api/auth/me").status_code == 200


def test_delay_expires_and_success_resets(env, ip_from_header, monkeypatch):
    A = ip_from_header
    admin = admin_client(env)
    pw = _pw()
    assert register(env["client"](), "late.student@ncuindia.edu", "STU029", pw).status_code == 201
    c = env["client"]()
    for i in range(6):
        c.post("/api/auth/login", json={"email": "late.student@ncuindia.edu", "password": "bad" + str(i)})
    assert login(c, "late.student@ncuindia.edu", pw).status_code == 429       # even the right password waits
    real = SEC.time.monotonic
    monkeypatch.setattr(SEC.time, "monotonic", lambda: real() + 30)            # 30 s later
    assert login(c, "late.student@ncuindia.edu", pw).status_code == 200
    assert A.LOGIN_DELAY_PER_ACCOUNT.failures("late.student@ncuindia.edu") == 0
    assert admin is not None


def test_unknown_accounts_are_throttled_the_same_way(env, ip_from_header):
    c = env["client"]()
    codes = [c.post("/api/auth/login", json={"email": "nobody@ncuindia.edu", "password": "x" + str(i)}).status_code
             for i in range(6)]
    assert codes == [401] * 5 + [429]                    # no difference that would reveal which accounts exist


def test_registration_limits_are_per_account_not_per_campus(env, ip_from_header):
    for i in range(12):                                   # more than the old per-IP limit of 10
        assert register(env["client"](), f"student{i}@ncuindia.edu", f"NEWSTU{i:03d}", _pw()).status_code == 201
    c = env["client"]()
    for i in range(5):
        assert register(c, "repeat@ncuindia.edu", f"REP{i:03d}", "short").status_code == 422   # rejected early
    codes = []
    for i in range(6):
        codes.append(register(c, "repeat@ncuindia.edu", f"RPT{i:03d}", _pw()).status_code)
    assert codes[:5] == [201, 409, 409, 409, 409] and codes[5] == 429


# ============================================================================ 6. hashing concurrency
def test_password_hashing_concurrency_is_capped(env, monkeypatch):
    A = env["A"]
    monkeypatch.setattr(A, "_HASH_SLOTS", threading.BoundedSemaphore(2))
    monkeypatch.setitem(A.HASH_STATS, "peak", 0)
    with ThreadPoolExecutor(8) as pool:
        hashes = list(pool.map(lambda i: A.hash_password(f"Password{i}x9"), range(8)))
    assert A.HASH_STATS["peak"] <= 2 and A.HASH_STATS["active"] == 0
    assert all(A.verify_password(f"Password{i}x9", h) for i, h in enumerate(hashes))


def test_hashing_busy_returns_503_not_a_crash(env, monkeypatch):
    A = env["A"]
    slots = threading.BoundedSemaphore(1)
    slots.acquire()                                         # all slots taken by "other" requests
    monkeypatch.setattr(A, "_HASH_SLOTS", slots)
    monkeypatch.setattr(A, "HASH_WAIT_SECONDS", 0.2)
    r = env["client"]().post("/api/auth/login", json={"email": "admin@eventpulse.test", "password": env["admin_pw"]})
    assert r.status_code == 503 and r.headers["Retry-After"] == "5"
    slots.release()


# ============================================================================ 7/8. QR check-in
def test_qr_requires_login_and_records_nothing_without_it(env):
    admin = admin_client(env)
    eid, token = make_today_event(admin)
    assert admin.post(f"/api/events/{eid}/registrations", json={"student_id": "STU029"}).status_code == 201
    anon = env["client"]()
    page = anon.get(f"/checkin/{eid}?t={token}")
    assert page.status_code == 401 and "Sign in to check in" in page.text and "/#/login?next=" in page.text
    assert anon.post(f"/checkin/{eid}", data={"t": token, "student_id": "STU029"}).status_code == 401
    assert anon.post(f"/checkin/{eid}", data={"t": token, "student_id": "STU029", "csrf_token": "x"}).status_code == 401
    anon.cookies.set(env["A"].SESSION_COOKIE, "forged")
    assert anon.post(f"/checkin/{eid}", data={"t": token, "student_id": "STU029"}).status_code == 401
    assert checkins_of(admin, eid) == set()


def test_pending_rejected_and_admin_accounts_cannot_check_in(env):
    admin = admin_client(env)
    eid, token = make_today_event(admin)
    for sid in ("STU029", "STU089"):
        assert admin.post(f"/api/events/{eid}/registrations", json={"student_id": sid}).status_code == 201
    pending = student_client(env, "pending.one@ncuindia.edu", "STU029")                 # not verified
    page = pending.get(f"/checkin/{eid}?t={token}")
    assert page.status_code == 403 and "waiting for the organiser" in page.text and "csrf_token" not in page.text
    assert pending.post(f"/checkin/{eid}", data={"t": token, "csrf_token": pending.headers["X-CSRF-Token"]}).status_code == 403
    rejected = student_client(env, "rejected.one@ncuindia.edu", "STU089")
    uid = next(u["user_id"] for u in admin.get("/api/admin/accounts").json() if u["email"] == "rejected.one@ncuindia.edu")
    assert admin.post(f"/api/admin/accounts/{uid}", json={"action": "reject"}).status_code == 200
    assert rejected.post(f"/checkin/{eid}", data={"t": token, "csrf_token": rejected.headers["X-CSRF-Token"]}).status_code == 403
    assert admin.get(f"/checkin/{eid}?t={token}").status_code == 403                    # organiser: desk only
    assert admin.post(f"/checkin/{eid}", data={"t": token, "csrf_token": admin.headers["X-CSRF-Token"]}).status_code == 403
    assert checkins_of(admin, eid) == set()


def test_qr_records_only_the_signed_in_students_own_id(env):
    admin = admin_client(env)
    eid, token = make_today_event(admin)
    for sid in ("STU029", "STU089"):
        assert admin.post(f"/api/events/{eid}/registrations", json={"student_id": sid}).status_code == 201
    stu = student_client(env, "own.id@ncuindia.edu", "STU029", verify_with=admin)
    page = stu.get(f"/checkin/{eid}?t={token}")
    assert page.status_code == 200 and "STU029" in page.text and 'name="student_id"' not in page.text
    csrf = checkin_csrf(page.text)
    # trying to check in somebody else: the submitted ID is ignored, only the session's verified ID counts
    r = stu.post(f"/checkin/{eid}", data={"t": token, "csrf_token": csrf, "student_id": "STU089"})
    assert r.status_code == 200 and "STU029" in r.text and "STU089" not in r.text
    assert checkins_of(admin, eid) == {"STU029"}
    again = stu.post(f"/checkin/{eid}", data={"t": token, "csrf_token": csrf})
    assert again.status_code == 400 and "already checked in" in again.text
    assert checkins_of(admin, eid) == {"STU029"}


def test_qr_needs_csrf_token_valid_link_and_registration(env):
    admin = admin_client(env)
    eid, token = make_today_event(admin)
    stu = student_client(env, "csrf.check@ncuindia.edu", "STU029", verify_with=admin)
    csrf = checkin_csrf(stu.get(f"/checkin/{eid}?t={token}").text)
    assert stu.post(f"/checkin/{eid}", data={"t": token}).status_code == 403                       # no CSRF
    assert stu.post(f"/checkin/{eid}", data={"t": token, "csrf_token": "x" * 43}).status_code == 403
    assert stu.post(f"/checkin/{eid}", data={"t": "wrong", "csrf_token": csrf}).status_code == 403  # bad link
    cross = stu.post(f"/checkin/{eid}", data={"t": token, "csrf_token": csrf},
                     headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
    assert cross.status_code == 403
    r = stu.post(f"/checkin/{eid}", data={"t": token, "csrf_token": csrf})                         # not registered
    assert r.status_code == 400 and "not registered" in r.text
    assert admin.delete(f"/api/attendance/{eid}/qr").status_code == 200                            # code revoked
    assert stu.get(f"/checkin/{eid}?t={token}").status_code == 403
    assert checkins_of(admin, eid) == set()


def test_150_students_behind_one_campus_address_can_check_in(env, ip_from_header, monkeypatch):
    """A whole class on campus Wi-Fi shares one public address: everyone signs in and checks in."""
    A = ip_from_header
    from core.db.repositories import RegistrationRepository
    from tests.conftest import make_student
    monkeypatch.setattr(A, "HASH_WAIT_SECONDS", 120.0)
    admin = admin_client(env)
    eid, token = make_today_event(admin, "Load test")
    n, pw = 150, _pw()
    pw_hash = A.hash_password(pw)
    sids = [f"LOAD{i:03d}" for i in range(n)]
    with env["Database"](env["db_path"]) as db:
        with db.transaction():
            for sid in sids:
                make_student(db, sid)
                RegistrationRepository(db).register(sid, eid, 3, 0)
                db.execute("INSERT INTO users(email, full_name, role, student_id, link_status, password_hash) "
                           "VALUES (?, 'Load Student', 'student', ?, 'verified', ?)", (f"{sid.lower()}@ncuindia.edu", sid, pw_hash))
    # Each student uses their own client (own cookie jar = own session), closed when that student is
    # done, so the test process does not hold 150+ event loops open at once (macOS allows only 256
    # open files per process by default; the server itself is a single process with one loop).
    from fastapi.testclient import TestClient
    # ten students mistype their password three times first (still under the free allowance)
    for sid in sids[:10]:
        with TestClient(env["S"].app) as c:
            for k in range(3):
                assert c.post("/api/auth/login", json={"email": f"{sid.lower()}@ncuindia.edu", "password": f"oops{k}x"}).status_code == 401

    def one(sid):
        with TestClient(env["S"].app) as c:
            r = login(c, f"{sid.lower()}@ncuindia.edu", pw)
            if r.status_code != 200:
                return sid, "login", r.status_code
            page = c.get(f"/checkin/{eid}?t={token}")
            if page.status_code != 200:
                return sid, "page", page.status_code
            r = c.post(f"/checkin/{eid}", data={"t": token, "csrf_token": checkin_csrf(page.text)})
            return sid, "checkin", r.status_code

    started = time.monotonic()
    with ThreadPoolExecutor(20) as pool:
        results = list(pool.map(one, sids))
    elapsed = time.monotonic() - started
    failures = [r for r in results if r[1] != "checkin" or r[2] != 200]
    assert not failures, failures[:5]
    assert checkins_of(admin, eid) == set(sids)
    assert A.HASH_STATS["peak"] <= A.CONFIG.hash_concurrency
    print(f"\n150 students, one address: all checked in in {elapsed:.1f} s")


# ============================================================================ 9. desk check-in
def test_desk_checkin_is_admin_only(env):
    admin = admin_client(env)
    eid, _ = make_today_event(admin)
    assert admin.post(f"/api/events/{eid}/registrations", json={"student_id": "STU029"}).status_code == 201
    stu = student_client(env, "desk.try@ncuindia.edu", "STU029", verify_with=admin)
    assert env["client"]().post(f"/api/attendance/{eid}/checkin", json={"student_id": "STU029"}).status_code == 401
    assert stu.post(f"/api/attendance/{eid}/checkin", json={"student_id": "STU029"}).status_code == 403
    assert checkins_of(admin, eid) == set()
    assert admin.post(f"/api/attendance/{eid}/checkin", json={"student_id": "STU029"}).status_code == 200
    assert checkins_of(admin, eid) == {"STU029"}


# ============================================================================ students: own records only
def test_students_reach_only_their_own_records_and_no_admin_route(env):
    S, admin = env["S"], admin_client(env)
    me = student_client(env, "me.only@ncuindia.edu", "STU029", verify_with=admin)
    other = student_client(env, "other.one@ncuindia.edu", "STU089", verify_with=admin)
    for path in ("/api/student/profile", "/api/student/registrations", "/api/student/attendance",
                 "/api/student/predictions", "/api/student/summary"):
        mine = me.get(path + "?student_id=STU089").json()                  # parameters cannot choose the student
        assert "STU089" not in json.dumps(mine), path
        assert "STU029" not in json.dumps(other.get(path).json()), path
    for method, path in admin_routes(S):
        assert me.request(method, path, json={}).status_code == 403, (method, path)
    assert me.get("/api/admin/students/STU089").status_code == 403
    assert me.get("/api/health/details").status_code == 403


# ============================================================================ 10. headers and health
def test_security_headers_everywhere(env):
    c = env["client"]()
    for path in ("/", "/api/health", "/api/auth/me", "/checkin/EV01?t=x", "/assets/app.js"):
        r = c.get(path)
        h = r.headers
        assert "default-src 'self'" in h["content-security-policy"] and "frame-ancestors 'none'" in h["content-security-policy"], path
        assert "script-src 'self'" in h["content-security-policy"] and "unsafe-eval" not in h["content-security-policy"]
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY", path
        assert h["referrer-policy"] == "same-origin" and "camera=()" in h["permissions-policy"], path
        assert h["cross-origin-opener-policy"] == "same-origin", path
        assert "strict-transport-security" not in h                         # HSTS only in production (HTTPS)
    assert c.get("/api/health").headers["cache-control"] == "no-store"
    assert c.get("/checkin/EV01?t=x").headers["cache-control"] == "no-store"


def test_public_health_is_minimal(env):
    anon = env["client"]()
    r = anon.get("/api/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert anon.get("/api/health/details").status_code == 401
    details = admin_client(env).get("/api/health/details").json()
    assert {"version", "database", "schema_version", "active_model", "demo"} <= set(details)


# ============================================================================ 11. CSRF on every state-changing request
PUBLIC_UNSAFE = {("POST", "/api/auth/register"), ("POST", "/api/auth/login"), ("POST", "/api/auth/logout"),
                 ("POST", "/api/auth/reset-password"), ("POST", "/checkin/{event_id}")}


def _all_routes(S):
    out = []
    for r in list(S.app.routes) + list(S.admin.routes) + list(S.auth.auth_router.routes) + \
            list(S.auth.admin_router.routes) + list(S.auth.student_router.routes):
        if isinstance(r, APIRoute):
            out.append(r)
    return out


def _depends_on(dependant, target) -> bool:
    return any(d.call is target or _depends_on(d, target) for d in dependant.dependencies)


def test_every_state_changing_route_is_csrf_protected(env):
    S = env["S"]
    seen = set()
    for r in _all_routes(S):
        for m in r.methods & {"POST", "PUT", "PATCH", "DELETE"}:
            seen.add((m, r.path))
            protected = _depends_on(r.dependant, S.auth.require_user)
            # routers carry their dependencies separately from the route's own
            protected = protected or any(getattr(d, "dependency", None) in (S.auth.require_admin, S.auth.require_user,
                                                                          S.auth.require_student)
                                         for d in r.dependencies)
            assert protected or (m, r.path) in PUBLIC_UNSAFE, (m, r.path)
    assert PUBLIC_UNSAFE <= seen


def test_signed_in_requests_without_csrf_token_are_refused(env):
    S, admin = env["S"], admin_client(env)
    token = admin.headers.pop("X-CSRF-Token")
    for method, path in admin_routes(S):
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            r = admin.request(method, path, json={})
            assert r.status_code == 403 and "security token" in r.text, (method, path)
    assert admin.post("/api/auth/logout").status_code == 403
    admin.headers["X-CSRF-Token"] = token
    assert admin.get("/api/auth/me").status_code == 200                       # still signed in


def test_cross_site_and_form_posts_to_public_endpoints_are_refused(env):
    c = env["client"]()
    body = {"email": "admin@eventpulse.test", "password": env["admin_pw"]}
    for headers in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
        assert c.post("/api/auth/login", json=body, headers=headers).status_code == 403
        assert c.post("/api/auth/register", json={}, headers=headers).status_code == 403
    # an HTML form from another site cannot send JSON: form-encoded bodies are refused outright
    assert c.post("/api/auth/login", data=body).status_code == 403
    assert c.post("/api/auth/reset-password", data={"email": "x"}).status_code == 403
    same = c.post("/api/auth/login", json=body, headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"})
    assert same.status_code == 200


# ============================================================================ 12. admin-controlled password reset
def _uid(admin, email):
    return next(u["user_id"] for u in admin.get("/api/admin/accounts").json() if u["email"] == email)


def test_admin_password_reset_flow(env):
    A, admin = env["A"], admin_client(env)
    old_pw, new_pw = _pw(), _pw()
    assert register(env["client"](), "forgot.pw@ncuindia.edu", "STU029", old_pw).status_code == 201
    signed_in = env["client"]()
    assert login(signed_in, "forgot.pw@ncuindia.edu", old_pw).status_code == 200
    uid = _uid(admin, "forgot.pw@ncuindia.edu")
    issued = admin.post(f"/api/admin/accounts/{uid}/reset-code")
    assert issued.status_code == 200
    code = issued.json()["code"]
    assert re.fullmatch(r"[2-9A-Z]{4}-[2-9A-Z]{4}-[2-9A-Z]{4}", code)
    acct = next(u for u in admin.get("/api/admin/accounts").json() if u["user_id"] == uid)
    assert acct["reset_code_expires_utc"] == issued.json()["expires_utc"]
    with env["Database"](env["db_path"]) as db:
        stored = db.query("SELECT token_hash, csrf_token FROM auth_sessions WHERE user_id = ?", (uid,))
    assert code not in json.dumps(stored) and code.replace("-", "") not in json.dumps(stored)   # only a hash is kept
    # the code can never act as a session cookie
    probe = env["client"]()
    for v in (code, code.replace("-", "")):
        probe.cookies.set(A.SESSION_COOKIE, v)
        assert probe.get("/api/auth/me").status_code == 401
    c = env["client"]()
    req = {"email": "forgot.pw@ncuindia.edu", "code": "AAAA-BBBB-CCCC", "password": new_pw, "confirm_password": new_pw}
    assert c.post("/api/auth/reset-password", json=req).status_code == 400
    ok = c.post("/api/auth/reset-password", json={**req, "code": code.lower()})          # case-insensitive
    assert ok.status_code == 200
    assert signed_in.get("/api/auth/me").status_code == 401                              # signed out everywhere
    assert login(env["client"](), "forgot.pw@ncuindia.edu", old_pw).status_code == 401
    assert login(env["client"](), "forgot.pw@ncuindia.edu", new_pw).status_code == 200
    assert c.post("/api/auth/reset-password", json={**req, "code": code}).status_code == 400   # single use
    actions = [r["action"] for r in admin.get("/api/admin/security-log").json()]
    assert {"password_reset_issued", "password_reset_completed", "password_reset_failed"} <= set(actions)
    assert code not in json.dumps(admin.get("/api/admin/security-log").json())


def test_reset_codes_expire_are_replaced_and_are_admin_only(env):
    admin = admin_client(env)
    pw = _pw()
    stu = student_client(env, "expiry@ncuindia.edu", "STU029")
    uid = _uid(admin, "expiry@ncuindia.edu")
    first = admin.post(f"/api/admin/accounts/{uid}/reset-code").json()["code"]
    second = admin.post(f"/api/admin/accounts/{uid}/reset-code").json()["code"]
    c = env["client"]()
    req = {"email": "expiry@ncuindia.edu", "password": pw, "confirm_password": pw}
    assert c.post("/api/auth/reset-password", json={**req, "code": first}).status_code == 400   # replaced
    with env["Database"](env["db_path"]) as db:
        with db.transaction():
            db.execute("UPDATE auth_sessions SET expires_utc = '2000-01-01T00:00:00Z' WHERE user_id = ? "
                       "AND csrf_token LIKE 'pwreset:%'", (uid,))
    assert c.post("/api/auth/reset-password", json={**req, "code": second}).status_code == 400  # expired
    assert stu.post(f"/api/admin/accounts/{uid}/reset-code").status_code == 403
    assert env["client"]().post(f"/api/admin/accounts/{uid}/reset-code").status_code == 401
    admin_uid = 0
    with env["Database"](env["db_path"]) as db:
        admin_uid = db.scalar("SELECT user_id FROM users WHERE role = 'admin'")
    assert admin.post(f"/api/admin/accounts/{admin_uid}/reset-code").status_code == 404        # never for the admin
    assert admin.post(f"/api/admin/accounts/{uid}", json={"action": "deactivate"}).status_code == 200
    assert admin.post(f"/api/admin/accounts/{uid}/reset-code").status_code == 400


def test_reset_attempts_are_limited_per_account(env):
    pw = _pw()
    c = env["client"]()
    req = {"email": "guess@ncuindia.edu", "code": "AAAA-BBBB-CCCC", "password": pw, "confirm_password": pw}
    codes = [c.post("/api/auth/reset-password", json=req).status_code for _ in range(6)]
    assert codes == [400] * 5 + [429]


# ============================================================================ registration policy
@pytest.mark.parametrize("email", ["someone@gmail.com", "x@ncuindia.edu.evil.com", "x@mail.ncuindia.edu",
                                   "x@ncuindia.org", "ncuindia.edu@gmail.com"])
def test_registration_requires_a_college_email(env, email):
    r = register(env["client"](), email, "STU029", _pw())
    assert r.status_code == 422 and "college e-mail" in r.text


def test_college_email_registration_is_pending_until_admin_approves(env):
    admin = admin_client(env)
    pw = _pw()
    r = register(env["client"](), "New.Student@NCUINDIA.EDU", "STU029", pw)
    assert r.status_code == 201 and r.json()["user"]["link_status"] == "pending"
    assert "approval" in r.json()["message"]
    stu = env["client"]()
    assert login(stu, "new.student@ncuindia.edu", pw).status_code == 200
    assert stu.get("/api/student/registrations").json()["rows"] == []                # nothing before approval
    r = env["client"]().post("/api/auth/register", json={"full_name": "Sneaky", "email": "s@ncuindia.edu",
                                                         "student_id": "STU089", "password": pw, "confirm_password": pw,
                                                         "role": "admin"})
    assert r.status_code == 422                                                      # role cannot be chosen
    with env["Database"](env["db_path"]) as db:
        assert db.scalar("SELECT COUNT(*) FROM users WHERE role = 'admin'") == 1     # exactly one admin
    assert admin is not None
