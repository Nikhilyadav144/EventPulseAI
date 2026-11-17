# EventPulse AI 1.1.0 — redesign and product polish

Everything below was implemented in the existing project, then built, tested and checked in the
running packaged app on the target Mac (Apple Silicon, macOS, Python 3.12.8, PySide6 6.12.0,
scikit-learn 1.9.1). Model training, evaluation, thresholds and predictions were **not** changed.

## 1. The new design

| Element | v1.0 | v1.1.0 |
|---|---|---|
| Canvas | dark navy `#0d1320` | warm cream `#F8F6F2` |
| Surfaces | navy cards | white cards, 14 px radius, hairline `#EBE7EF` borders |
| Primary | blue | purple `#6956A5` (hover `#5B4895`) |
| Highlights | — | lavender `#F0EDFA` (active navigation, selected rows, chips), soft blue `#DCE8F8` (information), gold `#E9BC65` (simulated-data notices, Demo Mode) |
| Text | near-white | ink `#29263A`, secondary `#57526B`, muted `#8A859C` |
| Status | — | green = completed / will attend / recorded; blue = scheduled / entered in app; gold = simulated / no-show; red = cancelled / errors |
| Charts | blue/orange on navy | purple (attend) and amber (not attend), teal and rose as 3rd/4th series; palette checked with a colour-vision validator; light grid, no chart box, wrapped category labels |

* **Brand:** a new logo and macOS icon (purple tile, white attendance "pulse" whose dotted forecast
  segment ends at a gold point = the prediction; no robots or brains), a start-up screen, and an
  About window (also in the macOS app menu: *EventPulse AI → About EventPulse AI*).
* **Sidebar:** logo + name + tagline; eight pages grouped as Workspace / Insights / Manage with
  consistent line icons drawn in code (crisp on Retina); lavender active state; an "Active model"
  card with a Live data / Demo Mode badge; version and About at the bottom.
* **Pages:** larger titles with short descriptions, consistent 32 px margins, section "eyebrows",
  KPI cards with tone-coloured icon chips, responsive card rows that reflow (6 → 3 → 2 columns)
  instead of scrolling sideways.
* **Evening theme** (Settings → Appearance) keeps the same identity on dark plum for low light.
  Earlier "dark/light" settings now open in the new Cream design.

## 2. Features added

| Area | What changed |
|---|---|
| Overview | KPI cards (students, events, predicted attendees, predicted no-shows, **CV ROC-AUC as the headline** with the holdout shown as reference, holdout accuracy vs baseline); **Data sources** panel (original / synthetic / entered in app; simulated vs recorded outcomes; upcoming events); **Recent activity** from stored predictions, evaluations and audited changes; recent predictions with coloured outcomes |
| Dataset Explorer | provenance badge (original · simulated outcomes / synthetic demo / imported file), missing cells tinted gold in the preview, quality KPIs coloured by result |
| Event Management | status and source colour-coded in the list; summary badges for the selected event (status, source, registrations, recorded outcome and whether it is simulated) |
| Attendance Prediction | **model selection** (active model by default, any registered version); two-step layout; plain-language lists of inputs that *raised* / *lowered* the probability (what-if comparison with typical training values, not causes); expected attendees **and expected no-shows** with approximate ranges; recorded attendance for the event shown separately from the prediction and labelled simulated or recorded |
| Model Laboratory | active model row highlighted; every metric has a short explanation (tooltips and a "How to read these metrics" section); cross-validation presented as the primary estimate |
| Analytics & Insights | data-source filter; model performance comparison chart (CV vs holdout ROC-AUC for every version) |
| Reports & Export | report presets (Full / Model performance / Events & attendance / Dataset & participation / Custom); live summary of the figures the report will contain; event and date filters with a row preview for table exports; Open report / Show in Finder after generation; reports carry the new branding, the version, and separate rows for simulated and recorded outcomes |
| Settings | Application information · Model configuration · Dataset configuration with **Demo Mode** · Database & backup · Data privacy · Appearance · About |
| Demo Mode | opens a **separate** database (`database/eventpulse_demo.db`) built only from the labelled synthetic dataset; a gold banner, window-title suffix and sidebar badge stay visible; your database is never written with demo rows; restores are disabled in Demo Mode |
| Packaging | version 1.1.0 in the bundle; self-test of every page at 1100, 1280 and 1920 px; disk image `release/EventPulse AI 1.1.0.dmg`; matplotlib font cache kept in the project so launches don't rebuild it |

**Reports — what is not filtered:** the PDF and Excel *reports* always describe all stored data.
Event/date filters apply to table exports, where the preview shows exactly what will be written.
Filtering a full report would need every statistic recomputed per subset; that was not done, so no
report can appear to summarise everything while only covering part of the data.
**No embedded PDF preview:** it would need Qt's PDF module in the bundle. "Open report" opens it in Preview instead.

## 3. Defects found on the Mac and fixed

| Found | Fix |
|---|---|
| Wrapped values overlapped the next row in two-column cards (Qt kept a stale height cache in nested layouts) | Responsive rows, key/value lists and card headers rebuilt from row widgets; labels reserve their wrapped height; new test checks 1100/1280/1440 px for clipping *and* overlap |
| Table colours (active model row, missing-value tint) ignored | removed a style-sheet rule that made Qt ignore per-row colours |
| Sidebar items activated through accessibility (VoiceOver, automation) highlighted but did not change page — also present in v1.0 | navigation follows the button's toggled state; new test |
| Form fields stayed at minimum width on macOS | forms let fields use the available width |
| "Columns & types" / "Clean & export" tabs elided; "69% atten" clipped | escaped `&` in tab titles; shorter KPI value |
| Rotated chart labels clipped with the Mac system font | few long categories are wrapped onto two lines instead |
| Packaged app rebuilt matplotlib's font cache (~20 s) on every launch, before any window appeared | cache kept in `.cache/matplotlib`; start-up screen shown before heavy imports. Measured second launch: ~4 s |
| Build script lost its executable permission after being updated | restored (`chmod +x`) |

## 4. Tests

Final run on the Mac (`EventPulse_Verify.command`, 12 Nov 2026 14:01): **152 passed, 0 failed**
(1 harmless matplotlib warning about a chart that was momentarily too small to lay out).

* 136 non-GUI tests: the 127 existing ones (data, validation, preprocessing, synthetic data,
  training, prediction, database schema/repositories/services/backup, analytics, reports) plus 9
  new: Demo Mode separation and refusals, data-source summary, recent activity, source filter,
  **persistence of events, registrations, predictions, model versions, actual attendance and
  comparisons after reopening the database**, reports labelling simulated vs recorded outcomes,
  brand assets.
* 16 GUI tests (Qt off-screen): the 10 existing ones plus Demo Mode on/off, prediction with a
  selected model, report presets and export filters, icons/logo/About, KPI reflow at 1100 vs 1920 px,
  and accessibility navigation.

`EventPulse_Build_App.command` (14:01): build OK; packaged self-test **24/24 page checks OK**
(8 pages × 1100, 1280, 1920 px; no horizontal overflow, no error notices); DMG built and verified.

## 5. Checks in the running app

Launched by double-clicking `EventPulse AI.app` in Finder (no Terminal). Window title
"EventPulse AI — Predict. Plan. Participate.", new icon in Finder and the Dock.

| Check | Result |
|---|---|
| All 8 pages open and render | yes (screenshots below) |
| Event creation and editing | created EV37, edited its venue, both saved; deleted again afterwards |
| Dataset loading | processed dataset: 383 rows, validation passed |
| Prediction workflow | STU001 at EV09 → 87.2 %, "Will attend", explanation lists shown |
| Model Laboratory | 5 versions, active row highlighted |
| PDF + Excel export | both generated; Excel summary matched the database exactly (279 students, 37 events at the time, 383 registrations, 383 simulated / 0 recorded outcomes); PDF shows version and both outcome rows |
| Demo Mode | on: 600 synthetic students / 48 events, banner and badge visible; off: back to 279 / 36 |
| Backup / restore / integrity | backup written and verified; restore made a pre-restore safety copy; integrity "ok" |
| Persistence after restart | EV37 (with its edit) and the new prediction were present after quitting and relaunching |
| Stale versions | the old Python-launched copy is no longer running; older instances were quit before each test |

## 6. Screenshots

* `docs/screenshots/v1.1.0/live/` — captured from the running packaged app on the Mac
  (all 8 pages, prediction explanation, backup/restore, report generated, Demo Mode, About, icon).
* `docs/screenshots/v1.1.0/selftest/` — window captures taken by the packaged app itself at
  1100, 1280 and 1920 px (24 images) plus `selftest.txt`.
* `docs/screenshots/v1.1.0/rendered/` — off-screen renders made by the test script.

## 7. Limitations

* **Not signed or notarised** (needs a paid Apple Developer ID). macOS may ask you to confirm the
  first launch (System Settings → Privacy & Security → Open Anyway). The first launch after each
  rebuild takes ~15–20 s while macOS checks the 227 MB bundle; later launches take ~4 s.
* The app and the DMG read data, models and the database from this `EventPulseAI` folder on this
  Mac. The DMG is a convenient copy for this Mac, not an installer for other computers.
* Attendance outcomes in the original dataset are simulated; Demo Mode outcomes are synthetic.
  Model metrics describe how well the models recover the simulation.
* Demo Mode uses the same trained models (trained on the processed original dataset).
* GUI tests run with Qt's off-screen platform; macOS-specific layout was checked in the live app.
* Activity created while testing remains in your history by design (prediction history is
  append-only): one individual prediction for STU001 at EV09, two backups (`manual`,
  `pre_restore`), two reports in `reports/`, and the demo database.
* Leftovers you can delete yourself: `_to_delete/` (a 20-byte probe file) and the empty `build/`.
  Deleting them from here would require delete permission for the whole `Aiml` folder, which also
  holds your original reference files, so I left them.

## 8. Files

Created: `gui/icons.py`, `gui/branding.py`, `scripts/render_brand.py`, `tests/test_v11_features.py`,
`assets/logo_mark.png`, `docs/V1_1_0_SUMMARY.md`, `docs/screenshots/v1.1.0/`,
`release/EventPulse AI 1.1.0.dmg`, `.cache/matplotlib/` (runtime cache).

Modified: `config/settings.py`, `core/analytics.py`, `core/charts.py`, `core/reports.py`,
`core/db/services.py`, `gui/app.py`, `gui/context.py`, `gui/main_window.py`, `gui/theme.py`,
`gui/widgets.py`, `gui/dialogs.py`, `gui/pages/` (all 9 files), `tests/test_gui.py`,
`scripts/capture_screenshots.py`, `packaging/eventpulse.spec`, `EventPulse_Build_App.command`,
`README.md`, `assets/icon.png`, `assets/AppIcon.icns`.

Unchanged: model training, evaluation, prediction and the database schema; original reference
files in the parent `Aiml` folder.

**App:** `/Users/nikhilyadav/Downloads/Aiml /EventPulseAI/EventPulse AI.app`
**DMG:** `/Users/nikhilyadav/Downloads/Aiml /EventPulseAI/release/EventPulse AI 1.1.0.dmg`
