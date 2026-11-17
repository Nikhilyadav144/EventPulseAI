# EventPulse AI — Production Deployment Readiness Audit

**Date:** 16 November 2026 · **Scope:** the complete project at `~/Downloads/Aiml /EventPulseAI`
**Type:** read-only audit. No application code, database, backup, account or package was changed. The only file written is this report.

## How this audit was done

- **Files:** read on the Mac through a read-only shell (code, configs, scripts, logs).
- **Database:** never opened for writing. Each check used a snapshot copy made with SQLite's online-backup API (read-only source) into a temporary folder outside the project.
- **Secrets:** scanned by pattern. Only file names, line numbers and key names were printed, never values.
- **Tests:** the existing suite was re-run with `EventPulse_Web_Verify.command`. Every test fixture uses temporary directories or a read-only copy of the database (verified by reading `tests/conftest.py`, `test_app.py`, `test_auth.py`, `test_webapp.py`, `test_gui.py` and `test_training.py`). Row counts and integrity of the real database were re-checked after the run and are unchanged.
- **Evidence labels:**
  - **VERIFIED**: observed directly, by command output or code reading with a line reference.
  - **MANUAL**: needs a test I could not or must not run (fresh machine, cloud host, HTTPS, load).
  - **DESIGN**: a conclusion drawn from code reading that has not been exercised.

---

## 1. Executive summary

EventPulse AI works well **as a local, single-machine application**. The evidence:

- **Tests:** the full suite passed today at 16:54 IST, with the real administrator account present: **223 passed, 0 failed, 0 skipped** (`logs/web/verify_20261116_165436.log`).
- **Database:**
  - SQLite reports integrity `ok`, with 0 foreign-key violations.
  - Every original record count is intact: 279 students, 36 events, 383 registrations, 383 attendance records, 58 predictions.
  - The core schema is v3, so the desktop app keeps working.
  - Auth migration 0001 is applied, with a pre-migration backup present.
- **Models:** all 5 registered model files match the SHA-256 hashes stored in the database.
- **Access control:** students and admins are separated correctly, and every password is stored as an scrypt hash.

**The project is not ready for production deployment.** The deployment configuration you have (`Dockerfile`, `render.yaml`, CI) ships **only the CSL236 prediction API (`app.py`)**. It does not include the web application, the login system, the database or the model registry. Deploying the web app as it stands would hit three critical blockers:

1. The target platform (Render, free plan) **erases a SQLite database on every redeploy, restart or idle spin-down** and cannot attach a persistent disk.
2. The database, model registry and source data are **not under version control**, and no secure provisioning path exists for them. The database now also holds names, e-mails and password hashes.
3. The **HTTPS and reverse-proxy settings are not configured**:
   - cookies are not `Secure` by default;
   - client IPs behind a proxy are wrong, so the rate limits break;
   - there are no trusted-host or HSTS settings.

There are also High-severity items in memory use under load (scrypt), CI coverage (the auth and web tests skip in CI) and account-lockout abuse.

**Verdict: NOT READY for public production deployment.** It is suitable for continued local or trusted-LAN use. See §10.

| Severity | Count |
|---|---|
| Critical | 3 |
| High | 6 |
| Medium | 15 |
| Low | 11 |

---

## 2. Project inventory (VERIFIED)

| Component | Location | Notes |
|---|---|---|
| Desktop app (PySide6) | `main.py`, `gui/`, packaged `EventPulse AI.app` (built into `release/`, 503 MB) | Opens `database/eventpulse.db`; refuses databases with a core schema newer than v3 |
| Web backend (FastAPI) | `webapp/server.py`, `webapp/auth.py`, `webapp/provision_admin.py`, `webapp/auth_migrations/` | Port 8100; serves `web/dist` and `/api/*` |
| Web frontend (React 19) | `web/src/`, built output `web/dist/` (app.js 304 KB, no source maps) | Built offline with esbuild; Vite config for development |
| Public prediction API (CSL236) | `app.py` (+ `static/`, `model/`) | **This is the only thing the Dockerfile and Render deploy** |
| Standalone QR server | `checkin_app.py` (port 8010, `0.0.0.0`) | Desktop's LAN check-in server; same database |
| Domain / DB layer | `core/` (repositories, services, migrations, backup, analytics, intelligence, checkin, reports) | Shared by desktop and web |
| Database | `database/eventpulse.db` (WAL, 491 KB), `database/eventpulse_demo.db`, `database/backups/` (10 backups, 4.6 MB) | Git-ignored |
| ML pipeline | `train.py`, `core/model_training.py`, `core/model_registry.py`, `core/prediction.py`, `mlflow.db` (9 runs), `mlruns/` | Serving model `model/`; registry `models/` (5 versions) |
| Data | `data/event_attendance*.csv` (end-term training data), `data/processed/`, `data/synthetic/`, `data/original/` (real survey workbook) | `data/original` and `data/processed` are git-ignored on purpose |
| Deployment config | `Dockerfile`, `.dockerignore` (allow-list), `render.yaml` (free plan), `.github/workflows/ci.yml` | No deployment config exists for the web app |
| Launchers (macOS) | `EventPulse_*.command` (Setup, Web, Web_LAN, Web_Stop, Admin_Setup, Web_Verify, Web_AuthTest, Backend_Verify, EndTerm_Run, CheckIn_Server, Build_App, Verify) | Use `open` and `lsof`, so they are macOS-only |
| Version control | **None in this folder** (`.git` absent) | CI and Render refer to a GitHub repository that is not this folder |

---

## 3. Security audit

### What is in place (VERIFIED unless marked)

- **Passwords:**
  - scrypt with N=2¹⁷, r=8, p=1, a 16-byte salt and constant-time comparison (`webapp/auth.py:176-207`).
  - All 4 stored hashes use `scrypt$`; no plaintext anywhere.
  - Unknown e-mails run a dummy hash, so response timing doesn't reveal which accounts exist (`auth.py:215`).
- **Sessions:**
  - 256-bit random token, with only its SHA-256 stored (`auth.py:323-345`).
  - Cookie is HttpOnly and SameSite=Strict (`auth.py:359`); idle timeout 2 h, absolute lifetime 12 h.
  - Sessions are deleted on logout and on deactivation.
  - Real-database snapshot: 2 sessions, both unexpired.
- **CSRF:** a per-session token in the `X-CSRF-Token` header, checked with `compare_digest` on every state-changing request (`auth.py:506`, `583`). The frontend keeps it in memory only; the built bundle contains no `localStorage`, `sessionStorage` or `document.cookie` (VERIFIED by grep of `web/dist/assets/app.js`).
- **Authorization:**
  - Every data endpoint sits on an admin-only router (`server.py`, router `admin` + `app.include_router` at 806-809).
  - Student endpoints derive the student ID from the session only.
  - On the Mac (manual browser test, 16 Nov): a student got 403 on all admin APIs and couldn't reach another student's records by changing parameters, and signed-out requests got 401.
  - Automated: `tests/test_auth.py` (19 tests).
- **Database guards:** a single admin is enforced by a unique partial index, and role changes are blocked by trigger `trg_users_role_immutable` (VERIFIED in the snapshot).
- **Registration:** extra fields such as `role` are rejected (`extra="forbid"`, `auth.py:441`), and validation runs on the server.
- **Secrets:** none found in code, configs, scripts, docs or the frontend bundle. No `.env` files. The only `.pem` is the CA bundle inside the desktop `.app`. The throwaway test-server folder `logs/web/authtest/` has been deleted.
- **Security headers:** nosniff, `X-Frame-Options: DENY`, Referrer-Policy, and `no-store` on the API (`server.py:90-96`).
- **Audit trail:** `auth_audit` (20 events) records registrations, logins, failures and account actions, with no secrets.

### Findings

**S1 — Critical — Session cookie is not `Secure` unless an env var is set; no HTTPS enforcement**
- **Where:** `webapp/auth.py:47` (`SECURE_COOKIES = os.environ.get("EVENTPULSE_SECURE_COOKIES", "0") == "1"`) and `:359`.
- **Problem:** the default is insecure. Nothing redirects HTTP to HTTPS, and there is no HSTS header.
- **Why it matters:** behind a public URL, a session cookie sent over plain HTTP can be stolen by anyone on the network path (session hijack, including the admin's).
- **Fix:** in production, require HTTPS and set `EVENTPULSE_SECURE_COOKIES=1`. Better, fail closed: refuse to start when an `EVENTPULSE_ENV=production` flag is set and secure cookies are off. Add `Strict-Transport-Security` once on HTTPS, and redirect HTTP to HTTPS at the proxy.

**S2 — High — Client IP is wrong behind a reverse proxy, so the rate limits break**
- **Where:** `webapp/auth.py:284-285` (`request.client.host`), used by all limiters (`auth.py:273-276`) and the check-in limiter (`server.py:651`).
- **Problem:** on Render or any proxy, `request.client.host` is the proxy's address unless uvicorn runs with `--proxy-headers --forwarded-allow-ips=<proxy>`. None of the launchers or the Dockerfile set this. (DESIGN; MANUAL test needed on the host.)
- **Why it matters:** every visitor would share one rate-limit bucket. 30 bad logins from anyone would lock sign-in for everyone for 15 minutes (DoS), and per-IP limits would stop meaning anything.
- **Fix:** run uvicorn with `--proxy-headers --forwarded-allow-ips` set to the platform's proxy range, or add Starlette's `ProxyHeadersMiddleware` with an explicit trusted list. Never trust `X-Forwarded-For` from arbitrary clients.

**S3 — High — scrypt memory cost allows memory exhaustion on small instances**
- **Where:** `webapp/auth.py:176-177` (N=2¹⁷, r=8, so about 128 MiB per hash; `maxmem` 256 MiB).
- **Problem:** each login and each registration hashes on a worker thread. Several concurrent attempts need several × 128 MiB. A 512 MB instance (Render's free and starter tiers) can be pushed out of memory by a handful of parallel requests. (DESIGN; MANUAL load test needed.)
- **Fix:**
  - Cap concurrent hashing with a semaphore (e.g. 2).
  - Or install `argon2-cffi` and use Argon2id with OWASP's lower-memory settings (m=19 MiB, t=2, p=1). The code already prefers Argon2 when it is installed (`auth.py:168-174`).
  - Add a load test (see T6).

**S4 — High — Targeted account lockout (including the admin)**
- **Where:** `webapp/auth.py:273`, `:566`. 5 failed logins per e-mail lock that account for 15 minutes, from any IP.
- **Problem:** anyone who knows the admin's e-mail can keep the admin locked out indefinitely by sending 5 wrong passwords every 15 minutes. (DESIGN; tested in `test_repeated_failed_logins_are_rate_limited`.)
- **Fix:** key the lock on (account, IP), add progressive delays instead of a hard lock, add a CAPTCHA after several failures, and alert on repeated lockouts. Consider restricting admin login to a trusted network or VPN.

**S5 — Medium — Public self-registration has no domain restriction or verification**
- **Where:** `webapp/auth.py:531-552`.
- **Problem:** anyone on the internet can create student accounts (10 per IP per hour). Accounts are harmless until verified, but they can fill the database and squat Student IDs, which the admin can release with *Reject ID*.
- **Fix:** for a public deployment, add one or more of: e-mail verification, an allowed e-mail domain (e.g. the university domain), an invite or registration code from the organiser, or a CAPTCHA.

**S6 — Medium — No Content-Security-Policy; public API docs; no trusted-host check**
- **Where:**
  - CSP: `webapp/server.py:90-96` has no `Content-Security-Policy`.
  - Docs: `server.py:80`, where `FastAPI(...)` keeps the default `/docs`, `/redoc` and `/openapi.json` (VERIFIED: `/docs` returned 200 unauthenticated on 8100).
  - Hosts: no `TrustedHostMiddleware`.
- **Why it matters:** a CSP limits the damage of any future XSS bug. Public docs map the attack surface. A forged `Host` header changes generated QR links (`server.py:566-570`).
- **Fix:**
  - Add a strict CSP: `default-src 'self'; img-src 'self' data:; frame-ancestors 'none'`.
  - Disable docs in production (`docs_url=None`, `redoc_url=None`, `openapi_url=None`) or protect them with `require_admin`.
  - Add `TrustedHostMiddleware(allowed_hosts=[<your domain>])`.

**S7 — Medium — QR links are always `http://` and use the request's Host/port**
- **Where:** `webapp/server.py:566-570`.
- **Problem:** behind HTTPS, QR codes would point to `http://<host>:<internal port>`, and phones would fail or downgrade to HTTP. On a Mac it substitutes the LAN IP, which is meaningless in the cloud.
- **Fix:** a configurable `EVENTPULSE_PUBLIC_BASE_URL` (e.g. `https://events.example.edu`) used for QR links; keep the LAN IP only in LAN mode.

**S8 — Medium — Standalone QR server (`checkin_app.py`) lacks the web app's hardening**
- **Where:** `checkin_app.py:75-82` (GET shows the event name and date without validating the token) and `:85-96` (no rate limiting). It binds `0.0.0.0:8010` (`EventPulse_CheckIn_Server.command:18`).
- **Problem:** the web app's guarded wrapper (`server.py:630-656`) doesn't apply when the desktop's QR server runs. Anyone on the LAN can enumerate event names and attempt check-ins without a rate limit. A valid token is still required to check in.
- **Fix:** apply the same token-first and rate-limit logic in `checkin_app.py`, or retire it in favour of the web app's `/checkin` route. Never expose it to the internet.

**S9 — Medium — No password change or reset, and no MFA for the admin**
- **Where:** `webapp/auth.py` (no such endpoints); documented in `docs/AUTH.md`.
- **Problem:** a forgotten admin password can only be recovered by rolling back all accounts. There is no second factor on the most powerful account.
- **Fix:** add change-password (requiring the current password, and revoking all sessions), an admin reset via a local CLI that requires the server operator, and TOTP MFA for the admin.

**S10 — Low — Login does not revoke other sessions, and there is no session list**
- **Where:** `webapp/auth.py:323-332`.
- **Fix:** let users see and revoke sessions; revoke all sessions on password change.

**S11 — Low — Public `/api/health` reveals the database file name, schema version and demo flag**
- **Where:** `webapp/server.py:198-199`.
- **Fix:** return only `status` publicly; move the details behind admin.

**S12 — Low — The QR token travels in the URL query string**
- **Where:** `webapp/server.py` `/checkin/{event_id}?t=…`.
- **Problem:** uvicorn's access log records full request lines, so the token would be written to `logs/web/run_*.log` when used. No such lines exist today: VERIFIED 0 occurrences. Exposure is limited because tokens are per event and revocable.
- **Fix:** strip query strings from access logs, or rotate the token after each event.

**S13 — Low — Development-only CORS origins hard-coded**
- **Where:** `webapp/server.py:83` (`http://127.0.0.1:5173`, `http://localhost:5173`).
- **Why it matters:** harmless (no credentials allowed), but development settings shouldn't ship.
- **Fix:** read the allowed origins from an env var; use an empty list in production, where the frontend is served from the same origin.

---

## 4. Database safety audit

### Verified state (snapshot via the backup API, 16 Nov, 16:5x IST)

- **Integrity:** `PRAGMA integrity_check` = `ok`; `foreign_key_check` = 0 rows. Journal mode is WAL.
- **Core migrations:** 1 `initial_schema`, 2 `indexes_and_views`, 3 `event_workflow`. Web-auth migration 1 `accounts` was applied 2026-11-16T09:38:05Z.
- **Counts:** students 279, events 36, registrations 383, attendance 383, predictions 58, prediction runs 9, model versions 5, users 4 (1 admin, 2 verified students, 1 rejected), auth sessions 2, auth_audit 20.
- **Unchanged by the test run:** the counts were identical after today's suite run.
- **Backups:** 10 files in `database/backups/`, permissions `-rw-------`. They include `eventpulse_pre_auth_migration_*.db`, which has no `users` table, so it is a true pre-migration copy.
- **Migrations:** the core system and the auth track are both checksummed and transactional, with an automatic backup before applying (`core/db/migrations.py`, `webapp/auth.py:96-135`).
- **Restore and rollback code:**
  - Restore validates its input and takes a safety backup (`core/db/backup.py:119-145`).
  - Rollback of the auth tables is tested on a copy (`test_migration_is_additive_backed_up_and_reversible`).
  - **Neither was run against the real database (by rule).**

### Findings

**D1 — Critical — SQLite on the configured host loses all data**
- **Where:** `render.yaml` (`plan: free`, no disk), `webapp/server.py` (`DB_PATH`, SQLite).
- **Problem:** Render free web services have an ephemeral filesystem and cannot attach a persistent disk. Local SQLite files are lost on every redeploy, restart or spin-down (after 15 idle minutes). Accounts, events, attendance and predictions would silently disappear.
- **Fix:** one of:
  - a paid instance with a persistent disk mounted at the database folder (single instance only, SQLite);
  - porting `core/db` to managed PostgreSQL (needed anyway for more than one instance);
  - keeping the app on a machine you control.
- **Also:** move backups off the host.

**D2 — High — Restore is not auth-aware**
- **Where:** `core/db/backup.py:119-145` (used by the desktop Settings page, `gui/pages/settings_page.py:191-212`) and `webapp/auth.py:154-165` (`_ENSURED` cache).
- **Problem 1:** restoring any backup also restores that moment's `users` and `auth_sessions`. This revives deactivated accounts, old password hashes and old sessions.
- **Problem 2:** restoring a pre-auth backup while the web server runs removes the auth tables. The server's in-process cache still believes the schema exists, so requests fail with 500 until a restart, which then recreates **empty** account tables (the admin is gone). (DESIGN; MANUAL test on a copy recommended.)
- **Fix:**
  - Require the web server to be stopped for a restore, or clear `_ENSURED` and re-check the schema on each connection.
  - Delete all `auth_sessions` after any restore.
  - Document that a restore rolls accounts back too, or keep accounts in a separate file that restores don't touch.

**D3 — Medium — Backups: no retention, unencrypted, same disk, now contain personal data**
- **Where:** `EventPulse_Web.command` (a backup on every start); `core/db/backup.py:90` (`prune_backups` exists but is never called — VERIFIED by grep). Backups sit beside the database.
- **Problem:** since the auth migration, backups contain names, e-mails and password hashes. Six `web_start` backups have already accumulated today.
- **Fix:** call `prune_backups(keep=N)` after start-up backups. Encrypt and copy backups off the machine. Document a restore drill.

**D4 — Medium — Desktop and web write to the same SQLite file**
- **Where:** `config/settings.py` (`DATABASE_DIR`), `webapp/server.py` (`DB_PATH`).
- **Problem:** this works locally (WAL, busy timeout), but a cloud deployment cannot share a file with a desktop app on a laptop. Two copies would diverge.
- **Fix:** decide the system of record for production (the web server). The desktop app then becomes a client of the web API, or stays offline/demo only.

**D5 — Low — Rollback of the auth migration needs an interactive confirmation and stopped servers**
- **Where:** `webapp/provision_admin.py` (`--rollback-auth`).
- **Fix:** document "stop the web server first" next to the command (see D2).

---

## 5. Backend and API audit

**B1 — Critical — No deployment artifact for the web application**
- **Where:** `Dockerfile` (copies only `app.py`, `static/`, `model/` and a subset of `core/`; `CMD uvicorn app:app`), `.dockerignore` (allow-list excludes `webapp/`, `web/dist`, `models/`, `database/`, `checkin_app.py`), `render.yaml`, `.github/workflows/ci.yml` (docker job smoke-tests `/predict` of `app.py` only).
- **Problem:** the login system, role-based pages, database and model registry would not be deployed at all. Deploying today publishes only the public CSL236 prediction API.
- **Fix:**
  - Create a separate image for the web app: `uvicorn webapp.server:app` with `requirements` including `python-multipart` and `segno`.
  - Copy `webapp/`, `web/dist/`, `core/`, `config/`, `checkin_app.py`, `app.py` (imported by `server.py`) and the model registry.
  - Never copy `data/original/`.
  - Provide the database via a persistent volume or PostgreSQL (D1), with env-based configuration.
  - Add a CI smoke test for `/api/health`, `/api/auth/login` and a 401 check.

**B2 — Critical — Database, model registry and data are not version-controlled, and there is no secure provisioning path**
- **Where:** `.gitignore` excludes `database/`, `models/`, `data/original/`, `data/processed/`, `logs/`. This folder isn't a git repository at all.
- **Problem:** a fresh server (or a fresh clone) has no `database/eventpulse.db`, no registered models (`model_versions.artifact_path` points to `models/...`) and no source data to rebuild them. `data/original/` holds the real survey workbook and must **never** be published, so "just commit it" is not an option. The database now holds personal data too.
- **Fix:**
  - Write a one-time, documented provisioning procedure: an encrypted transfer of `eventpulse.db` and `models/` to the server's persistent volume, verified by checksum.
  - Or a bootstrap script that registers `model/` (the end-term serving model) in a fresh database.
  - Keep the project folder under git, with the existing ignore rules, so the deployed code can be traced.

**B3 — Medium — The model is loaded from disk on every web prediction**
- **Where:** `core/db/services.py:175-182` (`_predictor_for` → `Predictor(...)`, i.e. `joblib.load`, per request); `core/analytics.py:279` (per analytics request).
- **Why it matters:** latency and CPU per request. Random Forest is 787 KB; tolerable locally, wasteful in production. (`app.py` correctly loads once: `app.py:149-150`.)
- **Fix:** cache predictors per `(model_version_id, artifact_sha256)` in-process and invalidate on activation.

**B4 — Medium — In-memory rate limits and a single-process assumption**
- **Where:** `webapp/auth.py:247-276`.
- **Problem:** counters reset on restart and aren't shared between workers or instances.
- **Fix:** run one worker, or move the counters into the database or Redis before scaling out.

**B5 — Medium — Uploaded files are never deleted**
- **Where:** `webapp/server.py:174-182` (`tempfile.mkdtemp`, file kept); used by `/api/datasets/inspect` (269), `/api/registrations/import` (460), `/api/predict/file` (520). The whole file is read into memory before the 10 MB check (178-179).
- **Problem:** uploaded registration and prediction files (personal data) accumulate in the system temp folder, and a large upload uses memory before it is rejected.
- **Fix:** delete the temp file in a `finally` block, stream with a size limit, and add a request-size limit at the proxy.

**B6 — Low — Error messages return internal text**
- **Where:** `webapp/server.py:143-156` (`str(exc)` for `DatabaseError` and `ValueError`).
- **Problem:** messages can include table and constraint names. Most of these endpoints are admin-only.
- **Fix:** map known errors to friendly messages and log the detail on the server.

**B7 — Low — No structured application logging for the web backend**
- **Where:** `webapp/` uses only uvicorn's default logs (`logs/web/run_*.log`); there is no request ID or error log level configuration (VERIFIED: no `logging.basicConfig` in `webapp/`).
- **Fix:** configure JSON logging with request IDs, keep auth events in `auth_audit`, and ship logs to the platform's log stream.

**B8 — Low — Generated reports accumulate**
- **Where:** `webapp/server.py:769-777` (`exports/web/`).
- **Fix:** stream reports without keeping them, or prune the folder.

**B9 — Low — Public prediction API (`app.py`) has no rate limit and an unbounded log**
- **Where:** `app.py:235-240` (append-only JSONL), `app.py:292-302` (`/monitoring/summary` reads the whole file on each call).
- **Fix:** add a rate limit, rotate the log, and keep counters instead of re-reading the file.

---

## 6. ML deployment audit

### Verified

- **Registry integrity:** all 5 registry artifacts match their stored `artifact_sha256`: Decision Tree, Gradient Boosting, Logistic Regression (active), the LR original baseline and Random Forest.
- **Library versions:** models and the environment both use scikit-learn 1.9.1. `core/model_registry.py:40-49` refuses a minor-version mismatch.
- **Serving model:** `model/` (end-term Logistic Regression) records its training-data SHA-256 and MLflow run ID. The training file `data/event_attendance.csv` exists.
- **Startup:** `app.py` loads its model once at startup (`app.py:149-150`).
- **Live checks:**
  - Real web predictions worked on the Mac (prediction #58 stored, 16 Nov).
  - The Model Laboratory showed the ROC curve and all 9 MLflow runs.
  - `test_app.py` and `test_db_services.py` cover the prediction paths.

### Findings

**M1 — High — The web app needs files a deployment would not have**
- **Where:**
  - `models/<name>/model.joblib` (registry; git-ignored).
  - `data/processed/ml_dataset_processed.csv` (ROC curve, `core/analytics.py:272`; Dataset Explorer; git-ignored).
  - Training data used by drift monitoring.
  - `mlflow.db` (Model Laboratory runs).
- **Problem:** without them, predictions fail ("No active model"), and ROC, drift monitoring and dataset views are empty or wrong. (DESIGN, from code; MANUAL test in a clean container needed.)
- **Fix:** list the runtime files explicitly and provision them with the database (B2). Make each page degrade with a clear message when an optional file is missing; most already return `None`. Never ship `data/original/`.

**M2 — Medium — `joblib` artifacts are loaded without checking their hash**
- **Where:** `core/model_registry.py:40-49` (`joblib.load`); the stored `artifact_sha256` is not compared at load time.
- **Problem:** `joblib` files are pickles, and loading one can execute code. Anyone who can write to `models/` could run code inside the server.
- **Fix:** verify `sha256(model.joblib) == model_versions.artifact_sha256` (and `metadata.json` for `model/`) before loading. Make the model folders read-only for the service user.

**M3 — Low — Models trained on SIMULATED labels**
- **Where:** documented throughout (`config/settings.py` `SIMULATED_TARGET_NOTICE`).
- **Problem:** this is not a defect, and the UI labels it. But production users should not treat predictions as validated.
- **Fix:** keep the notice. Retrain on recorded attendance once enough real outcomes exist; Model Monitoring already tracks this.

---

## 7. Configuration audit

### Environment variables in use (names only; VERIFIED by grep)

| Variable | Used in | Default | Production requirement |
|---|---|---|---|
| `EVENTPULSE_DB` | `webapp/server.py`, `checkin_app.py`, `provision_admin.py` | `database/eventpulse.db` | Point to the persistent volume |
| `EVENTPULSE_SECURE_COOKIES` | `webapp/auth.py:47` | `0` (insecure) | **Must be `1`** behind HTTPS |
| `EVENTPULSE_SESSION_IDLE_MINUTES` / `EVENTPULSE_SESSION_HOURS` | `webapp/auth.py:45-46` | 120 / 12 | Review |
| `EVENTPULSE_ADMIN_EMAIL` / `_NAME` / `_PASSWORD` | `webapp/provision_admin.py` (`--from-env` only) | — | Use once, then unset; never put them in a Dockerfile or `render.yaml` |
| `EVENTPULSE_WEB_HOST` / `EVENTPULSE_WEB_PORT` | `EventPulse_Web.command` | `127.0.0.1` / 8100 | Platform `$PORT`, `0.0.0.0` behind the proxy |
| `EVENTPULSE_HOME` | `config/settings.py` | — | Desktop `.app` only |
| `MODEL_DIR`, `PREDICTION_LOG`, `REJECTED_LOG` | `app.py:40-42` | `model/`, `logs/*.jsonl` | `app.py` only |
| `PORT` | `Dockerfile`, `render.yaml` | 8000 | — |

No secret keys are needed (sessions are random server-side tokens). **Verified: no secrets are stored in code, configs or frontend.**

### Findings

**C1 — Medium — Development defaults throughout, and macOS-only launchers**
- **Where:**
  - `EventPulse_Web.command:19,50` (`open`).
  - `EventPulse_Web_Stop.command:5` (`lsof`).
  - `webapp/provision_admin.py:86` (prints `http://127.0.0.1:8100/`).
  - `webapp/server.py:4-5` (docstring).
  - `web/vite.config.js:9` (development proxy; not in the build — VERIFIED: no localhost URLs in `web/dist/assets/app.js`).
- **Problem:** there is no Linux or container start command for the web app.
- **Fix:** add a production entrypoint, e.g. `uvicorn webapp.server:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips=…`, and use an env var for the public URL.

**C2 — Medium — No settings validation or "production mode"**
- **Problem:** the app starts with insecure defaults silently.
- **Fix:** add `EVENTPULSE_ENV=production`. In that mode, refuse to start unless secure cookies, a public base URL, allowed hosts, a writable persistent database path and an existing admin are all configured. Disable `/docs`.

**C3 — Low — The throwaway test launcher ships in the project**
- **Where:** `EventPulse_Web_AuthTest.command`.
- **Problem:** it writes a generated test password to `logs/web/authtest/test_admin.txt` (mode 600) and copies the database (with personal data) into `logs/`. That's fine for local use, but it must not be packaged or deployed.
- **Fix:** exclude it from any deployment image (the current `.dockerignore` allow-list already does).

---

## 8. Dependencies

**P1 — Medium — No lock file; ranges only**
- **Where:** `requirements.txt`, `requirements-serve.txt`. Only `scikit-learn==1.9.1` is pinned; everything else is `>=`.
- **Installed here (VERIFIED from `.venv`):**
  - Python 3.12.8, fastapi 0.162.5, starlette 1.10.0, uvicorn 0.54.0, pydantic 2.14.3.
  - pandas 3.1.1, numpy 2.5.4, scipy 1.18.1, joblib 1.6.0, mlflow 3.18.0.
  - python-multipart 0.0.33, segno 1.6.6, httpx 0.28.1, cryptography 50.0.2.
  - `argon2-cffi` not installed.
- **Problem:** a fresh install can resolve to different major versions; Starlette 1.x already removed APIs used by older code. CI uses Python 3.11 while the Mac uses 3.12.
- **Fix:** generate a lock file (`pip-compile` or `pip freeze` into `requirements-web.lock`) for the exact deployed set, and pin the Python version.

**P2 — Medium — No vulnerability scan**
- **Problem:** I didn't run `pip-audit` (it needs network access and installing a package, which this audit doesn't allow). **Status: NOT VERIFIED.**
- **Fix:** run `pip-audit -r requirements-web.lock` in CI. Track the deprecation warning seen in tests ("Using `httpx` with `starlette.testclient` is deprecated"): `logs/web/verify_20261116_165436.log`.

**P3 — Low — Optional Argon2 dependency not declared**
- **Fix:** add `argon2-cffi` to the web requirements (see S3).

---

## 9. Testing audit

### Verified

- **Full suite on the Mac:** **223 passed, 0 failed, 0 skipped**. It ran on 16 Nov at 16:54 with the real admin present; 359 s; `logs/web/verify_20261116_165436.log`. An earlier run at 15:18 gave the same result.
- **Breakdown by file (test functions):**

| File | Tests |
|---|---|
| test_auth | 19 |
| test_webapp | 8 |
| test_app | 14 |
| test_analytics_reports | 14 |
| test_data_loader | 11 |
| test_db_backup | 5 |
| test_db_repositories | 18 |
| test_db_schema | 12 |
| test_db_services | 16 |
| test_gui | 17 |
| test_prediction | 8 |
| test_preprocessing | 6 |
| test_synthetic | 6 |
| test_training | 7 |
| test_v11_features | 9 |
| test_v12_checkin_app | 3 |
| test_v12_workflow | 12 |
| test_validation | 13 |

  Parametrised tests account for the difference with the 223 collected.

### Findings

**T1 — High — The security and web tests skip in CI**
- **Where:** `tests/test_auth.py` and `tests/test_webapp.py` (`pytest.skip("database/eventpulse.db not present")`), `tests/test_v11_features.py` (processed data not published), `tests/test_gui.py` (PySide6 not in `requirements.txt`), and `.github/workflows/ci.yml`.
- **Problem:** the database is git-ignored, so CI would report green while silently skipping all 27 auth and web tests. pytest output in CI is the only signal.
- **Fix:**
  - Make the auth and web tests build their own database from the published synthetic data (`data/synthetic/`), so they never depend on the private database.
  - Fail CI if security tests are skipped (e.g. `-p no:skip` for those modules, or `pytest -rs` with a check).
  - Add `segno` and `python-multipart` to the CI install; they are already in `requirements.txt`.

**T2 — High — The production configuration is untested**
- **Problem:** nothing tests the real HTTPS, proxy and cookie setup. Missing tests:
  - `Secure` cookie flag when `EVENTPULSE_SECURE_COOKIES=1`.
  - Rate limiting with proxy headers (S2).
  - Trusted hosts and CSP (S6).
  - QR URLs with a public base URL (S7).
  - Behaviour after a restart with a persistent database.
- **Fix:** add these as tests run against the production image in CI.

**T3 — Medium — No test for restore with accounts present**
- **Problem:** restoring a pre-auth or older backup while the web app runs isn't tested (D2).
- **Fix:** add the test on a temporary copy, asserting that sessions are cleared and that the admin outcome is documented.

**T4 — Medium — No fresh-install or container test for the web app**
- **Problem:** no test builds a clean environment, installs only the documented requirements and starts `webapp.server` (B1, B2, M1). **Status: MANUAL** (needs a clean machine or container with network access).
- **Fix:** add a CI job: build the web image, provision a synthetic database and model, start it, log in, predict and log out.

**T5 — Medium — No CSRF test for multipart uploads; no test that hashes are never logged**
- **Fix:** add a CSRF check on `/api/predict/file`, and assert the access and audit logs contain no passwords or tokens.

**T6 — Medium — No load or concurrency tests**
- **Problem:** concurrent logins (S3 memory), concurrent SQLite writers (desktop + web) and many simultaneous QR check-ins at event start are untested.
- **Fix:** add a small Locust or k6 script against a staging copy.

**T7 — Low — No dependency or vulnerability check in CI**
- See P2.

### Items that still need manual testing (not claimed as passed)

1. Fresh-machine installation (macOS and Linux) from documented steps only.
2. Deployment to the real target with HTTPS: cookie flags, proxy IPs, trusted host, health checks.
3. Restore of a backup on a copy while the web server is running (D2).
4. Memory and CPU under concurrent logins on the target instance size (S3).
5. QR check-in from real phones through the public URL (S7).
6. The desktop app opening the migrated real database. It is expected to work (core schema v3, verified), but the `.app` wasn't launched in this audit.
7. `pip-audit` vulnerability scan.

---

## 10. Critical blockers

| # | Blocker | Findings |
|---|---|---|
| 1 | The web application (auth, roles, database, model registry) has **no deployment artifact**; only `app.py` is containerised and deployed | B1 |
| 2 | The configured host (Render free) **erases SQLite data** on every redeploy, restart or spin-down; no persistent storage plan | D1 |
| 3 | **No secure provisioning** for the database (now with personal data), the model registry and the required data files | B2, M1 |
| 4 | **HTTPS and secure cookies not enforced; proxy IP handling missing**, which breaks the rate limits and the account-lockout protection | S1, S2 |

(S1 and S2 are the Critical and High findings that must be closed before any public URL exists.)

## 11. Recommended fixes in priority order

1. **Choose the hosting model** (D1, D4). Either a paid instance with a persistent disk (SQLite, one instance), or managed PostgreSQL. Decide that the web server is the system of record.
2. **Build a web-app image and entrypoint** (B1, C1). `uvicorn webapp.server:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips=<proxy>`, a lock file (P1), and no `data/original/`.
3. **Secure provisioning** (B2, M1). Checksum-verified transfer of `eventpulse.db` and `models/` (or a bootstrap from `model/`), plus a documented admin setup on the server (`provision_admin --from-env`, then unset the variables).
4. **Production mode and security settings** (S1, S6, C2, S7, S13). `EVENTPULSE_ENV=production` fails closed unless: `EVENTPULSE_SECURE_COOKIES=1`, HTTPS with HSTS, `TrustedHostMiddleware`, CSP, docs disabled, `EVENTPULSE_PUBLIC_BASE_URL` set, CORS empty.
5. **Proxy-aware rate limiting and lockout redesign** (S2, S4, B4). Per-(account, IP) keys, progressive delays or CAPTCHA, one worker or shared storage.
6. **Bound password-hashing memory** (S3). A concurrency semaphore, or Argon2id with the OWASP low-memory profile (P3).
7. **CI that actually runs the security tests** (T1, T2, T4). A synthetic test database, a fail-on-skip check, a container smoke test of the web app, and `pip-audit` (P2).
8. **Backup and restore hardening** (D2, D3, D5). Auth-aware restore (clear sessions, stop the server), retention via `prune_backups`, encrypted off-site copies, a restore drill.
9. **Model loading** (B3, M2). Cache predictors, and verify the SHA-256 before `joblib.load`.
10. **Account lifecycle** (S5, S9, S10). Restrict or verify registration, add change-password and admin reset, and MFA for the admin.
11. **Housekeeping** (B5, B6, B7, B8, B9, S8, S11, S12, C3). Delete uploads, add structured logging, give friendly errors, rotate logs, harden or retire `checkin_app.py`, minimise `/api/health`.

## 12. Final deployment readiness verdict

| Use | Verdict |
|---|---|
| Local use on the organiser's Mac (127.0.0.1) | **READY.** Verified by 223 passing tests, a browser walkthrough of both roles, intact data and working backups |
| Trusted LAN (QR check-in from phones) | **READY with care.** Use `EventPulse_Web_LAN.command` on a trusted network only (no HTTPS, cookies not Secure; S1, S8) |
| Public internet / cloud (Render as configured) | **NOT READY.** 3 Critical findings (B1, B2, D1) plus S1 and S2 must be fixed first |

The application logic, access control and data integrity are in good shape. The gap is deployment engineering: packaging the web app, persistent storage, secure provisioning and production security configuration. None of these were in scope of the work done so far, and none is verified as working.
