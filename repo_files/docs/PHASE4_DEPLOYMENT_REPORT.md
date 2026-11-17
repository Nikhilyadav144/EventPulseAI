# EventPulse AI — Phase 4 Deployment Report (GitHub + Render)

**Date:** 17 November 2026 · **Live URL:** https://eventpulse-ncu.onrender.com · **Status:** deployed and verified on the real public URL. The few items not testable are marked **NOT VERIFIED** below.

No passwords, database URLs, tokens or other credentials appear in this report.

---

## 1. GitHub

- **Repository:** https://github.com/Nikhilyadav144/EventPulseAI. It is **private**: the push script confirmed it through GitHub's API under your sign-in, after the visibility was changed from public on 17 Nov 2026.
- **Before the push:** the repository was confirmed **empty**, with no commits or branches. The local folder had no previous Git history.
- **Commit `4d0f65b`** on branch `main`. Exactly **103 files**, identical to the audited and approved list; every file was checked against its approved SHA-256 hash (`phase4_approved_files.sha256` in the checkpoint folder).
- **No force-push;** no remote history was touched.
- **Excluded:** `database/`, `data/original/`, `data/processed/`, `.venv`, `.env`, all SQLite databases including `mlflow.db`, `models/`, `model/model.joblib`, logs, exports, backups, checkpoints, caches, `docs/screenshots/`, `README.md` (team roll numbers), `tests/` (roll-number-like examples), and the CSL236/desktop files (`Dockerfile`, `render.yaml`, `.github/`, `gui/`, `main.py` …). Those files are untracked and unchanged on your Mac.
- **`web/dist/`:** only its 4 built files were added, each by exact name. They are byte-identical to the verified build (`app.js` `c6c2362c…`).

**Committed files (103):**
- **Root (5):** `.env.example`, `.gitignore`, `app.py`, `checkin_app.py`, `requirements-web.txt`
- **Code:** `config/` (2), `core/` (31), `webapp/` (11)
- **Frontend:** `web/` (32 source files + `web/dist/` 4)
- **Synthetic data (6):** `data/event_attendance*.csv/json`, `data/synthetic/*`
- **Static files:** `static/` (2), `assets/logo_mark.png`
- **Scripts:** `scripts/postgres/` (3)
- **Docs (6):** `AUTH.md`, `DEPLOYMENT_RENDER.md`, `SYNTHETIC_DATA.md`, and the Phase 1/2/3 reports

The full list is in `phase4_approved_files.txt`, in the checkpoint folder.

**Checks before the push:**
- **Isolation test:** the 103-file tree, alone, passed the hosted test set (70 passed, 2 skipped because they need the local database, which is deliberately absent).
- **Push script safety tests:** decoy database, `.env` and log files were not pushed; an existing history, a non-empty remote and a changed file each stopped the script; cancelling worked.
- **The real push:** the script's name and content scan found no keys, tokens, credentialed URLs, personal e-mails or roll numbers.

---

## 2. Render configuration (no secrets)

| Item | Value |
|---|---|
| Workspace / project | My Workspace → My project → Production |
| Database | `eventpulse-db`: Render PostgreSQL **16**, **Free**, region **Singapore**; storage autoscaling off, no connection pool |
| Web service | `eventpulse-ncu`: **Python 3**, **Free**, region **Singapore**, repository `Nikhilyadav144/EventPulseAI`, branch `main` |
| Build command | `pip install -r requirements-web.txt && python -m webapp.hosted_setup build-models --out models_hosted` |
| Start command | `uvicorn webapp.server:app --host 0.0.0.0 --port $PORT --workers 1 --no-proxy-headers` (Render also sets `WEB_CONCURRENCY=1`) |
| Health check | `/api/health` |
| Python | 3.12.8 |
| Database URL | **Internal** connection URL (the app adds `sslmode=require`, connect timeout and application name) |
| Environment variables | `EVENTPULSE_ENV=production`, `EVENTPULSE_PUBLIC_BASE_URL=https://eventpulse-ncu.onrender.com`, `EVENTPULSE_TRUSTED_PROXIES=*`, `EVENTPULSE_PROXY_HOPS=1`, `EVENTPULSE_DB_ALLOW_PUBLIC_SCHEMA=1`, `EVENTPULSE_TIMEZONE=Asia/Kolkata`, `TZ=Asia/Kolkata`, `EVENTPULSE_SEED_SYNTHETIC_DEMO=1`, `EVENTPULSE_HOSTED_MODELS_DIR=models_hosted`, `PYTHON_VERSION=3.12.8`; secrets you entered yourself: `EVENTPULSE_DB_URL`, `EVENTPULSE_ADMIN_EMAIL`/`_NAME`, and `EVENTPULSE_ADMIN_PASSWORD` (to be deleted after the first sign-in, §6) |

### Deployment history
1. **First deploy: build succeeded, start-up failed as designed.** The Admin password was shorter than 10 characters, so the app refused to start. The log named only the rule, never the password. Migrations had already run; nothing was half-created.
2. **Second deploy (password corrected): Live.** The service was available at the primary URL, and the health checks returned 200.
3. **Third deploy (after you deleted `EVENTPULSE_ADMIN_PASSWORD`): restart test, see §3 item 17.**

---

## 3. Public deployment verification (on https://eventpulse-ncu.onrender.com)

Testing was done from the browser pane in the Claude desktop app on your Mac (real internet, real Render). Test accounts: `test.student.alpha@ncuindia.edu` → `SYN-STU-00001` and `test.student.beta@ncuindia.edu` → `SYN-STU-00002`, with randomly generated test-only passwords. The Admin sign-in was typed by you; the Admin password was never seen or handled here.

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | HTTPS works | **PASSED** | Page served over HTTPS; `Strict-Transport-Security: max-age=31536000`; a plain `http://` request ended up on `https://` |
| 2 | React frontend loads without errors | **PASSED** | Sign-in page rendered ("Sign in · EventPulse AI"); no JavaScript errors in the console (only the 401/403/422 responses that the tests provoked on purpose) |
| 3 | Health endpoint | **PASSED** | `/api/health` → 200 `{"status":"ok"}`; `/api/health/details` → 401 signed out, 403 for students |
| 4 | Admin login | **PASSED** | You signed in with the bootstrapped Admin; `/api/auth/me` → role `admin` |
| 5 | Registration needs a college e-mail | **PASSED** | `x@gmail.com` → 422 "use your college e-mail address (@ncuindia.edu)"; `@ncuindia.edu` → 201 |
| 6 | New accounts pending | **PASSED** | Status `pending`; the pending student sees no records ("waiting for the organiser") |
| 7 | Admin approves | **PASSED** | Both test accounts were verified (200) |
| 8 | Student signs in after approval | **PASSED** | Alpha and Beta signed in; status `verified` |
| 9 | Each student sees only their own records | **PASSED** | Alpha: EV01 plus 2 synthetic events, no mention of Beta; Beta: EV01 plus 1 synthetic event, no mention of Alpha |
| 10 | Students blocked from Admin and research functions | **PASSED** | 403 for overview, students, another student's record, accounts, predictions, health details and desk check-in |
| 11 | Admin and research functions | **PASSED** | 200 for overview, models, analytics, insights, monitoring, registrations, reports, datasets, calendar and the security log |
| 12 | QR check-in on the deployed HTTPS domain | **PASSED** in the browser | QR link `https://eventpulse-ncu.onrender.com/checkin/EV01?t=…`; QR PNG generated; signed out → 401 "Sign in to check in" (no Student ID field); signed in → page shows the student's own ID; clicking **Check me in** → "Checked in: SYN-STU-00001 at 03:57 (IST)" |
| 12a | **Scanning with a phone camera** | **NOT VERIFIED** | No phone was used. Also untested: whether a phone browser sends the session cookie when the link comes from the camera app (the page then offers "Sign in" or "Already signed in? Continue") |
| 13 | Desk check-in is Admin-only | **PASSED** | Admin desk check-in for Beta → 200 (repeat → 409); a student → 403 |
| 14 | Attendance and predictions | **PASSED** | Per-event predictions (0.65 / 0.66) and a single prediction with 3 organiser tips; attendance shows Beta (DESK) and Alpha (QR); overview figures shown with the "SIMULATED outcomes" notice |
| 15 | Check-in dates follow Asia/Kolkata | **PASSED** | Render's clock was still **16 Nov** in UTC (22:27 UTC); the event dated **17 Nov** (IST) accepted check-in, and the confirmation showed **03:57 (IST)**. The exact midnight boundary was tested before deployment (Phase 3), not live |
| 16 | Security headers, redirects, cookies, CSRF | **PASSED**, cookie partly | Live: CSP, `frame-ancestors 'none'`, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, COOP, HSTS, `no-store`; API docs not served; write without CSRF token → 403; form-encoded login → 403; QR submission without CSRF → 403; wrong QR token → 403; repeat check-in refused; session cookie **HttpOnly** (invisible to JavaScript). The cookie's Secure, `__Host-` and SameSite=Strict attributes are enforced by the production configuration and were verified in the Phase 3 rehearsal; the browser pane can't display them live. **NOT VERIFIED live:** cross-site request refusal (needs a second website; verified in local tests and the rehearsal) |
| 17 | Restart: no duplicate Admins, migrations or synthetic records | **PASSED**, if the third deploy ran | After the redeploy: one `admin_provisioned` event ever; 600 students (600 distinct, 0 non-synthetic); 49 events; 5 model versions; schema 3; today's test check-ins still present (the database persisted across the restart) |
| 18 | Logs hold no secrets | **PASSED** for QR tokens, **PASSED (rehearsal)** for passwords | Render's live log shows `GET /checkin/EV01?t=[redacted]`. The Admin password does not appear in logs in the Phase 3 rehearsal (0 occurrences); you can confirm by searching your Render logs for it. Note: the info-level "Seeded …" message is not printed by Render's logger configuration, so the seed is verified through the database counts instead |

**Not tested live:**
- **Waking from sleep:** the cold start after 15 minutes idle (about 1 minute, per Render).
- **Free-tier expiry:** the free database's 30-day expiry.

---

## 4. Synthetic data

- **What was loaded:** at first start, `EVENTPULSE_SEED_SYNTHETIC_DEMO=1` loaded `data/synthetic/synthetic_registrations.csv` into the **empty** hosted database. That is **600 students** (`SYN-STU-#####`), **48 events** (`SYN-EV-###`) and **783 registrations**, every row labelled `Data_Source = SYNTHETIC`.
- **Labelling:** the database is flagged "demo" (`/api/health/details` → `demo: true`). The overview carries the notice that attendance outcomes are **simulated**.
- **Duplication:** the seed runs only when there are no student records, so restarts can't duplicate it (verified, item 17). No desktop records or personal data were uploaded or imported.
- **Models:** five models (Logistic Regression active), trained at build time on the synthetic dataset only.
- **Test data added during verification:** event `EV01` ("Phase 4 live test (synthetic)", 17 Nov 2026), two test student accounts (Alpha and Beta), and their two check-ins. You can keep them for the demo or use fresh accounts.

---

## 5. Security summary

- **Private repository**; there are no secrets anywhere in the repository. Secrets exist only in Render's environment, entered by you.
- **Production mode refuses to start when unsafe:** proven live by the first deploy (short Admin password).
- **Database connection:** HTTPS-only app with HSTS; database reached over the internal network with `sslmode=require`; a single worker.
- **Accounts:** the college-domain rule, Admin approval, own-records-only access, an Admin-only desk, QR check-in that needs sign-in + approval + the CSRF token + the current QR token, and the Admin created once from secrets and never reset.
- **Unchanged locally:** the desktop app, its SQLite database (`c6b87cec…`, integrity ok, schema 3, counts unchanged) and the CSL236 files (fingerprint `4cefb8e3…`).

---

## 6. Manual steps still required from you

1. **If you haven't yet**, make sure `EVENTPULSE_ADMIN_PASSWORD` is **deleted** in Render → Environment. Then sign in as Admin once more to confirm the account still works. It does: your last sign-in came after the redeploy.
2. **Before presenting:** open the site **5 minutes early** to wake the free service (§8).
3. **Optional:** remove the test accounts and test event after the demo, or keep them as demo material.
4. **After the presentation:** the free database **expires on about 16 Dec 2026** (30 days after creation) and is **deleted** about 14 days later. Back it up first if you want to keep it (`scripts/postgres/pg_backup.py`, which needs PostgreSQL client tools on your Mac), or simply let it go.

---

## 7. Rollback

- **Website:** in Render, suspend or delete the web service `eventpulse-ncu`; the database stays. Alternatively, use Render's **Rollback** to a previous deploy.
- **Database:** delete `eventpulse-db` in Render. This is permanent; free databases have no backups.
- **GitHub:** the repository holds one commit. Make it private-only (it already is) or delete it in its GitHub settings.
- **Your Mac:** only a `.git` folder was added to the project. Deleting `.git` returns the folder to its exact pre-Phase-4 state (manifest in `EventPulseAI_phase4_github_checkpoint_20261117/`). No other project file was changed by Phase 4.

---

## 8. Presentation guide (18 November 2026)

**URL:** https://eventpulse-ncu.onrender.com

**Admin login:** use the Admin e-mail and password you set in Render (in your password manager; never written here). Use a private or incognito window for the student, so both can be signed in at once.

### 0. Five minutes before
- Open the URL. If it shows Render's loading page, wait about a minute; the free service was asleep. Reload until the sign-in page appears.
- Sign in as Admin in a normal window.
- Open a private window for the student.

### 1. Admin overview and research mode (Admin window)
- **Overview:** the KPIs, the simulated-outcome notice, the active model.
- **Model Laboratory:** compare the 5 models trained on synthetic data.
- **Analytics** (by Department), **Insights**, **Monitoring**, **Dataset Explorer** (synthetic dataset), **Reports**.

### 2. Student registration and approval
1. **Private window:** click **Create a student account**. First try a Gmail address to show the college-only rule (refused). Then register with a demo address such as `demo.student@ncuindia.edu` and a free synthetic ID, e.g. `SYN-STU-00010`.
2. **Sign in** as that student: the dashboard shows "waiting for the organiser"; no records are visible yet.
3. **Admin window:** **Student Accounts** → the new account is under *Waiting for verification* → **Verify**.
4. **Student window:** reload. The dashboard now shows that student's synthetic registrations and attendance, and only theirs.

### 3. Attendance and QR check-in
1. **Admin:** **Events** → create an event dated **today**, then register the demo student for it (Registrations, or the event's page).
2. **Admin:** **Attendance Records** → the event → **Show QR code**.
3. **Student window:** open the QR link (or scan it with a phone; scanning is not yet verified). The page shows the student's own ID → **Check me in** → "Checked in … (IST)".
4. **Admin:** the check-in appears. Show **desk check-in** for another registered student, and point out that a student cannot use desk check-in.

### 4. Prediction with synthetic data
- **Admin:** **Attendance Prediction** → pick an event and predict. Show the probabilities and organiser tips, and the **What-if** planner (for example, change the time to Evening).

### Free-tier limitations to mention
- **Cold start:** the service sleeps after 15 minutes idle, and the first request takes about a minute.
- **Ephemeral files:** the server's files reset on restart (models are rebuilt on every deploy; data lives in PostgreSQL).
- **Free database:** expires after 30 days and has no backups.
- **Single instance.** All data shown is **synthetic**.

### If something goes wrong
- **"Service waking up" or the page is slow:** wait 60 seconds and reload.
- **Error or blank page:** Render dashboard → `eventpulse-ncu` → **Logs** to see what happened; then **Manual Deploy → Deploy latest commit** (about 4 minutes) or **Restart service**.
- **Admin password forgotten:** there is no web reset for the Admin, by design. Recovery means redeploying with a new database. Keep the password in your password manager.
- **Fallback:** run the local web app on your Mac (`EventPulse_Web.command`) or use the desktop app.
