"""Event check-in (QR and desk) and recorded-attendance management.

Workflow (documented in docs/CHECKIN.md):
1. The organiser opens Attendance Records in the desktop app and creates the event's
   check-in code. The app shows a QR code for  http://<this Mac's address>:8010/checkin/<event>?t=<token>.
2. ``EventPulse_CheckIn_Server.command`` runs ``checkin_app.py`` (FastAPI) on the local
   network. A student scans the QR code with the phone's own camera app, the page opens,
   and the student enters their (pseudonymous) Student ID.
3. ``check_in()`` validates the token, the event, the date and the registration, refuses
   duplicates, and writes the check-in AND a recorded attendance row in one transaction.
4. After the event, ``finalise_attendance()`` marks registered students who never checked
   in as absent, stores the event outcome and compares it with the predictions made BEFORE
   the event (services.evaluate_event).

No camera code exists in the desktop app: scanning is done by the phone's camera app.
Real (recorded) attendance is never written for events whose outcomes came from a
dataset (simulated), so simulated and recorded outcomes cannot mix.
"""
from __future__ import annotations

import io
import json
import re
import secrets
import socket
from datetime import date, datetime

import pandas as pd

from core.db.connection import Database, utc_now
from core.db.errors import ConstraintViolationError, DuplicateRecordError, NotFoundError
from core.db.repositories import AttendanceRepository, EventRepository, RegistrationRepository

CHECKIN_PORT = 8010
SIMULATED_SOURCES = ("ORIGINAL_SIMULATED", "SYNTHETIC")
_SID = re.compile(r"^[A-Za-z0-9._-]{1,40}$")


def has_simulated_outcomes(db: Database, event_id: str) -> bool:
    return bool(db.scalar(
        "SELECT EXISTS(SELECT 1 FROM attendance_records a JOIN registrations r USING(registration_id) "
        f"WHERE r.event_id = ? AND a.source IN ({','.join('?' * len(SIMULATED_SOURCES))}))",
        (event_id, *SIMULATED_SOURCES)))


def ensure_real_event(db: Database, event_id: str) -> dict:
    ev = EventRepository(db).get(event_id)
    if ev["data_source"] in ("ORIGINAL_HYBRID", "SYNTHETIC") or has_simulated_outcomes(db, event_id):
        raise ConstraintViolationError(
            f"{event_id} comes from a dataset with SIMULATED outcomes. Real attendance can only be recorded "
            "for events created or imported for real use, so simulated and recorded outcomes never mix.")
    if ev["status"] == "Cancelled":
        raise ConstraintViolationError(f"{event_id} was cancelled.")
    return ev


# ------------------------------------------------------------------ tokens
def get_or_create_token(db: Database, event_id: str) -> str:
    ensure_real_event(db, event_id)
    row = db.query_one("SELECT token, active FROM checkin_tokens WHERE event_id = ?", (event_id,))
    if row and row["active"]:
        return row["token"]
    token = secrets.token_urlsafe(18)
    with db.transaction():
        db.execute("INSERT INTO checkin_tokens(event_id, token, active, created_utc) VALUES (?,?,1,?) "
                   "ON CONFLICT(event_id) DO UPDATE SET token = excluded.token, active = 1, "
                   "created_utc = excluded.created_utc", (event_id, token, utc_now()))
        db.audit("checkin_token", "event", event_id, "issued")
    return token


def revoke_token(db: Database, event_id: str) -> None:
    with db.transaction():
        db.execute("UPDATE checkin_tokens SET active = 0 WHERE event_id = ?", (event_id,))
        db.audit("checkin_token", "event", event_id, "revoked")


def lan_address() -> str:
    """This computer's address on the local network (no traffic is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def checkin_url(event_id: str, token: str, host: str | None = None, port: int = CHECKIN_PORT) -> str:
    return f"http://{host or lan_address()}:{port}/checkin/{event_id}?t={token}"


def qr_png(data: str, scale: int = 8) -> bytes:
    """PNG bytes of a QR code (needs the 'segno' package, pure Python)."""
    import segno
    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="png", scale=scale, border=2, dark="#29263A", light="#FFFFFF")
    return buf.getvalue()


# ------------------------------------------------------------------ check-in
def check_in(db: Database, event_id: str, student_id: str, token: str | None = None, method: str = "QR",
             today: date | None = None) -> dict:
    """Record one check-in. QR check-ins need the event's active token; desk check-ins
    are made by the organiser in the app. Raises a clear error otherwise."""
    student_id = (student_id or "").strip()
    if not _SID.match(student_id):
        raise ConstraintViolationError("Enter your Student ID (letters, digits, '-', '_' or '.').")
    if method == "QR":
        row = db.query_one("SELECT token, active FROM checkin_tokens WHERE event_id = ?", (event_id,))
        if not row or not row["active"] or not token or not secrets.compare_digest(row["token"], token):
            raise ConstraintViolationError("This check-in link is not valid. Ask the organiser for the current QR code.")
    elif method != "DESK":
        raise ConstraintViolationError("Unknown check-in method.")
    ev = ensure_real_event(db, event_id)
    today = today or date.today()
    if ev["event_date"] and date.fromisoformat(ev["event_date"]) != today:
        raise ConstraintViolationError(f"Check-in is open on the event day only ({ev['event_date']}).")
    reg = RegistrationRepository(db).find(student_id, event_id)
    if not reg or reg["status"] != "Registered":
        raise NotFoundError(f"{student_id} is not registered for this event. Please see the organiser's desk.")
    if db.scalar("SELECT 1 FROM checkins WHERE registration_id = ?", (reg["registration_id"],)):
        raise DuplicateRecordError(f"{student_id} has already checked in.")
    ts = utc_now()
    with db.transaction():
        db.execute("INSERT INTO checkins(event_id, registration_id, student_id, method, checked_in_utc) "
                   "VALUES (?,?,?,?,?)", (event_id, reg["registration_id"], student_id, method, ts))
        existing = db.query_one("SELECT * FROM attendance_records WHERE registration_id = ?",
                                (reg["registration_id"],))
        note = f"{'QR' if method == 'QR' else 'Desk'} check-in at {ts}"
        if existing:
            if existing["attended"] != 1:
                AttendanceRepository(db).correct(reg["registration_id"], 1, "check-in", note)
        else:
            db.execute("INSERT INTO attendance_records(registration_id, attended, source, recorded_by, "
                       "recorded_utc, notes) VALUES (?,?, 'MANUAL', ?, ?, ?)",
                       (reg["registration_id"], 1, "QR check-in" if method == "QR" else "desk check-in", ts, note))
    return {"event_id": event_id, "student_id": student_id, "checked_in_utc": ts, "method": method,
            "event_name": ev["name"] or event_id}


def checkins(db: Database, event_id: str) -> pd.DataFrame:
    return pd.DataFrame(db.query("SELECT student_id, method, checked_in_utc FROM checkins WHERE event_id = ? "
                                 "ORDER BY checked_in_utc", (event_id,)),
                        columns=["student_id", "method", "checked_in_utc"])


def finalise_attendance(db: Database, event_id: str, walk_ins: int = 0, recorded_by: str = "organiser") -> dict:
    """After the event: everyone registered who did not check in (and has no record) is
    recorded absent; the outcome is stored, the event completed and predictions evaluated."""
    from core.db.services import evaluate_event
    ev = ensure_real_event(db, event_id)
    if ev["event_date"] and date.fromisoformat(ev["event_date"]) > date.today():
        raise ConstraintViolationError("The event has not taken place yet.")
    regs = RegistrationRepository(db).for_event(event_id, status="Registered")
    if not regs:
        raise ConstraintViolationError("The event has no active registrations.")
    att = AttendanceRepository(db)
    with db.transaction():
        absent = 0
        for r in regs:
            if not db.scalar("SELECT 1 FROM attendance_records WHERE registration_id = ?", (r["registration_id"],)):
                att.record(r["registration_id"], 0, "MANUAL", recorded_by, "no check-in; finalised as absent")
                absent += 1
        present = int(db.scalar("SELECT COUNT(*) FROM attendance_records a JOIN registrations r "
                                "USING(registration_id) WHERE r.event_id = ? AND r.status = 'Registered' "
                                "AND a.attended = 1", (event_id,)))
        att.set_outcome(event_id, "per_registration", present, int(walk_ins), recorded_by,
                        "finalised from check-ins")
        if ev["status"] != "Completed":
            EventRepository(db).set_status(event_id, "Completed")
        evaluations = evaluate_event(db, event_id)
    return {"present": present, "marked_absent": absent, "walk_ins": int(walk_ins),
            "registered": len(regs), "evaluations": evaluations}


def correct_attendance(db: Database, registration_id: int, attended: int, corrected_by: str, reason: str) -> None:
    """Organiser correction of a RECORDED outcome. Name and reason are required and are
    kept, with the old value, in the audit log. Simulated outcomes cannot be edited."""
    if not (corrected_by or "").strip() or not (reason or "").strip():
        raise ConstraintViolationError("A correction needs the name of the person making it and a reason.")
    rec = db.query_one("SELECT * FROM attendance_records WHERE registration_id = ?", (registration_id,))
    if rec is None:
        raise NotFoundError("No attendance has been recorded for this registration yet.")
    if rec["source"] in SIMULATED_SOURCES:
        raise ConstraintViolationError("Simulated outcomes from a dataset cannot be edited.")
    if int(attended) == rec["attended"]:
        return
    reg = RegistrationRepository(db).get(registration_id)
    with db.transaction():
        AttendanceRepository(db).correct(registration_id, int(attended), corrected_by.strip(), reason.strip())
        if int(attended) == 0:
            db.execute("DELETE FROM checkins WHERE registration_id = ?", (registration_id,))
        out = AttendanceRepository(db).outcome(reg["event_id"])
        if out and out["method"] == "per_registration":
            present = int(db.scalar("SELECT COUNT(*) FROM attendance_records a JOIN registrations r "
                                    "USING(registration_id) WHERE r.event_id = ? AND r.status = 'Registered' "
                                    "AND a.attended = 1", (reg["event_id"],)))
            AttendanceRepository(db).set_outcome(reg["event_id"], "per_registration", present, out["walk_ins"],
                                                 corrected_by.strip(), f"corrected: {reason.strip()}")


def attendance_table(db: Database, event_id: str | None = None, include_simulated: bool = False) -> pd.DataFrame:
    sql = ("SELECT r.event_id, e.name AS event_name, e.event_date, r.registration_id, r.student_id, r.status AS "
           "registration_status, a.attended, a.source, a.recorded_by, a.recorded_utc, c.method AS checkin, "
           "c.checked_in_utc FROM registrations r JOIN events e USING(event_id) "
           "LEFT JOIN attendance_records a ON a.registration_id = r.registration_id "
           "LEFT JOIN checkins c ON c.registration_id = r.registration_id WHERE 1=1")
    p: list = []
    if event_id:
        sql += " AND r.event_id = ?"
        p.append(event_id)
    if not include_simulated:
        sql += f" AND (a.source IS NULL OR a.source NOT IN ({','.join('?' * len(SIMULATED_SOURCES))}))"
        p += list(SIMULATED_SOURCES)
    df = pd.DataFrame(db.query(sql + " ORDER BY r.event_id, r.student_id", p))
    if not df.empty:
        df["outcome"] = df.apply(lambda r: "Not recorded" if pd.isna(r["attended"]) else
                                 ("Attended" if r["attended"] == 1 else "Absent")
                                 + (" (simulated)" if r["source"] in SIMULATED_SOURCES else ""), axis=1)
    return df


def correction_log(db: Database, event_id: str | None = None) -> pd.DataFrame:
    rows = db.query("SELECT l.ts_utc, l.entity_id AS registration_id, l.details, r.event_id, r.student_id "
                    "FROM audit_log l JOIN registrations r ON l.entity_id = CAST(r.registration_id AS TEXT) "
                    "WHERE l.action = 'correct' AND l.entity = 'attendance' ORDER BY l.ts_utc DESC")
    out = []
    for r in rows:
        if event_id and r["event_id"] != event_id:
            continue
        d = json.loads(r["details"] or "{}")
        out.append({"when_utc": r["ts_utc"], "event_id": r["event_id"], "student_id": r["student_id"],
                    "old": d.get("old"), "new": d.get("new"), "by": d.get("by"), "reason": d.get("reason")})
    return pd.DataFrame(out, columns=["when_utc", "event_id", "student_id", "old", "new", "by", "reason"])


def real_events(db: Database) -> list[dict]:
    """Events that can take recorded attendance (not from a simulated dataset)."""
    return [e for e in EventRepository(db).list() if e["data_source"] in ("MANUAL", "UPLOAD")
            and e["status"] != "Cancelled" and not has_simulated_outcomes(db, e["event_id"])]
