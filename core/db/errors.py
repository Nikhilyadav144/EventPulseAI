"""Database exceptions with user-facing messages.

Raw sqlite3 errors - and PostgreSQL errors (``PgError``, raised by the PostgreSQL
adapter in ``core/db/postgres.py``) - are translated into the same exception classes, so
the UI and API can show something meaningful and decide how to react (e.g. a duplicate
registration vs. a disk problem) whatever the database engine.
"""
from __future__ import annotations

import sqlite3


class DatabaseError(Exception):
    """Base class for all EventPulse database errors."""


class NotFoundError(DatabaseError):
    pass


class DuplicateRecordError(DatabaseError):
    pass


class ConstraintViolationError(DatabaseError):
    pass


class ReferenceError_(DatabaseError):
    """A foreign key points to a missing record, or a referenced record cannot be removed."""


class HistoryProtectedError(DatabaseError):
    """The operation would delete or change prediction / attendance history."""


class MigrationError(DatabaseError):
    pass


class BackupError(DatabaseError):
    pass


class PersonalDataError(DatabaseError):
    """Personal information was offered for storage."""


class PgError(Exception):
    """A PostgreSQL error, independent of the Python driver that reported it."""

    def __init__(self, sqlstate: str, message: str, constraint: str | None = None,
                 table: str | None = None, detail: str | None = None):
        super().__init__(message)
        self.sqlstate, self.message = sqlstate or "XX000", message
        self.constraint, self.table, self.detail = constraint, table, detail


#: Every low-level error a connection can raise (catch these, then ``translate``).
DB_ERRORS = (sqlite3.Error, PgError)

# PostgreSQL unique constraints named by the PostgreSQL schema -> the same friendly messages.
_PG_UNIQUE = {
    "registrations_student_id_event_id_key": "registrations.student_id, registrations.event_id",
    "students_pkey": "students.student_id",
    "events_pkey": "events.event_id",
    "attendance_records_registration_id_key": "attendance_records.registration_id",
    "event_outcomes_pkey": "event_outcomes.event_id",
    "model_versions_model_key_version_key": "model_versions.model_key, model_versions.version",
    "ux_model_versions_single_active": "model_versions.is_active",
}


_FRIENDLY_UNIQUE = {
    "registrations.student_id, registrations.event_id":
        "This student is already registered for this event.",
    "students.student_id": "A student with this Student_ID already exists.",
    "events.event_id": "An event with this Event_ID already exists.",
    "attendance_records.registration_id": "Attendance has already been recorded for this registration.",
    "event_outcomes.event_id": "An outcome has already been recorded for this event.",
    "model_versions.model_key, model_versions.version": "This model version already exists.",
    "model_versions.is_active": "Only one model can be active.",
}


def _translate_pg(exc: PgError) -> DatabaseError:
    st, msg = exc.sqlstate, exc.message
    if st == "23505":                                     # unique_violation
        cols = _PG_UNIQUE.get(exc.constraint or "", exc.constraint or "")
        return DuplicateRecordError(_FRIENDLY_UNIQUE.get(cols, f"Duplicate record ({cols})."))
    if st == "23503":                                     # foreign_key_violation
        return ReferenceError_("The record refers to something that does not exist, or other records "
                               "still depend on it.")
    if st in ("23514", "23502"):                          # check_violation, not_null_violation
        return ConstraintViolationError(f"Invalid value: {msg}" + (f" ({exc.constraint})" if exc.constraint else ""))
    if "append-only" in msg:
        return HistoryProtectedError("Prediction history cannot be modified.")
    if st.startswith("23") or st == "P0001":              # other integrity / RAISE EXCEPTION in a trigger
        return ConstraintViolationError(msg)
    if st in ("40001", "40P01", "55P03"):                 # serialization failure, deadlock, lock not available
        return DatabaseError("The database is busy. Please try again in a moment.")
    if st.startswith("08"):                               # connection problems
        return DatabaseError("The database server is not reachable. Please try again in a moment.")
    return DatabaseError(f"Database operation failed: {msg}")


def translate(exc: Exception) -> DatabaseError:
    if isinstance(exc, PgError):
        return _translate_pg(exc)
    msg = str(exc)
    if isinstance(exc, sqlite3.IntegrityError):
        if msg.startswith("UNIQUE constraint failed"):
            cols = msg.split(":", 1)[1].strip()
            return DuplicateRecordError(_FRIENDLY_UNIQUE.get(cols, f"Duplicate record ({cols})."))
        if "FOREIGN KEY constraint failed" in msg:
            return ReferenceError_("The record refers to something that does not exist, or other records "
                                   "still depend on it.")
        if msg.startswith("CHECK constraint failed") or msg.startswith("NOT NULL constraint failed"):
            return ConstraintViolationError(f"Invalid value: {msg}")
        if "append-only" in msg:
            return HistoryProtectedError("Prediction history cannot be modified.")
        return ConstraintViolationError(msg)
    if isinstance(exc, sqlite3.OperationalError):
        if "locked" in msg or "busy" in msg:
            return DatabaseError("The database is busy. Please try again in a moment.")
        if "append-only" in msg:
            return HistoryProtectedError("Prediction history cannot be modified.")
        return DatabaseError(f"Database operation failed: {msg}")
    if isinstance(exc, sqlite3.DatabaseError) and "not a database" in msg:
        return DatabaseError("The file is not a valid EventPulse database.")
    return DatabaseError(msg)
