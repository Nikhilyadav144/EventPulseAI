"""What-if event planner (a tab of Attendance Prediction): change event-level model inputs
and/or the number of registrations and compare expected attendance with the event as stored."""
from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QSpinBox, QVBoxLayout, QWidget

from core import charts as C
from core import intelligence as I
from core.schema import COLUMNS
from gui import theme
from gui.widgets import ChartView, KpiCard, Notice, ResponsiveGrid, button, card, hbox, label, show_error

CHOICES = {"Event_Type": "Event category", "Event_Day": "Day", "Event_Time": "Time slot",
           "Weather_Forecast": "Weather forecast"}
NUMBERS = {"Event_Duration_Hours": ("Duration (hours)", 0.5, 12, 0.5), "Event_Popularity": ("Popularity (0-100)", 0, 100, 5)}


class WhatIfPanel(QWidget):
    def __init__(self, page):
        super().__init__()
        self.page = page
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)
        self.event = QComboBox()
        self.event.currentTextChanged.connect(lambda _: self.load_defaults())
        self.fields: dict[str, tuple[QCheckBox, QWidget]] = {}
        form = QFormLayout()
        for col, name in CHOICES.items():
            cb, w = QCheckBox(name), QComboBox()
            w.addItems([str(v) for v in COLUMNS[col].allowed])
            self.fields[col] = (cb, w)
        for col, (name, lo, hi, step) in NUMBERS.items():
            cb, w = QCheckBox(name), QDoubleSpinBox()
            w.setRange(lo, hi)
            w.setSingleStep(step)
            self.fields[col] = (cb, w)
        cb, w = QCheckBox("Exam near the event"), QComboBox()
        w.addItems(["No", "Yes"])
        self.fields["Has_Exam_Near_Event"] = (cb, w)
        for col, (cb, w) in self.fields.items():
            w.setEnabled(False)
            cb.toggled.connect(w.setEnabled)
            form.addRow(cb, w)
        self.reg_cb = QCheckBox("Number of registrations")
        self.reg_n = QSpinBox()
        self.reg_n.setRange(1, 5000)
        self.reg_n.setEnabled(False)
        self.reg_cb.toggled.connect(self.reg_n.setEnabled)
        form.addRow(self.reg_cb, self.reg_n)
        fw = QWidget()
        fw.setLayout(form)
        self.btn = button("Compare scenarios", "Primary", self.run)
        lay.addWidget(card(hbox(label("Event", wrap=False), (self.event, 1)), fw, self.btn,
                           title="What-if event planner",
                           subtitle="Tick an input to change it. Only event-level inputs the model actually uses can "
                                    "be changed; student profiles stay as registered."))
        self.k = [KpiCard("Original: expected attendees", tone="blue", icon="check"),
                  KpiCard("Scenario: expected attendees", tone="purple", icon="spark"),
                  KpiCard("Difference", tone="gold", icon="curve"),
                  KpiCard("Scenario: expected no-shows", tone="gold", icon="cross")]
        lay.addWidget(ResponsiveGrid(self.k, min_width=160, allowed=(4, 2), spacing=14))
        self.chart = ChartView(min_height=280)
        self.notes = Notice()
        lay.addWidget(self.notes)
        lay.addWidget(card(self.chart, title="Original vs scenario"))

    def refresh(self, db):
        cur = self.event.currentText()
        self.event.blockSignals(True)
        self.event.clear()
        for r in db.query("SELECT event_id, event_type, active_registrations FROM v_event_participation "
                          "WHERE active_registrations > 0 ORDER BY event_id"):
            self.event.addItem(f"{r['event_id']}  ·  {r['event_type']} · {r['active_registrations']} registered")
        if cur:
            self.event.setCurrentText(cur)
        self.event.blockSignals(False)
        self.load_defaults()

    def _eid(self) -> str | None:
        t = self.event.currentText()
        return t.split("  ")[0] if t else None

    def load_defaults(self):
        eid = self._eid()
        if not eid:
            return
        db = self.page.ctx.db
        e = db.query_one("SELECT Event_Type, Event_Day, Event_Time, Event_Duration_Hours, Event_Popularity, "
                         "Weather_Forecast FROM v_registration_features WHERE Event_ID = ? LIMIT 1", (eid,))
        n = db.scalar("SELECT COUNT(*) FROM registrations WHERE event_id = ? AND status = 'Registered'", (eid,))
        if not e:
            return
        for col in CHOICES:
            self.fields[col][1].setCurrentText(str(e[col]))
        for col in NUMBERS:
            if e[col] is not None:
                self.fields[col][1].setValue(float(e[col]))
        self.reg_n.setValue(int(n or 1))

    def run(self):
        eid = self._eid()
        if not eid:
            return
        changes = {}
        for col, (cb, w) in self.fields.items():
            if not cb.isChecked():
                continue
            if col == "Has_Exam_Near_Event":
                changes[col] = 1 if w.currentText() == "Yes" else 0
            elif col in NUMBERS:
                changes[col] = w.value()
            else:
                changes[col] = w.currentText()
        try:
            r = I.what_if(self.page.ctx.db, eid, changes, self.reg_n.value() if self.reg_cb.isChecked() else None,
                          self.page.selected_model_id())
        except (ValueError, Exception) as exc:            # surfaced to the user
            show_error(self, "Scenario not possible", str(exc))
            return
        o, m = r.original, r.modified
        self.k[0].set(f"{o.expected_attendees:.1f}", f"of {o.registrations} · approx. {o.low:.0f}–{o.high:.0f}")
        self.k[1].set(f"{m.expected_attendees:.1f}", f"of {m.registrations} · approx. {m.low:.0f}–{m.high:.0f}")
        self.k[2].set(f"{r.difference:+.1f}", "scenario − original")
        self.k[3].set(f"{m.expected_no_shows:.1f}", f"original {o.expected_no_shows:.1f}")
        changed = ", ".join(f"{k} = {v}" for k, v in r.changes.items()) or "no input changed"
        self.notes.set(f"{r.model}. Changes: {changed}. " + " ".join(r.notes), "info")
        self.chart.set_figure(C.grouped_bar(["Expected attendees", "Expected no-shows"],
                                            {"Original": [o.expected_attendees, o.expected_no_shows],
                                             "Scenario": [m.expected_attendees, m.expected_no_shows]},
                                            f"{eid}: original vs scenario", theme.current().chart))
