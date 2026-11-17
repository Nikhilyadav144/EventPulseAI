"""Registration Management: every registration across events, filters, department-wise
participation, status changes and validated CSV/Excel import (duplicates refused)."""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QFileDialog, QLineEdit

from core import charts as C
from core import workflow as W
from core.data_loader import DataImportError, load_table
from core.db.errors import DatabaseError
from core.db.repositories import RegistrationRepository
from gui import theme
from gui.pages.base import Page
from gui.widgets import (DATA_SOURCE_LABELS, ChartView, DataTable, KpiCard, ResponsiveGrid, attendance_tone, button,
                         card, confirm, hbox, label, show_error, source_tone, status_tone)

STATUSES = ["All statuses", "Registered", "Waitlisted", "Cancelled"]
SOURCES = {"All sources": None, "Original dataset": "ORIGINAL_HYBRID", "Synthetic demo": "SYNTHETIC",
           "Entered in app": "MANUAL", "Uploaded file": "UPLOAD"}


class RegistrationsPage(Page):
    title = "Registration Management"
    subtitle = ("All registrations across events: search, filter, change status and import registrations from a "
                "file. A student can be registered for an event only once.")
    areas = ("events", "datasets")

    def build(self):
        self.k = [KpiCard("Registrations", tone="purple", icon="students"),
                  KpiCard("Active", tone="good", icon="check"),
                  KpiCard("Waitlisted / cancelled", tone="gold", icon="cross"),
                  KpiCard("Departments", tone="blue", icon="analytics")]
        self.body.addWidget(ResponsiveGrid(self.k, min_width=160, allowed=(4, 2), spacing=14))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Student ID, event ID, event name or course…")
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(self.fill)
        self.event = QComboBox()
        self.dept = QComboBox()
        self.status = QComboBox()
        self.status.addItems(STATUSES)
        self.source = QComboBox()
        self.source.addItems(list(SOURCES))
        for c in (self.event, self.dept, self.status, self.source):
            c.currentTextChanged.connect(lambda _: self.fill())
        self.table = DataTable(searchable=False)
        self.table.setMinimumHeight(340)
        self.import_btn = button("Import registrations…", "Primary", self.import_file,
                                 "CSV/Excel with Student_ID, Event_ID and Registration_Days_Before (or Registered_On)")
        self.body.addWidget(card(
            hbox((self.search, 2), button("Search", "Ghost", self.fill), spacing=8),
            hbox(label("Event", wrap=False), (self.event, 1), label("Department", wrap=False), (self.dept, 1),
                 spacing=8),
            hbox(label("Status", wrap=False), (self.status, 1), label("Source", wrap=False), (self.source, 1),
                 spacing=8),
            self.table,
            hbox(button("Mark registered", "Ghost", lambda: self.set_status("Registered")),
                 button("Waitlist", "Ghost", lambda: self.set_status("Waitlisted")),
                 button("Cancel registration", "Danger", lambda: self.set_status("Cancelled")),
                 button("Open event", "Ghost", self.open_event), "stretch", spacing=8),
            title="Registrations", actions=[self.import_btn],
            subtitle="Attendance shows recorded outcomes; '(simulated)' marks outcomes that came from a dataset."))
        self.dept_chart = ChartView(min_height=300)
        self.dept_table = DataTable(filterable=False, searchable=False)
        self.dept_table.setMinimumHeight(220)
        self.body.addWidget(card(ResponsiveGrid([self.dept_chart, self.dept_table], min_width=380, allowed=(2, 1)),
                                 title="Department-wise participation",
                                 subtitle="Registrations per department for the selected event (or all events)."))

    # ------------------------------------------------------------------ data
    def refresh(self):
        db = self.ctx.db
        cur_e, cur_d = self.event.currentText(), self.dept.currentText()
        for combo, items, cur in (
                (self.event, ["All events"] + [r["event_id"] for r in db.query(
                    "SELECT event_id FROM events ORDER BY event_id")], cur_e),
                (self.dept, ["All departments"] + [r["d"] for r in db.query(
                    "SELECT DISTINCT department d FROM courses ORDER BY 1")], cur_d)):
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(items)
            if cur in items:
                combo.setCurrentText(cur)
            combo.blockSignals(False)
        t = db.query_one("SELECT COUNT(*) n, SUM(status='Registered') a, SUM(status<>'Registered') o, "
                         "(SELECT COUNT(DISTINCT department) FROM courses c JOIN students s "
                         "ON s.course_id = c.course_id JOIN registrations r USING(student_id)) d FROM registrations")
        self.k[0].set(str(t["n"] or 0), "all events")
        self.k[1].set(str(t["a"] or 0), "status Registered")
        self.k[2].set(str(t["o"] or 0), "not counted in predictions")
        self.k[3].set(str(t["d"] or 0), "with at least one registration")
        self.fill()

    def _filters(self) -> dict:
        ev = self.event.currentText()
        return {"search": self.search.text().strip() or None,
                "event_id": None if ev in ("", "All events") else ev,
                "department": None if self.dept.currentText() in ("", "All departments") else self.dept.currentText(),
                "status": None if self.status.currentText() == "All statuses" else self.status.currentText(),
                "data_source": SOURCES[self.source.currentText()]}

    def fill(self):
        f = self._filters()
        df = W.registration_table(self.ctx.db, **f)
        if not df.empty:
            df["data_source"] = df["data_source"].map(DATA_SOURCE_LABELS).fillna(df["data_source"])
            df["has_exam_near_event"] = df["has_exam_near_event"].map({1: "Yes", 0: "No"})
        self.table.set_frame(df, tones={"status": status_tone, "attendance": attendance_tone,
                                        "data_source": source_tone})
        dep = W.department_participation(self.ctx.db, f["event_id"])
        th = theme.current().chart
        self.dept_chart.set_figure(C.bar(dep["department"] if not dep.empty else [],
                                         dep["registrations"] if not dep.empty else [],
                                         "Registrations by department", th,
                                         subtitle=f["event_id"] or "all events", horizontal=True))
        self.dept_table.set_frame(dep)

    # ------------------------------------------------------------------ actions
    def set_status(self, status: str):
        rec = self.table.selected_record()
        if not rec:
            self.ctx.status.emit("Select a registration first.")
            return
        if status == "Cancelled" and not confirm(self, "Cancel registration",
                                                 f"Cancel {rec['student_id']}'s registration for {rec['event_id']}? "
                                                 "Its history is kept.", "Cancel registration", danger=True):
            return
        try:
            RegistrationRepository(self.ctx.db).set_status(int(rec["registration_id"]), status)
            self.ctx.db.audit("status", "registration", rec["registration_id"], status)
        except DatabaseError as exc:
            show_error(self, "Could not change the status", str(exc))
            return
        self.ctx.notify("events")
        self.ctx.status.emit(f"{rec['student_id']} → {rec['event_id']}: {status}")

    def open_event(self):
        rec = self.table.selected_record()
        if rec:
            self.ctx.open_event.emit(rec["event_id"])

    def import_file(self, path: str | None = None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Import registrations", "", "Tables (*.csv *.xlsx)")
        if not path:
            return
        try:
            df = load_table(path).df
            preview = W.import_registrations(self.ctx.db, df, dry_run=True)
        except (DataImportError, DatabaseError) as exc:
            show_error(self, "Import refused", str(exc))
            return
        if preview.errors:
            show_error(self, "Import refused - nothing was written",
                       f"{len(preview.errors)} row(s) are invalid. Fix them and import again.",
                       "\n".join(preview.errors[:50]))
            return
        n_new = len(df) - len(preview.duplicates)
        if n_new == 0:
            self.notice.set(f"Nothing to import: all {len(preview.duplicates)} row(s) are existing registrations.",
                            "note")
            return
        if not confirm(self, "Import registrations",
                       f"Add {n_new} registration(s)? {len(preview.duplicates)} duplicate(s) will be skipped.",
                       "Import"):
            return
        try:
            res = W.import_registrations(self.ctx.db, df)
        except DatabaseError as exc:
            show_error(self, "Import failed - nothing was written", str(exc))
            return
        self.notice.set(res.summary(), "ok")
        self.ctx.notify("events")
