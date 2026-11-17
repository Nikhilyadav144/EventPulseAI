"""Event Management: events, registrations, forecasts, actual attendance and evaluation."""
from __future__ import annotations


import pandas as pd
from PySide6.QtCore import QDate, QDateTime, Qt, QTime
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDateTimeEdit, QDoubleSpinBox, QFormLayout,
                               QGridLayout, QLineEdit, QRadioButton, QSpinBox, QTableWidget, QTableWidgetItem,
                               QTabWidget, QTimeEdit, QVBoxLayout, QWidget)

from core import charts as C
from core.db import services
from core.db.errors import DatabaseError, HistoryProtectedError
from core.db.repositories import (AttendanceRepository, EvaluationRepository, EventRepository, ForecastRepository,
                                  RegistrationRepository, StudentRepository)
from gui import theme
from gui.dialogs import RegistrationDialog, StudentDialog
from gui.pages.base import Page
from gui.widgets import (ATTENDANCE_SOURCE_LABELS, DATA_SOURCE_LABELS, Badge, ChartView, DataTable, button, card,
                         confirm, hbox, label, outcome_tone, show_error, source_tone, status_tone)

EVENT_TYPES = ["Technical", "Workshop", "Cultural", "Seminar", "Sports"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
SLOTS = ["Morning", "Afternoon", "Evening"]


class EventsPage(Page):
    title = "Event Management"
    subtitle = ("Create and edit events, manage registrations and weather forecasts, and record actual attendance "
                "after an event to measure prediction error.")
    areas = ("events", "predictions", "datasets")

    def build(self):
        self.current: str | None = None
        self.header_actions.addWidget(button("New event", "Primary", self.new_event))
        self.table = DataTable()
        self.table.setMinimumHeight(300)
        self.table.row_selected.connect(self._select_row)
        self.body.addWidget(card(self.table, title="Events",
                                 subtitle="Select an event to edit it. Events imported from the original dataset are "
                                          "undated and their outcomes are simulated; scheduled events are future, "
                                          "real events."))

        # ---- form
        f = QFormLayout()
        f.setHorizontalSpacing(14)
        self.f_id = QLineEdit()
        self.f_id.setPlaceholderText("e.g. EV37")
        self.f_name = QLineEdit()
        self.f_type = QComboBox()
        self.f_type.addItems(EVENT_TYPES)
        self.f_has_date = QCheckBox("Date known")
        self.f_has_date.setChecked(True)
        self.f_date = QDateEdit(QDate.currentDate())
        self.f_date.setCalendarPopup(True)
        self.f_date.setDisplayFormat("yyyy-MM-dd")
        self.f_day = QComboBox()
        self.f_day.addItems(DAYS)
        self.f_slot = QComboBox()
        self.f_slot.addItems(SLOTS)
        self.f_has_time = QCheckBox("Start time")
        self.f_time = QTimeEdit(QTime(10, 0))
        self.f_dur = QDoubleSpinBox()
        self.f_dur.setRange(0.5, 12)
        self.f_dur.setSingleStep(0.5)
        self.f_dur.setValue(2)
        self.f_venue = QLineEdit()
        self.f_cap = QSpinBox()
        self.f_cap.setRange(0, 100000)
        self.f_cap.setSpecialValueText("not set")
        self.f_pop = QSpinBox()
        self.f_pop.setRange(-1, 100)
        self.f_pop.setSpecialValueText("unknown")
        self.f_pop.setValue(60)
        self.f_status = QComboBox()
        self.f_status.addItems(["Scheduled", "Completed", "Cancelled"])
        self.f_has_dl = QCheckBox("Registration deadline")
        self.f_dl = QDateEdit(QDate.currentDate())
        self.f_dl.setCalendarPopup(True)
        self.f_dl.setDisplayFormat("yyyy-MM-dd")
        self.f_dl.setEnabled(False)
        self.f_has_dl.toggled.connect(self.f_dl.setEnabled)
        self.f_has_date.toggled.connect(self._date_toggle)
        self.f_has_time.toggled.connect(self.f_time.setEnabled)
        self.f_time.setEnabled(False)
        left, right = QFormLayout(), QFormLayout()
        for lab, w in (("Event ID", self.f_id), ("Name", self.f_name), ("Type", self.f_type),
                       ("", self.f_has_date), ("Date", self.f_date), ("Weekday", self.f_day),
                       (self.f_has_dl, self.f_dl)):
            left.addRow(lab, w)
        for lab, w in (("Time slot", self.f_slot), (self.f_has_time, self.f_time), ("Duration (h)", self.f_dur),
                       ("Venue", self.f_venue), ("Capacity", self.f_cap), ("Popularity (0-100)", self.f_pop),
                       ("Status", self.f_status)):
            right.addRow(lab, w)
        g = QGridLayout()
        g.addLayout(left, 0, 0)
        g.addLayout(right, 0, 1)
        fw = QWidget()
        fw.setLayout(g)
        self.btn_save = button("Save event", "Primary", self.save_event)
        self.btn_cancel_ev = button("Mark cancelled", None, self.cancel_event)
        self.btn_delete = button("Delete…", "Danger", self.delete_event)
        self.form_title = label("New event", "SectionTitle")
        self.b_status, self.b_source = Badge("", "blue"), Badge("", "purple")
        self.b_regs, self.b_outcome = Badge("", "neutral"), Badge("", "neutral")
        self.badges = hbox(self.b_status, self.b_source, self.b_regs, self.b_outcome, "stretch", spacing=6)
        self.body.addWidget(card(self.form_title, self.badges, fw,
                                 hbox(self.btn_save, self.btn_cancel_ev, "stretch", self.btn_delete)))

        # ---- detail tabs
        self.tabs = QTabWidget()
        self.tabs.setMinimumHeight(430)
        # registrations
        self.regs = DataTable()
        rt = QWidget()
        rl = QVBoxLayout(rt)
        rl.addWidget(hbox(button("Add registration…", "Primary", self.add_registration),
                          button("New student…", None, self.new_student),
                          button("Cancel selected registration", None, self.cancel_registration), "stretch"))
        rl.addWidget(self.regs)
        self.tabs.addTab(rt, "Registrations")
        # forecast
        ft = QWidget()
        fl = QVBoxLayout(ft)
        self.fc_current = label("", "Secondary")
        self.fc_value = QComboBox()
        self.fc_value.addItems(["Clear", "Cloudy", "Rainy", "Unavailable"])
        self.fc_when = QDateTimeEdit(QDateTime.currentDateTime())
        self.fc_when.setCalendarPopup(True)
        self.fc_when.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.fc_value.currentTextChanged.connect(lambda v: self.fc_when.setEnabled(v != "Unavailable"))
        self.fc_hist = DataTable(filterable=False, searchable=False)
        fl.addWidget(self.fc_current)
        fl.addWidget(hbox(label("Forecast", wrap=False), self.fc_value, label("obtained at", wrap=False),
                          self.fc_when, button("Record forecast", "Primary", self.add_forecast), "stretch"))
        fl.addWidget(label("The model uses the most recent forecast. 'Unavailable' is stored as-is; no weather value "
                           "is guessed. Every forecast keeps the time it was obtained.", "Muted"))
        fl.addWidget(self.fc_hist)
        self.tabs.addTab(ft, "Weather forecast")
        # actual attendance
        at = QWidget()
        al = QVBoxLayout(at)
        self.m_per = QRadioButton("Per student")
        self.m_head = QRadioButton("Head count only")
        self.m_per.setChecked(True)
        self.head = QSpinBox()
        self.head.setRange(0, 100000)
        self.walk = QSpinBox()
        self.walk.setRange(0, 100000)
        self.att_table = QTableWidget(0, 3)
        self.att_table.setHorizontalHeaderLabels(["Student", "Attended", "Recorded"])
        self.att_table.horizontalHeader().setStretchLastSection(True)
        self.att_table.verticalHeader().setVisible(False)
        self.m_per.toggled.connect(lambda on: (self.att_table.setEnabled(on), self.head.setEnabled(not on)))
        self.head.setEnabled(False)
        self.eval_text = label("", "Secondary")
        al.addWidget(hbox(self.m_per, self.m_head, label("Head count", wrap=False), self.head,
                          label("Walk-ins (not registered)", wrap=False), self.walk, "stretch"))
        al.addWidget(self.att_table)
        al.addWidget(hbox(button("Save attendance & compare with predictions", "Primary", self.save_attendance),
                          "stretch"))
        al.addWidget(self.eval_text)
        self.tabs.addTab(at, "Actual attendance")
        # statistics
        self.stats_chart = ChartView(min_height=280)
        self.eval_table = DataTable(filterable=False)
        st = QWidget()
        sl = QVBoxLayout(st)
        sl.addWidget(self.stats_chart)
        sl.addWidget(self.eval_table)
        self.tabs.addTab(st, "Participation && evaluation")
        self.body.addWidget(card(self.tabs, margins=12))
        self.new_event()

    # ------------------------------------------------------------------ data
    @property
    def db(self):
        return self.ctx.db

    def refresh(self):
        ev = pd.DataFrame(EventRepository(self.db).list())
        cols = ["event_id", "name", "event_type", "event_date", "event_day", "time_slot", "status", "registrations",
                "recorded_attended", "expected_attendance_active_model", "data_source"]
        self.table.set_frame(ev[cols] if len(ev) else pd.DataFrame(columns=cols),
                             formats={"expected_attendance_active_model": lambda v: f"{v:.1f}",
                                      "data_source": lambda v: DATA_SOURCE_LABELS.get(v, v)},
                             tones={"status": status_tone, "data_source": source_tone})
        if self.current and EventRepository(self.db).exists(self.current):
            self.load_event(self.current)
        else:
            self.new_event()

    def _select_row(self, row: int):
        rec = self.table.model.frame.iloc[row]
        self.load_event(rec["event_id"])

    def _date_toggle(self, on: bool):
        self.f_date.setEnabled(on)
        self.f_day.setEnabled(not on)

    def new_event(self):
        self.current = None
        self.form_title.setText("New event")
        self.f_id.setEnabled(True)
        n = EventRepository(self.db).count() + 1
        self.f_id.setText(f"EV{n:02d}" if not EventRepository(self.db).exists(f"EV{n:02d}") else "")
        self.f_name.clear()
        self.f_has_date.setChecked(True)
        self.f_date.setDate(QDate.currentDate().addDays(7))
        self.f_status.setCurrentText("Scheduled")
        self.f_venue.clear()
        self.f_has_dl.setChecked(False)
        for b in (self.btn_cancel_ev, self.btn_delete):
            b.setEnabled(False)
        self.tabs.setEnabled(False)
        self.b_status.set("New · will be saved as Scheduled", "blue")
        self.b_source.set("Entered in app", "blue")
        self.b_regs.set("")
        self.b_outcome.set("")

    def load_event(self, event_id: str):
        e = EventRepository(self.db).get(event_id)
        self.current = event_id
        self.form_title.setText(f"Edit {event_id}")
        self.f_id.setText(event_id)
        self.f_id.setEnabled(False)
        self.f_name.setText(e["name"] or "")
        self.f_type.setCurrentText(e["event_type"])
        self.f_has_date.setChecked(bool(e["event_date"]))
        if e["event_date"]:
            self.f_date.setDate(QDate.fromString(e["event_date"], "yyyy-MM-dd"))
        self.f_day.setCurrentText(e["event_day"])
        self.f_slot.setCurrentText(e["time_slot"])
        self.f_has_time.setChecked(bool(e["start_time"]))
        if e["start_time"]:
            self.f_time.setTime(QTime.fromString(e["start_time"][:5], "HH:mm"))
        self.f_dur.setValue(e["duration_hours"])
        self.f_venue.setText(e["venue"] or "")
        self.f_cap.setValue(e["capacity"] or 0)
        self.f_pop.setValue(int(round(e["popularity"])) if e["popularity"] is not None else -1)
        self.f_status.setCurrentText(e["status"])
        dl = e.get("registration_deadline")
        self.f_has_dl.setChecked(bool(dl))
        if dl:
            self.f_dl.setDate(QDate.fromString(dl, "yyyy-MM-dd"))
        for b in (self.btn_cancel_ev, self.btn_delete):
            b.setEnabled(True)
        self.tabs.setEnabled(True)
        self.b_status.set(e["status"], status_tone(e["status"]) or "neutral")
        self.b_source.set(DATA_SOURCE_LABELS.get(e["data_source"], e["data_source"]),
                          source_tone(e["data_source"]) or "neutral")
        self._load_details()

    def _form_values(self) -> dict:
        v = dict(name=self.f_name.text().strip() or None, event_type=self.f_type.currentText(),
                 time_slot=self.f_slot.currentText(), duration_hours=self.f_dur.value(),
                 venue=self.f_venue.text().strip() or None, capacity=self.f_cap.value() or None,
                 popularity=self.f_pop.value() if self.f_pop.value() >= 0 else None,
                 status=self.f_status.currentText(),
                 start_time=self.f_time.time().toString("HH:mm") if self.f_has_time.isChecked() else None,
                 registration_deadline=self.f_dl.date().toString("yyyy-MM-dd") if self.f_has_dl.isChecked() else None)
        if self.f_has_date.isChecked():
            v["event_date"] = self.f_date.date().toString("yyyy-MM-dd")
        else:
            v["event_date"] = None
            v["event_day"] = self.f_day.currentText()
        return v

    def save_event(self):
        repo = EventRepository(self.db)
        try:
            v = self._form_values()
            if self.current:
                repo.update(self.current, **v)
                eid = self.current
            else:
                eid = self.f_id.text().strip()
                if not eid:
                    raise DatabaseError("Enter an Event ID.")
                repo.create(event_id=eid, **{k: x for k, x in v.items() if x is not None})
        except DatabaseError as exc:
            show_error(self, "Cannot save event", str(exc))
            return
        self.current = eid
        self.ctx.status.emit(f"Saved {eid}")
        self.ctx.notify("events")

    def cancel_event(self):
        if self.current and confirm(self, "Cancel event", f"Mark {self.current} as cancelled? Its history is kept.",
                                    "Mark cancelled"):
            EventRepository(self.db).set_status(self.current, "Cancelled")
            self.ctx.notify("events")

    def delete_event(self):
        if not self.current:
            return
        repo = EventRepository(self.db)
        counts = repo.history_counts(self.current)
        if not confirm(self, "Delete event", f"Delete {self.current} and its {counts['registrations']} "
                                             "registration(s)? This cannot be undone.", "Delete", danger=True):
            return
        try:
            repo.delete(self.current)
        except HistoryProtectedError as exc:
            if not confirm(self, "Event has history", f"{exc}\n\nDelete the event AND all of its prediction, "
                                                      "attendance and evaluation history permanently?",
                           "Delete everything", danger=True):
                return
            repo.delete(self.current, include_history=True)
        self.ctx.status.emit(f"Deleted {self.current}")
        self.current = None
        self.ctx.notify("all")

    # ------------------------------------------------------------------ detail tabs
    def _load_details(self):
        eid = self.current
        th = theme.current().chart
        regs = pd.DataFrame(self.db.query(
            "SELECT r.registration_id, r.student_id, c.department, s.year, r.registration_days_before, "
            "r.has_exam_near_event, r.status, a.attended, lp.probability, lp.predicted_attend FROM registrations r "
            "JOIN students s USING(student_id) JOIN courses c USING(course_id) "
            "LEFT JOIN attendance_records a USING(registration_id) "
            "LEFT JOIN (SELECT p.* FROM v_latest_predictions p JOIN model_versions mv USING(model_version_id) "
            "WHERE mv.is_active = 1) lp ON lp.registration_id = r.registration_id WHERE r.event_id = ? "
            "ORDER BY r.student_id", (eid,)))
        self.regs.set_frame(regs, formats={"probability": lambda v: f"{v:.3f}",
                                           "attended": lambda v: "attended" if v == 1 else "absent",
                                           "predicted_attend": lambda v: "Will attend" if v == 1 else "Will not attend"},
                            tones={"status": status_tone, "attended": lambda v: outcome_tone(
                                "attended" if v == 1 else "absent"),
                                   "predicted_attend": lambda v: outcome_tone("Will attend" if v == 1 else "Will not")})
        n_active = int((regs["status"] == "Registered").sum()) if len(regs) else 0
        self.b_regs.set(f"{n_active} active registration{'s' if n_active != 1 else ''}", "purple")
        src = self.db.query_one("SELECT COUNT(*) n, SUM(attended) a, MIN(source) s FROM attendance_records a "
                                "JOIN registrations r USING(registration_id) WHERE r.event_id = ?", (eid,))
        if src and src["n"]:
            kind = ATTENDANCE_SOURCE_LABELS.get(src["s"], src["s"])
            self.b_outcome.set(f"{kind}: {int(src['a'] or 0)} of {src['n']} attended",
                               "gold" if src["s"] in ("ORIGINAL_SIMULATED", "SYNTHETIC") else "good")
        else:
            self.b_outcome.set("No attendance recorded yet", "neutral")
        latest = ForecastRepository(self.db).latest(eid)
        self.fc_current.setText(f"Current forecast: {latest['forecast']} (obtained "
                                f"{latest['forecast_obtained_at'] or 'unknown'}, source {latest['source']})"
                                if latest else "No forecast recorded - predictions use 'Unavailable'.")
        self.fc_hist.set_frame(pd.DataFrame(ForecastRepository(self.db).history(eid)))
        active = regs[regs["status"] == "Registered"] if len(regs) else regs
        self.att_table.setRowCount(len(active))
        for i, r in enumerate(active.itertuples()):
            self.att_table.setItem(i, 0, QTableWidgetItem(r.student_id))
            it = QTableWidgetItem()
            it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            it.setCheckState(Qt.Checked if r.attended == 1 else Qt.Unchecked)
            self.att_table.setItem(i, 1, it)
            rec = "—" if pd.isna(r.attended) else ("attended" if r.attended == 1 else "absent")
            self.att_table.setItem(i, 2, QTableWidgetItem(rec))
        out = AttendanceRepository(self.db).outcome(eid)
        if out:
            self.head.setValue(out["actual_attendees"])
            self.walk.setValue(out["walk_ins"])
        evals = pd.DataFrame(EvaluationRepository(self.db).for_event(eid))
        cols = ["computed_utc", "model_name", "model_version", "method", "expected_attendance", "actual_attendees",
                "error", "percentage_error", "accuracy", "brier", "target_is_simulated"]
        self.eval_table.set_frame(evals[cols] if len(evals) else pd.DataFrame(columns=cols),
                                  formats={"expected_attendance": lambda v: f"{v:.1f}", "error": lambda v: f"{v:+.1f}",
                                           "percentage_error": lambda v: f"{v:.1f}%",
                                           "accuracy": lambda v: f"{v:.1%}", "brier": lambda v: f"{v:.3f}",
                                           "target_is_simulated": lambda v: "yes" if v else "no"})
        if len(evals):
            e0 = evals.iloc[0]
            self.stats_chart.set_figure(C.grouped_bar(
                [eid], {"Expected (model)": [e0["expected_attendance"]], "Actual": [e0["actual_attendees"]]},
                "Predicted vs actual attendance", th,
                subtitle="Simulated outcome" if e0["target_is_simulated"] else None))
        else:
            n_reg = int((regs["status"] == "Registered").sum()) if len(regs) else 0
            exp = float(regs["probability"].sum()) if len(regs) and regs["probability"].notna().any() else 0.0
            self.stats_chart.set_figure(C.bar(["Active registrations", "Expected (active model)"], [n_reg, exp],
                                              f"{eid}: registrations and expected attendance", th))

    def add_registration(self):
        if not self.current:
            return
        students = [r["student_id"] for r in self.db.query("SELECT student_id FROM students ORDER BY student_id")]
        ev = EventRepository(self.db).get(self.current)
        dlg = RegistrationDialog(self, students, ev["event_date"])
        if dlg.exec():
            v = dlg.values()
            try:
                RegistrationRepository(self.db).register(event_id=self.current, **v)
            except DatabaseError as exc:
                show_error(self, "Cannot register", str(exc))
                return
            self.ctx.notify("events")

    def new_student(self):
        n = StudentRepository(self.db).count() + 1
        dlg = StudentDialog(self, f"STU{n:03d}")
        while dlg.exec():
            try:
                StudentRepository(self.db).create(**dlg.values())
            except DatabaseError as exc:
                dlg.err.set(str(exc), "error")
                continue
            self.ctx.status.emit(f"Created student {dlg.values()['student_id']}")
            self.ctx.notify("events")
            break

    def cancel_registration(self):
        rec = self.regs.selected_record()
        if not rec:
            show_error(self, "No registration selected", "Select a registration in the table first.")
            return
        RegistrationRepository(self.db).set_status(int(rec["registration_id"]), "Cancelled")
        self.ctx.notify("events")

    def add_forecast(self):
        if not self.current:
            return
        val = self.fc_value.currentText()
        when = None
        if val != "Unavailable":
            when = self.fc_when.dateTime().toUTC().toString("yyyy-MM-ddTHH:mm:ssZ")
        try:
            ForecastRepository(self.db).add(self.current, val, when, source="MANUAL")
        except DatabaseError as exc:
            show_error(self, "Cannot record forecast", str(exc))
            return
        self.ctx.notify("events")

    def save_attendance(self):
        if not self.current:
            return
        try:
            if self.m_per.isChecked():
                att = {self.att_table.item(i, 0).text(): int(self.att_table.item(i, 1).checkState() == Qt.Checked)
                       for i in range(self.att_table.rowCount())}
                if not att:
                    raise DatabaseError("This event has no active registrations.")
                res = services.record_actual_attendance(self.db, self.current, attended=att,
                                                        walk_ins=self.walk.value(), recorded_by="admin")
            else:
                res = services.record_actual_attendance(self.db, self.current, headcount=self.head.value(),
                                                        walk_ins=self.walk.value(), recorded_by="admin")
        except DatabaseError as exc:
            show_error(self, "Cannot record attendance", str(exc))
            return
        if res:
            lines = [f"{r['expected_attendance']:.1f} expected vs {r['actual_attendees']} actual → error "
                     f"{r['error']:+.1f} ({r['percentage_error']:.1f}%)" if r["percentage_error"] is not None else
                     f"{r['expected_attendance']:.1f} expected vs {r['actual_attendees']} actual" for r in res]
            self.eval_text.setText("Saved. " + " · ".join(lines) +
                                   (" — SIMULATED outcomes." if any(r["target_is_simulated"] for r in res) else ""))
        else:
            self.eval_text.setText("Saved. No predictions made before this event were found, so there is nothing to "
                                   "compare. Predict events before they take place.")
        self.ctx.notify("predictions")
