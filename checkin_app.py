"""EventPulse AI - local QR check-in service (runs on the organiser's Mac, same Wi-Fi).

    venv/bin/python -m uvicorn checkin_app:app --host 0.0.0.0 --port 8010
    (or double-click EventPulse_CheckIn_Server.command)

A student scans the event's QR code (shown in the desktop app: Attendance Records) with
the phone's camera app; it opens /checkin/<event>?t=<token>. The student types their
Student ID and taps Check in. The record goes straight into the desktop app's SQLite
database (database/eventpulse.db) through core.checkin.check_in(), which refuses invalid
links, other days, unregistered students and duplicates.

This service is separate from app.py (the public prediction API) and is never deployed:
it needs the local database and is meant for the event venue's network only.
"""
from __future__ import annotations

import html
import os
from pathlib import Path

from fastapi import FastAPI, Form, Query
from fastapi.responses import HTMLResponse

from config.settings import APP_NAME
from core.checkin import check_in
from core.db.connection import DEFAULT_DB_PATH, Database
from core.db.errors import DatabaseError
from core.db.repositories import EventRepository

DB_PATH = Path(os.environ.get("EVENTPULSE_DB", DEFAULT_DB_PATH))
app = FastAPI(title=f"{APP_NAME} check-in", docs_url=None, redoc_url=None, openapi_url=None)

PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Check in · {title}</title>
<style>body{{margin:0;background:#F8F6F2;color:#29263A;font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",
Helvetica,Arial,sans-serif}}main{{max-width:420px;margin:0 auto;padding:28px 20px}}
.card{{background:#fff;border:1px solid #EBE7EF;border-radius:16px;padding:22px}}
.eyebrow{{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#6956A5;font-weight:700;margin:0}}
h1{{font-size:22px;margin:4px 0 2px}}.muted{{color:#8A859C;font-size:14px;margin:0 0 18px}}
label{{font-size:14px;color:#57526B}}input{{width:100%;box-sizing:border-box;font:inherit;padding:12px;margin:6px 0 14px;
border:1px solid #DCD7E3;border-radius:10px}}button{{width:100%;font:inherit;font-weight:650;padding:13px;border:0;
border-radius:12px;background:#6956A5;color:#fff}}.ok{{background:#E3F2EA;color:#2E7D5B}}
.err{{background:#F8E4E1;color:#7A2A23}}.msg{{border-radius:12px;padding:14px;margin-bottom:14px}}
small{{display:block;margin-top:16px;color:#8A859C;font-size:12.5px}}</style></head><body><main>
<p class="eyebrow">EventPulse AI · Check-in</p><h1>{title}</h1><p class="muted">{sub}</p>
<div class="card">{body}</div><small>Only your pseudonymous Student ID is recorded, with the time of check-in.</small>
</main></body></html>"""


def _page(title: str, sub: str, body: str, code: int = 200) -> HTMLResponse:
    return HTMLResponse(PAGE.format(title=html.escape(title), sub=html.escape(sub), body=body), status_code=code)


def _form(event_id: str, token: str, msg: str = "", kind: str = "") -> str:
    box = f'<div class="msg {kind}">{html.escape(msg)}</div>' if msg else ""
    return (f'{box}<form method="post" action="/checkin/{html.escape(event_id)}">'
            f'<input type="hidden" name="t" value="{html.escape(token)}">'
            '<label for="sid">Student ID</label><input id="sid" name="student_id" autocomplete="off" '
            'autocapitalize="characters" required maxlength="40" placeholder="e.g. STU042">'
            '<button type="submit">Check in</button></form>')


def _event(db: Database, event_id: str) -> dict | None:
    try:
        return EventRepository(db).get(event_id)
    except DatabaseError:
        return None


@app.get("/health")
def health():
    return {"status": "ok", "database": DB_PATH.name}


@app.get("/checkin/{event_id}", response_class=HTMLResponse)
def checkin_form(event_id: str, t: str = Query("", max_length=100)):
    with Database(DB_PATH) as db:
        ev = _event(db, event_id)
    if not ev:
        return _page("Unknown event", "", '<div class="msg err">This event does not exist.</div>', 404)
    sub = " · ".join(x for x in (ev["event_date"], ev["venue"]) if x)
    return _page(ev["name"] or event_id, sub, _form(event_id, t))


@app.post("/checkin/{event_id}", response_class=HTMLResponse)
def checkin_submit(event_id: str, student_id: str = Form("", max_length=40), t: str = Form("", max_length=100)):
    with Database(DB_PATH) as db:
        ev = _event(db, event_id)
        if not ev:
            return _page("Unknown event", "", '<div class="msg err">This event does not exist.</div>', 404)
        sub = " · ".join(x for x in (ev["event_date"], ev["venue"]) if x)
        try:
            r = check_in(db, event_id, student_id, t, "QR")
        except DatabaseError as exc:
            return _page(ev["name"] or event_id, sub, _form(event_id, t, str(exc), "err"), 400)
    return _page(ev["name"] or event_id, sub,
                 f'<div class="msg ok">Checked in: <b>{html.escape(r["student_id"])}</b> at '
                 f'{html.escape(r["checked_in_utc"][11:16])} UTC. Enjoy the event!</div>')
