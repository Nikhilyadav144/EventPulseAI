# CHANGES – what this package adds to the repository

## Baseline

- Repository: `github.com/Nikhilyadav144/EventPulseAI` (private), branch `main`.
- Baseline commit: `4d0f65b` – "EventPulse AI web app: hosted deployment files (Phase 4)". The live Render site was deployed from this commit.
- Nikhil's local `main` = `origin/main` = `4d0f65b`. There are **no modified, deleted or renamed tracked files**.
- Since that commit, only one local file has changed: `docs/PHASE4_DEPLOYMENT_REPORT.md`, which was never on GitHub and is included here.

So this package **adds new files only**. It does not change, replace or delete any file already in the repository.

## What is not changing

| Area | Status |
|---|---|
| Web app (`webapp/`, `core/`, `web/src/`, `web/dist/`) | Unchanged. Already on GitHub |
| Database migrations (`core/db/sql_migrations/`, `webapp/auth_migrations/`) | **None added. No migration to run** |
| Configuration (`config/settings.py`, `requirements-web.txt`, `.env.example`, `.gitignore`) | Unchanged |
| Frontend build (`web/dist/`) | Unchanged – **no rebuild needed** |
| Render settings and environment variables | Unchanged – nothing to deploy |
| Google Form import feature | **Not implemented.** It was planned but never approved or built, and none of it is in this package |

## New files, by group

### 1. Test suite – `tests/` (27 files)

The project's automated tests: core ML pipeline, preprocessing, validation, database (SQLite and PostgreSQL), web API, authentication, security hardening (Phase 2), database persistence (Phase 3), QR check-in, workflow and GUI.

**Test status.** These were run on 17 Nov 2026 in Claude's cloud workspace, using a copy whose test, core, webapp and GUI files match Nikhil's laptop byte for byte (SHA-256). They were not run on the laptop itself.

| Environment | Result |
|---|---|
| Full local project (with the local database, models and data files that are not in the repository) | **309 passed, 52 skipped, 0 failed** |
| Repository-only checkout = GitHub files + this package (what a team member will have), with the recommended exclusions below | **154 passed, 177 skipped, 2 deselected, 0 failed** |

Skips are expected: no local PostgreSQL server, no PySide6, and no private data or database.

**Important: on a repository-only checkout, three things fail because their files are deliberately not in the repository.**
- `tests/test_app.py` (27 errors): tests the CSL236 `app.py` and needs `model/metadata.json` (CSL236 model folder, not on GitHub).
- `tests/test_db_configuration.py::test_requirements_web_is_pinned_and_consistent`: reads `requirements-serve.txt` (CSL236, not on GitHub).
- `tests/test_auth.py::test_migration_is_additive_backed_up_and_reversible`: needs the private desktop database `database/eventpulse.db`.

Run the suite with these excluded; the command is in `TEAM_COMMIT_GUIDE.md`. Do **not** "fix" these tests by adding the private database or model files to the repository.

**Privacy check.** The tests contain only fake identities:
- e-mails such as `student.a@ncuindia.edu` and `admin@eventpulse.test`
- fake roll-number-like strings (`21CSU123`, `26CSU210`, `24bap043`), used to prove that roll numbers are rejected
- fake passwords and `postgresql://` URLs pointing at local test servers

No real student data, no real credentials.

### 2. Documentation – `docs/` (22 Markdown files + `er_diagram.mmd` + `er_diagram.png`)

Phase 1–4 audit and summary reports, the Phase 4 deployment report, API documentation, the database, ML pipeline, check-in and web architecture notes, the desktop app guide, the data card, the PostgreSQL compatibility report and migration plans.

- Not code. No tests apply. No secrets or real student data: only demo or test e-mails such as `demo.student@ncuindia.edu`. Checked by a scan for e-mails, passwords, database URLs and roll numbers.
- Four documents (`DESKTOP_APP.md`, `PHASE4_DEPLOYMENT_REPORT.md`, `PHASE4_7_SUMMARY.md`, `V1_1_0_SUMMARY.md`) link to images in `docs/screenshots/`, which is not included. Those image links will show as broken on GitHub; the text is complete.
- Some reports describe dates, live checks and states as of 17 Nov 2026 (for example "free database expires about 16 Dec 2026").

### 3. Desktop application – `gui/` (26 files), `main.py`, `assets/AppIcon.icns`, `assets/icon.png`

The offline PySide6 desktop app (14 pages). It uses the same `core/` package as the web app, but a separate local SQLite database (`database/eventpulse.db`), which is **not** shared.

- **Status:** this is the existing, working local version; it was not changed for this package. The GUI tests (`tests/test_gui.py`) were **not run** in this session because PySide6 is not installed there. Treat the desktop app as "existing feature, not re-verified today".
- **Running it on a fresh clone:**
  - A fresh clone has no `database/eventpulse.db`. How the desktop app behaves without one was **not checked** in this session.
  - For demo data, `scripts/seed_demo_database.py --sqlite <new-file.db>` creates a separate synthetic-only database. It refuses to touch a real database.
  - The pages that use the mid-term dataset need `data/processed/`, which is private and not shared.
- **Dependencies:** `requirements-desktop.txt` (in `reference_only/`) begins with `-r requirements.txt`, a CSL236 file that is not in the repository. Do not commit it as it is. Install with the command in `TEAM_COMMIT_GUIDE.md`, step 6:
  `pip install -r requirements-web.txt pytest httpx PySide6`

### 4. Helper scripts – `scripts/` (9 files)

`capture_screenshots.py`, `generate_synthetic.py`, `manage_db.py`, `prepare_data.py`, `render_brand.py`, `render_er_diagram.py`, `seed_demo_database.py`, `smoke_api.py`, `train_models.py`.

- Existing local tools, unchanged; there are no dedicated tests for them.
- `prepare_data.py` reads the private `data/original/` folder. The code is safe to share; the data is not shared and must never be added.
- None of these is run by Render.

## Not for the repository

- `reference_only/requirements-desktop.txt` – read the desktop section above.
- `team_materials/` – the presentation (PPTX and PDF), master guide (ODT and PDF), cheat sheet and demo files: registration import files, a bulk prediction file and a split-evidence script with its outputs. Use these for the presentation. Do not commit them.

## Risks and notes

1. **Auto-deploy:** merging into `main` makes Render rebuild the live site. The new files are not used by the web build (`requirements-web.txt` and `webapp/` are unchanged), so behaviour should not change. Merge at an agreed time, not just before a demo.
2. **Repository visibility:** the repository is private. Keep it private; the docs describe the deployment in detail.
3. **No migrations:** nothing in this package touches the live PostgreSQL database.
