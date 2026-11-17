# EventPulse AI 1.1.0 — desktop application guide
*Predict. Plan. Participate.*

A macOS desktop application that predicts whether registered students will attend college events, estimates expected head-counts, compares predictions with actual attendance after events, and produces PDF/Excel reports. Built on the CSL236 *AI-Based College Event Attendance Prediction System* (Team 8, The NorthCap University).

> **Important:** the training target `Will_Attend` is **simulated** from a documented probability model; no real attendance was recorded. All metrics show how well a model recovers the simulation rules. They do not establish real-world prediction performance. The app shows this notice wherever simulated outcomes are used.

---

## Launching the app (everyday use)

**Double-click `EventPulse AI.app`** in this folder. That's it. No Terminal and no Python commands.

- To keep it handy, **drag `EventPulse AI.app` to the Dock**, or ⌥⌘-drag it to Applications to create an *alias*. Keep the app itself inside this `EventPulseAI` folder: it stores the database, models and data here.
- First launch can take a few seconds while macOS checks the app.
- If macOS says the app "cannot be opened because the developer cannot be verified", open **System Settings → Privacy & Security** and click **Open Anyway** (one time). The app is built locally and is not signed or notarised by Apple (that needs a paid Apple Developer ID).
- A disk image is also produced: `release/EventPulse AI 1.1.0.dmg` (app + Applications shortcut). The app in it still reads its data from this `EventPulseAI` folder on this Mac.

## What's new in 1.1.0
- **New visual design**: warm cream canvas, white cards, lavender highlights, purple primary actions, soft-blue information and gold highlights; new logo, app icon and start-up screen; line icons in a grouped sidebar; responsive card grids that reflow at narrow widths. An *Evening* theme keeps the same identity on a dark plum background (Settings → Appearance).
- **Demo Mode** (Settings → Dataset configuration): opens a *separate* database built only from the labelled synthetic dataset. A gold banner stays visible while it is on; your own database is never written with demo rows.
- **Data provenance everywhere**: the Overview shows how many records come from the original dataset, synthetic demo data or entries made in the app, and separates *simulated* outcomes from attendance *recorded* after real events; tables colour-code status and source.
- **Prediction**: choose any model version (default: active model); plain-language lists of the inputs that raised or lowered the probability; expected attendees *and* expected no-shows with ranges; recorded attendance for the event is shown separately from the prediction.
- **Model Laboratory**: active model highlighted, every metric explained (hover or the "How to read these metrics" section); cross-validation is presented as the primary estimate.
- **Analytics**: data-source filter, model performance comparison chart.
- **Reports**: report presets, a live summary of the figures that will be included, event and date filters with a row preview for table exports, open/show-in-Finder after generation; reports carry the new branding and list simulated and recorded outcomes separately.
- **Settings**: Application information, Model configuration, Dataset configuration (with Demo Mode), Database & backup, Data privacy, Appearance and About (also in the app menu: *EventPulse AI → About EventPulse AI*).

## One-time setup (already done on this Mac)

| Script (double-click) | What it does |
|---|---|
| `EventPulse_Setup.command` | Creates `.venv`, installs `requirements.txt` (needs internet), checks the saved models against the installed scikit-learn (retrains if needed), syncs the database and runs the test suite. Log: `logs/setup_*.log` |
| `EventPulse_Build_App.command` | Builds the standalone `EventPulse AI.app` with PyInstaller, runs the packaged app's self-test (every page at 1100, 1280 and 1920 px), installs it in this folder and writes `release/EventPulse AI <version>.dmg`. The earlier lightweight launcher is kept in `packaging/launcher/` |
| `EventPulse_Verify.command` | Runs all automated tests (including GUI tests off-screen) and renders page screenshots into `docs/screenshots/v1.1.0/rendered/` |

## What the app does (1.2.0 — 14 pages)

| Section | Page | Highlights |
|---|---|---|
| Workspace | **Overview** | Students, events, predicted attendees / no-shows, CV ROC-AUC and holdout accuracy with baseline, data sources (simulated vs recorded), recent activity |
| | **Dataset Explorer** | Import CSV/Excel (personal-data columns and `Raw_Merged_Data` refused), types, missing values, validation, cleaning, export |
| | **Event Management** | Create / edit / cancel / delete events, dates, times, capacity, **registration deadline**, registrations, forecasts, actual attendance or head count |
| | **Event Calendar** | Month view of dated events; upcoming / ongoing / completed / cancelled filters; undated dataset events counted separately |
| | **Registration Management** | All registrations across events, search and filters, status changes, department-wise participation, CSV/Excel import with duplicate refusal (all-or-nothing) |
| | **Attendance Prediction** | Individual (with explanation), whole event, bulk file, **What-if planner** (event-level inputs and registration count; never stored) |
| | **Attendance Records** | QR check-in (see `docs/CHECKIN.md`), desk check-in, finalise after the event, named + justified corrections with audit log, CSV/Excel export; simulated outcomes read-only |
| Intelligence | **Model Laboratory** | Model versions, CV vs holdout, ROC, confusion matrix, importance, **MLflow runs** from `mlflow.db`, activation only for compatible models |
| | **Analytics** | Participation by group, event trends, predicted vs actual, model comparison, probability distribution, importance |
| | **Insights** | Data-derived observations (events, departments, timing, planning, prediction errors) with sample sizes, 95 % intervals and SIMULATED/RECORDED labels |
| | **Model Monitoring** | Incoming inputs vs training data (drift), missing values, schema violations (incl. API 422s), recorded-event accuracy, retraining recommendation |
| Manage | **Reports & Export** | PDF/Excel reports, table exports incl. recorded/simulated attendance, registrations, calendar, MLflow runs |
| | **Notifications** | Upcoming events, deadlines, capacity, overdue attendance — in-app, dismissible |
| | **Settings** | Application, dataset (Demo Mode), model, database & backup, privacy, appearance, About |

Keyboard: ⌘1–⌘9 open the first nine pages.

## Evaluation in one paragraph
Student-grouped 20% holdout (no student in both parts), hyper-parameters tuned by 5-fold student-grouped cross-validation on training data only, decision threshold chosen on out-of-fold training predictions, recommendation = best mean CV ROC-AUC. On the processed original dataset the recommended Logistic Regression reaches **CV ROC-AUC 0.786 ± 0.061**. The seed-42 holdout gives 0.884 / 80.5% accuracy against a 68.8% majority baseline, but that split is easier than average: over 10 other splits the range is 0.69–0.88. The original notebook pipeline is kept as a baseline (CV 0.753) and still reproduces its historical 67.53% / 0.7932. Details: `docs/PHASE2_SUMMARY.md`.

## Developer commands
```bash
.venv/bin/python main.py                              # run from source
.venv/bin/python -m pytest -q                         # 152 tests (16 GUI tests need PySide6)
.venv/bin/python scripts/prepare_data.py              # rebuild data/processed/
.venv/bin/python scripts/generate_synthetic.py        # rebuild data/synthetic/ (labelled SYNTHETIC)
.venv/bin/python scripts/train_models.py              # retrain + compare (also possible in the app)
.venv/bin/python scripts/manage_db.py status|backup|list-backups|restore <file>|sync-models
.venv/bin/python scripts/render_er_diagram.py         # docs/er_diagram.png from the live schema
.venv/bin/python scripts/capture_screenshots.py       # docs/screenshots/v1.1.0/rendered/ from the real UI
python3 scripts/render_brand.py                       # assets/: logo_mark.png, icon.png, AppIcon.icns
```

## Project layout
```
EventPulse AI.app        standalone macOS app (PyInstaller)          main.py   entry point
config/settings.py       paths, seeds, notices
core/                    schema, loading, validation, preprocessing, synthetic generator, training,
                         evaluation, prediction, analytics, charts, reports
core/db/                 SQLite: connection/transactions, migrations (sql_migrations/), repositories,
                         services, backup/restore
gui/                     PySide6 app: theme (design tokens + style sheet), icons, branding (logo, splash,
                         About), widgets, main window, dialogs, pages/ (8 pages)
assets/                  logo mark, app icon (PNG + .icns)       release/  disk image
data/original/           untouched reference workbook (only ML_Dataset is ever read)
data/processed/          processed dataset + metadata          data/synthetic/  SYNTHETIC dataset
models/                  trained pipelines + metadata (+ runs/ for retraining from the app)
database/                eventpulse.db and backups/            reports/, exports/  generated files
docs/                    audit, data card, synthetic data, database design + ER diagram,
                         phase summaries, screenshots/
tests/                   152 automated tests                    packaging/  PyInstaller spec, launcher
logs/                    setup/verify/build logs, app.log
```
