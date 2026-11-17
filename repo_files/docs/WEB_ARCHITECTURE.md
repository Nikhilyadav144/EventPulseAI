# Web Architecture Baseline

## Current verified backend boundary

The verified FastAPI component is a model-serving API only:

```text
Browser prediction form
      -> FastAPI app.py
      -> Predictor loaded once at startup
      -> model/model.joblib + metadata.json
      -> prediction response and local JSONL monitoring logs
```

Its serving artifact is trained by `train.py` on the isolated 1,323-row synthetic end-term dataset and is tracked in `mlflow.db`. The desktop product continues to use its own PySide6 interface, SQLite repositories and `models/` registry; it has not been altered in this phase.

## Intended migration architecture

The web migration should use a React/Vite client and an expanded FastAPI REST backend. The backend should expose Pydantic-validated endpoints around the existing, tested domain services rather than duplicate their business rules in the frontend.

```text
React/Vite web client
        |
        | HTTPS JSON API
        v
FastAPI REST application
  |- model-serving module (current verified boundary)
  |- event / registration / attendance services
  |- analytics, insights, reporting and notification services
  |- authentication and authorisation
        |
        +--> PostgreSQL in production
        +--> SQLite retained for local desktop development and rollback
        +--> managed object/log storage for reports and monitoring
```

## Constraints discovered in Phase B

- The current static HTML form is not React/Vite and covers only individual prediction.
- The operational desktop database layer uses SQLite SQL and must not be placed on a Vercel ephemeral filesystem.
- Local prediction JSONL logs and generated reports also require durable hosted storage before production deployment.
- The QR check-in server is intentionally local-network-only and must not become a public unauthenticated endpoint.
- The source tree has no authentication or role system; that must precede operational write endpoints.
- Frontend deployment and backend hosting selection are not implemented or verified in this phase.

## Phase B outcome

The model artifact contract is now restored: the FastAPI service has a genuine serialized pipeline, generated metadata and nine local MLflow runs. This removes the backend startup blocker, but it does not constitute a complete web application or a deployment.

## Next architecture work

Before frontend migration, design versioned request/response schemas for each existing domain service, introduce a PostgreSQL-compatible persistence layer with tested migrations/backups, and add authentication/CORS policy. Hosting and Vercel configuration must be assessed against current official provider documentation immediately before deployment work.


## Backend repair status (16 November 2026)

Verified on the Mac: the serving artifact is genuine and reproducible, the API starts and every
endpoint behaves as documented (see `API_DOCUMENTATION.md`), and the full test suite passes
(196 passed, 0 failed, 0 skipped, 1 third-party deprecation warning).

Remaining blockers before any hosted deployment:

1. No Git repository / remote yet, so CI (`.github/workflows/ci.yml`) has never run on GitHub.
2. The Docker image has not been built anywhere (Docker is not installed on this Mac; the CI job builds it).
3. Hosted logs need durable storage (Render/Vercel filesystems are temporary).
4. The operational features (events, registrations, attendance, reports) exist only in the desktop app
   and the SQLite layer; they have no HTTP API, authentication or PostgreSQL support yet.
5. The packaged `EventPulse AI.app` predates the date fix below; it is fixed in source and will be
   included the next time the app is rebuilt (not rebuilt in this phase, as instructed).
6. The `fastapi.testclient` deprecation warning (httpx → httpx2) should be handled when dependencies are pinned.

Fixed in this phase: attendance recording compared the event's local date with the UTC date, so between
00:00 and 05:30 IST an event dated "today" was refused as "not taken place yet" (found because the test
run happened at 03:05 IST). It now uses the local date, consistently with check-in.
