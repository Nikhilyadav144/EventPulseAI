"""Smoke-test a running EventPulse AI API (local, Docker or the live URL).

    python scripts/smoke_api.py http://127.0.0.1:8000            # checks only
    python scripts/smoke_api.py https://<app>.onrender.com --rows 40

Checks GET /health, GET /, GET /docs, a valid POST /predict, several invalid inputs
(each must return 422) and that /monitoring/summary counts the logged predictions.
With --rows N it also sends N registrations taken from data/event_attendance.csv
(features only, no target), which become entries in the server's prediction log.
Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FEATURES = ["Course", "Year", "Currently_UBA_Volunteer", "Participated_UBA_Event_Before", "Experience_Level",
            "Area_Of_Interest", "Role", "Event_Type", "Event_Day", "Event_Time", "Event_Duration_Hours",
            "Registration_Days_Before", "Previous_Event_Attendance", "Previous_Events_Registered",
            "Previous_Attendance_Rate", "Academic_Engagement_Score", "Event_Popularity",
            "Distance_From_Venue_KM", "Has_Exam_Near_Event", "Weather_Forecast"]
INTS = {"Year", "Currently_UBA_Volunteer", "Participated_UBA_Event_Before", "Registration_Days_Before",
        "Previous_Event_Attendance", "Previous_Events_Registered", "Has_Exam_Near_Event"}
VALID = {"Course": "B.Tech CSE", "Year": 2, "Currently_UBA_Volunteer": 1, "Participated_UBA_Event_Before": 1,
         "Experience_Level": "Some experience", "Area_Of_Interest": "Problem Solving & Decision Making",
         "Role": "Content", "Event_Type": "Workshop", "Event_Day": "Wednesday", "Event_Time": "Morning",
         "Event_Duration_Hours": 2.0, "Registration_Days_Before": 5, "Previous_Event_Attendance": 4,
         "Previous_Events_Registered": 5, "Academic_Engagement_Score": 72.0, "Event_Popularity": 70.0,
         "Distance_From_Venue_KM": 3.5, "Has_Exam_Near_Event": 0, "Weather_Forecast": "Clear"}
INVALID = [("Year = 9", {"Year": 9}), ("Year = 'two'", {"Year": "two"}),
           ("negative distance", {"Distance_From_Venue_KM": -2}),
           ("unknown event type", {"Event_Type": "Hackathon"}),
           ("attended > registered", {"Previous_Event_Attendance": 9})]


def call(base, path, body=None, timeout=90):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base.rstrip("/") + path, data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def row_payload(row: dict) -> dict:
    out = {}
    for c in FEATURES:
        v = row.get(c, "")
        if v in ("", None):
            if c == "Previous_Attendance_Rate" or c in ("Previous_Events_Registered", "Academic_Engagement_Score",
                                                        "Event_Popularity", "Distance_From_Venue_KM"):
                out[c] = None
            continue
        if c in INTS:
            out[c] = int(float(v))
        elif c in ("Event_Duration_Hours", "Previous_Attendance_Rate", "Academic_Engagement_Score",
                   "Event_Popularity", "Distance_From_Venue_KM"):
            out[c] = float(v)
        else:
            out[c] = v
    # the derived rate is recomputed by the API; send it only when consistent
    out["Previous_Attendance_Rate"] = None
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--rows", type=int, default=0)
    a = ap.parse_args(argv)
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}{'  ' + detail if detail else ''}")

    print(f"Smoke test of {a.base}")
    s, b = call(a.base, "/health")
    check("GET /health -> 200, model loaded", s == 200 and json.loads(b).get("model_loaded"), b)
    s, b = call(a.base, "/")
    check("GET / -> UI", s == 200 and "EventPulse AI" in b)
    s, _ = call(a.base, "/docs")
    check("GET /docs -> 200", s == 200)
    s0, b0 = call(a.base, "/monitoring/summary")
    before = json.loads(b0)["logged_predictions"] if s0 == 200 else -1
    s, b = call(a.base, "/predict", VALID)
    r = json.loads(b) if s == 200 else {}
    check("POST /predict valid -> 200", s == 200,
          f"probability {r.get('probability')}, {r.get('label')}, tips {len(r.get('tips', []))}")
    for name, change in INVALID:
        s, b = call(a.base, "/predict", dict(VALID, **change))
        check(f"POST /predict {name} -> 422", s == 422, f"(got {s})")
    sent = 1
    if a.rows:
        with open(ROOT / "data" / "event_attendance.csv", newline="") as f:
            rows = list(csv.DictReader(f))
        step = max(1, len(rows) // a.rows)
        codes = [call(a.base, "/predict", row_payload(rows[i]))[0] for i in range(0, step * a.rows, step)]
        check(f"{a.rows} dataset registrations -> 200", all(c == 200 for c in codes),
              f"{codes.count(200)}/{len(codes)}")
        sent += len(codes)
    s, b = call(a.base, "/monitoring/summary")
    after = json.loads(b)["logged_predictions"] if s == 200 else -1
    check("every valid prediction logged (invalid ones are not)", after - before == sent,
          f"{before} -> {after}")
    print("RESULT:", "ALL PASSED" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
