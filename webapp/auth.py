"""Authentication and role-based access control for the EventPulse AI web app.

Design (see docs/AUTH.md):
* Two roles: exactly ONE ``admin`` (provisioned from the command line, never through the web)
  and self-registered ``student`` accounts. The role is always assigned by the server.
* Passwords: Argon2id when ``argon2-cffi`` is installed, otherwise the standard library's
  scrypt (N=2^17, r=8, p=1 - OWASP's recommended parameters). Plaintext is never stored.
* Sessions: server-side rows in ``auth_sessions``. The browser holds a random token in an
  HttpOnly, SameSite=Strict cookie (Secure when EVENTPULSE_SECURE_COOKIES=1); only its SHA-256
  is stored. Idle timeout 2 h, absolute lifetime 12 h, deleted on logout.
* CSRF: every state-changing request from a signed-in user must send the per-session token in
  the ``X-CSRF-Token`` header (the frontend keeps it in memory only, never in localStorage).
* Brute force (Phase 2): failed sign-ins cause PROGRESSIVE delays per account and per
  account+address (never a permanent lockout); limits are counted per account, with only a high
  per-address ceiling so ~150 students behind one campus NAT address are not blocked together.
  The client address comes from webapp/security.py (X-Forwarded-For only from trusted proxies).
* Password hashing (scrypt, ~64 MB each) runs at most EVENTPULSE_HASH_CONCURRENCY at a time.
* Registration only with a college e-mail address (EVENTPULSE_ALLOWED_EMAIL_DOMAINS, default
  ncuindia.edu); new accounts stay "pending" until the administrator verifies the Student ID.
* Password reset is administrator-controlled: the admin issues a one-time code (30 min) and hands
  it to the student, who sets a new password with it; all of that account's sessions end.
* Authorization is enforced by reusable FastAPI dependencies (``require_admin``,
  ``require_student``) on the server - the React UI only mirrors it.
* A student sees data only for the Student ID an administrator has VERIFIED for the account;
  the ID is always taken from the session, never from the URL or request body.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.db.connection import Database, utc_now
from core.db.errors import DB_ERRORS, DuplicateRecordError
from webapp import security as SEC

# ============================================================================ configuration
MIGRATIONS_DIR = Path(__file__).resolve().parent / "auth_migrations"
# Security configuration (webapp/security.py). In production an unsafe configuration raises
# security.ConfigError here, so the server refuses to start (fail closed).
CONFIG = SEC.load_config()
SESSION_COOKIE = CONFIG.session_cookie          # "__Host-ep_session" when cookies are Secure
CSRF_HEADER = "X-CSRF-Token"
CSRF_FORM_FIELD = "csrf_token"                  # server-rendered forms (QR check-in) send it in the body
IDLE_TIMEOUT = timedelta(minutes=int(os.environ.get("EVENTPULSE_SESSION_IDLE_MINUTES", "120")))
ABSOLUTE_TIMEOUT = timedelta(hours=int(os.environ.get("EVENTPULSE_SESSION_HOURS", "12")))
SECURE_COOKIES = CONFIG.secure_cookies
RESET_CODE_TTL = timedelta(minutes=30)
RESET_MARKER = "pwreset:"                       # auth_sessions rows that are reset codes, not sessions
GENERIC_RESET_ERROR = "This reset code is not valid or has expired. Ask the organiser for a new one."
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
GENERIC_LOGIN_ERROR = "Invalid email or password."
SIMULATED_SOURCES = ("ORIGINAL_SIMULATED", "SYNTHETIC")

# Same privacy rules as the core ``students`` table: pseudonymous IDs only.
STUDENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,39}$")
PHONE_LIKE = re.compile(r"\d{10,}")
ROLL_LIKE = re.compile(r"^\d\d[A-Za-z]+\d{3}$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                      r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
NAME_RE = re.compile(r"^[^\W\d_](?:[^\W\d_]|[ .'\-])*$", re.UNICODE)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


# ============================================================================ schema migration
def _migrations_dir(db: Database | None = None) -> Path:
    """SQLite scripts for the desktop/default database; PostgreSQL equivalents in postgres/."""
    return MIGRATIONS_DIR if db is None or db.is_sqlite else MIGRATIONS_DIR / "postgres"


def _auth_migrations(db: Database | None = None) -> list[tuple[int, str, Path]]:
    out = []
    for p in sorted(_migrations_dir(db).glob("[0-9][0-9][0-9][0-9]_*.sql")):
        out.append((int(p.name[:4]), p.stem[5:], p))
    if [v for v, _, _ in out] != list(range(1, len(out) + 1)):
        raise RuntimeError("Auth migration files must be numbered 0001..N without gaps.")
    return out


def _split(script: str) -> list[str]:
    statements, buf = [], ""
    for line in script.splitlines(keepends=True):
        if line.strip().startswith("--") and not buf.strip():
            continue
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                statements.append(buf.strip())
            buf = ""
    if buf.strip():
        raise RuntimeError("Incomplete SQL statement in auth migration.")
    return statements


def ensure_auth_schema(db: Database) -> list[int]:
    """Apply pending web-auth migrations (additive, transactional, checksummed).

    Recorded in ``web_auth_migrations`` - separate from the core ``schema_migrations`` so the
    desktop app (which refuses databases with a newer core schema) keeps working. A verified
    backup of the database is written to ``<db folder>/backups`` before anything changes."""
    has_table = db.table_exists("web_auth_migrations")
    applied = {r["version"]: r["checksum"] for r in db.query("SELECT version, checksum FROM web_auth_migrations")} \
        if has_table else {}
    migrations = _auth_migrations(db)
    known = {v for v, _, _ in migrations}
    if applied and max(applied) > max(known):
        raise RuntimeError("The database has a newer web-auth schema than this version of EventPulse AI.")
    for v, name, path in migrations:
        if v in applied and applied[v] != hashlib.sha256(path.read_bytes()).hexdigest():
            raise RuntimeError(f"Auth migration {v:04d}_{name} was modified after it was applied; refusing to start.")
    pending = [m for m in migrations if m[0] not in applied]
    if not pending:
        return []
    if db.is_sqlite:
        from core.db.backup import backup_database
        backup_database(db, label="pre_auth_migration", backup_dir=db.path.parent / "backups")
    elif applied and os.environ.get("EVENTPULSE_PG_BACKUP_CONFIRMED") != "1":
        # PostgreSQL: no file to copy. Upgrading an existing auth schema needs a pg_dump first.
        raise RuntimeError("The PostgreSQL auth schema needs an upgrade. Take a backup (pg_dump) first, then set "
                           "EVENTPULSE_PG_BACKUP_CONFIRMED=1 and start again.")
    done = []
    for v, name, path in pending:
        sql = path.read_text(encoding="utf-8")
        try:
            db.conn.execute("BEGIN IMMEDIATE")
            db.conn.execute("CREATE TABLE IF NOT EXISTS web_auth_migrations (version INTEGER PRIMARY KEY, "
                            "name TEXT NOT NULL, checksum TEXT NOT NULL, applied_utc TEXT NOT NULL)")
            if db.is_sqlite:
                for stmt in _split(sql):
                    db.conn.execute(stmt)
            else:
                db.conn.executescript(sql)            # PostgreSQL DDL is transactional
            db.conn.execute("INSERT INTO web_auth_migrations VALUES (?,?,?,?)",
                            (v, name, hashlib.sha256(sql.encode("utf-8")).hexdigest(), utc_now()))
            db.conn.execute("COMMIT")
        except DB_ERRORS as exc:
            try:
                db.conn.execute("ROLLBACK")
            except DB_ERRORS:
                pass
            raise RuntimeError(f"Auth migration {v:04d}_{name} failed and was rolled back: {exc}") from exc
        done.append(v)
    return done


def rollback_auth_schema(db: Database) -> None:
    """Reverse migration 0001 (drops ONLY the auth tables) after taking a verified backup."""
    if db.is_sqlite:
        from core.db.backup import backup_database
        backup_database(db, label="pre_auth_rollback", backup_dir=db.path.parent / "backups")
    elif os.environ.get("EVENTPULSE_PG_BACKUP_CONFIRMED") != "1":
        raise RuntimeError("Take a PostgreSQL backup (pg_dump) first, then set EVENTPULSE_PG_BACKUP_CONFIRMED=1.")
    sql = (_migrations_dir(db) / "rollback_0001_accounts.sql").read_text(encoding="utf-8")
    db.conn.execute("BEGIN IMMEDIATE")
    try:
        if db.is_sqlite:
            for stmt in _split(sql):
                db.conn.execute(stmt)
        else:
            db.conn.executescript(sql)
        db.conn.execute("COMMIT")
    except DB_ERRORS:
        db.conn.execute("ROLLBACK")
        raise
    _ENSURED.discard(db.identity)


_ENSURED: set[str] = set()
_ENSURE_LOCK = threading.Lock()


def ensure_auth_schema_once(db: Database) -> None:
    key = db.identity
    if key in _ENSURED:
        return
    with _ENSURE_LOCK:
        if key not in _ENSURED:
            ensure_auth_schema(db)
            _ENSURED.add(key)


# ============================================================================ passwords
try:                                                    # preferred when installed
    from argon2 import PasswordHasher as _PH
    from argon2.exceptions import InvalidHashError as _BadHash, VerificationError as _VerifyError
    _ARGON = _PH()
except ImportError:                                     # pragma: no cover - depends on environment
    _ARGON = None

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 17, 8, 1
_SCRYPT_MAXMEM = 256 * 1024 * 1024


class HashingBusy(RuntimeError):
    """Too many password hashes are being computed; the request should be retried shortly."""


# Each scrypt hash needs ~64 MB (Argon2 similar). Cap how many run at once so a burst of sign-ins
# cannot exhaust the memory of a small (512 MB) hosting instance. Waiting requests queue briefly.
HASH_WAIT_SECONDS = float(os.environ.get("EVENTPULSE_HASH_WAIT_SECONDS", "30"))
_HASH_SLOTS = threading.BoundedSemaphore(CONFIG.hash_concurrency)
_HASH_STATS_LOCK = threading.Lock()
HASH_STATS = {"active": 0, "peak": 0, "busy": 0}


@contextmanager
def _hash_slot():
    if not _HASH_SLOTS.acquire(timeout=HASH_WAIT_SECONDS):
        with _HASH_STATS_LOCK:
            HASH_STATS["busy"] += 1
        raise HashingBusy("The server is busy. Please try again in a few seconds.")
    with _HASH_STATS_LOCK:
        HASH_STATS["active"] += 1
        HASH_STATS["peak"] = max(HASH_STATS["peak"], HASH_STATS["active"])
    try:
        yield
    finally:
        with _HASH_STATS_LOCK:
            HASH_STATS["active"] -= 1
        _HASH_SLOTS.release()


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def hash_password(password: str) -> str:
    with _hash_slot():
        if _ARGON is not None:
            return _ARGON.hash(password)
        salt = secrets.token_bytes(16)
        dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                            maxmem=_SCRYPT_MAXMEM, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        if stored.startswith("$argon2"):
            if _ARGON is None:
                return False
            try:
                with _hash_slot():
                    return _ARGON.verify(stored, password)
            except (_VerifyError, _BadHash):
                return False
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        with _hash_slot():
            calc = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r),
                                  p=int(p), maxmem=_SCRYPT_MAXMEM, dklen=len(base64.b64decode(dk)))
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except (ValueError, TypeError):
        return False


_DUMMY_HASH: list[str] = []


def _burn_time(password: str) -> None:
    """Spend the same time as a real check when the account does not exist (no user enumeration)."""
    if not _DUMMY_HASH:
        _DUMMY_HASH.append(hash_password(secrets.token_urlsafe(16)))
    verify_password(password, _DUMMY_HASH[0])


def password_problems(password: str, email: str = "") -> list[str]:
    p = []
    if len(password) < 10:
        p.append("at least 10 characters")
    if len(password) > 128:
        p.append("at most 128 characters")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        p.append("at least one letter and one number")
    if email and password.strip().lower() == email.strip().lower():
        p.append("must not be your email address")
    return p


def student_id_problem(sid: str) -> Optional[str]:
    if not STUDENT_ID_RE.match(sid) or "@" in sid or PHONE_LIKE.search(sid) or ROLL_LIKE.match(sid):
        return ("Use the pseudonymous Student ID given by the event organiser (for example STU001) - "
                "not a roll number, phone number or e-mail.")
    return None


# ============================================================================ rate limiting
class RateLimiter:
    """Sliding-window counter kept in memory (one server process)."""

    def __init__(self, limit: int, window_seconds: int):
        self.limit, self.window = limit, window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque:
        q = self._hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._prune(key, time.monotonic())) >= self.limit

    def hit(self, key: str) -> None:
        with self._lock:
            self._prune(key, time.monotonic()).append(time.monotonic())

    def reset(self, key: Optional[str] = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


# Limits are per ACCOUNT. Per-address limits are only a high ceiling against floods: a whole
# campus (~150+ students) can share one public NAT address and must not be blocked together.
# Failed sign-ins: progressive delay, never a permanent lockout.
#   per account + address : 5 free, then 1 s, 2 s, 4 s ... capped at 15 min
#   per account (any addr): 20 free, then 1 s, 2 s ... capped at 5 min (slows distributed guessing)
LOGIN_DELAY_PER_ACCOUNT_IP = SEC.ProgressiveThrottle(free=5, base=1, cap=15 * 60, forget_after=60 * 60)
LOGIN_DELAY_PER_ACCOUNT = SEC.ProgressiveThrottle(free=20, base=1, cap=5 * 60, forget_after=60 * 60)
LOGIN_ATTEMPTS_PER_IP = RateLimiter(_env_int("EVENTPULSE_LOGIN_IP_CEILING", 1500), 15 * 60)
REGISTRATIONS_PER_ACCOUNT = RateLimiter(5, 60 * 60)          # keyed by e-mail and by claimed Student ID
REGISTRATIONS_PER_IP = RateLimiter(_env_int("EVENTPULSE_REGISTER_IP_CEILING", 400), 60 * 60)
CHECKINS_PER_ACCOUNT = RateLimiter(10, 10 * 60)
CHECKINS_PER_IP = RateLimiter(_env_int("EVENTPULSE_CHECKIN_IP_CEILING", 5000), 10 * 60)
RESETS_PER_ACCOUNT = RateLimiter(5, 15 * 60)
RESETS_PER_IP = RateLimiter(_env_int("EVENTPULSE_RESET_IP_CEILING", 300), 15 * 60)
_LIMITERS = (LOGIN_DELAY_PER_ACCOUNT_IP, LOGIN_DELAY_PER_ACCOUNT, LOGIN_ATTEMPTS_PER_IP, REGISTRATIONS_PER_ACCOUNT,
             REGISTRATIONS_PER_IP, CHECKINS_PER_ACCOUNT, CHECKINS_PER_IP, RESETS_PER_ACCOUNT, RESETS_PER_IP)


def reset_rate_limits() -> None:
    for r in _LIMITERS:
        r.reset()


def client_ip(request: Request) -> str:
    """Real client address (X-Forwarded-For is believed only from configured trusted proxies)."""
    return SEC.client_ip(request, CONFIG)


def too_many(message: str, retry_after: int) -> HTTPException:
    return HTTPException(429, message, headers={"Retry-After": str(max(1, int(retry_after)))})


def email_domain_allowed(email: str) -> bool:
    return email.rsplit("@", 1)[-1].strip().lower() in CONFIG.allowed_email_domains


# ============================================================================ accounts & sessions
def audit(db: Database, action: str, user_id: Optional[int] = None, detail: Optional[str] = None) -> None:
    db.execute("INSERT INTO auth_audit(ts_utc, user_id, action, detail) VALUES (?,?,?,?)",
               (utc_now(), user_id, action, detail))


def admin_exists(db: Database) -> bool:
    return bool(db.scalar("SELECT COUNT(*) FROM users WHERE role = 'admin'"))


def create_admin(db: Database, email: str, full_name: str, password: str) -> int:
    """Create THE administrator. Refuses if one exists (never overwrites or resets it)."""
    ensure_auth_schema_once(db)
    email = email.strip()
    if not EMAIL_RE.match(email):
        raise ValueError("Enter a valid e-mail address.")
    if not NAME_RE.match(full_name.strip()) or not 2 <= len(full_name.strip()) <= 100:
        raise ValueError("Enter a valid name.")
    problems = password_problems(password, email)
    if problems:
        raise ValueError("Password needs " + ", ".join(problems) + ".")
    with db.transaction():
        if admin_exists(db):
            raise PermissionError("An administrator account already exists. It was NOT changed.")
        cur = db.execute("INSERT INTO users(email, full_name, role, password_hash) VALUES (?,?, 'admin', ?)",
                         (email, full_name.strip(), hash_password(password)))
        uid = cur.lastrowid
        audit(db, "admin_provisioned", uid, None)
    return uid


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db: Database, user_id: int) -> tuple[str, str]:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    now = _now()
    with db.transaction():
        db.execute("DELETE FROM auth_sessions WHERE expires_utc < ? OR last_seen_utc < ?",
                   (_iso(now), _iso(now - IDLE_TIMEOUT)))
        db.execute("INSERT INTO auth_sessions(token_hash, user_id, csrf_token, created_utc, last_seen_utc, expires_utc) "
                   "VALUES (?,?,?,?,?,?)", (_token_hash(token), user_id, csrf, _iso(now), _iso(now),
                                            _iso(now + ABSOLUTE_TIMEOUT)))
    return token, csrf


def load_session(db: Database, token: str) -> Optional[dict]:
    if not token or len(token) > 200:
        return None
    s = db.query_one("SELECT s.session_id, s.csrf_token, s.last_seen_utc, s.expires_utc, u.user_id, u.email, "
                     "u.full_name, u.role, u.student_id, u.link_status, u.is_active "
                     "FROM auth_sessions s JOIN users u USING(user_id) WHERE s.token_hash = ? AND s.csrf_token NOT LIKE ?",
                     (_token_hash(token), RESET_MARKER + "%"))
    if not s:
        return None
    now = _now()
    if not s["is_active"] or _parse(s["expires_utc"]) <= now or _parse(s["last_seen_utc"]) + IDLE_TIMEOUT <= now:
        db.execute("DELETE FROM auth_sessions WHERE session_id = ?", (s["session_id"],))
        return None
    if (now - _parse(s["last_seen_utc"])).total_seconds() >= 60:
        db.execute("UPDATE auth_sessions SET last_seen_utc = ? WHERE session_id = ?", (_iso(now), s["session_id"]))
    return s


def public_user(s: dict) -> dict:
    return {"user_id": s["user_id"], "email": s["email"], "full_name": s["full_name"], "role": s["role"],
            "student_id": s["student_id"], "link_status": s["link_status"]}


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(SESSION_COOKIE, token, max_age=int(ABSOLUTE_TIMEOUT.total_seconds()), path="/",
                        httponly=True, secure=SECURE_COOKIES, samesite="strict")


def _clear_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=SECURE_COOKIES, samesite="strict")


# ============================================================================ admin-controlled password reset
# Codes are kept (hashed) as marked rows in auth_sessions, so Phase 2 needs NO schema change. Such
# rows can never act as a session: load_session() skips them and their hash has its own prefix.
_RESET_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"     # no 0/O, 1/I/L


def _normalise_code(code: str) -> str:
    return re.sub(r"[\s-]", "", code or "").upper()


def _reset_hash(code: str) -> str:
    return hashlib.sha256((RESET_MARKER + _normalise_code(code)).encode("utf-8")).hexdigest()


def issue_reset_code(db: Database, user_id: int) -> tuple[str, str]:
    """Create a one-time reset code for an account (replacing any earlier one). Returns (code, expiry)."""
    raw = "".join(secrets.choice(_RESET_ALPHABET) for _ in range(12))          # ~59 bits
    code = f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"
    now = _now()
    expires = now + min(RESET_CODE_TTL, IDLE_TIMEOUT)
    db.execute("DELETE FROM auth_sessions WHERE user_id = ? AND csrf_token LIKE ?", (user_id, RESET_MARKER + "%"))
    db.execute("INSERT INTO auth_sessions(token_hash, user_id, csrf_token, created_utc, last_seen_utc, expires_utc) "
               "VALUES (?,?,?,?,?,?)", (_reset_hash(code), user_id, RESET_MARKER + secrets.token_urlsafe(32),
                                        _iso(now), _iso(now), _iso(expires)))
    return code, _iso(expires)


def pending_reset(db: Database, user_id: int) -> Optional[str]:
    row = db.query_one("SELECT expires_utc FROM auth_sessions WHERE user_id = ? AND csrf_token LIKE ?",
                       (user_id, RESET_MARKER + "%"))
    return row["expires_utc"] if row and _parse(row["expires_utc"]) > _now() else None


# ============================================================================ student data (ownership-scoped)
def linked_student_id(s: dict) -> Optional[str]:
    """The Student ID whose records this account may see - only after an administrator verified it."""
    return s["student_id"] if s["role"] == "student" and s["link_status"] == "verified" else None


def student_profile(db: Database, sid: str) -> Optional[dict]:
    return db.query_one(
        "SELECT s.student_id, c.name AS course, c.department, s.year, s.experience_level, s.area_of_interest, "
        "s.role AS uba_role, s.currently_uba_volunteer, s.previous_event_attendance, s.previous_events_registered, "
        "s.previous_attendance_rate, s.data_source FROM students s JOIN courses c USING(course_id) "
        "WHERE s.student_id = ?", (sid,))


def student_registrations(db: Database, sid: str) -> list[dict]:
    return db.query(
        "SELECT r.registration_id, r.event_id, e.name AS event_name, e.event_type, e.event_date, e.time_slot, "
        "e.venue, e.status AS event_status, r.status, r.data_source, r.created_utc "
        "FROM registrations r JOIN events e USING(event_id) WHERE r.student_id = ? "
        "ORDER BY COALESCE(e.event_date, '') DESC, r.event_id DESC", (sid,))


def student_attendance(db: Database, sid: str) -> list[dict]:
    rows = db.query(
        "SELECT r.event_id, e.name AS event_name, e.event_type, e.event_date, a.attended, a.source, a.recorded_utc, "
        "ck.method AS checkin_method, ck.checked_in_utc "
        "FROM registrations r JOIN events e USING(event_id) "
        "LEFT JOIN attendance_records a ON a.registration_id = r.registration_id "
        "LEFT JOIN checkins ck ON ck.registration_id = r.registration_id "
        "WHERE r.student_id = ? AND (a.attendance_id IS NOT NULL OR ck.checkin_id IS NOT NULL) "
        "ORDER BY COALESCE(e.event_date, '') DESC, r.event_id DESC", (sid,))
    for r in rows:
        r["simulated"] = r["source"] in SIMULATED_SOURCES
        r["outcome"] = ("Not finalised" if r["attended"] is None else "Attended" if r["attended"] else "Absent")
    return rows


def student_predictions(db: Database, sid: str, limit: int = 500) -> list[dict]:
    return db.query(
        "SELECT p.prediction_id, p.predicted_utc, COALESCE(r.event_id, p.event_ref) AS event_id, e.name AS event_name, "
        "p.probability, p.predicted_attend, p.certainty, p.forecast_used, "
        "mv.name || ' v' || mv.version AS model "
        "FROM predictions p JOIN model_versions mv USING(model_version_id) "
        "LEFT JOIN registrations r ON r.registration_id = p.registration_id "
        "LEFT JOIN events e ON e.event_id = COALESCE(r.event_id, p.event_ref) "
        "WHERE r.student_id = ? OR (p.registration_id IS NULL AND p.student_ref = ?) "
        "ORDER BY p.prediction_id DESC LIMIT ?", (sid, sid, limit))


def student_summary(db: Database, sid: str) -> dict:
    regs = student_registrations(db, sid)
    att = [a for a in student_attendance(db, sid) if a["attended"] is not None]
    rec = [a for a in att if not a["simulated"]]
    sim = [a for a in att if a["simulated"]]
    today = date.today().isoformat()
    upcoming = [r for r in regs if r["status"] == "Registered" and r["event_status"] == "Scheduled"
                and (r["event_date"] or "9999") >= today]
    preds = student_predictions(db, sid, 1)
    rate = lambda xs: (sum(a["attended"] for a in xs) / len(xs)) if xs else None
    return {"registrations": len(regs), "active_registrations": sum(r["status"] == "Registered" for r in regs),
            "upcoming_events": len(upcoming), "recorded_outcomes": len(rec), "recorded_attended": sum(a["attended"] for a in rec),
            "recorded_attendance_rate": rate(rec), "simulated_outcomes": len(sim),
            "simulated_attendance_rate": rate(sim), "latest_prediction": preds[0] if preds else None}


def student_bundle(db: Database, sid: str) -> dict:
    return {"profile": student_profile(db, sid), "summary": student_summary(db, sid),
            "registrations": student_registrations(db, sid), "attendance": student_attendance(db, sid),
            "predictions": student_predictions(db, sid)}


# ============================================================================ request models
class RegisterIn(BaseModel):
    """Public student registration. ``extra='forbid'``: a request that tries to set ``role``
    (or anything else) is rejected outright; the server always assigns role 'student'."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    full_name: str = Field(min_length=2, max_length=100)
    email: str = Field(min_length=3, max_length=254)
    student_id: str = Field(min_length=2, max_length=40)
    password: str = Field(min_length=1, max_length=128)
    confirm_password: str = Field(min_length=1, max_length=128)

    @field_validator("full_name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME_RE.match(v):
            raise ValueError("use letters, spaces, apostrophes, hyphens or full stops only")
        return re.sub(r"\s+", " ", v)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        if not EMAIL_RE.match(v):
            raise ValueError("enter a valid e-mail address")
        if not email_domain_allowed(v):
            raise ValueError("use your college e-mail address ("
                             + " or ".join("@" + d for d in CONFIG.allowed_email_domains) + ")")
        return v

    @field_validator("student_id")
    @classmethod
    def _sid(cls, v: str) -> str:
        problem = student_id_problem(v)
        if problem:
            raise ValueError(problem)
        return v

    @model_validator(mode="after")
    def _passwords(self):
        problems = password_problems(self.password, self.email)
        if problems:
            raise ValueError("Password needs " + ", ".join(problems) + ".")
        if not hmac.compare_digest(self.password, self.confirm_password):
            raise ValueError("The two passwords do not match.")
        return self


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class AccountAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["verify", "unverify", "reject", "deactivate", "activate"]


class ResetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    code: str = Field(min_length=12, max_length=24)
    password: str = Field(min_length=1, max_length=128)
    confirm_password: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _passwords(self):
        problems = password_problems(self.password, self.email)
        if problems:
            raise ValueError("Password needs " + ", ".join(problems) + ".")
        if not hmac.compare_digest(self.password, self.confirm_password):
            raise ValueError("The two passwords do not match.")
        return self


# ============================================================================ wiring
class Auth:
    """Builds the dependencies and routers around the app's ``get_db`` dependency."""

    def __init__(self, get_db: Callable):
        def current_session(request: Request, db: Database = Depends(get_db)) -> Optional[dict]:
            ensure_auth_schema_once(db)
            s = load_session(db, request.cookies.get(SESSION_COOKIE, ""))
            request.state.session = s
            return s

        def require_user(request: Request, s: Optional[dict] = Depends(current_session)) -> dict:
            if not s:
                raise HTTPException(401, "Please sign in.")
            if request.method not in SAFE_METHODS and \
                    not hmac.compare_digest(request.headers.get(CSRF_HEADER, ""), s["csrf_token"]):
                raise HTTPException(403, "Missing or invalid security token. Reload the page and try again.")
            return s

        def require_admin(s: dict = Depends(require_user)) -> dict:
            if s["role"] != "admin":
                raise HTTPException(403, "This area is for the administrator only.")
            return s

        def require_student(s: dict = Depends(require_user)) -> dict:
            if s["role"] != "student":
                raise HTTPException(403, "This area is for student accounts.")
            return s

        self.current_session, self.require_user = current_session, require_user
        self.require_admin, self.require_student = require_admin, require_student
        self.auth_router = self._auth_router(get_db)
        self.admin_router = self._admin_router(get_db)
        self.student_router = self._student_router(get_db)

    # ------------------------------------------------------------------ /api/auth
    def _auth_router(self, get_db) -> APIRouter:
        r = APIRouter(prefix="/api/auth", tags=["auth"])
        current_session, require_user = self.current_session, self.require_user

        @r.post("/register", status_code=201)
        def register(body: RegisterIn, request: Request, db: Database = Depends(get_db)):
            ensure_auth_schema_once(db)
            ip = client_ip(request)
            keys = ("email:" + body.email.lower(), "sid:" + body.student_id.lower())
            if REGISTRATIONS_PER_IP.blocked(ip):
                raise too_many("Too many registrations from this network. Try again later.", 600)
            if any(REGISTRATIONS_PER_ACCOUNT.blocked(k) for k in keys):
                raise too_many("Too many registration attempts for this e-mail address or Student ID. "
                               "Try again in an hour.", 3600)
            REGISTRATIONS_PER_IP.hit(ip)
            for k in keys:
                REGISTRATIONS_PER_ACCOUNT.hit(k)
            pw_hash = hash_password(body.password)
            try:
                with db.transaction():
                    if db.scalar("SELECT COUNT(*) FROM users WHERE lower(email) = lower(?) OR lower(student_id) = lower(?)",
                                 (body.email, body.student_id)):
                        raise HTTPException(409, "An account with this e-mail address or Student ID already exists. "
                                                 "Sign in, or contact the organiser if this is your Student ID.")
                    cur = db.execute("INSERT INTO users(email, full_name, role, student_id, link_status, password_hash) "
                                     "VALUES (?,?, 'student', ?, 'pending', ?)",
                                     (body.email, body.full_name, body.student_id, pw_hash))
                    audit(db, "student_registered", cur.lastrowid, None)
            except (sqlite3.IntegrityError, DuplicateRecordError):   # race with a concurrent identical registration
                raise HTTPException(409, "An account with this e-mail address or Student ID already exists.")
            return {"message": "Account created and waiting for the organiser's approval. You can sign in, but "
                               "your records and QR check-in unlock only after the organiser verifies your "
                               "Student ID.",
                    "user": {"email": body.email, "full_name": body.full_name, "student_id": body.student_id,
                             "role": "student", "link_status": "pending"}}

        @r.post("/login")
        def login(body: LoginIn, request: Request, response: Response, db: Database = Depends(get_db)):
            ensure_auth_schema_once(db)
            ip, acct = client_ip(request), body.email.strip().lower()
            pair = f"{acct}|{ip}"
            if LOGIN_ATTEMPTS_PER_IP.blocked(ip):
                raise too_many("Too many sign-in attempts from this network. Try again in a few minutes.", 60)
            wait = max(LOGIN_DELAY_PER_ACCOUNT_IP.retry_after(pair), LOGIN_DELAY_PER_ACCOUNT.retry_after(acct))
            if wait:                              # progressive delay - the password is not even checked
                raise too_many(f"Too many failed sign-in attempts. Wait {wait} seconds and try again.", wait)
            LOGIN_ATTEMPTS_PER_IP.hit(ip)
            u = db.query_one("SELECT user_id, password_hash, is_active FROM users WHERE lower(email) = lower(?)", (body.email.strip(),))
            ok = verify_password(body.password, u["password_hash"]) if u else (_burn_time(body.password) or False)
            if not ok or not u["is_active"]:
                LOGIN_DELAY_PER_ACCOUNT_IP.failure(pair)
                LOGIN_DELAY_PER_ACCOUNT.failure(acct)
                with db.transaction():
                    audit(db, "login_failed", u["user_id"] if u else None, None if u else "unknown account")
                raise HTTPException(401, GENERIC_LOGIN_ERROR)
            LOGIN_DELAY_PER_ACCOUNT_IP.reset(pair)
            LOGIN_DELAY_PER_ACCOUNT.reset(acct)
            token, _ = create_session(db, u["user_id"])
            with db.transaction():
                db.execute("UPDATE users SET last_login_utc = ? WHERE user_id = ?", (utc_now(), u["user_id"]))
                audit(db, "login", u["user_id"], None)
            s = load_session(db, token)
            _set_cookie(response, token)
            return {"user": public_user(s), "csrf_token": s["csrf_token"]}

        @r.post("/logout")
        def logout(request: Request, response: Response, s: Optional[dict] = Depends(current_session),
                   db: Database = Depends(get_db)):
            if s:
                if not hmac.compare_digest(request.headers.get(CSRF_HEADER, ""), s["csrf_token"]):
                    raise HTTPException(403, "Missing or invalid security token. Reload the page and try again.")
                with db.transaction():
                    db.execute("DELETE FROM auth_sessions WHERE session_id = ?", (s["session_id"],))
                    audit(db, "logout", s["user_id"], None)
            _clear_cookie(response)
            return {"signed_out": True}

        @r.post("/reset-password")
        def reset_password(body: ResetIn, request: Request, db: Database = Depends(get_db)):
            """Set a new password with a one-time code issued by the administrator."""
            ensure_auth_schema_once(db)
            ip, acct = client_ip(request), body.email.strip().lower()
            if RESETS_PER_IP.blocked(ip):
                raise too_many("Too many reset attempts from this network. Try again in a few minutes.", 60)
            if RESETS_PER_ACCOUNT.blocked(acct):
                raise too_many("Too many reset attempts for this account. Try again in 15 minutes.", 900)
            RESETS_PER_IP.hit(ip)
            RESETS_PER_ACCOUNT.hit(acct)
            u = db.query_one("SELECT user_id, role, is_active FROM users WHERE lower(email) = lower(?)", (body.email.strip(),))
            row = db.query_one("SELECT session_id, expires_utc FROM auth_sessions WHERE user_id = ? AND token_hash = ? "
                               "AND csrf_token LIKE ?", (u["user_id"], _reset_hash(body.code), RESET_MARKER + "%")) if u else None
            if not u or not row or not u["is_active"] or u["role"] != "student" or _parse(row["expires_utc"]) <= _now():
                with db.transaction():
                    audit(db, "password_reset_failed", u["user_id"] if u else None, None if u else "unknown account")
                raise HTTPException(400, GENERIC_RESET_ERROR)
            pw_hash = hash_password(body.password)
            with db.transaction():
                if not db.execute("DELETE FROM auth_sessions WHERE session_id = ?", (row["session_id"],)).rowcount:
                    raise HTTPException(400, GENERIC_RESET_ERROR)          # already used (concurrent redeem)
                db.execute("UPDATE users SET password_hash = ?, updated_utc = ? WHERE user_id = ?",
                           (pw_hash, utc_now(), u["user_id"]))
                db.execute("DELETE FROM auth_sessions WHERE user_id = ?", (u["user_id"],))   # sign out everywhere
                audit(db, "password_reset_completed", u["user_id"], None)
            LOGIN_DELAY_PER_ACCOUNT.reset(acct)
            LOGIN_DELAY_PER_ACCOUNT_IP.reset(f"{acct}|{ip}")
            RESETS_PER_ACCOUNT.reset(acct)
            return {"message": "Your password has been changed. Sign in with the new password."}

        @r.get("/me")
        def me(s: dict = Depends(require_user)):
            return {"user": public_user(s), "csrf_token": s["csrf_token"]}

        return r

    # ------------------------------------------------------------------ /api/admin
    def _admin_router(self, get_db) -> APIRouter:
        r = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(self.require_admin)])

        @r.get("/accounts")
        def accounts(db: Database = Depends(get_db)):
            return db.query(
                "SELECT u.user_id, u.email, u.full_name, u.student_id, u.link_status, u.is_active, u.created_utc, "
                "u.last_login_utc, (SELECT MAX(a.expires_utc) FROM auth_sessions a WHERE a.user_id = u.user_id AND "
                "a.csrf_token LIKE 'pwreset:%') AS reset_code_expires_utc, CASE WHEN EXISTS(SELECT 1 FROM students s WHERE lower(s.student_id) = lower(u.student_id)) "
                "THEN 1 ELSE 0 END AS record_exists, (SELECT COUNT(*) FROM registrations g WHERE g.student_id = u.student_id) AS registrations "
                "FROM users u WHERE u.role = 'student' ORDER BY u.link_status = 'pending' DESC, u.created_utc DESC")

        @r.post("/accounts/{user_id}")
        def account_action(user_id: int, body: AccountAction, s: dict = Depends(self.require_admin),
                           db: Database = Depends(get_db)):
            u = db.query_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
            if not u or u["role"] != "student":
                raise HTTPException(404, "No such student account.")
            with db.transaction():
                if body.action == "verify":
                    if not u["student_id"]:
                        raise HTTPException(400, "This account has no Student ID to verify.")
                    canonical = db.scalar("SELECT student_id FROM students WHERE lower(student_id) = lower(?) ORDER BY student_id LIMIT 1",
                                          (u["student_id"],))
                    if not canonical:
                        raise HTTPException(400, f"There is no student record with ID {u['student_id']}. Import or "
                                                 "create the student record first, then verify the account.")
                    db.execute("UPDATE users SET student_id = ?, link_status = 'verified', updated_utc = ? "
                               "WHERE user_id = ?", (canonical, utc_now(), user_id))
                elif body.action == "unverify":
                    db.execute("UPDATE users SET link_status = 'pending', updated_utc = ? WHERE user_id = ?",
                               (utc_now(), user_id))
                elif body.action == "reject":         # releases the claimed ID so the real owner can use it
                    db.execute("UPDATE users SET link_status = 'rejected', student_id = NULL, updated_utc = ? "
                               "WHERE user_id = ?", (utc_now(), user_id))
                elif body.action == "deactivate":
                    db.execute("UPDATE users SET is_active = 0, updated_utc = ? WHERE user_id = ?", (utc_now(), user_id))
                    db.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
                else:
                    db.execute("UPDATE users SET is_active = 1, updated_utc = ? WHERE user_id = ?", (utc_now(), user_id))
                audit(db, f"account_{body.action}", s["user_id"], f"user {user_id}")
            return db.query_one("SELECT user_id, email, full_name, student_id, link_status, is_active FROM users "
                                "WHERE user_id = ?", (user_id,))

        @r.post("/accounts/{user_id}/reset-code")
        def reset_code(user_id: int, s: dict = Depends(self.require_admin), db: Database = Depends(get_db)):
            """One-time password-reset code for a STUDENT account, shown once to the administrator, who
            gives it to the student in person. The administrator's own password can never be reset
            through the web."""
            u = db.query_one("SELECT user_id, email, role, is_active FROM users WHERE user_id = ?", (user_id,))
            if not u or u["role"] != "student":
                raise HTTPException(404, "No such student account.")
            if not u["is_active"]:
                raise HTTPException(400, "This account is deactivated. Activate it first.")
            with db.transaction():
                code, expires = issue_reset_code(db, user_id)
                audit(db, "password_reset_issued", s["user_id"], f"user {user_id}")
            return {"user_id": user_id, "email": u["email"], "code": code, "expires_utc": expires,
                    "message": "Give this code to the student in person. It works once, for 30 minutes."}

        @r.get("/students/{student_id}")
        def student_detail(student_id: str, db: Database = Depends(get_db)):
            if not student_profile(db, student_id):
                raise HTTPException(404, f"No student record {student_id}.")
            out = student_bundle(db, student_id)
            out["account"] = db.query_one("SELECT user_id, email, full_name, link_status, is_active, last_login_utc "
                                          "FROM users WHERE lower(student_id) = lower(?) AND role = 'student'", (student_id,))
            return out

        @r.get("/security-log")
        def security_log(db: Database = Depends(get_db)):
            return db.query("SELECT a.ts_utc, a.action, a.detail, u.email FROM auth_audit a "
                            "LEFT JOIN users u USING(user_id) ORDER BY a.audit_id DESC LIMIT 200")

        return r

    # ------------------------------------------------------------------ /api/student (own data only)
    def _student_router(self, get_db) -> APIRouter:
        r = APIRouter(prefix="/api/student", tags=["student"])
        require_student = self.require_student

        def scoped(s: dict) -> Optional[str]:
            return linked_student_id(s)

        def notice(s: dict) -> Optional[str]:
            if s["link_status"] == "verified":
                return None
            if s["link_status"] == "rejected":
                return "The organiser could not confirm your Student ID. Contact them to correct it."
            return "Your Student ID is waiting for the organiser to verify it. Your records appear after that."

        @r.get("/profile")
        def profile(s: dict = Depends(require_student), db: Database = Depends(get_db)):
            sid = scoped(s)
            return {"account": public_user(s), "record": student_profile(db, sid) if sid else None, "notice": notice(s)}

        @r.get("/summary")
        def summary(s: dict = Depends(require_student), db: Database = Depends(get_db)):
            sid = scoped(s)
            return {"linked": bool(sid), "notice": notice(s), "summary": student_summary(db, sid) if sid else None}

        @r.get("/registrations")
        def registrations(s: dict = Depends(require_student), db: Database = Depends(get_db)):
            sid = scoped(s)
            return {"linked": bool(sid), "notice": notice(s), "rows": student_registrations(db, sid) if sid else []}

        @r.get("/attendance")
        def attendance(s: dict = Depends(require_student), db: Database = Depends(get_db)):
            sid = scoped(s)
            return {"linked": bool(sid), "notice": notice(s), "rows": student_attendance(db, sid) if sid else []}

        @r.get("/predictions")
        def predictions(s: dict = Depends(require_student), db: Database = Depends(get_db)):
            sid = scoped(s)
            return {"linked": bool(sid), "notice": notice(s), "rows": student_predictions(db, sid) if sid else []}

        return r
