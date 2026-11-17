# Phase 3 Summary — Database and backend services

The full design (ER diagram, relationships, integrity rules, error definitions) is in **`docs/DATABASE.md`**.

## 1. Requirements checklist

| # | Requirement | How it is met |
|---|---|---|
| 1 | Relational design with PKs, FKs, constraints | 16 tables, 23 foreign keys, enforced on every connection. CHECK constraints on every coded column; table CHECKs for cross-column rules |
| 2 | No duplicate student–event registrations | `UNIQUE (student_id, event_id)`, reported as "This student is already registered for this event." |
| 3 | Predicted and actual attendance kept separate | `predictions` vs `attendance_records` / `event_outcomes`, linked only through `registrations` and `prediction_evaluation_items` |
| 4 | Probability, outcome, model version, timestamp | Columns on every `predictions` row, plus threshold, certainty, forecast used and a JSON input snapshot |
| 5 | Complete prediction history | `BEFORE UPDATE` trigger makes predictions append-only. Re-predicting adds rows; `v_latest_predictions` picks the newest |
| 6 | Model versions and metrics | `model_versions` (version number, artifact SHA-256, threshold, parameters, training data, library versions, one active) + `model_metrics` (CV, holdout, majority baseline, split sensitivity, confusion cells) |
| 7 | Forecast and forecast timestamp stored separately | `weather_forecasts` history table with `forecast_obtained_at`. A real forecast without a timestamp is rejected; imported historical rows keep `NULL` and say why |
| 8 | Non-destructive migrations | Versioned SQL files, one transaction each, checksum verification, automatic verified backup before migrating, newer databases refused, additive-only policy |
| 9 | Backup and restore | Online backup API, integrity-verified. Restore validates first, takes a safety backup, migrates older backups and falls back on failure. Pruning keeps safety copies |
| 10 | Errors and transactions | `BEGIN IMMEDIATE` with nested savepoints; typed exceptions with user-facing messages; WAL and busy timeout |
| 11 | Personal data protected; Raw_Merged_Data never imported | No PII columns. PII fields and e-mail/phone/roll-number-shaped IDs are rejected in code, and phone/roll-number patterns again in SQL. The loader refuses PII columns and the sheet; `datasets.source_sheet` CHECK refuses `Raw_Merged_Data` |
| 12 | Tests for all major operations | 54 database tests (section 3) |
| + | Actual attendance → predicted-vs-actual error | `services.record_actual_attendance()` takes per-student values or a head count (+ walk-ins), marks the event Completed and stores an evaluation per model version. Only predictions made before the outcome and the event date are used. Simulated outcomes are flagged and excluded from accuracy history by default |

## 2. Implementation

| File | Contents |
|---|---|
| `core/db/sql_migrations/0001_initial_schema.sql` | 16 tables, constraints, append-only trigger, single-active-model index |
| `core/db/sql_migrations/0002_indexes_and_views.sql` | 8 indexes; views `v_latest_forecast`, `v_registration_features`, `v_latest_predictions`, `v_event_participation` |
| `core/db/connection.py` | `Database`: connection pragmas, nested transactions, query helpers, audit, integrity check |
| `core/db/migrations.py` | Migration runner (ordering, checksums, pre-migration backup, rollback on failure, downgrade refusal) |
| `core/db/errors.py` | Typed exceptions and translation of sqlite3 errors into friendly messages |
| `core/db/repositories.py` | Settings, Dataset, Course, Student, Event, Forecast, Registration, ModelVersion, Prediction, Attendance and Evaluation repositories |
| `core/db/services.py` | `import_dataset` / `import_file`, `bootstrap`, `sync_models`, `registration_features`, `predict_event` / `predict_record` / `predict_file`, `record_actual_attendance`, `evaluate_event`, `model_accuracy_history` |
| `core/db/backup.py` | `backup_database`, `list_backups`, `prune_backups`, `validate_backup`, `restore_database` |
| `scripts/manage_db.py` | `init`, `status`, `backup`, `list-backups`, `restore`, `sync-models` |
| `scripts/render_er_diagram.py` | ER diagram (PNG + Mermaid) generated from the live schema |
| `database/eventpulse.db` | Initialised database: 279 students, 36 events, 383 registrations, 36 forecasts, 383 simulated outcome records, 5 model versions (Logistic Regression active). Integrity ok |

**Data check:** a test joins `v_registration_features` back to the processed CSV for all 383 registrations. Course, year, registration days, popularity, distance, attendance rate, engagement and forecast all match exactly, so predictions made from the database use the same inputs the models were evaluated on.

## 3. Tests run

`python -m pytest -q` was **executed**: **108 passed, 0 failed** (about 52 s). That is 54 Phase 2 tests and 54 new database tests.

| File | Tests | Covers |
|---|---|---|
| `test_db_schema.py` | 12 | Fresh schema and views; no PII columns; reopening is idempotent; **migration v1→v2 keeps data and makes a backup**; failed migration rolls back; edited migration detected; newer DB refused; nested transaction rollback; connection still usable after an error; FK error translation; predictions append-only; 7 CHECK constraints (bad year, attended > registered, wrong weekday, bad type, roll-number ID, forecast without timestamp, Raw_Merged_Data dataset) |
| `test_db_repositories.py` | 18 | Student CRUD and derived rate; 4 PII-shaped IDs and a PII field rejected; value validation; delete protection; date→weekday logic; invalid event values; event delete protects history / explicit delete with audit; forecast history, latest forecast and `Unavailable`; **duplicate registration prevented**; date-derived lead time; cancelled events and registrations; unknown references; model sync, metrics, idempotency, single active model, new artifact = version 2; settings; Raw_Merged_Data refused |
| `test_db_services.py` | 16 | Import of the processed dataset (counts, simulated labels, forecast provenance); **DB features equal the processed CSV**; re-import detected; conflicting import rolls back completely; PII files refused and workbook → ML_Dataset only; synthetic import stays labelled; event prediction stores every required field; history keeps all runs; individual + bulk-file predictions; no-active-model error; **per-student actuals with hand-checked error, percentage error and Brier score**; head-count actuals; corrections keep both evaluations and the old value; hindsight predictions excluded; future and cancelled events refused; simulated outcomes flagged and excluded from history |
| `test_db_backup.py` | 5 | Verified backup; restore with safety copy and DB still usable; older-schema backup migrated on restore; damaged, foreign, newer and missing files refused with data untouched; pruning keeps safety backups |

## 4. Decisions to be aware of
- **Original dataset events have no calendar dates**, only weekdays. They are imported as `Completed`, and their outcomes are stored as `ORIGINAL_SIMULATED` attendance so analytics can use them. They are always labelled simulated.
- **Predictions made after an event's date are ignored** when measuring error. Predictions must therefore be made before the event to count.
- **Event deletion with history** requires explicit confirmation (`include_history=True`); otherwise the UI should offer **Cancel event** instead.
- **Model artifact paths** are stored relative to the project, so the project folder can be moved.

## 5. Remaining for later phases
- **Your Mac:** the database is portable SQLite, but run `python scripts/manage_db.py status` and `python -m pytest -q` there after `pip install -r requirements.txt`. With a different scikit-learn version, retrain, then run `manage_db.py sync-models`; new artifacts are registered as version 2 and the old versions stay in history.
- **Phase 4 (as planned):** evaluation and visualization helpers on top of these tables (ROC and confusion-matrix data, analytics queries, report data), then the PySide6 GUI.
