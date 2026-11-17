"""EventPulse AI 1.2 - event workflow (calendar, registrations, notifications), QR/desk
check-in with corrections, what-if planner, insights and model monitoring."""
from datetime import date, timedelta

import pandas as pd
import pytest

from core import checkin as C
from core import intelligence as I
from core import workflow as W
from core.db import services
from core.db.errors import ConstraintViolationError, DuplicateRecordError, NotFoundError, PersonalDataError
from core.db.repositories import AttendanceRepository, EventRepository, RegistrationRepository
from tests.conftest import make_student

TODAY = date.today()


@pytest.fixture
def live_db(seeded_db):
    db = seeded_db
    ev = EventRepository(db)
    ev.create(event_id="EVQR", name="QR day", event_type="Workshop", event_date=TODAY.isoformat(),
              time_slot="Morning", duration_hours=2, capacity=3, popularity=70,
              registration_deadline=TODAY.isoformat())
    for s in ("STU900", "STU901", "STU902"):
        make_student(db, s)
        RegistrationRepository(db).register(s, "EVQR", 3, 0)
    ev.create(event_id="EVF", name="Future", event_type="Seminar",
              event_date=(TODAY + timedelta(days=3)).isoformat(), time_slot="Evening", duration_hours=1, capacity=10)
    return db


@pytest.mark.sqlite_only
def test_migration_0003_is_additive(db):
    assert db.scalar("SELECT MAX(version) FROM schema_migrations") == 3
    cols = {r["name"] for r in db.query("PRAGMA table_info(events)")}
    assert "registration_deadline" in cols
    assert {"checkin_tokens", "checkins"} <= {r["name"] for r in db.query("SELECT name FROM sqlite_master")}


def test_deadline_must_not_follow_event(live_db):
    with pytest.raises(ConstraintViolationError):
        EventRepository(live_db).update("EVF", registration_deadline=(TODAY + timedelta(days=9)).isoformat())


def test_calendar_phases(live_db):
    cal = W.calendar_events(live_db)
    assert set(cal["event_id"]) == {"EVQR", "EVF"}                 # undated dataset events are not on a calendar
    assert cal.set_index("event_id").loc["EVF", "phase"] == "Upcoming"
    assert W.event_phase({"status": "Scheduled", "event_date": None, "time_slot": "Morning"}) == "Undated"
    past = {"status": "Scheduled", "event_date": "2020-01-01", "time_slot": "Morning", "duration_hours": 1}
    assert W.event_phase(past) == "Completed"
    assert W.calendar_events(live_db, start=TODAY + timedelta(days=1))["event_id"].tolist() == ["EVF"]


def test_registration_management_and_import(live_db):
    db = live_db
    assert len(W.registration_table(db, event_id="EVQR")) == 3
    assert W.registration_table(db, search="STU001")["student_id"].eq("STU001").all()
    dep = W.department_participation(db)
    assert dep["registrations"].sum() == db.scalar("SELECT COUNT(*) FROM registrations")
    bad = pd.DataFrame({"Student_ID": ["STU900", "STU999"], "Event_ID": ["EVF", "EVF"],
                        "Registration_Days_Before": [2, 2]})
    res = W.import_registrations(db, bad)
    assert res.errors and res.added == 0 and not RegistrationRepository(db).find("STU900", "EVF")   # all or nothing
    ok = pd.DataFrame({"Student_ID": ["STU900", "STU900", "STU901"], "Event_ID": ["EVF", "EVF", "EVF"],
                       "Registration_Days_Before": [2, 2, 1]})
    res = W.import_registrations(db, ok)
    assert res.added == 2 and res.duplicates == ["STU900 → EVF"]
    assert W.import_registrations(db, ok).added == 0                        # re-import: all duplicates
    with pytest.raises(PersonalDataError):
        W.import_registrations(db, ok.assign(Email="x@y.z"))


def test_notifications_from_stored_data(live_db):
    cats = {(n.category, n.event_id) for n in W.notifications(live_db)}
    assert ("Capacity", "EVQR") in cats and ("Registration deadline", "EVQR") in cats
    assert ("Upcoming event", "EVF") in cats
    assert not any(e == "EV06" for _, e in cats)                           # dataset events raise no alerts


def test_qr_checkin_finalise_and_correct(live_db):
    db = live_db
    services.predict_event(db, "EVQR")                                     # prediction made BEFORE the event
    tok = C.get_or_create_token(db, "EVQR")
    assert C.get_or_create_token(db, "EVQR") == tok and len(tok) >= 16
    assert C.checkin_url("EVQR", tok, host="10.0.0.5").startswith("http://10.0.0.5:8010/checkin/EVQR?t=")
    with pytest.raises(ConstraintViolationError):
        C.check_in(db, "EVQR", "STU900", "wrong-token")
    C.check_in(db, "EVQR", "STU900", tok)
    with pytest.raises(DuplicateRecordError):
        C.check_in(db, "EVQR", "STU900", tok)
    with pytest.raises(NotFoundError):
        C.check_in(db, "EVQR", "STU001", tok)
    with pytest.raises(ConstraintViolationError):
        C.check_in(db, "EVQR", "STU9<script>", tok)
    C.check_in(db, "EVQR", "STU901", method="DESK")
    rec = AttendanceRepository(db).for_event("EVQR")
    assert {r["student_id"]: (r["attended"], r["source"]) for r in rec} == {"STU900": (1, "MANUAL"),
                                                                             "STU901": (1, "MANUAL")}
    res = C.finalise_attendance(db, "EVQR", walk_ins=1)
    assert (res["present"], res["marked_absent"]) == (2, 1)
    assert res["evaluations"] and res["evaluations"][0]["target_is_simulated"] == 0
    assert EventRepository(db).get("EVQR")["status"] == "Completed"
    rid = RegistrationRepository(db).find("STU902", "EVQR")["registration_id"]
    with pytest.raises(ConstraintViolationError):
        C.correct_attendance(db, rid, 1, "", "")
    C.correct_attendance(db, rid, 1, "Organiser", "arrived late")
    assert AttendanceRepository(db).outcome("EVQR")["actual_attendees"] == 3
    log = C.correction_log(db, "EVQR")
    assert log.iloc[0][["old", "new", "by", "reason"]].tolist() == [0, 1, "Organiser", "arrived late"]
    t = C.attendance_table(db, "EVQR")
    assert sorted(t["outcome"]) == ["Attended"] * 3


def test_checkin_refused_for_simulated_or_wrong_day(live_db):
    db = live_db
    with pytest.raises(ConstraintViolationError, match="SIMULATED"):
        C.get_or_create_token(db, "EV06")
    with pytest.raises(ConstraintViolationError, match="SIMULATED"):
        C.check_in(db, "EV06", "STU001", method="DESK")
    RegistrationRepository(db).register("STU900", "EVF", 1, 0)
    with pytest.raises(ConstraintViolationError, match="event day"):
        C.check_in(db, "EVF", "STU900", method="DESK")
    assert "EV06" not in {e["event_id"] for e in C.real_events(db)}


def test_simulated_outcomes_cannot_be_overwritten(live_db):
    att = {r["student_id"]: 1 - r["attended"] for r in AttendanceRepository(live_db).for_event("EV06")}
    with pytest.raises(ConstraintViolationError, match="SIMULATED"):
        services.record_actual_attendance(live_db, "EV06", attended=att)
    with pytest.raises(ConstraintViolationError, match="SIMULATED"):
        services.record_actual_attendance(live_db, "EV06", headcount=1)


def test_what_if_planner(live_db):
    w = I.what_if(live_db, "EV22", {"Has_Exam_Near_Event": 1, "Weather_Forecast": "Rainy"}, registrations=100)
    assert w.original.registrations == 24 and w.modified.registrations == 100
    assert w.modified.mean_probability < w.original.mean_probability      # exam + rain lower attendance
    assert w.original.low <= w.original.expected_attendees <= w.original.high
    assert any("not saved" in n for n in w.notes) and any("resemble" in n for n in w.notes)
    same = I.what_if(live_db, "EV22", {})
    assert same.difference == pytest.approx(0)
    before = live_db.scalar("SELECT COUNT(*) FROM predictions")
    I.what_if(live_db, "EV22", {"Event_Time": "Evening"})
    assert live_db.scalar("SELECT COUNT(*) FROM predictions") == before    # never stored
    with pytest.raises(ValueError):
        I.what_if(live_db, "EV22", {"Course": "BBA"})                       # not an event-level input
    with pytest.raises(ValueError):
        I.what_if(live_db, "EV22", {"Event_Popularity": 150})
    with pytest.raises(ValueError):
        I.what_if(live_db, "EVF", {})                                      # no registrations


def test_insights_are_data_backed(live_db):
    res = I.insights(live_db)
    assert res["counts"]["RECORDED"] == 0
    assert any("No attendance has been RECORDED" in x for x in res["limitations"])
    assert res["insights"] and all(i.source == "SIMULATED" and i.n >= 8 for i in res["insights"])
    exam = [i for i in res["insights"] if "exam near" in i.title]
    assert exam and "lower" in exam[0].title


def test_insights_empty_database(db):
    res = I.insights(db)
    assert res["insights"] == [] and res["limitations"]


def test_monitoring_drift(live_db):
    m = I.monitor(live_db)
    assert m.n_incoming == 0 and m.report is None and "needed" in m.reasons[0]
    for e in ("EV22", "EV06", "EV09", "EV10"):
        services.predict_event(live_db, e)
    m = I.monitor(live_db)
    assert m.n_incoming >= 30 and m.report is not None and len(m.report) == 20
    assert m.schema_violations == 0 and "Drift alone" not in m.recommendation or m.drifted
    shifted = I.incoming_inputs(live_db)
    shifted["Distance_From_Venue_KM"] = shifted["Distance_From_Venue_KM"].fillna(3) + 15
    from core.preprocessing import prepare_frame
    rep = I.drift_report(I.training_frame_for({"training_data": {"file": "ml_dataset_processed.csv"}}),
                         prepare_frame(shifted))
    assert rep.set_index("feature").loc["Distance_From_Venue_KM", "drift"]
