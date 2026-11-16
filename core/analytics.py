"""Analytics and evaluation queries used by the dashboard, analytics pages and reports.

Every number comes from the database, the saved model metadata or a loaded
dataset - nothing is hard-coded. Functions return plain pandas objects / dicts so
they can be tested without a GUI and reused by the report generator.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from config import settings
from core.db.connection import Database
from core.db.repositories import ModelVersionRepository, resolve_path
from core.schema import ID_COLUMNS, TARGET

SIMULATED_NOTE = ("Attendance outcomes in the imported dataset are SIMULATED; rates below describe the "
                  "simulation, not observed student behaviour.")


# ======================================================================== overview
@dataclass
class OverviewKPIs:
    students: int
    events: int
    registrations: int
    active_registrations: int
    upcoming_events: int
    completed_events: int
    predicted_attendees: int | None
    predicted_non_attendees: int | None
    expected_attendance: float | None
    predicted_registrations: int
    model_name: str | None
    model_version: int | None
    accuracy: float | None
    roc_auc: float | None
    cv_roc_auc: float | None
    cv_roc_auc_std: float | None
    baseline_accuracy: float | None
    recorded_attendance_rate: float | None
    recorded_attendance_simulated: bool
    notes: list[str] = field(default_factory=list)


def overview_kpis(db: Database) -> OverviewKPIs:
    q = db.scalar
    active = ModelVersionRepository(db).active()
    metrics = ModelVersionRepository(db).metrics(active["model_version_id"]) if active else {}
    pred = None
    if active:
        pred = db.query_one(
            "SELECT COUNT(*) n, SUM(lp.predicted_attend) yes, SUM(lp.probability) expected "
            "FROM v_latest_predictions lp JOIN registrations r USING(registration_id) "
            "WHERE lp.model_version_id = ? AND r.status = 'Registered'", (active["model_version_id"],))
    n_pred = int(pred["n"]) if pred and pred["n"] else 0
    att = db.query_one("SELECT COUNT(*) n, SUM(attended) a, SUM(CASE WHEN source IN ('ORIGINAL_SIMULATED','SYNTHETIC') THEN 1 ELSE 0 END) s "
                       "FROM attendance_records")
    get = lambda s, m, k="value": metrics.get(s, {}).get(m, {}).get(k)  # noqa: E731
    k = OverviewKPIs(
        students=int(q("SELECT COUNT(*) FROM students")),
        events=int(q("SELECT COUNT(*) FROM events")),
        registrations=int(q("SELECT COUNT(*) FROM registrations")),
        active_registrations=int(q("SELECT COUNT(*) FROM registrations WHERE status = 'Registered'")),
        upcoming_events=int(q("SELECT COUNT(*) FROM events WHERE status = 'Scheduled'")),
        completed_events=int(q("SELECT COUNT(*) FROM events WHERE status = 'Completed'")),
        predicted_attendees=int(pred["yes"] or 0) if n_pred else None,
        predicted_non_attendees=(n_pred - int(pred["yes"] or 0)) if n_pred else None,
        expected_attendance=round(float(pred["expected"]), 1) if n_pred else None,
        predicted_registrations=n_pred,
        model_name=active["name"] if active else None,
        model_version=active["version"] if active else None,
        accuracy=get("holdout", "accuracy"), roc_auc=get("holdout", "roc_auc"),
        cv_roc_auc=get("cv", "roc_auc"), cv_roc_auc_std=get("cv", "roc_auc", "std"),
        baseline_accuracy=get("majority_baseline", "accuracy"),
        recorded_attendance_rate=(att["a"] / att["n"]) if att["n"] else None,
        recorded_attendance_simulated=bool(att["s"]),
    )
    if not active:
        k.notes.append("No active model - train or register one in the Model Laboratory.")
    if not n_pred:
        k.notes.append("No predictions stored yet - run predictions on the Attendance Prediction page.")
    if k.recorded_attendance_simulated:
        k.notes.append(SIMULATED_NOTE)
    return k


def recent_predictions(db: Database, limit: int = 15) -> pd.DataFrame:
    rows = db.query(
        "SELECT p.prediction_id, p.predicted_utc, COALESCE(r.student_id, p.student_ref) AS student_id, "
        "COALESCE(r.event_id, p.event_ref) AS event_id, ROUND(CAST(p.probability AS NUMERIC), 3) AS probability, "
        "CASE p.predicted_attend WHEN 1 THEN 'Will attend' ELSE 'Will not attend' END AS outcome, "
        "p.certainty, p.forecast_used, mv.name || ' v' || mv.version AS model, pr.run_type "
        "FROM predictions p JOIN model_versions mv USING(model_version_id) JOIN prediction_runs pr USING(run_id) "
        "LEFT JOIN registrations r USING(registration_id) "
        "ORDER BY p.predicted_utc DESC, p.prediction_id DESC LIMIT ?", (limit,))
    return pd.DataFrame(rows, columns=["prediction_id", "predicted_utc", "student_id", "event_id", "probability",
                                       "outcome", "certainty", "forecast_used", "model", "run_type"])


# ======================================================================== participation
def _registration_frame(db: Database) -> pd.DataFrame:
    rows = db.query(
        "SELECT f.registration_id, f.\"Student_ID\", f.\"Event_ID\", f.\"Department\", f.\"Course\", f.\"Year\", f.\"Event_Type\", "
        "f.\"Event_Day\", f.\"Event_Time\", f.\"Weather_Forecast\", f.\"Registration_Days_Before\", f.\"Has_Exam_Near_Event\", "
        "f.\"Event_Status\", f.\"Data_Source\", r.status AS registration_status, a.attended, a.source AS outcome_source "
        "FROM v_registration_features f JOIN registrations r USING(registration_id) "
        "LEFT JOIN attendance_records a USING(registration_id)")
    return pd.DataFrame(rows)


def participation_by(db: Database, dimension: str, data_source: str | None = None) -> pd.DataFrame:
    """Registrations, recorded outcomes and attendance rate grouped by one dimension
    (Department, Year, Event_Type, Event_Day, Event_Time, Weather_Forecast, Course).
    ``data_source`` optionally keeps only registrations from one source
    (ORIGINAL_HYBRID, SYNTHETIC, MANUAL, UPLOAD)."""
    allowed = {"Department", "Year", "Event_Type", "Event_Day", "Event_Time", "Weather_Forecast", "Course"}
    if dimension not in allowed:
        raise ValueError(f"dimension must be one of {sorted(allowed)}")
    df = _registration_frame(db)
    if data_source and not df.empty:
        df = df[df["Data_Source"] == data_source]
    if df.empty:
        return pd.DataFrame(columns=[dimension, "registrations", "outcomes", "attended", "attendance_rate"])
    g = df.groupby(dimension, dropna=False)
    out = pd.DataFrame({
        "registrations": g.size(),
        "outcomes": g["attended"].count(),
        "attended": g["attended"].sum(min_count=1).fillna(0).astype(int),
    }).reset_index()
    out["attendance_rate"] = np.where(out["outcomes"] > 0, out["attended"] / out["outcomes"].replace(0, np.nan), np.nan)
    order = {"Event_Day": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
             "Event_Time": ["Morning", "Afternoon", "Evening"],
             "Weather_Forecast": ["Clear", "Cloudy", "Rainy", "Unavailable"]}.get(dimension)
    if order:
        out[dimension] = pd.Categorical(out[dimension], categories=[o for o in order if o in set(out[dimension])],
                                        ordered=True)
        out = out.sort_values(dimension)
    elif dimension == "Year":
        out = out.sort_values(dimension)
    else:
        out = out.sort_values("registrations", ascending=False)
    return out.reset_index(drop=True)


def attendance_distribution(db: Database) -> dict:
    r = db.query_one("SELECT COUNT(*) n, SUM(attended) a, SUM(CASE WHEN source IN ('ORIGINAL_SIMULATED','SYNTHETIC') THEN 1 ELSE 0 END) s "
                     "FROM attendance_records")
    n, a = int(r["n"] or 0), int(r["a"] or 0)
    return {"recorded": n, "attended": a, "did_not_attend": n - a,
            "rate": a / n if n else None, "simulated": bool(r["s"])}


def event_trends(db: Database) -> pd.DataFrame:
    """One row per event: registrations, recorded attendance, expected attendance from the
    active model and the latest stored prediction error. Dated events are ordered by date."""
    ev = pd.DataFrame(db.query("SELECT * FROM v_event_participation"))
    if ev.empty:
        return ev
    errs = pd.DataFrame(db.query(
        "SELECT pe.event_id, pe.error, pe.percentage_error, pe.target_is_simulated FROM prediction_evaluations pe "
        "WHERE pe.evaluation_id = (SELECT MAX(evaluation_id) FROM prediction_evaluations p2 "
        "WHERE p2.event_id = pe.event_id)"))
    if not errs.empty:
        ev = ev.merge(errs, on="event_id", how="left")
    ev["attendance_rate"] = np.where(ev["recorded_outcomes"] > 0,
                                     ev["recorded_attended"] / ev["recorded_outcomes"].replace(0, np.nan), np.nan)
    ev["_order"] = ev["event_date"].fillna("9999") + ev["event_id"]
    return ev.sort_values("_order").drop(columns="_order").reset_index(drop=True)


def predicted_vs_actual(db: Database, include_simulated: bool = True) -> pd.DataFrame:
    """Stored post-event comparisons (latest evaluation per event and model version)."""
    rows = db.query(
        "SELECT pe.*, mv.name || ' v' || mv.version AS model, e.event_type, e.event_date FROM prediction_evaluations pe "
        "JOIN model_versions mv USING(model_version_id) JOIN events e USING(event_id) "
        "WHERE pe.evaluation_id IN (SELECT MAX(evaluation_id) FROM prediction_evaluations "
        "GROUP BY event_id, model_version_id)" + ("" if include_simulated else " AND pe.target_is_simulated = 0") +
        " ORDER BY pe.computed_utc")
    return pd.DataFrame(rows)


def prediction_error_summary(db: Database, include_simulated: bool = False) -> dict:
    df = predicted_vs_actual(db, include_simulated)
    if df.empty:
        return {"events": 0}
    return {"events": int(len(df)),
            "mean_error": float(df["error"].mean()),
            "mean_absolute_error": float(df["absolute_error"].mean()),
            "mean_percentage_error": float(df["percentage_error"].dropna().mean()) if df["percentage_error"].notna().any() else None,
            "total_expected": float(df["expected_attendance"].sum()),
            "total_actual": int(df["actual_attendees"].sum()),
            "includes_simulated": bool(df["target_is_simulated"].any())}


def probability_distribution(db: Database, model_version_id: int | None = None, bins: int = 10) -> dict:
    mv = model_version_id or (ModelVersionRepository(db).active() or {}).get("model_version_id")
    if not mv:
        return {"counts": [0] * bins, "edges": list(np.linspace(0, 1, bins + 1)), "n": 0}
    p = np.array([r["probability"] for r in db.query(
        "SELECT probability FROM v_latest_predictions WHERE model_version_id = ?", (mv,))])
    counts, edges = np.histogram(p, bins=bins, range=(0, 1))
    return {"counts": counts.tolist(), "edges": edges.round(2).tolist(), "n": int(len(p)),
            "mean": float(p.mean()) if len(p) else None}


# ======================================================================== models
def model_comparison(db: Database) -> pd.DataFrame:
    """One row per registered model version with CV, holdout and baseline metrics."""
    repo = ModelVersionRepository(db)
    rows = []
    for mv in repo.list():
        m = repo.metrics(mv["model_version_id"])
        g = lambda s, k, f="value": m.get(s, {}).get(k, {}).get(f)  # noqa: E731
        rows.append({
            "model_version_id": mv["model_version_id"], "model": mv["name"], "version": mv["version"],
            "active": bool(mv["is_active"]), "baseline": bool(mv["is_baseline"]), "threshold": mv["threshold"],
            "cv_roc_auc": g("cv", "roc_auc"), "cv_roc_auc_std": g("cv", "roc_auc", "std"),
            "cv_accuracy": g("cv", "accuracy"), "cv_f1": g("cv", "f1"),
            "holdout_roc_auc": g("holdout", "roc_auc"), "accuracy": g("holdout", "accuracy"),
            "balanced_accuracy": g("holdout", "balanced_accuracy"), "precision": g("holdout", "precision"),
            "recall": g("holdout", "recall"), "f1": g("holdout", "f1"),
            "recall_non_attenders": g("holdout", "recall_non_attenders"), "brier": g("holdout", "brier"),
            "split_auc_mean": g("split_sensitivity", "roc_auc_mean"),
            "split_auc_min": g("split_sensitivity", "roc_auc_min"),
            "split_auc_max": g("split_sensitivity", "roc_auc_max"),
            "majority_baseline_accuracy": g("majority_baseline", "accuracy"),
            "tn": g("holdout", "tn"), "fp": g("holdout", "fp"), "fn": g("holdout", "fn"), "tp": g("holdout", "tp"),
            "trained_utc": mv["trained_utc"], "sklearn": mv["sklearn_version"],
        })
    df = pd.DataFrame(rows)
    return df.sort_values(["baseline", "cv_roc_auc"], ascending=[True, False]).reset_index(drop=True) if len(df) else df


def model_details(db: Database, model_version_id: int) -> dict:
    """Metadata, ROC curve, confusion matrix, CV folds and feature importance for one version,
    read from the saved model directory (comparison.json holds the ROC points)."""
    mv = ModelVersionRepository(db).get(model_version_id)
    d = resolve_path(mv["artifact_path"])
    meta = json.loads((d / "metadata.json").read_text())
    comp_file = d.parent / "comparison.json"
    roc = None
    if comp_file.exists():
        comp = json.loads(comp_file.read_text())
        for m in comp.get("models", []):
            if m["model_id"] == mv["model_key"]:
                roc = m.get("roc_curve")
    roc = roc or meta.get("roc_curve") or _recompute_roc(d, meta)
    ev = meta.get("evaluation", {})
    cm = ev.get("holdout", {}).get("confusion_matrix")
    return {"version": mv, "metadata": meta, "roc_curve": roc, "confusion_matrix": cm,
            "cv": ev.get("cv", {}), "holdout": ev.get("holdout", {}), "baseline": ev.get("majority_baseline", {}),
            "split_sensitivity": ev.get("split_sensitivity"), "holdout_note": ev.get("holdout_note"),
            "importance": meta.get("permutation_importance", [])}


def _recompute_roc(model_dir: Path, meta: dict) -> dict | None:
    """For models saved before ROC points were stored: rebuild the curve on the SAME holdout
    students recorded at training time (comparison.json), using the processed dataset."""
    from core.evaluation import roc_points
    from core.prediction import Predictor
    from core.preprocessing import prepare_frame
    from core.schema import MODEL_INPUTS, STUDENT_ID
    comp_file = model_dir.parent / "comparison.json"
    ids = meta.get("holdout_student_ids")
    if not ids and comp_file.exists():
        ids = json.loads(comp_file.read_text()).get("split", {}).get("holdout_student_ids")
    src = settings.PROCESSED_DIR / (meta.get("training_data", {}).get("file") or "")
    if not ids or not src.is_file():
        return None
    df = prepare_frame(pd.read_csv(src))
    hold = df[df[STUDENT_ID].isin(ids)]
    if hold.empty or hold[TARGET].nunique() < 2:
        return None
    model = Predictor(model_dir).model
    return roc_points(hold[TARGET].astype(int), model.predict_proba(hold[MODEL_INPUTS])[:, 1])


def feature_importance(db: Database, model_version_id: int | None = None, top: int = 12) -> pd.DataFrame:
    mv = model_version_id or (ModelVersionRepository(db).active() or {}).get("model_version_id")
    if not mv:
        return pd.DataFrame(columns=["feature", "mean", "std"])
    imp = model_details(db, mv)["importance"]
    return pd.DataFrame(imp[:top], columns=["feature", "mean", "std"])


# ======================================================================== dataset profiling
@dataclass
class DatasetProfile:
    rows: int
    columns: int
    dtypes: pd.DataFrame
    missing: pd.DataFrame
    duplicate_rows: int
    duplicate_registrations: int | None
    describe: pd.DataFrame
    class_balance: dict | None
    categorical_counts: dict[str, pd.Series]


def profile_dataset(df: pd.DataFrame) -> DatasetProfile:
    dtypes = pd.DataFrame({"column": df.columns, "dtype": [str(t) for t in df.dtypes],
                           "non_null": df.notna().sum().values, "unique": df.nunique().values})
    miss = df.isna().sum()
    missing = pd.DataFrame({"column": miss.index, "missing": miss.values,
                            "missing_pct": (miss.values / max(len(df), 1) * 100).round(2)})
    missing = missing[missing["missing"] > 0].sort_values("missing", ascending=False).reset_index(drop=True)
    dup_reg = int(df.duplicated(list(ID_COLUMNS)).sum()) if set(ID_COLUMNS) <= set(df.columns) else None
    num = df.select_dtypes("number")
    describe = num.describe().T.round(3).reset_index().rename(columns={"index": "column"}) if not num.empty \
        else pd.DataFrame()
    cb = None
    if TARGET in df.columns:
        vc = pd.to_numeric(df[TARGET], errors="coerce").value_counts().sort_index()
        cb = {int(k): int(v) for k, v in vc.items()}
    cats = {c: df[c].value_counts(dropna=False).head(20) for c in df.columns
            if (df[c].dtype == object or pd.api.types.is_string_dtype(df[c])) and c not in ID_COLUMNS}
    return DatasetProfile(len(df), df.shape[1], dtypes, missing, int(df.duplicated().sum()), dup_reg,
                          describe, cb, cats)


def clean_dataset(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Transparent cleaning for export: returns a NEW frame plus a log of every change.
    Never modifies the input. Imputation for modelling still happens inside the pipeline."""
    from core.preprocessing import consolidate_entities, prepare_frame
    log = []
    out = df.copy()
    n0 = len(out)
    out = out.drop_duplicates()
    if len(out) < n0:
        log.append(f"Removed {n0 - len(out)} exact duplicate row(s).")
    if set(ID_COLUMNS) <= set(out.columns):
        d = out.duplicated(list(ID_COLUMNS), keep="first")
        if d.any():
            log.append(f"Removed {int(d.sum())} repeated Student_ID + Event_ID registration(s) (kept the first).")
            out = out[~d]
    typed = prepare_frame(out)
    if "Weather_Forecast" in df.columns or "Weather" in df.columns:
        log.append("Blank weather forecasts set to the explicit category 'Unavailable'.")
    repaired, rlog = consolidate_entities(typed)
    for c, n in rlog.filled.items():
        log.append(f"{c}: filled {n} blank(s) from the same student's/event's other rows.")
    for c, n in rlog.remaining_missing.items():
        log.append(f"{c}: {n} blank(s) left for in-pipeline imputation (no same-entity source).")
    keep = [c for c in df.columns if c in repaired.columns] + \
           [c for c in repaired.columns if c not in df.columns and c != "Weather"]
    return repaired[keep].reset_index(drop=True), log


def load_reference_dataset(kind: str = "processed") -> pd.DataFrame:
    path = {"processed": settings.PROCESSED_DATASET, "synthetic": settings.SYNTHETIC_DATASET}[kind]
    return pd.read_csv(path)


# ======================================================================== data provenance & activity
def data_source_summary(db: Database) -> dict:
    """Counts by provenance so the UI can always tell original, synthetic and app-entered data apart,
    and simulated outcomes apart from recorded (observed) attendance."""
    def by(table: str, col: str = "data_source") -> dict:
        return {r["k"]: int(r["n"]) for r in db.query(f"SELECT {col} AS k, COUNT(*) AS n FROM {table} GROUP BY {col}")}
    att = by("attendance_records", "source")
    simulated = sum(n for k, n in att.items() if k in ("ORIGINAL_SIMULATED", "SYNTHETIC"))
    observed = sum(n for k, n in att.items() if k in ("MANUAL", "UPLOAD"))
    headcounts = int(db.scalar("SELECT COUNT(*) FROM event_outcomes WHERE method = 'headcount'") or 0)
    return {"students": by("students"), "events": by("events"), "registrations": by("registrations"),
            "attendance": att, "simulated_outcomes": simulated, "observed_outcomes": observed,
            "headcount_events": headcounts,
            "future_events": int(db.scalar("SELECT COUNT(*) FROM events WHERE status = 'Scheduled'") or 0)}


def recent_activity(db: Database, limit: int = 8) -> pd.DataFrame:
    """Latest stored actions: prediction runs, post-event evaluations and audited changes."""
    rows: list[dict] = []
    for r in db.query(
            "SELECT pr.created_utc AS ts, pr.run_type, pr.event_id, pr.n_predictions, mv.name || ' v' || mv.version "
            "AS model FROM prediction_runs pr JOIN model_versions mv USING(model_version_id) "
            "ORDER BY pr.run_id DESC LIMIT ?", (limit,)):
        what = {"event": f"Predicted {r['n_predictions']} registrations for {r['event_id']}",
                "individual": f"Individual prediction{' for ' + r['event_id'] if r['event_id'] else ''}",
                "bulk_file": f"Bulk prediction of {r['n_predictions']} rows"}.get(r["run_type"], r["run_type"])
        rows.append({"when_utc": r["ts"], "activity": what, "detail": r["model"], "kind": "prediction"})
    for r in db.query(
            "SELECT pe.computed_utc AS ts, pe.event_id, pe.expected_attendance, pe.actual_attendees, "
            "pe.target_is_simulated FROM prediction_evaluations pe ORDER BY pe.evaluation_id DESC LIMIT ?", (limit,)):
        rows.append({"when_utc": r["ts"],
                     "activity": f"Compared {r['event_id']}: {r['expected_attendance']:.1f} expected vs "
                                 f"{r['actual_attendees']} attended",
                     "detail": "simulated outcome" if r["target_is_simulated"] else "recorded attendance",
                     "kind": "evaluation"})
    labels = {("create", "event"): "Created event", ("update", "event"): "Edited event",
              ("status", "event"): "Changed event status", ("delete", "event"): "Deleted event",
              ("create", "student"): "Added student", ("import", "dataset"): "Imported dataset",
              ("activate", "model_version"): "Activated model", ("backup", "database"): "Backed up database",
              ("restore", "database"): "Restored database", ("correct", "attendance"): "Corrected attendance",
              ("create", "demo_database"): "Created demo database"}
    keys = " OR ".join("(action = ? AND entity = ?)" for _ in labels)
    params = [x for k in labels for x in k]
    for r in db.query(f"SELECT ts_utc, action, entity, entity_id, details FROM audit_log WHERE ({keys}) "
                      "AND NOT (action = 'create' AND entity IN ('event', 'student') AND entity_id IN "
                      "(SELECT event_id FROM events WHERE data_source IN ('ORIGINAL_HYBRID','SYNTHETIC') UNION ALL "
                      "SELECT student_id FROM students WHERE data_source IN ('ORIGINAL_HYBRID','SYNTHETIC'))) "
                      "ORDER BY log_id DESC LIMIT ?", (*params, limit)):
        label = labels[(r["action"], r["entity"])]
        ent = r["entity_id"] or ""
        if r["action"] == "create" and r["entity"] in ("event", "student"):
            table, key = ("events", "event_id") if r["entity"] == "event" else ("students", "student_id")
            src = db.scalar(f"SELECT data_source FROM {table} WHERE {key} = ?", (ent,))
            if src in ("ORIGINAL_HYBRID", "SYNTHETIC"):
                continue                          # bulk-imported rows are summarised by "Imported dataset"
        if r["entity"] == "model_version" and ent:
            mv = db.query_one("SELECT name, version FROM model_versions WHERE model_version_id = ?", (int(ent),))
            ent = f"{mv['name']} v{mv['version']}" if mv else ent
        detail = r["details"] if r["entity"] in ("event",) and r["action"] == "status" else ""
        if r["entity"] == "dataset":
            ent = ""
        rows.append({"when_utc": r["ts_utc"], "activity": f"{label} {ent}".strip(), "detail": detail or "",
                     "kind": r["entity"]})
    df = pd.DataFrame(rows, columns=["when_utc", "activity", "detail", "kind"])
    if df.empty:
        return df
    return df.sort_values("when_utc", ascending=False, kind="stable").head(limit).reset_index(drop=True)


# ======================================================================== MLflow results + activation checks
def mlflow_runs(db_path=None, experiment: str = "eventpulse-attendance") -> pd.DataFrame:
    """Runs of the end-term MLflow experiment, read straight from mlflow.db (SQLite) so the
    desktop app does not need the MLflow package. Empty if train.py has not been run."""
    import sqlite3
    db_path = Path(db_path or settings.PROJECT_ROOT / "mlflow.db")
    cols = ["run", "algorithm", "cv_roc_auc", "cv_accuracy", "cv_f1", "holdout_roc_auc", "holdout_accuracy",
            "holdout_f1", "midterm_roc_auc", "threshold", "start_utc", "run_id"]
    if not db_path.exists():
        return pd.DataFrame(columns=cols)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        runs = pd.read_sql_query(
            "SELECT r.run_uuid AS run_id, r.name AS run, r.start_time FROM runs r JOIN experiments e "
            "USING(experiment_id) WHERE e.name = ? AND r.lifecycle_stage = 'active' AND r.status = 'FINISHED'",
            con, params=(experiment,))
        if runs.empty:
            return pd.DataFrame(columns=cols)
        met = pd.read_sql_query("SELECT run_uuid AS run_id, key, value FROM latest_metrics", con)
        par = pd.read_sql_query("SELECT run_uuid AS run_id, key, value FROM params WHERE key IN "
                                "('algorithm', 'threshold')", con)
    except Exception:                       # not an MLflow database / older schema
        return pd.DataFrame(columns=cols)
    finally:
        con.close()
    out = runs.merge(met.pivot_table(index="run_id", columns="key", values="value", aggfunc="last").reset_index(),
                     on="run_id", how="left")
    out = out.merge(par.pivot_table(index="run_id", columns="key", values="value", aggfunc="last").reset_index(),
                    on="run_id", how="left")
    out["start_utc"] = pd.to_datetime(out["start_time"], unit="ms", utc=True).dt.strftime("%Y-%m-%d %H:%M")
    for c in cols:
        if c not in out:
            out[c] = np.nan
    return out[cols].sort_values("cv_roc_auc", ascending=False).reset_index(drop=True)


def model_support_problems(db: Database, model_version_id: int) -> list[str]:
    """Reasons a model version must NOT be activated (empty list = supported)."""
    import sklearn

    from core.db.repositories import ModelVersionRepository, _sha256, resolve_path
    from core.schema import MODEL_INPUTS
    mv = ModelVersionRepository(db).get(model_version_id)
    d = resolve_path(mv["artifact_path"])
    art = d / "model.joblib" if d.is_dir() else d
    problems = []
    if not art.exists():
        return [f"The model file is missing ({art})."]
    if _sha256(art) != mv["artifact_sha256"]:
        problems.append("The model file changed after it was registered (checksum mismatch).")
    saved = (mv.get("sklearn_version") or "?").split(".")[:2]
    if saved != sklearn.__version__.split(".")[:2]:
        problems.append(f"Saved with scikit-learn {mv.get('sklearn_version')}, this app has {sklearn.__version__}.")
    meta_file = (d if d.is_dir() else d.parent) / "metadata.json"
    if meta_file.exists():
        import json as _json
        inputs = _json.loads(meta_file.read_text()).get("inputs")
        if inputs and list(inputs) != list(MODEL_INPUTS):
            problems.append("The model expects different input features from this app's schema.")
    if not (0 < float(mv["threshold"]) < 1):
        problems.append("Invalid decision threshold.")
    return problems
