"""Deployment helper for the hosted (PostgreSQL) EventPulse AI web app (Phase 3).

    python -m webapp.hosted_setup check-config         # validate the production environment (no DB access)
    python -m webapp.hosted_setup build-models --out models_hosted
                                                       # train models on the SYNTHETIC dataset (build step)
    python -m webapp.hosted_setup init-db [--seed-synthetic-demo] [--models-dir DIR]
                                                       # apply all migrations to EVENTPULSE_DB_URL (+ optional demo data)
    python -m webapp.hosted_setup status               # schema versions, admin present, record counts

The administrator is created with ``python -m webapp.provision_admin --db-url ...`` (interactive,
password never echoed) or, on a host without a shell, from secret environment variables at start-up
(webapp/bootstrap.py). Every database command here needs an explicit PostgreSQL URL: the desktop's
SQLite database is never used.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from config import settings
from core.db.connection import Database, web_database_url


def _url(a) -> str:
    url = a.db_url or web_database_url()
    if not url:
        raise SystemExit("Set EVENTPULSE_DB_URL (postgresql://...) or pass --db-url. The desktop SQLite "
                         "database is never used by this tool.")
    return url


def cmd_check_config(_a) -> int:
    from webapp import security as SEC
    try:
        cfg = SEC.load_config()
    except SEC.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(json.dumps({"production": cfg.production, "public_base_url": cfg.public_base_url,
                      "allowed_hosts": list(cfg.allowed_hosts), "secure_cookies": cfg.secure_cookies,
                      "trusted_proxies": "*" if cfg.trust_all_proxies else [str(n) for n in cfg.trusted_proxies],
                      "timezone": cfg.timezone_name, "email_domains": list(cfg.allowed_email_domains),
                      "hash_concurrency": cfg.hash_concurrency, "database_configured": bool(web_database_url())},
                     indent=2))
    return 0


def cmd_build_models(a) -> int:
    import pandas as pd
    from core.model_training import train_and_compare
    out = Path(a.out).resolve()
    protected = [settings.MODELS_DIR.resolve(), (settings.PROJECT_ROOT / "model").resolve(),
                 settings.DATABASE_DIR.resolve(), settings.DATA_DIR.resolve()]
    if any(out == p or p in out.parents for p in protected):
        raise SystemExit(f"Refusing to write models into {out}: that folder belongs to the desktop app or its data.")
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty; choose a new folder.")
    csv = Path(a.dataset or settings.SYNTHETIC_DATASET)
    df = pd.read_csv(csv)
    if "Data_Source" not in df.columns or set(df["Data_Source"].dropna()) != {"SYNTHETIC"}:
        raise SystemExit("Hosted models are trained only on the labelled synthetic dataset.")
    run = train_and_compare(df, "SYNTHETIC - generated demonstration data (not real students)", csv,
                            fast=True, models_dir=out)
    print(f"Built {len(run.saved_dirs)} models in {out} (active: "
          f"{json.loads((out / 'active_model.json').read_text())['model_id']}).")
    return 0


def cmd_init_db(a) -> int:
    from webapp import auth as AUTH
    from webapp import bootstrap as BOOT
    url = _url(a)
    with Database(url=url) as db:                     # applies the core migrations
        if db.is_sqlite:
            raise SystemExit("init-db only initialises PostgreSQL.")
        AUTH.ensure_auth_schema(db)
        out = {"core_schema": db.scalar("SELECT MAX(version) FROM schema_migrations"),
               "web_auth_schema": db.scalar("SELECT MAX(version) FROM web_auth_migrations")}
        if a.seed_synthetic_demo:
            out["seed"] = BOOT.seed_synthetic_demo(db)
        if a.models_dir:
            out["models"] = sorted(BOOT.register_hosted_models(db, Path(a.models_dir)))
        out["admin_exists"] = AUTH.admin_exists(db)
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_status(a) -> int:
    from webapp import auth as AUTH
    with Database(url=_url(a)) as db:
        AUTH.ensure_auth_schema_once(db)
        out = {"engine": "postgresql", "schema": getattr(db.conn, "schema", None),
               "core_schema": db.scalar("SELECT MAX(version) FROM schema_migrations"),
               "web_auth_schema": db.scalar("SELECT MAX(version) FROM web_auth_migrations"),
               "admin_exists": AUTH.admin_exists(db),
               "student_accounts": {r["link_status"]: r["n"] for r in db.query(
                   "SELECT link_status, COUNT(*) AS n FROM users WHERE role = 'student' GROUP BY link_status")}}
        for t in ("students", "events", "registrations", "checkins", "model_versions"):
            out[t] = db.scalar(f"SELECT COUNT(*) FROM {t}")
    print(json.dumps(out, indent=2, default=str))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check-config")
    b = sub.add_parser("build-models")
    b.add_argument("--out", required=True)
    b.add_argument("--dataset", help="synthetic CSV (default: the project's synthetic dataset)")
    i = sub.add_parser("init-db")
    i.add_argument("--db-url")
    i.add_argument("--seed-synthetic-demo", action="store_true")
    i.add_argument("--models-dir")
    s = sub.add_parser("status")
    s.add_argument("--db-url")
    a = ap.parse_args(argv)
    return {"check-config": cmd_check_config, "build-models": cmd_build_models,
            "init-db": cmd_init_db, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
