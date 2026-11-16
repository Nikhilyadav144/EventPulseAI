"""Hosted (PostgreSQL) database bootstrap for the EventPulse AI web app (Phase 3).

Render's free web service has no shell, so the one-time set-up steps can run when the server
starts, controlled by environment variables. Everything here runs ONLY against PostgreSQL: the
desktop's SQLite database is never seeded, given models or given an administrator this way
(locally, use ``python -m webapp.provision_admin`` as before).

Steps (each is idempotent - safe on every start, including after Render's free tier spins up):

1. Migrations: the core schema (``schema_migrations``) is applied when ``Database`` connects and
   the web-auth tables (``web_auth_migrations``) by ``ensure_auth_schema`` - both before this runs.
2. Administrator: if ``EVENTPULSE_ADMIN_PASSWORD`` is set and NO administrator exists, creates the
   single administrator from ``EVENTPULSE_ADMIN_EMAIL`` / ``EVENTPULSE_ADMIN_NAME`` /
   ``EVENTPULSE_ADMIN_PASSWORD`` (a secret environment variable - never in source code, never
   logged). If an administrator already exists nothing is changed or reset; a warning asks you to
   delete the password variable. An invalid value makes startup FAIL (fail closed).
3. Optional demonstration data: ``EVENTPULSE_SEED_SYNTHETIC_DEMO=1`` imports the labelled
   SYNTHETIC dataset (every row must say Data_Source = SYNTHETIC; no real student data) - only into
   an EMPTY database (no student records), so it never mixes with real records. The database is
   then flagged as demo data, and the app shows "Demo data".
4. Optional models: ``EVENTPULSE_HOSTED_MODELS_DIR`` names a folder built at deploy time by
   ``python -m webapp.hosted_setup build-models`` (models trained on the synthetic dataset only).
   Its models are registered as versions (by artifact hash: re-registering is a no-op).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Mapping, Optional

import pandas as pd

from core.db import services
from core.db.connection import Database
from core.db.repositories import SettingsRepository

log = logging.getLogger("eventpulse.bootstrap")

ADMIN_EMAIL, ADMIN_NAME, ADMIN_PASSWORD = "EVENTPULSE_ADMIN_EMAIL", "EVENTPULSE_ADMIN_NAME", "EVENTPULSE_ADMIN_PASSWORD"
SEED_FLAG = "EVENTPULSE_SEED_SYNTHETIC_DEMO"
MODELS_DIR_VAR = "EVENTPULSE_HOSTED_MODELS_DIR"


class BootstrapError(RuntimeError):
    """A bootstrap setting is invalid; the server refuses to start."""


def _flag(env: Mapping[str, str], name: str) -> bool:
    v = (env.get(name) or "").strip().lower()
    if v in ("", "0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    raise BootstrapError(f"{name} must be 1 or 0.")


def bootstrap_admin(db: Database, env: Mapping[str, str]) -> str:
    """'created', 'exists' or 'not-configured'. Never resets an existing administrator."""
    from webapp.auth import admin_exists, create_admin
    password = env.get(ADMIN_PASSWORD) or ""
    email = (env.get(ADMIN_EMAIL) or "").strip()
    if not password:
        return "not-configured"
    if admin_exists(db):
        log.warning("An administrator already exists; %s was ignored. Delete that environment variable now.",
                    ADMIN_PASSWORD)
        return "exists"
    name = (env.get(ADMIN_NAME) or "EventPulse Administrator").strip()
    if not email:
        raise BootstrapError(f"{ADMIN_PASSWORD} is set but {ADMIN_EMAIL} is missing.")
    try:
        create_admin(db, email, name, password)
    except (ValueError, PermissionError) as exc:      # messages never contain the password
        raise BootstrapError(f"Administrator not created: {exc}") from None
    log.warning("Administrator account created from environment variables. Delete %s now; it is no longer used.",
                ADMIN_PASSWORD)
    return "created"


def seed_synthetic_demo(db: Database, csv_path: Optional[Path] = None) -> dict:
    """Import the labelled synthetic dataset into an EMPTY database (refuses anything else)."""
    from config import settings
    from core.data_loader import file_sha256
    csv_path = Path(csv_path or settings.SYNTHETIC_DATASET)
    if db.scalar("SELECT COUNT(*) FROM students"):
        return {"seeded": False, "reason": "the database already holds student records"}
    df = pd.read_csv(csv_path)
    if "Data_Source" not in df.columns or set(df["Data_Source"].dropna()) != {"SYNTHETIC"}:
        raise BootstrapError("Only the labelled synthetic dataset (every row Data_Source = SYNTHETIC) can be seeded.")
    summary = services.import_dataset(db, df, "SYNTHETIC", "Synthetic demonstration data (generated, not real students)",
                                      csv_path.name, file_sha256(csv_path))
    SettingsRepository(db).set(services.DEMO_FLAG, True)
    db.audit("create", "hosted_demo_seed", None, f"{summary.registrations} synthetic registrations")
    log.info("Seeded the synthetic demonstration dataset (%s registrations).", summary.registrations)
    return {"seeded": True, "registrations": summary.registrations, "dataset_id": summary.dataset_id}


def register_hosted_models(db: Database, models_dir: Path) -> dict:
    models_dir = Path(models_dir)
    if not (models_dir / "active_model.json").exists():
        raise BootstrapError(f"{MODELS_DIR_VAR} does not contain built models (no active_model.json). "
                             "Run: python -m webapp.hosted_setup build-models --out <dir>")
    for meta in models_dir.glob("*/metadata.json"):
        label = str((json.loads(meta.read_text()).get("training_data") or {}).get("label", ""))
        if "SYNTHETIC" not in label.upper():
            raise BootstrapError(f"{meta.parent.name} was not trained on the synthetic dataset; refusing to "
                                 "register it on the hosted database.")
    ds = db.scalar("SELECT dataset_id FROM datasets WHERE kind = 'SYNTHETIC' ORDER BY dataset_id LIMIT 1") \
        if db.table_exists("datasets") else None
    return services.sync_models(db, models_dir, dataset_id=ds)


def run_startup_bootstrap(db: Database, env: Optional[Mapping[str, str]] = None) -> dict:
    """Called by the web server at start-up after migrations. PostgreSQL only."""
    env = os.environ if env is None else env
    if db.is_sqlite:
        return {"skipped": "SQLite (desktop) database: start-up bootstrap only runs on PostgreSQL"}
    out = {"admin": bootstrap_admin(db, env)}
    if _flag(env, SEED_FLAG):
        out["seed"] = seed_synthetic_demo(db)
    models_dir = (env.get(MODELS_DIR_VAR) or "").strip()
    if models_dir:
        out["models"] = sorted(register_hosted_models(db, Path(models_dir)))
    return out
