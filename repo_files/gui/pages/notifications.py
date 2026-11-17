"""Notifications: upcoming events, registration deadlines, capacity and attendance alerts,
computed from stored events and registrations each time the page opens. In-app only
(no e-mail or system notifications are sent). Dismissed alerts are remembered."""
from __future__ import annotations

import pandas as pd
from PySide6.QtWidgets import QCheckBox

from core import workflow as W
from gui.pages.base import Page
from gui.widgets import DataTable, KpiCard, ResponsiveGrid, button, card, hbox, status_tone

KEY = "dismissed_notifications"


class NotificationsPage(Page):
    title = "Notifications"
    subtitle = ("Alerts worked out from your events and registrations: what is coming up, deadlines, capacity and "
                "attendance that still needs recording.")
    areas = ("events", "predictions")

    def build(self):
        self.k = [KpiCard("Critical", tone="critical", icon="bell"),
                  KpiCard("Warnings", tone="gold", icon="bell"),
                  KpiCard("Information", tone="blue", icon="info"),
                  KpiCard("Dismissed", tone="purple", icon="check")]
        self.body.addWidget(ResponsiveGrid(self.k, min_width=150, allowed=(4, 2), spacing=14))
        self.show_dismissed = QCheckBox("Show dismissed")
        self.show_dismissed.toggled.connect(lambda _: self.reload())
        self.table = DataTable()
        self.table.setMinimumHeight(360)
        self.body.addWidget(card(self.table,
                                 hbox(button("Open event", "Ghost", self.open_event),
                                      button("Dismiss", "Ghost", self.dismiss),
                                      button("Restore all", "Ghost", self.restore), "stretch", self.show_dismissed,
                                      spacing=8),
                                 title="Alerts",
                                 subtitle="Shown inside the app only. Capacity alerts need a capacity on the event; "
                                          "deadline alerts need a registration deadline."))
        self.items: list[W.Notification] = []

    def _dismissed(self) -> set[str]:
        return set(self.ctx.settings.get(KEY, []) or [])

    def refresh(self):
        db = self.ctx.db
        self.items = W.notifications(db, predicted=W.expected_attendance_by_event(db))
        dis = self._dismissed()
        rows = [{"level": n.level, "category": n.category, "title": n.title, "detail": n.detail,
                 "event_id": n.event_id, "dismissed": n.key in dis, "key": n.key} for n in self.items]
        df = pd.DataFrame(rows, columns=["level", "category", "title", "detail", "event_id", "dismissed", "key"])
        active = df[~df["dismissed"]] if not df.empty else df
        for i, lvl in enumerate(("critical", "warning", "info")):
            self.k[i].set(str(int((active["level"] == lvl).sum()) if not active.empty else 0))
        self.k[3].set(str(int(df["dismissed"].sum()) if not df.empty else 0))
        view = df if self.show_dismissed.isChecked() else active
        self.table.set_frame(view.drop(columns=["key"]) if not view.empty else view.reindex(
            columns=["level", "category", "title", "detail", "event_id", "dismissed"]),
            tones={"level": status_tone})
        self._view = view.reset_index(drop=True)
        self.notice.set("" if self.items else "No alerts. Dated events with capacities and deadlines produce "
                                              "reminders here.", "note")

    def _selected_key(self) -> str | None:
        r = self.table.selected_row()
        return None if r is None or getattr(self, "_view", None) is None else self._view.iloc[r]["key"]

    def dismiss(self):
        k = self._selected_key()
        if k:
            self.ctx.settings.set(KEY, sorted(self._dismissed() | {k}))
            self.reload()

    def restore(self):
        self.ctx.settings.set(KEY, [])
        self.reload()

    def open_event(self):
        rec = self.table.selected_record()
        if rec and rec.get("event_id"):
            self.ctx.open_event.emit(rec["event_id"])
