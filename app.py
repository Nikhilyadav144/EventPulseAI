"""EventPulse AI - prediction API and web UI (CSL236 end-term).

    uvicorn app:app --reload --port 8000        # then open http://127.0.0.1:8000/

Routes
    GET  /            prediction UI (static/index.html)
    GET  /health      liveness + whether the model is loaded
    POST /predict     validated prediction for one registration
    GET  /model-info  model, metrics and the data contract (used by the UI)
    GET  /monitoring/summary   how many predictions have been logged (drift monitoring)
    GET  /docs        interactive OpenAPI documentation (FastAPI)

The model (model/model.joblib) is loaded ONCE, when the app starts, by the same
``Predictor`` class the desktop app uses, so both interfaces give identical results.
Every prediction input is appended to logs/predictions.jsonl for drift checks
(see drift_check.py).
"""
from __future__ import annotations

import json
import math
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from config.settings import APP_VERSION, SIMULATED_TARGET_NOTICE
from core.prediction import FRIENDLY, Predictor
from core.schema import AREA_EVENT_FIT, COURSE_TO_DEPARTMENT, ROLE_EVENT_FIT

ROOT = Path(__file__).resolve().parent
MODEL_DIR = Path(os.environ.get("MODEL_DIR", ROOT / "model"))
LOG_PATH = Path(os.environ.get("PREDICTION_LOG", ROOT / "logs" / "predictions.jsonl"))
REJECTED_LOG = Path(os.environ.get("REJECTED_LOG", ROOT / "logs" / "rejected.jsonl"))
STATIC = ROOT / "static"

# ---------------------------------------------------------------------------
# Data contract (README "Data contract" table). Every rule below is enforced
# by Pydantic; anything else is rejected with HTTP 422.
# ---------------------------------------------------------------------------
CourseT = Literal[tuple(COURSE_TO_DEPARTMENT)]                       # type: ignore[valid-type]
ExperienceT = Literal["No experience", "Some experience", "Experienced"]
AreaT = Literal[tuple(AREA_EVENT_FIT)]                                # type: ignore[valid-type]
RoleT = Literal[tuple(ROLE_EVENT_FIT)]                                # type: ignore[valid-type]
EventTypeT = Literal["Technical", "Workshop", "Cultural", "Seminar", "Sports"]
DayT = Literal["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
SlotT = Literal["Morning", "Afternoon", "Evening"]
ForecastT = Literal["Clear", "Cloudy", "Rainy", "Unavailable"]


def _not_bool(v):
    if isinstance(v, bool):
        raise ValueError("use 0 or 1, not true/false")
    return v


FlagT = Annotated[Literal[0, 1], BeforeValidator(_not_bool)]


class Registration(BaseModel):
    """One student's registration for one event, as known BEFORE the event."""
    model_config = ConfigDict(extra="forbid", strict=False, json_schema_extra={"example": {
        "Course": "B.Tech CSE", "Year": 2, "Currently_UBA_Volunteer": 1, "Participated_UBA_Event_Before": 1,
        "Experience_Level": "Some experience", "Area_Of_Interest": "Problem Solving & Decision Making",
        "Role": "Content", "Event_Type": "Workshop", "Event_Day": "Wednesday", "Event_Time": "Morning",
        "Event_Duration_Hours": 2.0, "Registration_Days_Before": 5, "Previous_Event_Attendance": 4,
        "Previous_Events_Registered": 5, "Previous_Attendance_Rate": 80.0, "Academic_Engagement_Score": 72.0,
        "Event_Popularity": 70.0, "Distance_From_Venue_KM": 3.5, "Has_Exam_Near_Event": 0,
        "Weather_Forecast": "Clear"}})

    # student profile
    Course: CourseT = Field(description="Degree programme")
    Year: int = Field(ge=1, le=5, strict=True, description="Year of study")
    Currently_UBA_Volunteer: FlagT = Field(description="1 = currently a UBA volunteer")
    Participated_UBA_Event_Before: FlagT = Field(description="1 = took part in a UBA event before")
    Experience_Level: ExperienceT
    Area_Of_Interest: AreaT
    Role: RoleT = Field(description="Team the student applied for")
    # event
    Event_Type: EventTypeT
    Event_Day: DayT
    Event_Time: SlotT
    Event_Duration_Hours: float = Field(ge=0.5, le=12, allow_inf_nan=False, strict=True)
    Event_Popularity: Optional[float] = Field(None, ge=0, le=100, allow_inf_nan=False, strict=True,
                                              description="Pre-event interest score; null = unknown (imputed)")
    Weather_Forecast: ForecastT = Field("Unavailable", description="Event-day forecast; 'Unavailable' if none")
    # registration and history
    Registration_Days_Before: int = Field(ge=0, le=365, strict=True)
    Previous_Event_Attendance: int = Field(ge=0, le=500, strict=True)
    Previous_Events_Registered: Optional[int] = Field(None, ge=0, le=500, strict=True,
                                                      description="null = unknown (imputed)")
    Previous_Attendance_Rate: Optional[float] = Field(None, ge=0, le=100, allow_inf_nan=False, strict=True,
                                                      description="Derived; computed when null")
    Academic_Engagement_Score: Optional[float] = Field(None, ge=0, le=100, allow_inf_nan=False, strict=True,
                                                       description="null = unknown (imputed)")
    Distance_From_Venue_KM: Optional[float] = Field(None, ge=0, le=200, allow_inf_nan=False, strict=True,
                                                    description="null = unknown (imputed)")
    Has_Exam_Near_Event: FlagT = Field(description="1 = an exam falls near the event")

    @model_validator(mode="after")
    def history_is_consistent(self):
        reg, att = self.Previous_Events_Registered, self.Previous_Event_Attendance
        if reg is not None and att > reg:
            raise ValueError("Previous_Event_Attendance cannot exceed Previous_Events_Registered")
        if self.Previous_Attendance_Rate is not None:
            if reg is None:
                raise ValueError("Previous_Attendance_Rate needs Previous_Events_Registered")
            calc = 0.0 if reg == 0 else att / reg * 100
            if reg == 0 or abs(calc - self.Previous_Attendance_Rate) > 0.5:
                raise ValueError(f"Previous_Attendance_Rate must equal attended / registered x 100 "
                                 f"({'undefined' if reg == 0 else round(calc, 1)})")
        return self


class Factor(BaseModel):
    feature: str
    label: str
    value: Any
    typical: Any
    effect: float = Field(description="Change in probability vs the typical value (+ raises attendance)")


class Prediction(BaseModel):
    will_attend: bool
    probability: float
    threshold: float
    certainty: str
    label: str
    tips: list[str]
    factors: list[Factor]
    model: str
    notice: str


# ---------------------------------------------------------------------------
state: dict = {}
_log_lock = threading.Lock()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    state["predictor"] = Predictor(MODEL_DIR)          # loaded once, never per request
    yield
    state.clear()


app = FastAPI(title="EventPulse AI - attendance prediction API", version=APP_VERSION, lifespan=lifespan,
              description="Predicts whether a registered student will attend a college event. "
                          + SIMULATED_TARGET_NOTICE)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(RequestValidationError)
async def log_rejected(request: Request, exc: RequestValidationError):
    """Count schema violations for monitoring (field names and error types only, no values)."""
    if request.url.path == "/predict":
        try:
            REJECTED_LOG.parent.mkdir(parents=True, exist_ok=True)
            errs = [{"field": ".".join(str(x) for x in e.get("loc", [])[1:]), "type": e.get("type")}
                    for e in exc.errors()]
            with _log_lock, REJECTED_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                    "errors": errs}) + "\n")
        except OSError:
            pass
    # Pydantic includes the rejected value in its error payload.  JSON permits
    # neither NaN nor infinity, so return a JSON-safe 422 instead of letting
    # the error handler itself raise a 500 for a non-finite numeric request.
    def json_safe(value):
        if isinstance(value, float) and not math.isfinite(value):
            return str(value)
        if isinstance(value, BaseException):
            return str(value)
        if isinstance(value, dict):
            return {k: json_safe(v) for k, v in value.items()}
        if isinstance(value, list):
            return [json_safe(v) for v in value]
        if isinstance(value, tuple):
            return [json_safe(v) for v in value]
        return value

    return JSONResponse(status_code=422, content={"detail": json_safe(exc.errors())})


def predictor() -> Predictor:
    p = state.get("predictor")
    if p is None:
        raise HTTPException(503, "Model not loaded")
    return p


def organiser_tips(r: Registration, prob: float, threshold: float) -> list[str]:
    """2-3 rule-based, actionable tips for the event organiser (not model output)."""
    tips: list[tuple[int, str]] = []
    if r.Has_Exam_Near_Event:
        tips.append((1, "An exam is close to this event: send a short reminder and, if possible, share "
                        "slides or a recording so the student can catch up later."))
    if r.Registration_Days_Before <= 1:
        tips.append((2, "Last-minute registration: confirm by message a few hours before the start."))
    elif r.Registration_Days_Before >= 15:
        tips.append((2, "Registered more than two weeks ago: send a reminder one day before the event."))
    else:
        tips.append((8, "Send the usual reminder the day before, with venue and start time."))
    if r.Distance_From_Venue_KM is not None and r.Distance_From_Venue_KM > 12:
        tips.append((3, f"Long commute ({r.Distance_From_Venue_KM:g} km): share transport or shuttle details."))
    if r.Weather_Forecast == "Rainy":
        tips.append((3, "Rain is forecast: confirm an indoor venue and mention it in the reminder."))
    rate = r.Previous_Attendance_Rate
    if rate is None and r.Previous_Events_Registered:
        rate = r.Previous_Event_Attendance / r.Previous_Events_Registered * 100
    if rate is not None and rate < 50:
        tips.append((4, f"Has attended {rate:.0f}% of earlier registrations: a personal reminder helps most."))
    if r.Event_Type not in AREA_EVENT_FIT.get(r.Area_Of_Interest, set()):
        tips.append((5, "The event type does not match the stated interest: highlight what this student "
                        "gains from it in the reminder."))
    if r.Event_Time == "Evening" or r.Event_Day == "Saturday":
        tips.append((6, "Evening/Saturday slot: state the exact end time so students can plan travel."))
    if abs(prob - threshold) < 0.10:
        tips.append((7, "Borderline prediction: ask the student to confirm attendance before finalising numbers."))
    elif prob >= threshold:
        tips.append((7, "Likely to attend: count this registration when planning seats, kits and food."))
    else:
        tips.append((7, "Likely no-show: keep a waiting list so the place can be offered to someone else."))
    return [t for _, t in sorted(tips)[:3]]


def log_prediction(record: dict, result: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "input": record,
                       "probability": round(result["probability"], 4),
                       "will_attend": bool(result["predicted_attend"]), "model": result["model"]})
    with _log_lock, LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    p = state.get("predictor")
    return {"status": "ok" if p else "model_not_loaded", "model_loaded": p is not None,
            "model": p.name if p else None, "version": APP_VERSION}


@app.post("/predict", response_model=Prediction)
def predict(reg: Registration) -> Prediction:
    p = predictor()
    record = reg.model_dump()
    out = p.predict_one(record, top_k=5)
    log_prediction(record, out)
    prob, thr = out["probability"], out["threshold"]
    return Prediction(
        will_attend=bool(out["predicted_attend"]), probability=round(prob, 4), threshold=thr,
        certainty=out["certainty"], label="Will attend" if out["predicted_attend"] else "Likely no-show",
        tips=organiser_tips(reg, prob, thr), factors=[Factor(**vars(f)) for f in out["factors"]],
        model=out["model"], notice=SIMULATED_TARGET_NOTICE)


@app.get("/model-info")
def model_info():
    p = predictor()
    m = p.meta
    ev = m.get("evaluation", {})
    fields = []
    for name, f in Registration.model_json_schema()["properties"].items():
        opts = f.get("enum") or next((a.get("enum") for a in f.get("anyOf", []) if "enum" in a), None)
        num = f if "minimum" in f else next((a for a in f.get("anyOf", []) if "minimum" in a), {})
        fields.append({"name": name, "label": FRIENDLY.get(name, name), "options": opts,
                       "type": num.get("type") or f.get("type"), "min": num.get("minimum"),
                       "max": num.get("maximum"), "required": name in Registration.model_json_schema()["required"],
                       "default": f.get("default"), "description": f.get("description")})
    return {"model": m["name"], "algorithm": m.get("algorithm"), "params": m.get("best_params"),
            "threshold": p.threshold, "trained_utc": m.get("trained_utc"),
            "cv_roc_auc": ev.get("cv", {}).get("roc_auc", {}).get("mean"),
            "holdout": {k: ev.get("holdout", {}).get(k) for k in ("roc_auc", "accuracy", "f1", "n")},
            "mlflow_run_id": m.get("mlflow_run_id"), "training_rows": m.get("training_data", {}).get("rows_total"),
            "notice": SIMULATED_TARGET_NOTICE, "version": APP_VERSION, "fields": fields}


@app.get("/monitoring/summary")
def monitoring_summary():
    """Proof that inputs are being logged (count and time of the latest entry)."""
    if not LOG_PATH.exists():
        return {"logged_predictions": 0, "last": None, "log_file": str(LOG_PATH.relative_to(ROOT))
                if LOG_PATH.is_relative_to(ROOT) else str(LOG_PATH)}
    lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
    last = json.loads(lines[-1])["ts"] if lines else None
    rejected = (sum(1 for x in REJECTED_LOG.read_text(encoding="utf-8").splitlines() if x.strip())
                if REJECTED_LOG.exists() else 0)
    return {"logged_predictions": len(lines), "last": last, "rejected_requests": rejected,
            "log_file": str(LOG_PATH.relative_to(ROOT)) if LOG_PATH.is_relative_to(ROOT) else str(LOG_PATH)}
