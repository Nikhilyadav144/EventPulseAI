"""Which database each part of EventPulse uses (Phase 1 - Foundation).

* Desktop app: always SQLite (database/eventpulse.db by default). It never reads EVENTPULSE_DB_URL.
* Web app and its tools: SQLite unless EVENTPULSE_DB_URL is set to an explicit postgresql:// URL;
  any other value is refused instead of being mistaken for a file path.

No test here opens the real database: SQLite files are temporary, PostgreSQL databases are throw-away
databases on a LOCAL test server (skipped when none is available).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from config import settings
from core.db.connection import DEFAULT_DB_PATH, Database, open_database, web_database_url

ROOT = Path(__file__).resolve().parent.parent
REMOTE = "postgresql://nobody@db.invalid.example/x"


def _run(code: str, env_extra: dict, unset: tuple = ("EVENTPULSE_DB_URL",)) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in unset}
    env.update(env_extra)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)


# ----------------------------------------------------------------------------- the explicit-URL rule
@pytest.mark.parametrize("value, expected", [(None, None), ("", None), ("   ", None),
                                             ("postgresql://u@h/db", "postgresql://u@h/db"),
                                             (" postgres://u@h/db ", "postgres://u@h/db")])
def test_web_database_url_accepts_only_postgresql(value, expected):
    env = {} if value is None else {"EVENTPULSE_DB_URL": value}
    assert web_database_url(env) == expected


@pytest.mark.parametrize("value", ["database/eventpulse.db", "/tmp/x.db", "sqlite:///x.db", "mysql://u@h/db",
                                   "host=db.example port=5432"])
def test_web_database_url_refuses_anything_else(value):
    with pytest.raises(ValueError, match="postgresql://"):
        web_database_url({"EVENTPULSE_DB_URL": value})


def test_database_url_argument_must_be_postgresql(tmp_path):
    with pytest.raises(ValueError, match="postgresql://"):
        Database(url=str(tmp_path / "x.db"))
    assert not (tmp_path / "x.db").exists()


def test_open_database_defaults_to_sqlite(tmp_path, monkeypatch):
    monkeypatch.delenv("EVENTPULSE_DB_URL", raising=False)
    with open_database(tmp_path / "web.db") as d:
        assert d.dialect == "sqlite" and d.path == tmp_path / "web.db"
        assert d.scalar("SELECT MAX(version) FROM schema_migrations") == 3


def test_open_database_refuses_a_non_postgresql_url(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENTPULSE_DB_URL", str(tmp_path / "typo.db"))
    with pytest.raises(ValueError):
        open_database(tmp_path / "web.db")
    assert not (tmp_path / "typo.db").exists() and not (tmp_path / "web.db").exists()


def test_open_database_uses_postgresql_only_with_an_explicit_url(tmp_path, monkeypatch, request):
    from tests import pg_support
    pg_support.server()                                                       # skips without a local server
    url, name = pg_support.create_database()
    request.addfinalizer(lambda: pg_support.drop_database(name))
    monkeypatch.setenv("EVENTPULSE_DB_URL", url)
    with open_database(tmp_path / "never-created.db") as d:
        assert d.dialect == "postgresql" and d.path is None
        assert d.scalar("SELECT MAX(version) FROM schema_migrations") == 3
    assert not (tmp_path / "never-created.db").exists()


# ----------------------------------------------------------------------------- desktop: always SQLite
DESKTOP_SOURCES = ["main.py", "checkin_app.py", "app.py"] + sorted(
    str(p.relative_to(ROOT)) for p in (ROOT / "gui").rglob("*.py"))


def test_desktop_code_never_reads_the_web_database_url():
    pattern = re.compile(r"EVENTPULSE_DB_URL|open_database|web_database_url|core\.db\.postgres|psycopg")
    hits = [f for f in DESKTOP_SOURCES if pattern.search((ROOT / f).read_text(encoding="utf-8"))]
    assert hits == [], hits
    assert len(DESKTOP_SOURCES) > 10


def test_desktop_default_database_is_the_sqlite_file():
    assert DEFAULT_DB_PATH == settings.DATABASE_DIR / "eventpulse.db"


def test_desktop_context_uses_sqlite_even_with_a_web_url_set(tmp_path):
    pytest.importorskip("PySide6")
    code = ("import sys; sys.path.insert(0, '.');"
            "from PySide6.QtWidgets import QApplication; app = QApplication([]);"
            "from gui.context import AppContext;"
            f"ctx = AppContext(r'{tmp_path / 'desk.db'}');"
            "print(ctx.db.dialect, ctx.db.path.name, ctx.db.scalar('SELECT MAX(version) FROM schema_migrations'),"
            " 'core.db.postgres' in sys.modules); ctx.close()")
    r = _run(code, {"EVENTPULSE_DB_URL": REMOTE}, unset=())
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.split()[-4:] == ["sqlite", "desk.db", "3", "False"]


# ----------------------------------------------------------------------------- web server configuration
def test_web_server_uses_sqlite_without_a_url():
    pytest.importorskip("fastapi")
    r = _run("import webapp.server as S; print(S.DB_URL, S.db_target() == S.DB_PATH, S.DB_PATH.name)", {})
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.split()[-3:] == ["None", "True", "eventpulse.db"]


def test_web_server_uses_postgresql_only_with_an_explicit_url():
    pytest.importorskip("fastapi")
    r = _run("import webapp.server as S; print(S.db_target())", {"EVENTPULSE_DB_URL": REMOTE})
    assert r.returncode == 0, r.stderr[-2000:]                               # importing does not connect
    assert r.stdout.split()[-1] == REMOTE


def test_web_server_refuses_a_non_postgresql_url():
    pytest.importorskip("fastapi")
    r = _run("import webapp.server", {"EVENTPULSE_DB_URL": "database/eventpulse.db"})
    assert r.returncode != 0 and "EVENTPULSE_DB_URL must be a postgresql:// URL" in r.stderr


# ----------------------------------------------------------------------------- web requirements
def test_requirements_web_is_pinned_and_consistent():
    lines = [ln.split("#")[0].strip() for ln in (ROOT / "requirements-web.txt").read_text().splitlines()]
    pins = dict(ln.split("==") for ln in lines if ln)
    assert all(v and "<" not in v and ">" not in v for v in pins.values()), pins      # exact pins only
    assert pins["psycopg"] == pins["psycopg-binary"] == "3.3.9"
    serve = (ROOT / "requirements-serve.txt").read_text()
    assert f"scikit-learn=={pins['scikit-learn']}" in serve                            # same as saved models
    assert not {"PySide6", "mlflow", "pyside6"} & set(pins)
    for other in ("requirements.txt", "requirements-desktop.txt", "requirements-serve.txt"):
        assert "psycopg" not in (ROOT / other).read_text()                              # desktop/CSL236 untouched
