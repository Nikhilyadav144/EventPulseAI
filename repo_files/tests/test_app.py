"""End-term API tests (CSL236): health, UI, domain logic, validation (HTTP 422), logging.

Run:  pytest -v --tb=short tests/test_app.py
Needs model/model.joblib (python train.py ... creates it).
"""
import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import app as api  # noqa: E402

VALID = {
    "Course": "B.Tech CSE", "Year": 2, "Currently_UBA_Volunteer": 1, "Participated_UBA_Event_Before": 1,
    "Experience_Level": "Some experience", "Area_Of_Interest": "Problem Solving & Decision Making",
    "Role": "Content", "Event_Type": "Workshop", "Event_Day": "Wednesday", "Event_Time": "Morning",
    "Event_Duration_Hours": 2.0, "Registration_Days_Before": 5, "Previous_Event_Attendance": 4,
    "Previous_Events_Registered": 5, "Previous_Attendance_Rate": 80.0, "Academic_Engagement_Score": 72.0,
    "Event_Popularity": 70.0, "Distance_From_Venue_KM": 3.5, "Has_Exam_Near_Event": 0,
    "Weather_Forecast": "Clear",
}
# A committed volunteer at a popular, matching, nearby event with no exam and clear weather ...
STRONG = dict(VALID, Currently_UBA_Volunteer=1, Participated_UBA_Event_Before=1, Experience_Level="Experienced",
              Previous_Event_Attendance=9, Previous_Events_Registered=10, Previous_Attendance_Rate=90.0,
              Event_Popularity=90.0, Distance_From_Venue_KM=1.0, Has_Exam_Near_Event=0, Weather_Forecast="Clear",
              Registration_Days_Before=5)
# ... versus a student with a poor record, an exam that week, a long commute and rain.
WEAK = dict(VALID, Currently_UBA_Volunteer=0, Participated_UBA_Event_Before=0, Experience_Level="No experience",
            Area_Of_Interest="Not Sure Yet", Previous_Event_Attendance=1, Previous_Events_Registered=6,
            Previous_Attendance_Rate=16.7, Event_Popularity=30.0, Distance_From_Venue_KM=18.0,
            Has_Exam_Near_Event=1, Weather_Forecast="Rainy", Registration_Days_Before=0, Event_Time="Evening")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    logs = tmp_path_factory.mktemp("logs")
    api.LOG_PATH = logs / "predictions.jsonl"
    api.REJECTED_LOG = logs / "rejected.jsonl"
    with TestClient(api.app) as c:          # runs the lifespan: model loaded once
        yield c


def prob(client, payload) -> float:
    r = client.post("/predict", json=payload)
    assert r.status_code == 200, r.text
    return r.json()["probability"]


# 1. health -----------------------------------------------------------------
def test_health_reports_model_loaded(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["model_loaded"] is True and body["model"]


# 2. UI ---------------------------------------------------------------------
def test_ui_loads(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "EventPulse AI" in r.text and "/predict" in r.text


def test_docs_page_available(client):
    assert client.get("/docs").status_code == 200
    assert "/predict" in client.get("/openapi.json").json()["paths"]


# 3. domain logic -------------------------------------------------------------
def test_strong_registration_beats_weak_one(client):
    strong, weak = prob(client, STRONG), prob(client, WEAK)
    assert strong > weak + 0.3, (strong, weak)


@pytest.mark.parametrize("field,better,worse", [
    ("Has_Exam_Near_Event", 0, 1),
    ("Weather_Forecast", "Clear", "Rainy"),
    ("Distance_From_Venue_KM", 1.0, 25.0),
])
def test_single_factor_moves_probability_the_right_way(client, field, better, worse):
    assert prob(client, dict(VALID, **{field: better})) > prob(client, dict(VALID, **{field: worse}))


def test_valid_prediction_shape_and_tips(client):
    r = client.post("/predict", json=WEAK)
    body = r.json()
    assert 0.0 <= body["probability"] <= 1.0
    assert body["will_attend"] == (body["probability"] >= body["threshold"])
    assert 2 <= len(body["tips"]) <= 3
    assert any("exam" in t.lower() for t in body["tips"])
    assert "SIMULATED" in body["notice"]


def test_unknown_optional_values_are_imputed_not_rejected(client):
    payload = dict(VALID, Academic_Engagement_Score=None, Event_Popularity=None,
                   Distance_From_Venue_KM=None, Previous_Attendance_Rate=None)
    payload.pop("Weather_Forecast")                        # defaults to "Unavailable"
    assert 0.0 <= prob(client, payload) <= 1.0


# 4. boundary validation: always 422, never 500 -------------------------------
@pytest.mark.parametrize("change", [
    {"Year": 6},                                  # above the contract maximum
    {"Year": 0},                                  # below the minimum
    {"Event_Duration_Hours": 0.25},               # below 0.5 h
    {"Distance_From_Venue_KM": -1},               # negative distance
    {"Academic_Engagement_Score": 100.5},         # above 100
    {"Registration_Days_Before": 2.5},            # not an integer
    {"Year": "two"},                              # wrong type
    {"Event_Type": "Hackathon"},                  # not an allowed category
    {"Has_Exam_Near_Event": 2},                   # flag must be 0 or 1
    {"Has_Exam_Near_Event": True},                # booleans are not accepted for 0/1 flags
    {"Previous_Event_Attendance": 6},             # attended more than registered (5)
    {"Previous_Attendance_Rate": 50.0},           # inconsistent with 4 / 5
    {"Unexpected_Field": 1},                      # extra fields are rejected
])
def test_out_of_contract_input_returns_422(client, change):
    r = client.post("/predict", json=dict(VALID, **change))
    assert r.status_code == 422, (change, r.status_code, r.text)


def test_missing_required_field_returns_422(client):
    payload = dict(VALID)
    payload.pop("Course")
    assert client.post("/predict", json=payload).status_code == 422


def test_nan_is_rejected(client):
    body = json.dumps(VALID).replace('"Event_Duration_Hours": 2.0', '"Event_Duration_Hours": NaN')
    r = client.post("/predict", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 422


# 5. monitoring + consistency ---------------------------------------------------
def test_predictions_are_logged_for_drift_checks(client):
    before = client.get("/monitoring/summary").json()["logged_predictions"]
    prob(client, VALID)
    after = client.get("/monitoring/summary").json()["logged_predictions"]
    assert after == before + 1
    last = json.loads(api.LOG_PATH.read_text().splitlines()[-1])
    assert last["input"]["Course"] == "B.Tech CSE" and 0 <= last["probability"] <= 1


def test_api_matches_desktop_predictor(client):
    """Both interfaces use core.prediction.Predictor on the same model file."""
    from core.prediction import Predictor
    desktop = Predictor(api.MODEL_DIR).predict_one(dict(VALID))
    assert prob(client, VALID) == pytest.approx(desktop["probability"], abs=1e-4)


def test_contract_matches_core_schema():
    """Every range in the core schema is the range Pydantic enforces."""
    from core.schema import COLUMNS
    props = api.Registration.model_json_schema()["properties"]
    for name, p in props.items():
        col = COLUMNS[name]
        num = p if "minimum" in p else next((a for a in p.get("anyOf", []) if "minimum" in a), None)
        if col.min is not None:
            assert num and num["minimum"] == col.min and num["maximum"] == col.max, name
        if col.allowed is not None:
            enum = p.get("enum") or next(a["enum"] for a in p.get("anyOf", []) if "enum" in a)
            assert set(enum) == set(col.allowed), name


def test_rejected_requests_are_counted_without_values(client):
    before = client.get("/monitoring/summary").json()["rejected_requests"]
    assert client.post("/predict", json=dict(VALID, Year=42)).status_code == 422
    assert client.get("/monitoring/summary").json()["rejected_requests"] == before + 1
    last = json.loads(api.REJECTED_LOG.read_text().splitlines()[-1])
    # the rejected value must never be logged (checked on the error entries only: the
    # timestamp itself may contain the digits "42")
    assert last["errors"][0]["field"] == "Year" and "42" not in json.dumps(last["errors"])
    assert set(last) == {"ts", "errors"} and all(set(e) <= {"field", "type"} for e in last["errors"])
