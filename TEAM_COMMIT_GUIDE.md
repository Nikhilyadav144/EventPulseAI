# TEAM COMMIT GUIDE – adding this package to the EventPulseAI repository

The commands are for macOS or Linux (Terminal). On Windows, use Git Bash.
Replace `~/EventPulseAI` with the folder where your copy of the repository is.

## 1. Extract the ZIP

```bash
cd ~/Downloads
unzip EventPulseAI_Team_Sharing_Package.zip
ls EventPulseAI_Team_Sharing_Package
# README.md  CHANGES.md  FILE_MANIFEST.md  TEAM_COMMIT_GUIDE.md  repo_files  reference_only  team_materials
```

Read `README.md` and `CHANGES.md` first.

## 2. Open the repository and check the branch

```bash
cd ~/EventPulseAI
git status              # must say "working tree clean"; commit or stash your own work first
git branch --show-current
git remote -v           # should be github.com/Nikhilyadav144/EventPulseAI
git switch main
git pull origin main
git log --oneline -1    # expect 4d0f65b, or something newer if the team has pushed since
```

## 3. Create a branch for this work (do not commit to `main` directly)

```bash
git switch -c team/add-tests-docs-desktop
```

## 4. Check for collisions before copying

All files in `repo_files/` should be **new** to the repository. This loop lists any that already exist:

```bash
PKG=~/Downloads/EventPulseAI_Team_Sharing_Package/repo_files
( cd "$PKG" && find . -type f ) | while read -r f; do
  [ -e "$f" ] && echo "ALREADY EXISTS: $f"
done
```

- **Nothing printed:** safe to copy.
- **Something printed:** compare before deciding, and do not overwrite a file you did not expect to change:

```bash
git diff --no-index -- tests/conftest.py "$PKG/tests/conftest.py"
```

## 5. Copy the files to their destinations

`-n` means "never overwrite an existing file":

```bash
cp -R -n "$PKG"/. .
git status              # should list only new (untracked) files from FILE_MANIFEST.md
```

Do **not** copy `team_materials/` or `reference_only/` into the repository.

## 6. Run the tests

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-web.txt pytest httpx      # add PySide6 for the desktop app and GUI tests
python -m pytest tests -q \
  --ignore=tests/test_app.py \
  --deselect tests/test_auth.py::test_migration_is_additive_backed_up_and_reversible \
  --deselect tests/test_db_configuration.py::test_requirements_web_is_pinned_and_consistent
```

Expected on a repository-only checkout without PostgreSQL or PySide6:

```
154 passed, 177 skipped, 2 deselected
```

- **Skips** are normal: no private data, no local database, no PostgreSQL. If you have PostgreSQL, set `EVENTPULSE_TEST_PG_URL` to a **local test** database (never the Render one) to run more tests.
- **Excluded tests:** the three excluded items need files that are deliberately not in the repository (`CHANGES.md`, section 1).

Optional, desktop app (needs `pip install PySide6`):

```bash
python scripts/seed_demo_database.py --sqlite /tmp/eventpulse_demo.db   # synthetic-only demo database
python main.py
```

## 7. Review exactly what will be committed

```bash
git status
git diff --stat           # should be empty – no existing file changed
git status --porcelain    # every line should start with "??" (new file)
```

## 8. Stage only the intended files, by name

Never use `git add .` or `git add -A`.

```bash
git add tests/
git add docs/API_DOCUMENTATION.md docs/AUDIT_V1_2.md docs/CHECKIN.md docs/CODEX_PROJECT_AUDIT.md \
        docs/DATABASE.md docs/DATA_CARD.md docs/DEPLOYMENT_READINESS_AUDIT.md docs/DESKTOP_APP.md \
        docs/FREE_DEPLOYMENT_READINESS.md docs/ML_PIPELINE.md docs/PHASE1_AUDIT.md \
        docs/PHASE2_DRIVER_VERIFICATION.md docs/PHASE2_SUMMARY.md docs/PHASE3_SUMMARY.md \
        docs/PHASE4_7_SUMMARY.md docs/PHASE4_DEPLOYMENT_REPORT.md docs/POSTGRESQL_COMPATIBILITY_REPORT.md \
        docs/PRODUCTION_MIGRATION_PLAN.md docs/V1_1_0_SUMMARY.md docs/VERCEL_SUPABASE_MIGRATION_PLAN.md \
        docs/WEB_APP.md docs/WEB_ARCHITECTURE.md docs/er_diagram.mmd docs/er_diagram.png
git add gui/ main.py assets/AppIcon.icns assets/icon.png
git add scripts/capture_screenshots.py scripts/generate_synthetic.py scripts/manage_db.py \
        scripts/prepare_data.py scripts/render_brand.py scripts/render_er_diagram.py \
        scripts/seed_demo_database.py scripts/smoke_api.py scripts/train_models.py
git status                # check the staged list against FILE_MANIFEST.md
```

Make sure **no** `__pycache__/`, `.venv/`, `*.db`, `.env`, `data/original/` or `models/` path is staged. `git diff --cached --name-only` shows the staged list.

If the team only wants part of the package (for example, only the tests and docs), stage only those lines.

## 9. Commit with a meaningful message

```bash
git commit -m "Add test suite, project docs, desktop app source and helper scripts

- tests/: full automated suite (154 pass on a repo-only checkout; 3 items need private/CSL236 files)
- docs/: Phase 1-4 reports, API, database, ML pipeline, check-in and architecture notes
- gui/, main.py, assets/: offline PySide6 desktop app (unchanged local version)
- scripts/: data, training, screenshot and demo-database helpers
No web app, migration, configuration or frontend build changes."
```

## 10. Push the branch and open a pull request

```bash
git push origin team/add-tests-docs-desktop
```

Then on GitHub: **Pull requests → New pull request → base: `main`, compare: `team/add-tests-docs-desktop`**. Ask Nikhil to review.

Merging into `main` triggers an automatic Render redeploy of the live site. Merge at an agreed time, not just before a presentation.

If the team decides to push directly to an agreed branch instead:

```bash
git push origin <branch-name>
```

## If something goes wrong (before pushing)

```bash
git restore --staged <file>     # unstage one file
git switch main                 # go back; your branch stays until you delete it
```

Never run `git push --force` on `main`.
