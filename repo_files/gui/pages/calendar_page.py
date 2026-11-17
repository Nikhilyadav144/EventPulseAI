"""Event Calendar: dated events by month, with phase (upcoming / ongoing / completed) filters."""
from __future__ import annotations

from PySide6.QtCore import QDate
from PySide6.QtGui import QColor, QTextCharFormat, QFont
from PySide6.QtWidgets import QCalendarWidget, QComboBox

from core import workflow as W
from gui import theme
from gui.pages.base import Page
from gui.widgets import DataTable, KpiCard, ResponsiveGrid, button, card, hbox, label, status_tone

PHASE_FILTERS = {"All phases": None, "Upcoming": ("Upcoming",), "Ongoing": ("Ongoing",),
                 "Completed": ("Completed",), "Cancelled": ("Cancelled",)}


class EventCalendarPage(Page):
    title = "Event Calendar"
    subtitle = "Dated events by month. Pick a day to see its events; open one to edit it in Event Management."
    areas = ("events",)

    def build(self):
        self.k = [KpiCard("Upcoming", tone="blue", icon="calendar"),
                  KpiCard("Ongoing now", tone="purple", icon="events"),
                  KpiCard("Completed (dated)", tone="good", icon="check"),
                  KpiCard("Undated events", tone="gold", icon="info",
                          tooltip="Events without a date (e.g. imported dataset events) cannot be placed on a "
                                  "calendar. Add a date in Event Management.")]
        self.body.addWidget(ResponsiveGrid(self.k, min_width=160, allowed=(4, 2), spacing=14))
        self.cal = QCalendarWidget()
        self.cal.setGridVisible(False)
        self.cal.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.cal.setMinimumHeight(320)
        self.cal.currentPageChanged.connect(lambda *_: self.fill_table())
        self.cal.selectionChanged.connect(self.fill_table)
        self.phase = QComboBox()
        self.phase.addItems(list(PHASE_FILTERS))
        self.phase.currentTextChanged.connect(lambda _: self.reload())
        self.scope = QComboBox()
        self.scope.addItems(["Selected day", "Visible month", "All dated events"])
        self.scope.setCurrentIndex(1)
        self.scope.currentTextChanged.connect(lambda _: self.fill_table())
        self.table = DataTable(filterable=False)
        self.table.setMinimumHeight(260)
        self.open_btn = button("Open in Event Management", "Ghost", self.open_selected)
        self.today_btn = button("Today", "Ghost", lambda: (self.cal.setSelectedDate(QDate.currentDate()),
                                                          self.cal.showToday()))
        self.body.addWidget(ResponsiveGrid([
            card(self.cal, title="Calendar", subtitle="Highlighted days have events (bold = upcoming).",
                 actions=[self.today_btn]),
            card(hbox(label("Show", wrap=False), self.scope, label("Phase", wrap=False), self.phase, "stretch"),
                 self.table, self.open_btn, title="Events")], min_width=420, allowed=(2, 1)))
        self.frame = None

    def refresh(self):
        db = self.ctx.db
        self.frame = W.calendar_events(db, phases=PHASE_FILTERS[self.phase.currentText()])
        allf = W.calendar_events(db)
        undated = db.scalar("SELECT COUNT(*) FROM events WHERE event_date IS NULL")
        n = lambda ph: int((allf["phase"] == ph).sum()) if not allf.empty else 0  # noqa: E731
        self.k[0].set(str(n("Upcoming")), "scheduled, date ahead")
        self.k[1].set(str(n("Ongoing")), "started, not yet finished")
        self.k[2].set(str(n("Completed")), "dated events in the past or completed")
        self.k[3].set(str(undated), "not shown on the calendar")
        self.notice.set("No dated events yet. Imported dataset events have no dates; create events with a date in "
                        "Event Management to plan them here." if allf.empty else "", "note")
        p = theme.current()
        self.cal.setDateTextFormat(QDate(), QTextCharFormat())          # clear previous highlights
        for _, r in self.frame.iterrows():
            f = QTextCharFormat()
            f.setBackground(QColor(p.accent_soft if r["phase"] != "Cancelled" else p.surface_alt))
            if r["phase"] in ("Upcoming", "Ongoing"):
                f.setFontWeight(QFont.Bold)
                f.setForeground(QColor(p.accent))
            self.cal.setDateTextFormat(QDate.fromString(r["event_date"], "yyyy-MM-dd"), f)
        self.fill_table()

    def fill_table(self):
        if self.frame is None:
            return
        df = self.frame
        scope = self.scope.currentText()
        if not df.empty and scope != "All dated events":
            if scope == "Selected day":
                d = self.cal.selectedDate().toString("yyyy-MM-dd")
                df = df[df["event_date"] == d]
            else:
                y, m = self.cal.yearShown(), self.cal.monthShown()
                df = df[df["event_date"].str.startswith(f"{y:04d}-{m:02d}")]
        cols = ["event_date", "start", "end", "event_id", "name", "event_type", "phase", "status", "venue",
                "registrations", "capacity", "registration_deadline"]
        self.table.set_frame(df[cols] if not df.empty else df.reindex(columns=cols),
                             tones={"phase": status_tone, "status": status_tone})

    def open_selected(self):
        rec = self.table.selected_record()
        if rec:
            self.ctx.open_event.emit(rec["event_id"])
        else:
            self.ctx.status.emit("Select an event in the table first.")

