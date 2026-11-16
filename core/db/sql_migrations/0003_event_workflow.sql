-- EventPulse AI - migration 0003: real event workflow (calendar deadlines, QR check-in).
-- Additive only: one new nullable column and two new tables. Existing rows are untouched.
-- (migrations.py takes a verified backup before applying this to a database with data.)

ALTER TABLE events ADD COLUMN registration_deadline TEXT
    CHECK (registration_deadline IS NULL OR date(registration_deadline) = registration_deadline);

-- One secret check-in token per event; the QR code encodes a URL containing it.
CREATE TABLE checkin_tokens (
    event_id      TEXT PRIMARY KEY REFERENCES events(event_id) ON DELETE CASCADE,
    token         TEXT NOT NULL UNIQUE CHECK (length(token) >= 16),
    active        INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    created_utc   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Raw check-in log. One check-in per registration (duplicates are refused by UNIQUE).
-- The matching attendance_records row (source MANUAL, recorded_by 'QR check-in') is written
-- in the same transaction, so recorded attendance and check-ins cannot disagree.
CREATE TABLE checkins (
    checkin_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL REFERENCES events(event_id) ON DELETE RESTRICT,
    registration_id INTEGER NOT NULL UNIQUE REFERENCES registrations(registration_id) ON DELETE RESTRICT,
    student_id      TEXT NOT NULL,
    method          TEXT NOT NULL CHECK (method IN ('QR','DESK')),
    checked_in_utc  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX ix_checkins_event ON checkins(event_id, checked_in_utc);
