"""Application services that combine repositories with the ML backend.

These are the functions the desktop UI will call.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import balanced_accuracy_score, roc_auc_score

from config import settings
from core.data_loader import load_table
from core.data_validation import validate
from core.db.connection import Database, utc_now
from core.db.errors import ConstraintViolationError, NotFoundError, PersonalDataError
from core.db.repositories import (AttendanceRepository, DatasetRepository, EvaluationRepository,
                                  EventRepository, ForecastRepository, ModelVersionRepository,
                                  PredictionRepository, RegistrationRepository, SettingsRepository,
                                  StudentRepository, check_no_personal_data)
from core.preprocessing import prepare_frame
from core.schema import FORECAST_UNAVAILABLE, MODEL_INPUTS, PII_COLUMNS

SIMULATED_SOURCES = {"ORIGINAL_SIMULATED", "SYNTHETIC"}


# ======================================================================== dataset import
@dataclass
class ImportSummary:
    dataset_id: int
    already_imported: bool = False
    students: int = 0
    events: int = 0
    registrations: int = 0
    forecasts: int = 0
    attendance_records: int = 0
    skipped_duplicates: int = 0
    warnings: list[str] = field(default_factory=list)


_STUDENT_MAP = {
    "Year": "year", "Currently_UBA_Volunteer": "currently_uba_volunteer",
    "Participated_UBA_Event_Before": "participated_uba_event_before", "Experience_Level": "experience_level",
    "Area_Of_Interest": "area_of_interest", "Role": "role",
    "Previous_Event_Attendance": "previous_event_attendance",
    "Previous_Events_Registered": "previous_events_registered",
    "Previous_Attendance_Rate": "previous_attendance_rate",
    "Academic_Engagement_Score": "academic_engagement_score", "Distance_From_Venue_KM": "distance_from_venue_km",
}
_EVENT_MAP = {"Event_Type": "event_type", "Event_Day": "event_day", "Event_Time": "time_slot",
              "Event_Duration_Hours": "duration_hours", "Event_Popularity": "popularity"}


def _clean(v):
    if v is None:
        return None
    if isinstance(v, float) and np.isnan(v):
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    return v


def _first_consistent(group: pd.DataFrame, col: str, entity: str, key) -> object:
    vals = group[col].dropna().unique()
    if len(vals) > 1:
        raise ConstraintViolationError(f"{entity} '{key}' has conflicting values for {col}: {list(vals)[:3]}")
    return _clean(vals[0]) if len(vals) else None


def import_dataset(db: Database, df: pd.DataFrame, kind: str, name: str, file_name: str | None = None,
                   sha256: str | None = None, source_sheet: str | None = None) -> ImportSummary:
    """Import an ML-format table (ML_Dataset / processed / synthetic) into the
    relational tables in ONE transaction. Rows with a Will_Attend value become
    attendance records labelled as simulated."""
    if source_sheet == "Raw_Merged_Data":
        raise PersonalDataError("The Raw_Merged_Data sheet contains personal data and is never imported.")
    found_pii = sorted(PII_COLUMNS.intersection(df.columns))
    if found_pii:
        raise PersonalDataError("Personal-data columns present: " + ", ".join(found_pii))
    has_target = "Will_Attend" in df.columns and df["Will_Attend"].notna().any()
    rep = validate(df, "training" if has_target else "prediction")
    if not rep.ok:
        raise ConstraintViolationError("The dataset failed validation:\n" + rep.summary())
    for c in ("Student_ID", "Event_ID"):
        if c not in df.columns:
            raise ConstraintViolationError(f"Column {c} is required to import records.")

    datasets = DatasetRepository(db)
    if sha256 and (existing := datasets.find(kind, sha256)):
        return ImportSummary(dataset_id=existing["dataset_id"], already_imported=True)

    data = prepare_frame(df)
    raw_forecast = df["Weather_Forecast"] if "Weather_Forecast" in df.columns else pd.Series(None, index=df.index)
    data_source = {"ORIGINAL": "ORIGINAL_HYBRID", "PROCESSED": "ORIGINAL_HYBRID",
                   "SYNTHETIC": "SYNTHETIC"}.get(kind, "UPLOAD")
    if "Data_Source" in df.columns and set(df["Data_Source"].dropna()) == {"SYNTHETIC"}:
        data_source = "SYNTHETIC"
    forecast_source = {"ORIGINAL_HYBRID": "ORIGINAL_DATASET", "SYNTHETIC": "SYNTHETIC"}.get(data_source, "UPLOAD")
    attendance_source = {"ORIGINAL_HYBRID": "ORIGINAL_SIMULATED", "SYNTHETIC": "SYNTHETIC"}.get(data_source, "UPLOAD")

    students, events = StudentRepository(db), EventRepository(db)
    regs, forecasts, attendance = RegistrationRepository(db), ForecastRepository(db), AttendanceRepository(db)
    summary = ImportSummary(dataset_id=0, warnings=list(rep.warnings))
    with db.transaction():
        summary.dataset_id = datasets.register(name, kind, file_name, sha256, len(df), source_sheet,
                                               notes=settings.SIMULATED_TARGET_NOTICE if has_target else None)
        for sid, g in data.groupby("Student_ID", sort=True):
            if students.exists(sid):
                continue
            rec = {"student_id": sid, "course": _first_consistent(g, "Course", "Student", sid),
                   "data_source": data_source, "dataset_id": summary.dataset_id}
            rec.update({col: _first_consistent(g, k, "Student", sid) for k, col in _STUDENT_MAP.items()})
            students.create(**{k: v for k, v in rec.items() if v is not None})
            summary.students += 1
        for eid, g in data.groupby("Event_ID", sort=True):
            if events.exists(eid):
                continue
            rec = {"event_id": eid, "status": "Completed" if has_target else "Scheduled",
                   "data_source": data_source, "dataset_id": summary.dataset_id}
            rec.update({col: _first_consistent(g, k, "Event", eid) for k, col in _EVENT_MAP.items()})
            events.create(**{k: v for k, v in rec.items() if v is not None})
            summary.events += 1
            known = raw_forecast.loc[g.index].dropna()
            known = known[known != FORECAST_UNAVAILABLE]
            fc = known.iloc[0] if len(known.unique()) == 1 else FORECAST_UNAVAILABLE
            forecasts.add(eid, fc, None, source=forecast_source,
                          notes="Imported; time the forecast was obtained is unknown.")
            summary.forecasts += 1
        for idx, row in data.iterrows():
            if regs.find(row["Student_ID"], row["Event_ID"]):
                summary.skipped_duplicates += 1
                continue
            rid = regs.register(row["Student_ID"], row["Event_ID"], int(row["Registration_Days_Before"]),
                                int(row["Has_Exam_Near_Event"]), data_source=data_source,
                                dataset_id=summary.dataset_id)
            summary.registrations += 1
            if has_target and not pd.isna(row["Will_Attend"]):
                attendance.record(rid, int(row["Will_Attend"]), source=attendance_source,
                                  recorded_by="import", notes="SIMULATED outcome from the dataset, not observed")
                summary.attendance_records += 1
        db.audit("import", "dataset", summary.dataset_id, json.dumps(summary.__dict__, default=str))
    return summary


def import_file(db: Database, path: str | Path, kind: str = "UPLOAD", name: str | None = None) -> ImportSummary:
    """Import a CSV/Excel file. Personal data and the Raw_Merged_Data sheet are refused by the loader."""
    loaded = load_table(path)
    return import_dataset(db, loaded.df, kind, name or Path(path).stem, Path(path).name, loaded.sha256, loaded.sheet)


# ======================================================================== models
def sync_models(db: Database, models_dir: str | Path | None = None, dataset_id: int | None = None,
                activate: bool = True) -> dict:
    """Register every saved model under ``models_dir`` as a version and mark the
    one named in active_model.json as active."""
    models_dir = Path(models_dir or settings.MODELS_DIR)
    repo = ModelVersionRepository(db)
    ids = {}
    for meta_file in sorted(models_dir.glob("*/metadata.json")):
        ids[meta_file.parent.name] = repo.register_from_dir(meta_file.parent, dataset_id)
    active_file = models_dir / "active_model.json"
    if activate and active_file.exists():
        key = json.loads(active_file.read_text())["model_id"]
        if key in ids:
            repo.set_active(ids[key])
    return ids


def _predictor_for(db: Database, model_version_id: int | None):
    from core.prediction import Predictor
    repo = ModelVersionRepository(db)
    mv = repo.get(model_version_id) if model_version_id else repo.active()
    if mv is None:
        raise NotFoundError("No active model. Train and register a model first.")
    from core.db.repositories import resolve_path
    return Predictor(resolve_path(mv["artifact_path"])), mv


# ======================================================================== predictions
def registration_features(db: Database, event_id: str | None = None,
                          registration_ids: list[int] | None = None, only_active: bool = True) -> pd.DataFrame:
    sql, p = "SELECT * FROM v_registration_features WHERE 1=1", []
    if event_id:
        sql += ' AND "Event_ID" = ?'
        p.append(event_id)
    if registration_ids:
        sql += f" AND registration_id IN ({', '.join('?' * len(registration_ids))})"
        p += list(registration_ids)
    if only_active:
        sql += """ AND "Registration_Status" = 'Registered'"""
    return pd.DataFrame(db.query(sql + " ORDER BY registration_id", p))


def _rows_for_storage(frame: pd.DataFrame, out: pd.DataFrame, threshold: float) -> list[dict]:
    rows = []
    inputs = prepare_frame(frame)[MODEL_INPUTS]
    for i in range(len(out)):
        snap = {k: _clean(v) for k, v in inputs.iloc[i].to_dict().items()}
        rows.append({
            "registration_id": _clean(frame["registration_id"].iloc[i]) if "registration_id" in frame else None,
            "forecast_id": _clean(frame["forecast_id"].iloc[i]) if "forecast_id" in frame else None,
            "student_ref": _clean(frame["Student_ID"].iloc[i]) if "Student_ID" in frame else None,
            "event_ref": _clean(frame["Event_ID"].iloc[i]) if "Event_ID" in frame else None,
            "probability": float(out["Attendance_Probability"].iloc[i]),
            "predicted_attend": int(out["Predicted_Attend"].iloc[i]),
            "threshold": threshold, "certainty": out["Prediction_Certainty"].iloc[i],
            "forecast_used": out["Forecast_Used"].iloc[i], "input_snapshot": snap,
        })
    return rows


def predict_event(db: Database, event_id: str, model_version_id: int | None = None) -> dict:
    """Predict every active registration of an event and store the results."""
    ev = EventRepository(db).get(event_id)
    frame = registration_features(db, event_id=event_id)
    if frame.empty:
        raise ConstraintViolationError(f"Event '{event_id}' has no active registrations to predict.")
    predictor, mv = _predictor_for(db, model_version_id)
    out = predictor.predict(frame[["Student_ID", "Event_ID"] + MODEL_INPUTS])
    # predictor rounds to 4 dp; keep the outcome consistent with the stored probability
    out["Predicted_Attend"] = (out["Attendance_Probability"] >= predictor.threshold).astype(int)
    preds = PredictionRepository(db)
    with db.transaction():
        run_id = preds.create_run(mv["model_version_id"], "event", event_id=event_id)
        ids = preds.add_many(run_id, mv["model_version_id"], _rows_for_storage(frame, out, predictor.threshold))
    summary = predictor.bulk_summary(out)
    summary.update({"run_id": run_id, "prediction_ids": ids, "model_version_id": mv["model_version_id"],
                    "event_status": ev["status"]})
    return summary


def predict_record(db: Database, record: dict, model_version_id: int | None = None) -> dict:
    """Individual prediction for a record that may not exist in the database
    (e.g. a what-if entry). Stored in history with pseudonymous references."""
    check_no_personal_data(record, "Student_ID")
    predictor, mv = _predictor_for(db, model_version_id)
    result = predictor.predict_one(record)
    frame = pd.DataFrame([record])
    reg = None
    if record.get("Student_ID") and record.get("Event_ID"):
        reg = RegistrationRepository(db).find(record["Student_ID"], record["Event_ID"])
    out = pd.DataFrame({"Attendance_Probability": [round(result["probability"], 4)],
                        "Prediction_Certainty": [result["certainty"]], "Forecast_Used": [result["forecast_used"]]})
    out["Predicted_Attend"] = (out["Attendance_Probability"] >= predictor.threshold).astype(int)
    rows = _rows_for_storage(frame, out, predictor.threshold)
    rows[0]["registration_id"] = reg["registration_id"] if reg else None
    preds = PredictionRepository(db)
    with db.transaction():
        run_id = preds.create_run(mv["model_version_id"], "individual")
        result["prediction_id"] = preds.add_many(run_id, mv["model_version_id"], rows)[0]
    result["model_version_id"] = mv["model_version_id"]
    return result


def predict_file(db: Database, path: str | Path, model_version_id: int | None = None) -> tuple[pd.DataFrame, dict]:
    """Bulk prediction from an uploaded file; every row is stored in history."""
    loaded = load_table(path)
    predictor, mv = _predictor_for(db, model_version_id)
    out = predictor.predict(loaded.df)
    out["Predicted_Attend"] = (out["Attendance_Probability"] >= predictor.threshold).astype(int)
    frame = loaded.df.copy()
    regs = RegistrationRepository(db)
    if {"Student_ID", "Event_ID"} <= set(frame.columns):
        frame["registration_id"] = [
            (r["registration_id"] if (r := regs.find(s, e)) else None)
            for s, e in zip(frame["Student_ID"], frame["Event_ID"])]
    datasets = DatasetRepository(db)
    with db.transaction():
        existing = datasets.find("UPLOAD", loaded.sha256)
        ds_id = existing["dataset_id"] if existing else datasets.register(
            Path(path).stem, "UPLOAD", Path(path).name, loaded.sha256, len(frame), loaded.sheet,
            notes="Bulk prediction input")
        preds = PredictionRepository(db)
        run_id = preds.create_run(mv["model_version_id"], "bulk_file", input_dataset_id=ds_id)
        preds.add_many(run_id, mv["model_version_id"], _rows_for_storage(frame, out, predictor.threshold))
    summary = predictor.bulk_summary(out)
    summary.update({"run_id": run_id, "model_version_id": mv["model_version_id"], "dataset_id": ds_id})
    return out, summary


# ======================================================================== actual attendance + evaluation
def record_actual_attendance(db: Database, event_id: str, attended: dict[str, int] | None = None,
                             headcount: int | None = None, walk_ins: int = 0,
                             recorded_by: str | None = None, evaluate: bool = True) -> list[dict]:
    """Enter actual attendance after an event and store predicted-vs-actual comparisons.

    Provide either ``attended`` ({Student_ID: 0/1} for registered students) or a
    ``headcount`` of registered students who attended. Walk-ins (attended without
    registering) are stored but are not part of prediction error, because the
    model only predicts registered students.
    """
    if (attended is None) == (headcount is None):
        raise ConstraintViolationError("Provide either per-student attendance or a head count, not both.")
    events, regs, att = EventRepository(db), RegistrationRepository(db), AttendanceRepository(db)
    ev = events.get(event_id)
    active = {r["student_id"]: r for r in regs.for_event(event_id, status="Registered")}
    with db.transaction():
        if attended is not None:
            unknown = sorted(set(attended) - set(active))
            if unknown:
                raise NotFoundError(f"Not registered for {event_id}: {unknown[:5]}")
            for sid, a in attended.items():
                rid = active[sid]["registration_id"]
                old = db.query_one("SELECT attended, source FROM attendance_records WHERE registration_id = ?", (rid,))
                if old and old["source"] in ("ORIGINAL_SIMULATED", "SYNTHETIC"):
                    if int(a) != old["attended"]:
                        raise ConstraintViolationError(
                            f"{sid} has a SIMULATED outcome from the dataset; it cannot be overwritten with a "
                            "recorded value (simulated and real attendance are kept apart).")
                    continue                       # unchanged simulated value: evaluated as simulated
                if old:
                    if int(a) != old["attended"]:
                        att.correct(rid, int(a), recorded_by, "re-entered after event")
                else:
                    att.record(rid, int(a), "MANUAL", recorded_by)
            n_att = int(db.scalar("SELECT COUNT(*) FROM attendance_records a JOIN registrations r "
                                  "USING(registration_id) WHERE r.event_id = ? AND r.status = 'Registered' "
                                  "AND a.attended = 1", (event_id,)))
            att.set_outcome(event_id, "per_registration", n_att, walk_ins, recorded_by)
        else:
            if db.scalar("SELECT EXISTS(SELECT 1 FROM attendance_records a JOIN registrations r USING(registration_id) "
                         "WHERE r.event_id = ? AND a.source IN ('ORIGINAL_SIMULATED','SYNTHETIC'))", (event_id,)):
                raise ConstraintViolationError(
                    f"{event_id} has SIMULATED outcomes from the dataset; a recorded head count cannot be added "
                    "to it (simulated and real attendance are kept apart).")
            if headcount > len(active):
                raise ConstraintViolationError(
                    f"Head count {headcount} exceeds the {len(active)} active registrations; "
                    "record extra people as walk-ins.")
            att.set_outcome(event_id, "headcount", headcount, walk_ins, recorded_by)
        if ev["status"] != "Completed":
            events.set_status(event_id, "Completed")
        results = evaluate_event(db, event_id) if evaluate else []
    return results


def evaluate_event(db: Database, event_id: str, model_version_ids: list[int] | None = None) -> list[dict]:
    """Compare predictions with the recorded outcome for each model version that
    predicted this event, and store the comparison. Only predictions made before the
    outcome was recorded (and not after the event date) are used."""
    outcome = AttendanceRepository(db).outcome(event_id)
    if outcome is None:
        raise NotFoundError(f"No actual attendance recorded for '{event_id}'.")
    ev = EventRepository(db).get(event_id)
    cutoff = outcome["recorded_utc"]
    if ev["event_date"]:
        cutoff = min(cutoff, f"{ev['event_date']}T23:59:59Z")
    if model_version_ids is None:
        model_version_ids = [r["model_version_id"] for r in db.query(
            "SELECT DISTINCT p.model_version_id FROM predictions p JOIN registrations r USING(registration_id) "
            "WHERE r.event_id = ? AND p.predicted_utc <= ?", (event_id, cutoff))]
    preds_repo, eval_repo = PredictionRepository(db), EvaluationRepository(db)
    actual_rows = {a["registration_id"]: a for a in AttendanceRepository(db).for_event(event_id)}
    results = []
    for mv in model_version_ids:
        preds = preds_repo.latest_for_event(event_id, mv, cutoff)
        if not preds:
            continue
        p = np.array([x["probability"] for x in preds])
        yhat = np.array([x["predicted_attend"] for x in preds])
        expected, predicted = float(p.sum()), int(yhat.sum())
        values = {"event_id": event_id, "model_version_id": mv, "computed_utc": utc_now(),
                  "method": outcome["method"], "registrations_evaluated": len(preds),
                  "expected_attendance": round(expected, 4), "predicted_attendees": predicted}
        items: list[tuple[int, int | None]] = []
        simulated = False
        if outcome["method"] == "per_registration":
            matched = [(x, actual_rows[x["registration_id"]]) for x in preds if x["registration_id"] in actual_rows]
            if not matched:
                continue
            y = np.array([a["attended"] for _, a in matched])
            pm = np.array([x["probability"] for x, _ in matched])
            ym = np.array([x["predicted_attend"] for x, _ in matched])
            expected, predicted, actual = float(pm.sum()), int(ym.sum()), int(y.sum())
            simulated = any(a["source"] in SIMULATED_SOURCES for _, a in matched)
            tn = int(((ym == 0) & (y == 0)).sum()); fp = int(((ym == 1) & (y == 0)).sum())  # noqa: E702
            fn = int(((ym == 0) & (y == 1)).sum()); tp = int(((ym == 1) & (y == 1)).sum())  # noqa: E702
            two = len(np.unique(y)) == 2
            values.update({
                "registrations_evaluated": len(matched), "expected_attendance": round(expected, 4),
                "predicted_attendees": predicted,
                "accuracy": float((ym == y).mean()),
                "balanced_accuracy": float(balanced_accuracy_score(y, ym)) if two else None,
                "brier": float(np.mean((pm - y) ** 2)),
                "roc_auc": float(roc_auc_score(y, pm)) if two else None,
                "tn": tn, "fp": fp, "fn": fn, "tp": tp})
            items = [(x["prediction_id"], a["attendance_id"]) for x, a in matched]
        else:
            actual = int(outcome["actual_attendees"])
            items = [(x["prediction_id"], None) for x in preds]
            values.update({k: None for k in ("accuracy", "balanced_accuracy", "brier", "roc_auc",
                                             "tn", "fp", "fn", "tp")})
        err = expected - actual
        values.update({"actual_attendees": actual, "error": round(err, 4), "absolute_error": round(abs(err), 4),
                       "percentage_error": round(abs(err) / actual * 100, 2) if actual else None,
                       "count_error": predicted - actual, "target_is_simulated": int(simulated),
                       "notes": "Outcome is SIMULATED; not real-world evidence." if simulated else None})
        values["evaluation_id"] = eval_repo.add(values, items)
        results.append(values)
    return results


def model_accuracy_history(db: Database, model_version_id: int | None = None,
                           include_simulated: bool = False) -> pd.DataFrame:
    """Stored predicted-vs-actual comparisons, for analytics and model monitoring."""
    return pd.DataFrame(EvaluationRepository(db).history(model_version_id, include_simulated))


# ======================================================================== bootstrap
def bootstrap(db: Database, processed_csv: Path | None = None, models_dir: Path | None = None) -> dict:
    """First-run setup: import the processed original dataset and register models."""
    processed_csv = Path(processed_csv or settings.PROCESSED_DATASET)
    from core.data_loader import file_sha256
    df = pd.read_csv(processed_csv)
    s = import_dataset(db, df, "PROCESSED", "Processed ML_Dataset (original, simulated target)",
                       processed_csv.name, file_sha256(processed_csv), None)
    ids = sync_models(db, models_dir, dataset_id=s.dataset_id)
    return {"import": s.__dict__, "model_versions": ids}


# ======================================================================== demo mode
DEMO_FLAG = "demo_database"


def is_demo_database(db: Database) -> bool:
    """True for the separate demonstration database built from the labelled synthetic dataset."""
    return bool(SettingsRepository(db).get(DEMO_FLAG, False))


def create_demo_database(path: str | Path, synthetic_csv: Path | None = None,
                         models_dir: Path | None = None) -> dict:
    """Build (once) a SEPARATE database holding only the synthetic demonstration data.

    Demo Mode never writes synthetic rows into the user's database: it switches the app to this
    file. Every student, event and outcome in it is labelled SYNTHETIC.
    """
    from core.data_loader import file_sha256
    path = Path(path)
    synthetic_csv = Path(synthetic_csv or settings.SYNTHETIC_DATASET)
    with Database(path) as db:
        if is_demo_database(db) and db.scalar("SELECT COUNT(*) FROM students"):
            return {"created": False, "path": str(path)}
        if db.scalar("SELECT COUNT(*) FROM students") and not is_demo_database(db):
            raise ConstraintViolationError(f"{path.name} already holds non-demo data; it will not be used for "
                                           "Demo Mode.")
        df = pd.read_csv(synthetic_csv)
        if "Data_Source" not in df.columns or set(df["Data_Source"].dropna()) != {"SYNTHETIC"}:
            raise ConstraintViolationError("Demo Mode only uses the labelled synthetic dataset "
                                           "(every row must have Data_Source = SYNTHETIC).")
        summary = import_dataset(db, df, "SYNTHETIC", "Synthetic demonstration data (generated, not real students)",
                                 synthetic_csv.name, file_sha256(synthetic_csv))
        ids = sync_models(db, models_dir, dataset_id=summary.dataset_id)
        SettingsRepository(db).set(DEMO_FLAG, True)
        db.audit("create", "demo_database", None, f"{summary.registrations} synthetic registrations")
    return {"created": True, "path": str(path), "import": summary.__dict__, "model_versions": ids}
