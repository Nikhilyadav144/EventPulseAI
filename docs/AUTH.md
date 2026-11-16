# EventPulse AI — Authentication & Role-Based Access Control

Applies to the web application (`webapp/`, `web/`). The desktop app and `app.py` (CSL236 API)
are unchanged.

## Roles

| Role | How it is created | Can see / do |
|---|---|---|
| **Administrator** (exactly one) | `EventPulse_Admin_Setup.command` (or `python -m webapp.provision_admin`) on the computer that runs the app | Everything: all 14 migrated pages, **Students** (every student record with registrations, attendance and predictions) and **Student Accounts** (verify / reject / deactivate accounts, security log) |
| **Student** | Self-registration at `#/register` | Own dashboard, own registrations, own attendance, own predictions — and only after the administrator has verified the Student ID |

### Pages per role

* **Administrator**: Overview, Dataset Explorer, Event Management, Event Calendar, Registration
  Management, Attendance Prediction (incl. What-if Planner), Attendance Records (incl. QR codes),
  Students, Model Laboratory (incl. MLflow runs), Analytics, Insights, Model Monitoring,
  Reports & Export, Student Accounts, Notifications, Settings.
* **Student**: My dashboard, My registrations, My attendance, My predictions.
* **Not signed in**: Sign in, Create a student account (college e-mail only), Reset password (with a
  code from the administrator). The QR check-in page only asks them to sign in (Phase 2).

## Administrator setup (one time)

1. Double-click **`EventPulse_Admin_Setup.command`** in the project folder.
2. Type the administrator e-mail, name and a password (10+ characters with letters and numbers;
   it is not shown while typing and is stored only as a salted hash).
3. Start the app with `EventPulse_Web.command` and sign in at http://127.0.0.1:8100/.

Non-interactive alternative (e.g. a server): set `EVENTPULSE_ADMIN_EMAIL`, `EVENTPULSE_ADMIN_NAME`,
`EVENTPULSE_ADMIN_PASSWORD` and run `.venv/bin/python -m webapp.provision_admin --from-env`, then unset
the variables. `--status` reports whether an administrator exists.

Guarantees: the password is never in source code, frontend code or logs. If an administrator already
exists the command creates, changes and resets nothing (exit code 3). The database enforces a single
administrator (unique partial index) and forbids changing any account's role (trigger).

## Student registration, sign-in and sign-out

1. Open http://127.0.0.1:8100/ → **Create a student account**.
2. Enter full name, e-mail, the **pseudonymous Student ID given by the organiser** (e.g. `STU029` —
   roll numbers, phone numbers and e-mails are refused, as everywhere else in EventPulse AI),
   password and confirmation. All fields are validated on the server; the role is always `student`
   (a request that tries to send a `role` is rejected).
3. Sign in with e-mail and password. The account works immediately, but records appear only after
   the administrator verifies the Student ID (see below).
4. **Sign out** is at the bottom of the sidebar. It deletes the server-side session; the old cookie
   cannot be reused.

## Linking accounts to existing student records

Existing student records are pseudonymous (no names or e-mails), so **nothing is linked
automatically** — not by name, not by e-mail, not by the claimed ID alone.

1. The student registers with the Student ID they were given. The account is `pending`.
2. The administrator opens **Student Accounts**, confirms with the student in person or through an
   official channel that the ID is really theirs, and clicks **Verify**. Only IDs that exist in the
   `students` table can be verified (otherwise: import/create the record first).
3. **Reject ID** releases a wrongly claimed ID (the real owner can then register with it);
   **Unverify** hides the records again; **Deactivate** blocks sign-in and ends all sessions.

A student's data is always selected with the Student ID stored on the verified account in the
server-side session — never with an ID from the URL, query string or request body.

## Security controls

| Area | Implementation |
|---|---|
| Passwords | Argon2id if `argon2-cffi` is installed, otherwise standard-library **scrypt** (N=2^17, r=8, p=1, 16-byte salt) — OWASP-recommended. Plaintext is never stored or logged. Unknown e-mails still run a hash check (no timing difference). |
| Sessions | Random 256-bit token in cookie `ep_session`: **HttpOnly**, **SameSite=Strict**, **Secure** when `EVENTPULSE_SECURE_COOKIES=1` (set this behind HTTPS). Only the SHA-256 of the token is stored. Idle timeout 2 h, absolute 12 h (`EVENTPULSE_SESSION_IDLE_MINUTES`, `EVENTPULSE_SESSION_HOURS`). Deleted on logout and on deactivation. |
| CSRF | Every state-changing request needs the per-session token in `X-CSRF-Token` (kept in JavaScript memory only — nothing is put in localStorage/sessionStorage). |
| Brute force (Phase 2) | Failed sign-ins → progressive delay (no permanent lockout): per account+address 5 free, then 1 s, 2 s, 4 s … max 15 min; per account from any address 20 free, then … max 5 min; success resets. Per-address limits are only high ceilings (sign-in 1500 / 15 min, registration 400 / h, QR 5000 / 10 min) so a whole class behind one campus NAT address is not blocked. Registration 5 attempts per e-mail / Student ID per hour; QR 10 per account / 10 min; reset codes 5 attempts per account / 15 min. Client addresses come from `webapp/security.py` (X-Forwarded-For only from trusted proxies). |
| Errors | Sign-in failures always say "Invalid email or password." 401 = not signed in, 403 = wrong role / missing CSRF token. |
| Authorization | Reusable dependencies in `webapp/auth.py`: `require_user`, `require_admin`, `require_student`. All existing `/api/*` data routes are on an admin-only router; `/api/admin/*` is admin-only; `/api/student/*` is student-only and ownership-scoped. Public: `/api/health`, `/api/auth/*`, the built frontend, `/checkin/*`. |
| QR check-in (Phase 2) | Needs the event's secret token **and** a signed-in student account the administrator has verified. Attendance is recorded only for that account's verified Student ID (taken from the session; no Student ID is read from the request). The POST needs the session's CSRF token. Pending/rejected accounts and the administrator are refused (desk check-in is admin-only). |
| Headers | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`, `Cache-Control: no-store` on the API. |
| Audit | `auth_audit` table: registrations, sign-ins, failed sign-ins, logouts, account changes (no passwords, tokens or unknown e-mails). Visible to the administrator under Student Accounts. |

## Database migration

* Same SQLite file (`database/eventpulse.db`), **separate migration track**
  `web_auth_migrations` with `webapp/auth_migrations/0001_accounts.sql`. The core
  `schema_migrations` stays at version 3, so the installed desktop app (which refuses databases with
  a newer core schema) keeps working.
* Additive only — new tables `users`, `auth_sessions`, `auth_audit` (+ index and trigger). No
  existing table, row, view or MLflow file is modified.
* Applied automatically when the web server starts; a verified backup
  `database/backups/eventpulse_pre_auth_migration_<timestamp>.db` is written first. Checksummed: an
  edited migration file stops the server instead of silently diverging.
* **Reverse:** `.venv/bin/python -m webapp.provision_admin --rollback-auth` (asks for confirmation,
  backs up first, drops only the auth tables and their migration record — accounts and sessions are lost, nothing else).
* Names and e-mails of account holders live only in `users`; they are not included in reports,
  exports, datasets or predictions.

## Tests

`tests/test_auth.py` (19 tests) runs against a temporary copy of the database: registration and
hashing, server-side validation, duplicate rejection, no admin via registration (and the database
refusing a second admin / role change), provisioning never overwriting, admin and student sign-in,
generic errors, rate limiting, every admin route returning 403 for students and 401 when signed out,
ownership (no access to another student's data by changing IDs or parameters; unverified claims see
nothing), CSRF, logout and idle expiry, deactivation, QR check-in still working and guarded, admin
functionality intact, and the migration being additive, backed up and reversible.
`tests/test_webapp.py` signs in as the administrator; its assertions are unchanged.

## Known limitations

* Rate-limit counters are in memory (reset when the server restarts; one server process).
* Over plain HTTP (local / LAN mode) cookies cannot be `Secure`; enable HTTPS and
  `EVENTPULSE_SECURE_COOKIES=1` before any hosting.
* No password reset by e-mail and no "change password" page yet. A forgotten **student** password:
  the administrator deactivates the account and the student registers again after **Reject ID** has
  released the Student ID. A forgotten **administrator** password: restore is manual — roll back the
  auth tables (`--rollback-auth`, which removes all accounts) and provision again.
* New students who are not yet in the `students` table can create an account but cannot be verified
  until their record is imported.
* `/docs` (API documentation) is public; it lists endpoints but no data.

## Phase 2 security hardening (summary)

* `webapp/security.py`: `EVENTPULSE_ENV=production` fails closed unless HTTPS base URL, trusted
  proxies and a PostgreSQL URL are configured; HTTPS redirect/refusal, Host validation, HSTS,
  `__Host-` Secure cookie, API docs off. Security headers (CSP, nosniff, frame denial, referrer,
  permissions and cross-origin policies) in every mode.
* Cross-site requests refused on every state-changing request (Origin / Sec-Fetch-Site); sign-in
  endpoints accept JSON only; signed-in requests need the `X-CSRF-Token` header (forms: `csrf_token`).
* Password hashing capped at `EVENTPULSE_HASH_CONCURRENCY` (default 2) concurrent hashes; when busy
  for 30 s the request gets HTTP 503 + Retry-After instead of exhausting memory.
* Registration only with `@ncuindia.edu` (`EVENTPULSE_ALLOWED_EMAIL_DOMAINS`); accounts stay
  pending until verified. Exactly one administrator (database unique index).
* Admin-controlled password reset: **Student accounts → Reset code** shows a one-time code
  (XXXX-XXXX-XXXX, 30 min, hashed at rest, replaces earlier codes). The student uses it on
  **Reset password**; all of their sessions end. The administrator's own password cannot be reset
  through the web (by design); there is no admin-reset command yet (see the Phase 2 report). Codes are stored as marked rows of `auth_sessions`, so no
  schema change was needed.
* `/api/health` is public and returns only `{"status": "ok"}`; details are at the admin-only
  `/api/health/details`.
* Full details and test evidence: `docs/PHASE2_SECURITY_REPORT.md`.
