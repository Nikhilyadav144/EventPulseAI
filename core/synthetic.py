"""Supplementary SYNTHETIC dataset generator.

Re-implements the rules documented in the original workbook's Generation_Log
(sections 5-10), because the original ``generate_ml_dataset.py`` was not
available. Every record is simulated; no real student data is read or copied.
Only *aggregate* proportions (course mix, year mix, team mix) from the
documentation are used as starting distributions.

Parameters the Generation_Log does not specify are marked ``# CHOICE`` and
listed in ``GENERATOR_CHOICES`` so they are visible in the metadata file.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from config import settings
from core.preprocessing import distance_category
from core.schema import AREA_EVENT_FIT, COURSE_TO_DEPARTMENT, EXPERIENCE_ORDER, FORECAST_UNAVAILABLE, ROLE_EVENT_FIT

GENERATOR_VERSION = "1.0"

# Aggregate mixes taken from the documented row counts (Generation_Log 4g, Data_Dictionary).
COURSE_MIX = {
    "B.Tech CSE": 225, "BBA LLB (Hons)": 28, "BA (Hons) Psychology": 24, "BCA (Media & IT)": 20,
    "B.Com (Hons)": 19, "LLB (3-year)": 12, "BBA Business Analytics": 11, "B.Tech ECE": 9,
    "BBA Digital Marketing": 9, "B.Tech Robotics & AI": 8, "B.Com (Hons) Financial Markets": 5,
    "MCA": 4, "BBA": 3, "BA (Hons) Economics": 2, "BCA": 2, "BCA (GD)": 1, "MA Psychology": 1,
}
YEAR_MIX = {1: 0.80, 2: 0.10, 3: 0.08, 4: 0.02}                           # CHOICE (≈ documented 85% Year 1)
ROLE_MIX = {"Cultural & Event Management": 163, "Did not apply": 100, "Village Attache": 50,
            "Graphics/Creativity": 38, "Social Media": 18, "Content": 11, "Multiple teams": 3}
# Area-of-interest probabilities by team (Generation_Log section 5).
AREA_ORDER = ["Community & Social Work", "Team Activities & Challenges",
              "Problem Solving & Decision Making", "Not Sure Yet", "Awareness & Communication"]
AREA_BY_ROLE = {
    "Cultural & Event Management": [0.429, 0.345, 0.109, 0.108, 0.008],
    "Village Attache": [0.372, 0.222, 0.130, 0.261, 0.015],
    "Graphics/Creativity": [0.176, 0.258, 0.440, 0.102, 0.025],
    "Social Media": [0.369, 0.332, 0.137, 0.131, 0.032],
    "Content": [0.430, 0.221, 0.159, 0.153, 0.037],
    "Multiple teams": [0.430, 0.221, 0.159, 0.153, 0.037],
    "Did not apply": [0.315, 0.253, 0.205, 0.185, 0.041],   # CHOICE: ≈ overall survey distribution
}
P_VOLUNTEER = 0.49                                           # CHOICE: ≈ documented overall share
P_PARTICIPATED = {1: 0.692, 0: 0.474}                        # Generation_Log section 5
REGISTRATIONS_PER_STUDENT = {1: 0.72, 2: 0.22, 3: 0.04, 4: 0.015, 5: 0.005}  # CHOICE: ≈ original mix

EVENT_TYPE_SHARE = {"Technical": 8, "Workshop": 7, "Cultural": 7, "Seminar": 8, "Sports": 6}
POPULARITY_BASE = {"Technical": 68, "Cultural": 66, "Sports": 62, "Workshop": 60, "Seminar": 50}
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
DAY_WEIGHTS = {                                              # CHOICE: shapes described in the log
    "Technical": [0.05, 0.25, 0.40, 0.15, 0.15, 0.00],
    "Workshop":  [0.05, 0.30, 0.20, 0.25, 0.20, 0.00],
    "Cultural":  [0.00, 0.10, 0.05, 0.10, 0.35, 0.40],
    "Seminar":   [0.15, 0.30, 0.30, 0.25, 0.00, 0.00],
    "Sports":    [0.05, 0.20, 0.25, 0.15, 0.20, 0.15],
}
TIMES = ["Morning", "Afternoon", "Evening"]
TIME_WEIGHTS = {                                             # CHOICE
    "Technical": [0.30, 0.60, 0.10], "Workshop": [0.45, 0.50, 0.05], "Cultural": [0.05, 0.25, 0.70],
    "Seminar": [0.40, 0.60, 0.00], "Sports": [0.50, 0.30, 0.20],
}
DURATIONS = {                                                # CHOICE: type ranges
    "Technical": [1.5, 2.0, 3.0], "Workshop": [2.0, 2.5, 3.0], "Cultural": [2.0, 2.5, 3.0],
    "Seminar": [1.0, 1.5, 2.0], "Sports": [1.5, 2.0],
}
LONG_FORMAT = {"Technical": 5.0, "Sports": 4.5}
REG_MEDIAN_DAYS = {"Workshop": 7, "Technical": 6, "Cultural": 5, "Sports": 5, "Seminar": 4}
DEPARTMENT_EVENT_FIT = {                                     # CHOICE: the log names the rule, not the mapping
    "Engineering": {"Technical", "Workshop"}, "Computer Applications": {"Technical", "Workshop"},
    "Commerce": {"Seminar", "Workshop"}, "Management": {"Seminar", "Workshop"},
    "Law": {"Seminar"}, "Liberal Arts": {"Cultural", "Seminar"},
}
FORECAST_UNAVAILABLE_EVENT_SHARE = 0.08                      # CHOICE: share of events with no forecast

# Target model coefficients (Generation_Log section 9).
COEF = dict(prev_rate_z=0.70, log1p_prev_attended=0.31, volunteer=0.63, participated=0.48,
            experience_num=0.24, engagement_c=0.019, popularity_z=0.94, area_match=1.25,
            role_match=0.55, reg_last_minute=-1.25, reg_normal=0.24, reg_very_early=-0.55,
            exam_near=-1.88, distance_over5=-0.15, distance_over12=-0.94, evening=-0.48,
            saturday=-0.48, rainy=-0.94, cloudy=-0.15, duration_c=-0.39, year_c=-0.13)
STUDENT_EFFECT_SD = 0.5
TARGET_MEAN_PROBABILITY = 0.68

GENERATOR_CHOICES = [
    "Year mix 80/10/8/2 %", "Volunteer share 0.49", "Area mix for 'Did not apply' = overall survey mix",
    "Registrations per student 72/22/4/1.5/0.5 %", "Day and time weights per event type",
    "Duration ranges per event type", "Department-to-event-type fit", "Engagement stretch of "
    "registration lead time = x(1 + 0.01*(score-60))", f"{FORECAST_UNAVAILABLE_EVENT_SHARE:.0%} of events "
    "have no forecast ('Unavailable')", "Popularity clipped to 20-95", "Exam-week events = 25% of events",
]


@dataclass
class SyntheticResult:
    df: pd.DataFrame
    events: pd.DataFrame
    metadata: dict


def _norm(d):
    keys = list(d)
    w = np.array([d[k] for k in keys], float)
    return keys, w / w.sum()


def _students(rng, n):
    courses, cw = _norm(COURSE_MIX)
    years, yw = _norm(YEAR_MIX)
    roles, rw = _norm(ROLE_MIX)
    s = pd.DataFrame({"Student_ID": [f"SYN-STU-{i:05d}" for i in range(1, n + 1)]})
    s["Course"] = rng.choice(courses, n, p=cw)
    s["Year"] = rng.choice(years, n, p=yw).astype(int)
    s["Role"] = rng.choice(roles, n, p=rw)
    s["Currently_UBA_Volunteer"] = (rng.random(n) < P_VOLUNTEER).astype(int)
    p_part = np.where(s["Currently_UBA_Volunteer"] == 1, P_PARTICIPATED[1], P_PARTICIPATED[0])
    s["Participated_UBA_Event_Before"] = (rng.random(n) < p_part).astype(int)
    s["Area_Of_Interest"] = [rng.choice(AREA_ORDER, p=np.array(AREA_BY_ROLE[r]) / sum(AREA_BY_ROLE[r]))
                             for r in s["Role"]]
    z = (0.9 * s["Participated_UBA_Event_Before"] + 0.7 * s["Currently_UBA_Volunteer"]
         + 0.35 * (s["Year"] - 1) + rng.normal(0, 0.9, n))
    exp_num = np.where(z < 0.45, 0, np.where(z < 1.90, 1, 2))
    s["Experience_Level"] = [EXPERIENCE_ORDER[i] for i in exp_num]
    # Distance: hidden residence group.
    grp = rng.choice(3, n, p=[0.38, 0.56, 0.06])
    near = np.minimum(0.5 + rng.gamma(2, 0.35, n), 3.0)
    day = np.clip(rng.lognormal(np.log(5.5), 0.45, n), 2, 11)
    far = rng.uniform(12, 20, n)
    s["Distance_From_Venue_KM"] = np.round(np.select([grp == 0, grp == 1], [near, day], far), 1)
    eng = (rng.normal(60, 13, n) + 3 * s["Participated_UBA_Event_Before"]
           + 2 * s["Currently_UBA_Volunteer"] + 1.5 * exp_num)
    s["Academic_Engagement_Score"] = np.clip(np.round(eng), 5, 99).astype(int)
    lam = np.exp(np.log(2) + 0.45 * (s["Year"] - 1) + 0.35 * s["Currently_UBA_Volunteer"]
                 + 0.30 * s["Participated_UBA_Event_Before"] + 0.25 * exp_num
                 + 0.012 * (s["Academic_Engagement_Score"] - 60))
    reg = rng.poisson(lam)
    very_active = rng.random(n) < 0.035
    reg = reg + np.where(very_active, rng.integers(8, 15, n), 0)
    s["Previous_Events_Registered"] = np.clip(reg, 1, 25).astype(int)
    q = 1 / (1 + np.exp(-(0.10 + 0.8 * s["Currently_UBA_Volunteer"] + 0.5 * s["Participated_UBA_Event_Before"]
                          + 0.025 * (s["Academic_Engagement_Score"] - 60) + rng.normal(0, 0.45, n))))
    att = rng.binomial(s["Previous_Events_Registered"], q)
    att = np.where((s["Participated_UBA_Event_Before"] == 1) & (att == 0), 1, att)
    s["Previous_Event_Attendance"] = att.astype(int)
    s["Previous_Attendance_Rate"] = np.round(att / s["Previous_Events_Registered"] * 100, 1)
    s["_experience_num"] = exp_num
    s["_student_effect"] = rng.normal(0, STUDENT_EFFECT_SD, n)
    return s


def _events(rng, n_events):
    types, tw = _norm(EVENT_TYPE_SHARE)
    counts = np.maximum(1, np.round(tw * n_events).astype(int))
    rows, eid = [], 1
    for t, c in zip(types, counts):
        for j in range(c):
            long_ = j == 0 and t in LONG_FORMAT
            dur = LONG_FORMAT[t] if long_ else float(rng.choice(DURATIONS[t]))
            pop = POPULARITY_BASE[t] + rng.normal(0, 12) - (12 if long_ else 0)
            rows.append(dict(Event_ID=f"SYN-EV-{eid:03d}", Event_Type=t,
                             Event_Day=rng.choice(DAYS, p=DAY_WEIGHTS[t]),
                             Event_Time=rng.choice(TIMES, p=TIME_WEIGHTS[t]),
                             Event_Duration_Hours=dur, Event_Popularity=int(np.clip(round(pop), 20, 95)),
                             _long=long_))
            eid += 1
    ev = pd.DataFrame(rows)
    n = len(ev)
    # Fixed 20:10:6 proportions, shuffled (Generation_Log section 6).
    n_cloudy, n_rainy = round(n * 10 / 36), round(n * 6 / 36)
    pool = ["Cloudy"] * n_cloudy + ["Rainy"] * n_rainy + ["Clear"] * (n - n_cloudy - n_rainy)
    ev["Weather_Forecast"] = rng.permutation(pool)
    no_fc = rng.random(n) < FORECAST_UNAVAILABLE_EVENT_SHARE
    ev.loc[no_fc, "Weather_Forecast"] = FORECAST_UNAVAILABLE
    ev["_near_exam"] = False
    ev.loc[rng.choice(n, max(1, round(0.25 * n)), replace=False), "_near_exam"] = True
    return ev


def _register(rng, students, events):
    ks, kw = _norm(REGISTRATIONS_PER_STUDENT)
    rows = []
    for _, s in students.iterrows():
        k = min(int(rng.choice(ks, p=kw)), len(events))
        dept = COURSE_TO_DEPARTMENT[s["Course"]]
        w = (events["Event_Popularity"] / 60).to_numpy(float).copy()
        w *= np.where(events["Event_Type"].isin(AREA_EVENT_FIT[s["Area_Of_Interest"]]), 1.5, 1.0)
        w *= np.where(events["Event_Type"].isin(ROLE_EVENT_FIT[s["Role"]]), 1.3, 1.0)
        w *= np.where(events["Event_Type"].isin(DEPARTMENT_EVENT_FIT[dept]), 1.25, 1.0)
        w *= np.where(events["_long"], 0.5, 1.0)
        picks = rng.choice(len(events), k, replace=False, p=w / w.sum())
        for i in picks:
            e = events.iloc[i]
            med = REG_MEDIAN_DAYS[e["Event_Type"]]
            days = rng.gamma(2.5, med / 2.167) * (1 + 0.01 * (s["Academic_Engagement_Score"] - 60))
            u = rng.random()
            if u < 0.045:
                days = rng.integers(20, 31)
            elif u < 0.095:
                days = 0
            exam = rng.random() < (0.70 if e["_near_exam"] else 0.07)
            rows.append({**s.to_dict(), **e.to_dict(),
                         "Registration_Days_Before": int(np.clip(round(days), 0, 30)),
                         "Has_Exam_Near_Event": int(exam)})
    return pd.DataFrame(rows)


def _logit_terms(df):
    reg = df["Registration_Days_Before"]
    dist = df["Distance_From_Venue_KM"]
    area = [int(e in AREA_EVENT_FIT[a]) for a, e in zip(df["Area_Of_Interest"], df["Event_Type"])]
    role = [int(e in ROLE_EVENT_FIT[r]) for r, e in zip(df["Role"], df["Event_Type"])]
    c = COEF
    return (c["prev_rate_z"] * (df["Previous_Attendance_Rate"] - 70) / 20
            + c["log1p_prev_attended"] * np.log1p(df["Previous_Event_Attendance"])
            + c["volunteer"] * df["Currently_UBA_Volunteer"] + c["participated"] * df["Participated_UBA_Event_Before"]
            + c["experience_num"] * df["_experience_num"] + c["engagement_c"] * (df["Academic_Engagement_Score"] - 60)
            + c["popularity_z"] * (df["Event_Popularity"] - 60) / 15
            + c["area_match"] * np.array(area) + c["role_match"] * np.array(role)
            + c["reg_last_minute"] * (reg <= 1) + c["reg_normal"] * ((reg >= 2) & (reg <= 14))
            + c["reg_very_early"] * (reg >= 15) + c["exam_near"] * df["Has_Exam_Near_Event"]
            + c["distance_over5"] * np.maximum(dist - 5, 0) + c["distance_over12"] * (dist > 12)
            + c["evening"] * (df["Event_Time"] == "Evening") + c["saturday"] * (df["Event_Day"] == "Saturday")
            + c["rainy"] * (df["Weather_Forecast"] == "Rainy") + c["cloudy"] * (df["Weather_Forecast"] == "Cloudy")
            + c["duration_c"] * (df["Event_Duration_Hours"] - 2) + c["year_c"] * (df["Year"] - 1)
            + df["_student_effect"])


def _bisect_intercept(eta, target=TARGET_MEAN_PROBABILITY):
    lo, hi = -10.0, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if (1 / (1 + np.exp(-(mid + eta)))).mean() < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _blank(rng, df, col, n, p=None):
    idx = rng.choice(len(df), n, replace=False, p=p)
    df.loc[df.index[idx], col] = np.nan
    return [int(i) for i in idx]


def generate(n_students: int = 600, n_events: int = 48, seed: int = settings.SYNTHETIC_SEED) -> SyntheticResult:
    if n_students < 30 or n_events < 5:
        raise ValueError("Use at least 30 students and 5 events.")
    rng = np.random.default_rng(seed)
    students = _students(rng, n_students)
    events = _events(rng, n_events)
    df = _register(rng, students, events)

    eta = _logit_terms(df)
    intercept = _bisect_intercept(eta)
    p = 1 / (1 + np.exp(-(intercept + eta)))
    df["Will_Attend"] = (rng.random(len(df)) < p).astype(int)

    # Missing values AFTER the target, mirroring Generation_Log section 10 rates.
    n = len(df)
    _blank(rng, df, "Distance_From_Venue_KM", round(0.050 * n))
    w = np.where(df["Currently_UBA_Volunteer"] == 0, 4.0, 1.0)
    _blank(rng, df, "Academic_Engagement_Score", round(0.044 * n), p=w / w.sum())
    _blank(rng, df, "Event_Popularity", round(0.029 * n))
    _blank(rng, df, "Previous_Events_Registered", round(0.039 * n))
    df.loc[df["Previous_Events_Registered"].isna(), "Previous_Attendance_Rate"] = np.nan
    df["Distance_Category"] = distance_category(df["Distance_From_Venue_KM"])
    df["Data_Source"] = "SYNTHETIC"

    cols = ["Data_Source", "Student_ID", "Event_ID", "Course", "Year", "Currently_UBA_Volunteer",
            "Participated_UBA_Event_Before", "Experience_Level", "Area_Of_Interest", "Role", "Event_Type",
            "Event_Day", "Event_Time", "Event_Duration_Hours", "Registration_Days_Before",
            "Previous_Event_Attendance", "Previous_Events_Registered", "Previous_Attendance_Rate",
            "Academic_Engagement_Score", "Event_Popularity", "Distance_From_Venue_KM", "Distance_Category",
            "Has_Exam_Near_Event", "Weather_Forecast", "Will_Attend"]
    out = df[cols].reset_index(drop=True)
    _sanity_checks(out)
    ev_out = events[[c for c in events.columns if not c.startswith("_")]].copy()
    ev_out["Near_Exam_Week"] = events["_near_exam"].astype(int)
    meta = {
        "label": "SYNTHETIC - every record is simulated. Not real college student data.",
        "generator": "core/synthetic.py", "generator_version": GENERATOR_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seed": seed, "n_students": n_students, "n_events": int(len(events)), "rows": int(len(out)),
        "intercept": round(float(intercept), 4), "mean_true_probability": round(float(p.mean()), 4),
        "class_balance": {str(k): int(v) for k, v in out["Will_Attend"].value_counts().sort_index().items()},
        "based_on": "Rules documented in the original workbook's Generation_Log, sections 5-10 "
                    "(original generate_ml_dataset.py was not available).",
        "choices_not_in_documentation": GENERATOR_CHOICES,
        "target_coefficients": COEF, "student_effect_sd": STUDENT_EFFECT_SD,
        "missing_values": {c: int(v) for c, v in out.isna().sum().items() if v > 0},
        "weather_forecast": "Event-level forecast; 'Unavailable' for events without one. "
                            "forecast_obtained_at is not simulated (null).",
    }
    return SyntheticResult(out, ev_out, meta)


def _sanity_checks(df: pd.DataFrame) -> None:
    assert not df.duplicated(["Student_ID", "Event_ID"]).any(), "duplicate registration"
    assert not df.duplicated().any(), "duplicate row"
    reg, att = df["Previous_Events_Registered"], df["Previous_Event_Attendance"]
    assert not (att > reg).any(), "attended more than registered"
    assert not (df["Event_Day"] == "Sunday").any()
    assert not ((df["Participated_UBA_Event_Before"] == 1) & (att == 0)).any()
    for key, cols in (("Student_ID", ["Course", "Year", "Role"]), ("Event_ID", ["Event_Type", "Event_Day"])):
        assert (df.groupby(key)[cols].nunique() <= 1).all().all(), f"{key} attributes vary"


def save(result: SyntheticResult, out_csv: Path | None = None, out_meta: Path | None = None) -> Path:
    settings.ensure_dirs()
    out_csv = Path(out_csv or settings.SYNTHETIC_DATASET)
    out_meta = Path(out_meta or settings.SYNTHETIC_METADATA)
    result.df.to_csv(out_csv, index=False)
    result.events.to_csv(out_csv.with_name(out_csv.stem + "_events.csv"), index=False)
    out_meta.write_text(json.dumps(result.metadata, indent=2, default=str))
    return out_csv
