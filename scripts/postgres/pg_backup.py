#!/usr/bin/env python3
"""Backup, verification and restore of the hosted EventPulse AI PostgreSQL database (Phase 3).

Uses the standard PostgreSQL client tools (pg_dump, pg_restore, psql - same major version as the
server or newer). Connection details are passed to them as PG* environment variables parsed from
the URL, so the password never appears on a command line or in the process list.

    # backup (URL from EVENTPULSE_BACKUP_DB_URL, else EVENTPULSE_DB_URL)
    python scripts/postgres/pg_backup.py backup  --out-dir ~/EventPulseBackups
    # check a backup file (checksum + readable archive + expected tables)
    python scripts/postgres/pg_backup.py verify  ~/EventPulseBackups/eventpulse_<stamp>.dump
    # restore into an EMPTY database (URL from EVENTPULSE_RESTORE_DB_URL), then compare row counts
    # and per-table content checksums with the manifest written at backup time
    python scripts/postgres/pg_backup.py restore ~/EventPulseBackups/eventpulse_<stamp>.dump

Each backup writes three files, readable only by you (mode 600, folder 700):
    eventpulse_<UTC stamp>.dump            pg_dump custom format (no owners / grants: restorable anywhere)
    eventpulse_<UTC stamp>.dump.sha256     checksum of the dump
    eventpulse_<UTC stamp>.manifest.json   row count and content checksum of every table at backup time

Backups contain account e-mails and password HASHES: keep them on your own encrypted disk, never
inside the project folder, never in Git, never uploaded anywhere. This tool never uploads anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TABLES = {"schema_migrations", "web_auth_migrations", "users", "auth_sessions", "auth_audit",
                   "students", "events", "registrations", "checkins", "attendance_records"}
COUNT_SQL = """
SELECT format('SELECT %L, %L, count(*), md5(coalesce(string_agg(t::text, %L ORDER BY t::text), %L)) FROM %I.%I t',
              table_schema, table_name, chr(30), '', table_schema, table_name)
FROM information_schema.tables
WHERE table_type = 'BASE TABLE' AND table_schema NOT IN ('pg_catalog', 'information_schema')
  AND table_schema NOT LIKE 'pg_toast%'
ORDER BY table_schema, table_name
\\gexec
"""


class BackupError(RuntimeError):
    pass


def pg_env(url: str) -> dict:
    """libpq environment for a postgresql:// URL (password kept out of argv)."""
    if not url.startswith(("postgresql://", "postgres://")):
        raise BackupError("A postgresql:// URL is required.")
    p = urlsplit(url)
    q = {k: v[-1] for k, v in parse_qs(p.query).items()}
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    env.update({"PGHOST": q.get("host") or p.hostname or "", "PGPORT": str(q.get("port") or p.port or 5432),
                "PGUSER": unquote(p.username or ""), "PGDATABASE": unquote(p.path.lstrip("/")),
                "PGAPPNAME": "eventpulse-backup", "PGCONNECT_TIMEOUT": q.get("connect_timeout", "15")})
    if p.password:
        env["PGPASSWORD"] = unquote(p.password)
    if q.get("hostaddr"):
        env["PGHOSTADDR"] = q["hostaddr"]
    local = env["PGHOST"].startswith("/") or env["PGHOST"] in ("localhost", "127.0.0.1", "::1")
    mode = q.get("sslmode") or ("prefer" if local else "require")
    if not local and mode not in ("require", "verify-ca", "verify-full"):
        raise BackupError(f"Refusing sslmode={mode} for a remote database host.")
    env["PGSSLMODE"] = mode
    if q.get("sslrootcert"):
        env["PGSSLROOTCERT"] = q["sslrootcert"]
    return {k: v for k, v in env.items() if v != ""}


def _tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise BackupError(f"{name} not found. Install the PostgreSQL client tools (same major version as the "
                          "server or newer), e.g. on a Mac: brew install libpq.")
    return path


def _run(args: list[str], env: dict, stdin: str | None = None) -> str:
    r = subprocess.run(args, env=env, input=stdin, capture_output=True, text=True, timeout=3600)
    if r.returncode != 0:
        raise BackupError(f"{Path(args[0]).name} failed: {r.stderr.strip()[-800:]}")
    return r.stdout


def _major(text: str) -> int:
    m = re.search(r"(\d+)(?:\.\d+)?", text)
    return int(m.group(1)) if m else 0


def server_major(env: dict) -> int:
    return _major(_run([_tool("psql"), "-X", "-At", "-c", "SHOW server_version"], env))


def table_fingerprints(env: dict) -> dict:
    out = _run([_tool("psql"), "-X", "-At", "-F", "\t", "-v", "ON_ERROR_STOP=1", "-f", "-"], env, COUNT_SQL)
    res = {}
    for line in out.splitlines():
        schema, table, n, digest = line.split("\t")
        res[f"{schema}.{table}"] = {"rows": int(n), "md5": digest}
    return res


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _private_dir(d: Path) -> Path:
    d = d.expanduser().resolve()
    if d == PROJECT_ROOT or PROJECT_ROOT in d.parents:
        raise BackupError("Backups must not be written inside the project folder (it could end up in Git).")
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, stat.S_IRWXU)
    return d


def backup(url: str, out_dir: Path) -> Path:
    env = pg_env(url)
    tool_major, srv = _major(_run([_tool("pg_dump"), "--version"], env)), server_major(env)
    if tool_major < srv:
        raise BackupError(f"pg_dump {tool_major} is older than the server ({srv}); install client tools {srv}+.")
    out_dir = _private_dir(out_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dump = out_dir / f"eventpulse_{stamp}.dump"
    old = os.umask(0o077)
    try:
        before = table_fingerprints(env)
        _run([_tool("pg_dump"), "--format=custom", "--no-owner", "--no-privileges", "--file", str(dump)], env)
        after = table_fingerprints(env)
        if before != after:
            raise BackupError("The database changed while the backup was running; run the backup again.")
        digest = sha256(dump)
        dump.with_suffix(".dump.sha256").write_text(f"{digest}  {dump.name}\n")
        manifest = {"created_utc": stamp, "server_major": srv, "pg_dump_major": tool_major, "sha256": digest,
                    "tables": before}
        (out_dir / f"eventpulse_{stamp}.manifest.json").write_text(json.dumps(manifest, indent=2))
    finally:
        os.umask(old)
    for f in out_dir.glob(f"eventpulse_{stamp}.*"):
        os.chmod(f, stat.S_IRUSR | stat.S_IWUSR)
    return dump


def _manifest(dump: Path) -> dict:
    m = dump.parent / dump.name.replace(".dump", ".manifest.json")
    if not m.exists():
        raise BackupError(f"Manifest {m.name} is missing.")
    return json.loads(m.read_text())


def verify(dump: Path) -> dict:
    dump = Path(dump).expanduser()
    man = _manifest(dump)
    if sha256(dump) != man["sha256"]:
        raise BackupError("Checksum mismatch: the backup file is damaged or was changed.")
    side = dump.with_suffix(".dump.sha256")
    if side.exists() and side.read_text().split()[0] != man["sha256"]:
        raise BackupError("The .sha256 file does not match the manifest.")
    listing = _run([_tool("pg_restore"), "--list", str(dump)], dict(os.environ))
    tables = set(re.findall(r" TABLE DATA \S+ (\S+) ", listing))
    missing = sorted(t for t in EXPECTED_TABLES if t not in tables)
    if missing:
        raise BackupError(f"Backup does not contain expected tables: {', '.join(missing)}")
    return {"ok": True, "tables_in_archive": len(tables), "rows": sum(t["rows"] for t in man["tables"].values())}


def restore(dump: Path, url: str) -> dict:
    dump = Path(dump).expanduser()
    verify(dump)
    man = _manifest(dump)
    env = pg_env(url)
    if table_fingerprints(env):
        raise BackupError("The target database is not empty. Restore only into a NEW, empty database.")
    _run([_tool("pg_restore"), "--no-owner", "--no-privileges", "--exit-on-error", "--single-transaction",
          "--dbname", env["PGDATABASE"], str(dump)], env)
    got = table_fingerprints(env)
    diff = sorted(t for t in set(man["tables"]) | set(got) if man["tables"].get(t) != got.get(t))
    if diff:
        raise BackupError(f"Restored data differs from the backup in: {', '.join(diff)}")
    return {"ok": True, "tables": len(got), "rows": sum(t["rows"] for t in got.values())}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="EventPulse AI PostgreSQL backup / verify / restore")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--out-dir", default=os.environ.get("EVENTPULSE_BACKUP_DIR", "~/EventPulseBackups"))
    v = sub.add_parser("verify")
    v.add_argument("dump")
    r = sub.add_parser("restore")
    r.add_argument("dump")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "backup":
            url = os.environ.get("EVENTPULSE_BACKUP_DB_URL") or os.environ.get("EVENTPULSE_DB_URL")
            if not url:
                raise BackupError("Set EVENTPULSE_BACKUP_DB_URL (or EVENTPULSE_DB_URL) to the database URL.")
            d = backup(url, Path(a.out_dir))
            print(f"Backup written: {d}\n" + json.dumps(verify(d)))
        elif a.cmd == "verify":
            print(json.dumps(verify(Path(a.dump))))
        else:
            url = os.environ.get("EVENTPULSE_RESTORE_DB_URL")
            if not url:
                raise BackupError("Set EVENTPULSE_RESTORE_DB_URL to the NEW, empty target database URL.")
            print(json.dumps(restore(Path(a.dump), url)))
    except BackupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
