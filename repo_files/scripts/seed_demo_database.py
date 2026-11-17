"""Create a DEMO database that holds only SYNTHETIC (fictional) data - for a portfolio deployment.

Nothing here is copied from the real database: students, events, registrations, attendance
outcomes and predictions are all generated (core/synthetic.py), labelled SYNTHETIC, and the
database is flagged as a demo database (the app shows its demo notice). The models used for the
demo predictions are trained here on the same synthetic data, into a separate folder, so the
project's own models/ folder is never touched. No login accounts are created.

Usage (examples):
    python scripts/seed_demo_database.py --sqlite /tmp/eventpulse_demo.db
    EVENTPULSE_DB_URL=postgresql://... python scripts/seed_demo_database.py --postgres --models-dir demo_models

Safety: refuses the real database file, refuses any database that already holds non-demo data,
and does nothing if the target is already a populated demo database.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402
from core import synthetic  # noqa: E402
from core.db import services  # noqa: E402
from core.db.connection import DEFAULT_DB_PATH, Database  # noqa: E402
from core.db.repositories import SettingsRepository  # noqa: E402

LABEL = "SYNTHETIC DEMO DATA - generated, fictional students and events (not real people)"


def seed(db: Database, models_dir: Path, students: int = 300, events: int = 24, seed_value: int = 2026,
         predict_events: int = 3, progress=print) -> dict:
    if services.is_demo_database(db) and db.scalar("SELECT COUNT(*) FROM students"):
        progress("Target is already a populated demo database - nothing changed.")
        return {"created": False}
    used = [t for t in ("students", "events", "registrations", "predictions") if db.scalar(f"SELECT COUNT(*) FROM {t}")]
    if used:
        raise SystemExit(f"Refusing: the target already holds data in {', '.join(used)} (not a demo database).")

    df = synthetic.generate(n_students=students, n_events=events, seed=seed_value).df
    assert set(df["Data_Source"]) == {"SYNTHETIC"} and df["Student_ID"].str.startswith("SYN-").all()
    progress(f"Generated {len(df)} synthetic registrations ({df.Student_ID.nunique()} students, "
             f"{df.Event_ID.nunique()} events).")

    from core.model_training import train_and_compare
    models_dir.mkdir(parents=True, exist_ok=True)
    train_and_compare(df, "SYNTHETIC demo data", fast=True, models_dir=models_dir, progress=lambda m: None)
    progress(f"Trained demo models on the synthetic data into {models_dir}.")

    summary = services.import_dataset(db, df, "SYNTHETIC", LABEL)
    ids = services.sync_models(db, models_dir, dataset_id=summary.dataset_id)
    preds = []
    for ev in [r["event_id"] for r in db.query("SELECT event_id FROM events ORDER BY event_id LIMIT ?",
                                                (predict_events,))]:
        preds.append(services.predict_event(db, ev)["registrations"])
    SettingsRepository(db).set(services.DEMO_FLAG, True)
    db.audit("create", "demo_database", None, f"{summary.registrations} synthetic registrations (seed script)")
    progress(f"Imported {summary.registrations} registrations with SIMULATED outcomes; "
             f"{sum(preds)} demo predictions stored for {len(preds)} events.")
    return {"created": True, "registrations": summary.registrations, "models": ids, "predictions": sum(preds)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--sqlite", help="path of a NEW SQLite demo database file")
    g.add_argument("--postgres", action="store_true", help="use the PostgreSQL database in EVENTPULSE_DB_URL")
    ap.add_argument("--models-dir", default=None, help="where the demo models are written (default: next to "
                                                       "the SQLite file, or ./demo_models)")
    ap.add_argument("--students", type=int, default=300)
    ap.add_argument("--events", type=int, default=24)
    ap.add_argument("--seed", type=int, default=2026)
    a = ap.parse_args(argv)

    if a.sqlite:
        path = Path(a.sqlite).expanduser().resolve()
        if path == DEFAULT_DB_PATH.resolve() or path.parent == settings.DATABASE_DIR.resolve():
            raise SystemExit("Refusing: the demo database must not be created in the app's database folder.")
        models = Path(a.models_dir) if a.models_dir else path.parent / (path.stem + "_models")
        target = Database(path)
    else:
        url = os.environ.get("EVENTPULSE_DB_URL", "").strip()
        if not url:
            raise SystemExit("Set EVENTPULSE_DB_URL to the (empty) PostgreSQL demo database first.")
        models = Path(a.models_dir or "demo_models")
        target = Database(url=url)
    if models.resolve() == settings.MODELS_DIR.resolve():
        raise SystemExit("Refusing: demo models must not be written into the project's models/ folder.")
    with target as db:
        print(f"Target: {db.display_name}")
        seed(db, models, a.students, a.events, a.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
