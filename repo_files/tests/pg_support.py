"""PostgreSQL test support: a fresh, throw-away database per test on a LOCAL server only.

Where the server comes from (first that applies):
1. ``EVENTPULSE_TEST_PG_URL`` - URL of a maintenance database on a disposable local server,
   e.g. ``postgresql://eptest@/postgres?host=/tmp/pgtest&port=55432``.
2. A temporary cluster started here with ``initdb``/``pg_ctl`` when PostgreSQL server binaries are
   installed and the tests do not run as root (Unix socket only, no TCP port, deleted afterwards).
Otherwise PostgreSQL tests are skipped.

Safety: only local servers are accepted (Unix socket, localhost, 127.0.0.1, ::1) so a test can never
reach a cloud database such as Supabase. Every test database is named ``ep_test_<random>`` and dropped
afterwards. Only synthetic data is ever written. The real SQLite database is never opened.

Driver: psycopg if installed, else the ctypes libpq shim in tests/pg_libpq.py.

Production-like mode (``EVENTPULSE_TEST_PG_PRODLIKE=1``), for a local cluster prepared like the
cloud database (SSL-only TCP, SCRAM passwords, en_US collation, the eventpulse_app role):
* ``EVENTPULSE_TEST_PG_URL`` is the administrator's maintenance URL (Unix socket);
* every test database gets scripts/postgres/setup_private_schema.sql (private "eventpulse"
  schema owned by eventpulse_app; no access for API roles);
* the application connects through ``EVENTPULSE_TEST_PG_APP_URL`` - a template such as
  ``postgresql://eventpulse_app@pg-prodlike.test:55434/{db}?hostaddr=127.0.0.1`` - with the
  password read from ``EVENTPULSE_TEST_PG_APP_PASSWORD_FILE``. The host NAME is not local, so the
  application adds sslmode=require itself; ``hostaddr`` must still be a local address.
"""
from __future__ import annotations

import atexit
import glob
import os
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit

import pytest

LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1"}
_state: dict = {}


def _is_local(url: str) -> bool:
    """Where the connection really goes: hostaddr if given, else the host names."""
    parts = urlsplit(url)
    q = parse_qs(parts.query)
    hostaddr = q.get("hostaddr", [""])[0]
    if hostaddr:
        return all(a.strip() in LOCAL_HOSTS - {""} for a in hostaddr.split(","))
    host = parts.hostname or ""
    qhost = q.get("host", [""])[0]
    return (host in LOCAL_HOSTS) and (qhost == "" or qhost.startswith("/") or qhost in LOCAL_HOSTS)


def prodlike() -> bool:
    return os.environ.get("EVENTPULSE_TEST_PG_PRODLIKE") == "1"


ROOT = Path(__file__).resolve().parent.parent
SETUP_SQL = ROOT / "scripts" / "postgres" / "setup_private_schema.sql"


def _with_db(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/" + name, parts.query, ""))


def app_url(name: str) -> str:
    """Production-like mode: the application's URL for test database ``name``."""
    from urllib.parse import quote
    tpl = os.environ["EVENTPULSE_TEST_PG_APP_URL"]
    pw = Path(os.environ["EVENTPULSE_TEST_PG_APP_PASSWORD_FILE"]).read_text().strip()
    url = tpl.format(db=name).replace("eventpulse_app@", f"eventpulse_app:{quote(pw, safe='')}@", 1)
    if not _is_local(url):
        pytest.fail("EVENTPULSE_TEST_PG_APP_URL must connect to a LOCAL address (use hostaddr=127.0.0.1)")
    return url


def _driver() -> str:
    from core.db import postgres
    if os.environ.get("EVENTPULSE_TEST_REQUIRE_PSYCOPG") == "1":   # Phase 2: the real driver or fail
        import psycopg  # noqa: F401  (ImportError here fails the run instead of falling back)
        postgres.set_driver_factory(None)
        return "psycopg"
    try:
        import psycopg  # noqa: F401
        postgres.set_driver_factory(None)
        return "psycopg"
    except ImportError:
        from tests import pg_libpq
        if not pg_libpq.AVAILABLE:
            pytest.skip("no PostgreSQL driver: psycopg not installed and libpq not found")
        postgres.set_driver_factory(pg_libpq.LibpqRaw)
        return "libpq-shim (tests only)"


def _start_temp_cluster() -> str | None:
    if os.name != "posix" or os.geteuid() == 0:
        return None
    bins = sorted(glob.glob("/usr/lib/postgresql/*/bin")) + ["/opt/homebrew/bin", "/usr/local/bin"]
    initdb = shutil.which("initdb") or next((f"{b}/initdb" for b in bins if os.path.exists(f"{b}/initdb")), None)
    if not initdb:
        return None
    bindir = os.path.dirname(initdb)
    root = Path(tempfile.mkdtemp(prefix="ep_pgtest_"))
    data, sock = root / "data", root
    subprocess.run([initdb, "-D", str(data), "-U", "eptest", "-A", "trust", "-E", "UTF8", "--no-locale"],
                   check=True, capture_output=True)
    port = "55433"
    subprocess.run([f"{bindir}/pg_ctl", "-D", str(data), "-l", str(root / "log"), "-w", "-o",
                    f"-k {sock} -p {port} -c listen_addresses=''", "start"], check=True, capture_output=True)

    def _stop():
        subprocess.run([f"{bindir}/pg_ctl", "-D", str(data), "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(root, ignore_errors=True)
    atexit.register(_stop)
    return f"postgresql://eptest@/postgres?host={sock}&port={port}"


def server() -> tuple[str, str]:
    """(maintenance URL, driver name) or pytest.skip."""
    if "server" not in _state:
        url = os.environ.get("EVENTPULSE_TEST_PG_URL", "").strip() or _start_temp_cluster()
        _state["server"] = url
    url = _state["server"]
    if not url:
        pytest.skip("no local PostgreSQL server for tests (set EVENTPULSE_TEST_PG_URL or install PostgreSQL)")
    if not _is_local(url):
        pytest.fail("EVENTPULSE_TEST_PG_URL must point to a LOCAL disposable server (refusing remote hosts)")
    return url, _driver()


def _admin_exec(sql: str) -> None:
    from core.db import postgres
    url, _ = server()
    conn = postgres.connect(url)
    try:
        conn.execute(sql)
    finally:
        conn.close()


def create_database(private_schema: bool = True) -> tuple[str, str]:
    """Create an empty database; returns (url the application should use, name).

    Production-like mode: the private schema is set up (unless ``private_schema=False``) and
    the URL is the application role's SSL URL; otherwise the administrator's local URL."""
    url, _ = server()
    name = "ep_test_" + secrets.token_hex(6)
    _admin_exec(f'CREATE DATABASE "{name}"')
    if not prodlike():
        return _with_db(url, name), name
    if private_schema:
        from core.db import postgres
        conn = postgres.connect(_with_db(url, name))
        try:
            conn.executescript(SETUP_SQL.read_text(encoding="utf-8"))
        finally:
            conn.close()
    return app_url(name), name


def admin_url(name: str) -> str:
    """Administrator URL (local socket) for test database ``name``."""
    url, _ = server()
    return _with_db(url, name)


def drop_database(name: str) -> None:
    assert name.startswith("ep_test_")
    _admin_exec(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def engine() -> str:
    return "postgresql" if os.environ.get("EVENTPULSE_TEST_ENGINE", "").lower() in ("postgresql", "postgres", "pg") \
        else "sqlite"
