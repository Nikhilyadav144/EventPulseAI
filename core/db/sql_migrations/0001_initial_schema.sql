-- EventPulse AI - migration 0001: initial relational schema.
-- Conventions: timestamps are UTC ISO-8601 text ('YYYY-MM-DDTHH:MM:SSZ');
-- booleans are INTEGER 0/1 with CHECK constraints; foreign keys are enforced
-- (PRAGMA foreign_keys = ON is set on every connection).
-- No table has a column for names, e-mail addresses, phone or roll numbers.

-- ---------------------------------------------------------------- settings
CREATE TABLE app_settings (
    key          TEXT PRIMARY KEY,
    value        TEXT NOT NULL,                 -- JSON-encoded
    updated_utc  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ---------------------------------------------------------------- datasets
CREATE TABLE datasets (
    dataset_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('ORIGINAL','PROCESSED','SYNTHETIC','UPLOAD')),
    file_name      TEXT,
    source_sheet   TEXT CHECK (source_sheet IS NULL OR source_sheet <> 'Raw_Merged_Data'),
    sha256         TEXT,
    row_count      INTEGER CHECK (row_count IS NULL OR row_count >= 0),
    imported_utc   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    notes          TEXT,
    UNIQUE (kind, sha256)
);

-- ---------------------------------------------------------------- courses (lookup)
CREATE TABLE courses (
    course_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    department  TEXT NOT NULL
);

-- ---------------------------------------------------------------- students (pseudonymous)
CREATE TABLE students (
    student_id                      TEXT PRIMARY KEY
        CHECK (length(student_id) BETWEEN 1 AND 40 AND student_id NOT LIKE '%@%'
               AND student_id NOT GLOB '*[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]*'   -- phone-like
               AND student_id NOT GLOB '[0-9][0-9][A-Za-z][A-Za-z]*[0-9][0-9][0-9]'),         -- roll-number-like
    course_id                       INTEGER NOT NULL REFERENCES courses(course_id) ON DELETE RESTRICT,
    year                            INTEGER NOT NULL CHECK (year BETWEEN 1 AND 5),
    currently_uba_volunteer         INTEGER NOT NULL CHECK (currently_uba_volunteer IN (0,1)),
    participated_uba_event_before   INTEGER NOT NULL CHECK (participated_uba_event_before IN (0,1)),
    experience_level                TEXT NOT NULL
        CHECK (experience_level IN ('No experience','Some experience','Experienced')),
    area_of_interest                TEXT NOT NULL,
    role                            TEXT NOT NULL,
    previous_event_attendance       INTEGER NOT NULL CHECK (previous_event_attendance >= 0),
    previous_events_registered      INTEGER CHECK (previous_events_registered IS NULL OR previous_events_registered >= 0),
    previous_attendance_rate        REAL CHECK (previous_attendance_rate IS NULL OR previous_attendance_rate BETWEEN 0 AND 100),
    academic_engagement_score       REAL CHECK (academic_engagement_score IS NULL OR academic_engagement_score BETWEEN 0 AND 100),
    distance_from_venue_km          REAL CHECK (distance_from_venue_km IS NULL OR distance_from_venue_km BETWEEN 0 AND 200),
    data_source                     TEXT NOT NULL
        CHECK (data_source IN ('ORIGINAL_HYBRID','SYNTHETIC','MANUAL','UPLOAD')),
    dataset_id                      INTEGER REFERENCES datasets(dataset_id) ON DELETE SET NULL,
    created_utc                     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_utc                     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    CHECK (previous_events_registered IS NULL OR previous_event_attendance <= previous_events_registered)
);

-- ---------------------------------------------------------------- events
CREATE TABLE events (
    event_id                TEXT PRIMARY KEY CHECK (length(event_id) BETWEEN 1 AND 40),
    name                    TEXT,
    event_type              TEXT NOT NULL CHECK (event_type IN ('Technical','Workshop','Cultural','Seminar','Sports')),
    event_date              TEXT CHECK (event_date IS NULL OR date(event_date) = event_date),
    event_day               TEXT NOT NULL
        CHECK (event_day IN ('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')),
    start_time              TEXT CHECK (start_time IS NULL OR time(start_time) IS NOT NULL),
    time_slot               TEXT NOT NULL CHECK (time_slot IN ('Morning','Afternoon','Evening')),
    duration_hours          REAL NOT NULL CHECK (duration_hours > 0 AND duration_hours <= 12),
    venue                   TEXT,
    capacity                INTEGER CHECK (capacity IS NULL OR capacity > 0),
    popularity              REAL CHECK (popularity IS NULL OR popularity BETWEEN 0 AND 100),
    status                  TEXT NOT NULL DEFAULT 'Scheduled'
        CHECK (status IN ('Scheduled','Completed','Cancelled')),
    data_source             TEXT NOT NULL CHECK (data_source IN ('ORIGINAL_HYBRID','SYNTHETIC','MANUAL','UPLOAD')),
    dataset_id              INTEGER REFERENCES datasets(dataset_id) ON DELETE SET NULL,
    created_utc             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_utc             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    -- the weekday must agree with the calendar date when a date is known
    CHECK (event_date IS NULL OR event_day = CASE strftime('%w', event_date)
        WHEN '0' THEN 'Sunday' WHEN '1' THEN 'Monday' WHEN '2' THEN 'Tuesday' WHEN '3' THEN 'Wednesday'
        WHEN '4' THEN 'Thursday' WHEN '5' THEN 'Friday' ELSE 'Saturday' END)
);

-- ---------------------------------------------------------------- weather forecasts (history per event)
CREATE TABLE weather_forecasts (
    forecast_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id              TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    forecast              TEXT NOT NULL CHECK (forecast IN ('Clear','Cloudy','Rainy','Unavailable')),
    forecast_obtained_at  TEXT CHECK (forecast_obtained_at IS NULL
                                      OR strftime('%Y-%m-%dT%H:%M:%SZ', forecast_obtained_at) IS NOT NULL),
    source                TEXT NOT NULL DEFAULT 'MANUAL'
        CHECK (source IN ('MANUAL','ORIGINAL_DATASET','SYNTHETIC','UPLOAD')),
    recorded_utc          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    notes                 TEXT,
    -- a real forecast entered in the app must say when it was obtained
    CHECK (forecast = 'Unavailable' OR forecast_obtained_at IS NOT NULL
           OR source IN ('ORIGINAL_DATASET','SYNTHETIC'))
);

-- ---------------------------------------------------------------- registrations
CREATE TABLE registrations (
    registration_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id                TEXT NOT NULL REFERENCES students(student_id) ON DELETE RESTRICT,
    event_id                  TEXT NOT NULL REFERENCES events(event_id) ON DELETE RESTRICT,
    registered_on             TEXT CHECK (registered_on IS NULL OR date(registered_on) = registered_on),
    registration_days_before  INTEGER NOT NULL CHECK (registration_days_before BETWEEN 0 AND 365),
    has_exam_near_event       INTEGER NOT NULL CHECK (has_exam_near_event IN (0,1)),
    status                    TEXT NOT NULL DEFAULT 'Registered'
        CHECK (status IN ('Registered','Cancelled','Waitlisted')),
    data_source               TEXT NOT NULL CHECK (data_source IN ('ORIGINAL_HYBRID','SYNTHETIC','MANUAL','UPLOAD')),
    dataset_id                INTEGER REFERENCES datasets(dataset_id) ON DELETE SET NULL,
    created_utc               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_utc               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (student_id, event_id)            -- no duplicate student-event registrations
);

-- ---------------------------------------------------------------- model versions and metrics
CREATE TABLE model_versions (
    model_version_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    model_key           TEXT NOT NULL,              -- e.g. 'logistic_regression'
    version             INTEGER NOT NULL CHECK (version >= 1),
    name                TEXT NOT NULL,
    algorithm           TEXT NOT NULL,
    is_baseline         INTEGER NOT NULL DEFAULT 0 CHECK (is_baseline IN (0,1)),
    is_active           INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0,1)),
    artifact_path       TEXT NOT NULL,
    artifact_sha256     TEXT NOT NULL,
    threshold           REAL NOT NULL CHECK (threshold > 0 AND threshold < 1),
    hyperparameters     TEXT NOT NULL DEFAULT '{}',  -- JSON
    training_dataset_id INTEGER REFERENCES datasets(dataset_id) ON DELETE SET NULL,
    training_data_label TEXT,
    training_data_sha256 TEXT,
    sklearn_version     TEXT,
    python_version      TEXT,
    trained_utc         TEXT,
    registered_utc      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    notes               TEXT,
    UNIQUE (model_key, version),
    UNIQUE (model_key, artifact_sha256)
);
-- at most one active model
CREATE UNIQUE INDEX ux_model_versions_single_active ON model_versions(is_active) WHERE is_active = 1;

CREATE TABLE model_metrics (
    metric_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    model_version_id  INTEGER NOT NULL REFERENCES model_versions(model_version_id) ON DELETE CASCADE,
    evaluation_set    TEXT NOT NULL
        CHECK (evaluation_set IN ('cv','holdout','split_sensitivity','majority_baseline')),
    metric            TEXT NOT NULL,
    value             REAL,
    std               REAL,
    n                 INTEGER,
    UNIQUE (model_version_id, evaluation_set, metric)
);

-- ---------------------------------------------------------------- predictions (append-only history)
CREATE TABLE prediction_runs (
    run_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    model_version_id  INTEGER NOT NULL REFERENCES model_versions(model_version_id) ON DELETE RESTRICT,
    run_type          TEXT NOT NULL CHECK (run_type IN ('individual','bulk_file','event')),
    event_id          TEXT REFERENCES events(event_id) ON DELETE RESTRICT,
    input_dataset_id  INTEGER REFERENCES datasets(dataset_id) ON DELETE SET NULL,
    n_predictions     INTEGER NOT NULL DEFAULT 0 CHECK (n_predictions >= 0),
    created_utc       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    notes             TEXT
);

CREATE TABLE predictions (
    prediction_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id             INTEGER NOT NULL REFERENCES prediction_runs(run_id) ON DELETE RESTRICT,
    model_version_id   INTEGER NOT NULL REFERENCES model_versions(model_version_id) ON DELETE RESTRICT,
    registration_id    INTEGER REFERENCES registrations(registration_id) ON DELETE RESTRICT,
    forecast_id        INTEGER REFERENCES weather_forecasts(forecast_id) ON DELETE SET NULL,
    student_ref        TEXT,          -- pseudonymous reference for rows not stored as registrations
    event_ref          TEXT,
    probability        REAL NOT NULL CHECK (probability BETWEEN 0 AND 1),
    predicted_attend   INTEGER NOT NULL CHECK (predicted_attend IN (0,1)),
    threshold          REAL NOT NULL CHECK (threshold > 0 AND threshold < 1),
    certainty          TEXT NOT NULL CHECK (certainty IN ('Uncertain','Moderate','Clear')),
    forecast_used      TEXT NOT NULL CHECK (forecast_used IN ('Clear','Cloudy','Rainy','Unavailable')),
    input_snapshot     TEXT NOT NULL,  -- JSON of the exact model inputs used
    predicted_utc      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    CHECK (predicted_attend = (probability >= threshold))
);

CREATE TRIGGER trg_predictions_immutable BEFORE UPDATE ON predictions
BEGIN
    SELECT RAISE(ABORT, 'prediction history is append-only');
END;

-- ---------------------------------------------------------------- actual attendance (separate from predictions)
CREATE TABLE attendance_records (
    attendance_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    registration_id  INTEGER NOT NULL UNIQUE REFERENCES registrations(registration_id) ON DELETE RESTRICT,
    attended         INTEGER NOT NULL CHECK (attended IN (0,1)),
    source           TEXT NOT NULL CHECK (source IN ('MANUAL','UPLOAD','ORIGINAL_SIMULATED','SYNTHETIC')),
    recorded_by      TEXT,
    recorded_utc     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    notes            TEXT
);

-- event-level outcome (head count), usable even when per-student attendance is unknown
CREATE TABLE event_outcomes (
    event_id          TEXT PRIMARY KEY REFERENCES events(event_id) ON DELETE RESTRICT,
    method            TEXT NOT NULL CHECK (method IN ('per_registration','headcount')),
    actual_attendees  INTEGER NOT NULL CHECK (actual_attendees >= 0),   -- registered students who attended
    walk_ins          INTEGER NOT NULL DEFAULT 0 CHECK (walk_ins >= 0),  -- attended without registering
    recorded_by       TEXT,
    recorded_utc      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    notes             TEXT
);

-- ---------------------------------------------------------------- stored predicted-vs-actual comparisons
CREATE TABLE prediction_evaluations (
    evaluation_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id                TEXT NOT NULL REFERENCES events(event_id) ON DELETE RESTRICT,
    model_version_id        INTEGER NOT NULL REFERENCES model_versions(model_version_id) ON DELETE RESTRICT,
    computed_utc            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    method                  TEXT NOT NULL CHECK (method IN ('per_registration','headcount')),
    registrations_evaluated INTEGER NOT NULL CHECK (registrations_evaluated >= 0),
    expected_attendance     REAL NOT NULL,        -- sum of probabilities
    predicted_attendees     INTEGER NOT NULL,     -- count at or above threshold
    actual_attendees        INTEGER NOT NULL,
    error                   REAL NOT NULL,        -- expected - actual (signed)
    absolute_error          REAL NOT NULL,
    percentage_error        REAL,                 -- |error| / actual x 100 (NULL when actual = 0)
    count_error             INTEGER NOT NULL,     -- predicted_attendees - actual
    accuracy                REAL,                 -- per-registration metrics (NULL for headcount)
    balanced_accuracy       REAL,
    brier                   REAL,
    roc_auc                 REAL,
    tn INTEGER, fp INTEGER, fn INTEGER, tp INTEGER,
    target_is_simulated     INTEGER NOT NULL DEFAULT 0 CHECK (target_is_simulated IN (0,1)),
    notes                   TEXT
);

CREATE TABLE prediction_evaluation_items (
    evaluation_id   INTEGER NOT NULL REFERENCES prediction_evaluations(evaluation_id) ON DELETE CASCADE,
    prediction_id   INTEGER NOT NULL REFERENCES predictions(prediction_id) ON DELETE RESTRICT,
    attendance_id   INTEGER REFERENCES attendance_records(attendance_id) ON DELETE RESTRICT,
    PRIMARY KEY (evaluation_id, prediction_id)
);

-- ---------------------------------------------------------------- audit log
CREATE TABLE audit_log (
    log_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    action     TEXT NOT NULL,
    entity     TEXT,
    entity_id  TEXT,
    details    TEXT
);
