"""Event workflow queries: calendar, registration management and notifications.

Everything here reads stored records only. Nothing is estimated or invented:
an event without a date cannot appear on a calendar, an event without a
capacity gets no capacity warning, and so on.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Iterable

import pandas as pd

from core.db.connection import Database
from core.db.errors import ConstraintViolationError, NotFoundError, PersonalDataError
from core.db.repositories import _PII_KEYS, EventRepository, RegistrationRepository, StudentRepository

PHASES = ("Upcoming", "Ongoing", "Completed", "Cancelled", "Undated")
SLOT_START = {"Morning": time(9, 0), "Afternoon": time(13, 0), "Evening": time(17, 0)}


# ------------------------------------------------------------------ calendar
def _start_end(e: dict) -> tuple[datetime, datetime] | None:
    if not e.get("event_date"):
        return None
    d = date.fromisoformat(e["event_date"])
    t = time.fromisoformat(e["start_time"][:5]) if e.get("start_time") else SLOT_START.get(e["time_slot"], time(9))
    start = datetime.combine(d, t)
    return start, start + timedelta(hours=float(e.get("duration_hours") or 1))


def event_phase(e: dict, now: datetime | None = None) -> str:
    """Upcoming / Ongoing / Completed / Cancelled / Undated, from the stored status, date and time.

    Times are local wall-clock times as entered. An event whose stored status is
    'Completed' is Completed even if its date is in the future (organiser's decision)."""
    if e["status"] == "Cancelled":
        return "Cancelled"
    if e["status"] == "Completed":
        return "Completed"
    se = _start_end(e)
    if se is None:
        return "Undated"
    now = now or datetime.now()
    if now < se[0]:
        return "Upcoming"
    if now <= se[1]:
        return "Ongoing"
    return "Completed"          # date has passed; status still Scheduled (see notifications)


def calendar_events(db: Database, start: date | None = None, end: date | None = None,
                    phases: Iterable[str] | None = None, now: datetime | None = None,
                    dated_only: bool = True) -> pd.DataFrame:
    """Events for the calendar (dated events only by default; dataset events have no dates)."""
    rows = []
    for e in EventRepository(db).list():
        full = db.query_one("SELECT start_time, duration_hours, capacity, venue, registration_deadline "
                            "FROM events WHERE event_id = ?", (e["event_id"],))
        e = {**e, **full}
        ph = event_phase(e, now)
        if phases and ph not in phases:
            continue
        if e["event_date"]:
            d = date.fromisoformat(e["event_date"])
            if (start and d < start) or (end and d > end):
                continue
        elif start or end or dated_only:
            continue
        se = _start_end(e)
        rows.append({"event_id": e["event_id"], "name": e["name"], "event_type": e["event_type"],
                     "event_date": e["event_date"], "start": se[0].strftime("%H:%M") if se else None,
                     "end": se[1].strftime("%H:%M") if se else None, "time_slot": e["time_slot"],
                     "venue": e["venue"], "status": e["status"], "phase": ph,
                     "registrations": e["active_registrations"] or 0, "capacity": e["capacity"],
                     "registration_deadline": e["registration_deadline"], "data_source": e["data_source"]})
    cols = ["event_id", "name", "event_type", "event_date", "start", "end", "time_slot", "venue", "status",
            "phase", "registrations", "capacity", "registration_deadline", "data_source"]
    return pd.DataFrame(rows, columns=cols)


# ------------------------------------------------------------------ registration management
def registration_table(db: Database, search: str | None = None, event_id: str | None = None,
                       department: str | None = None, status: str | None = None,
                       data_source: str | None = None) -> pd.DataFrame:
    sql = ("SELECT r.registration_id, r.student_id, r.event_id, e.name AS event_name, e.event_type, e.event_date, "
           "c.department, c.name AS course, s.year, r.status, r.registration_days_before, r.has_exam_near_event, "
           "r.data_source, r.created_utc, "
           "CASE WHEN a.attendance_id IS NULL THEN 'Not recorded' "
           "     WHEN a.source IN ('ORIGINAL_SIMULATED','SYNTHETIC') THEN "
           "          CASE a.attended WHEN 1 THEN 'Attended (simulated)' ELSE 'Absent (simulated)' END "
           "     ELSE CASE a.attended WHEN 1 THEN 'Attended' ELSE 'Absent' END END AS attendance "
           "FROM registrations r JOIN students s USING(student_id) JOIN courses c ON c.course_id = s.course_id "
           "JOIN events e ON e.event_id = r.event_id "
           "LEFT JOIN attendance_records a ON a.registration_id = r.registration_id WHERE 1=1")
    p: list = []
    for col, val in (("r.event_id", event_id), ("c.department", department), ("r.status", status),
                     ("r.data_source", data_source)):
        if val:
            sql += f" AND {col} = ?"
            p.append(val)
    if search:
        sql += (" AND (lower(r.student_id) LIKE lower(?) OR lower(r.event_id) LIKE lower(?) "
                "OR lower(e.name) LIKE lower(?) OR lower(c.name) LIKE lower(?))")
        p += [f"%{search}%"] * 4
    return pd.DataFrame(db.query(sql + " ORDER BY r.event_id, r.student_id", p))


def department_participation(db: Database, event_id: str | None = None) -> pd.DataFrame:
    sql = ("SELECT c.department AS department, COUNT(*) AS registrations, "
           "SUM(CASE WHEN r.status = 'Registered' THEN 1 ELSE 0 END) AS active, "
           "SUM(CASE WHEN r.status = 'Cancelled' THEN 1 ELSE 0 END) AS cancelled, "
           "SUM(CASE WHEN r.status = 'Waitlisted' THEN 1 ELSE 0 END) AS waitlisted, COUNT(DISTINCT r.student_id) AS students "
           "FROM registrations r JOIN students s USING(student_id) JOIN courses c ON c.course_id = s.course_id")
    p: list = []
    if event_id:
        sql += " WHERE r.event_id = ?"
        p.append(event_id)
    return pd.DataFrame(db.query(sql + " GROUP BY c.department ORDER BY registrations DESC", p))


@dataclass
class RegistrationImport:
    added: int = 0
    duplicates: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        s = f"{self.added} registration(s) added, {len(self.duplicates)} duplicate(s) skipped"
        if self.errors:
            s += f", {len(self.errors)} row(s) rejected"
        return s + "."


REG_COLUMNS = {"student_id", "event_id", "registration_days_before", "registered_on", "has_exam_near_event"}


def import_registrations(db: Database, df: pd.DataFrame, dry_run: bool = False) -> RegistrationImport:
    """Register EXISTING students for EXISTING events from a table.

    Columns (case-insensitive): Student_ID, Event_ID, and Registration_Days_Before or
    Registered_On (YYYY-MM-DD, needs a dated event); optional Has_Exam_Near_Event (0/1).
    The whole file is rejected if it contains personal-data columns. Rows that would
    duplicate an existing registration (or an earlier row) are skipped and reported.
    All accepted rows are written in one transaction; nothing is written if any row is
    invalid."""
    cols = {c.lower(): c for c in df.columns}
    pii = sorted(c for c in df.columns if str(c).lower() in _PII_KEYS)
    if pii:
        raise PersonalDataError("Personal-data columns present: " + ", ".join(pii))
    for need in ("student_id", "event_id"):
        if need not in cols:
            raise ConstraintViolationError(f"Column {need.title().replace('_Id', '_ID')} is required.")
    if "registration_days_before" not in cols and "registered_on" not in cols:
        raise ConstraintViolationError("Provide Registration_Days_Before or Registered_On.")
    res = RegistrationImport()
    regs, students, events = RegistrationRepository(db), StudentRepository(db), EventRepository(db)
    seen: set[tuple[str, str]] = set()
    planned = []
    for i, row in df.iterrows():
        n = i + 2                                   # spreadsheet row number (header = 1)
        sid, eid = str(row[cols["student_id"]]).strip(), str(row[cols["event_id"]]).strip()
        if not students.exists(sid):
            res.errors.append(f"row {n}: unknown student {sid}")
            continue
        if not events.exists(eid):
            res.errors.append(f"row {n}: unknown event {eid}")
            continue
        if (sid, eid) in seen or regs.find(sid, eid):
            res.duplicates.append(f"{sid} → {eid}")
            continue
        seen.add((sid, eid))
        days = row.get(cols.get("registration_days_before", ""), None) if "registration_days_before" in cols else None
        on = row.get(cols["registered_on"]) if "registered_on" in cols else None
        exam = row.get(cols["has_exam_near_event"], 0) if "has_exam_near_event" in cols else 0
        try:
            days = None if days is None or pd.isna(days) else int(days)
            on = None if on is None or pd.isna(on) else str(on)[:10]
            exam = 0 if exam is None or pd.isna(exam) else int(exam)
            if exam not in (0, 1):
                raise ValueError("Has_Exam_Near_Event must be 0 or 1")
        except (TypeError, ValueError) as exc:
            res.errors.append(f"row {n}: {exc}")
            continue
        planned.append((n, sid, eid, days, on, exam))
    if res.errors or dry_run:
        return res
    with db.transaction():
        for n, sid, eid, days, on, exam in planned:
            try:
                regs.register(sid, eid, days, exam, registered_on=on, data_source="UPLOAD")
            except (ConstraintViolationError, NotFoundError) as exc:
                raise ConstraintViolationError(f"row {n}: {exc}") from exc
            res.added += 1
        db.audit("import", "registrations", None, res.summary())
    return res


# ------------------------------------------------------------------ notifications
@dataclass
class Notification:
    key: str            # stable id, used to dismiss
    level: str          # info | warning | critical
    category: str       # Upcoming event | Registration deadline | Capacity | Attendance
    title: str
    detail: str
    event_id: str | None = None


def notifications(db: Database, today: date | None = None, horizon_days: int = 7,
                  predicted: dict[str, float] | None = None) -> list[Notification]:
    """Alerts derived from stored events, registrations and attendance.

    ``predicted`` optionally maps event_id -> expected attendees (from the latest
    stored predictions) for "expected attendance above capacity" warnings."""
    today = today or date.today()
    out: list[Notification] = []
    rows = db.query("SELECT e.*, (SELECT COUNT(*) FROM registrations r WHERE r.event_id = e.event_id "
                    "AND r.status = 'Registered') AS active, "
                    "(SELECT COUNT(*) FROM attendance_records a JOIN registrations r USING(registration_id) "
                    " WHERE r.event_id = e.event_id) AS recorded, "
                    "(SELECT COUNT(*) FROM event_outcomes o WHERE o.event_id = e.event_id) AS has_outcome "
                    "FROM events e WHERE e.status <> 'Cancelled'")
    predicted = predicted or {}
    for e in rows:
        eid, name = e["event_id"], e["name"] or e["event_id"]
        d = date.fromisoformat(e["event_date"]) if e["event_date"] else None
        if e["status"] == "Scheduled" and d:
            days = (d - today).days
            if 0 <= days <= horizon_days:
                when = "today" if days == 0 else ("tomorrow" if days == 1 else f"in {days} days")
                out.append(Notification(f"upcoming:{eid}:{d}", "info", "Upcoming event",
                                        f"{name} is {when} ({d:%a %d %b})",
                                        f"{e['active']} active registration(s).", eid))
            if days < 0:
                out.append(Notification(f"overdue:{eid}", "warning", "Attendance",
                                        f"{name} took place on {d:%d %b} but is still marked Scheduled",
                                        "Record the actual attendance (or a head count) to complete it.", eid))
        if e["registration_deadline"] and e["status"] == "Scheduled":
            dl = date.fromisoformat(e["registration_deadline"])
            left = (dl - today).days
            if 0 <= left <= 3:
                out.append(Notification(f"deadline:{eid}:{dl}", "warning" if left <= 1 else "info",
                                        "Registration deadline",
                                        f"Registration for {name} closes "
                                        f"{'today' if left == 0 else f'in {left} day(s)'} ({dl:%d %b})",
                                        f"{e['active']} registered so far"
                                        + (f" of {e['capacity']} places." if e["capacity"] else "."), eid))
        if e["capacity"] and e["status"] == "Scheduled":
            cap, act = int(e["capacity"]), int(e["active"])
            if act > cap:
                out.append(Notification(f"over:{eid}:{act}", "critical", "Capacity",
                                        f"{name} is over capacity: {act} registrations for {cap} places",
                                        "Waitlist late registrations or move to a bigger venue.", eid))
            elif act >= 0.9 * cap:
                out.append(Notification(f"near:{eid}:{act}", "warning", "Capacity",
                                        f"{name} is {act / cap:.0%} full ({act}/{cap})",
                                        "Consider a waiting list.", eid))
            exp = predicted.get(eid)
            if exp is not None and exp > cap:
                out.append(Notification(f"expected:{eid}:{round(exp)}", "warning", "Capacity",
                                        f"{name}: about {exp:.0f} expected attendees for {cap} places",
                                        "Based on the latest stored prediction for each registration.", eid))
        if e["status"] == "Completed" and e["data_source"] in ("MANUAL", "UPLOAD") \
                and not e["recorded"] and not e["has_outcome"]:
            out.append(Notification(f"noatt:{eid}", "warning", "Attendance",
                                    f"{name} is completed but has no attendance recorded",
                                    "Without it the prediction cannot be compared with reality.", eid))
    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(out, key=lambda n: (order[n.level], n.category, n.title))


def expected_attendance_by_event(db: Database) -> dict[str, float]:
    """Sum of the latest active-model probabilities per event (only events that were predicted)."""
    rows = db.query("SELECT event_id, expected_attendance_active_model AS exp FROM v_event_participation "
                    "WHERE expected_attendance_active_model IS NOT NULL")
    return {r["event_id"]: float(r["exp"]) for r in rows}
