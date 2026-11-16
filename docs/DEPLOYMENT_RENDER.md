# EventPulse AI web app on Render: deployment guide (prepared in Phase 3, NOT yet deployed)

This guide describes the planned architecture and the exact settings. **Nothing here has been created yet.** Creating the Render services, pushing code and publishing the URL need your approval (Phase 4).

## Architecture

```
 browser ──HTTPS──> Render proxy ──> ONE web service (Python, 1 Uvicorn worker)
                                        FastAPI  /api/*  +  built React app  /  +  /checkin/*
                                        │  internal URL, sslmode=require
                                        ▼
                                     Render PostgreSQL (free; fresh database; schema "public")

 Desktop app (your Mac) ──> database/eventpulse.db (SQLite)  ← completely separate; never uploaded
```

- **Same origin:** the frontend and the API come from one URL (`https://<name>.onrender.com`), so no CORS is needed.
- **Fresh start:** no local records are migrated. Optionally, the labelled **synthetic** demonstration dataset is loaded into the empty database (`EVENTPULSE_SEED_SYNTHETIC_DEMO=1`).
- **Single worker:** sign-in limits, delays and the password-hashing cap live in one process. Do not scale to more instances or workers.

## Render free-tier facts (checked 17 Nov 2026 at render.com/docs/free)

**Free web service**
- Sleeps after 15 minutes without traffic.
- Takes about 1 minute to wake.
- Has an ephemeral filesystem: anything written to local disk is lost on redeploy, restart or sleep.
- Has no shell or SSH (those are paid-only).

**Free PostgreSQL**
- 1 GB of storage.
- Only one free database per workspace.
- **Expires 30 days after creation.** After a 14-day grace period it is deleted with all its data.
- **No backups of any kind.** Nothing is recoverable unless *you* take a backup (see "Backups").

**Plan for the demo.** Open the URL a few minutes before presenting so the service is awake. After the demo, back up the database if its contents matter.

## 1. Create the database (Render dashboard → New → PostgreSQL)

- **Region:** the same region as the web service.
- **PostgreSQL version: 16** (the version all tests used; Render's default is 18).
- **Plan:** Free.
- **Connection URL:** use the **Internal Database URL** for `EVENTPULSE_DB_URL`. The app adds `sslmode=require`, connect timeout and application name automatically; Render supports TLS on internal connections.
- **Schema:** Render gives one owner account and the `public` schema. Render has no public data API, so the app is allowed to use `public` with `EVENTPULSE_DB_ALLOW_PUBLIC_SCHEMA=1`. Without that setting the app refuses `public` on a remote host. This exact setup (non-superuser owner, `public`, SSL, SCRAM) is covered by the test `test_render_like_owner_role_on_public_schema`.

## 2. Create the web service (Render dashboard → New → Web Service)

The existing `render.yaml` and `Dockerfile` belong to the CSL236 prediction API and are **not** used or changed. Create the web service manually with the **Python** runtime:

| Setting | Value |
|---|---|
| Build command | `pip install -r requirements-web.txt && python -m webapp.hosted_setup build-models --out models_hosted` |
| Start command | `uvicorn webapp.server:app --host 0.0.0.0 --port $PORT --workers 1 --no-proxy-headers` |
| Health check path | `/api/health` (returns only `{"status":"ok"}`) |
| Instance type | Free |
| Auto-deploy | Off for the demo (deploy manually) |

**Frontend build.** The React build (`web/dist`) is excluded by `.gitignore` (`dist/`). Add the verified build to the deployment repository with `git add -f web/dist`; it is byte-for-byte reproducible from `web/build_esbuild.mjs`. Otherwise `npm install && npm run build` must be added to the build command; that path was not tested here.

**Model build.** `build-models` trains the five models on the synthetic dataset only (about 40 s) into `models_hosted/`. It refuses the desktop's `models/` and `model/` folders.

**Environment variables:** see `.env.example`. Mark `EVENTPULSE_DB_URL` and `EVENTPULSE_ADMIN_PASSWORD` as secret. Set `PYTHON_VERSION=3.12.8`.

## 3. First start

On startup the server does the following, in order:
1. Refuses to start if the production configuration is unsafe (`python -m webapp.hosted_setup check-config` shows the same checks).
2. Switches the process to Asia/Kolkata.
3. Applies the core migrations (schema 3) and the web-auth migration (1). Every step is idempotent, so a restart changes nothing.
4. If no Admin exists, creates the Admin from `EVENTPULSE_ADMIN_*`. It never resets an existing Admin.
5. With `EVENTPULSE_SEED_SYNTHETIC_DEMO=1`, seeds the synthetic data. This happens only if the database has no student records.
6. Registers the models from `EVENTPULSE_HOSTED_MODELS_DIR`.

**Then delete `EVENTPULSE_ADMIN_PASSWORD` from Render's environment.** The server keeps working; if the variable is left in place, the log carries a warning.

## 4. Backups (your responsibility on the free tier)

From your Mac, with PostgreSQL client tools 16 or newer installed (`brew install libpq`, then add its `bin` folder to `PATH`):

```
export EVENTPULSE_BACKUP_DB_URL='<External Database URL>'   # keep it out of shell history if possible
python3 scripts/postgres/pg_backup.py backup --out-dir ~/EventPulseBackups
python3 scripts/postgres/pg_backup.py verify ~/EventPulseBackups/eventpulse_<stamp>.dump
```

- **Restore** only into a **new, empty** database: `EVENTPULSE_RESTORE_DB_URL=<new db URL> python3 scripts/postgres/pg_backup.py restore <file>`. The tool compares every table's row count and content checksum with the backup.
- **Backup files** contain account e-mails and password hashes. They are written with mode 600 (folder 700) and refused inside the project folder. Never upload them.
- **Two separate backup systems:** the desktop's SQLite backups (`database/backups/`, Settings → Backup) are unrelated and stay on your Mac.

## 5. Things that need your approval (not done)

1. A GitHub repository (or branch) to deploy from, containing the project without `data/original/`, `database/`, `models/`, `.venv`, logs or any secret. `.gitignore` already excludes these; `web/dist` must be added with `-f`.
2. Creating the Render PostgreSQL database and the web service.
3. Entering the secret environment variables in Render.
4. The first deploy and publishing the `*.onrender.com` URL.
