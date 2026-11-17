# EventPulse AI — Production Migration Plan (Render + PostgreSQL)

**Date:** 16 November 2026 · **Status:** plan only. No code, database, account or deployment was changed while preparing it.
**Inputs:**
- `docs/DEPLOYMENT_READINESS_AUDIT.md` (43 findings).
- A read-only inspection of the code on the Mac.
- Measurements taken on the cloud workspace's own copy of the code, with an older local database copy (no real data left the Mac).
- Platform documentation, checked 16 November 2026.

**Companion document:** `docs/VERCEL_SUPABASE_MIGRATION_PLAN.md` evaluates the Vercel + Supabase preference and makes the final recommendation. **Both plans share sections C–F and H–J**: the database port, the desktop strategy, private file storage, security changes, backups, staging and launch. Only the hosting layer (section G) differs.

**Target:**
- Render for the complete web application.
- Render PostgreSQL for relational data.
- Private storage for ML artifacts and data files.
- HTTPS on a custom domain.
- Reachable from college Wi-Fi and the public internet.
- The Mac desktop app keeps working on its own SQLite database.

---

## A. Current architecture and exact entry points

### A.1 Entry points (verified in source)

| Component | Entry point | Started by | Notes |
|---|---|---|---|
| **Web app (backend + frontend)** | `webapp/server.py` → `app` (FastAPI) | `uvicorn webapp.server:app` via `EventPulse_Web.command:51` (127.0.0.1:8100) | Serves `/api/*`, `/checkin/*`, the React build `web/dist` (SPA fallback `server.py:818`) |
| Auth module | `webapp/auth.py` → `Auth(get_db)` routers `/api/auth`, `/api/admin`, `/api/student` | included by `server.py:806-809` | Sessions, CSRF, rate limits, auth migration track |
| Admin provisioning | `python -m webapp.provision_admin` | `EventPulse_Admin_Setup.command` | Interactive (getpass) or `--from-env` |
| Frontend | `web/src/main.jsx` → `App.jsx` | built to `web/dist` by `web/build_esbuild.mjs` (or Vite) | Hash routes; 16 admin + 4 student pages |
| Public prediction API (CSL236) | `app.py` → `app` | `Dockerfile` CMD `uvicorn app:app`; `render.yaml`; CI | **The only service the current deployment config ships** |
| Standalone QR server | `checkin_app.py` → `app` | `EventPulse_CheckIn_Server.command` (0.0.0.0:8010) | Desktop's LAN check-in |
| Desktop app | `main.py` → `gui/` (PySide6) | `EventPulse AI.app` | Uses `core/` directly on `database/eventpulse.db` |
| Training | `train.py`, `core/model_training.py` | `EventPulse_EndTerm_Run.command`, CI | Writes `model/`, `models/`, `mlflow.db` |

`webapp/server.py` imports `app` (for the `Registration` schema and `organiser_tips`) and `checkin_app` (for the check-in HTML handlers). Both must ship with the web app.

### A.2 Pages and the endpoints behind them

**Admin (16 pages):** Overview, Dataset Explorer, Event Management, Event Calendar, Registration Management, Attendance Prediction (individual / whole event / bulk file / What-if), Attendance Records (QR + desk), Students, Model Laboratory, Analytics, Insights, Model Monitoring, Reports & Export, Student Accounts, Notifications, Settings.
- Served by 44 routes on the admin router in `webapp/server.py` and 4 routes in `/api/admin/*` (`webapp/auth.py`).

**Student (4 pages):** My dashboard, My registrations, My attendance, My predictions.
- Served by 5 routes in `/api/student/*`.

**Public:**
- `/api/health`
- `/api/auth/{register,login,logout,me}`
- `/checkin/{event_id}` (GET/POST, needs the event's QR token)
- static frontend.

### A.3 State the web app depends on (verified)

| State | Where | Used by |
|---|---|---|
| Relational data | `database/eventpulse.db` (SQLite, WAL; 279 students, 36 events, 383 registrations, 383 attendance, 58 predictions, 4 users) | Everything; shared with the desktop app |
| Model registry | `models/<name>/model.joblib + metadata.json` (5 versions, 1.1 MB; `model_versions.artifact_path`) | Predictions, Model Lab, What-if (`core/db/services.py:175-182`) |
| Serving model | `model/` (32 KB) | `app.py`, `/api/models` (MLflow run marker, `server.py:665`) |
| MLflow history | `mlflow.db` (SQLite, 1.1 MB, 9 runs; read by `core/analytics.py:430-450`) | Model Lab "MLflow runs" |
| Reference data | `data/processed/ml_dataset_processed.csv` (**survey-derived, private**), `data/synthetic/*`, `data/event_attendance.csv` | Dataset Explorer, ROC (`analytics.py:272`), drift (`intelligence.py:281-285`) |
| Logs read back by features | `logs/predictions.jsonl`, `logs/rejected.jsonl` (`server.py:69-70`, `intelligence.py:297, 331`) | Model Monitoring |
| Files written | `exports/web/` (reports, `server.py:771`), temp uploads (`server.py:181`), `database/backups/` (`server.py:802`, `auth.py:118`) | Reports, imports, backup button |
| In-memory | rate limiters (`auth.py:247-276`), auth-schema cache (`auth.py:154`) | Login, check-in |
| Never deployable | `data/original/` (real survey workbook incl. personal data) | Desktop import only |

### A.4 Measured runtime profile

Measured in the cloud workspace on Python 3.13, with the same code and its own database copy. Treat these as estimates.

**Start-up and memory:**
- Cold import of `webapp.server`: **1.0–3.0 s**.
- Memory after import: **≈167 MB**; peak **≈255 MB** while generating the PDF report.
- Heavy modules loaded at import: numpy, pandas, scipy, scikit-learn, joblib. ReportLab, openpyxl and matplotlib load only when needed.

**Model load times (per load):**

| Model | Time |
|---|---|
| Logistic Regression | 2 ms |
| LR baseline | 6 ms |
| Random Forest | 31 ms |
| Gradient Boosting | 95 ms |
| Decision Tree | 161 ms |

**Endpoint and prediction latencies (SQLite):**

| Operation | Time |
|---|---|
| Stored individual prediction (includes a model load) | 68 ms |
| Prediction with an already-loaded model | 32 ms |
| Overview | 9 ms |
| Analytics | 150 ms |
| Model list + MLflow | 242 ms |
| PDF report | **≈1.0 s** (478 KB) |
| Excel report | 200 ms |

**Sizes:**
- Largest export (registrations CSV): 47 KB.
- Runtime dependencies on Linux: **≈370 MB**, or **≈400 MB** with matplotlib and Pillow (needed for PDF charts).
  - scipy 107 MB + 31 MB libs, pandas 68 MB, sklearn 46 MB, numpy 41 MB + 28 MB libs.
  - matplotlib 34 MB, Pillow 21 MB, ReportLab 8 MB.
- Application files: models 1.1 MB, `web/dist` 336 KB, data files ≈0.5 MB.

---

## B. Proposed production architecture

```
Phones (college Wi-Fi / mobile data)       Organiser & students (browsers)
              │  HTTPS  https://events.<domain>  (custom domain, Render-managed TLS)
              ▼
┌──────────── Render Web Service "eventpulse-web" (Docker, always-on, 1 instance) ─────────────┐
│ uvicorn webapp.server:app  --proxy-headers --forwarded-allow-ips=<Render proxy>              │
│  • React build (web/dist) + /api/* + /checkin/*      • EVENTPULSE_ENV=production             │
│  • DB-backed sessions, CSRF, rate limits             • model cache (verified SHA-256)        │
│  • reads private artifacts from /var/data (persistent disk) or object storage at start-up    │
└───────────────┬──────────────────────────────────────────────────┬───────────────────────────┘
                │ TLS, private network                             │ read-only at start-up
                ▼                                                  ▼
   Render PostgreSQL "eventpulse-db"                   Private artifact store
   (system of record for the web app;                  (models/, model/, mlflow export,
    daily backups; PITR on higher plans)                data/processed + synthetic + end-term CSV)
                ▲
                │ one-off migration tool (SQLite → PG) and optional nightly PG → SQLite snapshot
   Mac desktop app (PySide6) on its own local SQLite database — independent, as today
```

### Service responsibilities

| Service | Responsibility | Not responsible for |
|---|---|---|
| `eventpulse-web` (Render web service) | All 16 admin and 4 student pages, the auth API, public `/checkin`, predictions with the registered models, reports | Training models; storing durable files on its own disk (except a mounted disk, if chosen) |
| Render PostgreSQL | All relational data, accounts, sessions, rate-limit counters, prediction logs, auth audit | Model binaries |
| Private artifact store | Model folders, reference CSVs, MLflow run export; read-only for the service | Anything public |
| Optional `eventpulse-api` (existing `app.py` image) | Public CSL236 prediction API if still required for the course | Accounts, real data |
| Mac desktop | Local organising, training, demo mode, local QR server on LAN | Production data |

**Not public, ever:**
- `data/original/`
- database files and dumps
- password hashes
- `.env` values
- backups

---

## C. SQLite → PostgreSQL migration strategy

### C.1 Approach: one schema, two dialects, same repository code

The desktop must keep SQLite, and the web app moves to PostgreSQL. So `core/db` gets a **thin dialect layer** instead of a rewrite. Repositories and services keep their SQL, written in the portable subset.

The inspection showed the runtime SQL is mostly portable already: dates are computed in Python and stored as ISO text. **About 25 sites need changes:**

| SQLite-specific construct | Locations (verified) | PostgreSQL replacement |
|---|---|---|
| `?` placeholders | all `db.execute/query/scalar` calls (≈210 call sites, 12 files) | Translate `?`→`%s` once, inside `Database.execute` (psycopg) |
| `sqlite3.Row`, `PRAGMA foreign_keys/busy_timeout/journal_mode/synchronous` | `core/db/connection.py:21-25` | `psycopg` `dict_row`; foreign keys always on in PG; statement timeout |
| `BEGIN IMMEDIATE` / `SAVEPOINT` | `connection.py:67-69`, `core/db/migrations.py:107`, `webapp/auth.py:123,143` | `BEGIN` + `SAVEPOINT` (supported); keep the nesting logic |
| `cur.lastrowid` | `core/db/repositories.py:80,106,357,395,491,560,578,641,696`, `webapp/auth.py:314,548` | `INSERT … RETURNING <id>`, via a helper `db.insert_returning()` |
| `sqlite_master` | `core/db/migrations.py:71`, `webapp/auth.py:104`, `core/db/backup.py:39` | `information_schema.tables` / `to_regclass()` |
| `PRAGMA integrity_check / foreign_key_check / user_version` | `connection.py:120-121`, `migrations.py:112`, `backup.py:38` | PG: `amcheck`-free check (counts + FK-validated constraints); skip `user_version` |
| `COLLATE NOCASE` | `webapp/auth.py:605,619`; `auth_migrations/0001_accounts.sql` (email, student_id) | `lower()` unique indexes (`CREATE UNIQUE INDEX … ON users (lower(email))`) or `citext` |
| `GLOB 'EV[0-9]*'` | `webapp/server.py:317` | `event_id ~ '^EV[0-9]+'` (or compute in Python) |
| `ON CONFLICT … DO UPDATE … excluded` | `repositories.py:60,667`, `core/checkin.py:66` | Same syntax in PG ✔ |
| `sqlite3.IntegrityError/OperationalError` | `core/db/errors.py:59-73`, `webapp/auth.py:549` | Map `psycopg.errors.UniqueViolation / ForeignKeyViolation / CheckViolation` to the same `DatabaseError` subclasses |
| `sqlite3` online backup API | `core/db/backup.py` (desktop) | Desktop only; PG uses `pg_dump` / provider backups (section H) |
| MLflow `mlflow.db` read via `sqlite3` | `core/analytics.py:430-450` | Export runs to a JSON/CSV artifact at release time; read that in production |

### C.2 Schema port (`core/db/sql_migrations/postgres/0001…0003`, `webapp/auth_migrations/postgres/0001`)

**Tables affected (all 21):**
- **Core:** `app_settings`, `datasets`, `courses`, `students`, `events`, `weather_forecasts`, `registrations`, `model_versions`, `model_metrics`, `prediction_runs`, `predictions`, `attendance_records`, `event_outcomes`, `prediction_evaluations`, `prediction_evaluation_items`, `audit_log`, `checkin_tokens`, `checkins`.
- **Auth:** `users`, `auth_sessions`, `auth_audit`.
- **Migration tracking:** `schema_migrations`, `web_auth_migrations`.

**Views (4):** `v_latest_forecast`, `v_registration_features`, `v_latest_predictions`, `v_event_participation`.

| SQLite DDL | PostgreSQL DDL |
|---|---|
| `INTEGER PRIMARY KEY AUTOINCREMENT` (15) | `BIGINT GENERATED BY DEFAULT AS IDENTITY` (ids are copied explicitly, then sequences advanced with `setval`) |
| `TEXT` dates with `CHECK (date(x) = x)` / `strftime('%w', …)` | Keep `TEXT` for byte-identical behaviour. Use `CHECK (x ~ '^\d{4}-\d{2}-\d{2}$' AND to_date(x,'YYYY-MM-DD') IS NOT NULL)` and an `event_day` check via `trim(to_char(to_date(event_date,'YYYY-MM-DD'),'Day'))`. *(Option: native `DATE` later)* |
| `GLOB` student-ID privacy checks (phone-like, roll-like) | `!~` regular expressions with the same patterns |
| `DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))` (22) | `DEFAULT to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"')` |
| `CREATE TRIGGER … RAISE(ABORT)` (predictions append-only; role immutable) | PL/pgSQL trigger functions raising `EXCEPTION` |
| Partial unique index `ux_users_single_admin` | Same syntax ✔ |
| 0/1 integer flags | `SMALLINT` + the same `CHECK (x IN (0,1))`; Python keeps ints |

### C.3 Data migration tool (new: `scripts/migrate_sqlite_to_postgres.py`)

1. Open the SQLite source **read-only** (`mode=ro` URI, online-backup snapshot first).
2. Check the target is empty and at the expected schema.
3. Copy tables in foreign-key order inside **one transaction**, keeping primary keys, then `setval` the identity sequences.
4. **Verify:**
   - per-table row counts;
   - per-table SHA-256 of canonicalised rows (sorted by primary key);
   - the 5 model `artifact_sha256` values;
   - foreign-key validity;
   - spot queries such as the overview KPIs being identical.
5. `--dry-run` (default) writes nothing. `--commit` requires `--confirm <target host>`.

**Accounts are migrated as hashes, never re-hashed.** The scrypt strings are portable. Sessions are **not** migrated, so everyone signs in again after cut-over.

### C.4 Per-change summary

| Change | Files to modify / add | Tables | Dependencies | Risks / compatibility | Tests before → after |
|---|---|---|---|---|---|
| Dialect layer | `core/db/connection.py`, `core/db/errors.py`, `core/db/migrations.py`, `core/db/repositories.py` (RETURNING), `webapp/auth.py` (migration helpers, `COLLATE NOCASE`), `webapp/server.py:317` | none (code) | `psycopg[binary]>=3.2` (web only); none for the desktop | Breaking the desktop's SQLite path; subtle type differences (bool/int, text dates) | Full suite on SQLite stays at 223 passed → the same suite must pass on SQLite **and** on PostgreSQL (CI service container) |
| PG schema | `core/db/sql_migrations/postgres/*.sql`, `webapp/auth_migrations/postgres/0001_accounts.sql` | all 21 + 4 views + triggers | — | Constraint semantics (date checks, regexes) differ subtly | New `tests/test_schema_parity.py`: the same invalid inputs must be rejected by both engines (bad dates, roll-number IDs, second admin, role change, prediction update) |
| Migration tool | `scripts/migrate_sqlite_to_postgres.py`, `docs/DB_MIGRATION_RUNBOOK.md` | all (insert only) | psycopg | Partial copy; sequence drift; text encoding | Run against a **copy** of the database to a throwaway PG: counts + row hashes + KPI equality |
| MLflow export | `scripts/export_mlflow_runs.py`; `core/analytics.py:430` reads `mlflow_runs.json` when `mlflow.db` is absent | — | — | Stale export | Model Lab shows 9 runs from the export |

---

## D. Desktop and web database compatibility strategy

**Decision (recommended):** after cut-over, **PostgreSQL is the system of record for live web operations**: accounts, registrations, check-ins, attendance and predictions. The Mac desktop app **keeps its own SQLite database** and continues to work fully offline, exactly as today.

| Need | Mechanism |
|---|---|
| Desktop keeps working | No change to `gui/`; `core/db` keeps SQLite as the default dialect (`EVENTPULSE_DB_URL` unset → SQLite file) |
| Desktop sees production data (optional) | New `scripts/export_pg_to_sqlite_snapshot.py`: nightly or manual, produces a **read-only** SQLite snapshot (core schema v3, **without** `users`/`auth_sessions`/`auth_audit`), opened in the desktop's Settings → Restore. Never written back automatically |
| Desktop-created data → production | Not automatic. Explicit admin import via existing CSV/Excel import endpoints (`/api/registrations/import`), so there is no two-way sync and no conflicts |
| Schema evolution | Every future core migration ships as a pair (`sql_migrations/NNNN_*.sql` + `postgres/NNNN_*.sql`) with the same version number; the parity test enforces it |

**Risks:**
- The two systems drift. Mitigated by one-way snapshots and by documenting that the desktop copy is not authoritative after launch.
- Restoring an old snapshot on the desktop can never touch production.

**Tests:**
- Desktop GUI tests: `tests/test_gui.py`, 17 tests, must stay green.
- A new test opens a generated snapshot with `Database()` and checks the core schema is v3 with no auth tables.

---

## E. ML model and private file storage strategy

**Inventory.** Total ≈ 3 MB:
- `models/` (5 × model.joblib + metadata, 1.1 MB);
- `model/` (32 KB);
- `mlflow_runs.json` (export);
- `data/processed/ml_dataset_processed.csv` (84 KB, survey-derived, **private**);
- `data/synthetic/*` (160 KB);
- `data/event_attendance.csv` (248 KB);
- `assets/logo_mark.png` (PDF reports).

**Recommended storage:**
1. A private bucket: Render has no object store, so use Supabase Storage (private) or any S3-compatible bucket.
2. Holding a versioned **release bundle**: `eventpulse-artifacts-<version>.tar.gz` plus a `MANIFEST.json` listing each file's SHA-256.
3. On start-up the service downloads the bundle with a server-side credential, verifies **every SHA-256 against the manifest**, verifies each model's SHA-256 against `model_versions.artifact_sha256` in PostgreSQL, unpacks to a read-only directory, and refuses to start if anything mismatches.

**Simpler alternative (Render only):** a 1 GB persistent disk at `/var/data`, provisioned once by an encrypted transfer and verified by the same manifest check.

| Change | Files | Tables | Dependencies | Risks | Tests |
|---|---|---|---|---|---|
| Artifact root configurable | `config/settings.py` (`EVENTPULSE_ARTIFACT_DIR` overrides `MODELS_DIR`, `DATA_DIR`), `core/db/repositories.py` (`resolve_path`) | `model_versions` (read) | — | Paths in `artifact_path` are relative (`models/…`), so they are kept relative to the artifact root | Existing prediction tests with the artifact dir moved to a temp folder |
| Integrity on load + model cache | `core/model_registry.py:40-49` (verify SHA-256 before `joblib.load`), `core/db/services.py:175-182` (cache `Predictor` by `(model_version_id, sha)`), `core/analytics.py:279` | `model_versions` | — | Cache invalidation on activation | A tampered file must be refused; activation switches the model; latency test (second prediction does no disk load) |
| Bootstrap/download | new `webapp/artifacts.py`, `scripts/build_artifact_bundle.py` | — | `httpx` (already used) or `boto3` | Credential leakage | Bundle-verification test with a corrupted file |

**Rule:** `data/original/` is never bundled. The bundle builder has an allow-list and fails if any path under `data/original` appears.

---

## F. Authentication and production security changes

| # | Change (closes audit finding) | Files | Tables | Deps | Risk | Tests |
|---|---|---|---|---|---|---|
| F1 | `EVENTPULSE_ENV=production` fail-closed settings check (secure cookies, public base URL, allowed hosts, DB URL, artifact dir, admin exists) (C2, S1) | new `webapp/config.py`; `webapp/server.py` start-up | `users` (read) | — | Misconfiguration blocks start (intended) | Start-up refuses each missing setting |
| F2 | Proxy-aware client IP (S2) | `Dockerfile.web` CMD `--proxy-headers --forwarded-allow-ips`; `webapp/auth.py:284` | — | — | Trusting spoofed `X-Forwarded-For` if misconfigured | Test with a trusted vs untrusted proxy header |
| F3 | DB-backed rate limits (S2, B4) | `webapp/auth.py:247-276` → table-based limiter | **new `auth_rate_limits(key, window_start, count)`** | — | Write per attempt; small | Lockout persists across restart and across 2 workers |
| F4 | Lockout redesign: per-(account, IP) and progressive delay; optional CAPTCHA; alert on admin lockout (S4) | `webapp/auth.py` login | `auth_rate_limits`, `auth_audit` | (CAPTCHA provider optional) | UX | Attacker IP cannot lock the admin from another IP |
| F5 | Bound hashing memory: semaphore(2) or Argon2id m=19 MiB (S3) | `webapp/auth.py:168-207` | `users` (hash format both accepted) | `argon2-cffi` (optional) | Existing scrypt hashes must keep verifying | Old scrypt hash verifies; new hashes use the chosen scheme; 10 parallel logins stay under the memory budget |
| F6 | Security headers: CSP, HSTS (HTTPS only), `TrustedHostMiddleware`; disable `/docs` in production (S6) | `webapp/server.py:80-96` | — | CSP breaking inline styles | Header tests; `/docs` returns 404 in production |
| F7 | Public base URL for QR (S7) | `webapp/server.py:566-570` (`EVENTPULSE_PUBLIC_BASE_URL`) | `checkin_tokens` (read) | — | Wrong URL breaks phones | QR URL equals `https://<domain>/checkin/…`; phone test (I.4) |
| F8 | Registration control: allowed e-mail domain and/or event-organiser invite code (S5) | `webapp/auth.py:531-552`, `web/src/pages/AuthPages.jsx` | `users`, `app_settings` | — | Blocks legitimate external students | Wrong-domain registration rejected |
| F9 | Change password, admin TOTP MFA, session list/revoke (S9, S10) | `webapp/auth.py`, new pages | `users` (+`totp_secret_enc`), `auth_sessions` | `pyotp` | Lockout of the admin if TOTP is lost (recovery codes needed) | Full flow tests |
| F10 | Prediction/rejection logs into PostgreSQL instead of files (B9, A.3) | `webapp/server.py:69-70`, `core/intelligence.py:297,331`, `app.py:235-240` | **new `api_prediction_log`, `api_rejected_log`** | — | Monitoring query changes | Monitoring results identical to the file-based version on the same data |
| F11 | Uploads: stream with size cap, delete temp files, 5 MB proxy limit (B5) | `webapp/server.py:174-182` | — | — | — | Temp folder empty after upload; 6 MB upload → 413 |
| F12 | Remove file-based features in production: backup button → shows the provider's backup status; reports streamed, not stored (D3, B8) | `webapp/server.py:769-802`, `web/src/pages/Settings.jsx` | — | — | Admins expecting a backup file | UI shows backup policy; no files written under `exports/` |
| F13 | Restore safety: clear `auth_sessions` after any restore; `_ENSURED` re-check (D2) | `core/db/backup.py`, `webapp/auth.py:154-165` | `auth_sessions` | — | — | Restore test with accounts present |
| F14 | Harden or retire the LAN `checkin_app.py` (S8) | `checkin_app.py` | — | — | — | Same check-in tests as the web wrapper |
| F15 | Minimal public `/api/health` (S11) | `webapp/server.py:198` | — | — | Monitoring checks | Health returns only `status` |

**Preserved unchanged:**
- admin provisioning (single admin, never overwritten);
- student self-registration with the role assigned by the server;
- verification and linking by the admin only;
- RBAC routers;
- CSRF header;
- HttpOnly + SameSite=Strict cookies (`Secure` added);
- the `auth_audit` trail.

---

## G. Render service configuration and environment variables

### G.1 New files

- `Dockerfile.web`:
  - `python:3.12-slim`, non-root user;
  - installs `requirements-web.lock`;
  - copies `app.py`, `checkin_app.py`, `config/`, `core/`, `webapp/`, `web/dist/`, `static/`, `assets/logo_mark.png`;
  - **does not copy** `data/`, `database/`, `models/`, `mlflow.db`, `logs/`, `release/`, `docs/`, `tests/`;
  - `CMD uvicorn webapp.server:app --host 0.0.0.0 --port $PORT --workers 1 --proxy-headers --forwarded-allow-ips="*"`. Restrict this to Render's proxy if Render documents a range; Render terminates TLS and forwards.
- `.dockerignore.web`: an allow-list.
- `requirements-web.lock`: exact pins (fastapi, starlette, uvicorn, pydantic, scikit-learn==1.9.1, pandas, numpy, scipy, joblib, python-multipart, segno, openpyxl, reportlab, matplotlib, psycopg[binary]).
- `render.yaml`: adds services; the existing `eventpulse-ai` (`app.py`) stays unchanged.

```yaml
services:
  - type: web
    name: eventpulse-web
    runtime: docker
    dockerfilePath: ./Dockerfile.web
    plan: starter            # always-on, 512 MB; free plan is NOT acceptable (ephemeral, sleeps)
    region: singapore        # nearest Render region to Gurugram (confirm)
    healthCheckPath: /api/health
    autoDeployTrigger: checksPass
    numInstances: 1
    envVars:
      - key: EVENTPULSE_ENV            ; value: production
      - key: EVENTPULSE_DB_URL         ; fromDatabase: { name: eventpulse-db, property: connectionString }
      - key: EVENTPULSE_SECURE_COOKIES ; value: "1"
      - key: EVENTPULSE_PUBLIC_BASE_URL; sync: false   # https://events.<domain>
      - key: EVENTPULSE_ALLOWED_HOSTS  ; sync: false   # events.<domain>
      - key: EVENTPULSE_ARTIFACT_BUNDLE_URL ; sync: false
      - key: EVENTPULSE_ARTIFACT_TOKEN ; sync: false   # secret
      - key: EVENTPULSE_ARTIFACT_SHA256 ; sync: false  # manifest hash
      - key: EVENTPULSE_REGISTRATION_DOMAINS ; sync: false
databases:
  - name: eventpulse-db
    plan: basic-256mb        # paid (backups); Free Postgres expires after 30 days
    region: singapore
    ipAllowList: []          # private network only
```

### G.2 Environment variables

| Variable | Secret? | Purpose |
|---|---|---|
| `EVENTPULSE_ENV` | no | `production` enables fail-closed checks |
| `EVENTPULSE_DB_URL` | **yes** | PostgreSQL DSN (TLS required) |
| `EVENTPULSE_SECURE_COOKIES` | no | Must be `1` |
| `EVENTPULSE_PUBLIC_BASE_URL` | no | QR links, absolute URLs |
| `EVENTPULSE_ALLOWED_HOSTS` | no | `TrustedHostMiddleware` |
| `EVENTPULSE_SESSION_IDLE_MINUTES` / `EVENTPULSE_SESSION_HOURS` | no | Session lifetime |
| `EVENTPULSE_ARTIFACT_BUNDLE_URL` / `_TOKEN` / `_SHA256` | token **yes** | Private artifact download + integrity |
| `EVENTPULSE_REGISTRATION_DOMAINS` | no | Allowed student e-mail domains |
| `EVENTPULSE_ADMIN_EMAIL` / `_NAME` / `_PASSWORD` | **yes** | **One-off** admin provisioning job only; never permanent |

**Custom domain:** add `events.<domain>` in Render with a CNAME at the DNS provider. Render issues the TLS certificate. HSTS is enabled after verification.

**Compute estimate:**
- Starter (512 MB, 0.5 CPU) fits the measured 167–255 MB working set, provided hashing is bounded (F5).
- Choose Standard (2 GB) if more than 50 concurrent sign-ins at event start are expected.

---

## H. Backup, restoration and rollback

| Layer | Procedure |
|---|---|
| **Before migration** | Mac: stop web + desktop → `backup_database(label="pre_cloud_migration")` → checksum → keep an offline copy (encrypted external drive). The real SQLite file is **never** modified by the migration tool (read-only source) |
| **PostgreSQL ongoing** | Provider daily backups (paid plan) **plus** a nightly `pg_dump -Fc` by a Render cron job to the private bucket (encrypted with `age`/GPG, 30 daily + 12 monthly retention) |
| **Artifacts** | Versioned bundles are immutable; keep the last 3; the manifest hash is pinned in env |
| **Restore drill (monthly)** | Restore the latest dump into a staging database → run the acceptance suite (I.3) → record the time-to-restore |
| **Rollback of a release** | Render "rollback to previous deploy"; schema migrations are additive-only (as today), so the previous code runs on the newer schema |
| **Rollback of the cut-over** (first 72 h) | Freeze writes (maintenance flag) → `export_pg_to_sqlite_snapshot.py` → compare with the pre-migration SQLite → resume the local Mac server on the original file. The desktop is never affected |
| **Account safety after restore** | Always delete `auth_sessions`; re-check the single-admin invariant; audit-log the restore |

**Tests:**
- dump → restore → counts and hashes equal;
- restore-with-accounts test (F13);
- cut-over rollback rehearsal in staging.

---

## I. Staging deployment and acceptance testing

1. **Staging = production clone with synthetic data first.** Build a PG database from `data/synthetic/` (no real data). Use a separate Render service and database. The domain is `staging.events.<domain>`, protected by HTTP Basic or an IP allow-list.
2. **Automated suites (CI):**
   - the existing 223 tests on SQLite;
   - the same core, auth and web tests on PostgreSQL;
   - schema parity;
   - container smoke test (`/api/health`, login, a 401, a prediction);
   - `pip-audit`.
3. **Acceptance checklist on staging (manual + scripted):**
   - all 16 admin pages and 4 student pages load without errors;
   - register → verify → student sees only their own data;
   - every admin route returns 403 for students and 401 for anonymous users;
   - CSRF is enforced;
   - logout invalidates the session;
   - cookie flags are `Secure; HttpOnly; SameSite=Strict`;
   - HSTS, CSP and trusted host are active;
   - `/docs` is disabled;
   - the rate limit works across restarts;
   - all 5 models predict with the SHA-256 verified;
   - What-if, PDF and Excel reports, and exports work;
   - Model Lab shows 9 MLflow runs;
   - Monitoring works from DB logs;
   - uploads up to 5 MB are accepted and larger ones return 413.
4. **Phone test:** QR check-in from at least 2 Android and 2 iOS phones, on **college Wi-Fi and mobile data**, via `https://staging…/checkin/…`. Also a burst of 100 check-ins in 5 minutes (scripted).
5. **Load:** 30 concurrent logins with memory under 400 MB; p95 page API latency under 1 s.
6. **Data rehearsal:** migrate a **copy** of the real SQLite database into a throwaway staging database. The verification report must match counts, hashes and KPIs exactly. Then drop that database.

---

## J. Production launch checklist

- [ ] Decisions in §K confirmed in writing (domain, plan and budget, data-hosting approval, registration policy).
- [ ] Private git repository; `.gitignore` reviewed; secret scanning on; no data files committed.
- [ ] CI green: SQLite + PG suites, parity, container smoke, `pip-audit` (no High/Critical).
- [ ] Artifact bundle built from the allow-list; manifest SHA-256 recorded; uploaded to the private bucket.
- [ ] PG production database created (paid plan, region confirmed, TLS, private network); backups verified.
- [ ] Render web service env vars set (§G.2); `EVENTPULSE_ENV=production` start-up checks pass.
- [ ] Custom domain + TLS active; HTTP→HTTPS redirect; HSTS enabled after a 24 h trial.
- [ ] Maintenance window announced; desktop app and Mac web server stopped; `pre_cloud_migration` backup + offline copy.
- [ ] Migration tool `--dry-run` report reviewed, then `--commit`; verification report archived.
- [ ] Admin provisioned with the one-off job (or migrated hash confirmed); env credentials removed afterwards.
- [ ] Acceptance checklist (I.3) and phone test (I.4) re-run on production.
- [ ] Monitoring: Render health checks, log stream, uptime ping, alert e-mail.
- [ ] Rollback rehearsed; owner and contact recorded in `docs/OPERATIONS.md`.

---

## K. Decisions requiring confirmation

| # | Decision | Recommendation / estimate |
|---|---|---|
| K1 | **Hosting budget** | Render Starter web ($7/mo) + Render Postgres Basic-256mb ($6/mo) ≈ **$13–15/mo**. Free plans are unsuitable: the web service sleeps and its filesystem is ephemeral; Free Postgres expires after 30 days |
| K2 | **Data-hosting approval** | Hosting 279 survey-derived student records, the processed dataset and account e-mails in the cloud needs the mentor's or institution's approval (consent, data protection). Alternative: production starts with **new** data only, and historical records stay on the Mac |
| K3 | **Domain** | Who owns DNS (college IT or personal)? Subdomain name |
| K4 | **Region** | Singapore (Render) is nearest to India; confirm latency from campus |
| K5 | **System of record** | PostgreSQL for the web app; the desktop gets read-only snapshots (§D) |
| K6 | **Registration policy** | Restrict to the college e-mail domain? Invite codes? |
| K7 | **Admin MFA** | Required before launch (recommended) |
| K8 | **Public CSL236 API** | Keep the `app.py` service live, or retire it |
| K9 | **Backup retention** | 30 daily + 12 monthly encrypted dumps (≈ a few MB each) |
| K10 | **Concurrency target** | Peak simultaneous users at event start (decides Starter vs Standard) |

**Storage estimate:**
- Database ≈ 5–10 MB today, after the PG port with indexes.
- Growth ≈ 10–20 MB/year at 10k predictions/year (≈1 KB each, mostly `input_snapshot`).
- Artifacts ≈ 3 MB per release.
- Dumps ≈ 1–3 MB each compressed.

**Compute estimate:** 1 instance, ≈170–260 MB RAM, mostly idle; CPU spikes during report generation (≈1 s) and password hashing (≈0.3 s each).

---

## L. Phased implementation sequence

| Phase | Content | Exit criteria |
|---|---|---|
| **0 — Decisions & groundwork** | Confirm §K; private git repo; secrets policy; staging and production accounts named (not created by tools) | Written approval of K1–K10 |
| **1 — Database portability (FIRST implementation phase)** | §C dialect layer, PG schema, schema-parity tests, CI with a PostgreSQL service container; SQLite behaviour unchanged | 223 tests green on SQLite **and** the core/auth/web tests green on PG |
| 2 — Production hardening | §F (F1–F15), §E integrity + model cache, logs to the database, uploads | Audit findings S1–S8, B3–B9, M2, D2 closed with tests |
| 3 — Packaging & CI | `Dockerfile.web`, lock file, `render.yaml`, container smoke test, `pip-audit` | Image builds and passes the smoke test in CI |
| 4 — Artifacts & migration tooling | Bundle builder + verifier; SQLite→PG tool; PG→SQLite snapshot; MLflow export | Rehearsal on a copy: exact counts, hashes and KPIs |
| 5 — Staging | Deploy with synthetic data → acceptance + phone + load tests → data rehearsal | §I fully passed |
| 6 — Production cut-over | §J checklist | Launch sign-off |
| 7 — Operate | Restore drills, dependency updates, retraining on recorded attendance | Monthly drill logged |

**Start with Phase 0, then Phase 1 (database portability).** Every hosting option depends on Phase 1, including the Vercel + Supabase option in the companion plan. It can also be built and fully tested locally without deploying anything or touching the real database.

---

**Sources (platform facts, checked 16 Nov 2026):**
- Render free tier: <https://render.com/docs/free>
- Render persistent disks: <https://render.com/docs/disks>
- Render pricing: <https://render.com/pricing>
- Render small-business cost article: <https://render.com/articles/how-much-does-cloud-application-hosting-cost-for-small-businesses>
