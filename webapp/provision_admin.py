"""One-time creation of THE EventPulse AI administrator account.

Usage (from the project folder):
    .venv/bin/python -m webapp.provision_admin                 # asks for e-mail, name and password
    EVENTPULSE_ADMIN_EMAIL=... EVENTPULSE_ADMIN_NAME=... EVENTPULSE_ADMIN_PASSWORD=... \
        .venv/bin/python -m webapp.provision_admin --from-env   # non-interactive
    .venv/bin/python -m webapp.provision_admin --status         # does an admin exist?
    .venv/bin/python -m webapp.provision_admin --rollback-auth  # remove the auth tables (backup first)

Rules: the password is never stored in plain text, printed, logged or put in source code. If an
administrator already exists nothing is created, changed or reset (exit code 3). Option --db
selects another database file (default: database/eventpulse.db or EVENTPULSE_DB).
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from core.db.connection import DEFAULT_DB_PATH, Database, web_database_url
from webapp.auth import admin_exists, create_admin, ensure_auth_schema, password_problems, rollback_auth_schema


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Create the single EventPulse AI administrator account.")
    ap.add_argument("--db", default=os.environ.get("EVENTPULSE_DB", str(DEFAULT_DB_PATH)))
    ap.add_argument("--db-url", default=web_database_url(),
                    help="PostgreSQL URL (cloud deployment); default: EVENTPULSE_DB_URL, else the SQLite --db file")
    ap.add_argument("--from-env", action="store_true",
                    help="read EVENTPULSE_ADMIN_EMAIL, EVENTPULSE_ADMIN_NAME, EVENTPULSE_ADMIN_PASSWORD")
    ap.add_argument("--status", action="store_true", help="only report whether an administrator exists")
    ap.add_argument("--rollback-auth", action="store_true",
                    help="drop the web-auth tables (accounts, sessions) after a backup; core data is untouched")
    a = ap.parse_args(argv)
    if a.db.startswith(("postgresql://", "postgres://")):        # a PostgreSQL URL given as --db
        a.db_url = a.db

    db_path = Path(a.db)
    if not a.db_url and not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2
    with Database(url=a.db_url) if a.db_url else Database(db_path) as db:
        if a.rollback_auth:
            if input("Type REMOVE ACCOUNTS to drop all web accounts and sessions: ").strip() != "REMOVE ACCOUNTS":
                print("Cancelled - nothing changed.")
                return 1
            rollback_auth_schema(db)
            print("Web-auth tables removed" + (" (a backup was written to database/backups first)." if db.is_sqlite else "."))
            return 0
        applied = ensure_auth_schema(db)
        if applied:
            print(f"Auth tables created (migration {applied})" + ("; a backup was written first." if db.is_sqlite else "."))
        if a.status:
            print("Administrator exists." if admin_exists(db) else "No administrator yet.")
            return 0
        if admin_exists(db):
            print("An administrator account already exists. Nothing was created or changed.")
            return 3

        if a.from_env:
            email = os.environ.get("EVENTPULSE_ADMIN_EMAIL", "")
            name = os.environ.get("EVENTPULSE_ADMIN_NAME", "Administrator")
            password = os.environ.get("EVENTPULSE_ADMIN_PASSWORD", "")
            if not email or not password:
                print("Set EVENTPULSE_ADMIN_EMAIL and EVENTPULSE_ADMIN_PASSWORD.", file=sys.stderr)
                return 2
        else:
            email = input("Administrator e-mail: ").strip()
            name = input("Administrator full name: ").strip() or "Administrator"
            while True:
                password = getpass.getpass("Password (min 10 characters, letters and numbers; not shown): ")
                problems = password_problems(password, email)
                if problems:
                    print("Password needs " + ", ".join(problems) + ".")
                    continue
                if getpass.getpass("Repeat password: ") != password:
                    print("The passwords do not match.")
                    continue
                break
        try:
            create_admin(db, email, name, password)
        except PermissionError as exc:
            print(str(exc))
            return 3
        except ValueError as exc:
            print(f"Not created: {exc}", file=sys.stderr)
            return 2
    print(f"Administrator account created for {email}. Sign in at http://127.0.0.1:8100/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
