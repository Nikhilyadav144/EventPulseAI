"""Dataset validation against the schema.

``validate(df, mode)`` never modifies its input. It returns a report with
errors (block use), warnings (usable, but the user should know) and summary
statistics. ``coerce_types`` returns a typed *copy* for downstream use.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from core.schema import (COLUMNS, EVENT_ID, ID_COLUMNS, PREDICTION_REQUIRED, STUDENT_ID,
                         TARGET, TRAINING_REQUIRED, FORECAST_UNAVAILABLE)

_BINARY_MAP = {"1": 1, "0": 0, "yes": 1, "no": 0, "true": 1, "false": 0, "y": 1, "n": 0,
               "1.0": 1, "0.0": 0}


@dataclass
class ValidationReport:
    mode: str
    n_rows: int
    n_columns: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)
    missing: dict[str, int] = field(default_factory=dict)
    duplicate_rows: int = 0
    duplicate_registrations: int = 0
    class_balance: dict[int, int] = field(default_factory=dict)
    unknown_columns: list[str] = field(default_factory=list)
    missing_columns: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        lines = [f"Validation ({self.mode}): {self.n_rows} rows x {self.n_columns} columns - "
                 + ("PASSED" if self.ok else f"FAILED ({len(self.errors)} errors)")]
        lines += [f"  ERROR: {e}" for e in self.errors]
        lines += [f"  WARNING: {w}" for w in self.warnings]
        lines += [f"  INFO: {i}" for i in self.info]
        return "\n".join(lines)


def _to_binary(s: pd.Series) -> pd.Series:
    def conv(v):
        if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NA:
            return np.nan
        key = str(v).strip().lower()
        return _BINARY_MAP.get(key, -1)
    return s.map(conv).astype(float)


def coerce_types(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with schema columns cast to their declared types.

    Values that cannot be converted become NaN for numeric columns (validation
    reports them) and -1 for binary columns (flagged as invalid).
    """
    out = df.copy()
    for name, col in COLUMNS.items():
        if name not in out.columns:
            continue
        if col.kind in ("numeric",):
            out[name] = pd.to_numeric(out[name], errors="coerce")
        elif col.kind in ("binary", "target"):
            out[name] = _to_binary(out[name])
        elif col.kind in ("categorical", "ordinal", "id"):
            s = out[name]
            out[name] = s.where(s.isna(), s.astype(str).str.strip())
    if "Weather_Forecast" in out.columns:
        out["Weather_Forecast"] = out["Weather_Forecast"].where(
            out["Weather_Forecast"].notna(), None)
    return out


def validate(df: pd.DataFrame, mode: str = "training") -> ValidationReport:
    if mode not in ("training", "prediction"):
        raise ValueError("mode must be 'training' or 'prediction'")
    rep = ValidationReport(mode=mode, n_rows=len(df), n_columns=df.shape[1])
    if df.empty:
        rep.errors.append("The dataset has no rows.")
        return rep

    required = TRAINING_REQUIRED if mode == "training" else PREDICTION_REQUIRED
    rep.missing_columns = [c for c in required if c not in df.columns]
    if rep.missing_columns:
        rep.errors.append("Missing required columns: " + ", ".join(rep.missing_columns))
    if mode == "training":
        for idc in ID_COLUMNS:
            if idc not in df.columns:
                rep.warnings.append(f"Column {idc} is absent; group-aware evaluation by {idc} is not possible.")
    rep.unknown_columns = [c for c in df.columns if c not in COLUMNS]
    if rep.unknown_columns:
        rep.info.append("Ignored columns not used by the model: " + ", ".join(rep.unknown_columns))
    if "Weather_Forecast" not in df.columns:
        rep.warnings.append(f"No Weather_Forecast column: every row is treated as '{FORECAST_UNAVAILABLE}'.")

    raw = df
    typed = coerce_types(df)
    rep.missing = {c: int(n) for c, n in raw.isna().sum().items() if n > 0}

    for name, col in COLUMNS.items():
        if name not in typed.columns:
            continue
        s = typed[name]
        n_missing = int(raw[name].isna().sum())
        if n_missing and not col.nullable and not (mode == "prediction" and col.kind == "target"):
            rep.errors.append(f"{name}: {n_missing} missing value(s) in a column that must be complete.")
        elif n_missing and name == "Weather_Forecast":
            rep.warnings.append(f"Weather_Forecast: {n_missing} row(s) without a forecast; treated as "
                                f"'{FORECAST_UNAVAILABLE}' (no weather value is guessed).")
        elif n_missing and col.kind not in ("id",):
            rep.warnings.append(f"{name}: {n_missing} missing value(s) ({n_missing / len(df):.1%}); "
                                "they will be imputed inside the model pipeline.")

        if col.kind == "numeric":
            bad = int((s.isna() & raw[name].notna()).sum())
            if bad:
                rep.errors.append(f"{name}: {bad} value(s) are not numbers.")
            vals = s.dropna()
            if col.min is not None and (vals < col.min).any():
                rep.errors.append(f"{name}: {(vals < col.min).sum()} value(s) below {col.min}.")
            if col.max is not None and (vals > col.max).any():
                rep.errors.append(f"{name}: {(vals > col.max).sum()} value(s) above {col.max}.")
            if col.integer and len(vals) and not np.allclose(vals, np.round(vals)):
                rep.warnings.append(f"{name}: non-whole numbers found in a count column.")
        elif col.kind in ("binary", "target"):
            bad = int((s == -1).sum())
            if bad:
                rep.errors.append(f"{name}: {bad} value(s) are not 0/1 (or Yes/No).")
        elif col.kind in ("categorical", "ordinal") and col.allowed:
            vals = s.dropna().astype(str)
            unknown = sorted(set(vals) - set(map(str, col.allowed)))
            if unknown:
                msg = f"{name}: unrecognised value(s) {unknown[:5]}{'...' if len(unknown) > 5 else ''}"
                if col.kind == "ordinal" and name != "Distance_Category":
                    rep.errors.append(msg + f". Allowed: {list(col.allowed)}")
                else:
                    rep.warnings.append(msg + "; the model treats unseen categories as neutral.")

    # Logical consistency
    if {"Previous_Event_Attendance", "Previous_Events_Registered"} <= set(typed.columns):
        a, r = typed["Previous_Event_Attendance"], typed["Previous_Events_Registered"]
        bad = int((a > r).sum())
        if bad:
            rep.errors.append(f"{bad} row(s) attended more previous events than they registered for.")
        if "Previous_Attendance_Rate" in typed.columns:
            calc = (a / r.replace(0, np.nan) * 100)
            diff = (calc - typed["Previous_Attendance_Rate"]).abs()
            n = int((diff > 0.15).sum())
            if n:
                rep.warnings.append(f"Previous_Attendance_Rate disagrees with attendance/registered in {n} row(s).")
    if {"Participated_UBA_Event_Before", "Previous_Event_Attendance"} <= set(typed.columns):
        n = int(((typed["Participated_UBA_Event_Before"] == 1) & (typed["Previous_Event_Attendance"] == 0)).sum())
        if n:
            rep.warnings.append(f"{n} row(s) report earlier UBA participation but zero previous attendance.")

    # Duplicates
    rep.duplicate_rows = int(raw.duplicated().sum())
    if rep.duplicate_rows:
        rep.warnings.append(f"{rep.duplicate_rows} exact duplicate row(s).")
    if {STUDENT_ID, EVENT_ID} <= set(raw.columns):
        rep.duplicate_registrations = int(raw.duplicated([STUDENT_ID, EVENT_ID]).sum())
        if rep.duplicate_registrations:
            msg = f"{rep.duplicate_registrations} repeated Student_ID + Event_ID registration(s)."
            (rep.errors if mode == "training" else rep.warnings).append(msg)
        rep.info.append(f"{raw[STUDENT_ID].nunique()} students, {raw[EVENT_ID].nunique()} events.")

    if TARGET in typed.columns and mode == "training":
        t = typed[TARGET].dropna()
        rep.class_balance = {int(k): int(v) for k, v in t.value_counts().sort_index().items() if k in (0, 1)}
        if len(rep.class_balance) < 2:
            rep.errors.append("The target has only one class; a classifier cannot be trained.")
        else:
            minority = min(rep.class_balance.values()) / sum(rep.class_balance.values())
            rep.info.append(f"Class balance: {rep.class_balance} (minority {minority:.1%}).")
            if minority < 0.2:
                rep.warnings.append("Strong class imbalance; accuracy alone will be misleading.")
        if len(df) < 50:
            rep.errors.append("At least 50 labelled rows are needed to train and evaluate a model.")
    return rep
