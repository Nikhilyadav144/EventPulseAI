"""EventPulse AI web backend (FastAPI).

Run (from the project folder):
    .venv/bin/python -m uvicorn webapp.server:app --host 127.0.0.1 --port 8100
    then open http://127.0.0.1:8100/  (the built React app in web/dist is served at /)

This is a thin HTTP layer over the existing, tested domain code in ``core/`` - the same
functions the PySide6 desktop app calls. Nothing is re-implemented here:
* events, registrations, attendance, check-in   -> core.db.repositories / services, core.workflow, core.checkin
* predictions (stored, model-versioned)          -> core.db.services.predict_* (core.prediction.Predictor)
* analytics, insights, monitoring, what-if       -> core.analytics, core.intelligence
* reports and exports                            -> core.reports

Persistence is the desktop's SQLite database (database/eventpulse.db, or EVENTPULSE_DB), so the
web app and the desktop app see the same records. Each request opens its own connection (WAL mode).
Access control (webapp/auth.py, docs/AUTH.md): every /api route below is ADMIN-ONLY (router
``admin`` with the ``require_admin`` dependency) except /api/health and the /api/auth, /api/student
routers. Students only reach their own records through /api/student/*. The /checkin pages need the
event's secret QR token AND a signed-in, administrator-verified student account: attendance is
recorded only for the Student ID linked to that account (Phase 2). Security configuration,
HTTPS/proxy handling and headers: webapp/security.py.
"""
from __future__ import annotations

import dataclasses
import io
import json
import math
import os
import tempfile
import html as _html
from urllib.parse import quote
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, Optional

import numpy as np
import pandas as pd
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

import app as serving                       # Registration schema + organiser tips (the CSL236 API)
import checkin_app
from config import settings
from core import analytics as A
from core import checkin as CK
from core import intelligence as I
from core import workflow as W
from core.data_loader import DataImportError, load_table
from core.data_validation import validate
from core.db import services
from core.db.backup import backup_database, list_backups
from core.db.connection import DEFAULT_DB_PATH, Database, web_database_url
from core.db.errors import (ConstraintViolationError, DatabaseError, DuplicateRecordError, NotFoundError,
                            PersonalDataError)
from core.db.repositories import (AttendanceRepository, EventRepository, ModelVersionRepository,
                                  PredictionRepository, RegistrationRepository, SettingsRepository,
                                  StudentRepository)
from core.prediction import FRIENDLY
from core.schema import COLUMNS, MODEL_INPUTS
from webapp import auth as AUTH
from webapp import bootstrap as BOOT
from webapp import security as SEC

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "web" / "dist"
DB_PATH = Path(os.environ.get("EVENTPULSE_DB", DEFAULT_DB_PATH))
# Optional PostgreSQL (cloud deployment). Unset -> the SQLite file above, exactly as before.
DB_URL = web_database_url()          # None unless an explicit postgresql:// URL is set (else refused)


def db_target():
    """Resolved on every call (not at import) so DB_PATH / DB_URL overrides always take effect."""
    return DB_URL or DB_PATH
EXPORT_DIR = ROOT / "exports" / "web"
API_LOG = ROOT / "logs" / "predictions.jsonl"
REJECTED_LOG = ROOT / "logs" / "rejected.jsonl"
DISMISSED_KEY = "dismissed_notifications"

@asynccontextmanager
async def lifespan(_app):
    SEC.install_log_redaction()                     # no tokens, codes, passwords or DB credentials in logs
    if AUTH.CONFIG.production:
        SEC.apply_process_timezone(AUTH.CONFIG)     # web process only (never the desktop app)
    with Database(db_target()) as db:               # core migrations on connect; web-auth migration next
        AUTH.ensure_auth_schema_once(db)
        BOOT.run_startup_bootstrap(db)              # PostgreSQL only: admin from secrets, optional demo seed/models
    yield


CONFIG = AUTH.CONFIG                                # webapp/security.py (fails closed in production)
_DOCS = CONFIG.docs_enabled                         # interactive API docs: development only
app = FastAPI(title="EventPulse AI - web API", version=settings.APP_VERSION, lifespan=lifespan,
              docs_url="/docs" if _DOCS else None, redoc_url="/redoc" if _DOCS else None,
              openapi_url="/openapi.json" if _DOCS else None,
              description="Event management and attendance prediction API used by the EventPulse AI web app. "
                          + settings.SIMULATED_TARGET_NOTICE)
if not CONFIG.production:                           # Vite dev server only; production is same-origin
    app.add_middleware(CORSMiddleware, allow_origins=list(SEC.DEV_ORIGINS),
                       allow_methods=["*"], allow_headers=["*"])
# Host check + HTTPS enforcement (production), cross-site request refusal, security headers.
SEC.install(app, CONFIG)


# =========================================================================== helpers
def get_db():
    db = Database(db_target())
    try:
        AUTH.ensure_auth_schema_once(db)
        yield db
    finally:
        db.close()


auth = AUTH.Auth(get_db)
# Every data/management endpoint is registered on this router: administrator only.
admin = APIRouter(dependencies=[Depends(auth.require_admin)])


def clean(obj: Any) -> Any:
    """JSON-safe conversion of DataFrames, dataclasses, numpy and dates (NaN -> null)."""
    if isinstance(obj, pd.DataFrame):
        return json.loads(obj.to_json(orient="records", date_format="iso", default_handler=str))
    if isinstance(obj, pd.Series):
        return clean(obj.to_dict())
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: clean(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [clean(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return clean(obj.tolist())
    if isinstance(obj, (datetime, date, pd.Timestamp)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    return obj


@app.exception_handler(DatabaseError)
async def db_error(_: Request, exc: DatabaseError):
    code = 404 if isinstance(exc, NotFoundError) else 409 if isinstance(exc, DuplicateRecordError) else 400
    return JSONResponse(status_code=code, content={"detail": str(exc), "type": type(exc).__name__})


@app.exception_handler(AUTH.HashingBusy)
async def hashing_busy(_: Request, exc: AUTH.HashingBusy):
    return JSONResponse(status_code=503, content={"detail": str(exc)}, headers={"Retry-After": "5"})


@app.exception_handler(DataImportError)
async def import_error(_: Request, exc: DataImportError):
    return JSONResponse(status_code=400, content={"detail": str(exc), "type": "DataImportError"})


@app.exception_handler(ValueError)
async def value_error(_: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc), "type": "ValueError"})


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    def safe(v):
        if isinstance(v, float) and not math.isfinite(v):
            return str(v)
        if isinstance(v, BaseException):
            return str(v)
        if isinstance(v, dict):
            return {k: safe(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [safe(x) for x in v]
        return v
    return JSONResponse(status_code=422, content={"detail": safe(exc.errors())})


def _upload_to_temp(f: UploadFile) -> Path:
    suffix = Path(f.filename or "upload.csv").suffix.lower()
    if suffix not in (".csv", ".xlsx"):
        raise HTTPException(400, "Upload a .csv or .xlsx file.")
    data = f.file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "Files up to 10 MB are accepted.")
    tmp = Path(tempfile.mkdtemp(prefix="eventpulse_")) / f"upload{suffix}"
    tmp.write_bytes(data)
    return tmp


def _file_response(df: pd.DataFrame, name: str, fmt: str) -> Response:
    if fmt == "xlsx":
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            df.to_excel(xw, index=False, sheet_name="Data")
        return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'})
    return Response(df.to_csv(index=False), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


# =========================================================================== meta
@app.get("/api/health")
def health():
    """Public liveness/readiness check for the hosting platform. Reveals nothing about the database,
    schema, model or version (those are in the administrator-only /api/health/details)."""
    try:
        with Database(db_target()) as db:
            db.scalar("SELECT 1")
    except Exception:                                   # never leak connection details
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ok"}


@admin.get("/api/health/details")
def health_details(db: Database = Depends(get_db)):
    active = ModelVersionRepository(db).active()
    return {"status": "ok", "version": settings.APP_VERSION, "database": db.display_name,
            "schema_version": db.scalar("SELECT MAX(version) FROM schema_migrations"),
            "active_model": f"{active['name']} v{active['version']}" if active else None,
            "demo": services.is_demo_database(db)}


@admin.get("/api/schema")
def schema():
    """The data contract used by forms (allowed values, ranges, friendly labels)."""
    out = []
    for name in MODEL_INPUTS:
        c = COLUMNS[name]
        out.append({"name": name, "label": FRIENDLY.get(name, name), "kind": c.kind,
                    "allowed": list(c.allowed) if c.allowed is not None else None,
                    "min": c.min, "max": c.max, "nullable": c.nullable, "description": c.description})
    return {"inputs": out, "notice": settings.SIMULATED_TARGET_NOTICE}


# =========================================================================== overview
@admin.get("/api/overview")
def overview(db: Database = Depends(get_db)):
    return clean({"kpis": A.overview_kpis(db), "sources": A.data_source_summary(db),
                  "activity": A.recent_activity(db, 10), "recent_predictions": A.recent_predictions(db, 8),
                  "distribution": A.attendance_distribution(db),
                  "participation": A.participation_by(db, "Department"),
                  "notifications": len(W.notifications(db))})


# =========================================================================== datasets
REFERENCE = {"processed": ("Original mid-term dataset (383 rows, simulated outcomes)", settings.PROCESSED_DATASET),
             "synthetic": ("Synthetic demo dataset (783 rows)", settings.SYNTHETIC_DATASET),
             "endterm": ("End-term training dataset (1,323 synthetic rows)", ROOT / "data" / "event_attendance.csv")}


def _profile(df: pd.DataFrame, label: str, source: str, page: int, size: int, search: str | None) -> dict:
    p = A.profile_dataset(df)
    rep = validate(df, mode="training" if "Will_Attend" in df.columns else "prediction")
    view = df
    if search:
        mask = view.astype(str).apply(lambda s: s.str.contains(search, case=False, na=False)).any(axis=1)
        view = view[mask]
    total = len(view)
    view = view.iloc[page * size:(page + 1) * size]
    return clean({"label": label, "source": source, "rows": p.rows, "columns": p.columns,
                  "dtypes": p.dtypes, "missing": p.missing, "duplicate_rows": p.duplicate_rows,
                  "duplicate_registrations": p.duplicate_registrations, "describe": p.describe,
                  "class_balance": p.class_balance,
                  "categorical": {k: [{"value": str(i), "count": int(v)} for i, v in s.items()]
                                  for k, s in list(p.categorical_counts.items())[:12]},
                  "validation": {"ok": rep.ok, "errors": rep.errors, "warnings": rep.warnings, "info": rep.info},
                  "preview": {"total": total, "page": page, "size": size, "rows": view}})


@admin.get("/api/datasets")
def datasets():
    return [{"key": k, "label": v[0], "available": v[1].exists()} for k, v in REFERENCE.items()]


@admin.get("/api/datasets/{key}")
def dataset(key: Literal["processed", "synthetic", "endterm"], page: int = Query(0, ge=0),
            size: int = Query(50, ge=1, le=500), search: Optional[str] = None):
    label, path = REFERENCE[key]
    if not path.exists():
        raise HTTPException(404, f"{path.name} is not available on this computer.")
    df = pd.read_csv(path)
    return _profile(df, label, {"processed": "ORIGINAL_HYBRID"}.get(key, "SYNTHETIC"), page, size, search)


@admin.post("/api/datasets/inspect")
def inspect_upload(file: UploadFile = File(...), page: int = 0, size: int = 50):
    """Inspect an uploaded CSV/Excel file. Read-only: nothing is stored. Personal-data columns
    and the Raw_Merged_Data sheet are refused by core.data_loader."""
    tmp = _upload_to_temp(file)
    loaded = load_table(tmp)
    return _profile(loaded.df, file.filename or "upload", "UPLOAD (not stored)", page, size, None)


# =========================================================================== students
@admin.get("/api/students")
def students(search: Optional[str] = None, limit: int = Query(200, le=2000), db: Database = Depends(get_db)):
    return clean(StudentRepository(db).list(search=search)[:limit])


# =========================================================================== events
class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: Optional[str] = Field(None, max_length=40)
    name: Optional[str] = Field(None, max_length=120)
    event_type: Literal["Technical", "Workshop", "Cultural", "Seminar", "Sports"]
    event_date: Optional[date] = None
    event_day: Optional[Literal["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]] = None
    start_time: Optional[str] = Field(None, pattern=r"^\d{2}:\d{2}$")
    time_slot: Literal["Morning", "Afternoon", "Evening"]
    duration_hours: float = Field(gt=0, le=12)
    venue: Optional[str] = Field(None, max_length=120)
    capacity: Optional[int] = Field(None, gt=0, le=100000)
    popularity: Optional[float] = Field(None, ge=0, le=100)
    registration_deadline: Optional[date] = None


class EventPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = Field(None, max_length=120)
    event_type: Optional[Literal["Technical", "Workshop", "Cultural", "Seminar", "Sports"]] = None
    event_date: Optional[date] = None
    start_time: Optional[str] = Field(None, pattern=r"^\d{2}:\d{2}$")
    time_slot: Optional[Literal["Morning", "Afternoon", "Evening"]] = None
    duration_hours: Optional[float] = Field(None, gt=0, le=12)
    venue: Optional[str] = Field(None, max_length=120)
    capacity: Optional[int] = Field(None, gt=0, le=100000)
    popularity: Optional[float] = Field(None, ge=0, le=100)
    registration_deadline: Optional[date] = None
    status: Optional[Literal["Scheduled", "Completed", "Cancelled"]] = None


def _next_event_id(db: Database) -> str:
    # 'EV' followed by digits only (was SQL GLOB, which PostgreSQL lacks; filtered here for both engines)
    ids = [r["event_id"] for r in db.query("SELECT event_id FROM events WHERE event_id LIKE 'EV%'")]
    n = [int(i[2:]) for i in ids if i[2:].isdigit()]
    return f"EV{(max(n) + 1) if n else 1:02d}"


@admin.get("/api/events")
def events(status: Optional[str] = None, search: Optional[str] = None, db: Database = Depends(get_db)):
    rows = db.query("SELECT e.*, p.active_registrations, p.recorded_outcomes, p.recorded_attended, "
                    "p.expected_attendance_active_model FROM events e LEFT JOIN v_event_participation p "
                    "USING(event_id) ORDER BY COALESCE(e.event_date, '9999'), e.event_id")
    out = []
    for r in rows:
        if status and r["status"] != status:
            continue
        if search and search.lower() not in f"{r['event_id']} {r['name'] or ''} {r['event_type']} {r['venue'] or ''}".lower():
            continue
        r["phase"] = W.event_phase(r)
        r["simulated_outcomes"] = CK.has_simulated_outcomes(db, r["event_id"])
        out.append(r)
    return clean(out)


@admin.get("/api/events/{event_id}")
def event(event_id: str, db: Database = Depends(get_db)):
    ev = EventRepository(db).get(event_id)
    ev["phase"] = W.event_phase(ev)
    regs = W.registration_table(db, event_id=event_id)
    return clean({"event": ev, "registrations": regs, "outcome": AttendanceRepository(db).outcome(event_id),
                  "history": EventRepository(db).history_counts(event_id),
                  "simulated_outcomes": CK.has_simulated_outcomes(db, event_id),
                  "evaluations": A.predicted_vs_actual(db)[lambda d: d["event_id"] == event_id]
                  if len(A.predicted_vs_actual(db)) else []})


@admin.post("/api/events", status_code=201)
def create_event(body: EventIn, db: Database = Depends(get_db)):
    data = body.model_dump()
    if data.get("event_date"):
        data["event_day"] = data["event_date"].strftime("%A")      # the date decides the day
    elif not data.get("event_day"):
        raise HTTPException(422, "Give an event date or, for an undated event, the planned day of the week.")
    data["event_id"] = (data.get("event_id") or "").strip() or _next_event_id(db)
    data = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in data.items() if v is not None}
    data.setdefault("status", "Scheduled")
    data["data_source"] = "MANUAL"
    with db.transaction():
        eid = EventRepository(db).create(**data)
        db.audit("create", "event", eid, "web")
    return clean(EventRepository(db).get(eid))


@admin.patch("/api/events/{event_id}")
def update_event(event_id: str, body: EventPatch, db: Database = Depends(get_db)):
    changes = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in body.model_dump(exclude_unset=True).items()}
    status = changes.pop("status", None)
    with db.transaction():
        if changes:
            EventRepository(db).update(event_id, **changes)
        if status:
            EventRepository(db).set_status(event_id, status)
        db.audit("update", "event", event_id, json.dumps(sorted(list(changes) + (["status"] if status else []))))
    return clean(EventRepository(db).get(event_id))


@admin.delete("/api/events/{event_id}")
def delete_event(event_id: str, db: Database = Depends(get_db)):
    """Only events without any history can be deleted (cancel them otherwise)."""
    h = EventRepository(db).history_counts(event_id)
    if any(h.values()):
        raise ConstraintViolationError(f"{event_id} has history {h}; cancel it instead of deleting.")
    with db.transaction():
        EventRepository(db).delete(event_id)
        db.audit("delete", "event", event_id, "web")
    return {"deleted": event_id}


class RegisterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    student_id: str = Field(min_length=1, max_length=40)
    registration_days_before: Optional[int] = Field(None, ge=0, le=365)
    has_exam_near_event: Literal[0, 1] = 0


@admin.post("/api/events/{event_id}/registrations", status_code=201)
def register(event_id: str, body: RegisterIn, db: Database = Depends(get_db)):
    regs = RegistrationRepository(db)
    if regs.find(body.student_id, event_id):
        raise DuplicateRecordError(f"{body.student_id} is already registered for {event_id}.")
    with db.transaction():
        ev = EventRepository(db).get(event_id)
        # a dated event registered "now" without an explicit lead time: the registration date is today
        today = SEC.local_today(CONFIG).isoformat() if body.registration_days_before is None and ev["event_date"] else None
        rid = regs.register(body.student_id, event_id, body.registration_days_before, body.has_exam_near_event,
                            registered_on=today, data_source="MANUAL")
    return clean(regs.get(rid))


class AttendanceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attended: Optional[dict[str, Literal[0, 1]]] = None
    headcount: Optional[int] = Field(None, ge=0)
    walk_ins: int = Field(0, ge=0)
    recorded_by: str = Field("web", max_length=60)


@admin.post("/api/events/{event_id}/attendance")
def record_attendance(event_id: str, body: AttendanceIn, db: Database = Depends(get_db)):
    if (body.attended is None) == (body.headcount is None):
        raise HTTPException(422, "Send either 'attended' (per student) or 'headcount'.")
    res = services.record_actual_attendance(db, event_id, attended=body.attended, headcount=body.headcount,
                                            walk_ins=body.walk_ins, recorded_by=body.recorded_by)
    return clean(res)


# =========================================================================== calendar
@admin.get("/api/calendar")
def calendar(start: Optional[date] = None, end: Optional[date] = None, phase: Optional[str] = None,
             db: Database = Depends(get_db)):
    df = W.calendar_events(db, start=start, end=end, phases=[phase] if phase else None)
    undated = db.scalar("SELECT COUNT(*) FROM events WHERE event_date IS NULL")
    return clean({"events": df, "undated": undated})


# =========================================================================== registrations
@admin.get("/api/registrations")
def registrations(search: Optional[str] = None, event_id: Optional[str] = None, department: Optional[str] = None,
                  status: Optional[str] = None, data_source: Optional[str] = None, db: Database = Depends(get_db)):
    df = W.registration_table(db, search, event_id, department, status, data_source)
    return clean({"rows": df, "departments": W.department_participation(db, event_id),
                  "all_departments": [r["d"] for r in db.query("SELECT DISTINCT department d FROM courses ORDER BY 1")]})


class StatusIn(BaseModel):
    status: Literal["Registered", "Waitlisted", "Cancelled"]


@admin.patch("/api/registrations/{registration_id}")
def set_registration_status(registration_id: int, body: StatusIn, db: Database = Depends(get_db)):
    with db.transaction():
        RegistrationRepository(db).set_status(registration_id, body.status)
        db.audit("status", "registration", registration_id, body.status)
    return clean(RegistrationRepository(db).get(registration_id))


@admin.post("/api/registrations/import")
def import_registrations(file: UploadFile = File(...), commit: bool = False, db: Database = Depends(get_db)):
    """Dry run by default (commit=false). All-or-nothing; duplicates skipped and reported."""
    df = load_table(_upload_to_temp(file)).df
    res = W.import_registrations(db, df, dry_run=not commit)
    return {"committed": commit and not res.errors, "added": res.added, "duplicates": res.duplicates,
            "errors": res.errors, "rows": len(df), "summary": res.summary()}


# =========================================================================== predictions
class ModelChoice(BaseModel):
    model_version_id: Optional[int] = None


class IndividualIn(serving.Registration):
    model_config = ConfigDict(extra="forbid", strict=False)
    Student_ID: Optional[str] = Field(None, max_length=40)
    Event_ID: Optional[str] = Field(None, max_length=40)
    model_version_id: Optional[int] = None


@admin.post("/api/predict")
def predict(body: IndividualIn, db: Database = Depends(get_db)):
    """Individual prediction with the selected (or active) desktop model; stored immutably
    in prediction history with the model version and the exact inputs used."""
    rec = body.model_dump(exclude={"model_version_id"})
    if not rec.get("Student_ID"):
        rec.pop("Student_ID")
    if not rec.get("Event_ID"):
        rec.pop("Event_ID")
    res = services.predict_record(db, rec, body.model_version_id)
    reg = serving.Registration(**{k: v for k, v in rec.items() if k in serving.Registration.model_fields})
    res["tips"] = serving.organiser_tips(reg, res["probability"], res["threshold"])
    res["factors"] = [dataclasses.asdict(f) for f in res.get("factors", [])]
    mv = ModelVersionRepository(db).get(res["model_version_id"])
    res["model"] = f"{mv['name']} v{mv['version']}"
    res["notice"] = settings.SIMULATED_TARGET_NOTICE
    return clean(res)


@admin.get("/api/registration-features")
def registration_features(student_id: str, event_id: str, db: Database = Depends(get_db)):
    """Model inputs of a stored registration (to pre-fill the individual prediction form)."""
    f = services.registration_features(db, event_id=event_id)
    f = f[f["Student_ID"] == student_id]
    if f.empty:
        raise NotFoundError(f"{student_id} has no active registration for {event_id}.")
    return clean(f.iloc[0][MODEL_INPUTS + ["Student_ID", "Event_ID"]])


@admin.post("/api/events/{event_id}/predict")
def predict_event(event_id: str, body: ModelChoice = ModelChoice(), db: Database = Depends(get_db)):
    s = services.predict_event(db, event_id, body.model_version_id)
    rows = pd.DataFrame(db.query(
        "SELECT p.student_ref AS student_id, p.probability, p.predicted_attend, p.certainty, p.forecast_used "
        "FROM predictions p WHERE p.run_id = ? ORDER BY p.probability DESC", (s["run_id"],)))
    s.pop("prediction_ids", None)
    return clean({"summary": s, "rows": rows})


@admin.post("/api/predict/file")
def predict_bulk(file: UploadFile = File(...), model_version_id: Optional[int] = None,
                 db: Database = Depends(get_db)):
    out, summary = services.predict_file(db, _upload_to_temp(file), model_version_id)
    return clean({"summary": summary, "rows": out.head(1000), "total": len(out)})


class WhatIfIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str
    changes: dict[str, Any] = {}
    registrations: Optional[int] = Field(None, ge=1, le=5000)
    model_version_id: Optional[int] = None


@admin.post("/api/whatif")
def whatif(body: WhatIfIn, db: Database = Depends(get_db)):
    r = I.what_if(db, body.event_id, body.changes, body.registrations, body.model_version_id)
    return clean({"event_id": r.event_id, "model": r.model, "changes": r.changes, "original": r.original,
                  "modified": r.modified, "difference": r.difference, "notes": r.notes})


@admin.get("/api/predictions")
def prediction_history(limit: int = Query(100, le=1000), db: Database = Depends(get_db)):
    return clean(A.recent_predictions(db, limit))


# =========================================================================== attendance records / check-in
@admin.get("/api/attendance/events")
def attendance_events(db: Database = Depends(get_db)):
    return clean(CK.real_events(db))


@admin.get("/api/attendance")
def attendance(event_id: Optional[str] = None, simulated: bool = False, db: Database = Depends(get_db)):
    if simulated:
        df = CK.attendance_table(db, None, include_simulated=True)
        df = df[df["source"].isin(CK.SIMULATED_SOURCES)] if not df.empty else df
    else:
        df = CK.attendance_table(db, event_id) if event_id else pd.DataFrame()
    tok = db.query_one("SELECT token, active FROM checkin_tokens WHERE event_id = ?", (event_id,)) if event_id else None
    return clean({"rows": df, "corrections": CK.correction_log(db, event_id),
                  "checkins": CK.checkins(db, event_id) if event_id else [],
                  "has_token": bool(tok and tok["active"])})


def _base_url(request: Request) -> str:
    if CONFIG.public_base_url:                       # production: the public HTTPS address
        return CONFIG.public_base_url
    host = request.url.hostname
    if host in ("127.0.0.1", "localhost"):
        host = CK.lan_address()                      # phones must reach this computer on the LAN
    return f"http://{host}:{request.url.port or 80}"


@admin.post("/api/attendance/{event_id}/qr")
def qr(event_id: str, request: Request, db: Database = Depends(get_db)):
    tok = CK.get_or_create_token(db, event_id)
    url = f"{_base_url(request)}/checkin/{event_id}?t={tok}"
    return {"url": url, "png": f"/api/attendance/{event_id}/qr.png"}


@admin.get("/api/attendance/{event_id}/qr.png")
def qr_png(event_id: str, request: Request, db: Database = Depends(get_db)):
    row = db.query_one("SELECT token, active FROM checkin_tokens WHERE event_id = ?", (event_id,))
    if not row or not row["active"]:
        raise NotFoundError("No active check-in code for this event.")
    return Response(CK.qr_png(f"{_base_url(request)}/checkin/{event_id}?t={row['token']}", 10),
                    media_type="image/png")


@admin.delete("/api/attendance/{event_id}/qr")
def revoke_qr(event_id: str, db: Database = Depends(get_db)):
    CK.revoke_token(db, event_id)
    return {"revoked": event_id}


class DeskIn(BaseModel):
    student_id: str = Field(min_length=1, max_length=40)


@admin.post("/api/attendance/{event_id}/checkin")
def desk_checkin(event_id: str, body: DeskIn, db: Database = Depends(get_db)):
    return clean(CK.check_in(db, event_id, body.student_id, method="DESK", today=SEC.local_today(CONFIG)))


class FinaliseIn(BaseModel):
    walk_ins: int = Field(0, ge=0, le=100000)


@admin.post("/api/attendance/{event_id}/finalise")
def finalise(event_id: str, body: FinaliseIn, db: Database = Depends(get_db)):
    return clean(CK.finalise_attendance(db, event_id, body.walk_ins, "web"))


class CorrectionIn(BaseModel):
    registration_id: int
    attended: Literal[0, 1]
    corrected_by: str = Field(min_length=1, max_length=60)
    reason: str = Field(min_length=1, max_length=300)


@admin.post("/api/attendance/correct")
def correct(body: CorrectionIn, db: Database = Depends(get_db)):
    CK.correct_attendance(db, body.registration_id, body.attended, body.corrected_by, body.reason)
    return {"ok": True}


# QR check-in (Phase 2). Scanning the code opens /checkin/<event>?t=<secret token>. Nothing happens
# without the event's current token AND a signed-in student account that the administrator has
# verified; attendance is recorded ONLY for the Student ID linked to that account (taken from the
# session - no Student ID is ever read from the request). The POST also needs the session's CSRF
# token. Per-account limit + a high per-address ceiling (a whole class shares one campus NAT address).
INVALID_LINK = "This check-in link is not valid. Scan the current QR code shown by the organiser."


def _checkin_token_ok(db: Database, event_id: str, token: str) -> bool:
    row = db.query_one("SELECT token, active FROM checkin_tokens WHERE event_id = ?", (event_id,))
    return bool(row and row["active"] and token and AUTH.hmac.compare_digest(row["token"], token))


def _checkin_page(title: str, sub: str, body: str, code: int = 200) -> HTMLResponse:
    return checkin_app._page(title, sub, body, code)


def _checkin_refused(code: int, msg: str, title: str = "Check-in") -> HTMLResponse:
    return _checkin_page(title, "", f'<div class="msg err">{_html.escape(msg)}</div>', code)


_LINK = ('style="display:block;text-align:center;text-decoration:none;font-weight:650;padding:13px;'
         'border-radius:12px;background:#6956A5;color:#fff;margin-top:6px"')


def _sign_in_page(event_id: str, token: str, ev: Optional[dict], code: int = 401) -> HTMLResponse:
    here = f"/checkin/{quote(event_id, safe='')}?t={quote(token, safe='')}"
    body = ('<div class="msg">Sign in with your student account to check in. Attendance is recorded for '
            'the Student ID the organiser verified for your account.</div>'
            f'<a {_LINK} href="/#/login?next={quote(here, safe="")}">Sign in to check in</a>'
            f'<p class="muted" style="margin-top:14px">Already signed in? <a href="{_html.escape(here)}">Continue</a></p>')
    return _checkin_page((ev or {}).get("name") or "Check-in", _event_sub(ev), body, code)


def _event_or_none(db: Database, event_id: str) -> Optional[dict]:
    try:
        return EventRepository(db).get(event_id)
    except DatabaseError:
        return None


def _event_sub(ev: Optional[dict]) -> str:
    return " · ".join(x for x in ((ev or {}).get("event_date"), (ev or {}).get("venue")) if x)


def _student_gate(s: Optional[dict]) -> Optional[tuple[int, str]]:
    """Why this session may not QR check in (None = allowed)."""
    if s["role"] != "student":
        return 403, "Organiser accounts do not check in with the QR code. Use desk check-in in the app."
    if s["link_status"] == "rejected":
        return 403, "The organiser could not confirm your Student ID, so you cannot check in. Please see the desk."
    if not AUTH.linked_student_id(s):
        return 403, ("Your account is waiting for the organiser to verify your Student ID. "
                     "Until then you cannot check in - please see the organiser's desk.")
    return None


@app.get("/checkin/{event_id}", response_class=HTMLResponse, include_in_schema=False)
def checkin_form(event_id: str, t: str = Query("", max_length=100),
                 s: Optional[dict] = Depends(auth.current_session), db: Database = Depends(get_db)):
    if not _checkin_token_ok(db, event_id, t):
        return _checkin_refused(403, INVALID_LINK)
    ev = _event_or_none(db, event_id)
    if not ev:
        return _checkin_refused(404, "This event does not exist.", "Unknown event")
    if not s:
        return _sign_in_page(event_id, t, ev)
    gate = _student_gate(s)
    if gate:
        return _checkin_page(ev["name"] or event_id, _event_sub(ev),
                             f'<div class="msg err">{_html.escape(gate[1])}</div>', gate[0])
    sid = AUTH.linked_student_id(s)
    body = (f'<p>Signed in as <b>{_html.escape(s["full_name"])}</b><br>Student ID <b>{_html.escape(sid)}</b></p>'
            f'<form method="post" action="/checkin/{_html.escape(quote(event_id, safe=""))}">'
            f'<input type="hidden" name="t" value="{_html.escape(t)}">'
            f'<input type="hidden" name="{AUTH.CSRF_FORM_FIELD}" value="{_html.escape(s["csrf_token"])}">'
            '<button type="submit">Check me in</button></form>'
            '<p class="muted" style="margin-top:14px">Not you? Sign out in the app first, then scan again.</p>')
    return _checkin_page(ev["name"] or event_id, _event_sub(ev), body)


@app.post("/checkin/{event_id}", response_class=HTMLResponse, include_in_schema=False)
def checkin_submit(event_id: str, request: Request, t: str = Form("", max_length=100),
                   csrf_token: str = Form("", max_length=200),
                   s: Optional[dict] = Depends(auth.current_session), db: Database = Depends(get_db)):
    ip = AUTH.client_ip(request)
    if AUTH.CHECKINS_PER_IP.blocked(ip):
        return _checkin_refused(429, "The check-in service is very busy. Please try again in a minute or see the desk.")
    AUTH.CHECKINS_PER_IP.hit(ip)
    if not _checkin_token_ok(db, event_id, t):
        return _checkin_refused(403, INVALID_LINK)
    ev = _event_or_none(db, event_id)
    if not ev:
        return _checkin_refused(404, "This event does not exist.", "Unknown event")
    if not s:
        return _sign_in_page(event_id, t, ev)
    if not AUTH.hmac.compare_digest(csrf_token or "", s["csrf_token"]):
        return _checkin_refused(403, "This page has expired. Scan the QR code again.")
    gate = _student_gate(s)
    if gate:
        return _checkin_refused(gate[0], gate[1], ev["name"] or event_id)
    key = f"user:{s['user_id']}"
    if AUTH.CHECKINS_PER_ACCOUNT.blocked(key):
        return _checkin_refused(429, "Too many check-in attempts for your account. Please see the organiser's desk.")
    AUTH.CHECKINS_PER_ACCOUNT.hit(key)
    sid = AUTH.linked_student_id(s)                  # ONLY the verified ID of the signed-in account
    sub = _event_sub(ev)
    try:
        r = CK.check_in(db, event_id, sid, t, "QR", today=SEC.local_today(CONFIG))   # event day in IST
    except DatabaseError as exc:
        return _checkin_page(ev["name"] or event_id, sub, f'<div class="msg err">{_html.escape(str(exc))}</div>', 400)
    at = SEC.local_now(CONFIG, datetime.fromisoformat(r["checked_in_utc"].replace("Z", "+00:00")))
    return _checkin_page(ev["name"] or event_id, sub,
                         f'<div class="msg ok">Checked in: <b>{_html.escape(r["student_id"])}</b> at '
                         f'{at:%H:%M} ({_html.escape(at.tzname() or CONFIG.timezone_name)}). Enjoy the event!</div>')


# =========================================================================== models
@admin.get("/api/models")
def models(db: Database = Depends(get_db)):
    mc = A.model_comparison(db)
    if len(mc):
        mc["supported"] = [not A.model_support_problems(db, int(i)) for i in mc["model_version_id"]]
    meta = ROOT / "model" / "metadata.json"
    saved = json.loads(meta.read_text()).get("mlflow_run_id") if meta.exists() else None
    runs = A.mlflow_runs()
    if len(runs):
        runs["saved_model"] = runs["run_id"] == saved
    return clean({"versions": mc, "mlflow": runs})


@admin.get("/api/models/{model_version_id}")
def model(model_version_id: int, db: Database = Depends(get_db)):
    d = A.model_details(db, model_version_id)
    d["metadata"] = {k: d["metadata"].get(k) for k in ("name", "description", "algorithm", "best_params",
                                                        "threshold", "threshold_rule", "trained_utc",
                                                        "training_data", "target_notice")}
    d["problems"] = A.model_support_problems(db, model_version_id)
    return clean(d)


@admin.post("/api/models/{model_version_id}/activate")
def activate(model_version_id: int, db: Database = Depends(get_db)):
    problems = A.model_support_problems(db, model_version_id)
    if problems:
        raise ConstraintViolationError("This model cannot be activated: " + " ".join(problems))
    with db.transaction():
        ModelVersionRepository(db).set_active(model_version_id)
        db.audit("activate", "model_version", model_version_id, "web")
    return {"active": model_version_id}


# =========================================================================== analytics / insights / monitoring
@admin.get("/api/analytics")
def analytics(dimension: str = "Department", source: Optional[str] = None, db: Database = Depends(get_db)):
    allowed = {"Department", "Course", "Year", "Event_Type", "Event_Day", "Event_Time", "Weather_Forecast",
               "Experience_Level", "Area_Of_Interest", "Role", "Distance_Category"}
    if dimension not in allowed:
        raise HTTPException(422, f"dimension must be one of {sorted(allowed)}")
    return clean({"participation": A.participation_by(db, dimension, source), "events": A.event_trends(db),
                  "predicted_vs_actual": A.predicted_vs_actual(db), "errors": A.prediction_error_summary(db, True),
                  "errors_recorded": A.prediction_error_summary(db, False),
                  "probability": A.probability_distribution(db), "models": A.model_comparison(db),
                  "importance": A.feature_importance(db), "distribution": A.attendance_distribution(db)})


@admin.get("/api/insights")
def insights(db: Database = Depends(get_db)):
    r = I.insights(db)
    return clean({"insights": [i.as_dict() for i in r["insights"]], "limitations": r["limitations"],
                  "counts": r["counts"]})


@admin.get("/api/monitoring")
def monitoring(db: Database = Depends(get_db)):
    m = I.monitor(db, None, API_LOG, REJECTED_LOG)
    return clean(m)


# =========================================================================== notifications
@admin.get("/api/notifications")
def notifications(db: Database = Depends(get_db)):
    dismissed = set(SettingsRepository(db).get(DISMISSED_KEY, []) or [])
    items = W.notifications(db, predicted=W.expected_attendance_by_event(db))
    return clean([dict(dataclasses.asdict(n), dismissed=n.key in dismissed) for n in items])


class KeyIn(BaseModel):
    key: Optional[str] = None


@admin.post("/api/notifications/dismiss")
def dismiss(body: KeyIn, db: Database = Depends(get_db)):
    s = SettingsRepository(db)
    cur = set(s.get(DISMISSED_KEY, []) or [])
    s.set(DISMISSED_KEY, sorted(cur | {body.key}) if body.key else [])
    return {"ok": True}


# =========================================================================== reports & export
TABLES = {"events": lambda db: A.event_trends(db), "registrations": lambda db: W.registration_table(db),
          "attendance_recorded": lambda db: CK.attendance_table(db, None, False),
          "predictions": lambda db: A.recent_predictions(db, 5000),
          "predicted_vs_actual": lambda db: A.predicted_vs_actual(db),
          "models": lambda db: A.model_comparison(db), "mlflow_runs": lambda db: A.mlflow_runs(),
          "calendar": lambda db: W.calendar_events(db)}


@admin.get("/api/export/{table}")
def export(table: str, fmt: Literal["csv", "xlsx"] = "csv", event_id: Optional[str] = None,
           db: Database = Depends(get_db)):
    if table not in TABLES:
        raise HTTPException(404, f"Unknown table; choose from {sorted(TABLES)}")
    df = TABLES[table](db)
    if event_id and "event_id" in df.columns:
        df = df[df["event_id"] == event_id]
    return _file_response(df, f"eventpulse_{table}", fmt)


@admin.get("/api/reports/summary")
def report_summary(db: Database = Depends(get_db)):
    from core.reports import collect, key_findings
    rd = collect(db)
    return clean({"findings": key_findings(rd), "kpis": rd.kpis, "sources": rd.sources})


@admin.post("/api/reports/{kind}")
def report(kind: Literal["pdf", "xlsx"], db: Database = Depends(get_db)):
    from core.reports import generate_excel, generate_pdf
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = EXPORT_DIR / f"EventPulse_report_{stamp}.{kind}"
    (generate_pdf if kind == "pdf" else generate_excel)(db, path)
    media = "application/pdf" if kind == "pdf" else \
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return FileResponse(path, media_type=media, filename=path.name)


# =========================================================================== settings
@admin.get("/api/settings")
def settings_info(db: Database = Depends(get_db)):
    import platform
    import sklearn
    active = ModelVersionRepository(db).active()
    return clean({"app": {"name": settings.APP_NAME, "tagline": settings.APP_TAGLINE, "version": settings.APP_VERSION,
                          "python": platform.python_version(), "sklearn": sklearn.__version__},
                  "database": {"path": (str(DB_PATH.relative_to(ROOT)) if DB_PATH.is_relative_to(ROOT) else str(DB_PATH))
                               if db.is_sqlite else db.display_name,
                               "engine": db.dialect,
                               "schema_version": db.scalar("SELECT MAX(version) FROM schema_migrations"),
                               "integrity": db.integrity_check() or "ok",
                               "demo": services.is_demo_database(db)},
                  "model": active,
                  "backups": [dataclasses.asdict(b) for b in list_backups()[:15]] if db.is_sqlite else [],
                  "datasets": datasets(), "privacy": [
                      "Event, registration and prediction records use pseudonymous Student IDs only; names, e-mails, phone and roll numbers are refused there.",
                      "Login accounts keep a name and e-mail in the separate users table; they are never exported in reports, datasets or predictions. Passwords are stored only as salted hashes.",
                      "The Raw_Merged_Data sheet of the original workbook is never loaded.",
                      "Dataset attendance outcomes are SIMULATED and kept separate from recorded attendance."]})


@admin.post("/api/settings/backup")
def backup(db: Database = Depends(get_db)):
    if not db.is_sqlite:
        raise HTTPException(400, "In-app backups are for the SQLite database. Use the PostgreSQL provider's "
                                 "backup or pg_dump instead.")
    return clean(dataclasses.asdict(backup_database(db, "web")))


# =========================================================================== routers
app.include_router(admin)
app.include_router(auth.auth_router)
app.include_router(auth.admin_router)
app.include_router(auth.student_router)


# =========================================================================== frontend
if (DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "Unknown API route")
    f = DIST / path
    if path and f.is_file() and DIST in f.resolve().parents:
        return FileResponse(f)
    index = DIST / "index.html"
    if not index.exists():
        return HTMLResponse("<h1>EventPulse AI</h1><p>The web frontend has not been built (web/dist missing).</p>",
                            status_code=503)
    return FileResponse(index)
