# EventPulse AI — Phases 4–7 summary (GUI, analytics, reports, packaging, verification)

Everything below was executed on the target Mac (Apple Silicon, Python 3.12.8, PySide6 6.12.0,
scikit-learn 1.9.1), not only in a development sandbox.

## What was built

| Area | Delivered |
|---|---|
| Analytics (`core/analytics.py`) | Overview KPIs, participation by department/course/year/type, event trends, predicted-vs-actual, prediction-error summary, probability distribution, model comparison, ROC recomputed from stored holdout ids, permutation feature importance, dataset profiling and cleaning |
| Charts (`core/charts.py`) | One Matplotlib layer shared by the GUI and the reports; dark and light themes; palette validated for colour-vision deficiency against the dark surface |
| Reports (`core/reports.py`) | PDF (ReportLab) with KPIs, model performance, event analysis and key findings; Excel workbook (9 sheets plus a native chart); CSV/Excel export of any analysis table. Every report carries the simulated-target notice |
| Desktop GUI (`gui/`) | 8 pages — Overview, Dataset Explorer, Event Management, Attendance Prediction, Model Laboratory, Analytics & Insights, Reports & Export, Settings. Background threads for training and reports, sortable/filterable tables, first-run setup, error dialogs and crash logging |
| Packaging | `EventPulse_Build_App.command` builds a self-contained `EventPulse AI.app` (PyInstaller 6.22.3 onedir, 227 MB, own icon, bundle id `in.eventpulse.desktop`) and runs a built-in self-test of all 8 pages before installing it in the project folder |
| One-click scripts | `EventPulse_Setup.command` (environment, dependencies, models, database, tests), `EventPulse_Verify.command` (full test suite plus screenshots), `EventPulse_Build_App.command` |

## Test results (run on the Mac)

`EventPulse_Verify.command`, 12 Nov 2026 11:53 local time: **137 passed, 0 failed** (93 s).

- 127 backend tests: data loading, validation, preprocessing, synthetic generator, training,
  prediction, database schema/repositories/services/backup, analytics and reports.
- 10 GUI tests (offscreen Qt): every page loads, overview matches the database, dataset
  workflow, event lifecycle with prediction and actual attendance, individual and bulk
  prediction, model inspection/activation/retraining, reports and exports, window resizing
  (no horizontal scrolling at 1100, 1280 and 1920 px), and Settings values not clipped.

`EventPulse_Build_App.command`, 11:55: build OK, packaged-app self-test OK.

## Live verification of the packaged app

The installed `EventPulse AI.app` was launched like a normal app and driven on screen:

- All 8 pages opened and rendered with data (`docs/screenshots/live/`).
- Dataset Explorer loaded the processed dataset: 383 rows, 25 columns, validation passed.
- A whole-event prediction for EV22 was stored (24 registrations, expected 18.5 attendees,
  19 predicted to attend / 5 no-shows) and the Overview updated to match.
- PDF (414 KB) and Excel reports were generated from the Reports page.
- Model Laboratory shows all 5 models; active model Logistic Regression v1:
  CV ROC-AUC 0.786 ± 0.061, holdout ROC-AUC 0.884, accuracy 80.5 % against a 68.8 %
  majority baseline, threshold 0.68 chosen on training data only.

## Defects found on the Mac and fixed

| Found | Fix |
|---|---|
| Charts not sized to their canvas on Retina | Figure fitted using the device pixel ratio |
| "&" in labels shown as a keyboard mnemonic | Escaped in buttons and navigation |
| Window larger than a laptop screen | Opens at 80 % × 88 % of the available screen |
| Attendance Prediction needed horizontal scrolling at 1100 px (235 px) | Two-column form, compact combo boxes; Model Laboratory controls split over two rows; test now fails on any page that overflows |
| Settings values cut to one line ("Raw_Merged_Data is") | Info forms let values use the full width and wrap; new test |
| Live window could not be targeted while the app ran as a Python process | Packaged as a real `.app` bundle |

## Known limitations

- Will_Attend in the original dataset is simulated, so all metrics describe how well the
  models recover the simulation rules, not real-world attendance.
- The app is not code-signed or notarised; on first launch macOS may ask you to confirm it
  (System Settings → Privacy & Security → Open Anyway).
- The packaged app reads and writes the project folder next to it; keep `EventPulse AI.app`
  inside `EventPulseAI/` or set `EVENTPULSE_HOME`.
