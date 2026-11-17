# EventPulse AI — audit before version 1.2 (13 Nov 2026)

Checked on the actual project and its databases, read-only, before any change.

## Project as found (v1.1.0)

| Area | Found |
|---|---|
| Desktop app | PySide6, 8 pages (Overview, Dataset Explorer, Event Management, Attendance Prediction, Model Laboratory, Analytics & Insights, Reports & Export, Settings), packaged `.app` + DMG |
| ML core | `core/` — schema, validation, preprocessing pipeline (FeatureEngineer + ColumnTransformer), 5 model versions (original-notebook baseline, logistic regression, decision tree, random forest, gradient boosting), grouped CV + holdout, permutation importance |
| Database | `database/eventpulse.db`, SQLite WAL, schema version 2 (migrations `0001`, `0002`), 16 tables + 4 views, automatic verified backup before migrations |
| Tests | 152 passing on the Mac on 12 Nov (136 core + 16 GUI) |
| End-term (added 12–13 Nov) | `train.py` + MLflow, `app.py` FastAPI + UI, Dockerfile, CI workflow, README data contract — written, not yet run on the Mac |

## Training data and the 783 vs 383 discrepancy

| Dataset | Rows | Students / events | Where it is used | Outcomes |
|---|---|---|---|---|
| `data/processed/ml_dataset_processed.csv` (from the mid-term `ML_Dataset` sheet) | **383** | 279 / 36 | Desktop model training **and** the main database (383 registrations) | SIMULATED (`ORIGINAL_SIMULATED`) |
| `data/synthetic/synthetic_registrations.csv` (`core/synthetic.py`, seed 2026) | **783** | 600 / 48 | **Only** the separate Demo Mode database `database/eventpulse_demo.db` (verified: 783 registrations, all `SYNTHETIC`) | SYNTHETIC |
| `data/event_attendance.csv` (same generator, 1,000 students, 60 events, seed 2026) | **1,323** | 1,000 / 60 | End-term model (`train.py`, MLflow, `model/model.joblib`) | SYNTHETIC |

They are **three different datasets**. The 383 rows were never merged with the 783 or the 1,323;
the synthetic files are generated independently from the documented rules, not by copying rows.

## Simulated vs recorded

* Main database: 383 attendance records, **all `ORIGINAL_SIMULATED`**; **0 recorded after a real event**.
* All 36 events are dataset events: status Completed, **no dates, no capacities, no deadlines**.
* So: every outcome-based figure (attendance rates, insights, predicted-vs-actual) currently describes
  simulated data. The app labels this; real attendance can only come from events created in the app.

## Problems found

| # | Problem | Action in 1.2 |
|---|---|---|
| 1 | `record_actual_attendance` could overwrite a SIMULATED outcome with a different value while keeping its simulated label (real and simulated would mix) | Refused with a clear error; unchanged values still evaluate as simulated (existing behaviour); head counts refused for dataset events. Tests added |
| 2 | Analytics and Insights were one page | Split: Analytics (data) and Insights (derived observations with evidence thresholds) |
| 3 | No registration deadline field; no calendar possible for undated events | Migration 0003 adds `registration_deadline`; calendar shows dated events and counts undated ones |
| 4 | No check-in workflow; attendance only entered after the event | QR + desk check-in, finalise, corrections with audit (migration 0003: `checkin_tokens`, `checkins`) |
| 5 | Any registered model could be activated, even if incompatible | Activation checks file, checksum, scikit-learn version and inputs |
| 6 | 14 navigation items would not fit the fixed sidebar on small screens | Sidebar navigation scrolls |
| 7 | Tests hard-coded schema version 2 | Updated to 3 |
| 8 | Event Management already has registration and attendance tabs | Kept. New pages are cross-event views (all registrations, all attendance); no duplicate forms |

## Not changed

Model training/evaluation code, thresholds, existing tables and rows, the original reference files,
Demo Mode, backups.
