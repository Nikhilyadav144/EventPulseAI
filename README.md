# EventPulse AI – Team Sharing Package

Prepared on 17 November 2026 from Nikhil's local project (`EventPulseAI/`), for Team 8.

## Read this first

**There are no new web-app code changes in this package.** Nikhil's local branch `main` is exactly at commit `4d0f65b`, which is already on GitHub (`Nikhilyadav144/EventPulseAI`, branch `main`). No file that GitHub tracks has been changed since that push. That means the live Render site, `core/`, `webapp/` and `web/` are already identical on GitHub.

This package contains the project files that exist **only on Nikhil's laptop**. They were deliberately left out of GitHub in Phase 4, and the team has now chosen to share them:

| Folder in this package | What it is | Goes into the repository? |
|---|---|---|
| `repo_files/tests/` | The full automated test suite (27 files) | Yes (optional, see `CHANGES.md`) |
| `repo_files/docs/` | Project reports and technical notes (22 Markdown files, ER diagram) | Yes |
| `repo_files/gui/`, `repo_files/main.py`, `repo_files/assets/` | The offline desktop app (PySide6) | Yes, if the team wants the desktop app on GitHub |
| `repo_files/scripts/` | 9 helper scripts (data, training, screenshots, demo database) | Yes |
| `reference_only/requirements-desktop.txt` | Desktop dependency list | **No** – it points to `requirements.txt`, which is not in the repository. Read `CHANGES.md` |
| `team_materials/` | Presentation, master guide, cheat sheet, demo files | **No** – for presenting only, never commit |

Everything under `repo_files/` is laid out with the **same paths as in the repository**: `repo_files/tests/conftest.py` goes to `tests/conftest.py`, and so on. `FILE_MANIFEST.md` lists every file and its destination.

## What is NOT in this package (on purpose)

- `.env`, API keys, passwords, database URLs, session secrets, Render settings.
- Any database (`database/`, `*.db`, `mlflow.db`) or database dump.
- Real data: `data/original/` (student survey), `data/processed/`, Google Form responses.
- Trained models (`models/`, `model/`), MLflow runs (`mlruns/`).
- `.venv`, `node_modules`, `__pycache__`, `.pytest_cache`, `.DS_Store`, logs, exports, reports, build output (`build/`, `release/`, `*.app`).
- `.git`.
- `docs/screenshots/` (26 MB of images; some show the desktop app with project data).
- CSL236 course files kept off GitHub: `app.py`'s `model/`, `train.py`, `Dockerfile`, `render.yaml`, `.github/`, `requirements.txt`, `requirements-serve.txt`, `drift_check.py`.
- `README.md` from the project root.
- Mac-only launchers (`EventPulse_*.command`, `packaging/`, which contain machine-specific paths).

## Steps for team members (summary)

1. Download and extract `EventPulseAI_Team_Sharing_Package.zip`.
2. Open your existing local copy of the EventPulseAI repository.
3. Check that you are on the agreed branch and up to date (`git status`, `git pull`).
4. Create a new branch for this work. Do not commit straight to `main`; see the warning below.
5. Copy the files from `repo_files/` to the same paths in the repository.
6. Before copying, check whether any destination file already exists. If one does, compare first with `git diff --no-index`.
7. Never overwrite unrelated files, and never copy `team_materials/` or `reference_only/` into the repository.
8. Run the tests (`TEAM_COMMIT_GUIDE.md`, step 6).
9. Stage only the intended files, by name.
10. Commit with a clear message, push your branch, and open a pull request.

`TEAM_COMMIT_GUIDE.md` has the exact commands.

> **Warning:** Render redeploys the live site automatically whenever `main` changes. These files are not used by the web build, so the site should behave the same. A merge to `main` still triggers a rebuild, and the free service is briefly unavailable while it rebuilds. Push to a branch, open a pull request, and agree with Nikhil when to merge.

## Files in this package

- `README.md` – this file
- `CHANGES.md` – what each group of files is, its test status, and known limitations
- `FILE_MANIFEST.md` – every file, its size, SHA-256 and destination path
- `TEAM_COMMIT_GUIDE.md` – step-by-step Git instructions
