# EventPulse AI — Phase 3 Report: Database Persistence & Backup

**Date:** 17 November 2026 · **Scope:** local preparation only · **Status:** complete, stopped for review

Phase 3 prepares the web app for a **fresh hosted PostgreSQL database on Render**. The desktop app keeps its own local SQLite database.

**Nothing was deployed.** No Render service or database was created, nothing was uploaded or pushed, no URL was published, and no data was transferred.

**Not touched:**
- the desktop app;
- its SQLite database and models;
- the CSL236 files (`app.py`, `Dockerfile`, `render.yaml`, `requirements*.txt`, `.github/`, `model/`);
- `.gitignore` / `.dockerignore`;
- the shared `core/` code.

**There is no schema change** to the local database: core schema 3, web-auth migration 1.

---

## 1. Checkpoint (created before any change)

Folder: `~/Downloads/Aiml /EventPulseAI_phase3_database_checkpoint_20261117/`

| File | Contents |
|---|---|
| `before_phase3_files.tar` (+ `.sha256` `3cb4a5c6…`, verified before applying) | `webapp/`, `core/db/`, `tests/`, `docs/`, `scripts/`, `requirements-web.txt` as they were |
| `before_phase3_project_manifest.sha256` | SHA-256 of all 21,428 project files |
| `before_phase3_real_data_baseline.txt` | Size, mtime and SHA-256 of `database/`, `models/`, `model/model.joblib` |
| `before_phase3_db_integrity.txt` | Read-only integrity check, schema version, table count and row counts |
| `before_phase3_protected_fingerprint.txt` | One combined fingerprint of the desktop and CSL236 files: `EventPulse AI.app`, `model/`, `main.py`, `gui/`, `app.py`, `Dockerfile`, `render.yaml`, `requirements*.txt`, `.github/`, `checkin_app.py`, `.gitignore`, `.dockerignore` |
| `incoming/` | The Phase 3 package (`phase3_files.tar` + `.sha256`) |
| `after_phase3_*` | The same evidence after the changes |
| `smoke/` | Read-only smoke check script and its log |

**Rollback:**
1. Extract `before_phase3_files.tar` over the project.
2. Delete the 6 new files: `webapp/bootstrap.py`, `webapp/hosted_setup.py`, `scripts/postgres/pg_backup.py`, `tests/test_phase3_database.py`, `.env.example`, `docs/DEPLOYMENT_RENDER.md`.
3. No database rollback is needed; nothing in the local database changed.

---

## 2. What changed (12 files: 6 modified, 6 new)

The project manifest shows **exactly these 12 files** changed, plus log output (`logs/web/verify_20261117_022324.log`, and the startup line that the desktop smoke check writes to `logs/app.log`, which points at the throwaway copy).

| File | Change | SHA-256 before → after |
|---|---|---|
| `webapp/security.py` | Time zone setting and IST date helpers; production refuses `WEB_CONCURRENCY` ≠ 1 and unknown zones; **log redaction filter** | `f6b99717…` → `20b7abb49a153e4b55da07bacec839d57c53e70aa6201df3978a5a583c98fe7d` |
| `webapp/server.py` | At startup: log redaction, IST process zone (production), hosted bootstrap. QR and desk check-in use the **IST date**; the check-in confirmation shows IST time | `de078dfd…` → `c702c30b8e41a2ad6c8af3453d1afd6f2013ef5930830dc64e70f97cb9597851` |
| `webapp/auth_migrations/postgres/0001_accounts.sql` | Trigger function gets a pinned `search_path` (Phase 1 open item). PostgreSQL-only file, never applied to any real database | `b6e701d5…` → `d4f4055e1981a77881af88ae90d18f5c220897d8a58a51ac79cd233cac7c8296` |
| `webapp/bootstrap.py` | **new.** Hosted startup steps, PostgreSQL only: Admin from secrets, optional synthetic demo seed, hosted models | — → `92df9de89f4c09bfafdb9a3df5f8f80d6b57f0cb9dcfd73a82bfa74059dece5e` |
| `webapp/hosted_setup.py` | **new.** CLI: `check-config`, `build-models`, `init-db`, `status` | — → `abc73424b6f7024be97e378ef2774845e9f4e91fbe4f58e6958d8cc5f89c928a` |
| `scripts/postgres/pg_backup.py` | **new.** PostgreSQL backup / verify / restore with checksums and a per-table manifest | — → `8d39fdd549a8482c609b0b0343ba6b93da75217b8f4df88b496163f1f0258b64` |
| `tests/test_phase3_database.py` | **new.** 27 Phase 3 tests | — → `64f20899e06ac72759847400647e5dc8f3a3e3892a19f7d15bf4c42ba852d2bc` |
| `tests/test_auth.py` | Adds `app_today()` (the app's IST date); the QR test event uses it | `f88fb008…` → `c2345e15afbdeeca903477a90885ca4825a5bb863d83756e02b1a8af21921cdd` |
| `tests/test_security_phase2.py` | Today's event uses `app_today()` | `02b1ea75…` → `83a3d0646429a8648d19a4429700932eac5dab9bac36b85f4e5e908361a4380c` |
| `tests/test_webapp.py` | Today's event uses `app_today()` | `e956e537…` → `f4e497cf8ade9104d078f8a25430518c80cc6a6e355d67961fb10831e9186b7c` |
| `.env.example` | **new.** Every production variable, with placeholders only (no secrets) | — → `8597ed2705ff6713240730c80e0bb59e4fd3f7db01c01fbe05c328a7958642a6` |
| `docs/DEPLOYMENT_RENDER.md` | **new.** Render setup, build/start commands, first start, backups, approvals still needed | — → `0c4de36c395386e9957d48725efb561c7e36125e1975aaf6da21abb7c47e991f` |

**Why three existing tests changed.** They created "today's" event with the test machine's own date. The web app now deliberately uses India's date, so on a machine running in UTC the two differ between 18:30 and 24:00 UTC. The tests now use the app's date. **No assertion was removed or weakened.**

**Deliberately not changed:**
- **`requirements-web.txt`:** nothing new is needed. The time zone falls back to the exact +05:30 offset if the host lacks the time-zone database.
- **No new `password_resets` table (Task 2 evaluation):** a new web-auth migration would alter the local SQLite database the next time the local web app starts. Reset codes stay as hashed, marked rows in `auth_sessions`, which are tested in Phase 2 and are safe and compatible.
- **The CSL236 `render.yaml` / `Dockerfile`:** the web service is configured in Render's dashboard instead (§8).

---

## 3. Database architecture

```
Desktop app (Mac) ── SQLite  database/eventpulse.db     (default; never uploaded; unchanged)
Hosted web app    ── PostgreSQL via EVENTPULSE_DB_URL     (explicit postgresql:// or postgres:// only)
```

**Connection rules:**
- **Engine selection:** the web app uses PostgreSQL **only** when `EVENTPULSE_DB_URL` holds a `postgresql://` or `postgres://` URL. Any other value is refused, and the desktop never reads this variable (tested).
- **TLS:** remote hosts get `sslmode=require`; weaker modes are refused. The app also sets `connect_timeout` (10 s) and `application_name=eventpulse`.
- **Production:** production mode refuses to start without a PostgreSQL URL (Phase 2).

**Schema:**
- **Least-privilege setup (tested):** private schema `eventpulse` plus the restricted `eventpulse_app` role. The role cannot create roles or databases, cannot escape its schema, and API-style roles are denied (Phase 1, re-run now).
- **Render free tier:** Render provides **one owner account and the `public` schema**. The app refuses `public` on a remote host unless `EVENTPULSE_DB_ALLOW_PUBLIC_SCHEMA=1`. That refusal exists for Supabase-style data APIs, which Render does not have. This exact setup (non-superuser owner, `public`, SSL, SCRAM) is **tested** (`test_render_like_owner_role_on_public_schema`) and was used in the full rehearsal (§6).
- **Pinned `search_path`:** functions and the trigger function keep a fixed `search_path`.

**Migrations:**
- **Two tracks:** core `schema_migrations` (3) and `web_auth_migrations` (1).
- **Each migration is one transaction:** a failure rolls back completely (tested for core in Phase 1 and for web-auth now).
- **Re-running is a no-op:** versions and checksums are unchanged (tested).
- **Edited migrations are refused:** a migration file edited after it was applied stops the app.

**How each student's data is kept separate:**
- **Account:** each student has their own `users` row (unique e-mail; unique claimed Student ID; status `pending` until the Admin verifies it).
- **Records:** registrations, check-ins, attendance and predictions are linked to the student's pseudonymous Student ID.
- **Access:** every `/api/student/*` query uses the verified Student ID **from the session only**, never from the URL or request body. QR check-in records only that ID. Everything else is Admin-only.
- **Tested end to end** on a fresh PostgreSQL database with two students and in the rehearsal: each sees only their own records, and the other's ID never appears.

---

## 4. Fresh database initialisation and Admin provisioning

**On Render's free tier there is no shell**, so the one-time steps run when the server starts. Each one is idempotent:

1. **Configuration check:** production configuration is validated, and startup fails if anything is unsafe.
2. **Time zone:** the process switches to Asia/Kolkata (production only).
3. **Migrations:** the core migrations (3) and the web-auth migration (1) are applied.
4. **Admin:** if `EVENTPULSE_ADMIN_PASSWORD` is set **and no Admin exists**, the single Admin is created from `EVENTPULSE_ADMIN_EMAIL` / `_NAME` / `_PASSWORD`, which are secret environment variables in Render, never in code.
   - An invalid or weak value **stops startup**.
   - The password is never logged; tested with log capture and in the rehearsal's real server log.
   - An existing Admin is **never changed or reset**; a warning asks you to delete the variable.
5. **Optional demo seed:** with `EVENTPULSE_SEED_SYNTHETIC_DEMO=1`, the labelled **synthetic** dataset is imported:
   - only into an **empty** database;
   - only if every row says `SYNTHETIC`;
   - the app then shows "Demo data";
   - no local or real student records are ever imported.
6. **Optional models:** with `EVENTPULSE_HOSTED_MODELS_DIR`, models built at deploy time by `hosted_setup build-models` are registered. They are trained **only** on the synthetic dataset (about 40 s), and models not labelled synthetic are refused. The build refuses to write into the desktop's `models/` or `model/` folders.

**These steps never run against SQLite** (tested: the desktop database is never seeded or given an Admin this way).

**Alternative from your Mac:** `python -m webapp.provision_admin --db-url <External URL>`. It is interactive and the password is never echoed. This needs psycopg in a **separate** environment, not the desktop `.venv`.

**Why the demo seed matters.** Students can only be approved against an existing student record, and the web app has no page for creating student records. In a completely empty database, nobody could be approved. The opt-in synthetic seed provides `SYN-STU-#####` records that students can claim during the demo. Leave it off for a truly empty database.

---

## 5. Backup and restore (`scripts/postgres/pg_backup.py`)

| Step | What it does |
|---|---|
| `backup --out-dir ~/EventPulseBackups` | `pg_dump` custom format, without owners or grants (so it can be restored anywhere). Also writes a `.sha256` file and a **manifest** with every table's row count and content checksum. Re-checks the checksums after the dump and refuses if data changed mid-backup. Refuses if `pg_dump` is older than the server. |
| `verify <file>` | Checks the SHA-256 against the manifest, checks the archive is readable (`pg_restore --list`), and checks the expected tables are present. A damaged file is detected (tested). |
| `restore <file>` | **Only into a new, empty database** (refuses a non-empty one). Single transaction, stops on the first error. Afterwards it compares **every table's row count and content checksum** with the manifest. |

**Security of backup files:**
- **No password on the command line:** the database password goes to the PostgreSQL tools as an environment variable, never as an argument or in the process list (tested).
- **Permissions:** files are mode 600 and the folder is 700 (tested).
- **Location:** backups are refused inside the project folder, so they can't end up in Git.
- **Never uploaded:** nothing is sent anywhere. Backups contain account e-mails and password hashes, so keep them on your encrypted Mac disk.

**Tested:** a full round trip on both test setups.
- **Production-like:** SSL, the `eventpulse_app` role, private schema.
- **Plain** local PostgreSQL.
- **Result:** all row counts and content checksums identical after restore; the restored database accepts the same Admin password and passes its migration checks.

**Free-tier warnings:**
- **Render backs up nothing.** Free Render PostgreSQL has **no backups of any kind**, expires **30 days** after creation and is **deleted** after a 14-day grace period. Your own `pg_backup.py backup` is the only backup.
- **Client tools needed:** the Mac needs PostgreSQL client tools of the server's version or newer (`brew install libpq`). They are not installed yet; installing them needs your approval.

**Two separate systems:**
- **Desktop SQLite backups:** in `database/backups/` (desktop Settings → Backup), unchanged.
- **Hosted backups:** use only this tool.

---

## 6. Time zone and production settings

- **Event day:** the check-in "event day only" rule and the "registered today" date now use **Asia/Kolkata** (`EVENTPULSE_TIMEZONE`). This doesn't depend on the server's own zone.
- **Whole process:** in production, the web process switches to IST at startup, so the shared core code's `date.today()` agrees too (for example, finalising an event or "upcoming" lists).
- **Without a time-zone database:** the app uses the exact fixed +05:30 offset.
- **Tested at the midnight boundary**, with the server clock in UTC:
  - 18:29:59 UTC (23:59:59 IST, 17 Nov): check-in for an 18 Nov event is refused.
  - 18:30:00 UTC (00:00 IST, 18 Nov): accepted (QR and desk).
  - 00:00 IST on 19 Nov: refused again.
  - A production server started with `TZ=UTC` runs in IST (offset 19800 s) and its core date matches the app's date.
- **Single worker:** production refuses `WEB_CONCURRENCY` ≠ 1. The start command uses `--workers 1`. **Not multi-worker or multi-instance safe.**
- **Fail-safe startup:** missing or unsafe production settings stop the server (Phase 2 rules plus the new checks). `python -m webapp.hosted_setup check-config` shows the result without starting anything and never prints secrets (tested).
- **Logs:** a redaction filter is attached to the app's and Uvicorn's loggers and handlers. It removes QR tokens (`?t=`), codes, CSRF tokens, session cookies, `password=` values and credentials in database URLs.
  - **Tested:** unit tests, a real Uvicorn access-log record, and the rehearsal's real server log (QR paths logged as `t=[redacted]`; the Admin password appears 0 times).
  - Password, reset-code and session values are never logged by the app itself.

---

## 7. Test results (exact counts)

### 7.1 Suites

| Suite | Result | Phase 2 |
|---|---|---|
| **Your Mac, full suite** (`logs/web/verify_20261117_022324.log`) | **327 passed, 50 skipped, 0 failed, exit 0** · real-data guard: 46 files unchanged | 312 passed / 38 skipped |
| SQLite regression (cloud workspace, IST clock like your Mac and production) | **332 passed, 29 skipped, 0 failed** | 317 / 17 |
| SQLite regression on a **UTC** clock (non-production) | 331 passed, 29 skipped, **1 failed**: `test_event_lifecycle_prediction_and_attendance` (finalise; see §9.4) | — |
| Plain PostgreSQL (real psycopg 3.3.9) | **290 passed, 71 skipped, 0 failed** | 264 / 70 |
| Production-like PostgreSQL (SSL-only, SCRAM, private schema, restricted app role, Supabase-like roles, Render-like owner role) | **306 passed, 55 skipped, 0 failed** | 279 / 55 |
| The 27 new Phase 3 tests | Production-like PostgreSQL: **27 passed, 0 skipped**. SQLite: 15 passed, 12 skipped (need PostgreSQL) | — |

### 7.2 Rehearsal of the planned deployment (real Uvicorn, production mode, server clock UTC)

Setup:
- a fresh Render-like database (non-superuser owner, `public` schema, SSL, SCRAM);
- the Admin created from environment secrets;
- the synthetic seed;
- models built exactly as the build command does.

Result: **21/21 PASS.**
- **HTTPS and hosts:** plain HTTP → 308 to https; HSTS and CSP present; API docs off; a foreign Host header refused.
- **Admin:** signs in with the secret password; the cookie is `__Host-`, Secure, HttpOnly, SameSite=Strict; demo flag and active model shown; only synthetic students.
- **Student flow:**
  - registering a student for today's IST event works;
  - the QR link uses the public https URL;
  - a college e-mail registers;
  - a pending student is refused at check-in;
  - after Admin approval, the student sees their own ID;
  - **QR check-in works on the IST event day**, with only the student's own ID recorded.
- **Afterwards:** finalising today's event works; prediction with the hosted model works; the student sees their own attendance and is blocked from Admin pages.
- **Logs:** the Admin password appeared 0 times in the server log, and QR tokens were redacted.
- **Restart:** a second start changed nothing (identical counts, no second seed, no new model versions, Admin untouched; warning to delete the password variable).

### 7.3 Your Mac: read-only desktop/web smoke check (`smoke/checkpoint.log`)

- **Web on a throwaway copy:** 48/48 PASS.
- **Desktop source:** window visible, 1176×748, exit 0.
- **Packaged `.app`:** running after 45 s on a copy.
- **Fingerprint:** `4cefb8e3…` before = after = checkpoint baseline. "DESKTOP AND CSL236 FILES UNCHANGED".
- **Real database:** `c6b87cec…` before = after (backups 10 → 10). "REAL DATABASE UNCHANGED".

### 7.4 The 13 requested test categories

| # | Category | Evidence | Result |
|---|---|---|---|
| 1 | Mac suite | 7.1 | 327 passed, 0 failed |
| 2 | SQLite regression | 7.1 | 332 passed, 0 failed (IST); UTC note in §9.4 |
| 3 | Plain PostgreSQL | 7.1 | 290 passed, 0 failed |
| 4 | Production-like PostgreSQL | 7.1 | 306 passed, 0 failed |
| 5 | Fresh database initialisation | `test_fresh_hosted_database_initialisation_is_complete_and_idempotent`, rehearsal | Passed: schema 3 + auth 1, zero records, `search_path` pinned |
| 6 | Migration idempotency | same test (second run: identical versions and checksums), restart in rehearsal, `test_failed_web_auth_migration_rolls_back_completely`, Phase 1 core tests | Passed |
| 7 | Backup and restore | `test_backup_verify_restore_round_trip`, `test_backup_command_line_keeps_password_out_of_arguments`, `test_backups_are_refused_inside_the_project` | Passed on both PostgreSQL setups |
| 8 | Admin provisioning | `test_admin_bootstrap_from_secret_environment`, `test_weak_bootstrap_password_stops_startup`, `test_provision_admin_cli_on_postgres`, `test_bootstrap_errors_and_logs_never_contain_the_password`, rehearsal | Passed |
| 9 | Registration and approval | `test_hosted_end_to_end_…`, rehearsal, Phase 2 tests | Passed |
| 10 | Data isolation | `test_hosted_end_to_end_…`, Phase 2 isolation tests (all engines), rehearsal | Passed |
| 11 | Midnight IST boundary | `test_qr_and_desk_checkin_follow_ist_midnight`, `test_local_date_is_ist_whatever_the_server_zone`, `test_production_server_on_a_utc_host_runs_in_ist`, `test_ist_fallback_without_tz_database` | Passed |
| 12 | Production configuration | `test_production_refuses_multi_worker_and_bad_timezone`, `test_check_config_command`, Phase 2 fail-closed tests, rehearsal | Passed |
| 13 | Desktop regression | Mac suite (including GUI tests) and smoke check | Passed, files unchanged |

### 7.5 Every skipped test explained

**Your Mac: 50 skipped.**
- **38 are the same as in Phase 2.** The Mac has no PostgreSQL server and no psycopg (by design), so the PostgreSQL test modules skip.
- **12 are new Phase 3 tests that need a PostgreSQL server:** 11 marked "hosted-database test", plus the Render-like account test.
- **Impact:** none on the hosted readiness verdict. Those exact tests ran and passed on both PostgreSQL setups in the cloud workspace (7.1), and the GUI tests that the cloud can't run did run on the Mac.
- **Caveat:** the Mac log doesn't print individual skip reasons. These counts come from the suite totals and the per-reason lists of the cloud runs.

**SQLite run: 29 skipped.**

| Count | Reason | Affects production? |
|---|---|---|
| 2 | PySide6 is not installed in the cloud workspace (GUI tests) | No: the Mac runs them, and the web has no GUI |
| 15 | Production-like PostgreSQL tests need that mode | No: they ran in the production-like suite |
| 11 + 1 | Phase 3 hosted-database tests need PostgreSQL | No: they ran in the PostgreSQL suites |

**Plain PostgreSQL: 71 skipped.**

| Count | Reason | Affects production? |
|---|---|---|
| 44 | Tests built on the copy of your **real** local database are skipped by design in PostgreSQL mode (PostgreSQL tests use synthetic data only) | No: equivalent synthetic-data checks run in `test_pg_compat.py` |
| 9 | SQLite-only behaviour (file backups, SQLite pragmas, desktop migration file) | No: the hosted app uses `pg_backup.py`, tested |
| 2 | PySide6 | No |
| 15 + 1 | Production-like-only tests | No: they ran in the production-like suite |

**Production-like PostgreSQL: 55 skipped.** These are the same 44 + 9 + 2 as the plain PostgreSQL run, for the same reasons. Nothing hosted-related was skipped there.

---

## 8. Deployment settings (prepared, not applied)

Full guide: `docs/DEPLOYMENT_RENDER.md`. Variables: `.env.example`.

**Build command:**
```
pip install -r requirements-web.txt && python -m webapp.hosted_setup build-models --out models_hosted
```

**Start command:**
```
uvicorn webapp.server:app --host 0.0.0.0 --port $PORT --workers 1 --no-proxy-headers
```

**Other service settings:**
- Health check path: `/api/health`.
- Runtime: Python, with `PYTHON_VERSION=3.12.8`.
- Plan: free.

**Database:** Render PostgreSQL **16** (Render's default is 18, which was not tested). Use the Internal URL; the app adds `sslmode=require`, which Render supports on internal connections. Set `EVENTPULSE_DB_ALLOW_PUBLIC_SCHEMA=1`.

**Frontend:** `web/dist` is excluded by `.gitignore` (`dist/`), so it must be added to the deployment repository with `git add -f web/dist`. Building the frontend on Render with npm is the untested alternative.

**Production environment variables:**

| Variable | Value |
|---|---|
| `EVENTPULSE_ENV` | `production` |
| `EVENTPULSE_PUBLIC_BASE_URL` | `https://<name>.onrender.com` |
| `EVENTPULSE_TRUSTED_PROXIES` | `*` |
| `EVENTPULSE_PROXY_HOPS` | `1` |
| `EVENTPULSE_DB_URL` | the database URL (secret) |
| `EVENTPULSE_DB_ALLOW_PUBLIC_SCHEMA` | `1` |
| `EVENTPULSE_TIMEZONE` and `TZ` | `Asia/Kolkata` |
| `EVENTPULSE_ADMIN_EMAIL`, `EVENTPULSE_ADMIN_NAME` | the organiser's details |
| `EVENTPULSE_ADMIN_PASSWORD` | secret; **delete after the first start** |
| `EVENTPULSE_SEED_SYNTHETIC_DEMO` | `1` |
| `EVENTPULSE_HOSTED_MODELS_DIR` | `models_hosted` |
| `PYTHON_VERSION` | `3.12.8` |

---

## 9. Known limitations and remaining risks

1. **Free tier facts** (render.com/docs/free, checked 17 Nov 2026):
   - the database has **no backups**, a **30-day expiry**, deletion after a 14-day grace period, 1 GB, and one per workspace;
   - the web service **sleeps after 15 minutes** and wakes in about a minute;
   - the filesystem is ephemeral (models are rebuilt at every deploy; nothing is stored on disk at runtime);
   - there is no shell.
   - Open the site a few minutes before presenting.
2. **Single process only.** Limits, delays and the hashing cap are in memory. Do not scale; a restart clears them.
3. **Clean dependency install not verified.** A clean `pip install -r requirements-web.txt` on Linux could not be run here: the workspace and the VM have no package-index access, and installing on your Mac needs approval. The app's actual imports were checked against the pins. Every unpinned import is an ordinary sub-dependency installed automatically, but the first real proof will be Render's build log. The pins were verified with Python 3.12.8, hence `PYTHON_VERSION`.
4. **Time zone outside production.** In non-production runs, the shared core's own date logic (finalise, "upcoming") follows the machine's zone. On a UTC machine this differs from the app's IST date between 18:30 and 24:00 UTC; this is the one UTC-clock failure in §7.1. Production forces IST (tested), and your Mac is IST, so neither the demo nor local use is affected. Fixing it for UTC development machines would mean changing the shared core code, which was deliberately not done.
5. **PostgreSQL version.** Render defaults to PostgreSQL 18; everything was tested on 16. Choose 16 at creation.
6. **Public schema on Render.** Render's free database gives one owner account. Least privilege is therefore "a dedicated database plus its owner", not the separate restricted role tested in Phase 1. Whether Render allows creating a restricted role is unverified.
7. **The Render platform itself is not exercised:** its real proxy headers, TLS certificate and internal network. All of this was rehearsed locally with equivalent settings (TLS, SCRAM, non-superuser, proxy headers, UTC clock).
8. **The demo uses synthetic data.** The web app can't create student records, so without the synthetic seed no student can be approved. Real student onboarding would need a student-record import or creation feature (not part of this phase).
9. **No repository yet.** The project folder is not a Git repository. Deploying needs a GitHub repository or branch that excludes `data/original/`, `database/`, `models/`, `.venv` and logs (`.gitignore` already does) and force-adds `web/dist`.
10. **Backup tools on the Mac.** `pg_dump`, `pg_restore` and `psql` are not installed on your Mac yet.
11. **Carried over:** QR cookie behaviour on real phones (SameSite=Strict) is still untested; there is no Admin password-reset command; inline styles are allowed by CSP. See the Phase 2 report.

---

## 10. Tasks that still need your explicit approval (external, not done)

1. **Repository:** create or choose a GitHub repository (or branch) for deployment and push the code, with `web/dist` force-added and no private data. *(push to remote)*
2. **Database:** create the free Render PostgreSQL database (version 16, same region). *(external resource)*
3. **Web service:** create the Render web service with the build/start commands and health check above. *(external resource)*
4. **Secrets:** enter the environment variables in Render, including the database URL and the Admin password as secrets. *(credentials on an external service)*
5. **Deploy:** run the first deploy and open the `*.onrender.com` URL (publishes the app). Then delete `EVENTPULSE_ADMIN_PASSWORD`. *(public exposure)*
6. **Optional:** install PostgreSQL client tools on your Mac (`brew install libpq`) to take backups of the hosted database. *(package install on your Mac)*

**Phase 3 is complete. Work has stopped here for your review. Phase 4 (deployment) has not been started.**
