"""Render every page of the real application to PNG (docs/screenshots/v1.1.0/rendered/).

Runs the actual MainWindow on a COPY of the project database, performs a short
demo workflow (event predictions, a dated event with recorded attendance), then
grabs each page. The project database is not modified.

    .venv/bin/python scripts/capture_screenshots.py [--offscreen]
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if "--offscreen" in sys.argv:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

import matplotlib  # noqa: E402

matplotlib.use("QtAgg")
from PySide6.QtCore import QThreadPool  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.db import services  # noqa: E402
from core.db.connection import DEFAULT_DB_PATH, Database  # noqa: E402
from core.db.repositories import (ForecastRepository, RegistrationRepository, EventRepository)
from gui import theme  # noqa: E402
from gui.context import AppContext  # noqa: E402
from gui.main_window import MainWindow  # noqa: E402

OUT = ROOT / "docs" / "screenshots" / "v1.2.0" / "rendered"


def settle(app, n=30):
    QThreadPool.globalInstance().waitForDone(120000)
    for _ in range(n):
        app.processEvents()


def demo_data(db: Database) -> None:
    """Use the app's own services to create realistic, clearly-demo activity."""
    for eid in ("EV06", "EV12", "EV22", "EV19"):
        services.predict_event(db, eid)
    today = datetime.now(timezone.utc).date().isoformat()
    if not EventRepository(db).exists("EVDEMO"):
        EventRepository(db).create(event_id="EVDEMO", name="Demo: AI/ML workshop", event_type="Workshop",
                                   event_date=today, time_slot="Afternoon", duration_hours=2.5, venue="Seminar Hall",
                                   capacity=60, popularity=70)
        ForecastRepository(db).add("EVDEMO", "Cloudy", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        students = [r["student_id"] for r in db.query("SELECT student_id FROM students ORDER BY student_id LIMIT 18")]
        for i, s in enumerate(students):
            RegistrationRepository(db).register(s, "EVDEMO", 2 + i % 10, has_exam_near_event=int(i % 7 == 0))
        services.predict_event(db, "EVDEMO")
        attended = {s: int(i % 4 != 0) for i, s in enumerate(students)}   # demo entry: 13 of 18
        services.record_actual_attendance(db, "EVDEMO", attended=attended, walk_ins=2, recorded_by="demo")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="ep_shots_"))
    db_copy = tmp / "eventpulse_screens.db"
    with Database(DEFAULT_DB_PATH) as src:
        import sqlite3
        dst = sqlite3.connect(db_copy)
        src.conn.backup(dst)
        dst.close()
    with Database(db_copy) as db:
        demo_data(db)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(theme.stylesheet(theme.set_current("cream")))
    ctx = AppContext(db_copy)
    win = MainWindow(ctx)
    win.resize(1440, 900)
    win.show()
    settle(app)
    names = ["01_overview", "02_dataset_explorer", "03_event_management", "04_event_calendar",
             "05_registration_management", "06_attendance_prediction", "07_attendance_records",
             "08_model_laboratory", "09_analytics", "10_insights", "11_model_monitoring", "12_reports",
             "13_notifications", "14_settings"]
    dx = win.pages[1]
    dx.load_reference("processed")
    ev = win.pages[2]
    pr = win.pages[5]
    for i, name in enumerate(names):
        win.go(i)
        settle(app)
        if i == 2:
            ev.load_event("EVDEMO")
            ev.tabs.setCurrentIndex(3)
            settle(app)
        if i == 5:
            pr.i_student.setCurrentText("STU001")
            pr.i_event.setCurrentText("EV09")
            pr.predict_individual()
            settle(app)
        win.grab().save(str(OUT / f"{name}.png"))
        print("saved", name)
    win.go(5)
    pr.tabs.setCurrentIndex(1)
    idx = next(i for i in range(pr.e_event.count()) if pr.e_event.itemText(i).startswith("EV22"))
    pr.e_event.setCurrentIndex(idx)
    pr.predict_event()
    settle(app)
    win.grab().save(str(OUT / "15_event_prediction.png"))
    pr.tabs.setCurrentIndex(3)
    pr.whatif.event.setCurrentIndex(max(0, next((i for i in range(pr.whatif.event.count())
                                                 if pr.whatif.event.itemText(i).startswith("EV22")), 0)))
    pr.whatif.fields["Has_Exam_Near_Event"][0].setChecked(True)
    pr.whatif.fields["Has_Exam_Near_Event"][1].setCurrentText("Yes")
    pr.whatif.run()
    settle(app)
    win.grab().save(str(OUT / "16_what_if_planner.png"))
    win.go(1)
    dx.tabs.setCurrentIndex(4)
    settle(app)
    win.grab().save(str(OUT / "17_dataset_distributions.png"))
    win.resize(1100, 760)                       # narrow-window layout check
    for i, name in ((0, "overview"), (5, "attendance_prediction"), (7, "model_laboratory"), (13, "settings")):
        win.go(i)
        settle(app)
        win.grab().save(str(OUT / f"narrow_1100_{name}.png"))
    win.close()
    shutil.rmtree(tmp, ignore_errors=True)
    print("screenshots in", OUT)


if __name__ == "__main__":
    main()
