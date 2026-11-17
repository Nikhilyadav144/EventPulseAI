"""Attendance Records: QR / desk check-in, finalising attendance after the event,
authorised corrections with an audit trail, and export. Recorded (real) attendance only;
simulated dataset outcomes are listed separately and cannot be edited."""
from __future__ import annotations

import urllib.request

from PySide6.QtCore import QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QCheckBox, QComboBox, QFileDialog, QLabel, QLineEdit, QSpinBox

from core import checkin as C
from core.db.errors import DatabaseError
from core.reports import export_frame
from gui.pages.base import Page
from gui.widgets import (DataTable, KpiCard, Notice, ResponsiveGrid, attendance_tone, button, card, confirm, hbox,
                         label, show_error)


class AttendanceRecordsPage(Page):
    title = "Attendance Records"
    subtitle = ("Record who actually attended: QR check-in from students' phones or check-in at the desk, then "
                "finalise after the event to compare with the predictions made before it.")
    areas = ("events", "predictions")

    def build(self):
        self.event = QComboBox()
        self.event.currentTextChanged.connect(lambda _: self.load_event())
        self.show_sim = QCheckBox("Show simulated dataset outcomes")
        self.show_sim.setToolTip("Outcomes imported with the dataset are SIMULATED. They are shown read-only.")
        self.show_sim.toggled.connect(lambda _: self.reload())
        self.body.addWidget(card(hbox(label("Event", wrap=False), (self.event, 1), self.show_sim),
                                 label("Only events created in the app (or imported for real use) take recorded "
                                       "attendance. Dataset events have simulated outcomes and are kept separate.",
                                       "Muted"), title="Event"))
        self.k = [KpiCard("Registered", tone="blue", icon="students"),
                  KpiCard("Checked in", tone="good", icon="check"),
                  KpiCard("Recorded absent", tone="gold", icon="cross"),
                  KpiCard("Not yet recorded", tone="purple", icon="info")]
        self.body.addWidget(ResponsiveGrid(self.k, min_width=150, allowed=(4, 2), spacing=14))

        # QR + desk check-in
        self.qr = QLabel()
        self.qr.setFixedSize(220, 220)
        self.qr.setScaledContents(True)
        self.url = label("", "Muted")
        self.server = Notice()
        self.qr_btn = button("Show check-in QR code", "Primary", self.make_qr)
        self.revoke_btn = button("Revoke code", "Ghost", self.revoke)
        self.desk_sid = QLineEdit()
        self.desk_sid.setPlaceholderText("Student ID")
        self.desk_sid.returnPressed.connect(self.desk_checkin)
        qr_card = card(hbox(self.qr, "stretch"), self.url, self.server, hbox(self.qr_btn, self.revoke_btn, "stretch"),
                       title="QR check-in",
                       subtitle="Students scan the code with their phone camera and enter their Student ID. Needs "
                                "EventPulse_CheckIn_Server.command running on this Mac and phones on the same Wi-Fi.")
        desk_card = card(hbox((self.desk_sid, 1), button("Check in", "Primary", self.desk_checkin)),
                         label("For students without a phone, or if the QR page is unreachable.", "Muted"),
                         title="Desk check-in")
        self.walk = QSpinBox()
        self.walk.setRange(0, 5000)
        fin_card = card(hbox(label("Walk-ins (attended without registering)", wrap=False), self.walk, "stretch"),
                        button("Finalise attendance", "Primary", self.finalise),
                        label("Marks every registered student without a check-in as absent, completes the event and "
                              "compares the result with the predictions stored before it.", "Muted"),
                        title="After the event")
        self.body.addWidget(ResponsiveGrid([qr_card, desk_card, fin_card], min_width=320, allowed=(3, 1)))

        self.table = DataTable()
        self.table.setMinimumHeight(300)
        self.c_val = QComboBox()
        self.c_val.addItems(["Attended", "Absent"])
        self.c_by = QLineEdit()
        self.c_by.setPlaceholderText("Your name")
        self.c_reason = QLineEdit()
        self.c_reason.setPlaceholderText("Reason for the correction")
        self.body.addWidget(card(self.table,
                                 hbox(label("Correct selected:", wrap=False), self.c_val, (self.c_by, 1),
                                      (self.c_reason, 2), button("Save correction", "Ghost", self.correct),
                                      spacing=8),
                                 hbox(button("Export CSV…", "Ghost", lambda: self.export(".csv")),
                                      button("Export Excel…", "Ghost", lambda: self.export(".xlsx")), "stretch"),
                                 title="Attendance",
                                 subtitle="Corrections need a name and a reason; the old value is kept in the log."))
        self.log = DataTable(filterable=False)
        self.log.setMinimumHeight(160)
        self.body.addWidget(card(self.log, title="Correction log"))
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self._tick)

    # ------------------------------------------------------------------ data
    def current(self) -> str | None:
        t = self.event.currentText()
        return t.split("  ")[0] if t and not t.startswith("(") else None

    def refresh(self):
        cur = self.current()
        self.event.blockSignals(True)
        self.event.clear()
        evs = C.real_events(self.ctx.db)
        if not evs:
            self.event.addItem("(no events that take recorded attendance)")
        for e in evs:
            self.event.addItem(f"{e['event_id']}  ·  {e['name'] or ''} · {e['event_date'] or 'no date'} · "
                               f"{e['status']}")
        if cur:
            i = next((k for k in range(self.event.count()) if self.event.itemText(k).startswith(cur + "  ")), 0)
            self.event.setCurrentIndex(i)
        self.event.blockSignals(False)
        self.notice.set("" if evs else "Create an event with a date in Event Management and register students to "
                                       "record real attendance. Dataset events have SIMULATED outcomes.", "note")
        self.load_event()

    def load_event(self):
        db, eid = self.ctx.db, self.current()
        if self.show_sim.isChecked():
            df = C.attendance_table(db, None, include_simulated=True)
            df = df[df["source"].isin(C.SIMULATED_SOURCES)] if not df.empty else df
            self.table.set_frame(df, tones={"outcome": attendance_tone})
        else:
            df = C.attendance_table(db, eid) if eid else C.attendance_table(db, "__none__")
            self.table.set_frame(df, tones={"outcome": attendance_tone})
        regs = int(db.scalar("SELECT COUNT(*) FROM registrations WHERE event_id = ? AND status = 'Registered'",
                             (eid,))) if eid else 0
        sub = C.attendance_table(db, eid) if eid else None
        att = int((sub["outcome"] == "Attended").sum()) if sub is not None and not sub.empty else 0
        ab = int((sub["outcome"] == "Absent").sum()) if sub is not None and not sub.empty else 0
        self.k[0].set(str(regs), "active registrations")
        self.k[1].set(str(att), "recorded present")
        self.k[2].set(str(ab), "recorded absent")
        self.k[3].set(str(max(regs - att - ab, 0)), "no outcome yet")
        self.log.set_frame(C.correction_log(db, eid))
        tok = db.query_one("SELECT token, active FROM checkin_tokens WHERE event_id = ?", (eid,)) if eid else None
        if tok and tok["active"]:
            self._show_qr(eid, tok["token"])
        else:
            self.qr.clear()
            self.url.setText("No active check-in code for this event.")
        self._server_status()

    def _show_qr(self, eid: str, token: str):
        url = C.checkin_url(eid, token)
        try:
            pm = QPixmap()
            pm.loadFromData(C.qr_png(url, scale=10), "PNG")
            self.qr.setPixmap(pm)
        except ImportError:
            self.qr.clear()
            self.url.setText("QR images need the 'segno' package (pip install -r requirements.txt). URL: " + url)
            return
        self.url.setText(url)

    def _server_status(self):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{C.CHECKIN_PORT}/health", timeout=0.6) as r:
                ok = r.status == 200
        except OSError:
            ok = False
        self.server.set("Check-in server is running on this Mac." if ok else
                        "Check-in server is not running: double-click EventPulse_CheckIn_Server.command in the "
                        "project folder before the event (desk check-in works without it).", "ok" if ok else "note")

    def _tick(self):
        if self.isVisible() and self.current():
            self.load_event()

    def showEvent(self, e):
        super().showEvent(e)
        self.timer.start()

    def hideEvent(self, e):
        self.timer.stop()
        super().hideEvent(e)

    # ------------------------------------------------------------------ actions
    def make_qr(self):
        eid = self.current()
        if not eid:
            return
        try:
            tok = C.get_or_create_token(self.ctx.db, eid)
        except DatabaseError as exc:
            show_error(self, "No check-in code", str(exc))
            return
        self._show_qr(eid, tok)

    def revoke(self):
        eid = self.current()
        if eid and confirm(self, "Revoke check-in code", "The current QR code will stop working. A new one can be "
                           "created at any time.", "Revoke"):
            C.revoke_token(self.ctx.db, eid)
            self.load_event()

    def desk_checkin(self):
        eid, sid = self.current(), self.desk_sid.text().strip()
        if not (eid and sid):
            return
        try:
            C.check_in(self.ctx.db, eid, sid, method="DESK")
        except DatabaseError as exc:
            self.notice.set(str(exc), "error")
            return
        self.desk_sid.clear()
        self.notice.set(f"{sid} checked in.", "ok")
        self.load_event()

    def finalise(self):
        eid = self.current()
        if not eid or not confirm(self, "Finalise attendance",
                                  "Registered students without a check-in will be recorded as absent and the "
                                  "event marked Completed. Corrections stay possible afterwards.", "Finalise"):
            return
        try:
            res = C.finalise_attendance(self.ctx.db, eid, self.walk.value())
        except DatabaseError as exc:
            show_error(self, "Could not finalise", str(exc))
            return
        ev = res["evaluations"]
        cmp = (f" Before the event {ev[0]['expected_attendance']:.1f} attendees were expected; actual "
               f"{ev[0]['actual_attendees']} (error {ev[0]['error']:+.1f})." if ev else
               " No prediction was stored before the event, so there is nothing to compare.")
        self.notice.set(f"{res['present']} present, {res['marked_absent']} marked absent, {res['walk_ins']} "
                        f"walk-ins.{cmp}", "ok")
        self.ctx.notify("events")
        self.ctx.notify("predictions")

    def correct(self):
        rec = self.table.selected_record()
        if not rec or rec.get("attended") is None or str(rec.get("attended")) == "nan":
            self.ctx.status.emit("Select a row with a recorded outcome.")
            return
        try:
            C.correct_attendance(self.ctx.db, int(rec["registration_id"]),
                                 1 if self.c_val.currentText() == "Attended" else 0,
                                 self.c_by.text(), self.c_reason.text())
        except DatabaseError as exc:
            show_error(self, "Correction refused", str(exc))
            return
        self.c_reason.clear()
        self.notice.set(f"Corrected {rec['student_id']} ({rec['event_id']}).", "ok")
        self.ctx.notify("events")

    def export(self, ext: str, path: str | None = None):
        df = self.table.visible_frame()
        if df.empty:
            return
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "Export attendance", f"attendance_{self.current() or 'all'}"
                                                  f"{ext}", f"*{ext}")
        if path:
            export_frame(df, path, "Attendance")
            self.notice.set(f"Exported {len(df)} rows to {path}", "ok")
