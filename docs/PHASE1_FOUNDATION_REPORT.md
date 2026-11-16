# EventPulse AI — Phase 1 (Foundation) Completion Report

*16 Nov 2026. Local only: nothing was pushed, deployed, uploaded or created on any external service.
Phase 0 decisions in force: Option A (fresh start), free tier (demo only), college e-mail registration,
desktop app and its SQLite database untouched.*

## Result

Phase 1 is complete. The PostgreSQL hardening from Step A is applied to the Mac project:
- TLS enforcement
- connection timeout
- application name
- private-schema guard
- search_path protection
- test isolation with a real-data guard

The web app uses PostgreSQL **only** with an explicit `postgresql://` URL; any other value is refused.
`requirements-web.txt` is added and pinned. Every suite passes with no failures and no unexpected
skips. The real database's SHA-256 is unchanged and its integrity check passes.

## Checkpoint (taken before any change)

Folder: `Aiml /EventPulseAI_phase1_foundation_checkpoint_20261116/` (next to the project, not inside it)

| File | Content |
|---|---|
| `before_phase1_modified_files.tar` + `.sha256` | The Mac versions of the 10 files changed below (restore = extract over the project) |
| `before_phase1_project_manifest.sha256` | SHA-256 of all 342 project source files before Phase 1 |
| `before_phase1_real_data_baseline.txt` | Size, mtime and SHA-256 of `database/` (incl. `-wal`/`-shm`), backups, models |
| `before_phase1_db_integrity.txt` | Integrity check and record counts before Phase 1 |
| `incoming/` | The exact 15 files applied, with their SHA-256 list |
| `after_phase1_source.sha256` | SHA-256 of 136 source files after Phase 1 |
| `CHECKPOINT_MANIFEST.sha256` | SHA-256 of the checkpoint files themselves |

The extracted copies were verified against the manifest (10 of 10 OK). None of the 6 new files existed
before.

## 1. Files changed (10)

| File | Change |
|---|---|
| `core/db/postgres.py` | `prepare_url`: `sslmode=require` for every non-local host (weaker modes refused), `connect_timeout` (default 10 s), `application_name` (default `eventpulse`). `check_schema`: refuses `public` on a non-local host and enforces `EVENTPULSE_DB_SCHEMA`. Connection failures now give the app's friendly error. |
| `core/db/connection.py` | `web_database_url()`: the explicit-URL rule. `Database(url=…)` refuses non-PostgreSQL values. `open_database()` uses the rule. SQLite path unchanged. |
| `core/db/sql_migrations/postgres/0001_initial_schema.sql` | `SET search_path FROM CURRENT` on the 6 helper functions (never applied to any database before) |
| `webapp/server.py` | `DB_URL = web_database_url()` (2 lines) |
| `webapp/provision_admin.py` | Same rule for `--db-url` (2 lines) |
| `tests/conftest.py` | Byte-snapshot of the real database, isolated backup folder, real-data guard (fails the run if anything under `database/` or `models/` changes) |
| `tests/test_auth.py`, `tests/test_webapp.py` | Copies made from the snapshot; the real file is never opened by SQLite |
| `tests/pg_support.py` | Production-like mode; local-only guard understands `hostaddr` |
| `tests/test_pg_compat.py` | +9 unit tests (SSL/timeout/name/schema rules, friendly connection error); nothing removed |

### New files (6)

- `requirements-web.txt` — the web runtime, pinned to the verified `.venv` versions plus `psycopg==3.3.9` / `psycopg-binary==3.3.9`. Not installed anywhere.
- `scripts/postgres/create_app_role.sql` and `scripts/postgres/setup_private_schema.sql` — the reusable role and private-schema setup.
- `tests/test_pg_production_like.py` (15 tests) and `tests/test_db_configuration.py` (21 tests).
- This report.

## 2. Files deliberately left untouched

- **Desktop:** `main.py`, `gui/` (all pages), `EventPulse AI.app`, `requirements-desktop.txt`, the `.venv`.
- **Business logic:** `core/` except the two database files above. That includes the repositories, services, analytics, check-in, reports, models code, the SQLite migrations `0001–0003` and `core/db/backup.py`.
- **CSL236:** `app.py`, `Dockerfile`, `render.yaml`, `requirements-serve.txt`, `requirements.txt`, `.github/workflows/ci.yml`, `model/`.
- **Data:** `database/` (all files, backups included), `models/`, `data/`, `mlflow.db`, `web/` (frontend source and build), `checkin_app.py`.

Checked by the before/after source manifests: only the 16 files listed above differ.

## 3. Test results

| Run | Where | Result |
|---|---|---|
| Mac verification suite (`EventPulse_Web_Verify.command`, project `.venv`, SQLite) | Mac, 23:30 IST | **267 passed, 38 skipped, 0 failed**; real-data guard: 46 files unchanged |
| PostgreSQL production-like, psycopg 3.3.9 required | Workspace* | **234 passed, 55 skipped, 0 failed** |
| PostgreSQL plain (superuser, public schema), psycopg | Workspace* | **219 passed, 70 skipped, 0 failed** |
| Default SQLite + parity tests (test-only driver) | Workspace* | **272 passed, 17 skipped, 0 failed** |
| Desktop/web smoke check (`run_checkpoint.command`, copies) | Mac, 23:38 IST | Web: 13/13 checks passed; desktop source: main window opened; packaged `.app`: running after 45 s |

\* The workspace copy was checked byte-for-byte against the Mac: 136/136 source files had the same SHA-256.
No file was copied from the Mac for this; only the hash list.

**Skips, all expected:**

- **Mac (38):**
  - 37 need a PostgreSQL server, which the Mac does not have: 21 parity, 15 production-like, 1 configuration.
  - 1 PostgreSQL driver check.
- **Production-like (55):**
  - 44 need the survey-derived dataset, which is never put into PostgreSQL.
  - 9 test SQLite internals.
  - 2 need PySide6 (they run on the Mac).
- **Plain PostgreSQL (70):** the same 55, plus the 15 production-like tests (that mode is not enabled in this run).
- **SQLite (17):**
  - 15 production-like tests (not enabled).
  - 2 need PySide6.

**What the production-like run proves** (local PostgreSQL 16.15 with SSL-only TCP, SCRAM passwords, `en_US.UTF-8`, Supabase-like `anon`/`authenticated`/`service_role` roles):

- **The app's connection:**
  - It connects as the non-superuser `eventpulse_app` over TLSv1.3 to a named host, adding `sslmode=require` itself.
  - The connection is named `eventpulse`, and the timeout is honoured (it gave up after 2.0 s with `connect_timeout=2`).
  - Weak `sslmode` values are refused, and the server rejects connections without encryption.
- **The private schema:**
  - Every table, view and function lives in the private `eventpulse` schema; nothing is in `public`.
  - The helper functions are pinned to `search_path=eventpulse`.
  - The API roles cannot read the data and get "insufficient privilege". The app role cannot create in `public`, create roles or read `pg_authid`.
- **Backup and restore:** `pg_dump` → restore into an empty database gives identical data in all 23 tables (817 synthetic rows), with sessions, CHECK rules, the append-only trigger, ID sequences and privileges all intact.
- **Negative control:** without the `search_path` fix, the restore fails on `events_start_time_check`.

## 4. Database integrity verification

| Check | Before Phase 1 | After Phase 1 |
|---|---|---|
| SHA-256 of `database/eventpulse.db` | `c6b87cec…7ff7c5` | `c6b87cec…7ff7c5` (identical) |
| `PRAGMA integrity_check` / foreign-key violations | ok / 0 | ok / 0 |
| Core schema version | 3 | 3 |
| Students / events / registrations / attendance / predictions / accounts / models | 279 / 36 / 383 / 383 / 58 / 4 / 5 | 279 / 36 / 383 / 383 / 58 / 4 / 5 |
| `-wal` / `-shm` and the 10 backups | — | unchanged (real-data guard, size + mtime + SHA-256) |

Read with SQLite's `immutable` flag, so the checks themselves could not write anything.

## 5. Remaining blockers and notes

1. **psycopg is not installed on the Mac.** That is intended: the production-like tests run in the workspace.
   Installing `requirements-web.txt` into a separate environment (`.venv-web`) needs your approval. The
   macOS wheel for psycopg 3.3.9 on Python 3.12 is not verified until then.
2. **`EventPulse AI.app` is the 13 Nov build.** It does not contain Phase 1 code, and Phase 1 changes
   nothing the desktop uses. A rebuild is optional and not needed.
3. **`webapp/auth_migrations/postgres/0001_accounts.sql`:** its trigger function was left without a
   pinned `search_path` because it references nothing. A provider advisor may still flag it; it can be
   added in Phase 2 if you want.
4. **The private network's TLS support on the free host** is still unverified. It can only be answered
   on staging, which is not authorised (demo-only, no deployment).
5. **College e-mail domain:** needed for Phase 2 (registration restriction). Please provide the exact domain.
6. **Phase 2** (security hardening: production mode, proxy-aware IP, account-based limits for campus Wi-Fi,
   progressive login delay, hashing cap, QR check-in by login, college-domain registration) waits for
   your approval.
