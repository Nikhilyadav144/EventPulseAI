"""Repositories: one class per aggregate, all writes inside transactions.

Field names are the database column names (snake_case). Services translate
between dataset/model column names (``Event_Type``) and these.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from core.db.connection import Database, utc_now
from core.db.errors import (ConstraintViolationError, HistoryProtectedError, NotFoundError,
                            PersonalDataError)
from core.schema import AREA_EVENT_FIT, COURSE_TO_DEPARTMENT, PII_COLUMNS, ROLE_EVENT_FIT

_EMAIL = re.compile(r"@")
_PHONE = re.compile(r"\d{10}")
_ROLL = re.compile(r"^\d{2}[A-Za-z]{2,}\d{3}$")
_PII_KEYS = {k.lower() for k in PII_COLUMNS} | {"name", "full_name", "email", "phone", "mobile", "roll_number"}


def check_no_personal_data(record: dict, id_field: str | None = None) -> None:
    bad = sorted(k for k in record if k.lower() in _PII_KEYS)
    if bad:
        raise PersonalDataError("Personal information cannot be stored: " + ", ".join(bad))
    if id_field and record.get(id_field) is not None:
        v = str(record[id_field])
        if _EMAIL.search(v) or _PHONE.search(v) or _ROLL.match(v):
            raise PersonalDataError(f"{id_field} looks like an e-mail, phone or roll number. "
                                    "Use a pseudonymous code such as STU001.")


def _require(row: dict | None, what: str, key) -> dict:
    if row is None:
        raise NotFoundError(f"{what} '{key}' not found.")
    return row


def _set_clause(values: dict) -> tuple[str, list]:
    cols = list(values)
    return ", ".join(f"{c} = ?" for c in cols), [values[c] for c in cols]


# ======================================================================== settings
class SettingsRepository:
    def __init__(self, db: Database):
        self.db = db

    def get(self, key: str, default: Any = None) -> Any:
        r = self.db.query_one("SELECT value FROM app_settings WHERE key = ?", (key,))
        return json.loads(r["value"]) if r else default

    def set(self, key: str, value: Any) -> None:
        with self.db.transaction():
            self.db.execute("INSERT INTO app_settings(key, value, updated_utc) VALUES (?,?,?) "
                            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_utc = excluded.updated_utc",
                            (key, json.dumps(value), utc_now()))

    def all(self) -> dict:
        return {r["key"]: json.loads(r["value"]) for r in self.db.query("SELECT key, value FROM app_settings")}


# ======================================================================== datasets
class DatasetRepository:
    def __init__(self, db: Database):
        self.db = db

    def register(self, name: str, kind: str, file_name: str | None = None, sha256: str | None = None,
                 row_count: int | None = None, source_sheet: str | None = None, notes: str | None = None) -> int:
        if source_sheet == "Raw_Merged_Data":
            raise PersonalDataError("The Raw_Merged_Data sheet contains personal data and is never imported.")
        with self.db.transaction():
            cur = self.db.execute(
                "INSERT INTO datasets(name, kind, file_name, sha256, row_count, source_sheet, notes, imported_utc) "
                "VALUES (?,?,?,?,?,?,?,?)", (name, kind, file_name, sha256, row_count, source_sheet, notes, utc_now()))
            return int(cur.lastrowid)

    def find(self, kind: str, sha256: str) -> dict | None:
        return self.db.query_one("SELECT * FROM datasets WHERE kind = ? AND sha256 = ?", (kind, sha256))

    def get(self, dataset_id: int) -> dict:
        return _require(self.db.query_one("SELECT * FROM datasets WHERE dataset_id = ?", (dataset_id,)),
                        "Dataset", dataset_id)

    def list(self) -> list[dict]:
        return self.db.query("SELECT * FROM datasets ORDER BY dataset_id")


# ======================================================================== courses
class CourseRepository:
    def __init__(self, db: Database):
        self.db = db

    def get_or_create(self, name: str) -> int:
        name = str(name).strip()
        r = self.db.query_one("SELECT course_id FROM courses WHERE name = ?", (name,))
        if r:
            return int(r["course_id"])
        with self.db.transaction():
            cur = self.db.execute("INSERT INTO courses(name, department) VALUES (?,?)",
                                  (name, COURSE_TO_DEPARTMENT.get(name, "Other")))
            return int(cur.lastrowid)

    def list(self) -> list[dict]:
        return self.db.query("SELECT * FROM courses ORDER BY department, name")


# ======================================================================== students
STUDENT_COLUMNS = ("year", "currently_uba_volunteer", "participated_uba_event_before", "experience_level",
                   "area_of_interest", "role", "previous_event_attendance", "previous_events_registered",
                   "previous_attendance_rate", "academic_engagement_score", "distance_from_venue_km",
                   "data_source", "dataset_id")


class StudentRepository:
    def __init__(self, db: Database):
        self.db = db
        self.courses = CourseRepository(db)

    def _prepare(self, data: dict, partial: bool = False) -> dict:
        check_no_personal_data(data, "student_id")
        unknown = set(data) - set(STUDENT_COLUMNS) - {"student_id", "course"}
        if unknown:
            raise ConstraintViolationError(f"Unknown student field(s): {sorted(unknown)}")
        if "area_of_interest" in data and data["area_of_interest"] not in AREA_EVENT_FIT:
            raise ConstraintViolationError(f"Unknown area of interest '{data['area_of_interest']}'.")
        if "role" in data and data["role"] not in ROLE_EVENT_FIT:
            raise ConstraintViolationError(f"Unknown team/role '{data['role']}'.")
        out = {k: v for k, v in data.items() if k != "course"}
        if "course" in data:
            out["course_id"] = self.courses.get_or_create(data["course"])
        if not partial and "previous_attendance_rate" not in out:
            reg, att = out.get("previous_events_registered"), out.get("previous_event_attendance")
            if reg:
                out["previous_attendance_rate"] = round(att / reg * 100, 1)
        return out

    def create(self, **data) -> str:
        data.setdefault("data_source", "MANUAL")
        values = self._prepare(data)
        with self.db.transaction():
            cols = ", ".join(values)
            self.db.execute(f"INSERT INTO students({cols}) VALUES ({', '.join('?' * len(values))})",
                            list(values.values()))
            self.db.audit("create", "student", values["student_id"])
        return values["student_id"]

    def update(self, student_id: str, **changes) -> None:
        if "student_id" in changes:
            raise ConstraintViolationError("Student_ID cannot be changed.")
        values = self._prepare(changes, partial=True)
        values["updated_utc"] = utc_now()
        clause, params = _set_clause(values)
        with self.db.transaction():
            cur = self.db.execute(f"UPDATE students SET {clause} WHERE student_id = ?", params + [student_id])
            if cur.rowcount == 0:
                raise NotFoundError(f"Student '{student_id}' not found.")
            self.db.audit("update", "student", student_id, json.dumps(sorted(changes)))

    def get(self, student_id: str) -> dict:
        return _require(self.db.query_one(
            "SELECT s.*, c.name AS course, c.department FROM students s JOIN courses c USING(course_id) "
            "WHERE student_id = ?", (student_id,)), "Student", student_id)

    def exists(self, student_id: str) -> bool:
        return bool(self.db.scalar("SELECT EXISTS(SELECT 1 FROM students WHERE student_id = ?)", (student_id,)))

    def list(self, search: str | None = None, department: str | None = None, data_source: str | None = None,
             limit: int = 10000, offset: int = 0) -> list[dict]:
        sql = ("SELECT s.*, c.name AS course, c.department FROM students s JOIN courses c USING(course_id) "
               "WHERE 1=1")
        params: list = []
        if search:
            sql += " AND (lower(s.student_id) LIKE lower(?) OR lower(c.name) LIKE lower(?))"
            params += [f"%{search}%"] * 2
        if department:
            sql += " AND c.department = ?"
            params.append(department)
        if data_source:
            sql += " AND s.data_source = ?"
            params.append(data_source)
        sql += " ORDER BY s.student_id LIMIT ? OFFSET ?"
        return self.db.query(sql, params + [limit, offset])

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM students"))

    def delete(self, student_id: str) -> None:
        n = self.db.scalar("SELECT COUNT(*) FROM registrations WHERE student_id = ?", (student_id,))
        if n:
            raise HistoryProtectedError(f"Student '{student_id}' has {n} registration(s); remove them first.")
        with self.db.transaction():
            cur = self.db.execute("DELETE FROM students WHERE student_id = ?", (student_id,))
            if cur.rowcount == 0:
                raise NotFoundError(f"Student '{student_id}' not found.")
            self.db.audit("delete", "student", student_id)


# ======================================================================== events
EVENT_COLUMNS = ("name", "event_type", "event_date", "event_day", "start_time", "time_slot", "duration_hours",
                 "venue", "capacity", "popularity", "status", "data_source", "dataset_id",
                 "registration_deadline")
_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class EventRepository:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _prepare(data: dict) -> dict:
        unknown = set(data) - set(EVENT_COLUMNS) - {"event_id"}
        if unknown:
            raise ConstraintViolationError(f"Unknown event field(s): {sorted(unknown)}")
        out = dict(data)
        if out.get("event_date"):
            d = out["event_date"]
            if isinstance(d, (date, datetime)):
                d = d.strftime("%Y-%m-%d")
            try:
                parsed = date.fromisoformat(str(d))
            except ValueError as exc:
                raise ConstraintViolationError(f"Invalid event date '{d}' (use YYYY-MM-DD).") from exc
            out["event_date"] = parsed.isoformat()
            weekday = _WEEKDAYS[parsed.weekday()]
            if out.get("event_day") and out["event_day"] != weekday:
                raise ConstraintViolationError(f"{parsed} is a {weekday}, not a {out['event_day']}.")
            out["event_day"] = weekday
        if out.get("registration_deadline"):
            dl = out["registration_deadline"]
            if isinstance(dl, (date, datetime)):
                dl = dl.strftime("%Y-%m-%d")
            try:
                out["registration_deadline"] = date.fromisoformat(str(dl)).isoformat()
            except ValueError as exc:
                raise ConstraintViolationError(f"Invalid registration deadline '{dl}' (use YYYY-MM-DD).") from exc
            if out.get("event_date") and out["registration_deadline"] > out["event_date"]:
                raise ConstraintViolationError("The registration deadline must be on or before the event date.")
        return out

    def create(self, **data) -> str:
        data.setdefault("data_source", "MANUAL")
        values = self._prepare(data)
        with self.db.transaction():
            self.db.execute(f"INSERT INTO events({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
                            list(values.values()))
            self.db.audit("create", "event", values["event_id"])
        return values["event_id"]

    def update(self, event_id: str, **changes) -> None:
        if "event_id" in changes:
            raise ConstraintViolationError("Event_ID cannot be changed.")
        current = self.get(event_id)
        values = dict(changes)
        if "event_date" in changes or "event_day" in changes:
            # The weekday always follows the (new or existing) date when a date is known.
            values["event_date"] = changes.get("event_date", current["event_date"])
            values["event_day"] = changes.get("event_day") or (None if values["event_date"] else current["event_day"])
        values = self._prepare(values)
        deadline = values.get("registration_deadline", current.get("registration_deadline"))
        when = values.get("event_date", current["event_date"])
        if deadline and when and deadline > when:
            raise ConstraintViolationError("The registration deadline must be on or before the event date.")
        values["updated_utc"] = utc_now()
        clause, params = _set_clause(values)
        with self.db.transaction():
            self.db.execute(f"UPDATE events SET {clause} WHERE event_id = ?", params + [event_id])
            self.db.audit("update", "event", event_id, json.dumps(sorted(changes)))

    def get(self, event_id: str) -> dict:
        return _require(self.db.query_one("SELECT * FROM events WHERE event_id = ?", (event_id,)), "Event", event_id)

    def exists(self, event_id: str) -> bool:
        return bool(self.db.scalar("SELECT EXISTS(SELECT 1 FROM events WHERE event_id = ?)", (event_id,)))

    def list(self, status: str | None = None, event_type: str | None = None, search: str | None = None,
             data_source: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM v_event_participation WHERE 1=1", []
        for col, val in (("status", status), ("event_type", event_type), ("data_source", data_source)):
            if val:
                sql += f" AND {col} = ?"
                params.append(val)
        if search:
            sql += " AND (lower(event_id) LIKE lower(?) OR lower(name) LIKE lower(?))"
            params += [f"%{search}%"] * 2
        return self.db.query(sql + " ORDER BY COALESCE(event_date, '9999'), event_id", params)

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM events"))

    def set_status(self, event_id: str, status: str) -> None:
        self.get(event_id)
        with self.db.transaction():
            self.db.execute("UPDATE events SET status = ?, updated_utc = ? WHERE event_id = ?",
                            (status, utc_now(), event_id))
            self.db.audit("status", "event", event_id, status)

    def history_counts(self, event_id: str) -> dict:
        q = lambda sql: int(self.db.scalar(sql, (event_id,)))  # noqa: E731
        return {
            "registrations": q("SELECT COUNT(*) FROM registrations WHERE event_id = ?"),
            "predictions": q("SELECT COUNT(*) FROM predictions p JOIN registrations r USING(registration_id) "
                             "WHERE r.event_id = ?") + q("SELECT COUNT(*) FROM predictions WHERE registration_id "
                                                         "IS NULL AND event_ref = ?"),
            "attendance_records": q("SELECT COUNT(*) FROM attendance_records a JOIN registrations r "
                                    "USING(registration_id) WHERE r.event_id = ?"),
            "evaluations": q("SELECT COUNT(*) FROM prediction_evaluations WHERE event_id = ?"),
        }

    def delete(self, event_id: str, include_history: bool = False) -> dict:
        """Delete an event. Refuses if predictions, attendance or evaluations exist,
        unless ``include_history=True`` (the UI must have asked the user to confirm)."""
        self.get(event_id)
        counts = self.history_counts(event_id)
        historic = counts["predictions"] + counts["attendance_records"] + counts["evaluations"]
        if historic and not include_history:
            raise HistoryProtectedError(
                f"Event '{event_id}' has {counts['predictions']} prediction(s), {counts['attendance_records']} "
                f"attendance record(s) and {counts['evaluations']} evaluation(s). Cancel it instead, or confirm "
                "deletion of its history.")
        with self.db.transaction():
            regs = "SELECT registration_id FROM registrations WHERE event_id = ?"
            self.db.execute("DELETE FROM prediction_evaluations WHERE event_id = ?", (event_id,))
            self.db.execute(f"DELETE FROM predictions WHERE registration_id IN ({regs})", (event_id,))
            self.db.execute("DELETE FROM predictions WHERE registration_id IS NULL AND event_ref = ?", (event_id,))
            self.db.execute("DELETE FROM prediction_runs WHERE event_id = ? AND run_id NOT IN "
                            "(SELECT run_id FROM predictions)", (event_id,))
            self.db.execute(f"DELETE FROM attendance_records WHERE registration_id IN ({regs})", (event_id,))
            self.db.execute("DELETE FROM event_outcomes WHERE event_id = ?", (event_id,))
            self.db.execute("DELETE FROM registrations WHERE event_id = ?", (event_id,))
            self.db.execute("DELETE FROM events WHERE event_id = ?", (event_id,))
            self.db.audit("delete", "event", event_id, json.dumps(counts))
        return counts


# ======================================================================== forecasts
class ForecastRepository:
    def __init__(self, db: Database):
        self.db = db

    def add(self, event_id: str, forecast: str | None, obtained_at: str | datetime | None = None,
            source: str = "MANUAL", notes: str | None = None) -> int:
        forecast = forecast or "Unavailable"
        if isinstance(obtained_at, datetime):
            obtained_at = obtained_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if forecast != "Unavailable" and source == "MANUAL" and not obtained_at:
            raise ConstraintViolationError("Enter when the forecast was obtained.")
        EventRepository(self.db).get(event_id)
        with self.db.transaction():
            cur = self.db.execute(
                "INSERT INTO weather_forecasts(event_id, forecast, forecast_obtained_at, source, recorded_utc, notes) "
                "VALUES (?,?,?,?,?,?)", (event_id, forecast, obtained_at, source, utc_now(), notes))
            return int(cur.lastrowid)

    def latest(self, event_id: str) -> dict | None:
        return self.db.query_one("SELECT * FROM v_latest_forecast WHERE event_id = ?", (event_id,))

    def history(self, event_id: str) -> list[dict]:
        return self.db.query("SELECT * FROM weather_forecasts WHERE event_id = ? "
                             "ORDER BY recorded_utc DESC, forecast_id DESC", (event_id,))


# ======================================================================== registrations
class RegistrationRepository:
    def __init__(self, db: Database):
        self.db = db

    def register(self, student_id: str, event_id: str, registration_days_before: int | None = None,
                 has_exam_near_event: int = 0, registered_on: str | None = None, status: str = "Registered",
                 data_source: str = "MANUAL", dataset_id: int | None = None) -> int:
        event = EventRepository(self.db).get(event_id)
        StudentRepository(self.db).get(student_id)
        if event["status"] == "Cancelled":
            raise ConstraintViolationError("Cannot register for a cancelled event.")
        if registered_on and event["event_date"]:
            days = (date.fromisoformat(event["event_date"]) - date.fromisoformat(registered_on)).days
            if days < 0:
                raise ConstraintViolationError("Registration date is after the event date.")
            if registration_days_before is not None and registration_days_before != days:
                raise ConstraintViolationError(
                    f"Registration date implies {days} days before the event, not {registration_days_before}.")
            registration_days_before = days
        if registration_days_before is None:
            raise ConstraintViolationError("Provide registration_days_before or both dates.")
        with self.db.transaction():
            cur = self.db.execute(
                "INSERT INTO registrations(student_id, event_id, registered_on, registration_days_before, "
                "has_exam_near_event, status, data_source, dataset_id) VALUES (?,?,?,?,?,?,?,?)",
                (student_id, event_id, registered_on, int(registration_days_before), int(has_exam_near_event),
                 status, data_source, dataset_id))
            return int(cur.lastrowid)

    def get(self, registration_id: int) -> dict:
        return _require(self.db.query_one("SELECT * FROM registrations WHERE registration_id = ?",
                                          (registration_id,)), "Registration", registration_id)

    def find(self, student_id: str, event_id: str) -> dict | None:
        return self.db.query_one("SELECT * FROM registrations WHERE student_id = ? AND event_id = ?",
                                 (student_id, event_id))

    def for_event(self, event_id: str, status: str | None = None) -> list[dict]:
        sql, p = "SELECT * FROM registrations WHERE event_id = ?", [event_id]
        if status:
            sql += " AND status = ?"
            p.append(status)
        return self.db.query(sql + " ORDER BY student_id", p)

    def for_student(self, student_id: str) -> list[dict]:
        return self.db.query("SELECT * FROM registrations WHERE student_id = ? ORDER BY event_id", (student_id,))

    def set_status(self, registration_id: int, status: str) -> None:
        self.get(registration_id)
        with self.db.transaction():
            self.db.execute("UPDATE registrations SET status = ?, updated_utc = ? WHERE registration_id = ?",
                            (status, utc_now(), registration_id))

    def delete(self, registration_id: int) -> None:
        self.get(registration_id)
        hist = self.db.scalar("SELECT (SELECT COUNT(*) FROM predictions WHERE registration_id = ?) + "
                              "(SELECT COUNT(*) FROM attendance_records WHERE registration_id = ?)",
                              (registration_id, registration_id))
        if hist:
            raise HistoryProtectedError("This registration has prediction or attendance history; cancel it instead.")
        with self.db.transaction():
            self.db.execute("DELETE FROM registrations WHERE registration_id = ?", (registration_id,))
            self.db.audit("delete", "registration", registration_id)

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM registrations"))


# ======================================================================== model versions
def portable_path(p: str | Path) -> str:
    """Store paths inside the project relative to it, so the database survives moving the folder."""
    from config import settings
    p = Path(p).resolve()
    try:
        return p.relative_to(settings.PROJECT_ROOT).as_posix()
    except ValueError:
        return str(p)


def resolve_path(stored: str) -> Path:
    from config import settings
    p = Path(stored)
    return p if p.is_absolute() else settings.PROJECT_ROOT / p


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


class ModelVersionRepository:
    def __init__(self, db: Database):
        self.db = db

    def register_from_dir(self, model_dir: str | Path, dataset_id: int | None = None) -> int:
        """Register a saved model (models/<id>/) as a new version, or return the
        existing version if this exact artifact is already registered."""
        model_dir = Path(model_dir)
        meta = json.loads((model_dir / "metadata.json").read_text())
        artifact = model_dir / "model.joblib"
        sha = _sha256(artifact)
        key = meta["model_id"]
        existing = self.db.query_one("SELECT model_version_id FROM model_versions WHERE model_key = ? AND "
                                     "artifact_sha256 = ?", (key, sha))
        if existing:
            return int(existing["model_version_id"])
        env = meta.get("environment", {})
        td = meta.get("training_data", {})
        with self.db.transaction():
            version = int(self.db.scalar("SELECT COALESCE(MAX(version), 0) + 1 FROM model_versions "
                                         "WHERE model_key = ?", (key,)))
            cur = self.db.execute(
                "INSERT INTO model_versions(model_key, version, name, algorithm, is_baseline, artifact_path, "
                "artifact_sha256, threshold, hyperparameters, training_dataset_id, training_data_label, "
                "training_data_sha256, sklearn_version, python_version, trained_utc, registered_utc, notes) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, version, meta["name"], meta["name"].split(" (")[0], int(meta.get("is_baseline", False)),
                 portable_path(model_dir), sha, float(meta["threshold"]), json.dumps(meta.get("best_params", {})),
                 dataset_id, td.get("label"), td.get("sha256"), env.get("scikit_learn"), env.get("python"),
                 meta.get("trained_utc"), utc_now(), meta.get("description")))
            mv = int(cur.lastrowid)
            self._store_metrics(mv, meta.get("evaluation", {}))
            self.db.audit("register", "model_version", mv, f"{key} v{version}")
        return mv

    def _store_metrics(self, mv: int, ev: dict) -> None:
        rows = []
        for name, d in (ev.get("cv") or {}).items():
            rows.append((mv, "cv", name, d.get("mean"), d.get("std"), len(d.get("folds", [])) or None))
        hold = ev.get("holdout") or {}
        n = hold.get("n")
        for name in ("accuracy", "balanced_accuracy", "precision", "recall", "f1", "f1_macro", "roc_auc",
                     "brier", "recall_non_attenders", "precision_non_attenders", "threshold"):
            if hold.get(name) is not None:
                rows.append((mv, "holdout", name, float(hold[name]), None, n))
        if hold.get("confusion_matrix"):
            (tn, fp), (fn, tp) = hold["confusion_matrix"]
            rows += [(mv, "holdout", k, float(v), None, n) for k, v in (("tn", tn), ("fp", fp), ("fn", fn), ("tp", tp))]
        base = ev.get("majority_baseline") or {}
        for name in ("accuracy", "balanced_accuracy", "f1", "roc_auc", "brier"):
            if base.get(name) is not None:
                rows.append((mv, "majority_baseline", name, float(base[name]), None, base.get("n")))
        sens = ev.get("split_sensitivity") or {}
        if sens:
            rows += [(mv, "split_sensitivity", "roc_auc_mean", sens["mean"], None, sens.get("splits")),
                     (mv, "split_sensitivity", "roc_auc_min", sens["min"], None, sens.get("splits")),
                     (mv, "split_sensitivity", "roc_auc_max", sens["max"], None, sens.get("splits"))]
        self.db.conn.executemany("INSERT INTO model_metrics(model_version_id, evaluation_set, metric, value, std, n) "
                                 "VALUES (?,?,?,?,?,?)", rows)

    def set_active(self, model_version_id: int) -> None:
        self.get(model_version_id)
        with self.db.transaction():
            self.db.execute("UPDATE model_versions SET is_active = 0 WHERE is_active = 1")
            self.db.execute("UPDATE model_versions SET is_active = 1 WHERE model_version_id = ?", (model_version_id,))
            self.db.audit("activate", "model_version", model_version_id)

    def active(self) -> dict | None:
        return self.db.query_one("SELECT * FROM model_versions WHERE is_active = 1")

    def get(self, model_version_id: int) -> dict:
        return _require(self.db.query_one("SELECT * FROM model_versions WHERE model_version_id = ?",
                                          (model_version_id,)), "Model version", model_version_id)

    def list(self) -> list[dict]:
        return self.db.query("SELECT * FROM model_versions ORDER BY model_key, version")

    def metrics(self, model_version_id: int, evaluation_set: str | None = None) -> dict:
        sql, p = "SELECT * FROM model_metrics WHERE model_version_id = ?", [model_version_id]
        if evaluation_set:
            sql += " AND evaluation_set = ?"
            p.append(evaluation_set)
        out: dict = {}
        for r in self.db.query(sql, p):
            out.setdefault(r["evaluation_set"], {})[r["metric"]] = {"value": r["value"], "std": r["std"], "n": r["n"]}
        return out


# ======================================================================== predictions
class PredictionRepository:
    def __init__(self, db: Database):
        self.db = db

    def create_run(self, model_version_id: int, run_type: str, event_id: str | None = None,
                   input_dataset_id: int | None = None, notes: str | None = None) -> int:
        with self.db.transaction():
            cur = self.db.execute("INSERT INTO prediction_runs(model_version_id, run_type, event_id, "
                                  "input_dataset_id, created_utc, notes) VALUES (?,?,?,?,?,?)",
                                  (model_version_id, run_type, event_id, input_dataset_id, utc_now(), notes))
            return int(cur.lastrowid)

    def add_many(self, run_id: int, model_version_id: int, rows: Iterable[dict]) -> list[int]:
        """rows: dicts with probability, predicted_attend, threshold, certainty, forecast_used,
        input_snapshot (dict) and optionally registration_id, forecast_id, student_ref, event_ref,
        predicted_utc."""
        ids = []
        now = utc_now()
        with self.db.transaction():
            for r in rows:
                cur = self.db.execute(
                    "INSERT INTO predictions(run_id, model_version_id, registration_id, forecast_id, student_ref, "
                    "event_ref, probability, predicted_attend, threshold, certainty, forecast_used, input_snapshot, "
                    "predicted_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, model_version_id, r.get("registration_id"), r.get("forecast_id"), r.get("student_ref"),
                     r.get("event_ref"), float(r["probability"]), int(r["predicted_attend"]), float(r["threshold"]),
                     r["certainty"], r["forecast_used"], json.dumps(r["input_snapshot"], default=str),
                     r.get("predicted_utc") or now))
                ids.append(int(cur.lastrowid))
            self.db.execute("UPDATE prediction_runs SET n_predictions = n_predictions + ? WHERE run_id = ?",
                            (len(ids), run_id))
        return ids

    def get(self, prediction_id: int) -> dict:
        return _require(self.db.query_one("SELECT * FROM predictions WHERE prediction_id = ?", (prediction_id,)),
                        "Prediction", prediction_id)

    def history(self, event_id: str | None = None, student_id: str | None = None,
                model_version_id: int | None = None, limit: int = 1000) -> list[dict]:
        sql = ("SELECT p.*, COALESCE(r.student_id, p.student_ref) AS student_id, "
               "COALESCE(r.event_id, p.event_ref) AS event_id, mv.name AS model_name, mv.version AS model_version, "
               "pr.run_type FROM predictions p JOIN model_versions mv USING(model_version_id) "
               "JOIN prediction_runs pr USING(run_id) LEFT JOIN registrations r USING(registration_id) WHERE 1=1")
        params: list = []
        if event_id:
            sql += " AND COALESCE(r.event_id, p.event_ref) = ?"
            params.append(event_id)
        if student_id:
            sql += " AND COALESCE(r.student_id, p.student_ref) = ?"
            params.append(student_id)
        if model_version_id:
            sql += " AND p.model_version_id = ?"
            params.append(model_version_id)
        return self.db.query(sql + " ORDER BY p.predicted_utc DESC, p.prediction_id DESC LIMIT ?", params + [limit])

    def runs(self, limit: int = 200) -> list[dict]:
        return self.db.query("SELECT pr.*, mv.name AS model_name, mv.version AS model_version FROM prediction_runs pr "
                             "JOIN model_versions mv USING(model_version_id) ORDER BY run_id DESC LIMIT ?", (limit,))

    def latest_for_event(self, event_id: str, model_version_id: int, cutoff_utc: str | None = None) -> list[dict]:
        """Latest prediction per active registration of the event, made at or before ``cutoff_utc``."""
        sql = ("SELECT p.* FROM predictions p JOIN registrations r USING(registration_id) "
               "WHERE r.event_id = ? AND r.status = 'Registered' AND p.model_version_id = ? "
               + ("AND p.predicted_utc <= ? " if cutoff_utc else "") +
               "AND p.prediction_id = (SELECT p2.prediction_id FROM predictions p2 WHERE "
               "p2.registration_id = p.registration_id AND p2.model_version_id = p.model_version_id "
               + ("AND p2.predicted_utc <= ? " if cutoff_utc else "") +
               "ORDER BY p2.predicted_utc DESC, p2.prediction_id DESC LIMIT 1)")
        params = [event_id, model_version_id] + ([cutoff_utc, cutoff_utc] if cutoff_utc else [])
        return self.db.query(sql, params)

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM predictions"))


# ======================================================================== actual attendance
class AttendanceRepository:
    def __init__(self, db: Database):
        self.db = db

    def record(self, registration_id: int, attended: int, source: str = "MANUAL",
               recorded_by: str | None = None, notes: str | None = None) -> int:
        reg = RegistrationRepository(self.db).get(registration_id)
        ev = EventRepository(self.db).get(reg["event_id"])
        _check_event_happened(ev)
        if attended not in (0, 1, True, False):
            raise ConstraintViolationError("Attendance must be 0 or 1.")
        with self.db.transaction():
            cur = self.db.execute(
                "INSERT INTO attendance_records(registration_id, attended, source, recorded_by, recorded_utc, notes) "
                "VALUES (?,?,?,?,?,?)", (registration_id, int(attended), source, recorded_by, utc_now(), notes))
            return int(cur.lastrowid)

    def correct(self, registration_id: int, attended: int, recorded_by: str | None = None, reason: str = "") -> None:
        """Change a recorded outcome; the old value is kept in the audit log."""
        old = self.db.query_one("SELECT * FROM attendance_records WHERE registration_id = ?", (registration_id,))
        _require(old, "Attendance record for registration", registration_id)
        with self.db.transaction():
            self.db.execute("UPDATE attendance_records SET attended = ?, recorded_by = ?, recorded_utc = ?, "
                            "notes = ? WHERE registration_id = ?",
                            (int(attended), recorded_by, utc_now(), reason or old["notes"], registration_id))
            self.db.audit("correct", "attendance", registration_id,
                          json.dumps({"old": old["attended"], "new": int(attended), "reason": reason,
                                     "by": recorded_by}))

    def for_event(self, event_id: str) -> list[dict]:
        return self.db.query("SELECT a.*, r.student_id FROM attendance_records a JOIN registrations r "
                             "USING(registration_id) WHERE r.event_id = ? ORDER BY r.student_id", (event_id,))

    def set_outcome(self, event_id: str, method: str, actual_attendees: int, walk_ins: int = 0,
                    recorded_by: str | None = None, notes: str | None = None) -> None:
        ev = EventRepository(self.db).get(event_id)
        _check_event_happened(ev)
        with self.db.transaction():
            old = self.db.query_one("SELECT * FROM event_outcomes WHERE event_id = ?", (event_id,))
            self.db.execute(
                "INSERT INTO event_outcomes(event_id, method, actual_attendees, walk_ins, recorded_by, recorded_utc, "
                "notes) VALUES (?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET method = excluded.method, "
                "actual_attendees = excluded.actual_attendees, walk_ins = excluded.walk_ins, "
                "recorded_by = excluded.recorded_by, recorded_utc = excluded.recorded_utc, notes = excluded.notes",
                (event_id, method, int(actual_attendees), int(walk_ins), recorded_by, utc_now(), notes))
            if old:
                self.db.audit("correct", "event_outcome", event_id, json.dumps(old, default=str))

    def outcome(self, event_id: str) -> dict | None:
        return self.db.query_one("SELECT * FROM event_outcomes WHERE event_id = ?", (event_id,))


def _check_event_happened(event: dict) -> None:
    if event["status"] == "Cancelled":
        raise ConstraintViolationError("The event was cancelled; attendance cannot be recorded.")
    # Event dates are local calendar dates (as entered by the organiser), so compare with the local date.
    if event["event_date"] and date.fromisoformat(event["event_date"]) > date.today():
        raise ConstraintViolationError("The event has not taken place yet.")


# ======================================================================== evaluations
class EvaluationRepository:
    def __init__(self, db: Database):
        self.db = db

    def add(self, values: dict, items: list[tuple[int, int | None]]) -> int:
        with self.db.transaction():
            cols = list(values)
            cur = self.db.execute(f"INSERT INTO prediction_evaluations({', '.join(cols)}) VALUES "
                                  f"({', '.join('?' * len(cols))})", [values[c] for c in cols])
            ev_id = int(cur.lastrowid)
            self.db.conn.executemany("INSERT INTO prediction_evaluation_items(evaluation_id, prediction_id, "
                                     "attendance_id) VALUES (?,?,?)", [(ev_id, p, a) for p, a in items])
            return ev_id

    def for_event(self, event_id: str) -> list[dict]:
        return self.db.query("SELECT pe.*, mv.name AS model_name, mv.version AS model_version FROM "
                             "prediction_evaluations pe JOIN model_versions mv USING(model_version_id) "
                             "WHERE event_id = ? ORDER BY computed_utc DESC, evaluation_id DESC", (event_id,))

    def history(self, model_version_id: int | None = None, include_simulated: bool = True) -> list[dict]:
        sql = ("SELECT pe.*, mv.name AS model_name, mv.version AS model_version, e.event_type, e.event_date "
               "FROM prediction_evaluations pe JOIN model_versions mv USING(model_version_id) "
               "JOIN events e USING(event_id) WHERE 1=1")
        p: list = []
        if model_version_id:
            sql += " AND pe.model_version_id = ?"
            p.append(model_version_id)
        if not include_simulated:
            sql += " AND pe.target_is_simulated = 0"
        return self.db.query(sql + " ORDER BY pe.computed_utc, pe.evaluation_id", p)

    def items(self, evaluation_id: int) -> list[dict]:
        return self.db.query("SELECT * FROM prediction_evaluation_items WHERE evaluation_id = ?", (evaluation_id,))
