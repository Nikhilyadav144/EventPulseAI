"""Local QR check-in service (checkin_app.py) end to end, plus the QR image itself."""
from datetime import date

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")
from fastapi.testclient import TestClient  # noqa: E402

import checkin_app  # noqa: E402
from core import checkin as C  # noqa: E402
from core.db.connection import Database  # noqa: E402
from core.db.repositories import AttendanceRepository, EventRepository, RegistrationRepository  # noqa: E402
from tests.conftest import make_student  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "live.db"
    with Database(path) as db:
        EventRepository(db).create(event_id="EVLIVE", name="Live workshop", event_type="Workshop",
                                   event_date=date.today().isoformat(), time_slot="Morning", duration_hours=2,
                                   venue="Hall A")
        make_student(db, "STU900")
        RegistrationRepository(db).register("STU900", "EVLIVE", 2, 0)
        token = C.get_or_create_token(db, "EVLIVE")
    monkeypatch.setattr(checkin_app, "DB_PATH", path)
    return TestClient(checkin_app.app), token, path


def test_scan_and_check_in(client):
    c, token, path = client
    page = c.get(f"/checkin/EVLIVE?t={token}")
    assert page.status_code == 200 and "Live workshop" in page.text and "Student ID" in page.text
    ok = c.post("/checkin/EVLIVE", data={"student_id": "STU900", "t": token})
    assert ok.status_code == 200 and "Checked in" in ok.text
    again = c.post("/checkin/EVLIVE", data={"student_id": "STU900", "t": token})
    assert again.status_code == 400 and "already checked in" in again.text
    with Database(path) as db:
        rec = AttendanceRepository(db).for_event("EVLIVE")
        assert [(r["student_id"], r["attended"], r["recorded_by"]) for r in rec] == [("STU900", 1, "QR check-in")]


def test_bad_token_unknown_student_and_event(client):
    c, token, _ = client
    assert c.post("/checkin/EVLIVE", data={"student_id": "STU900", "t": "nope"}).status_code == 400
    r = c.post("/checkin/EVLIVE", data={"student_id": "STU777", "t": token})
    assert r.status_code == 400 and "not registered" in r.text
    assert c.get("/checkin/NOPE?t=x").status_code == 404


def test_qr_png():
    pytest.importorskip("segno")
    png = C.qr_png(C.checkin_url("EVLIVE", "x" * 24, host="192.168.1.20"))
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 300
