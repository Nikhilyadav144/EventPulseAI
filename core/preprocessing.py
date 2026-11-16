"""Preprocessing: canonical frames, documented entity-level repair, and the
sklearn transformers used inside every model pipeline.

Anything that *learns* from data (medians, category lists, scalers) lives in a
scikit-learn Pipeline and is therefore fitted on training rows only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler

from core.data_validation import coerce_types
from core.schema import (AREA_EVENT_FIT, COURSE_TO_DEPARTMENT, DISTANCE_BINS, DISTANCE_LABELS,
                         EVENT_ID, EVENT_LEVEL, EXPERIENCE_ORDER, FORECAST_UNAVAILABLE, MODEL_INPUTS,
                         ROLE_EVENT_FIT, STUDENT_ID, STUDENT_LEVEL)


# ---------------------------------------------------------------------------
# Canonical frame
# ---------------------------------------------------------------------------
def prepare_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Typed copy with every model input present.

    * Weather_Forecast blank/absent -> 'Unavailable' (an explicit category, not a guess).
    * Previous_Attendance_Rate recomputed where it is blank but computable.
    * Input columns absent from the file are added as NaN (imputed by the pipeline),
      except Weather_Forecast which becomes 'Unavailable'.
    """
    out = coerce_types(df)
    if "Weather_Forecast" not in out.columns:
        out["Weather_Forecast"] = FORECAST_UNAVAILABLE
    out["Weather_Forecast"] = out["Weather_Forecast"].fillna(FORECAST_UNAVAILABLE)
    if {"Previous_Event_Attendance", "Previous_Events_Registered"} <= set(out.columns):
        calc = (out["Previous_Event_Attendance"]
                / out["Previous_Events_Registered"].replace(0, np.nan) * 100).round(1)
        if "Previous_Attendance_Rate" not in out.columns:
            out["Previous_Attendance_Rate"] = calc
        else:
            out["Previous_Attendance_Rate"] = out["Previous_Attendance_Rate"].fillna(calc)
    for c in MODEL_INPUTS:
        if c not in out.columns:
            out[c] = np.nan
    # Whole-number columns back to integers when complete (keeps CSV exports tidy).
    for c in ("Year", "Currently_UBA_Volunteer", "Participated_UBA_Event_Before",
              "Has_Exam_Near_Event", "Registration_Days_Before", "Previous_Event_Attendance", "Will_Attend"):
        if c in out.columns and out[c].notna().all() and (out[c] >= 0).all():
            out[c] = out[c].astype(int)
    return out


# ---------------------------------------------------------------------------
# Documented entity-level repair (used to build the processed dataset)
# ---------------------------------------------------------------------------
@dataclass
class RepairLog:
    filled: dict[str, int] = field(default_factory=dict)
    remaining_missing: dict[str, int] = field(default_factory=dict)
    distance_category_rebuilt: int = 0


def consolidate_entities(df: pd.DataFrame) -> tuple[pd.DataFrame, RepairLog]:
    """Fill a blank only from the *same* student's or *same* event's other rows.

    Student- and event-level attributes are constant for an entity (verified in
    the Phase 1 audit), so a value recorded on another row of that entity is the
    true value, not an estimate. The target is never used. Blanks with no
    same-entity source are left blank for the pipeline imputer.
    """
    out = df.copy()
    log = RepairLog()
    for cols, key in ((STUDENT_LEVEL, STUDENT_ID), (EVENT_LEVEL, EVENT_ID)):
        if key not in out.columns:
            continue
        for c in cols:
            if c not in out.columns or c == "Distance_Category":
                continue
            if c == "Weather_Forecast":
                missing = out[c].isna() | (out[c] == FORECAST_UNAVAILABLE)
                known = out[c].where(~missing)
            else:
                missing = out[c].isna()
                known = out[c]
            if not missing.any():
                continue
            per_entity = known.groupby(out[key]).agg(
                lambda s: s.dropna().iloc[0] if s.dropna().nunique() == 1 else np.nan)
            fill = out[key].map(per_entity)
            can = missing & fill.notna()
            if can.any():
                out.loc[can, c] = fill[can]
                log.filled[c] = int(can.sum())
    # Previous_Attendance_Rate depends on Previous_Events_Registered.
    if {"Previous_Event_Attendance", "Previous_Events_Registered", "Previous_Attendance_Rate"} <= set(out.columns):
        m = out["Previous_Attendance_Rate"].isna() & out["Previous_Events_Registered"].notna()
        if m.any():
            out.loc[m, "Previous_Attendance_Rate"] = (
                out.loc[m, "Previous_Event_Attendance"] / out.loc[m, "Previous_Events_Registered"] * 100).round(1)
            log.filled["Previous_Attendance_Rate"] = log.filled.get("Previous_Attendance_Rate", 0) + int(m.sum())
    if "Distance_From_Venue_KM" in out.columns:
        before = out.get("Distance_Category")
        out["Distance_Category"] = distance_category(out["Distance_From_Venue_KM"])
        if before is not None:
            log.distance_category_rebuilt = int((before.isna() & out["Distance_Category"].notna()).sum())
    log.remaining_missing = {c: int(n) for c, n in out.isna().sum().items() if n > 0}
    return out, log


def distance_category(km: pd.Series) -> pd.Series:
    cat = pd.cut(km, bins=DISTANCE_BINS, labels=DISTANCE_LABELS, include_lowest=True)
    return cat.astype(object).where(cat.notna(), None)


# ---------------------------------------------------------------------------
# Feature engineering transformer (stateless; safe inside CV)
# ---------------------------------------------------------------------------
def _registration_window(days: pd.Series) -> pd.Series:
    out = pd.Series("2-14 days", index=days.index, dtype=object)
    out[days <= 1] = "0-1 days"
    out[days >= 15] = "15+ days"
    out[days.isna()] = None
    return out


class FeatureEngineer(BaseEstimator, TransformerMixin):
    """Adds documented, pre-event features. Holds no learned state."""

    def fit(self, X, y=None):
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        X = X.copy()
        X["Department"] = X["Course"].map(COURSE_TO_DEPARTMENT).fillna("Other")
        X["Interest_Match"] = [int(e in AREA_EVENT_FIT.get(a, set()))
                               for a, e in zip(X["Area_Of_Interest"], X["Event_Type"])]
        X["Role_Match"] = [int(e in ROLE_EVENT_FIT.get(r, set()))
                           for r, e in zip(X["Role"], X["Event_Type"])]
        X["Registration_Window"] = _registration_window(X["Registration_Days_Before"])
        for c in ("Registration_Days_Before", "Previous_Event_Attendance", "Previous_Events_Registered"):
            X[f"Log_{c}"] = np.log1p(X[c].astype(float))
        X["Weather_Forecast"] = X["Weather_Forecast"].fillna(FORECAST_UNAVAILABLE)
        return X


ENGINEERED_NUMERIC = [
    "Year", "Currently_UBA_Volunteer", "Participated_UBA_Event_Before", "Event_Duration_Hours",
    "Log_Registration_Days_Before", "Log_Previous_Event_Attendance", "Log_Previous_Events_Registered",
    "Previous_Attendance_Rate", "Academic_Engagement_Score", "Event_Popularity",
    "Distance_From_Venue_KM", "Has_Exam_Near_Event", "Interest_Match", "Role_Match",
]
ENGINEERED_ORDINAL = ["Experience_Level"]
ENGINEERED_CATEGORICAL = [
    "Department", "Area_Of_Interest", "Role", "Event_Type", "Event_Day", "Event_Time",
    "Weather_Forecast", "Registration_Window",
]


def build_eventpulse_preprocessor(scale: bool = True) -> Pipeline:
    numeric_steps = [("impute", SimpleImputer(strategy="median", add_indicator=False))]
    if scale:
        numeric_steps.append(("scale", StandardScaler()))
    ct = ColumnTransformer([
        ("num", Pipeline(numeric_steps), ENGINEERED_NUMERIC),
        ("ord", Pipeline([("impute", SimpleImputer(strategy="most_frequent")),
                          ("encode", OrdinalEncoder(categories=[EXPERIENCE_ORDER],
                                                    handle_unknown="use_encoded_value",
                                                    unknown_value=np.nan)),
                          ("impute2", SimpleImputer(strategy="median"))]), ENGINEERED_ORDINAL),
        ("cat", Pipeline([("impute", SimpleImputer(strategy="most_frequent")),
                          ("encode", OneHotEncoder(handle_unknown="ignore", sparse_output=False))]),
         ENGINEERED_CATEGORICAL),
    ], verbose_feature_names_out=False)
    return Pipeline([("engineer", FeatureEngineer()), ("columns", ct)])


# ---------------------------------------------------------------------------
# Faithful replica of the original notebook's preprocessing (baseline)
# ---------------------------------------------------------------------------
class NotebookDistanceBinner(BaseEstimator, TransformerMixin):
    """Notebook cells 23-27: impute distance with the TRAINING median, bin it,
    drop the raw distance. Also restores 'Unavailable' forecasts to blanks so the
    notebook's most-frequent imputation applies, exactly as originally run."""

    def fit(self, X, y=None):
        self.median_ = float(pd.to_numeric(X["Distance_From_Venue_KM"], errors="coerce").median())
        return self

    def transform(self, X):
        X = X.copy()
        d = pd.to_numeric(X["Distance_From_Venue_KM"], errors="coerce").fillna(self.median_)
        X["Distance_Category"] = distance_category(d)
        X = X.drop(columns=["Distance_From_Venue_KM"])
        X["Weather"] = X["Weather_Forecast"].replace(FORECAST_UNAVAILABLE, np.nan)
        X = X.drop(columns=["Weather_Forecast"])
        return X


NOTEBOOK_NUMERIC = [
    "Year", "Currently_UBA_Volunteer", "Participated_UBA_Event_Before", "Event_Duration_Hours",
    "Registration_Days_Before", "Previous_Event_Attendance", "Previous_Events_Registered",
    "Previous_Attendance_Rate", "Academic_Engagement_Score", "Event_Popularity", "Has_Exam_Near_Event",
]
NOTEBOOK_CATEGORICAL = [
    "Course", "Experience_Level", "Area_Of_Interest", "Role", "Event_Type", "Event_Day",
    "Event_Time", "Weather", "Distance_Category",
]


def build_notebook_preprocessor() -> Pipeline:
    ct = ColumnTransformer([
        ("numeric", Pipeline([("imputer", SimpleImputer(strategy="median")),
                              ("scaler", StandardScaler())]), NOTEBOOK_NUMERIC),
        ("categorical", Pipeline([("imputer", SimpleImputer(strategy="most_frequent")),
                                  ("encoder", OneHotEncoder(handle_unknown="ignore"))]),
         NOTEBOOK_CATEGORICAL),
    ])
    return Pipeline([("distance", NotebookDistanceBinner()), ("columns", ct)])
