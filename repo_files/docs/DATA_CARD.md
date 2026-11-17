# Data Card — EventPulse AI datasets

> **The target `Will_Attend` is SIMULATED in every dataset here.** No attendance was ever recorded. Model metrics measure how well a model recovers the documented simulation rules. They do not establish real-world attendance prediction performance.

## 1. Datasets

| Dataset | Location | Origin | Used for |
|---|---|---|---|
| Original reference workbook | `data/original/College_Event_Attendance_ML_Final.xlsx` | Team 8 (CSL236). Archived **unchanged** (SHA-256 checked) and marked read-only | Source of truth. Only the `ML_Dataset` sheet is ever read |
| Processed dataset | `data/processed/ml_dataset_processed.csv` + `.metadata.json` | Built by `scripts/prepare_data.py` from `ML_Dataset` | Default training data (`Data_Source = ORIGINAL_HYBRID`) |
| Synthetic supplementary dataset | `data/synthetic/synthetic_registrations.csv` (+ `_events.csv`, `.metadata.json`) | Built by `scripts/generate_synthetic.py` | Demonstration and experiments only (`Data_Source = SYNTHETIC`) |

The three are never merged automatically. Every row carries a `Data_Source` label.

## 2. Personal data

`Raw_Merged_Data` (names, emails, roll numbers, mobile numbers) is **never opened**:
- `core/data_loader.py` lists it in `FORBIDDEN_SHEETS` and refuses it even when asked for by name.
- Any uploaded file containing a personal-data column (`Name`, `Email_Address`, `Mobile_No`, `Roll_Number`, `Person_ID`, …) is rejected with an explanation.
- `Student_ID` values are the workbook's pseudonymous codes (`STU001`…).

## 3. Processed dataset: what changed and why

| Step | Rows affected | Why |
|---|---|---|
| `Weather` renamed `Weather_Forecast` | all | The value is the event-day weather, which can only be known in advance as a forecast |
| Event-level repair from the same `Event_ID`'s other rows | Weather_Forecast 19, Event_Popularity 11 | Event attributes are constant per event (verified), so another registration of the same event holds the true value |
| Student-level repair from the same `Student_ID`'s other rows | Academic_Engagement_Score 10, Previous_Events_Registered 7 (+ rate 7), Distance 5 | Student attributes are constant per student (verified) |
| Left blank for in-pipeline imputation | Distance 14, Engagement 7, Prev. registered 8, Prev. rate 8 | No same-entity source exists. Imputed by medians learned on training rows only |
| `Distance_Category` rebuilt from distance | — | Kept for analytics. Not a model input (it duplicates distance) |

The target is never used in any repair step, and a test checks this. No value was taken from the workbook's "value before blanking" list in Generation_Log §10.

## 4. Weather forecast policy

- **Meaning:** the event-day forecast available when the prediction is made.
- **Original rows:** these hold simulated event-day weather. They are treated as if a forecast had been available at registration. **When the forecast was obtained is unknown for these rows**, and the metadata records `forecast_obtained_at = null`.
- **No forecast:** stored as the explicit category `Unavailable`. A weather value is never invented. No original training row lacks a forecast once repaired, so the models treat `Unavailable` as an unseen category, meaning **no weather adjustment**.
- **Measured effect:** with only 383 rows, the trained models learned almost no weather effect. Across the dataset, switching every forecast between Clear, Rainy and Unavailable moves the mean predicted probability by **under 1 percentage point** for every model. The simulation itself gave rain a strong negative coefficient (−0.94), so this is a sample-size limitation, not evidence that weather does not matter.
- **New events (Phase 3+):** the app stores the forecast together with the date and time it was obtained.

## 5. Model inputs

All 20 inputs are known before the event (enforced by an assertion in `core/schema.py`):

Course, Year, Currently_UBA_Volunteer, Participated_UBA_Event_Before, Experience_Level, Area_Of_Interest, Role, Event_Type, Event_Day, Event_Time, Event_Duration_Hours, Registration_Days_Before, Previous_Event_Attendance, Previous_Events_Registered, Previous_Attendance_Rate, Academic_Engagement_Score, Event_Popularity, Distance_From_Venue_KM, Has_Exam_Near_Event, Weather_Forecast.

Engineered inside the pipeline (stateless, documented): Department (from Course), Interest_Match, Role_Match, Registration_Window, and log1p of three count columns.

**Interpretation caveat.** Interest_Match, Role_Match and Registration_Window mirror terms of the simulation formula (Generation_Log §7, §9). They are legitimate pre-event features, but any lift they give shows the model recovering the simulation, not a finding about students.

**Not used:** `Registration_Status`. Every row is a registration, so the value would be constant. It becomes an operational field in Event Management (Phase 3).
