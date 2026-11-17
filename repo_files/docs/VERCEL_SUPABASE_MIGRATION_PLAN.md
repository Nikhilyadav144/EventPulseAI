# EventPulse AI — Vercel + Supabase Migration Plan and Feasibility Assessment

**Date:** 16 November 2026 · **Status:** plan only. Nothing was edited, created, uploaded or deployed; no Supabase or Vercel project was touched.
**Builds on:** `docs/PRODUCTION_MIGRATION_PLAN.md`. Its sections C (PostgreSQL port), D (desktop compatibility), E (artifacts), F (security), H (backups), I (staging) and J (launch checklist) apply unchanged and are referenced here. This document replaces only its hosting layer (section G) and decides between two architectures.

**Confirmed preference:**
- Vercel Hobby for the frontend, and possibly the Python FastAPI backend.
- Supabase Free for PostgreSQL and private file storage.
- Public access from college Wi-Fi and the internet over HTTPS.
- The Mac desktop app keeps its own SQLite workflow.

**Evidence base:**
- **Code:** a read-only inspection of `webapp/server.py`, `webapp/auth.py`, `core/**`, `app.py`, `checkin_app.py` and `web/` on the Mac.
- **Measurements:** taken in the cloud workspace on its own copy of the code (Python 3.13, Linux, comparable to Vercel's runtime). No real data was used outside the Mac.
- **Platform limits:** from Vercel and Supabase documentation and third-party summaries, checked 16 November 2026. Limits change, so confirm them again before implementing.

---

## 1. Summary and recommendation

**Can the complete EventPulse AI web app run as a Vercel Python Function? Technically yes, but only after substantial changes. It is not the safest option.**

- **It fits the platform's hard limits:**
  - bundle ≈ 400 MB against a 500 MB limit;
  - memory ≤ 255 MB measured against 2 GB;
  - slowest request ≈ 1 s against a 300 s maximum duration.
- **But the current code assumes a long-running server with a local disk:**
  - in-memory rate limits;
  - SQLite;
  - files written to `logs/`, `exports/` and `database/backups/`;
  - a schema migration in the start-up hook;
  - 10 MB uploads, where Vercel caps the body at 4.5 MB.
- **And the free plans have non-technical blockers for a real deployment:**
  - Vercel Hobby is restricted to personal, non-commercial use, and the Python runtime is in Beta;
  - Supabase Free pauses after a week of inactivity and has no backups.

**Safest architecture for EventPulse AI: B — the FastAPI backend on an always-on container host, PostgreSQL on a plan with backups, and the static frontend on Vercel (optional) through a same-origin rewrite.**
- Concretely: Render Starter for FastAPI.
- For the database, Supabase Pro, or Render Postgres if you prefer one vendor.
- Supabase Free is acceptable for staging only, with synthetic data.

**Implement first:** Phase 0 (decisions in §8) and then **Phase 1, database portability** (production plan §C). Both architectures need it, and it can be built and tested entirely locally.

---

## 2. What the source code requires (inspection findings)

| # | Topic | What the code does today (file:line) | Serverless (Vercel) implication |
|---|---|---|---|
| 1 | **Entry point & pages** | One ASGI app `webapp.server:app` (FastAPI). 44 admin-router routes + 4 `/api/admin/*` + 5 `/api/student/*` + 4 `/api/auth/*` + `/api/health` + `/checkin/*` GET/POST + SPA fallback serving `web/dist` (`server.py:806-818`). Imports `app.py` and `checkin_app.py` | Vercel's FastAPI support detects an `app` object; the module path is set with `tool.vercel.entrypoint`. All routes become **one** function. The static `web/dist` should be served by the Vercel CDN, not the function |
| 2 | **Sessions & auth** | Sessions stored in the DB (`auth_sessions`, token hash) with `last_seen` updates (`auth.py:323-345`); CSRF token in the session row; HttpOnly + SameSite=Strict cookie | **Compatible** once the DB is PostgreSQL, since no in-process session state exists. Scrypt uses ≈128 MiB per hash, which fits in 2 GB |
| 3 | **Rate limiting** | In-memory `RateLimiter` objects (`auth.py:247-276`) | **Broken** across concurrent instances and after every cold start. Must move to PostgreSQL (`auth_rate_limits`, production plan F3) or Redis/Upstash |
| 4 | **Schema migration at start-up** | `lifespan` → `ensure_auth_schema_once` (`server.py` lifespan; `auth.py:96-165`) also writes a backup **file** | Must not run on cold start. Migrations become a CI/CD job; backups become PostgreSQL dumps |
| 5 | **SQLite everywhere** | `core/db/connection.py` (sqlite3, PRAGMAs, `BEGIN IMMEDIATE`, SAVEPOINT), ≈210 SQL call sites across 12 files, 4 views, 2 triggers; MLflow history read from `mlflow.db` via sqlite3 (`core/analytics.py:430-450`) | Full PostgreSQL port required (production plan §C, ≈25 code sites + DDL). `mlflow.db` can ship read-only in the bundle, or as a JSON export |
| 6 | **Per-request DB connections** | `get_db()` opens a new connection per request (`server.py` `get_db`) | Every invocation connects to Supabase. Must use Supabase's **transaction-mode pooler** and psycopg with prepared statements off |
| 7 | **Model artifacts** | 5 models (1.1 MB total), loaded from disk **per request** (`core/db/services.py:175-182`); load time 2–161 ms measured; prediction with an already-loaded model 32 ms | Fine for the bundle size. Files must ship in the bundle (from a private build source) or be fetched from Supabase Storage to `/tmp` at cold start with SHA-256 verification |
| 8 | **Reference data** | `data/processed/ml_dataset_processed.csv` (survey-derived, **private**), `data/synthetic/*`, `data/event_attendance.csv` read at runtime (Dataset Explorer, ROC, drift) | Must **not** come from a public repository. Fetch from a private Supabase Storage bucket at cold start, or bundle from a private build step |
| 9 | **Files written at runtime** | `exports/web/` reports (`server.py:771`), temp uploads (`server.py:181`), `/api/settings/backup` writes an SQLite file (`server.py:802`), prediction logs read by Monitoring (`server.py:69-70`, `core/intelligence.py:297,331`) | Only `/tmp` is writable and it is ephemeral. Reports must be streamed (PDF 478 KB, fine), logs moved to tables, the backup button replaced |
| 10 | **Uploads** | Bulk prediction file, registration import and dataset inspect read whole files up to **10 MB** (`server.py:174-182`) | Vercel caps request bodies at **4.5 MB**. Lower the app limit to 4 MB, or upload directly to Supabase Storage with signed URLs |
| 11 | **QR check-in** | Public `/checkin/{event_id}?t=token`; QR URL built as `http://<host>:<port>` with LAN-IP substitution (`server.py:566-570`) | Works over HTTPS once `EVENTPULSE_PUBLIC_BASE_URL` is used (production plan F7). Bursts of check-ins scale fine; each is one short DB transaction |
| 12 | **Cold start** | Import of `webapp.server` measured at **1.0–3.0 s**, 167 MB (numpy/pandas/scipy/sklearn at import) | First request after idle is slow (≈2–4 s including DB connect and artifact fetch); later requests are fast |
| 13 | **Bundle size** | Runtime dependencies ≈ 370 MB, or **≈ 400 MB** with matplotlib + Pillow (PDF charts) + ≈ 3 MB app/artifacts + psycopg ≈ 10 MB | Under the 500 MB Python limit, with **≈ 20 % headroom**. A future scipy/pandas upgrade could cross it |
| 14 | **Durations** | Slowest measured: PDF ≈ 1.0 s, model list + MLflow 242 ms, analytics 150 ms (SQLite, local) | Well within limits. PostgreSQL round-trips will add latency per query (≈1–5 ms same region): estimate under 1 s for most pages, ≈2 s for the PDF |

---

## 3. Platform facts that matter (checked 16 Nov 2026)

### Vercel

- **Bundle size:** the standard Python bundle limit is 500 MB uncompressed. Larger bundles are possible only through a beta feature: <https://vercel.com/docs/functions/runtimes/python>
- **Runtime status:** the Python runtime is in Beta on all plans.
- **Duration:**
  - Hobby: 300 s default and maximum (with Fluid compute): <https://vercel.com/docs/functions/limitations>
  - Without Fluid compute, Hobby defaulted to 10 s (maximum 60 s). Fluid compute must therefore be **enabled**.
- **Memory:** Hobby functions get 2 GB, fixed.
- **Request body limit:** 4.5 MB on requests and on responses; larger files must go directly to storage: <https://vercel.com/kb/guide/how-to-bypass-vercel-body-size-limit-serverless-functions>
- **Plan terms:** the Hobby plan is restricted to non-commercial, personal use; commercial use needs Pro or Enterprise: <https://vercel.com/docs/plans/hobby>, <https://vercel.com/docs/limits/fair-use-guidelines>
- **Over-limit behaviour:** if a Hobby project exceeds its usage limits, you usually wait 30 days before the feature works again (there's no option to pay for more).

### Supabase

- **Free plan limits:** 2 projects, 500 MB database, 1 GB file storage, 5 GB egress.
- **Pausing:** free projects pause after **7 days of inactivity**.
- **Backups:** Free lists **no backups**; Pro ($25/month) includes daily backups kept for 7 days and never pauses.
  - Sources: <https://supabase.com/pricing>; third-party summaries checked: automationatlas.io, jetadmin.io, selfhost.dev.
- **Over the size limit:** a free project goes **read-only** if the database exceeds 500 MB.
- **Connections:** the shared pooler in transaction mode allows many client connections but no prepared statements; session mode allows few (third-party summary, l2dy.github.io). **Confirm on the Supabase dashboard.**

### EventPulse AI's measured needs against these limits

- **Database:** ≈ 5–10 MB after the port, growing ≈ 10–20 MB/year. The 500 MB limit is fine for years.
- **Storage:** ≈ 3 MB of artifacts. The 1 GB limit is fine.
- **Egress:** small (JSON pages, 478 KB PDFs). The 5 GB limit is fine.
- **Memory and duration:** fine on Vercel.
- **The real constraints:**
  - **pausing**;
  - **no backups**;
  - **non-commercial terms**;
  - the **4.5 MB body limit**;
  - **stateless execution**.

---

## 4. Architecture A — the entire FastAPI app as a Vercel Python Function

```
Browser / phone ──HTTPS──► Vercel (custom domain, TLS)
                              ├─ CDN: web/dist (static)
                              └─ Python Function: webapp.server:app (all /api/*, /checkin/*)
                                      │ psycopg via Supabase pooler (transaction mode, TLS)
                                      ▼
                         Supabase Postgres (Free)  +  Supabase Storage (private bucket: models, data CSVs, mlflow export)
```

### Required changes, on top of production plan C–F

| Change | Files | Tables | Dependencies | Risk | Tests |
|---|---|---|---|---|---|
| A1. PostgreSQL port (plan §C) | `core/db/*`, `webapp/auth.py`, `webapp/server.py:317`, new `postgres/` migrations | all 21 + views | `psycopg[binary]` | High effort; desktop must stay SQLite | Dual-engine suite (SQLite + PG) |
| A2. Pooler-safe connections: `prepare_threshold=None`, short transactions, `sslmode=require`, connect timeout | `core/db/connection.py` | — | psycopg | Exhausting the pooler on bursts | 50 parallel requests against staging |
| A3. No start-up side effects: remove lifespan migration and backup; migrations via `scripts/migrate.py` in CI | `webapp/server.py` lifespan, `webapp/auth.py:96-165` | `schema_migrations`, `web_auth_migrations` | — | Forgetting to migrate before deploy | CI job asserts the schema version before promotion |
| A4. DB-backed rate limiting | `webapp/auth.py:247-276` | new `auth_rate_limits` | — | One write per attempt | Limits hold across 2 parallel instances |
| A5. Stateless files: stream reports; logs → tables; drop the backup button; uploads ≤ 4 MB or signed upload to Storage | `webapp/server.py:69-70,174-182,769-802`, `core/intelligence.py:281-331`, `web/src/pages/Settings.jsx`, `Reports.jsx`, `Prediction.jsx`, `Registrations.jsx`, `DatasetExplorer.jsx` | new `api_prediction_log`, `api_rejected_log` | `supabase` or `httpx` for signed URLs | Feature behaviour changes visible to admins | Upload 4.4 MB OK / 4.6 MB clear error |
| A6. Artifact fetch at cold start: download a private bundle from Supabase Storage (service key **only** server-side), verify SHA-256 against the manifest and `model_versions`, cache in `/tmp` | new `webapp/artifacts.py`; `config/settings.py`; `core/model_registry.py:40-49` | `model_versions` | `httpx` | Cold-start time +≈0.3 s; secret handling | Corrupted artifact → refuses to serve predictions |
| A7. Vercel config: `pyproject.toml` `[tool.vercel] entrypoint = "webapp.server:app"`, `vercel.json` (Fluid compute, `maxDuration` 60, `excludeFiles` for `data/**`, `database/**`, `models/**`, `logs/**`, `release/**`, `docs/**`, `tests/**`), static `web/dist` on the CDN | new `vercel.json`, `pyproject.toml`, `requirements.txt` (runtime-only lock) | — | — | Bundle near 500 MB; Python runtime Beta | Build size check in CI fails above 450 MB |
| A8. Production security F1–F15 (proxy IP from Vercel headers, secure cookies, CSP/HSTS, trusted hosts, base URL) | as production plan §F | as §F | — | Vercel-specific forwarded headers | Header tests on a preview deployment |
| A9. Supabase Free safeguards: scheduled `pg_dump` (GitHub Actions with an encrypted secret → private storage), keep-alive query, size alert at 400 MB | `.github/workflows/backup.yml` | — | `postgresql-client` in CI | A keep-alive works around the pause but **does not create backups**; secret in CI | Restore drill monthly |

### Assessment of A

**Advantages:**
- $0 hosting.
- Global CDN.
- Managed TLS and custom domain.
- Scales to zero.

**Disadvantages:**
- **Largest change set:** A2–A7 on top of the PostgreSQL port.
- **Beta Python runtime.**
- **Cold starts of 2–4 s.**
- **Bundle at ≈ 80 % of the limit.**
- **4.5 MB body cap.**
- **Plan terms:** Hobby is personal and non-commercial only. A college-wide service run for an institution may not qualify (decision V1).
- **Supabase Free:** pausing and no backups make it unsuitable for real student records.

**Verdict: feasible, but highest complexity and lowest reliability. Not recommended for production.** Acceptable as a **free demo** with synthetic data.

---

## 5. Architecture B — Vercel frontend + FastAPI on an always-on host

```
Browser / phone ──HTTPS──► Vercel (custom domain events.<domain>)
                              ├─ CDN: web/dist (static, 336 KB)
                              └─ rewrites: /api/*, /checkin/*  ──►  https://eventpulse-web.onrender.com (same origin for the browser)
                                                                        │ FastAPI container (Render Starter, always-on, 1 instance)
                                                                        │ psycopg (direct or session pooler, TLS)
                                                                        ▼
                                              PostgreSQL: Supabase **Pro** (or Render Postgres Basic) + private Storage bucket
```

**Why the rewrite matters:**
- Proxying `/api` and `/checkin` through the frontend's domain keeps the browser on **one origin**. The current security model stays unchanged: HttpOnly + **SameSite=Strict** cookie, header CSRF token, no CORS with credentials.
- The alternative is serving the API from `api.<same domain>`. That also keeps cookies same-site, but needs CORS with credentials.

**Verify on staging (MANUAL):**
- that Vercel external rewrites forward `Set-Cookie`, `X-Forwarded-For` and request bodies up to the size you need;
- that the proxy adds acceptable latency.

If either fails, use the `api.<domain>` option, or simply serve the frontend from the backend. FastAPI already serves `web/dist`.

### Required changes

**The same as A1 + A3/A4 + A6 + A8**, except:
- **No 4.5 MB upload limit** on direct API calls (keep 10 MB, enforced by the app). The cap still applies if uploads pass through the Vercel rewrite; if they do, route them to the API host directly or cap them at 4 MB.
- **Startup work is allowed:** the model cache and artifact download happen once per container start. Migrations still run as a deploy step, not inside the web start-up hook.
- **Files:** `/tmp` still serves temporary uploads. Persistent files still move to PostgreSQL or Storage, since containers are ephemeral too.
- **New files:**
  - `Dockerfile.web` + `requirements-web.lock` (production plan §G);
  - `render.yaml` service `eventpulse-web`;
  - `vercel.json` with `rewrites` only (no Python function);
  - frontend unchanged (relative `/api` paths already, `web/src/api.js`).

### Assessment of B

**Advantages:**
- Smallest change set beyond the PostgreSQL port.
- Server model matches the current code: long-running, warm model cache, predictable latency, no cold starts on the paid tier.
- No bundle-size pressure.
- Uploads unchanged.
- Rate limits could even stay in-memory for one instance, though DB-backed is recommended.
- Vercel gives CDN + TLS + domain for the static site.

**Disadvantages:**
- Two vendors to operate (three with Supabase).
- ≈ $7/month for the backend.
- Rewrite behaviour to verify.

**Verdict: recommended.**

---

## 6. Comparison

| Criterion | A: all on Vercel Function | B: Vercel frontend + container backend |
|---|---|---|
| Code changes beyond the PG port | Large (stateless rewrite, uploads, artifacts at cold start, migrations out-of-band, pooler tuning) | Moderate (artifacts at start-up, migrations as a deploy step, production hardening) |
| Compatibility with current code | Low: in-memory limits, disk writes, lifespan, 10 MB uploads all break | High: same uvicorn server model as today |
| Cold start / latency | 2–4 s after idle; then fast | None on an always-on instance; fast |
| Limits headroom | Bundle ≈ 400/500 MB; body 4.5 MB | No bundle limit; body limit set by the app |
| Reliability | Python runtime Beta; Supabase Free pausing; no backups | Mature container runtime; backups with a paid database |
| Security model | Unchanged in principle; more secrets at cold start | Unchanged; same-origin via rewrite |
| Plan terms | Vercel Hobby: personal non-commercial only | Same for the static frontend on Hobby (lower exposure); backend unaffected |
| Monthly cost | $0 (free tiers) | Render Starter $7 + Supabase Pro $25 = **$32**, or Render Postgres Basic $6 instead = **$13** |
| Maintenance | High (two runtimes' quirks, keep-alive, DIY backups) | Medium (one container, managed DB backups) |
| Suitable for real student data | **No** with free tiers (pausing, no backups) | **Yes** with a paid database |

---

## 7. Supabase-specific plan (applies to both)

1. **Projects:** two, staging and production. Choose the region nearest the campus, e.g. Mumbai (`ap-south-1`); confirm.
2. **Database role:** a dedicated database role for the app with least privilege. Row Level Security isn't needed, because the browser never talks to Supabase directly. **Never expose the anon or service keys to the frontend.**
3. **Connections:**
   - A: transaction pooler.
   - B: a direct or session connection from one container (few connections).
   - TLS required in both.
4. **Storage:**
   - A **private** bucket `eventpulse-artifacts`.
   - It holds the release bundle + `MANIFEST.json` (SHA-256 per file): models, `model/`, `mlflow_runs.json`, processed/synthetic/end-term CSVs, logo.
   - **Never** `data/original/`, database dumps in the clear, or backups without encryption.
5. **Backups:**
   - Free: none, so a DIY nightly `pg_dump` is mandatory (encrypted, to a separate private bucket or off-platform).
   - Pro: daily backups plus the same encrypted dump for longer retention.
6. **Pausing:** on Free, a paused project takes the app offline until someone resumes it. **Use Free only for staging/demo.**

---

## 8. Decisions requiring confirmation

| # | Decision | Notes |
|---|---|---|
| V1 | **Is EventPulse AI personal and non-commercial?** | Vercel Hobby forbids commercial use. A college-run service operated for the institution may need Vercel Pro ($20/user/month) or another static host; ask Vercel or the college if unsure |
| V2 | **Supabase plan for production data** | Free = no backups + pausing: not acceptable for the 279 student records and accounts. Pro is $25/month, or use Render Postgres Basic ($6/month) and keep Supabase only for Storage |
| V3 | **Data-hosting approval** | Hosting survey-derived records and account e-mails in the cloud needs mentor/institution approval (same as production plan K2) |
| V4 | **Domain & DNS owner** | Needed for Vercel + TLS and for QR codes |
| V5 | **Region** | Supabase Mumbai vs Render Singapore: about 60 ms between them; choose both in the same region if possible (e.g. Render Singapore + Supabase Singapore) |
| V6 | **Upload size** | Keep 10 MB (B) or reduce to 4 MB (A) |
| V7 | **Backend host for B** | Render Starter (recommended, already used for CSL236), or an equivalent always-on container host |
| V8 | Items K5–K10 of the production plan | System of record, registration policy, MFA, CSL236 API, retention, concurrency |

**Storage estimate:**
- Database ≈ 5–10 MB now and ≈ 10–20 MB/year growth (500 MB free limit not a concern).
- Storage ≈ 3 MB per artifact release + ≈ 1–3 MB per compressed dump.

**Compute estimate:**
- B: one 512 MB / 0.5 CPU container (measured working set 167–255 MB; bound password hashing to 2 at a time).
- A: 2 GB fixed per instance; the cost is cold starts.

---

## 9. Phased implementation sequence (Vercel + Supabase, architecture B)

| Phase | Work | Exit criteria |
|---|---|---|
| **0 — Decisions** | V1–V8 confirmed; private git repository; secrets policy | Written approvals |
| **1 — Database portability (implement FIRST)** | Production plan §C: dialect layer, PostgreSQL schema, parity tests, CI with a Postgres service container; desktop SQLite behaviour unchanged | Existing 223 tests green on SQLite; core/auth/web suites green on PostgreSQL |
| 2 — Production hardening | Production plan §F (F1–F15), DB rate limits, logs to tables, model SHA-256 check + cache, streamed reports | Audit findings closed with tests |
| 3 — Artifacts | Bundle builder + manifest + verifier; Supabase Storage download (server-side key) | Corrupted bundle refused in tests |
| 4 — Packaging | `Dockerfile.web`, lock file, `render.yaml` backend, `vercel.json` rewrites, CI smoke + `pip-audit` | Preview deployment passes the smoke test |
| 5 — Staging | Supabase **staging** project (Free is fine), synthetic data, acceptance + phone tests on HTTPS via the Vercel domain; verify rewrite cookie/forwarding behaviour | Production plan §I passed |
| 6 — Data rehearsal | Migrate a **copy** of the SQLite database into a throwaway staging DB; verify counts, hashes and KPIs; drop it | Exact match |
| 7 — Production cut-over | Supabase Pro (or Render Postgres) production database, launch checklist (production plan §J) | Sign-off |

**First action:** confirm the Phase 0 decisions (especially V1 non-commercial terms, V2 paid database and V3 data approval). **Then start Phase 1**, the SQLite → PostgreSQL portability layer with the desktop kept on SQLite. Every option needs it, and it touches no live system.
