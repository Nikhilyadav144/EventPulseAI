# EventPulse AI Project Audit

Audit date: 16 November 2026  
Audit scope: source tree, local datasets, SQLite databases, model artifacts, tests, existing documentation, the supplied project brief, the Word project description, notebook and presentation files. The audit is read-only except for this report. No original or reference file was changed.

## Executive conclusion

EventPulse AI is a substantial **PySide6 desktop application**, with fourteen functional desktop pages, a SQLite operational database, trained scikit-learn models and a local QR attendance workflow. It also contains a separate, much narrower FastAPI application intended for the CSL236 serving requirement. That FastAPI app is not the desktop application exposed through a browser: it only offers a single-registration prediction form and five API routes.

The proposed web migration is therefore a migration of an established desktop system, not a conversion of an existing React application. There is no React, Vite, TypeScript/JavaScript project, `package.json`, `vercel.json`, PostgreSQL configuration, authentication implementation, or public deployment URL in the audited source tree.

The desktop application remains the best-supported working interface. The current FastAPI app is **not runnable from this checkout** because it requires `model/model.joblib` and `model/metadata.json`, while the checked-in artifacts live under `models/`. Its own API test suite consequently fails during lifespan startup. This must be resolved before any backend can be considered deployable.

The data sources are correctly separated in the present files. All existing attendance labels are simulated; no validation in this audit supports a claim that the model predicts real attendance.

## Evidence gathered

| Item | Observed result |
|---|---|
| Repository metadata | No `.git` directory was present in the supplied workspace, so branch/commit history and remote CI status could not be verified. |
| Original workbook | `College_Event_Attendance_ML_Final.xlsx`, six sheets; `ML_Dataset` is 383 x 24 and `Raw_Merged_Data` is 383 x 20. |
| Protected workbook copy | `data/original/College_Event_Attendance_ML_Final.xlsx` exists with the same sheet dimensions. |
| Processed original data | 383 registrations, 279 students, 36 events; all rows labelled `ORIGINAL_HYBRID`; labels are simulated. |
| Demo synthetic data | 783 registrations, 600 students, 48 events; all rows labelled `SYNTHETIC`. |
| End-term ML data | 1,323 registrations, 1,000 students, 60 events; all rows labelled `SYNTHETIC`; 912 attend / 411 do not attend. |
| Main database | `database/eventpulse.db`, schema version 3: 279 students, 36 events, 383 registrations, 57 predictions, 5 model versions, no check-ins. |
| Demo database | `database/eventpulse_demo.db`, schema version 2: 600 students, 48 events, 783 registrations, no predictions. It does not have migration 0003 tables. |
| Saved desktop models | Five `models/*/model.joblib` artifacts: original logistic baseline, logistic regression, decision tree, random forest and gradient boosting. |
| Expected API model artifact | `model/model.joblib` and `model/metadata.json` are absent. |
| Historical verification | `logs/verify_status.txt` records `152 passed, 1 warning` on 12 November 2026. This is historical evidence, not a new passing run. |
| Current API test run | `pytest -q tests/test_app.py`: 1 passed and 27 errors. Every API-client test fails before execution because `app.py` cannot read the missing `model/metadata.json`. |

## Current architecture and data flow

```text
Workbook / CSV sources
        |
        +--> core.data_loader + core.data_validation + core.preprocessing
        |         |
        |         +--> models/<model_id>/model.joblib + metadata.json
        |         +--> SQLite operational database
        |                    |
        |                    +--> PySide6 desktop pages, reports, QR check-in
        |
        +--> train.py + MLflow SQLite store (configured, but no local mlflow.db found)
                  |
                  +--> intended model/model.joblib for FastAPI
                               |
                               +--> FastAPI /predict and static single-page prediction form
```

The desktop application uses the database repositories and model registry directly. The FastAPI application uses `core.prediction.Predictor` directly and appends prediction input to JSONL log files; it does not use the operational SQLite database for events, registrations, reports or attendance.

## Source inventory

| Location | Purpose | Audit finding |
|---|---|---|
| `main.py`, `gui/` | PySide6 desktop launcher, branding, theme, dialogs, widgets and fourteen pages. | Primary interface; complete desktop navigation is defined in `gui/main_window.py`. |
| `core/schema.py`, `data_validation.py`, `data_loader.py`, `dataset_pipeline.py`, `preprocessing.py` | Data contract, PII/sheet protection, validation, preparation and sklearn transforms. | Strong separation of pre-event inputs and explicit rejection of PII-bearing raw data. |
| `core/model_training.py`, `evaluation.py`, `model_registry.py`, `prediction.py` | Training, grouped validation, metrics, artifact persistence, model compatibility and inference. | Genuine sklearn pipeline; desktop artifacts are present and versioned. |
| `core/db/` | SQLite connection, non-destructive migrations, backup/restore, repositories and services. | Mature SQLite implementation with transactions, foreign keys, checksums and pre-migration backup. SQLite-specific code must be abstracted/reworked for PostgreSQL. |
| `core/workflow.py`, `checkin.py`, `analytics.py`, `intelligence.py`, `reports.py`, `charts.py` | Operational logic for events, registrations, attendance, analytics, monitoring, insights, exports and charts. | Valuable domain logic to preserve during API extraction. |
| `gui/pages/` | Overview, Dataset Explorer, Event Management, Event Calendar, Registration Management, Attendance Prediction, Attendance Records, Model Laboratory, Analytics, Insights, Monitoring, Reports, Notifications and Settings. | All requested concepts exist in desktop form. What-if planning is a panel inside Attendance Prediction, not a fifteenth top-level page. |
| `app.py`, `static/index.html` | Separate FastAPI prediction API and static prediction form. | Functional design on paper but broken in this checkout because the required API model directory is absent. Not React/Vite and not a full operational web UI. |
| `checkin_app.py` | Local venue-network QR check-in FastAPI app on port 8010. | Explicitly intended not to be deployed. It uses tokenised links, but has no user authentication. |
| `train.py`, `drift_check.py`, `scripts/` | End-term training, drift reporting, generation, imports, database operations, screenshots and model retraining. | `train.py` is a separate modern MLflow path; it is not reconciled with the checked-in desktop `models/` structure. |
| `data/` | Original protected workbook, processed original data, synthetic demo data and end-term synthetic training CSV. | Sources are labelled and separated. |
| `database/` | Main and demo SQLite files plus backups. | Main is at v3; demo is behind source migrations at v2. |
| `models/` | Desktop persisted models and comparison metadata. | Exists and loads under the local scikit-learn 1.9.1 environment. |
| `tests/` | API, DB, data, model, report, check-in and GUI tests. | Broad desktop test coverage exists; web frontend and API-to-operational-database coverage do not. |
| `.github/workflows/ci.yml`, `Dockerfile`, `render.yaml` | Intended GitHub Actions, Docker/Render serving configuration. | CI/Docker are aimed at the narrow FastAPI service and currently assume the absent `model/` artifacts. |
| `docs/` | Prior audits, data card, database and desktop guides. | Useful historical reference, but some claims are stale or conflict with the filesystem; this audit takes observed state as authoritative. |
| `EventPulse AI.app/`, `release/`, `packaging/` | macOS packaged application and packaging artifacts. | Preserve as rollback path; not suitable as the primary browser deployment artifact. |

Large bundled directories such as `.venv/`, packaged application resources, caches, historical screenshots and release copies were inventoried as generated/distribution material rather than treated as authored source.

## Functional assessment

### Desktop features

The following exist as implemented PySide6 pages backed by the core/database layer: overview KPIs; dataset preview, profiling and validation; event creation/editing/statuses; month calendar; registration lists/import and duplicate rejection; individual, event and bulk prediction; scenario/what-if planning; QR/desk attendance, finalisation and corrections; model comparison/activation checks; analytics; distinct evidence-qualified insights; input monitoring and drift checks; PDF/Excel/CSV reporting; in-app notifications; and settings/backup/demo-mode controls.

Important limitations observed in the shipped local state:

- The main database contains only legacy dataset events and simulated attendance. It has no recorded QR check-ins.
- The QR service is deliberately local-only and is not safe to expose as a public unrestricted endpoint.
- Demo data uses a separate database, which is good; its schema must be migrated to v3 before it can support the newest workflow unchanged.
- The desktop tests and screenshot logs are evidence of prior verification, but the current full suite was not re-certified by this audit after the API artifact discrepancy was found.

### Current web/API features

| Feature | Status |
|---|---|
| `GET /health` | Implemented; blocked at application startup by missing API model files. |
| `POST /predict` | Implemented with Pydantic model validation and probability/tips response; blocked by the same startup failure. |
| Invalid prediction request handling | Implemented through FastAPI/Pydantic and custom rejected-request logging; tests cannot currently reach it. |
| `/docs` | Automatically exposed by FastAPI; blocked by startup failure in normal TestClient use. |
| Prediction input logging | Implemented to JSONL; local filesystem storage is unsuitable for serverless/ephemeral deployment. |
| Browser UI | One static HTML prediction form only. It has no navigation, CRUD, calendar, attendance, reporting, authentication or connection to SQLite operations. |
| React/Vite frontend | Missing. |
| CORS policy | Missing. No `CORSMiddleware` configuration was found. |
| Authentication / RBAC | Missing. |
| PostgreSQL support | Missing. |

## ML pipeline verification

The project is not using a hard-coded attendance formula for serving. It uses trained scikit-learn pipelines and stores models with metadata. The end-term `train.py` supports Logistic Regression, Decision Tree, Random Forest and Gradient Boosting. Desktop training supports the same family plus an original-notebook logistic-regression baseline.

The documented training design is sound for this simulated repeated-student dataset: validation groups by `Student_ID`, uses a student-grouped holdout and five-fold `StratifiedGroupKFold` cross-validation, chooses thresholds from out-of-fold training predictions, and uses mean CV ROC-AUC for selection. Identifiers and `Data_Source` are excluded from model inputs; the twenty model inputs are pre-event registration, student-profile and event-context fields. Missing data is imputed inside the pipeline after controlled same-student/event repair in the processed original dataset.

Observed desktop model metadata identifies Logistic Regression as the active/recommended desktop model, with mean grouped-CV ROC-AUC 0.786 and standard deviation 0.061 on the 383-row processed original dataset. These numbers must remain labelled as performance against a **simulated target**, not empirical attendance performance. Random Forest (0.775 ± 0.071) and Gradient Boosting (0.749 ± 0.074) are also stored. The overlapping uncertainty means no strong superiority claim is warranted.

There are two unresolved ML serving paths:

1. The desktop registry reads `models/<model_id>/...`, while `app.py`/Dockerfile/CI expect a different `model/...` output created by `train.py`.
2. `train.py` claims SQLite-backed MLflow tracking (`mlflow.db`), but no `mlflow.db` or `mlruns/` directory was found in the supplied project tree. The audit therefore verifies the code path, not three completed local MLflow runs.

## Database and data-integrity assessment

The SQLite schema uses foreign keys, checks, transactions, indexed views and checksum-protected additive migrations. Migration code takes a verified backup before modifying a populated database. Repository-level PII checks, source labels and separate demo database handling are positive controls.

For a deployed application, this cannot simply be mounted on Vercel local storage: both SQLite WAL files and prediction JSONL logs are process-local and ephemeral. PostgreSQL migration work will require a database adapter/repository refactor, a tested import/migration command, transaction/concurrency verification, environment-driven connections and managed-provider backups. The existing SQLite implementation should remain as the local-development and rollback mode.

No silent merge among the three known datasets was observed. The raw `Raw_Merged_Data` sheet contains personal information and is expressly forbidden by the loader; it must never be rendered or sent to a browser. The source tree does contain local artifacts/reports and databases, so the migration must use a deliberate exclusion policy for PII-bearing files and generated data rather than publishing the complete directory.

## Security assessment

- No authentication, passwords, API keys or RBAC implementation was found.
- The public API has no CORS configuration and is not ready for an independently hosted frontend.
- QR check-in tokens are generated with `secrets.token_urlsafe`, checked with `compare_digest`, and duplicate check-ins are prevented in both logic and schema. However, bearer links plus a student ID are not sufficient authentication for public internet use.
- Current correction accountability is an entered name/reason plus audit log, not identity-backed authorisation.
- Environment variables are used for selected file paths (`MODEL_DIR`, logs, QR database path), but no general secret/configuration layer exists for hosted database credentials or permitted frontend origins.

## CSL236 compliance checklist

The supplied files include a team project description, notebook and presentation, but no separate official CSL236 guideline/rubric was identifiable in the workspace. The checklist below therefore uses the requirements in the supplied brief and marks any item that needs that missing official document as unverified.

| Requirement | Status | Evidence / gap |
|---|---|---|
| At least 1,000 valid rows | Verified | `data/event_attendance.csv` has 1,323 labelled synthetic rows. |
| At least five input features | Verified | Twenty declared model inputs. |
| Reproducible/source documentation | Verified | `core/synthetic.py`, generator script, metadata and data-card documentation. |
| Data contract in README | Implemented | README documents fields/ranges; needs correction of broken artifact/run claims. |
| Pydantic validation | Implemented | `Registration` model in `app.py`. |
| Invalid requests return 422 | Implemented but currently unverified end-to-end | API tests are blocked by the missing model artifact. |
| At least three MLflow runs | Unverified | Training code logs runs, but no local MLflow store/run evidence was found. |
| At least two algorithms | Verified | Four algorithms implemented and five desktop artifacts stored. |
| CLI hyperparameters | Verified | `train.py` argparse supports algorithms and algorithm-specific hyperparameters/sweep. |
| SQLite MLflow tracking | Implemented in code; run evidence unverified | `train.py` configures MLflow; local database not found. |
| Best model at `model/model.joblib` | Not satisfied in supplied checkout | Required `model/` directory/artifacts are absent. |
| Model loaded once at FastAPI startup | Implemented, currently broken | Lifespan loads once but fails because artifact is missing. |
| `GET /health`, `POST /predict`, `/`, `/docs` | Implemented, currently broken | Routes exist; API cannot start locally. |
| Four meaningful pytest tests including domain logic | Implemented | Broad suite; current API subset fails due artifact packaging. |
| Dockerfile uses `python:3.11-slim` | Verified | `Dockerfile` base image is correct. |
| `requirements-serve.txt` | Verified | Present. |
| GitHub Actions on main | Implemented | Workflow exists; cannot verify remote execution without git/remote access. |
| Prediction-input logging | Implemented | JSONL log code exists; unsuitable as hosted durable logging. |
| Live deployment | Not verified / not present | README uses placeholder `LIVE_URL`; no remote URL or successful request evidence. |

## Web-migration readiness and blockers

The next phase should preserve the core services and database business rules, while introducing a REST API that fronts those services. It should not wrap the PySide6 screens or present static placeholders for features that lack working endpoints.

Priority blockers before frontend work:

1. Reconcile the FastAPI `model/` artifact contract with the desktop `models/` registry, then retrain/export a reproducible serving artifact and re-run API tests.
2. Stabilise a single backend application boundary for events, registrations, predictions, attendance, analytics, reports and notifications; the current `app.py` exposes prediction only.
3. Design and test a PostgreSQL-compatible repository/migration layer while retaining SQLite local mode and explicit backups.
4. Define authentication and roles before exposing event/attendance changes or QR check-in.
5. Build a React/Vite frontend only after endpoints and schemas are established; no existing frontend code can be migrated mechanically.
6. Inspect current official hosting documentation during architecture/deployment work; no deployment has been attempted or claimed by this audit.

## Recommended controlled migration sequence

1. Repair and verify the serving model artifact path, then establish a backend regression suite.
2. Extract the desktop service/repository operations behind versioned Pydantic request/response schemas.
3. Add PostgreSQL support with backup, migration and source-labelling tests; leave SQLite as local fallback.
4. Add secure authentication/authorisation and restrict QR operations to authenticated organiser workflows or a purpose-designed token flow.
5. Create the React/Vite application and migrate each of the fourteen pages against real endpoints, with loading, error and empty states.
6. Add frontend tests, API integration tests and deployment configuration. Verify a real remote health and prediction request before publishing a URL.

## Audit limitations

- There is no git history or remote configuration in this supplied folder.
- The official CSL236 guideline itself was not separately identifiable; it must be supplied or located before final compliance can be certified requirement-by-requirement.
- Existing historical test logs were retained as evidence but not represented as a fresh successful test run.
- No deployment was performed, and there is no live URL to report.
