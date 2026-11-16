-- EventPulse AI web authentication (web-only migration track, recorded in web_auth_migrations).
-- Additive only: creates new tables, never touches the core schema (schema_migrations stays at v3,
-- so the desktop app keeps opening the database). Reverse with rollback_0001_accounts.sql.

CREATE TABLE users (
    user_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email            TEXT NOT NULL COLLATE NOCASE UNIQUE
        CHECK (length(email) BETWEEN 3 AND 254 AND email LIKE '%_@_%'),
    full_name        TEXT NOT NULL CHECK (length(trim(full_name)) BETWEEN 2 AND 100),
    role             TEXT NOT NULL CHECK (role IN ('admin', 'student')),
    -- Student ID the student CLAIMED at registration. It only unlocks records after an
    -- administrator verifies it (link_status = 'verified'); nothing is linked automatically.
    student_id       TEXT COLLATE NOCASE UNIQUE CHECK (student_id IS NULL OR length(student_id) BETWEEN 2 AND 40),
    link_status      TEXT CHECK (link_status IS NULL OR link_status IN ('pending', 'verified', 'rejected')),
    password_hash    TEXT NOT NULL CHECK (length(password_hash) >= 40),
    is_active        INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_utc      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_utc      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    last_login_utc   TEXT,
    CHECK ((role = 'admin' AND student_id IS NULL AND link_status IS NULL)
        OR (role = 'student' AND link_status IS NOT NULL))
);

-- exactly one administrator can ever exist
CREATE UNIQUE INDEX ux_users_single_admin ON users(role) WHERE role = 'admin';

-- a role can never be changed after the account is created (no privilege escalation by UPDATE)
CREATE TRIGGER trg_users_role_immutable BEFORE UPDATE OF role ON users
WHEN NEW.role <> OLD.role
BEGIN
    SELECT RAISE(ABORT, 'account roles cannot be changed');
END;

-- server-side sessions: only a SHA-256 hash of the cookie token is stored
CREATE TABLE auth_sessions (
    session_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash       TEXT NOT NULL UNIQUE CHECK (length(token_hash) = 64),
    user_id          INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    csrf_token       TEXT NOT NULL CHECK (length(csrf_token) >= 32),
    created_utc      TEXT NOT NULL,
    last_seen_utc    TEXT NOT NULL,
    expires_utc      TEXT NOT NULL
);
CREATE INDEX ix_auth_sessions_user ON auth_sessions(user_id);

-- security audit trail (no passwords, tokens or e-mail addresses of unknown accounts)
CREATE TABLE auth_audit (
    audit_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc           TEXT NOT NULL,
    user_id          INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    action           TEXT NOT NULL,
    detail           TEXT
);
