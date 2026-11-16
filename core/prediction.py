"""Individual and bulk predictions from a saved model.

Every number produced here comes from a trained pipeline. Explanations are
model-agnostic "what-if" comparisons: each input is replaced by its typical
training value and the change in predicted probability is reported.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from core.data_validation import ValidationReport, validate
from core.evaluation import attendance_interval
from core.model_registry import active_model_dir, load_model
from core.preprocessing import prepare_frame
from core.schema import FORECAST_UNAVAILABLE, MODEL_INPUTS

FRIENDLY = {
    "Course": "Course", "Year": "Year of study", "Currently_UBA_Volunteer": "Current UBA volunteer",
    "Participated_UBA_Event_Before": "Took part in a UBA event before", "Experience_Level": "Experience level",
    "Area_Of_Interest": "Area of interest", "Role": "Team applied for", "Event_Type": "Event type",
    "Event_Day": "Event day", "Event_Time": "Event time", "Event_Duration_Hours": "Event duration (h)",
    "Registration_Days_Before": "Days registered before event", "Previous_Event_Attendance": "Events attended before",
    "Previous_Events_Registered": "Events registered before", "Previous_Attendance_Rate": "Previous attendance rate (%)",
    "Academic_Engagement_Score": "Academic engagement score", "Event_Popularity": "Event popularity",
    "Distance_From_Venue_KM": "Distance from venue (km)", "Has_Exam_Near_Event": "Exam near the event",
    "Weather_Forecast": "Weather forecast",
}


class PredictionInputError(ValueError):
    def __init__(self, report: ValidationReport):
        super().__init__(report.summary())
        self.report = report


def certainty_label(prob: float, threshold: float) -> str:
    """Plain-language certainty. Deliberately conservative: the model's ranking
    ability is moderate (CV ROC-AUC ~0.75-0.80 on simulated data)."""
    gap = abs(prob - threshold)
    if gap < 0.10:
        return "Uncertain"
    if gap < 0.25:
        return "Moderate"
    return "Clear"


@dataclass
class Factor:
    feature: str
    label: str
    value: object
    typical: object
    effect: float           # change in probability vs typical value (+ raises attendance)

    def text(self) -> str:
        direction = "raises" if self.effect > 0 else "lowers"
        return (f"{self.label} = {_fmt(self.value)} {direction} the probability by "
                f"{abs(self.effect) * 100:.1f} points (typical: {_fmt(self.typical)})")


def _fmt(v):
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


class Predictor:
    def __init__(self, model_dir: str | Path | None = None):
        self.model_dir = Path(model_dir) if model_dir else active_model_dir()
        self.model, self.meta = load_model(self.model_dir)
        self.threshold = float(self.meta["threshold"])
        self.reference = self.meta["reference_values"]

    @property
    def name(self) -> str:
        return self.meta["name"]

    def _frame(self, df: pd.DataFrame) -> pd.DataFrame:
        rep = validate(df, mode="prediction")
        if not rep.ok:
            raise PredictionInputError(rep)
        self.last_report = rep
        return prepare_frame(df)

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        frame = self._frame(df)
        prob = self.model.predict_proba(frame[MODEL_INPUTS])[:, 1]
        out = df.copy()
        out["Attendance_Probability"] = np.round(prob, 4)
        out["Predicted_Attend"] = (prob >= self.threshold).astype(int)
        out["Prediction_Certainty"] = [certainty_label(p, self.threshold) for p in prob]
        out["Forecast_Used"] = frame["Weather_Forecast"].values
        out["Model"] = self.name
        return out

    def predict_one(self, record: dict, top_k: int = 6) -> dict:
        df = pd.DataFrame([record])
        frame = self._frame(df)
        prob = float(self.model.predict_proba(frame[MODEL_INPUTS])[:, 1][0])
        return {
            "probability": prob,
            "predicted_attend": int(prob >= self.threshold),
            "threshold": self.threshold,
            "certainty": certainty_label(prob, self.threshold),
            "forecast_used": frame["Weather_Forecast"].iloc[0],
            "forecast_unavailable": frame["Weather_Forecast"].iloc[0] == FORECAST_UNAVAILABLE,
            "factors": self.explain(frame, prob)[:top_k],
            "model": self.name,
            "notice": self.meta.get("target_notice", ""),
        }

    def explain(self, frame: pd.DataFrame, base_prob: float | None = None) -> list[Factor]:
        row = frame[MODEL_INPUTS].iloc[[0]]
        if base_prob is None:
            base_prob = float(self.model.predict_proba(row)[:, 1][0])
        variants, feats = [], []
        for c in MODEL_INPUTS:
            typical = self.reference.get(c)
            value = row[c].iloc[0]
            if typical is None or _same(value, typical):
                continue
            v = row.copy()
            v[c] = typical
            variants.append(v)
            feats.append((c, value, typical))
        if not variants:
            return []
        probs = self.model.predict_proba(pd.concat(variants, ignore_index=True))[:, 1]
        factors = [Factor(c, FRIENDLY.get(c, c), _py(val), _py(typ), float(base_prob - p))
                   for (c, val, typ), p in zip(feats, probs)]
        factors = [f for f in factors if abs(f.effect) >= 0.005]
        return sorted(factors, key=lambda f: abs(f.effect), reverse=True)

    def bulk_summary(self, predictions: pd.DataFrame) -> dict:
        p = predictions["Attendance_Probability"].to_numpy(float)
        mean, lo, hi = attendance_interval(p)
        summary = {
            "registrations": int(len(p)),
            "predicted_attendees": int((p >= self.threshold).sum()),
            "predicted_non_attendees": int((p < self.threshold).sum()),
            "expected_attendance": round(mean, 1),
            "expected_attendance_range_95": [round(lo, 1), round(hi, 1)],
            "mean_probability": round(float(p.mean()), 4),
            "histogram": np.histogram(p, bins=10, range=(0, 1))[0].tolist(),
            "threshold": self.threshold,
            "forecast_unavailable_rows": int((predictions.get("Forecast_Used") == FORECAST_UNAVAILABLE).sum())
            if "Forecast_Used" in predictions else 0,
            "model": self.name,
        }
        if "Event_ID" in predictions.columns:
            summary["by_event"] = (predictions.groupby("Event_ID")["Attendance_Probability"]
                                   .agg(registrations="size", expected_attendance="sum")
                                   .round(1).reset_index().to_dict("records"))
        return summary


def _same(a, b) -> bool:
    try:
        if pd.isna(a):
            return False
    except (TypeError, ValueError):
        pass
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def _py(v):
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    return v
