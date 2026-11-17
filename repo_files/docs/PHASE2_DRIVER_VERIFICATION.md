# EventPulse AI — Phase 2: Real PostgreSQL Driver Verification

*Date: 16 Nov 2026. Scope: verify the production driver (psycopg 3) only. Nothing was deployed or
uploaded, and no Supabase or other managed database was used. No existing database was migrated.
The production SQLite database, accounts, models, backups and package lists were not changed.*

## Result

**The real psycopg driver works.** With psycopg 3.3.9 against a throw-away PostgreSQL 16.15
database, every test that ran passed. The results are identical to the Phase 1 run with the
test-only libpq driver, and nothing failed.

| Run | Driver | Result |
|---|---|---|
| `tests/test_pg_compat.py` (SQLite vs PostgreSQL parity) | Phase 1 libpq shim | 36 passed |
| same | **psycopg 3.3.9** | **37 passed** (36 + a new check that psycopg really was used) |
| Whole suite, `EVENTPULSE_TEST_ENGINE=postgresql` | Phase 1 libpq shim | 189 passed, 54 skipped, 0 failed |
| same | **psycopg 3.3.9** | **190 passed, 54 skipped, 0 failed** |
| Whole suite, SQLite (container, system Python, no psycopg) | — | 243 passed, 1 skipped |
| **Mac desktop/SQLite suite** (`EventPulse_Web_Verify.command`, project `.venv`, no psycopg) | — | **238 passed, 22 skipped, 0 failed** |

The new test asserts that the adapter's connection is psycopg's (`_PsycopgRaw`) and prints the
driver: `DRIVER psycopg 3.3.9 (binary, libpq 180006), server 160015`. Setting
`EVENTPULSE_TEST_REQUIRE_PSYCOPG=1` makes the run **fail** instead of falling back to the shim.

### Per area, PostgreSQL mode with psycopg

| Area | Tests | Result |
|---|---|---|
| Authentication, sessions, CSRF, admin/student permissions, data isolation, rate limits, QR check-in (`test_auth.py`) | 19 | 18 passed, 1 skipped |
| Web API end to end (`test_webapp.py`) | 8 | 8 passed |
| Schema initialisation, migrations, transactions, constraints (`test_db_schema.py`) | 12 | 9 passed, 3 skipped |
| Repositories, CRUD and constraint errors (`test_db_repositories.py`) | 21 | 20 passed, 1 skipped |
| Import, predictions and attendance services (`test_db_services.py`) | 16 | 4 passed, 12 skipped |
| Parity, transactions, query compatibility, reports, seeder (`test_pg_compat.py`) | 37 | 37 passed |

**Skips, exactly as in Phase 1:**
* **44** tests load the survey-derived dataset (`seeded_db`). They are deliberately skipped on
  PostgreSQL so that only synthetic data reaches it; the same behaviour is covered on synthetic
  data by `test_pg_compat.py`.
* **9** check SQLite internals (PRAGMA, file backups, the SQLite auth-migration backup and
  rollback).
* **1** is the desktop GUI test (PySide6 isn't installed in the container; it ran on the Mac).

On the Mac, the 22 skips are the PostgreSQL tests: the Mac has no PostgreSQL server, by design.

**Failures: none.** No code change was needed for psycopg.

**Observation:** the parity file took 126 s with psycopg against 55 s with the shim. Most of it
is one test (the demo seeder trains models twice: 69 s). Connection set-up per test database
accounts for the rest. Query performance on a managed server has not been measured.

## How it was done (exact commands)

1. **Download only, on the Mac.** The container and the Mac's sandbox cannot reach the package
   index under this account's network policy.
   `Aiml /EventPulseAI_phase2_pgdriver/download_psycopg_wheels.command` ran:

   ```
   python3.12 -m pip download --only-binary=:all: --platform manylinux_2_28_x86_64 \
     --platform manylinux_2_17_x86_64 --platform manylinux2014_x86_64 --python-version 3.13 \
     --implementation cp --abi cp313 --abi abi3 --abi none -d wheels "psycopg[binary]"
   ```

   This saved `psycopg-3.3.9`, `psycopg_binary-3.3.9` (cp313, manylinux x86_64) and
   `typing_extensions-4.16.0`. SHA-256 sums are in `wheels.sha256`. Nothing was installed on the
   Mac.
2. **Separate environment in the test workspace.** The checksums were verified, then:

   ```
   sha256sum -c wheels.sha256
   python3 -m venv --system-site-packages /home/claude/phase2/pgdriver-venv
   pgdriver-venv/bin/python -m pip install --no-index --find-links wheels "psycopg[binary]==3.3.9"
   ```

   The system Python still has no psycopg (checked).
3. **Throw-away PostgreSQL 16.15 cluster.** `initdb` in `/tmp/pgtest2`, trust auth, Unix socket
   only, no TCP port. Each test creates its own `ep_test_<random>` database and drops it.
4. **Runs:**

   ```
   EVENTPULSE_TEST_PG_URL="postgresql://eptest@/postgres?host=/tmp/pgtest2&port=55432" \
   EVENTPULSE_TEST_REQUIRE_PSYCOPG=1 pgdriver-venv/bin/python -m pytest tests/test_pg_compat.py
   ... EVENTPULSE_TEST_ENGINE=postgresql pgdriver-venv/bin/python -m pytest -rs
   ```

5. **Mac:** `EventPulse_Web_Verify.command`, the established procedure, using the existing
   `.venv` unchanged.

The checksum of the container's working-copy database was checked before and after every run. The
Mac database checksum was the same before and after: `255e593b…`.

## Files

* **New:** `docs/PHASE2_DRIVER_VERIFICATION.md`, and the folder
  `Aiml /EventPulseAI_phase2_pgdriver/` (outside the project). It holds the download script, the
  wheels, the log, `sync/` and the pre-Phase-2 copies of the two changed test files.
* **Changed (test support only):**
  * `tests/pg_support.py`: the `EVENTPULSE_TEST_REQUIRE_PSYCOPG` switch.
  * `tests/test_pg_compat.py`: `test_driver_in_use_is_reported`.
* **Not changed:** application code, `requirements*.txt`, the desktop `.venv`, the database,
  models and backups.

## Still not verified (unchanged from Phase 1)

* A managed PostgreSQL (Supabase): SSL/`sslmode=require`, the transaction pooler on port 6543,
  collation, and role permissions.
* Concurrency under load.
* Adding `psycopg[binary]` to `requirements-serve.txt`. It is not done; it needs your approval.
