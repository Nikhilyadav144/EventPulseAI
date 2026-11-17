"""CRUD operations, constraints and personal-data protection."""
import json

import pytest

from core.db.errors import (ConstraintViolationError, DuplicateRecordError, HistoryProtectedError, NotFoundError,
                            PersonalDataError)
from core.db.repositories import (DatasetRepository, EventRepository, ForecastRepository, ModelVersionRepository,
                                  RegistrationRepository, SettingsRepository, StudentRepository)
from tests.conftest import make_event, make_student


# ---------------------------------------------------------------- students
def test_student_crud(db):
    make_student(db, "STU001")
    s = StudentRepository(db).get("STU001")
    assert s["course"] == "B.Tech CSE" and s["department"] == "Engineering"
    assert s["previous_attendance_rate"] == 80.0          # derived 4/5
    StudentRepository(db).update("STU001", year=3, course="BBA")
    s = StudentRepository(db).get("STU001")
    assert s["year"] == 3 and s["department"] == "Management"
    assert len(StudentRepository(db).list(search="STU0")) == 1
    StudentRepository(db).delete("STU001")
    with pytest.raises(NotFoundError):
        StudentRepository(db).get("STU001")


@pytest.mark.parametrize("bad_id", ["a.student@ncuindia.edu", "26CSU210", "24bap043", "STU9910801413"])
def test_personal_identifiers_rejected(db, bad_id):
    with pytest.raises(PersonalDataError):
        make_student(db, bad_id)


def test_personal_fields_rejected(db):
    with pytest.raises(PersonalDataError):
        make_student(db, "STU001", Name="Someone")


def test_student_value_validation(db):
    with pytest.raises(ConstraintViolationError, match="area of interest"):
        make_student(db, "STU001", area_of_interest="Gaming")
    with pytest.raises(ConstraintViolationError):
        make_student(db, "STU002", previous_event_attendance=9, previous_events_registered=3)
    with pytest.raises(DuplicateRecordError, match="already exists"):
        make_student(db, "STU003")
        make_student(db, "STU003")


def test_student_with_registrations_cannot_be_deleted(db):
    make_student(db, "STU001")
    make_event(db, "EV01")
    RegistrationRepository(db).register("STU001", "EV01", 5)
    with pytest.raises(HistoryProtectedError):
        StudentRepository(db).delete("STU001")


# ---------------------------------------------------------------- events
def test_event_date_sets_weekday_and_validates(db):
    make_event(db, "EV01", event_date="2026-10-16")
    assert EventRepository(db).get("EV01")["event_day"] == "Friday"
    with pytest.raises(ConstraintViolationError, match="Wednesday"):
        make_event(db, "EV02", event_date="2026-10-14", event_day="Monday")
    EventRepository(db).update("EV01", event_date="2026-10-17")
    assert EventRepository(db).get("EV01")["event_day"] == "Saturday"
    with pytest.raises(ConstraintViolationError):
        EventRepository(db).update("EV01", event_day="Monday")
    make_event(db, "EV03", event_date=None, event_day="Tuesday")      # undated historical event
    assert EventRepository(db).get("EV03")["event_day"] == "Tuesday"


def test_event_invalid_values(db):
    for bad in (dict(event_type="Party"), dict(duration_hours=0), dict(time_slot="Night"), dict(event_date="14/10/2026")):
        with pytest.raises(ConstraintViolationError):
            make_event(db, "EVBAD", **bad)


def test_event_delete_protects_history(seeded_db):
    events = EventRepository(seeded_db)
    with pytest.raises(HistoryProtectedError, match="attendance"):
        events.delete("EV01")
    counts = events.delete("EV01", include_history=True)
    assert counts["registrations"] == 3
    assert not events.exists("EV01")
    assert seeded_db.scalar("SELECT COUNT(*) FROM registrations WHERE event_id = 'EV01'") == 0
    assert seeded_db.integrity_check() == []
    log = seeded_db.query("SELECT * FROM audit_log WHERE action = 'delete' AND entity = 'event'")
    assert json.loads(log[-1]["details"])["attendance_records"] == 3


def test_event_without_history_deletes(db):
    make_event(db, "EV01")
    make_student(db, "STU001")
    RegistrationRepository(db).register("STU001", "EV01", 3)
    EventRepository(db).delete("EV01")
    assert db.scalar("SELECT COUNT(*) FROM registrations") == 0


# ---------------------------------------------------------------- forecasts
def test_forecast_history_and_unavailable(db):
    make_event(db, "EV01")
    make_student(db, "STU001")
    RegistrationRepository(db).register("STU001", "EV01", 3)
    feats = lambda: db.query_one('SELECT "Weather_Forecast", forecast_obtained_at FROM v_registration_features')  # noqa
    assert feats()["Weather_Forecast"] == "Unavailable"            # no forecast stored yet
    f = ForecastRepository(db)
    with pytest.raises(ConstraintViolationError, match="obtained"):
        f.add("EV01", "Rainy")
    f.add("EV01", "Unavailable")                                   # allowed without a timestamp
    f.add("EV01", "Rainy", "2026-10-10T08:00:00Z")
    f.add("EV01", "Clear", "2026-10-12T08:00:00Z")
    assert [h["forecast"] for h in f.history("EV01")] == ["Clear", "Rainy", "Unavailable"]
    assert feats() == {"Weather_Forecast": "Clear", "forecast_obtained_at": "2026-10-12T08:00:00Z"}


# ---------------------------------------------------------------- registrations
def test_duplicate_registration_prevented(db):
    make_student(db, "STU001")
    make_event(db, "EV01")
    regs = RegistrationRepository(db)
    regs.register("STU001", "EV01", 4)
    with pytest.raises(DuplicateRecordError, match="already registered"):
        regs.register("STU001", "EV01", 2)
    assert regs.count() == 1


def test_registration_dates(db):
    make_student(db, "STU001")
    make_event(db, "EV01", event_date="2026-10-14")
    regs = RegistrationRepository(db)
    rid = regs.register("STU001", "EV01", registered_on="2026-10-04")
    assert regs.get(rid)["registration_days_before"] == 10
    make_event(db, "EV02", event_date="2026-10-14")
    with pytest.raises(ConstraintViolationError, match="after the event"):
        regs.register("STU001", "EV02", registered_on="2026-10-20")
    with pytest.raises(ConstraintViolationError, match="implies"):
        regs.register("STU001", "EV02", 3, registered_on="2026-10-04")


def test_cancelled_event_and_registration(db):
    make_student(db, "STU001")
    make_event(db, "EV01", status="Cancelled")
    with pytest.raises(ConstraintViolationError, match="cancelled"):
        RegistrationRepository(db).register("STU001", "EV01", 3)
    make_event(db, "EV02")
    rid = RegistrationRepository(db).register("STU001", "EV02", 3)
    RegistrationRepository(db).set_status(rid, "Cancelled")
    assert db.scalar("""SELECT COUNT(*) FROM v_registration_features WHERE "Registration_Status" = 'Registered'""") == 0


def test_unknown_references(db):
    make_event(db, "EV01")
    with pytest.raises(NotFoundError):
        RegistrationRepository(db).register("STU404", "EV01", 3)


# ---------------------------------------------------------------- models
def test_model_versions_and_metrics(db, trained_models):
    from core.db.services import sync_models
    d, run = trained_models
    ids = sync_models(db, d)
    assert len(ids) == 5
    repo = ModelVersionRepository(db)
    active = repo.active()
    assert active["model_key"] == run.recommended and active["version"] == 1
    m = repo.metrics(active["model_version_id"])
    assert {"cv", "holdout", "majority_baseline"} <= set(m)
    assert m["holdout"]["tn"]["value"] + m["holdout"]["tp"]["value"] > 0
    assert sync_models(db, d) == ids                               # idempotent: same artifacts, same versions
    other = next(i for k, i in ids.items() if k != run.recommended)
    with pytest.raises(DuplicateRecordError):                      # DB allows a single active model
        db.execute("UPDATE model_versions SET is_active = 1 WHERE model_version_id = ?", (other,))
    repo.set_active(other)
    assert repo.active()["model_version_id"] == other


def test_new_artifact_gets_new_version(db, trained_models, tmp_path):
    import shutil
    from core.db.services import sync_models
    d, run = trained_models
    sync_models(db, d)
    copy = tmp_path / run.recommended
    shutil.copytree(d / run.recommended, copy)
    with open(copy / "model.joblib", "ab") as fh:                  # different bytes = different artifact
        fh.write(b"\0")
    mv = ModelVersionRepository(db).register_from_dir(copy)
    assert ModelVersionRepository(db).get(mv)["version"] == 2


# ---------------------------------------------------------------- settings / datasets
def test_settings_roundtrip(db):
    s = SettingsRepository(db)
    s.set("theme", {"mode": "dark", "accent": "#4F8CFF"})
    s.set("theme", {"mode": "dark", "accent": "#22C55E"})
    assert s.get("theme")["accent"] == "#22C55E" and s.get("missing", 5) == 5


def test_raw_merged_sheet_never_registered(db):
    with pytest.raises(PersonalDataError):
        DatasetRepository(db).register("raw", "UPLOAD", source_sheet="Raw_Merged_Data")
