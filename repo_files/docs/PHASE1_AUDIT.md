# EventPulse AI — Phase 1 Audit Report

*Predict. Plan. Participate.*
Audit date: 12 Nov 2026 · Scope: the four reference files in `Aiml/` · No reference file was modified.

---

## 1. What was inspected

| File | What it is | Status |
|---|---|---|
| `College_Event_Attendance_ML_Final.xlsx` | 6 sheets: `ML_Dataset` (383 × 24), `Raw_Merged_Data` (383 × 20), `Data_Dictionary`, `Generation_Log`, `Data_Quality_Report`, `Row_Provenance` | Fully profiled |
| `College_Event_Attendance_ML.ipynb` | Previous implementation: EDA → split → impute → bin → one-hot → scale → SelectKBest(15) → Logistic Regression | Re-executed |
| `Team_8_Presentation.pptx` | 12-slide mid-term deck (Team 8) | Read |
| `Team_8_Project_Description.docx` | Project write-up with preprocessing table and results | Read |

## 2. Dataset understanding

- **Unit of analysis:** one row = one student's registration for one event.
- **Size:** 383 rows, **279 students**, **36 events**. 201 students have 1 row; 78 have 2–6 rows.
- **Target:** `Will_Attend`: 265 × 1 (69.19%) and 118 × 0 (30.81%). The classes are moderately imbalanced.
- **Provenance:** this is a hybrid dataset, and the workbook documents it well.
  - **Real (pseudonymised):** Course and Year (read from roll numbers) and Role. Survey answers are real for 202 rows; Experience_Level is real for 46 rows.
  - **Rule-filled:** survey answers for 181 rows; Experience_Level for 337 rows.
  - **Fully simulated (seed 42):** the 36-event calendar, registration timing, attendance history, engagement, distance, exam proximity, weather and **the target**.
- **The target is simulated.** `Generation_Log` §9 contains the exact logistic formula that produced `Will_Attend`, plus a hidden per-student random effect. Any metric measures how well a model recovers those rules, not how real students behave. The app must state this wherever metrics are shown.

### Quality checks (recomputed, not copied from the workbook)

| Check | Result |
|---|---|
| Exact duplicate rows | 0 |
| Duplicate rows ignoring IDs | 0 |
| Same student registered twice for the same event | 0 |
| Missing values | 7 columns, 2.87–4.96% each; 75 rows have ≥1 gap. Missingness was added deliberately (MCAR, plus MAR on Academic_Engagement_Score) |
| Student-level fields consistent across a student's rows | Yes, all 8 checked |
| Event-level fields consistent across an event's rows | Yes, all 6 checked |
| Contradictions (attended > registered, UBA participant with 0 attendance, Sunday events) | None |
| `Previous_Attendance_Rate` vs recomputed rate | 2 rows differ by 0.1 because of half-rounding (56.25→56.2, 81.25→81.2). Harmless |
| Outliers | All legitimate (long commutes, early sign-ups, two long-format events) |

### Leakage review

| Feature | Known before the event? | Verdict |
|---|---|---|
| Previous_* history, engagement, profile fields | Yes ("before this semester") | Keep |
| Event_Type / Day / Time / Duration / Popularity | Yes (popularity = "pre-event interest score") | Keep |
| Registration_Days_Before, Has_Exam_Near_Event | Yes | Keep |
| **Weather** | **Only as a forecast.** It is the simulated weather *on the event day* | Keep, relabelled as a forecast input, **or drop**. Decision needed (§6) |
| Distance_Category | Derived from Distance_From_Venue_KM | Use one or the other, not both (the notebook already did this) |
| Student_ID / Event_ID | — | Never features. Use them only for grouping and event-level imputation |
| Target probability | Not in the dataset | No leakage |

**Personal data:** `Raw_Merged_Data` contains real names, NCU emails, roll numbers and mobile numbers. `ML_Dataset` correctly excludes all of them. EventPulse AI will **never load that sheet**.

## 3. Re-run of the existing Logistic Regression

The notebook pipeline was re-executed exactly as written (80:20, `random_state=42`, stratified, SelectKBest k=15).

| Metric | Reported | Recomputed |
|---|---|---|
| Test rows | 77 | 77 |
| Accuracy | 67.53% | **67.53%** ✔ |
| ROC-AUC | 0.7932 | **0.7932** ✔ |
| Confusion matrix | [[9, 15], [10, 43]] | identical ✔ |
| Recall, non-attenders | 0.38 | 0.375 ✔ |

The historical results are **reproducible**. Three caveats:

1. **Accuracy is below the trivial baseline.** Always predicting "attend" scores **68.83%**. The model's value is its ranking ability (AUC), not its accuracy at the 0.5 threshold. The report and the deck already say this honestly.
2. **One split is noisy.** Over 20 different random splits, the same pipeline's test AUC ranges from **0.70 to 0.87** (mean 0.795). 0.7932 is a typical draw, not a stable estimate.
3. **Student overlap.** 23 students (25 test rows) appear in both train and test. Student-grouped 5-fold CV gives AUC 0.788 ± 0.043, so the overlap does not noticeably inflate the score here. A grouped split is still the correct design, because the generator includes a hidden per-student effect.

### Notebook defects (do not affect the reported numbers when cells run out of order)

- `np` is never imported, but it is used in cells 13 and 26.
- `sns` (seaborn) is never imported (cells 12, 15).
- `plt` is used in cell 12 but only imported in cell 35.
- Cell 52 duplicates cell 46.
- The notebook therefore cannot run top-to-bottom on a fresh kernel.

### Inconsistencies across notebook, docs and deck

None material. Counts, missing-value figures, the 15 selected features, metrics and the confusion matrix agree across all three. The docs' "23 students in both sets" matches the recomputation. The project-description template still has blank Team No. / Members fields.

## 4. Preview: four models, student-grouped 5-fold CV (exploratory, not final)

| Model | ROC-AUC | Accuracy | Balanced acc. | Macro-F1 |
|---|---|---|---|---|
| Logistic Regression (balanced) | 0.764 ± 0.078 | 0.689 | 0.667 | 0.655 |
| Decision Tree (depth 4) | 0.711 ± 0.046 | 0.658 | 0.653 | 0.626 |
| Random Forest | 0.783 ± 0.055 | 0.747 | 0.713 | 0.705 |
| Gradient Boosting | 0.763 ± 0.058 | 0.744 | 0.641 | 0.650 |
| LogReg + interest/role-match features | 0.779 ± 0.073 | 0.700 | 0.677 | 0.666 |

Takeaway: realistic performance on this data is **AUC ≈ 0.75–0.80**. The models differ by less than their fold-to-fold noise. Phase 2 will tune them by CV on the training portion only and report one untouched holdout.

## 5. Mapping the requested feature list to the real schema

| Requested | Available as | Note |
|---|---|---|
| Department | `Course` → 6 groups (Engineering, Commerce, Law, …) | Grouping documented in Generation_Log §4g |
| Academic_Year | `Year` | |
| Event_Type / Day / Timing / Duration | `Event_Type`, `Event_Day`, `Event_Time`, `Event_Duration_Hours` | |
| Previous_Attendance | `Previous_Attendance_Rate`, `Previous_Event_Attendance` | |
| Previous_Event_Participation | `Participated_UBA_Event_Before`, `Previous_Events_Registered` | |
| Venue_Distance | `Distance_From_Venue_KM` | |
| **Registration_Status** | **Not supported** | Every row *is* a registration, so the column would be constant. It will not be fabricated. In the app it becomes an operational field (registered / cancelled) on the Event Management side, not a model input |

## 6. Implementation plan (Phases 2–7)

**Phase 2: Data and ML architecture**
- Copy the workbook unchanged into `data/original/`. Extract `ML_Dataset` only, after verifying it has no PII columns.
- Create a validation schema covering the 21 features, their types, allowed categories and ranges.
- Build one sklearn `Pipeline`: imputation, ordinal encoding for ordered columns, one-hot encoding, scaling, and optional engineered match features. All fitting happens inside the pipeline.
- Split: **student-grouped stratified holdout (20%)**, which is never touched until final evaluation. Run model selection with `StratifiedGroupKFold` on the training portion only.
- Train the four models; keep the original notebook pipeline as the named baseline. Choose the decision threshold by CV on training data.
- Supplementary synthetic dataset: a separate, clearly labelled file in `data/synthetic/`, generated from the documented §6/§9 rules with a new seed and new pseudonymous IDs (`SYN-STU-…`). It will never be mixed with the original without a source flag.

**Phase 3: Backend and database.** SQLite tables: students, events, registrations, predictions, model_registry, settings, datasets. Repository layer with tests.

**Phase 4: Models and evaluation.** Persisted pipelines (joblib) with metadata (feature list, metrics, data hash, sklearn version), plus an evaluation module producing confusion matrices, ROC curves and CV tables.

**Phase 5: PySide6 GUI.** Dark navy theme, sidebar, KPI cards, sortable/filterable table models, embedded matplotlib charts.

**Phase 6: Integration.** Wire all 8 pages to the backend, with no hard-coded values.

**Phase 7: Testing and polish.** pytest suite (data, ML, DB, reports, offscreen GUI navigation), PDF/Excel reports, screenshots, README.

## 7. Open decisions / blockers

1. **Folder access.** Granting `~/Downloads/Aiml` from the chat was refused. The folder must be added with the **Add folder** button in the Claude desktop app so the project can be written there.
2. **Weather.** Keep it as a forecast input, or drop it to be strictly pre-registration? (Recommended: keep it, labelled "forecast".)
3. **Generator script.** `generate_ml_dataset.py` is referenced in the workbook but was not among the uploads. If it exists, the synthetic supplement should reuse it. Otherwise a new generator will be written from the documented rules.
4. **Environment.** PySide6 is not installed in the build workspace, which has no package access. GUI tests and screenshots must run on your Mac (or in the linked VM) after `pip install -r requirements.txt`.
