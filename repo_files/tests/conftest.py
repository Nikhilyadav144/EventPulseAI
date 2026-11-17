import sys
import warnings
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore", category=UserWarning)

from config import settings  # noqa: E402
from core import synthetic  # noqa: E402

# ----------------------------------------------------------------------------- real-data protection
# The tests never open the real database or its backups with SQLite (even a read-only open of a
# WAL database refreshes its -shm file). They use a byte copy taken once per session, every backup
# goes to a temporary folder, and a guard compares size, mtime and SHA-256 of every file under
# database/ and models/ before and after the session: any difference fails the run.
import atexit  # noqa: E402
import hashlib  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402

REAL_DB = settings.DATABASE_DIR / "eventpulse.db"
_PROTECTED_DIRS = (settings.DATABASE_DIR, settings.DATABASE_DIR / "backups", settings.MODELS_DIR)
_snapshot_dir: Path | None = None


def _fingerprint() -> dict[str, tuple[int, int, str]]:
    out = {}
    for d in _PROTECTED_DIRS:
        if not d.is_dir():
            continue
        files = d.rglob("*") if d == settings.MODELS_DIR else d.iterdir()
        for p in sorted(files):
            if p.is_file():
                st = p.stat()
                h = hashlib.sha256()
                with open(p, "rb") as fh:                     # plain byte read: no SQLite, no -shm
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
                out[str(p.relative_to(ROOT))] = (st.st_size, st.st_mtime_ns, h.hexdigest())
    return out


def real_db_snapshot() -> Path:
    """Byte copy of database/eventpulse.db (and its -wal, if any), made once per session.

    Only raw bytes are read from the real files, so SQLite never touches them or their -shm.
    Tests open this copy (or copies of it) instead of the real database."""
    global _snapshot_dir
    if _snapshot_dir is None:
        _snapshot_dir = Path(tempfile.mkdtemp(prefix="ep_real_snapshot_"))
        atexit.register(shutil.rmtree, _snapshot_dir, True)
        shutil.copyfile(REAL_DB, _snapshot_dir / "eventpulse.db")
        wal = Path(str(REAL_DB) + "-wal")
        if wal.exists() and wal.stat().st_size:
            shutil.copyfile(wal, _snapshot_dir / "eventpulse.db-wal")
    return _snapshot_dir / "eventpulse.db"


def pytest_sessionstart(session):
    session.config._ep_real_data = _fingerprint()


def pytest_sessionfinish(session, exitstatus):
    before = getattr(session.config, "_ep_real_data", None)
    if before is None:
        return
    after = _fingerprint()
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    rep = session.config.pluginmanager.get_plugin("terminalreporter")
    if changed:
        msg = ("REAL DATA GUARD: files under database/ or models/ changed during the test run "
               "(close the desktop/web app while testing): " + ", ".join(changed))
        if rep:
            rep.write_line(msg, red=True)
        session.exitstatus = 1
    elif rep:
        rep.write_line(f"REAL DATA GUARD: {len(after)} files under database/ and models/ unchanged "
                       "(size, mtime, SHA-256).", green=True)


@pytest.fixture(scope="session", autouse=True)
def _isolated_backup_dir(tmp_path_factory):
    """Every backup made or listed by code under test goes to a temporary folder."""
    from core.db import backup as backup_mod
    old = backup_mod.BACKUP_DIR
    backup_mod.BACKUP_DIR = tmp_path_factory.mktemp("isolated_backups")
    os.environ.setdefault("EVENTPULSE_TEST_ISOLATED_BACKUPS", "1")
    yield
    backup_mod.BACKUP_DIR = old


@pytest.fixture(scope="session")
def original_workbook() -> Path:
    p = settings.ORIGINAL_WORKBOOK
    if not p.exists():
        pytest.skip("Original workbook not present in data/original/")
    return p


@pytest.fixture(scope="session")
def raw_ml(original_workbook) -> pd.DataFrame:
    from core.data_loader import load_original_ml_dataset
    return load_original_ml_dataset(original_workbook).df


@pytest.fixture(scope="session")
def small_synthetic() -> pd.DataFrame:
    return synthetic.generate(n_students=220, n_events=20, seed=7).df


def pytest_configure(config):
    config.addinivalue_line("markers", "sqlite_only: checks SQLite-specific internals (PRAGMA, file backups)")


def pytest_collection_modifyitems(config, items):
    from tests.pg_support import engine
    if engine() != "postgresql":
        return
    skip = pytest.mark.skip(reason="SQLite-specific check (PostgreSQL test mode)")
    for item in items:
        if "sqlite_only" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def db(tmp_path):
    """Fresh empty database. SQLite file by default; with EVENTPULSE_TEST_ENGINE=postgresql a fresh
    throw-away database on a local PostgreSQL test server (see tests/pg_support.py)."""
    from core.db.connection import Database
    from tests import pg_support
    if pg_support.engine() == "postgresql":
        url, name = pg_support.create_database()
        d = Database(url)
        yield d
        d.close()
        pg_support.drop_database(name)
        return
    d = Database(tmp_path / "test.db")
    yield d
    d.close()


@pytest.fixture
def seeded_db(db, trained_models):
    """Database with the processed original dataset imported and the test models registered."""
    from tests import pg_support
    if pg_support.engine() == "postgresql":
        pytest.skip("PostgreSQL test mode uses synthetic data only (see tests/test_pg_compat.py)")
    from core.data_loader import file_sha256
    from core.db import services
    path = settings.PROCESSED_DATASET
    if not path.exists():
        pytest.skip("processed dataset missing")
    services.import_dataset(db, pd.read_csv(path), "PROCESSED", "processed", path.name, file_sha256(path))
    services.sync_models(db, trained_models[0])
    return db


def make_student(db, sid="STU900", **kw):
    from core.db.repositories import StudentRepository
    rec = dict(student_id=sid, course="B.Tech CSE", year=2, currently_uba_volunteer=1,
               participated_uba_event_before=1, experience_level="Some experience",
               area_of_interest="Problem Solving & Decision Making", role="Content",
               previous_event_attendance=4, previous_events_registered=5, academic_engagement_score=70,
               distance_from_venue_km=3.5)
    rec.update(kw)
    return StudentRepository(db).create(**rec)


def make_event(db, eid="EVX1", **kw):
    from core.db.repositories import EventRepository
    rec = dict(event_id=eid, name="Test workshop", event_type="Workshop", event_date="2026-10-14",
               time_slot="Morning", duration_hours=2.0, popularity=65)
    rec.update(kw)
    return EventRepository(db).create(**rec)


@pytest.fixture(scope="session")
def trained_models(tmp_path_factory, small_synthetic):
    """Fast end-to-end training run into a temporary models directory."""
    from core.model_training import train_and_compare
    d = tmp_path_factory.mktemp("models")
    run = train_and_compare(small_synthetic, "SYNTHETIC (test)", fast=True, models_dir=d)
    return d, run
