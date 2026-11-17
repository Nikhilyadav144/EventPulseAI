# EventPulse AI — PostgreSQL Compatibility Report (Phase 1)

*Personal academic / portfolio project. Phase 1 prepares the code for an optional PostgreSQL
database (for example Supabase Free). Nothing was migrated, uploaded or deployed. The existing
SQLite database, the accounts and the model artifacts were not modified.*

**Status in one line:** the app now runs on either engine. PostgreSQL support was **tested**
against a real PostgreSQL 16.15 server (a temporary, socket-only cluster holding synthetic data
only), with one limitation: those tests used a test-only libpq driver. The production driver
(`psycopg`) is **not installed and not tested**. Installing it needs your approval.

---

## 1. Current architecture (unchanged by default)

```
Desktop app (PySide6) ─┐
                       ├─ core/ (repositories, services, analytics, check-in, reports)
Web API (FastAPI) ─────┘        │
                                └─ core/db/connection.Database ──► SQLite  database/eventpulse.db
                                                               └─► PostgreSQL (NEW, only if a URL is given)
```

* With no `EVENTPULSE_DB_URL` set, every code path behaves exactly as before: SQLite, WAL mode,
  `BEGIN IMMEDIATE`, file backups before migrations, and core schema version 3.
* PostgreSQL is used only when a `postgresql://` URL is passed to `Database(...)` or set in
  `EVENTPULSE_DB_URL` for the web server.
* The desktop never sets that URL, and the PostgreSQL module is never imported on the SQLite
  path (this is checked by a test).

## 2. Design

A small adapter, `core/db/postgres.py`, gives PostgreSQL the same tiny sqlite3-style interface
the code already uses (`execute`, `executemany`, `executescript`, `fetchone/fetchall`,
`lastrowid`, `rowcount`, row access by name). Business logic is therefore **not duplicated**.
The adapter does only the following:

| SQLite behaviour the code relies on | Adapter / schema handling on PostgreSQL |
|---|---|
| `?` placeholders | rewritten to `%s` (outside quotes and comments); a literal `%` becomes `%%` |
| `cursor.lastrowid` | `INSERT … RETURNING *`; the first column is the id |
| `BEGIN IMMEDIATE` | `BEGIN` |
| `PRAGMA …` (connection set-up) | no-op; PostgreSQL always enforces foreign keys |
| `sqlite3.Row` | `Row`: index, name, `keys()`, `dict(row)` |
| SQLite errors → friendly exceptions | SQLSTATE and constraint name → the same classes (`DuplicateRecordError`, `ReferenceError_`, `ConstraintViolationError`, `HistoryProtectedError`, …) |
| Booleans stored as 0/1, `13.0` stored in INTEGER columns, NaN stored as NULL | parameters normalised the same way (`True`→1, whole floats→int, NaN→NULL, numpy→Python, dates→ISO text) |
| `AVG`/`ROUND` return floats | psycopg `numeric` values are loaded as `float` |

Connections run in autocommit mode with explicit `BEGIN`/`COMMIT`/`SAVEPOINT`, exactly like the
SQLite connection (`isolation_level=None`), so the existing nested `transaction()` logic is
unchanged. Prepared statements are disabled (`prepare_threshold=None`) so the same code also
works behind Supabase's transaction pooler.

## 3. SQLite-specific dependencies found, and what was done

| Where | SQLite-specific feature | Change (same result on SQLite) |
|---|---|---|
| `core/db/sql_migrations/*.sql` | AUTOINCREMENT, `REAL`, `strftime` defaults, `date()`/`time()`/`strftime()` CHECKs, `GLOB` privacy CHECKs, `RAISE(ABORT)` trigger, boolean CHECK | **separate PostgreSQL scripts** in `sql_migrations/postgres/` (SQLite files untouched, checksums unchanged) |
| `webapp/auth_migrations/0001_accounts.sql` | `COLLATE NOCASE UNIQUE`, `WHEN` trigger | PostgreSQL script with `lower()` unique indexes and a plpgsql trigger, plus a rollback script |
| `core/db/migrations.py` | `sqlite_master`, `PRAGMA user_version`, file backup | uses `db.list_tables()`. PG scripts run as one transaction. No file backup on PG: instead, upgrading a PG database that already holds data **refuses** unless `EVENTPULSE_PG_BACKUP_CONFIRMED=1` (take a `pg_dump` first) |
| `core/db/connection.py` | `sqlite3.Error` only, PRAGMA integrity check | catches `DB_ERRORS`; `integrity_check()` returns `[]` on PG; new `dialect`, `display_name`, `list_tables()`, `table_exists()` and `open_database()` |
| `webapp/auth.py` | `sqlite_master`, `COLLATE NOCASE` comparisons, file backups | `table_exists()`; `lower(x) = lower(?)` for e-mail and Student ID matching; backups only on SQLite; the PG upgrade and rollback need the backup confirmation |
| `webapp/server.py` | `GLOB 'EV[0-9]*'`; DB file name in `/api/health` and settings; in-app backup | `LIKE 'EV%'` plus a digit filter in Python. Health and settings show a **redacted** host/database (never credentials). The backup endpoint returns 400 on PG. The database is resolved at call time (`db_target()`) |
| `core/workflow.py`, `core/db/repositories.py` | case-insensitive `LIKE`; `SUM(boolean)` | `lower(a) LIKE lower(?)`; `SUM(CASE WHEN … THEN 1 ELSE 0 END)` |
| `core/analytics.py` | `SUM(boolean)`; `ROUND(double, n)` | `CASE`; `ROUND(CAST(x AS NUMERIC), n)` |
| `core/intelligence.py`, `core/analytics.py`, `core/db/services.py` | unquoted mixed-case view columns (`f.Event_ID`) | quoted (`f."Event_ID"`); identical on SQLite |
| `core/checkin.py` | `CAST(text AS INTEGER)` join on audit rows | compares as text (`entity_id = CAST(id AS TEXT)`) |
| `webapp/provision_admin.py` | SQLite path only | `--db-url` / `EVENTPULSE_DB_URL` (or a URL as `--db`) |
| `core/db/backup.py` | SQLite online-backup API | **unchanged; SQLite only**. On PostgreSQL use the provider's backups or `pg_dump` |
| `gui/` (desktop) | `SUM(status='Registered')` etc. | **unchanged on purpose**: the desktop only uses SQLite |
| `core/analytics.py` MLflow reader | `sqlite3.connect(mlflow.db)` | unchanged; this reads a separate local file, not the app database |

### Schema translation (PostgreSQL scripts)

* `INTEGER PRIMARY KEY AUTOINCREMENT` → `INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY`.
* `REAL` → `DOUBLE PRECISION`. PostgreSQL's `REAL` is only 4 bytes and would change stored
  probabilities. A test asserts that no `real` columns exist.
* Timestamps stay **ISO text** (`'YYYY-MM-DDTHH:MM:SSZ'`), exactly as on SQLite. Defaults use
  `ep_utc_now()`.
* Validity checks use small IMMUTABLE helper functions:
  * `ep_is_iso_date` (equivalent to `date(x) = x`)
  * `ep_is_time`
  * `ep_is_timestamp`
  * `ep_weekday` (the weekday must match the date)
* Privacy CHECKs use regular expressions:
  * phone-like: `!~ '[0-9]{10}'`
  * roll-number-like: `!~ '^[0-9]{2}[A-Za-z]{2}.*[0-9]{3}$'`
  * e-mail: `NOT LIKE '%@%'`
* `CHECK (predicted_attend = (probability >= threshold))` becomes a `CASE` expression.
* The append-only prediction trigger and the role-immutability trigger are plpgsql functions
  with the same messages.
* Mixed-case view columns are quoted, so `SELECT *` returns exactly the same column names as on
  SQLite (tested).

### Known behavioural differences (documented, not bugs found by tests)

* SQLite's `time()` also accepts Julian day numbers and words such as `now`. The PG check
  accepts `HH:MM[:SS[.fff]]` and ISO date-times only. The app only ever writes `HH:MM`.
* A whole float sent to a TEXT column would be stored as `13`, not `13.0`. No code path does
  this.
* `NULL` ordering differs between engines. Every query whose order matters orders by non-null
  keys or uses `COALESCE` (checked by the parity tests).
* IDENTITY columns: if rows are ever bulk-copied **with** explicit ids, the sequences must be
  advanced (`setval`) afterwards. This belongs to a future data migration, not Phase 1.

## 4. Environment variables

| Variable | Used by | Meaning |
|---|---|---|
| `EVENTPULSE_DB` | web, provision, check-in | SQLite file (default `database/eventpulse.db`); unchanged |
| `EVENTPULSE_DB_URL` | web, provision, seeder | **new**: PostgreSQL URL; unset ⇒ SQLite |
| `EVENTPULSE_PG_BACKUP_CONFIRMED` | migrations | **new**: `1` = "I have taken a `pg_dump`", needed before upgrading a PG database that holds data |
| `EVENTPULSE_SECURE_COOKIES` | auth | existing: `1` on HTTPS (Secure cookie flag) |
| `EVENTPULSE_SESSION_IDLE_MINUTES`, `EVENTPULSE_SESSION_HOURS` | auth | existing session lifetimes |
| `EVENTPULSE_TEST_ENGINE`, `EVENTPULSE_TEST_PG_URL` | tests only | run the suite on PostgreSQL; local servers only |

Secrets (the database URL with its password) are read only from the environment. They are never
logged or written to files, and the API shows only `postgresql://host:port/dbname` (tested).

## 5. Security review on PostgreSQL

All of these were tested on PostgreSQL (`tests/test_auth.py` in PostgreSQL mode, plus
`tests/test_pg_compat.py`):

* **Role isolation:** students get 403 on every admin route (≥ 40 routes enumerated).
  Unauthenticated users get 401.
* **Database-level rules:** a single-admin unique index, a role-immutability trigger, and
  case-insensitive unique e-mail and Student ID.
* **Passwords:** salted scrypt/argon2 hashes only; the plaintext is never stored.
* **Sessions:** server-side; only a SHA-256 hash of the token is stored; idle and absolute
  expiry; logout invalidates; replayed cookies fail; deactivation signs the user out.
* **CSRF:** a token is required for changes.
* **Cookies:** HttpOnly and SameSite=Strict. The Secure flag is set with
  `EVENTPULSE_SECURE_COOKIES=1`.
* **Login:** generic login errors; case-insensitive e-mail login (same as the SQLite NOCASE
  column).
* **Student data isolation:** tampered parameters do not reveal other students. Unverified
  claimed IDs see nothing.
* **Rate limiting:** login and check-in limits are enforced. **Design note:** the limiter is
  **in-memory, per process**. That is correct for one Render instance, but it resets on restart
  and is not shared between instances or serverless invocations. A DB-backed limiter (a table
  `auth_rate_events(key, ts)` with pruning) is the recommended Phase 2 change. It was **not**
  implemented now, to avoid changing authentication behaviour in this phase.
* **Client IP behind a proxy:** `client_ip()` uses the socket peer. Behind Render's proxy all
  users would share one IP for the per-IP limits. Phase 2: run uvicorn with `--proxy-headers`
  and a restricted `--forwarded-allow-ips`.
* Nothing in authentication was weakened.

## 6. Test results (exact)

Environment: container, Python 3.13, PostgreSQL **16.15** temporary cluster
(`initdb`, trust auth, Unix socket only, no TCP port, synthetic data only). Driver for these
tests: `tests/pg_libpq.py` (ctypes over the system libpq).

| Run | Result |
|---|---|
| Full suite, default engine SQLite (+ PG parity tests) | **242 passed, 1 skipped** (desktop GUI test: PySide6 not installed in the container) |
| Full suite with `EVENTPULSE_TEST_ENGINE=postgresql` | **189 passed, 54 skipped, 0 failed** |
| `tests/test_pg_compat.py` alone | **36 passed** |
| **Mac**, `EventPulse_Web_Verify.command` (project `.venv`, Python 3.12, desktop GUI tests included) | **238 passed, 21 skipped, 0 failed** (before Phase 1: 223 passed). The 21 skips are the PostgreSQL tests: there is no PG server or driver on the Mac. The Mac database checksum was unchanged |

Baseline before Phase 1: 206 passed, 1 skipped (container). The checksum of
`database/eventpulse.db` was verified unchanged after every run.

### 6.1 PostgreSQL-mode run

`EVENTPULSE_TEST_ENGINE=postgresql pytest`: every test that uses the `db` fixture, the web API
suite (`test_webapp.py`, 8 tests) and the whole authentication/RBAC suite (`test_auth.py`) ran
against fresh PostgreSQL databases.

**189 passed, 54 skipped, 0 failed.** The skips, exactly:

* **44** tests need `seeded_db`, which loads the survey-derived processed dataset. In PG mode
  they are skipped so that only synthetic data reaches PostgreSQL. Their behaviour is covered
  on synthetic data by `test_pg_compat.py`.
* **9** check SQLite internals: PRAGMA, `sqlite_master`, file backups, and the SQLite
  auth-migration backup/rollback test.
* **1** is the desktop GUI test (PySide6 is not installed in the container).

Problems these runs found and fixed:
* `SUM(boolean)` (2 places in analytics);
* unquoted mixed-case view columns (2 in test SQL, plus `services.registration_features`);
* `13.0` sent to INTEGER columns from pandas (fixed by the adapter's normalisation);
* the import-time database resolution (§8).

### 6.2 What `tests/test_pg_compat.py` covers

* **Translation units:** placeholders inside quotes and comments, `%` escaping, RETURNING,
  PRAGMA, parameter normalisation, rows, URL redaction, refusal of remote test servers.
* **Desktop compatibility:** a SQLite database opened without a URL is core schema 3,
  `user_version` 3, WAL and foreign keys on, and the PG module is not loaded. This holds even
  with `EVENTPULSE_DB_URL` set in the environment.
* **Schema parity:**
  * identical tables, columns (in order) and view column names;
  * no 4-byte floats and no personal-data columns;
  * idempotent re-open;
  * a failed migration rolls back completely;
  * an upgrade of a database with data is refused without backup confirmation.
* **Constraint parity:** 12 kinds of bad data raise the **same exception class** on both
  engines:
  * e-mail, phone-like and roll-number-like IDs;
  * an impossible date, a non-ISO date, a wrong weekday, a bad time;
  * a duplicate registration, an unknown student, the raw sheet;
  * an out-of-range boolean, deleting an event in use.

  Also on both engines: append-only predictions, and the threshold-consistency check.
* **Transactions:** a savepoint rollback inside a committed transaction; an outer rollback; a
  failed import leaves nothing behind.
* **Same synthetic world on both engines, compared result by result:**
  * import counts and the full feature view (row by row);
  * case-insensitive search;
  * department participation;
  * prediction probabilities (equal to 1e-12) and history/latest-view behaviour;
  * overview KPIs, 7 participation dimensions, event trends, the probability distribution,
    model comparison and recent predictions;
  * insights and the what-if planner.
* **Attendance:** QR and desk check-in, duplicates and wrong tokens, finalise, correction log
  and protection of simulated outcomes (same results on both engines).
* **Reports:** PDF (≥ 4 pages) and Excel workbooks generated from PostgreSQL.
* **Web API on PostgreSQL:** case-insensitive admin login; redacted DB info in `/api/health`
  and settings; integrity "ok"; the backup endpoint refused; event-id generation without
  `GLOB`.
* **Synthetic demo seeder:** builds a SYNTHETIC-only demo database on both engines; is
  idempotent; refuses the real database and databases that already hold data.

### 6.3 What is NOT verified

* **psycopg (the production driver) has not been run.** It is not installed, and installing it
  needs your approval. Its code path (`_PsycopgRaw`) mirrors the test driver's contract. The
  first Phase 2 step is to install `psycopg[binary]` in a separate virtual environment and run
  `EVENTPULSE_TEST_ENGINE=postgresql pytest`.
* **No Supabase or other managed server was contacted.** Collation (`en_US.UTF-8` vs `C`),
  SSL, the poolers (ports 5432/6543) and Supabase's role permissions are unverified.
* **The desktop GUI tests** (PySide6) did not run in the container. They ran and passed on the Mac.
* **Concurrency** (several workers writing at once) was not load-tested on PostgreSQL.

## 7. Files

**Created**
* `core/db/postgres.py` — adapter, psycopg driver, URL redaction.
* `core/db/sql_migrations/postgres/0001_initial_schema.sql`, `0002_indexes_and_views.sql`,
  `0003_event_workflow.sql`.
* `webapp/auth_migrations/postgres/0001_accounts.sql`, `rollback_0001_accounts.sql`.
* `scripts/seed_demo_database.py` — synthetic demo database (SQLite or PostgreSQL).
* `tests/pg_libpq.py` — test-only libpq driver.
* `tests/pg_support.py` — local throw-away PG databases.
* `tests/test_pg_compat.py` — 36 tests.
* `docs/POSTGRESQL_COMPATIBILITY_REPORT.md` and `docs/FREE_DEPLOYMENT_READINESS.md`.

**Modified**
* `core/db/errors.py`, `core/db/connection.py`, `core/db/migrations.py`.
* `core/workflow.py`, `core/analytics.py`, `core/intelligence.py`, `core/checkin.py`,
  `core/db/repositories.py`, `core/db/services.py`.
* `webapp/auth.py`, `webapp/server.py`, `webapp/provision_admin.py`.
* Tests:
  * `tests/conftest.py` — engine switch and `sqlite_only` marker.
  * `tests/test_auth.py`, `tests/test_webapp.py` — PG mode uses synthetic databases.
  * `tests/test_db_repositories.py` — quoted two mixed-case view columns in test SQL.
  * `tests/test_db_schema.py`, `tests/test_db_backup.py`, `tests/test_v12_workflow.py` —
    `sqlite_only` marks on checks of SQLite internals.

  No assertion was weakened.

**Deliberately not changed:**
* the SQLite migration files (checksums are verified at start-up);
* `core/db/backup.py`;
* the desktop GUI;
* `checkin_app.py`;
* the UI and web frontend;
* the model artifacts and the real database;
* the dependency files.

## 8. One incident during development (fixed)

While I was wiring the server, an intermediate version resolved the database **at import
time**, so the tests' override of `DB_PATH` had no effect. One test run therefore wrote test
accounts into the **container's working copy** of `database/eventpulse.db`.

* The Mac's real database was not involved: the container has no connection to it.
* The copy was restored from the verified backup the app had taken just before
  (`pre_auth_migration`, integrity "ok", identical core row counts).
* The code now resolves the database on every call (`db_target()`).
* From then on, a checksum check was run before and after every test run.

The fix was in place before any file was copied to the Mac. The Mac's original versions of all
modified files are kept in `../EventPulseAI_phase1_originals/originals_before_phase1.tar` for
rollback.
