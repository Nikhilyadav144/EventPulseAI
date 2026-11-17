"""End-to-end tests of the web backend (webapp/server.py) through FastAPI's TestClient.

Every test runs against a temporary copy made from a byte snapshot of
database/eventpulse.db, so SQLite never opens the real database (or its -wal/-shm). Requires fastapi + httpx (installed in .venv)."""
import shutil
from datetime import date
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REAL_DB = ROOT / "database" / "eventpulse.db"


@pytest.fixture(scope="module")
def client(tmp_path_factory, request):
    import checkin_app
    import webapp.server as S
    from tests import pg_support
    tmp = tmp_path_factory.mktemp("web")
    pg_name = None
    if pg_support.engine() == "postgresql":                 # fresh local test database, SYNTHETIC data only
        from tests.test_auth import synthetic_pg_database
        db, pg_name = synthetic_pg_database(request.getfixturevalue("trained_models"),
                                            request.getfixturevalue("small_synthetic"))
    else:
        if not REAL_DB.exists():
            pytest.skip("database/eventpulse.db not present")
        db = tmp / "eventpulse.db"
        from tests.test_auth import pre_auth_copy           # consistent copy without real accounts
        pre_auth_copy(db)
    old = (S.DB_PATH, S.DB_URL, checkin_app.DB_PATH, S.EXPORT_DIR)
    S.DB_PATH, checkin_app.DB_PATH, S.EXPORT_DIR = db, db, tmp / "exports"
    S.DB_URL = db if pg_name else None
    # Since authentication was added every data endpoint is administrator-only, so these tests
    # sign in as a freshly provisioned admin of the temporary database (assertions unchanged).
    import secrets
    from core.db.connection import Database
    from webapp import auth as A
    A.reset_rate_limits()
    pw = "Pw" + secrets.token_hex(8) + "7"
    with Database(db) as d:
        A.create_admin(d, "admin@eventpulse.test", "Test Admin", pw)
    with TestClient(S.app) as c:
        r = c.post("/api/auth/login", json={"email": "admin@eventpulse.test", "password": pw})
        assert r.status_code == 200
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        yield c
    S.DB_PATH, S.DB_URL, checkin_app.DB_PATH, S.EXPORT_DIR = old
    A.reset_rate_limits()
    if pg_name:
        pg_support.drop_database(pg_name)


def test_health_and_frontend(client):
    assert client.get("/api/health").json() == {"status": "ok"}          # public: nothing else revealed
    h = client.get("/api/health/details").json()                       # administrator only
    assert h["status"] == "ok" and h["schema_version"] >= 3 and h["active_model"]
    r = client.get("/")
    assert r.status_code in (200, 503)            # 503 only when web/dist has not been built
    assert client.get("/api/does-not-exist").status_code == 404


def test_overview_and_read_endpoints(client):
    ov = client.get("/api/overview").json()
    assert ov["kpis"]["students"] > 0 and ov["kpis"]["events"] > 0
    for path in ["/api/datasets", "/api/events", "/api/calendar", "/api/registrations", "/api/models",
                 "/api/analytics?dimension=Department", "/api/insights", "/api/monitoring", "/api/notifications",
                 "/api/reports/summary", "/api/settings", "/api/predictions"]:
        assert client.get(path).status_code == 200, path
    assert client.get("/api/analytics?dimension=Name").status_code == 422


def test_dataset_preview_never_exposes_raw_sheet(client):
    keys = [d["key"] for d in client.get("/api/datasets").json()]
    assert all("raw" not in k.lower() for k in keys)
    d = client.get("/api/datasets/processed?page=0&size=10").json()
    assert d["rows"] > 0 and len(d["preview"]["rows"]) <= 10


def test_event_lifecycle_prediction_and_attendance(client):
    from tests.test_auth import app_today
    today = app_today().isoformat()                # the web app works in Asia/Kolkata
    ev = client.post("/api/events", json={"name": "pytest workshop", "event_type": "Workshop", "event_date": today,
                                          "time_slot": "Morning", "duration_hours": 2, "capacity": 30}).json()
    eid = ev["event_id"]
    assert ev["data_source"] == "MANUAL" and ev["event_day"] == app_today().strftime("%A")
    # undated event needs a planned day
    assert client.post("/api/events", json={"event_type": "Seminar", "time_slot": "Evening", "duration_hours": 1}).status_code == 422

    students = [s["student_id"] for s in client.get("/api/students?limit=3").json()]
    for sid in students:
        assert client.post(f"/api/events/{eid}/registrations", json={"student_id": sid, "registration_days_before": 4}).status_code == 201
    assert client.post(f"/api/events/{eid}/registrations", json={"student_id": students[0]}).status_code == 409

    p = client.post(f"/api/events/{eid}/predict", json={}).json()
    assert p["summary"]["registrations"] == 3 and len(p["rows"]) == 3
    assert all(0 <= r["probability"] <= 1 for r in p["rows"])

    one = client.post("/api/predict", json={**client.get(f"/api/registration-features?student_id={students[0]}&event_id={eid}").json()}).json()
    assert 0 <= one["probability"] <= 1 and one["prediction_id"] and one["tips"]
    assert client.post("/api/predict", json={"Year": 9}).status_code == 422

    w = client.post("/api/whatif", json={"event_id": eid, "changes": {"Event_Time": "Evening"}}).json()
    assert w["original"]["registrations"] == 3 and "difference" in w

    assert client.post(f"/api/attendance/{eid}/checkin", json={"student_id": students[0]}).status_code == 200
    assert client.post(f"/api/attendance/{eid}/checkin", json={"student_id": students[0]}).status_code == 409
    fin = client.post(f"/api/attendance/{eid}/finalise", json={"walk_ins": 0}).json()
    assert fin["present"] == 1 and fin["marked_absent"] == 2

    rows = client.get(f"/api/attendance?event_id={eid}").json()["rows"]
    absent = next(r for r in rows if r["attended"] == 0)
    assert client.post("/api/attendance/correct", json={"registration_id": absent["registration_id"], "attended": 1,
                                                        "corrected_by": "pytest", "reason": "late arrival"}).json()["ok"]
    assert len(client.get(f"/api/attendance?event_id={eid}").json()["corrections"]) == 1

    pva = client.get("/api/analytics").json()["predicted_vs_actual"]
    assert any(r["event_id"] == eid and not r["target_is_simulated"] for r in pva)

    csv = client.get(f"/api/export/attendance_recorded?fmt=csv&event_id={eid}")
    assert csv.status_code == 200 and eid in csv.text
    assert client.get("/api/export/events?fmt=xlsx").content[:2] == b"PK"
    assert client.post("/api/reports/pdf").content[:4] == b"%PDF"


def test_edit_cancel_delete(client):
    ev = client.post("/api/events", json={"event_type": "Seminar", "event_day": "Friday", "time_slot": "Evening",
                                          "duration_hours": 1}).json()
    eid = ev["event_id"]
    assert client.patch(f"/api/events/{eid}", json={"venue": "Hall B"}).json()["venue"] == "Hall B"
    assert client.patch(f"/api/events/{eid}", json={"status": "Cancelled"}).json()["status"] == "Cancelled"
    assert client.delete(f"/api/events/{eid}").status_code == 200
    assert client.get(f"/api/events/{eid}").status_code == 404


def test_cannot_delete_event_with_history(client):
    hist = next(e for e in client.get("/api/events").json() if e["active_registrations"])
    r = client.delete(f"/api/events/{hist['event_id']}")
    assert r.status_code == 400 and "cancel it instead" in r.json()["detail"]


def test_notifications_dismiss_and_restore(client):
    assert client.post("/api/notifications/dismiss", json={"key": "pytest-key"}).json()["ok"]
    assert client.post("/api/notifications/dismiss", json={"key": None}).json()["ok"]


def test_qr_checkin_page(client):
    pytest.importorskip("segno")
    from tests.test_auth import app_today
    ev = client.post("/api/events", json={"event_type": "Workshop", "event_date": app_today().isoformat(),
                                          "time_slot": "Morning", "duration_hours": 1}).json()
    eid = ev["event_id"]
    url = client.post(f"/api/attendance/{eid}/qr").json()["url"]
    assert f"/checkin/{eid}?t=" in url
    png = client.get(f"/api/attendance/{eid}/qr.png")
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    assert client.get(f"/checkin/{eid}?t=wrong").status_code in (200, 403)
