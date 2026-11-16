"""Single source of truth for the dataset schema.

Every column the application understands is declared here with its type,
allowed values/ranges, the level it belongs to (student / event / registration)
and whether it is known *before* the event (a requirement for any model input).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

TARGET = "Will_Attend"
STUDENT_ID = "Student_ID"
EVENT_ID = "Event_ID"
ID_COLUMNS = (STUDENT_ID, EVENT_ID)

# Value used when an event-day weather forecast is not available. It is an
# explicit category, never a guessed weather value.
FORECAST_UNAVAILABLE = "Unavailable"

# Column-name aliases accepted on import -> canonical name.
COLUMN_ALIASES = {
    "Weather": "Weather_Forecast",
    "Event_Timing": "Event_Time",
    "Event_Duration": "Event_Duration_Hours",
    "Academic_Year": "Year",
    "Venue_Distance": "Distance_From_Venue_KM",
}

# Personal-data columns that must never be imported into the application.
PII_COLUMNS = frozenset({
    "Name", "Email_Address", "Email", "Mobile_No", "Mobile", "Phone",
    "Roll_Number", "Roll_No", "Person_ID", "Timestamp",
    "Reviewer_Decision", "Reviewer_Experience_Note",
})
FORBIDDEN_SHEETS = frozenset({"Raw_Merged_Data"})

EXPERIENCE_ORDER = ["No experience", "Some experience", "Experienced"]
DISTANCE_BINS = [0, 2, 6, 12, float("inf")]
DISTANCE_LABELS = ["Near", "Moderate", "Far", "Very Far"]

COURSE_TO_DEPARTMENT = {
    "B.Com (Hons)": "Commerce",
    "B.Com (Hons) Financial Markets": "Commerce",
    "BCA": "Computer Applications",
    "BCA (GD)": "Computer Applications",
    "BCA (Media & IT)": "Computer Applications",
    "MCA": "Computer Applications",
    "B.Tech CSE": "Engineering",
    "B.Tech ECE": "Engineering",
    "B.Tech Robotics & AI": "Engineering",
    "BBA LLB (Hons)": "Law",
    "LLB (3-year)": "Law",
    "BA (Hons) Economics": "Liberal Arts",
    "BA (Hons) Psychology": "Liberal Arts",
    "MA Psychology": "Liberal Arts",
    "BBA": "Management",
    "BBA Business Analytics": "Management",
    "BBA Digital Marketing": "Management",
}
DEPARTMENTS = sorted(set(COURSE_TO_DEPARTMENT.values()))

# Interest matching documented in Generation_Log section 7.
AREA_EVENT_FIT = {
    "Community & Social Work": {"Cultural", "Seminar"},
    "Team Activities & Challenges": {"Cultural", "Sports"},
    "Problem Solving & Decision Making": {"Technical", "Workshop"},
    "Awareness & Communication": {"Seminar", "Workshop"},
    "Not Sure Yet": set(),
}
ROLE_EVENT_FIT = {
    "Cultural & Event Management": {"Cultural", "Sports"},
    "Graphics/Creativity": {"Workshop"},
    "Social Media": {"Cultural", "Workshop"},
    "Content": {"Seminar", "Workshop"},
    "Village Attache": {"Seminar"},
    "Multiple teams": set(),
    "Did not apply": set(),
}


@dataclass(frozen=True)
class Column:
    name: str
    kind: str                       # "id" | "numeric" | "binary" | "categorical" | "ordinal" | "target"
    level: str                      # "student" | "event" | "registration"
    pre_event: bool                 # known before the event takes place?
    required: bool = True           # must be present in an uploaded file
    allowed: Optional[tuple] = None  # allowed categories
    min: Optional[float] = None
    max: Optional[float] = None
    integer: bool = False
    nullable: bool = True
    description: str = ""


COLUMNS: dict[str, Column] = {c.name: c for c in [
    Column(STUDENT_ID, "id", "student", True, nullable=False, description="Pseudonymous student code"),
    Column(EVENT_ID, "id", "event", True, nullable=False, description="Event code"),
    Column("Course", "categorical", "student", True, allowed=tuple(COURSE_TO_DEPARTMENT), nullable=False,
           description="Degree programme"),
    Column("Year", "numeric", "student", True, min=1, max=5, integer=True, nullable=False,
           description="Year of study"),
    Column("Currently_UBA_Volunteer", "binary", "student", True, allowed=(0, 1), nullable=False),
    Column("Participated_UBA_Event_Before", "binary", "student", True, allowed=(0, 1), nullable=False),
    Column("Experience_Level", "ordinal", "student", True, allowed=tuple(EXPERIENCE_ORDER), nullable=False),
    Column("Area_Of_Interest", "categorical", "student", True, allowed=tuple(AREA_EVENT_FIT), nullable=False),
    Column("Role", "categorical", "student", True, allowed=tuple(ROLE_EVENT_FIT), nullable=False),
    Column("Event_Type", "categorical", "event", True,
           allowed=("Technical", "Workshop", "Cultural", "Seminar", "Sports"), nullable=False),
    Column("Event_Day", "categorical", "event", True,
           allowed=("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"), nullable=False),
    Column("Event_Time", "categorical", "event", True, allowed=("Morning", "Afternoon", "Evening"), nullable=False),
    Column("Event_Duration_Hours", "numeric", "event", True, min=0.5, max=12, nullable=False),
    Column("Registration_Days_Before", "numeric", "registration", True, min=0, max=365, integer=True,
           nullable=False),
    Column("Previous_Event_Attendance", "numeric", "student", True, min=0, max=500, integer=True,
           nullable=False),
    Column("Previous_Events_Registered", "numeric", "student", True, min=0, max=500, integer=True),
    Column("Previous_Attendance_Rate", "numeric", "student", True, min=0, max=100, required=False,
           description="Derived: attendance / registered x 100; recomputed when absent"),
    Column("Academic_Engagement_Score", "numeric", "student", True, min=0, max=100),
    Column("Event_Popularity", "numeric", "event", True, min=0, max=100,
           description="Pre-event interest score"),
    Column("Distance_From_Venue_KM", "numeric", "student", True, min=0, max=200),
    Column("Distance_Category", "ordinal", "student", True, allowed=tuple(DISTANCE_LABELS), required=False,
           description="Derived from distance; not used as a model input (duplicate information)"),
    Column("Has_Exam_Near_Event", "binary", "registration", True, allowed=(0, 1), nullable=False),
    Column("Weather_Forecast", "categorical", "event", True,
           allowed=("Clear", "Cloudy", "Rainy", FORECAST_UNAVAILABLE), required=False,
           description="Event-day weather FORECAST known at prediction time; 'Unavailable' when no "
                       "forecast exists. Never a guessed value."),
    Column(TARGET, "target", "registration", False, allowed=(0, 1), required=False, nullable=False,
           description="SIMULATED attendance outcome (training data only)"),
]}

# Columns an uploaded file must contain to be used for prediction.
PREDICTION_REQUIRED = [c.name for c in COLUMNS.values()
                       if c.required and c.kind not in ("target",) and c.name not in ID_COLUMNS]
# ... and for training.
TRAINING_REQUIRED = PREDICTION_REQUIRED + [TARGET]

# Raw model inputs (before feature engineering). Every one is known pre-event.
MODEL_INPUTS = [
    "Course", "Year", "Currently_UBA_Volunteer", "Participated_UBA_Event_Before",
    "Experience_Level", "Area_Of_Interest", "Role", "Event_Type", "Event_Day", "Event_Time",
    "Event_Duration_Hours", "Registration_Days_Before", "Previous_Event_Attendance",
    "Previous_Events_Registered", "Previous_Attendance_Rate", "Academic_Engagement_Score",
    "Event_Popularity", "Distance_From_Venue_KM", "Has_Exam_Near_Event", "Weather_Forecast",
]
assert all(COLUMNS[c].pre_event for c in MODEL_INPUTS), "model inputs must be pre-event"

STUDENT_LEVEL = [c.name for c in COLUMNS.values() if c.level == "student" and c.kind != "id"]
EVENT_LEVEL = [c.name for c in COLUMNS.values() if c.level == "event" and c.kind != "id"]
