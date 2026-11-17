"""Database administration.

    python scripts/manage_db.py init              # create/migrate DB, import processed dataset, register models
    python scripts/manage_db.py status            # schema version, row counts, active model, integrity
    python scripts/manage_db.py backup [label]    # verified backup into database/backups/
    python scripts/manage_db.py list-backups
    python scripts/manage_db.py restore <file>    # validated restore (safety backup taken first)
    python scripts/manage_db.py sync-models       # register newly trained models as versions
"""
import json
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore", category=UserWarning)

from core.db import services  # noqa: E402
from core.db.backup import backup_database, list_backups, restore_database  # noqa: E402
from core.db.connection import DEFAULT_DB_PATH, Database  # noqa: E402
from core.db.errors import DatabaseError  # noqa: E402
from core.db.migrations import current_version  # noqa: E402
from core.db.repositories import ModelVersionRepository  # noqa: E402


def status(db: Database) -> None:
    counts = {t: db.scalar(f"SELECT COUNT(*) FROM {t}") for t in (
        "students", "events", "registrations", "weather_forecasts", "model_versions", "predictions",
        "attendance_records", "event_outcomes", "prediction_evaluations")}
    active = ModelVersionRepository(db).active()
    print(f"Database: {db.path}")
    print(f"Schema version: {current_version(db)}")
    print("Rows:", json.dumps(counts))
    print("Active model:", f"{active['name']} v{active['version']}" if active else "none")
    problems = db.integrity_check()
    print("Integrity:", "ok" if not problems else problems)


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    try:
        with Database(DEFAULT_DB_PATH) as db:
            if cmd == "init":
                r = services.bootstrap(db)
                imp = r["import"]
                print("Already imported." if imp["already_imported"] else
                      f"Imported {imp['students']} students, {imp['events']} events, "
                      f"{imp['registrations']} registrations (simulated outcomes labelled).")
                print(f"Model versions registered: {r['model_versions']}")
                status(db)
            elif cmd == "status":
                status(db)
            elif cmd == "backup":
                b = backup_database(db, args[0] if args else "manual")
                print(f"Backup written and verified: {b.path} ({b.size_bytes:,} bytes)")
            elif cmd == "list-backups":
                for b in list_backups():
                    print(f"{b.created_utc}  v{b.schema_version}  {b.label:20s} {b.path.name}")
            elif cmd == "restore":
                if not args:
                    sys.exit("Usage: manage_db.py restore <backup file>")
                safety = restore_database(db, Path(args[0]))
                print(f"Restored from {args[0]}. Previous state saved as {safety.path.name}.")
                status(db)
            elif cmd == "sync-models":
                print(services.sync_models(db))
            else:
                sys.exit(__doc__)
    except DatabaseError as exc:
        sys.exit(f"Error: {exc}")


if __name__ == "__main__":
    main()
