"""AI intelligence features: what-if event planner, data-derived insights and model monitoring.

Rules followed throughout:
* only inputs the trained model actually uses (core.schema.MODEL_INPUTS) can be changed;
* what-if results are model outputs for hypothetical inputs, not causal effects, and are
  never stored as predictions;
* an insight is produced only when enough records support it, carries its sample size and
  says whether the outcomes are SIMULATED or RECORDED;
* drift is a signal to look closer, not proof that the model got worse.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from core.db.connection import Database
from core.preprocessing import prepare_frame
from core.schema import COLUMNS, MODEL_INPUTS

# ====================================================================== what-if
EVENT_LEVEL = ("Event_Type", "Event_Day", "Event_Time", "Event_Duration_Hours", "Event_Popularity",
               "Weather_Forecast", "Has_Exam_Near_Event")


def _check_change(col: str, value) -> object:
    if col not in EVENT_LEVEL:
        raise ValueError(f"{col} cannot be changed in the planner (event-level model inputs only: {EVENT_LEVEL}).")
    spec = COLUMNS[col]
    if spec.allowed is not None and value not in spec.allowed:
        raise ValueError(f"{col} must be one of {list(spec.allowed)}")
    if spec.min is not None:
        v = float(value)
        if not (spec.min <= v <= spec.max) or math.isnan(v):
            raise ValueError(f"{col} must be between {spec.min:g} and {spec.max:g}")
        return v
    return value


@dataclass
class Scenario:
    registrations: int
    expected_attendees: float
    expected_no_shows: float
    low: float
    high: float
    predicted_to_attend: int | None       # count at/above threshold (only for the real registrations)
    mean_probability: float


@dataclass
class WhatIfResult:
    event_id: str
    model: str
    changes: dict
    original: Scenario
    modified: Scenario
    notes: list[str] = field(default_factory=list)

    @property
    def difference(self) -> float:
        return self.modified.expected_attendees - self.original.expected_attendees


def _scenario(prob: np.ndarray, n: int, threshold: float) -> Scenario:
    k = len(prob)
    scale = n / k
    exp = float(prob.sum() * scale)
    sd = math.sqrt(float((prob * (1 - prob)).sum()) * scale)     # Poisson-binomial variance, scaled
    return Scenario(n, exp, n - exp, max(0.0, exp - 1.96 * sd), min(float(n), exp + 1.96 * sd),
                    int((prob >= threshold).sum()) if n == k else None, float(prob.mean()))


def what_if(db: Database, event_id: str, changes: dict, registrations: int | None = None,
            model_version_id: int | None = None) -> WhatIfResult:
    """Compare the event as stored with a modified version.

    ``changes`` maps event-level model inputs to new values. ``registrations`` (optional)
    asks "what if N students registered?"; the planner then ASSUMES the extra (or fewer)
    registrants resemble the current ones, i.e. it scales the current mean probability."""
    from core.db.services import _predictor_for, registration_features
    clean = {c: _check_change(c, v) for c, v in changes.items()}
    frame = registration_features(db, event_id=event_id)
    if frame.empty:
        raise ValueError(f"{event_id} has no active registrations to base a scenario on.")
    predictor, mv = _predictor_for(db, model_version_id)
    base = frame[MODEL_INPUTS].copy()
    def proba(df):
        return predictor.model.predict_proba(predictor._frame(df)[MODEL_INPUTS])[:, 1]

    p0 = proba(base)
    mod = base.copy()
    for c, v in clean.items():
        mod[c] = v
    p1 = proba(mod)
    k = len(base)
    n = int(registrations) if registrations else k
    if n < 1 or n > 5000:
        raise ValueError("Registrations must be between 1 and 5000.")
    notes = ["Model output for hypothetical inputs - an association learned from SIMULATED training labels, "
             "not a guaranteed or causal effect. What-if results are not saved as predictions."]
    if n != k:
        notes.append(f"Scaled from the {k} current registration(s): assumes the other registrants resemble them.")
    return WhatIfResult(event_id, f"{mv['name']} v{mv['version']}", clean,
                        _scenario(p0, k, predictor.threshold), _scenario(p1, n, predictor.threshold), notes)


# ====================================================================== insights
@dataclass
class Insight:
    kind: str           # event | department | timing | planning | prediction_error | data
    title: str
    detail: str
    source: str         # SIMULATED | RECORDED | n/a
    n: int
    level: str = "info"  # info | notable | investigate

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def _outcomes(db: Database) -> pd.DataFrame:
    rows = db.query(
        "SELECT f.\"Event_ID\", e.name AS \"Event_Name\", f.\"Department\", f.\"Event_Type\", f.\"Event_Day\", f.\"Event_Time\", "
        "f.\"Weather_Forecast\", f.\"Has_Exam_Near_Event\", a.attended, "
        "CASE WHEN a.source IN ('ORIGINAL_SIMULATED','SYNTHETIC') THEN 'SIMULATED' ELSE 'RECORDED' END AS src "
        "FROM v_registration_features f JOIN attendance_records a USING(registration_id) "
        "JOIN events e ON e.event_id = f.\"Event_ID\"")
    return pd.DataFrame(rows)


def insights(db: Database, min_event_n: int = 8, min_group_n: int = 20, gap_pts: float = 0.10) -> dict:
    """Observations derived from stored outcomes, split by SIMULATED vs RECORDED.

    Returns {"insights": [...], "limitations": [...], "counts": {...}}. Nothing is produced for a
    group below the minimum sample size; differences must exceed ``gap_pts`` AND the group's 95 %
    Wilson interval must exclude the rate of the rest, so small random differences are not reported."""
    df = _outcomes(db)
    out: list[Insight] = []
    limits: list[str] = []
    counts = {"SIMULATED": int((df["src"] == "SIMULATED").sum()) if not df.empty else 0,
              "RECORDED": int((df["src"] == "RECORDED").sum()) if not df.empty else 0}
    if counts["RECORDED"] == 0:
        limits.append("No attendance has been RECORDED after a real event yet. Every outcome insight below "
                      "comes from SIMULATED dataset outcomes and describes the simulation rules, not real students.")
    for src, g in (df.groupby("src") if not df.empty else []):
        if len(g) < min_event_n:
            limits.append(f"Only {len(g)} {src.lower()} outcome(s) are stored - too few for {src.lower()} "
                          f"insights (minimum {min_event_n}).")
            continue
        overall = g["attended"].mean()
        out.append(Insight("data", f"Overall attendance rate {overall:.0%} ({src.lower()})",
                           f"{int(g['attended'].sum())} of {len(g)} registrations with an outcome attended; "
                           f"plan for about {1 - overall:.0%} no-shows "
                           f"(95 % interval {1 - _wilson(int(g['attended'].sum()), len(g))[1]:.0%}–"
                           f"{1 - _wilson(int(g['attended'].sum()), len(g))[0]:.0%}).", src, len(g)))
        # events with unusually high/low attendance
        ev = g.groupby(["Event_ID", "Event_Name"], dropna=False)["attended"].agg(["sum", "count"]).reset_index()
        for _, r in ev[ev["count"] >= min_event_n].iterrows():
            lo, hi = _wilson(int(r["sum"]), int(r["count"]))
            rest = g[g["Event_ID"] != r["Event_ID"]]["attended"].mean()
            rate = r["sum"] / r["count"]
            if abs(rate - rest) >= gap_pts and (hi < rest or lo > rest):
                word = "high" if rate > rest else "low"
                out.append(Insight("event", f"{r['Event_ID']} had comparatively {word} attendance: {rate:.0%}",
                                   f"{int(r['sum'])}/{int(r['count'])} attended vs {rest:.0%} at other events "
                                   f"(95 % interval {lo:.0%}–{hi:.0%}).", src, int(r["count"]),
                                   "notable"))
        # department / timing patterns
        for dim, kind in (("Department", "department"), ("Event_Time", "timing"), ("Event_Day", "timing"),
                          ("Event_Type", "timing"), ("Weather_Forecast", "planning"),
                          ("Has_Exam_Near_Event", "planning")):
            agg = g.groupby(dim, dropna=False)["attended"].agg(["sum", "count"]).reset_index()
            for _, r in agg[agg["count"] >= min_group_n].iterrows():
                if dim == "Has_Exam_Near_Event" and r[dim] != 1:
                    continue                          # binary: report the "exam near" side only
                rest = g[g[dim] != r[dim]]["attended"]
                if len(rest) < min_group_n:
                    continue
                rate, rrate = r["sum"] / r["count"], rest.mean()
                lo, hi = _wilson(int(r["sum"]), int(r["count"]))
                if abs(rate - rrate) < gap_pts or lo <= rrate <= hi:
                    continue
                label = {"Has_Exam_Near_Event": "exam near the event" if r[dim] == 1 else "no exam nearby"}.get(
                    dim, f"{r[dim]}")
                what = {"Department": f"{label} students", "Event_Time": f"{label} events",
                        "Event_Day": f"{label} events", "Event_Type": f"{label} events",
                        "Weather_Forecast": f"events with a '{label}' forecast"}.get(dim, f"registrations with {label}")
                direction = "higher" if rate > rrate else "lower"
                detail = (f"{rate:.0%} attended ({int(r['sum'])}/{int(r['count'])}) vs {rrate:.0%} for the rest "
                          f"({len(rest)} registrations).")
                if kind == "planning" or (kind == "timing" and direction == "lower"):
                    detail += (" Planning note: when this applies, expect more no-shows and send reminders or "
                               "over-book slightly." if direction == "lower" else "")
                out.append(Insight(kind, f"{what[0].upper() + what[1:]} show {direction} attendance", detail,
                                   src, int(r["count"]), "notable"))
    # prediction errors that need investigation
    pe = pd.DataFrame(db.query(
        "SELECT pe.event_id, pe.expected_attendance, pe.actual_attendees, pe.error, pe.registrations_evaluated, "
        "pe.target_is_simulated, mv.name || ' v' || mv.version AS model FROM prediction_evaluations pe "
        "JOIN model_versions mv USING(model_version_id) WHERE pe.evaluation_id IN "
        "(SELECT MAX(evaluation_id) FROM prediction_evaluations GROUP BY event_id, model_version_id)"))
    for _, r in (pe.iterrows() if not pe.empty else []):
        n = int(r["registrations_evaluated"] or 0)
        if n < min_event_n:
            continue
        rel = abs(r["error"]) / n
        if rel >= 0.15:
            src = "SIMULATED" if r["target_is_simulated"] else "RECORDED"
            out.append(Insight("prediction_error",
                               f"{r['event_id']}: prediction {'over' if r['error'] > 0 else 'under'}-estimated by "
                               f"{abs(r['error']):.1f} attendees",
                               f"Expected {r['expected_attendance']:.1f}, actual {int(r['actual_attendees'])} of {n} "
                               f"registrations ({rel:.0%} of registrations; model {r['model']}). Check whether "
                               "something unusual happened (clash, venue change, weather).", src, n, "investigate"))
    if pe.empty:
        limits.append("No predicted-versus-actual comparisons are stored yet: predict an event before it "
                      "happens, then record attendance, to see prediction-error insights.")
    limits.append("Patterns are associations in stored data, not causes. Groups smaller than "
                  f"{min_group_n} registrations (events: {min_event_n}) are not reported.")
    order = {"investigate": 0, "notable": 1, "info": 2}
    out.sort(key=lambda i: (i.source != "RECORDED", order[i.level], -i.n))
    return {"insights": out, "limitations": limits, "counts": counts}


# ====================================================================== monitoring
NUMERIC = ["Year", "Event_Duration_Hours", "Registration_Days_Before", "Previous_Event_Attendance",
           "Previous_Events_Registered", "Previous_Attendance_Rate", "Academic_Engagement_Score",
           "Event_Popularity", "Distance_From_Venue_KM", "Has_Exam_Near_Event", "Currently_UBA_Volunteer",
           "Participated_UBA_Event_Before"]
CATEGORICAL = [c for c in MODEL_INPUTS if c not in NUMERIC]


def drift_report(train: pd.DataFrame, logged: pd.DataFrame, smd_limit: float = 0.5,
                 share_limit: float = 0.15) -> pd.DataFrame:
    """Numeric: standardised mean difference (logged - training) / training SD.
    Categorical: largest change in a category's share. Also reports missing-value rates."""
    rows = []
    for c in NUMERIC:
        t, l = pd.to_numeric(train[c], errors="coerce"), pd.to_numeric(logged[c], errors="coerce")
        sd = t.std() or 1.0
        smd = (l.mean() - t.mean()) / sd if l.notna().any() else float("nan")
        rows.append({"feature": c, "kind": "numeric", "training": f"{t.mean():.2f} ± {t.std():.2f}",
                     "incoming": f"{l.mean():.2f} ± {l.std():.2f}" if l.notna().any() else "—",
                     "score": round(float(smd), 2) if not math.isnan(smd) else None,
                     "missing_training": float(t.isna().mean()), "missing_incoming": float(l.isna().mean()),
                     "drift": bool(not math.isnan(smd) and abs(smd) > smd_limit)})
    for c in CATEGORICAL:
        t = train[c].astype(str).value_counts(normalize=True)
        l = logged[c].astype(str).value_counts(normalize=True)
        idx = t.index.union(l.index)
        diff = l.reindex(idx, fill_value=0) - t.reindex(idx, fill_value=0)
        worst = diff.abs().idxmax()
        rows.append({"feature": c, "kind": "categorical", "training": f"{worst}: {t.get(worst, 0):.0%}",
                     "incoming": f"{worst}: {l.get(worst, 0):.0%}", "score": round(float(diff[worst]), 2),
                     "missing_training": float(train[c].isna().mean()),
                     "missing_incoming": float(logged[c].isna().mean()),
                     "drift": bool(abs(diff[worst]) > share_limit)})
    return pd.DataFrame(rows)


def training_frame_for(meta: dict) -> pd.DataFrame | None:
    """The data a model was trained on, if that file is available locally."""
    from config import settings
    td = meta.get("training_data") or {}
    text = " ".join(str(v) for v in td.values()).lower()
    if "event_attendance" in text:
        p = settings.PROJECT_ROOT / "data" / "event_attendance.csv"
    elif "synthetic" in text and "processed" not in text:
        p = settings.SYNTHETIC_DATASET
    else:
        p = settings.PROCESSED_DATASET
    return prepare_frame(pd.read_csv(p)) if p.exists() else None


def incoming_inputs(db: Database, model_version_id: int | None = None, api_log=None) -> pd.DataFrame:
    """Inputs of stored desktop predictions (input_snapshot) plus, optionally, the API's prediction log."""
    sql, p = "SELECT input_snapshot, predicted_utc FROM predictions", []
    if model_version_id:
        sql += " WHERE model_version_id = ?"
        p.append(model_version_id)
    rows = [dict(json.loads(r["input_snapshot"]), _ts=r["predicted_utc"], _src="desktop") for r in db.query(sql, p)]
    if api_log is not None and api_log.exists():
        for line in api_log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                e = json.loads(line)
                rows.append(dict(e["input"], _ts=e.get("ts"), _src="api"))
    return pd.DataFrame(rows)


@dataclass
class MonitoringResult:
    n_incoming: int
    report: pd.DataFrame | None
    drifted: list[str]
    schema_violations: int
    recommendation: str
    reasons: list[str]
    accuracy: pd.DataFrame | None = None


def monitor(db: Database, model_version_id: int | None = None, api_log=None, rejected_log=None,
            min_rows: int = 30) -> MonitoringResult:
    from core.data_validation import validate
    from core.db.repositories import ModelVersionRepository
    from core.model_registry import load_metadata
    from core.db.repositories import resolve_path
    repo = ModelVersionRepository(db)
    mv = repo.get(model_version_id) if model_version_id else repo.active()
    if mv is None:
        return MonitoringResult(0, None, [], 0, "No active model.", [])
    inc = incoming_inputs(db, mv["model_version_id"], api_log)
    violations = 0
    if not inc.empty:
        rep = validate(inc[[c for c in MODEL_INPUTS if c in inc.columns]], mode="prediction")
        violations = len(rep.errors)
    if rejected_log is not None and rejected_log.exists():
        violations += sum(1 for x in rejected_log.read_text(encoding="utf-8").splitlines() if x.strip())
    reasons: list[str] = []
    report, drifted = None, []
    meta = load_metadata(resolve_path(mv["artifact_path"]))
    train = training_frame_for(meta)
    if len(inc) < min_rows:
        reasons.append(f"Only {len(inc)} prediction input(s) stored for this model; at least {min_rows} are "
                       "needed for a meaningful distribution comparison.")
    elif train is None:
        reasons.append("The model's training data file is not available on this computer, so drift cannot be "
                       "computed.")
    else:
        report = drift_report(train, prepare_frame(inc[[c for c in MODEL_INPUTS if c in inc.columns]]))
        drifted = report.loc[report["drift"], "feature"].tolist()
    acc = pd.DataFrame(db.query(
        "SELECT event_id, computed_utc, accuracy, balanced_accuracy, roc_auc, error, registrations_evaluated "
        "FROM prediction_evaluations WHERE model_version_id = ? AND target_is_simulated = 0 ORDER BY computed_utc",
        (mv["model_version_id"],)))
    recorded = int(acc["registrations_evaluated"].sum()) if not acc.empty else 0
    if len(drifted) >= 3:
        reasons.append(f"Input drift in {len(drifted)} features ({', '.join(drifted[:5])}).")
    if not acc.empty and acc["balanced_accuracy"].notna().sum() >= 3:
        ba = acc["balanced_accuracy"].dropna()
        cv = (meta.get("evaluation", {}).get("cv", {}).get("balanced_accuracy", {}) or {}).get("mean")
        if cv and ba.mean() < cv - 0.10:
            reasons.append(f"Balanced accuracy on recorded events ({ba.mean():.2f}) is more than 0.10 below the "
                           f"cross-validated estimate ({cv:.2f}).")
    if recorded >= 300:
        reasons.append(f"{recorded} RECORDED outcomes are available - enough to retrain on real attendance "
                       "instead of simulated labels.")
    retrain = any(r.startswith(("Input drift", "Balanced accuracy", f"{recorded} RECORDED")) for r in reasons)
    rec = ("Consider retraining (see reasons). Drift alone does not prove the model got worse; check recorded "
           "outcomes first." if retrain else
           "No retraining signal. Keep collecting recorded attendance; the model is trained on SIMULATED labels.")
    return MonitoringResult(len(inc), report, drifted, violations, rec, reasons, acc)
