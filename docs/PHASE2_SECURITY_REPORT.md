# EventPulse AI — Phase 2 Security Hardening Report

**Date:** 17 November 2026 · **Scope:** local development only · **Status:** complete, stopped for review

Phase 2 implements the 12 approved security items for the web app. The confirmed registration policy is applied:
- **Who can register:** college e-mail addresses at `@ncuindia.edu` only.
- **Approval:** new student accounts need Admin approval.
- **Admin:** exactly one Admin account.
- **Records:** students see only their own records.

Nothing was deployed or pushed to GitHub, and nothing connected to Render. No personal data was transferred.

These were not modified:
- the desktop application;
- the original SQLite database;
- the CSL236 files;
- `checkin_app.py`;
- the requirements files.

**There was no database schema change.** The core schema is still v3 and the web-auth migration track is still at 0001.

---

## 1. Checkpoint (created before any change)

Folder: `~/Downloads/Aiml /EventPulseAI_phase2_security_checkpoint_20261116/`

| File | Contents |
|---|---|
| `before_phase2_files.tar` (+ `.sha256`, verified OK before applying) | `webapp/`, `web/src`, `web/dist`, `web/build_esbuild.mjs`, `tests/`, `docs/` exactly as they were before Phase 2 |
| `before_phase2_project_manifest.sha256` | SHA-256 of all 21,423 project files (excludes `.git`, `mlruns`, `build`, `release`, `EventPulse AI.app`, `*.pyc`) |
| `before_phase2_real_data_baseline.txt` | Size, mtime and SHA-256 of everything under `database/` and `models/`, plus `model/model.joblib` |
| `before_phase2_db_integrity.txt` | Read-only (`immutable=1`) integrity check, schema version and row counts |
| `incoming/` | The Phase 2 package as delivered (`phase2_files.tar` + `.sha256`), and `fix1/` (the test fix, plus the file as it was before the fix) |
| `after_phase2_apply_project_manifest.sha256`, `after_phase2_project_manifest.sha256`, `after_phase2_real_data_baseline.txt`, `after_phase2_db_integrity.txt` | Evidence after the changes were applied |
| `smoke/` | The read-only desktop/web smoke check (`run_phase2_smoke.command`) and its log `checkpoint.log` |

**Rollback:** extract `before_phase2_files.tar` over the project folder and remove the two new files, `webapp/security.py` and `tests/test_security_phase2.py`. No database rollback is needed, because the database was never changed.

---

## 2. Changed files (12: 10 modified, 2 new)

The before/after project manifests show that exactly these 12 files changed out of 21,423. The only other change is new log output in `logs/`. The Mac copies were checked with `sha256sum -c` after copying.

| File | Change | SHA-256 before | SHA-256 after |
|---|---|---|---|
| `webapp/security.py` | **new**: configuration, proxy handling, progressive throttle, middleware | — | `f6b99717c62908bc82678d60afc7ccb607a9d534568cf43f92098d85fc89f072` |
| `webapp/auth.py` | domain rule, account-based limits, progressive delay, hashing cap, reset codes, `__Host-` cookie | `35551ee8…cb534bbe451` | `0b21f81480e278762c8a344b4698d43222728e47dc330de749ff74681be1d22a` |
| `webapp/server.py` | security middleware, production docs/CORS, minimal health, new QR check-in, 503 handler | `4b44ff30…5c72f435` | `de078dfd7bd9174f3aaa1b754eafd1e14778d17e26f9f5a6ef56ce39b3269579` |
| `web/src/App.jsx` | safe return to the QR page after sign-in, reset route, admin-only health details | `f97432a0…1dc24c` | `80239b161bc0653a8a470dbf14463cc3b02fce3515c192962870088dd4ce312a` |
| `web/src/pages/AuthPages.jsx` | college e-mail hint, approval message, **Reset password** page | `a17dfd83…eb281081` | `2092b9f014335d30a826b671bb2d6b70d6051addd324e1a06cc4429631049e6b` |
| `web/src/pages/Accounts.jsx` | **Reset code** button; the code is shown once to the Admin | `48c08ef2…cacca306` | `757c7878a3ac313ca1ae8d7e8bcf76c99526dfbd666ce2491baa81156bfe9143` |
| `web/dist/assets/app.js` | rebuilt frontend bundle (see note below) | `2b58f8e9…82a89a7` | `c6c2362ce7a76d6da2d60e77c9950b6726275244035d8e374389996c640675a6` |
| `tests/test_security_phase2.py` | **new**: 45 Phase 2 security tests | — | `02b1ea75d7bf79de25b8919c42307e79e809c0cbb460ffcc2ea633f23a41d256` |
| `tests/test_auth.py` | test e-mails changed to `@ncuindia.edu`; QR test rewritten for the logged-in flow | `40dde03b…5e25d14`* | `f88fb00861bebfda63a9c6db4cbc36b7e749ab96c724bcddc4ff62e8433ca779` |
| `tests/test_webapp.py` | health test checks the minimal body plus the admin-only details | `c3092d8e…14866` | `e956e537ae37753436aa14aa86ea66a1ee36a9a321626d1de27b581e7ce9888f` |
| `tests/test_pg_compat.py` | test e-mail domain; health details | `a405819f…d14`* | `65ae8bce7113ec134ed917167895a32e2611e5fad4f1a1347cca9da101bd2fa8` |
| `docs/AUTH.md` | Phase 2 rules documented | `1e36ef38…22a8a` | `523587f1e18f7e41d5fd92fc2987f50747576de85ae0c1e400f270147cad471e` |

\*Full before hashes:
- `test_auth.py`: `40dde03bda2a53bbc8282029331afdf0c615a8a97ebfb2c67a030a5cee3c707c`
- `test_pg_compat.py`: `a405819f368aafdbe4790cd0df9ef3d389028eb27da5aefd136f051243e25d14`

All before hashes are in `before_phase2_project_manifest.sha256`.

**Frontend build.** The bundle was rebuilt with the project's own offline build script (`web/build_esbuild.mjs`). Before any source edit, the toolchain was checked by rebuilding the unchanged sources: the result was **byte-identical** to your existing `app.js` (`2b58f8e9…`) and `app.css`. `app.css`, `index.html` and `logo.png` are unchanged.

### Changes to existing tests (no test was weakened or removed)
- **E-mail domain:** test e-mail addresses changed from `@uni.test` to `@ncuindia.edu`, because registration now requires the college domain.
- **`test_qr_checkin_still_works_and_is_guarded`:** previously asserted that an anonymous phone could check in by typing a Student ID. That is now forbidden by design (items 7 and 8). The test now asserts that the anonymous page returns 401 with no Student ID field, then checks in a verified student through the new flow. It is stricter than before.
- **`test_checkin_form_is_rate_limited`:** uses a small temporary per-address limit (20). The real ceiling of 5,000 is deliberately high for campus NAT. The test still proves the limiter returns 429.
- **Health tests:** now assert that the public body is exactly `{"status": "ok"}` and that the detailed fields come from the admin-only `/api/health/details`.

---

## 3. What was implemented (items 1–12)

| # | Item | Implementation |
|---|---|---|
| 1 | Production security configuration | `EVENTPULSE_ENV=production` **fails closed**: the server refuses to start (`ConfigError`) unless a valid `EVENTPULSE_PUBLIC_BASE_URL` (https), `EVENTPULSE_TRUSTED_PROXIES` and a PostgreSQL `EVENTPULSE_DB_URL` are set. Also refused: Secure cookies switched off, wildcard hosts, bad CIDRs, out-of-range values and an unknown `EVENTPULSE_ENV`. SQLite in production only with an explicit `EVENTPULSE_ALLOW_SQLITE_IN_PRODUCTION=1`. API docs (`/docs`, `/redoc`, `/openapi.json`) are off in production, and the development CORS origins are not loaded. **With no variables set, development behaves as before.** |
| 2 | HTTPS enforcement and secure cookies | In production, plain-HTTP `GET`/`HEAD` gets a **308 redirect** to the https base URL and plain-HTTP writes get **403**. HSTS is `max-age=31536000`. The Host header is checked against `EVENTPULSE_ALLOWED_HOSTS` (default: the base URL's host). The session cookie becomes `__Host-ep_session`: Secure, HttpOnly, SameSite=Strict, Path=/, no Domain. Browsers enforce the `__Host-` rules. |
| 3 | Trusted proxy configuration for Render | `X-Forwarded-For` and `X-Forwarded-Proto` are believed **only** when the direct peer is a configured trusted proxy (`*`, or IPs/CIDRs). Only the entry the proxy itself appended is used, counted `EVENTPULSE_PROXY_HOPS` from the right, so a client cannot choose its address by sending a fake header. For Render, the intended setting is `EVENTPULSE_TRUSTED_PROXIES=*` with 1 hop, because the service is reachable only through Render's proxy (this must be verified in Phase 3). |
| 4 | Account-based login and registration limits | Limits are keyed by **account**. Per-address limits are only high flood ceilings (sign-in 1,500 / 15 min, registration 400 / h, QR 5,000 / 10 min, reset 300 / 15 min; configurable), so a whole class behind one campus NAT address is not blocked together. Other limits: registration 5 attempts per e-mail and per claimed Student ID per hour; QR 10 per account per 10 min; reset 5 per account per 15 min. Over a limit → 429 with `Retry-After`. |
| 5 | Progressive login delay, no permanent lockout | Per account + address: 5 free failures, then waits of 1 s, 2 s, 4 s … up to **15 min**. Per account from any address: 20 free, then up to **5 min** (slows distributed guessing). While a wait is due the password is not even checked. A success resets both. Records are forgotten after 1 h without failures. Unknown e-mails are throttled identically, so throttling reveals nothing about which accounts exist. Signed-in sessions are never affected. |
| 6 | Password hashing concurrency protection | scrypt (~64 MB per hash) runs at most `EVENTPULSE_HASH_CONCURRENCY` at a time (default **2**). Other requests queue for up to 30 s, then get **503 + Retry-After: 5** instead of exhausting memory. Applies to hashing and verifying, including the dummy hash for unknown accounts. |
| 7 | QR check-in needs an authenticated, Admin-linked student | `/checkin/<event>?t=<token>` still needs the event's current secret token. **It also needs a signed-in student account whose Student ID the Admin has verified.** Signed out → 401 page with **Sign in to check in**; after sign-in the app returns to the QR page (only `/checkin/...` return paths are accepted, so this can't be used as an open redirect). Pending or rejected → 403. The Admin account → 403 (desk only). Revoked or wrong token → 403. Not registered for the event, event not today, or already checked in → clear 400 message. |
| 8 | QR records only the logged-in student's own ID | The page has **no Student ID field**. The ID is taken from the session (`linked_student_id`), never from the request: a `student_id` posted by hand is ignored. The POST also needs the session's CSRF token. |
| 9 | Admin-only desk check-in | `POST /api/attendance/{event}/checkin` stays on the Admin-only router. Signed out → 401, student → 403. |
| 10 | Security headers, minimal public health | Every response gets: CSP (`default-src 'self'; script-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'` …), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`, `Permissions-Policy` (camera, microphone, geolocation … off), `Cross-Origin-Opener-Policy` and `Cross-Origin-Resource-Policy: same-origin`, and `Cache-Control: no-store` on `/api` and `/checkin`. HSTS is added in production. `/api/health` returns only `{"status":"ok"}` (503 `unavailable` if the database is down, with no details). Version, database, schema and model information moved to the Admin-only `/api/health/details`. |
| 11 | CSRF on all state-changing requests | Three layers. (a) Every signed-in write needs the per-session `X-CSRF-Token` header, or the `csrf_token` form field on the QR page. (b) Middleware refuses **every** POST/PUT/PATCH/DELETE whose `Origin` is not this site, or whose `Sec-Fetch-Site` is `cross-site`. (c) The unauthenticated `/api/auth/*` endpoints accept JSON only, so a form on another site cannot submit them. A test checks every write route in the app: each one either requires a signed-in user or is one of the 5 deliberately public ones (register, login, logout, reset-password, QR check-in), all of which are protected by (b), (c) or the form token. |
| 12 | Admin-controlled password reset | The Admin clicks **Student accounts → Reset code**, sees a one-time code once (`XXXX-XXXX-XXXX`, about 59 bits, valid 30 min, replaces any earlier code), and hands it to the student in person. The student uses **Reset password**. On success the password changes and **every session of that account ends**. Codes are stored only as SHA-256 hashes; they are single-use and case-insensitive; they can't be used as a session cookie; they are refused for deactivated accounts and never issued for the Admin. Attempts are rate-limited and recorded in the security log (never the code itself). The code rows are marked rows in the existing `auth_sessions` table, so **no schema change** was needed. |

**Registration policy:**
- Only `@ncuindia.edu` addresses are accepted; the check is exact and case-insensitive. Sub-domains and look-alikes are refused, e.g. `x@mail.ncuindia.edu`, `x@ncuindia.edu.evil.com`, `ncuindia.edu@gmail.com`.
- New accounts are `pending` until the Admin verifies them. A pending account can sign in but sees no records and cannot check in.
- The role can't be chosen at registration. There is still exactly one Admin, enforced by the database's unique index.

---

## 4. Test results

### 4.1 Cloud workspace: SQLite, plain PostgreSQL, production-like PostgreSQL

These runs used the real psycopg 3.3.9 driver in its separate test environment. PostgreSQL databases held synthetic test data only. The SQLite tests worked on a copy made from a byte snapshot.

| Suite | Phase 2 result | Phase 1 result |
|---|---|---|
| SQLite (full) | **317 passed, 17 skipped, 0 failed** | 272 passed, 17 skipped |
| Plain PostgreSQL (full) | **264 passed, 70 skipped, 0 failed** | 219 passed, 70 skipped |
| Production-like PostgreSQL (SSL-only, SCRAM, private schema, Supabase-like roles; full) | **279 passed, 55 skipped, 0 failed** | 234 passed, 55 skipped |
| Final test file re-run, `test_security_phase2.py` + `test_auth.py` | SQLite under a **256 open-file limit** (macOS default): 64 passed. Production-like PG: 63 passed, 1 skipped. Plain PG: 63 passed, 1 skipped | — |

- **Skips:** the skip counts are identical to Phase 1. None of the new tests is skipped on any engine. The 1 skip in the re-runs is the existing SQLite-only check in `test_auth.py`.
- **Real-data guard:** every run ended with "REAL DATA GUARD: … unchanged".
- **Test servers:** before the final re-run, the two local test PostgreSQL servers in the cloud workspace had stopped while the session was idle waiting for your reply ("connection refused"). They were restarted with the same data directories and configuration; no code or test was changed.

### 4.2 Your Mac: full suite (`EventPulse_Web_Verify.command`)

| Run | Result |
|---|---|
| First run (`logs/web/verify_20261117_004125.log`) | 311 passed, 38 skipped, **1 failed**: `test_150_students_behind_one_campus_address_can_check_in` (see §5) |
| After the approved fix (`logs/web/verify_20261117_005519.log`) | **312 passed, 38 skipped, 0 failed, exit code 0** (Phase 1: 267 passed, 38 skipped). The guard reported "46 files under database/ and models/ unchanged". |

### 4.3 Your Mac: read-only desktop/web smoke check (`smoke/run_phase2_smoke.command`, log `smoke/checkpoint.log`)

The smoke check makes three throwaway copies of the database, opened read-only from the original. It starts the real web server (uvicorn, port 8199) on one copy, with a temporary Admin replacing the copied accounts. It runs the source desktop app on another copy and the packaged `.app` on the third, in a sandboxed home folder. The copies are deleted at the end.

- **Web:** 48/48 PASS — "WEB RESULT: ALL PASS". These run over real HTTP against uvicorn, and cover:
  - **Health and headers:** minimal health; CSP/frame/nosniff headers; no HSTS on local HTTP; health details 401 when signed out.
  - **Admin access:** unauthenticated Admin route → 401; Admin sign-in; HttpOnly cookie; the main Admin pages.
  - **CSRF and cross-site:** write without CSRF → 403; cross-site login → 403; form-encoded login → 403.
  - **Registration:** non-college e-mail → 422; college e-mail → pending.
  - **QR check-in:**
    - signed out → 401, and a POST while signed out → 401;
    - pending account → 403 for both the page and the POST;
    - after Admin approval, the student sees their own ID;
    - POST without CSRF → 403;
    - **a POST naming another student's ID recorded only the signed-in student's own ID**.
  - **Desk and records:** student desk check-in → 403; reading another student's record → 403; a student sees only their own registrations; Admin desk check-in works.
  - **Progressive delay:** 5 wrong passwords → 401 each, then 429, while the existing session keeps working.
  - **Reset code:** issued; redeemed; the old session ends; the code is single-use; the new password works.
  - **Sign-out:** logout; the session is invalid afterwards.
- **Desktop (current source):** main window visible, "EventPulse AI — Predict. Plan. Participate.", 1176×749, exit 0 (14.7 s).
- **Packaged `EventPulse AI.app` (13 Nov build):** running after 45 s, and its log shows it opened the throwaway copy.

### 4.4 Desktop, CSL236 and real data unchanged

| Check | Before | After |
|---|---|---|
| Fingerprint of `EventPulse AI.app`, `model/`, `main.py`, `gui/`, `app.py`, `Dockerfile`, `render.yaml`, `requirements*.txt`, `.github/`, `checkin_app.py` | `109a5c4b77bf01aa553fd2e54e4e2810aa8db386292fa3be6d9364a26fcf6c39` | `109a5c4b…6c39`: **DESKTOP AND CSL236 FILES UNCHANGED** |
| `database/eventpulse.db` SHA-256 | `c6b87cecd82efdb9bf17972482328888f769ec648b572916b8cec14ec45ff7c5` | **identical** (backups: 10 → 10) |
| Real-data baseline (size, mtime and SHA-256 of all of `database/`, `models/`, `model/model.joblib`) | `before_phase2_real_data_baseline.txt` | **identical** (`diff` empty) |
| Database integrity and counts | integrity ok, foreign keys 0, schema 3; 279 students / 36 events / 383 registrations / 383 attendance / 58 predictions / 4 users / 5 model versions; web-auth migration 1 | **identical** |
| Project manifest (21,423 files) | — | only the 12 files in §2 differ, plus new log output in `logs/` |

The log output is the two test-run logs and two startup lines in `logs/app.log`. Those lines come from the desktop source check, and they show it opened the throwaway copy.

---

## 5. Test failure on the Mac: what happened and the approved fix

**Failure.** On the first Mac run, `test_150_students_behind_one_campus_address_can_check_in` failed while creating test clients:
```
core.db.errors.MigrationError: This database uses schema version 3, newer than this app supports (0).
```
The log also showed `AttributeError: '_UnixSelectorEventLoop' object has no attribute '_ssock'`.

**Root cause.** The test process ran out of open files. This was a test-harness resource limit, not an application or security fault.
- **Too many clients open at once:** the test created one test client per simulated student (150, plus 10 for mistyped passwords) and kept them all open until the test ended. Each client holds its own event loop and a few open files.
- **macOS limit:** Terminal allows **256 open files per process** by default; the cloud workspace allows far more, which is why it passed there.
- **Why the error was misleading:** once the limit was reached, Python's directory listing (`glob`) failed silently and returned no files. The migration code therefore saw "0 migrations" and produced the error above.
- **The `_ssock` message:** this happens when an event loop can't create its internal socket because no file handles are left.

**Confirmation.**
- **Reproduced:** with the open-file limit set to 256 in the cloud workspace, the unchanged test failed with exactly the same error.
- **Fix confirmed:** on a throwaway copy, the fixed test passed under the same limit.

**Fix (approved, test file only).** Each simulated student's client is now opened with `with TestClient(app) as c:`, so it is closed when that student finishes; the same applies to the 10 mistyped-password clients.
- **Test unchanged otherwise:** 150 separate sessions from one campus address, 20 at a time, the same assertions (150 `assert` statements before and after), and the same success criterion: every student checks in and exactly those 150 IDs are recorded.
- **Hashes:** before `b2be98ec7b19fdfd3b2d2da30cd2fa298659085088d8b7db55dd55871022297d`, after `02b1ea75d7bf79de25b8919c42307e79e809c0cbb460ffcc2ea633f23a41d256`. The after-hash matches the validated copy. The exact before file and the diff are kept in the checkpoint's `incoming/fix1/`.
- **Result:** the full Mac suite then passed (§4.2).

**Why the app itself is not affected.** The real server is one process with one event loop. Its open-file use does not grow per signed-in student the way the test's did.

**Possible later hardening (not done, needs approval).** Make the migration loader fail loudly when it finds no migration files, instead of reporting a misleading schema-version error.

---

## 6. Security verification checklist (as requested)

| Requirement | Evidence | Result |
|---|---|---|
| Students cannot access another student's records | `test_students_reach_only_their_own_records_and_no_admin_route` (query parameters can't choose the student; the `/api/student/*` responses of each student never contain the other's ID); smoke check "student cannot read another student (403)" | **Verified** |
| Students cannot access Admin routes | Same test: **every** Admin route returns 403 for a signed-in student, including `/api/health/details` and `/api/admin/students/{id}`; existing `test_auth.py` checks | **Verified** |
| QR cannot be used without login | `test_qr_requires_login_and_records_nothing_without_it` (GET/POST 401, forged cookie 401, no attendance recorded); smoke check | **Verified** |
| Pending accounts cannot check in | `test_pending_rejected_and_admin_accounts_cannot_check_in` (pending, rejected and Admin all 403, nothing recorded); smoke check | **Verified** |
| 150 students behind one public IP can check in without unfair blocking | `test_150_students_behind_one_campus_address_can_check_in`: 150 verified students from one address, 20 at a time, 10 of them mistype passwords first → all 150 sign in and check in, exactly those 150 IDs recorded, hashing never above the cap. About 24 s in the cloud workspace; passed on the Mac (in the suite's 6 min 56 s total) | **Verified** |
| Existing desktop application still works | Mac suite (including GUI tests), desktop source window opened, packaged `.app` ran 45 s on a copy; fingerprint unchanged | **Verified** |
| QR records only the signed-in student's own ID | `test_qr_records_only_the_signed_in_students_own_id`; smoke check (a posted other ID was ignored) | **Verified** |
| Desk check-in Admin-only | `test_desk_checkin_is_admin_only`; smoke check | **Verified** |
| CSRF on all state-changing requests | `test_every_state_changing_route_is_csrf_protected` (checks every route), `test_signed_in_requests_without_csrf_token_are_refused`, `test_cross_site_and_form_posts_to_public_endpoints_are_refused`, QR CSRF tests | **Verified** |
| Production fail-closed / HTTPS / cookies / docs | 10 fail-closed cases; the server refuses to start; production subprocess test: 308 redirect, HTTP writes 403, Host 400, HSTS, `__Host-` Secure cookie, docs off, development origin refused | **Verified (simulated proxy; see risks)** |
| Hashing cap | `test_password_hashing_concurrency_is_capped` (peak ≤ 2 of 8 parallel); `test_hashing_busy_returns_503_not_a_crash` | **Verified** |
| Admin-controlled reset | 3 tests (flow, expiry/replacement/permissions, attempt limit); smoke check | **Verified** |

---

## 7. Remaining risks and limitations

1. **In-memory limits.** Limits, delays and hashing slots live in one server process. They reset on restart (including Render's free-tier sleep) and are not shared between processes. Production must run a single worker (`uvicorn` without `--workers`); more instances would need a shared store.
2. **Shared-address flooding.** A deliberate attacker on the campus network could use up the high per-address ceiling (for example 1,500 sign-in attempts in 15 min) and slow sign-ins for that whole address until the window passes. Already signed-in students are not affected.
3. **Targeted slow-down.** Someone who knows a student's e-mail can trigger that account's progressive delay: at most 5 min from other addresses, 15 min from the same address. There is no permanent lockout and no effect on existing sessions. There is no Admin "clear delay" button yet; a restart clears it.
4. **Hashing speed on the free tier.** Measured here at about 0.3 s per hash with 2 at a time. On Render's shared free CPU, 150 students signing in at the same moment may queue, and some could get "busy, try again" (503) after 30 s. Recommendation: students sign in before the event (sessions last up to 12 h), so check-in itself needs no hashing.
5. **HTTPS, HSTS and proxy settings are verified only by simulation.** They were tested with `X-Forwarded-Proto`/`X-Forwarded-For` headers, not against a real TLS endpoint or Render's actual proxy. Phase 3 must confirm Render's header format (1 hop) and that the service can't be reached except through the proxy.
6. **Phone cookie behaviour not tested on real phones.** With `SameSite=Strict`, some phone browsers may not send the session cookie when the QR link is opened from the camera app. The page then offers **Sign in** and **"Already signed in? Continue"** (a same-site link that does send the cookie). This was not tested on real phones because Phase 2 is local only.
7. **No password reset for the Admin.** The web never resets the Admin's password (by design). `provision_admin` cannot reset it either; it refuses to touch an existing Admin. A forgotten Admin password currently needs manual recovery. Suggested for Phase 3: a local `provision_admin --reset-password` command, with your approval.
8. **Where reset codes are stored.** Reset codes use marked rows of `auth_sessions` to avoid a schema change in Phase 2; they are safe and tested. With the fresh PostgreSQL start in Phase 3, a dedicated `password_resets` table is cleaner.
9. **Existing accounts.** The 4 accounts already in your local database were not changed. The college-domain rule applies to new registrations; existing accounts can still sign in. Phase 0 chose a fresh start for the hosted version.
10. **CSP inline styles.** CSP allows inline styles (`'unsafe-inline'` for styles only), because the check-in page and React style attributes use them. Scripts are restricted to `'self'`, with no inline script and no `eval`.
11. **Requests without an Origin header.** The cross-site check lets through requests that have no `Origin` header, such as non-browser tools. Browsers always send it on POST, and signed-in writes still need the CSRF token.
12. **Check-in date and time zone.** Check-in uses the server's local date (existing behaviour). On a UTC host, check-in near midnight IST could be refused. Phase 3 should set `TZ=Asia/Kolkata`.
13. **Carried over from Phase 1:**
    - psycopg is not installed on your Mac (by design), so the PostgreSQL suites ran only in the cloud workspace's test environment;
    - Render private-network TLS is unverified;
    - the trigger function in `auth_migrations/postgres/0001_accounts.sql` has no pinned `search_path`.
14. **Misleading migration error.** When file handles run out, the migration loader reports a wrong "schema version" error (§5). Hardening it is optional and was not done.

---

## 8. How to configure production later (Phase 3; not done now)

```
EVENTPULSE_ENV=production
EVENTPULSE_PUBLIC_BASE_URL=https://<your-service>.onrender.com
EVENTPULSE_TRUSTED_PROXIES=*          # only because Render's proxy is the sole entry point
EVENTPULSE_PROXY_HOPS=1
EVENTPULSE_DB_URL=postgresql://...    # private schema, app role (Phase 1)
# optional: EVENTPULSE_ALLOWED_HOSTS, EVENTPULSE_HASH_CONCURRENCY (default 2),
#           EVENTPULSE_ALLOWED_EMAIL_DOMAINS (default ncuindia.edu)
```
Run a single uvicorn worker. If any required value is missing, the server will refuse to start and list what is wrong.

---

**Phase 2 is complete. Work has stopped here for your review. Phase 3 has not been started.**
