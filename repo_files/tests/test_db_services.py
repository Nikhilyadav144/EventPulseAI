"""Import, prediction storage, actual attendance and predicted-vs-actual evaluation."""
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from config import settings
from core.data_loader import DataImportError
from core.db import services
from core.db.errors import ConstraintViolationError, NotFoundError, PersonalDataError
from core.db.repositories import (AttendanceRepository, EvaluationRepository, EventRepository, ForecastRepository,
                                  PredictionRepository, RegistrationRepository)
from tests.conftest import make_event, make_student


# ---------------------------------------------------------------- import
def test_import_processed_dataset(seeded_db):
    db = seeded_db
    assert db.scalar("SELECT COUNT(*) FROM students") == 279
    assert db.scalar("SELECT COUNT(*) FROM events") == 36
    assert db.scalar("SELECT COUNT(*) FROM registrations") == 383
    assert db.scalar("SELECT COUNT(*) FROM attendance_records WHERE source = 'ORIGINAL_SIMULATED'") == 383
    assert db.scalar("SELECT SUM(attended) FROM attendance_records") == 265
    f = db.query("SELECT DISTINCT source, forecast_obtained_at FROM weather_forecasts")
    assert f == [{"source": "ORIGINAL_DATASET", "forecast_obtained_at": None}]
    assert db.scalar("SELECT COUNT(*) FROM events WHERE status = 'Completed'") == 36
    assert db.integrity_check() == []


def test_features_view_matches_processed_file(seeded_db):
    view = pd.DataFrame(seeded_db.query("SELECT * FROM v_registration_features"))
    src = pd.read_csv(settings.PROCESSED_DATASET)
    merged = view.merge(src, on=["Student_ID", "Event_ID"], suffixes=("_db", "_csv"))
    assert len(merged) == 383
    for c in ("Year", "Registration_Days_Before", "Event_Popularity", "Distance_From_Venue_KM",
              "Previous_Attendance_Rate", "Academic_Engagement_Score"):
        a, b = merged[f"{c}_db"].astype(float), merged[f"{c}_csv"].astype(float)
        assert ((a == b) | (a.isna() & b.isna())).all(), c
    assert (merged["Weather_Forecast_db"] == merged["Weather_Forecast_csv"]).all()
    assert (merged["Course_db"] == merged["Course_csv"]).all()


def test_reimport_is_detected(seeded_db):
    from core.data_loader import file_sha256
    p = settings.PROCESSED_DATASET
    s = services.import_dataset(seeded_db, pd.read_csv(p), "PROCESSED", "again", p.name, file_sha256(p))
    assert s.already_imported and seeded_db.scalar("SELECT COUNT(*) FROM registrations") == 383


def test_conflicting_import_rolls_back_completely(db, raw_ml):
    df = raw_ml.copy()
    sid = df.Student_ID.value_counts().index[0]               # a student with several registrations
    rows = df.index[df.Student_ID == sid]
    df.loc[rows[0], "Year"], df.loc[rows[1], "Year"] = 1, 2   # same student, two different years
    with pytest.raises(ConstraintViolationError, match="conflicting"):
        services.import_dataset(db, df, "UPLOAD", "bad")
    for t in ("students", "events", "registrations", "datasets"):
        assert db.scalar(f"SELECT COUNT(*) FROM {t}") == 0, t


def test_import_rejects_personal_data(db, tmp_path, original_workbook):
    p = tmp_path / "pii.csv"
    pd.DataFrame({"Student_ID": ["STU1"], "Mobile_No": ["9999999999"]}).to_csv(p, index=False)
    with pytest.raises(DataImportError, match="personal-data"):
        services.import_file(db, p)
    with pytest.raises(PersonalDataError):
        services.import_dataset(db, pd.DataFrame({"Student_ID": ["S"], "Email_Address": ["x"]}), "UPLOAD", "x")
    s = services.import_file(db, original_workbook, kind="ORIGINAL")       # workbook -> ML_Dataset only
    assert seeded_sheet(db, s.dataset_id) == "ML_Dataset"


def seeded_sheet(db, dataset_id):
    return db.scalar("SELECT source_sheet FROM datasets WHERE dataset_id = ?", (dataset_id,))


def test_import_synthetic_kept_labelled(db, small_synthetic):
    s = services.import_dataset(db, small_synthetic, "SYNTHETIC", "synthetic")
    assert s.students == small_synthetic.Student_ID.nunique()
    assert db.query("SELECT DISTINCT data_source FROM students") == [{"data_source": "SYNTHETIC"}]
    assert db.query("SELECT DISTINCT source FROM attendance_records") == [{"source": "SYNTHETIC"}]
    assert db.scalar("SELECT COUNT(*) FROM weather_forecasts WHERE forecast = 'Unavailable'") > 0


# ---------------------------------------------------------------- predictions
def test_predict_event_stores_full_record(seeded_db):
    s = services.predict_event(seeded_db, "EV22")
    rows = PredictionRepository(seeded_db).history(event_id="EV22")
    assert len(rows) == s["registrations"] == 24
    r = rows[0]
    assert 0 <= r["probability"] <= 1 and r["predicted_attend"] == int(r["probability"] >= r["threshold"])
    assert r["model_version_id"] == s["model_version_id"] and r["predicted_utc"].endswith("Z")
    assert set(json.loads(r["input_snapshot"])) >= {"Event_Type", "Weather_Forecast", "Year"}
    assert r["forecast_id"] is not None and r["run_type"] == "event"
    assert abs(sum(x["probability"] for x in rows) - s["expected_attendance"]) < 0.05


def test_prediction_history_is_complete(seeded_db):
    services.predict_event(seeded_db, "EV22")
    services.predict_event(seeded_db, "EV22")
    assert len(PredictionRepository(seeded_db).history(event_id="EV22")) == 48       # nothing overwritten
    assert len(seeded_db.query("SELECT * FROM v_latest_predictions JOIN registrations USING(registration_id) "
                               "WHERE event_id = 'EV22'")) == 24
    assert len(PredictionRepository(seeded_db).runs()) == 2


def test_predict_record_and_file(seeded_db, small_synthetic, tmp_path):
    rec = small_synthetic.drop(columns=["Will_Attend", "Data_Source"]).iloc[0].to_dict()
    rec["Weather_Forecast"] = None
    r = services.predict_record(seeded_db, rec)
    p = PredictionRepository(seeded_db).get(r["prediction_id"])
    assert p["registration_id"] is None and p["student_ref"] == rec["Student_ID"]
    assert p["forecast_used"] == "Unavailable"
    f = tmp_path / "bulk.xlsx"
    small_synthetic.head(25).drop(columns=["Will_Attend"]).to_excel(f, index=False)
    out, summary = services.predict_file(seeded_db, f)
    assert len(out) == 25 and summary["registrations"] == 25
    assert seeded_db.scalar("SELECT n_predictions FROM prediction_runs WHERE run_id = ?", (summary["run_id"],)) == 25
    with pytest.raises(PersonalDataError):
        services.predict_record(seeded_db, {**rec, "Student_ID": "someone@ncuindia.edu"})


def test_no_active_model(db):
    make_event(db, "EV01")
    make_student(db, "STU001")
    RegistrationRepository(db).register("STU001", "EV01", 3)
    with pytest.raises(NotFoundError, match="active model"):
        services.predict_event(db, "EV01")


# ---------------------------------------------------------------- actual attendance + evaluation
@pytest.fixture
def live_event(seeded_db):
    """A dated event with 6 manual registrations, a forecast and stored predictions. It is dated today
    (UTC): predictions made after the event date are deliberately ignored, and outcomes cannot be
    recorded for future events, so 'today' is the only date where both are possible in a test."""
    db = seeded_db
    today = datetime.now(timezone.utc).date().isoformat()
    make_event(db, "EVLIVE", event_date=today)
    for i in range(6):
        make_student(db, f"STU9{i:02d}", previous_event_attendance=i, previous_events_registered=6,
                     currently_uba_volunteer=i % 2)
        RegistrationRepository(db).register(f"STU9{i:02d}", "EVLIVE", 2 + i, has_exam_near_event=int(i == 5))
    ForecastRepository(db).add("EVLIVE", "Cloudy", "2026-10-12T06:30:00Z")
    pred = services.predict_event(db, "EVLIVE")
    return db, pred


def test_per_student_actuals_and_error(live_event):
    db, pred = live_event
    actual = {"STU900": 1, "STU901": 0, "STU902": 1, "STU903": 1, "STU904": 0, "STU905": 0}
    res = services.record_actual_attendance(db, "EVLIVE", attended=actual, walk_ins=2, recorded_by="admin")
    assert len(res) == 1
    e = res[0]
    probs = {r["student_ref"]: r["probability"] for r in PredictionRepository(db).history(event_id="EVLIVE")}
    expected = sum(probs.values())
    assert e["actual_attendees"] == 3
    assert e["expected_attendance"] == pytest.approx(expected, abs=1e-3)
    assert e["error"] == pytest.approx(expected - 3, abs=1e-3)
    assert e["percentage_error"] == pytest.approx(abs(expected - 3) / 3 * 100, abs=0.01)
    y = np.array([actual[s] for s in sorted(actual)])
    p = np.array([probs[s] for s in sorted(actual)])
    assert e["brier"] == pytest.approx(float(np.mean((p - y) ** 2)), abs=1e-6)
    assert e["tn"] + e["fp"] + e["fn"] + e["tp"] == 6 and e["target_is_simulated"] == 0
    assert EventRepository(db).get("EVLIVE")["status"] == "Completed"
    assert AttendanceRepository(db).outcome("EVLIVE")["walk_ins"] == 2
    assert len(EvaluationRepository(db).items(e["evaluation_id"])) == 6
    hist = services.model_accuracy_history(db)
    assert list(hist["event_id"]) == ["EVLIVE"]                    # real (non-simulated) evaluations only


def test_headcount_actuals(live_event):
    db, pred = live_event
    res = services.record_actual_attendance(db, "EVLIVE", headcount=4)
    e = res[0]
    assert e["method"] == "headcount" and e["actual_attendees"] == 4 and e["accuracy"] is None
    assert e["error"] == pytest.approx(pred["expected_attendance"] - 4, abs=0.06)
    assert e["count_error"] == pred["predicted_attendees"] - 4
    with pytest.raises(ConstraintViolationError, match="exceeds"):
        services.record_actual_attendance(db, "EVLIVE", headcount=99)
    with pytest.raises(ConstraintViolationError, match="either"):
        services.record_actual_attendance(db, "EVLIVE")


def test_corrections_keep_history(live_event):
    db, _ = live_event
    services.record_actual_attendance(db, "EVLIVE", attended={"STU900": 1, "STU901": 1})
    services.record_actual_attendance(db, "EVLIVE", attended={"STU901": 0}, recorded_by="admin")
    evs = EvaluationRepository(db).for_event("EVLIVE")
    assert len(evs) == 2                                           # both comparisons kept
    assert evs[0]["actual_attendees"] == 1 and evs[1]["actual_attendees"] == 2
    log = db.query("SELECT details FROM audit_log WHERE action = 'correct' AND entity = 'attendance'")
    assert json.loads(log[-1]["details"])["old"] == 1


def test_predictions_after_outcome_are_excluded(live_event):
    db, _ = live_event
    services.record_actual_attendance(db, "EVLIVE", headcount=3)
    # Pretend the outcome was recorded before any prediction existed: hindsight predictions must be ignored.
    db.execute("UPDATE event_outcomes SET recorded_utc = '2026-01-01T00:00:00Z' WHERE event_id = 'EVLIVE'")
    assert services.evaluate_event(db, "EVLIVE") == []


def test_attendance_rules(seeded_db):
    db = seeded_db
    make_event(db, "EVFUT", event_date="2099-01-05")
    make_student(db, "STU950")
    RegistrationRepository(db).register("STU950", "EVFUT", 5)
    with pytest.raises(ConstraintViolationError, match="not taken place"):
        services.record_actual_attendance(db, "EVFUT", headcount=1)
    make_event(db, "EVCAN", event_date="2026-09-01")
    RegistrationRepository(db).register("STU950", "EVCAN", 5)
    EventRepository(db).set_status("EVCAN", "Cancelled")
    with pytest.raises(ConstraintViolationError, match="cancelled"):
        services.record_actual_attendance(db, "EVCAN", headcount=0)
    with pytest.raises(NotFoundError, match="Not registered"):
        services.record_actual_attendance(db, "EV22", attended={"STU950": 1})


def test_simulated_outcomes_flagged(seeded_db):
    services.predict_event(seeded_db, "EV06")
    att = {r["student_id"]: r["attended"] for r in AttendanceRepository(seeded_db).for_event("EV06")}
    res = services.record_actual_attendance(seeded_db, "EV06", attended=att)
    assert res[0]["target_is_simulated"] == 1 and "SIMULATED" in res[0]["notes"]
    assert services.model_accuracy_history(seeded_db).empty
    assert len(services.model_accuracy_history(seeded_db, include_simulated=True)) == 1
