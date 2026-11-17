# EventPulse AI — Web Application

The web application is a browser version of the EventPulse AI desktop app. It uses the
**same Python core, the same trained models and the same SQLite database** as the desktop
app, so both can be used side by side. The desktop app (`main.py`, `gui/`) and the
CSL236 serving API (`app.py`) are unchanged.

## Start and stop

| Action | How |
|---|---|
| First time | Double-click **`EventPulse_Admin_Setup.command`** to create the administrator (see `docs/AUTH.md`). |
| Start | Double-click **`EventPulse_Web.command`**. It checks the environment, backs up the database, starts the server and opens the browser. |
| Start for phone QR check-in | Double-click **`EventPulse_Web_LAN.command`** (listens on the local network; no login — trusted Wi-Fi only). |
| Stop | Close the Terminal window, or double-click **`EventPulse_Web_Stop.command`**. |
| Logs | `logs/web/run_*.log` |

| URL | What |
|---|---|
| http://127.0.0.1:8100/ | Frontend (React) |
| http://127.0.0.1:8100/api/… | Backend (FastAPI, JSON) |
| http://127.0.0.1:8100/docs | Interactive API documentation |
| http://127.0.0.1:8100/checkin/{EVENT_ID}?t=… | Public phone check-in page (opened from the QR code) |

Frontend and backend are served by one process on one port, so there is no CORS setup or
second server to keep in sync. For frontend development with hot reload (requires Node):
`cd web && npm install && npm run dev` → http://localhost:5173 (proxies `/api` to port 8100).

## Architecture

```
browser ──► webapp/server.py (FastAPI, port 8100)
              ├── /api/*        → core/ (services, analytics, intelligence, workflow, checkin, reports)
              │                    └── database/eventpulse.db  (SQLite, WAL, schema v3)
              │                    └── models/<model>/ (trained pipelines, loaded on demand and cached)
              ├── /checkin/*    → checkin_app.py handlers (same logic as the desktop QR server)
              └── /*            → web/dist (React build; SPA fallback to index.html)
```

* **Backend:** `webapp/server.py`. One database connection per request; all writes go
  through the existing repositories inside transactions and are audited. Errors are returned
  as JSON (`404` not found, `409` duplicate, `400` rule violation, `422` invalid input).
* **Frontend:** `web/` — React 19 with no UI or chart libraries (hand-made SVG charts, CSS
  design tokens in the EventPulse cream / lavender / purple palette). Hash routing
  (`#/events/EV12`). All data comes from the API; browser storage is not used.
* **Build:** `web/dist` is committed, so Node is not needed to run the app. `npm run build`
  (Vite) or `node build_esbuild.mjs` (offline esbuild build) regenerate it.
* **Database:** `EVENTPULSE_DB` overrides the database path (used by tests). SQLite is
  appropriate for one organiser machine; a cloud deployment needs a persistent volume or a
  port of `core/db` to PostgreSQL (see Limitations).

## Pages (migrated from the desktop app)

| Page | Real data / actions |
|---|---|
| Overview | KPIs, data-source breakdown, department chart, activity, recent predictions |
| Dataset Explorer | processed / synthetic / end-term datasets: preview with server-side search & paging, types, missing values, statistics, distributions, contract validation; inspect an uploaded CSV/Excel read-only |
| Event Management | create, edit, set status, delete (only without history), register students, predict the event, record a headcount, export |
| Event Calendar | month grid, day list, jump to months with events |
| Registration Management | filters, status changes, department chart, CSV/Excel import with dry run then all-or-nothing commit |
| Attendance Prediction | individual (with pre-fill from a stored registration and model choice), whole event, bulk file, **what-if planner**; every prediction is stored with model version and inputs |
| Attendance Records | QR self check-in, desk check-in, finalise, corrections with audit log, simulated outcomes shown separately |
| Model Laboratory | model comparison, activate (guarded), ROC, confusion matrix, cross-validation folds, permutation importance, MLflow runs |
| Analytics & Insights | participation by 11 dimensions, probability distribution, predicted vs actual, event trends; plain-language insights split by RECORDED vs SIMULATED |
| Model Monitoring | input drift vs training data, rejected requests, accuracy on recorded events, retraining recommendation |
| Reports & Export | PDF report, Excel workbook, 8 table exports (CSV / Excel) |
| Notifications | generated alerts, dismiss / restore (stored in the database settings table) |
| Settings | app, database (schema, integrity, demo/live), backups + "Back up now", privacy rules |

## Data honesty rules (enforced in the UI and API)

* Outcomes in the imported datasets are **SIMULATED** and are always labelled; analytics
  and insights keep RECORDED and SIMULATED results apart. Dataset events cannot receive
  "real" attendance.
* No chart or number is invented: empty states are shown when there is no data.
* Only pseudonymous Student IDs are stored; the `Raw_Merged_Data` sheet is never loaded and
  uploaded files with personal-data columns are refused.

## Tests

`tests/test_webapp.py` (8 tests) drives the API end to end with FastAPI's TestClient against
a temporary copy of the database: read endpoints, event lifecycle, registration, duplicate
rejection, event / individual / what-if predictions, check-in, finalise, correction,
predicted-vs-actual, CSV / Excel / PDF exports, edit-cancel-delete, QR code and check-in page.

## Limitations

* **Accounts and roles** (administrator / student) are described in `docs/AUTH.md`. Over plain
  HTTP cookies are not `Secure`; HTTPS is required before any hosted deployment.
* **SQLite.** Fine for one machine; not suitable for several concurrent cloud instances.
* **QR check-in** works only for devices on the same network, on the event day.
* The trained models learn from **simulated** labels; predictions are indications, not
  observed behaviour.
* Not deployed (by request). The newly uploaded datasets are not yet integrated.
