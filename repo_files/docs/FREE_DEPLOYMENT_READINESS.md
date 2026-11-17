# EventPulse AI — Free Deployment Readiness (after Phase 1)

*Target (future, not done): Vercel Hobby (frontend) + FastAPI on Render Free + Supabase
PostgreSQL Free, ₹0 budget, as a personal academic/portfolio demonstration — **not** an
official live college system. Nothing has been deployed or uploaded.*

## Verdict

**Not deployment-ready yet.** Database portability is done and tested against real
PostgreSQL 16. The blockers below remain, and some can only be cleared with your approval
(installing a package) or by using external services (Phase 2 and later).

| Area | State |
|---|---|
| Code runs on PostgreSQL | ✅ Tested on PG 16.15 (whole suite in PG mode; parity suite) |
| Desktop app unaffected | ✅ SQLite path unchanged; tested in the container; Mac run listed in the final report |
| Production PG driver (psycopg) | ❌ Not installed and not tested (needs approval) |
| Managed Postgres (Supabase) specifics | ❌ Unverified: SSL, pooler, collation, permissions |
| Demo data | ✅ `scripts/seed_demo_database.py` (synthetic only, tested on both engines) |
| Rate limiting across instances/restarts | ⚠️ In-memory per process (OK for one Render instance) |
| Client IP behind proxy | ⚠️ Needs `--proxy-headers` (all users would share one IP for limits) |
| Cross-site cookies (Vercel ↔ Render) | ⚠️ SameSite=Strict requires same origin. Use a Vercel rewrite to proxy `/api/*` |
| CORS origins | ⚠️ Hard-coded to localhost. Make them configurable if not proxied |
| Model artifacts on Render | ⚠️ `artifact_path` is stored per model version; demo models must ship with the service |
| File outputs (exports, logs) | ⚠️ Render Free disk is ephemeral. Exports are fine as downloads; logs vanish on restart |
| Backups | ⚠️ Supabase Free has no automatic backups. Use a manual `pg_dump` when the demo data matters |

## Recommended architecture (B from the earlier plan)

```
Browser ──HTTPS──► Vercel (static React build in web/dist)
                     └─ rewrite /api/*, /checkin/* ──► Render Free (uvicorn webapp.server:app)
                                                         └─ EVENTPULSE_DB_URL ──► Supabase Postgres (SYNTHETIC demo data)
```

Proxying through Vercel keeps the API on the same origin. That makes the existing
SameSite=Strict session cookie and CSRF design work without loosening anything.

## Free-tier limits that affect this project

These are from the earlier research in this project; re-check them before deploying, because
providers change their terms.

* **Supabase Free:** 500 MB database; the project **pauses after about 7 days of inactivity**;
  no automatic backups. The demo database is a few MB.
* **Render Free web service:** spins down after about 15 minutes idle, so the first request
  takes a cold start of tens of seconds. The filesystem is ephemeral. Render's own free
  Postgres expires after 30 days, which is why Supabase is preferred.
* **Vercel Hobby:** non-commercial use only; the static frontend is fine. The Python runtime is
  not used in architecture B (bundle limit 500 MB; our dependencies are about 370–400 MB).
* **Measured locally:** API import 1–3 s; RSS about 167 MB at rest, peak about 255 MB; PDF
  report about 1 s. This fits Render Free's 512 MB.

## Environment variables for the cloud API (names only — never commit values)

| Variable | Value |
|---|---|
| `EVENTPULSE_DB_URL` | Supabase connection string (with `sslmode=require`), set as a Render secret |
| `EVENTPULSE_SECURE_COOKIES` | `1` |
| `EVENTPULSE_ADMIN_EMAIL`, `EVENTPULSE_ADMIN_NAME`, `EVENTPULSE_ADMIN_PASSWORD` | one-time admin provisioning via `python -m webapp.provision_admin --from-env`; remove them afterwards |
| `EVENTPULSE_PG_BACKUP_CONFIRMED` | unset normally; `1` only for a schema upgrade after a `pg_dump` |

## Exact next steps

### Phase 2 (local; each step needs your go-ahead)

1. Approve installing `psycopg[binary]`. Do it in a **separate** virtual environment so the
   desktop `.venv` is not changed. Then run:

   ```
   EVENTPULSE_TEST_ENGINE=postgresql EVENTPULSE_TEST_PG_URL=<local disposable server> pytest
   ```

   This verifies the real driver.
2. Add `psycopg[binary]` to `requirements-serve.txt` only (not the desktop requirements).
3. Fix the per-IP limits behind proxies: use `uvicorn --proxy-headers --forwarded-allow-ips`
   for Render's proxy.
4. Optional: move the rate limiter into the database (a table plus pruning), with tests, so
   limits survive restarts.
5. Make CORS origins configurable (`EVENTPULSE_CORS_ORIGINS`), or rely on the Vercel rewrite.
6. Store model artifact paths relative to the models folder, or ship the demo models in the
   image, and test with the seeder's output.
7. Build the demo database with `scripts/seed_demo_database.py --sqlite /tmp/demo.db` and walk
   through every page locally against it.

### Phase 3 (external services; you create the accounts and enter the secrets)

1. Create the Supabase project; copy the connection string straight into Render's secret
   settings.
2. Seed it: `scripts/seed_demo_database.py --postgres` (synthetic only), then provision the
   admin from the environment.
3. Deploy the API on Render Free; set `healthCheckPath` to `/api/health`.
4. Deploy `web/dist` on Vercel with rewrites to the Render URL; verify login, CSRF, check-in
   and reports over HTTPS.
5. Label the site as a portfolio demo with synthetic data. Never load the real database or
   `data/original/` into the cloud.
