"""Attendance Prediction: individual (with explanation), whole event, and bulk file."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QLabel,
                               QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from config import settings
from core import charts as C
from core.data_loader import DataImportError, load_table
from core.data_validation import validate
from core.db import services
from core.db.errors import DatabaseError
from core.db.repositories import ModelVersionRepository
from core.prediction import PredictionInputError
from core.reports import export_frame
from core.schema import AREA_EVENT_FIT, COURSE_TO_DEPARTMENT, EXPERIENCE_ORDER, MODEL_INPUTS, ROLE_EVENT_FIT
from gui import theme
from gui.pages.base import Page
from gui.widgets import (ChartView, DataTable, KpiCard, Notice, ResponsiveGrid, button, card, hbox, label,
                         outcome_tone, run_task, show_error)

LIMITATIONS = ("How to read this: the probability is a planning estimate from a model trained on SIMULATED "
               "attendance outcomes (cross-validated ROC-AUC about 0.75-0.80 on unseen students). Factors compare "
               "this entry with a typical training value one input at a time - they describe the model's behaviour, "
               "not causes, and effects are not additive.")


class PredictionPage(Page):
    title = "Attendance Prediction"
    subtitle = ("Predict one registration, every registration of an event, or a whole uploaded file. Every prediction "
                "is stored with its probability, model version and time.")
    areas = ("events", "models", "predictions", "datasets")

    def build(self):
        self.model_box = QComboBox()
        self.model_box.setMinimumWidth(300)
        self.model_box.setToolTip("Predictions are stored with the model version used. The active model is the "
                                  "default; choose another version to compare.")
        ml = QLabel("Model")
        ml.setObjectName("Muted")
        self.header_actions.addWidget(ml)
        self.header_actions.addWidget(self.model_box)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_individual(), "Individual")
        self.tabs.addTab(self._build_event(), "Whole event")
        self.tabs.addTab(self._build_bulk(), "Bulk file")
        from gui.pages.whatif import WhatIfPanel
        self.whatif = WhatIfPanel(self)
        self.tabs.addTab(self.whatif, "What-if planner")
        self.body.addWidget(self.tabs)
        self.bulk_result: pd.DataFrame | None = None

    # ================================================================== individual
    def _build_individual(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        self.i_student = QComboBox()
        self.i_student.setEditable(True)
        self.i_event = QComboBox()
        self.i_student.currentTextChanged.connect(self._fill_from_db)
        self.i_event.currentTextChanged.connect(self._fill_from_db)
        self.f = {}
        S = lambda lo, hi, v: self._spin(lo, hi, v)  # noqa: E731
        fields = [
            ("Course", self._combo(sorted(COURSE_TO_DEPARTMENT))), ("Year", S(1, 5, 1)),
            ("Currently_UBA_Volunteer", QCheckBox()), ("Participated_UBA_Event_Before", QCheckBox()),
            ("Experience_Level", self._combo(EXPERIENCE_ORDER)), ("Area_Of_Interest", self._combo(list(AREA_EVENT_FIT))),
            ("Role", self._combo(list(ROLE_EVENT_FIT))),
            ("Previous_Event_Attendance", S(0, 500, 3)), ("Previous_Events_Registered", S(0, 500, 5)),
            ("Academic_Engagement_Score", S(0, 100, 60)), ("Distance_From_Venue_KM", self._dspin(0, 200, 3.0)),
            ("Event_Type", self._combo(["Technical", "Workshop", "Cultural", "Seminar", "Sports"])),
            ("Event_Day", self._combo(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])),
            ("Event_Time", self._combo(["Morning", "Afternoon", "Evening"])),
            ("Event_Duration_Hours", self._dspin(0.5, 12, 2.0)), ("Event_Popularity", S(0, 100, 60)),
            ("Registration_Days_Before", S(0, 365, 7)), ("Has_Exam_Near_Event", QCheckBox()),
            ("Weather_Forecast", self._combo(["Clear", "Cloudy", "Rainy", "Unavailable"])),
        ]
        g = QGridLayout()
        g.setHorizontalSpacing(18)
        forms = [QFormLayout(), QFormLayout()]
        for i, (name, wdg) in enumerate(fields):
            self.f[name] = wdg
            forms[0 if i < 11 else 1].addRow(name.replace("_", " "), wdg)    # student | event & registration
        for i, fl in enumerate(forms):
            g.addLayout(fl, 0, i)
        gw = QWidget()
        gw.setLayout(g)
        src = hbox(label("Student", wrap=False), (self.i_student, 1), label("Event", wrap=False), (self.i_event, 1),
                   button("Predict attendance", "Primary", self.predict_individual))
        lay.addWidget(card(src, gw, eyebrow="Step 1", title="Choose a registration or enter details",
                           subtitle="Pick a registered student and event to fill the form from the database, or edit "
                                    "any field for a what-if prediction. Student details are pseudonymous."))
        self.r_prob = KpiCard("Attendance probability", tone="purple", icon="prediction")
        self.r_out = KpiCard("Predicted outcome", tone="neutral", icon="check")
        self.r_cert = KpiCard("Certainty", tone="blue", icon="info",
                              tooltip="How far the probability is from the decision threshold: Clear, Moderate or "
                                      "Uncertain.")
        self.r_note = Notice()
        self.r_up = label("Run a prediction to see which inputs raised the probability.", "Secondary")
        self.r_down = label("", "Secondary")
        up_card = card(self.r_up, title="Raised the probability", name="SubtleCard", margins=16)
        down_card = card(self.r_down, title="Lowered the probability", name="SubtleCard", margins=16)
        self.r_factors = ChartView(min_height=280)
        lay.addWidget(card(ResponsiveGrid([self.r_prob, self.r_out, self.r_cert], min_width=170, allowed=(3, 1),
                                          spacing=12),
                           ResponsiveGrid([up_card, down_card], min_width=300, allowed=(2, 1), spacing=12),
                           self.r_factors, self.r_note, eyebrow="Step 2", title="Prediction and explanation",
                           subtitle="A prediction, not recorded attendance. Factors show how much each input moves "
                                    "the probability compared with a typical training value (what-if analysis)."))
        return w

    def _combo(self, items):
        c = QComboBox()
        c.addItems(items)
        return c

    def _spin(self, lo, hi, v):
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(v)
        return s

    def _dspin(self, lo, hi, v):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(1)
        s.setValue(v)
        return s

    def _record(self) -> dict:
        out = {}
        for k, w in self.f.items():
            if isinstance(w, QCheckBox):
                out[k] = int(w.isChecked())
            elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                out[k] = w.value()
            else:
                out[k] = w.currentText()
        if out["Previous_Events_Registered"] > 0:
            out["Previous_Attendance_Rate"] = round(out["Previous_Event_Attendance"] /
                                                    out["Previous_Events_Registered"] * 100, 1)
        s, e = self.i_student.currentText().strip(), self.i_event.currentText().strip()
        if s:
            out["Student_ID"] = s
        if e and e != "(none)":
            out["Event_ID"] = e
        return out

    def _fill_from_db(self, *_):
        s, e = self.i_student.currentText().strip(), self.i_event.currentText().strip()
        row = None
        if s and e and e != "(none)":
            row = self.ctx.db.query_one("SELECT * FROM v_registration_features WHERE Student_ID = ? AND Event_ID = ?",
                                        (s, e))
        if row is None:
            st = self.ctx.db.query_one("SELECT s.*, c.name AS course FROM students s JOIN courses c USING(course_id) "
                                       "WHERE student_id = ?", (s,)) if s else None
            ev = self.ctx.db.query_one("SELECT e.*, COALESCE((SELECT forecast FROM v_latest_forecast f WHERE "
                                       "f.event_id = e.event_id), 'Unavailable') AS fc FROM events e WHERE event_id = ?",
                                       (e,)) if e else None
            row = {}
            if st:
                row.update({"Course": st["course"], "Year": st["year"],
                            "Currently_UBA_Volunteer": st["currently_uba_volunteer"],
                            "Participated_UBA_Event_Before": st["participated_uba_event_before"],
                            "Experience_Level": st["experience_level"], "Area_Of_Interest": st["area_of_interest"],
                            "Role": st["role"], "Previous_Event_Attendance": st["previous_event_attendance"],
                            "Previous_Events_Registered": st["previous_events_registered"],
                            "Academic_Engagement_Score": st["academic_engagement_score"],
                            "Distance_From_Venue_KM": st["distance_from_venue_km"]})
            if ev:
                row.update({"Event_Type": ev["event_type"], "Event_Day": ev["event_day"], "Event_Time": ev["time_slot"],
                            "Event_Duration_Hours": ev["duration_hours"], "Event_Popularity": ev["popularity"],
                            "Weather_Forecast": ev["fc"]})
        for k, v in row.items():
            w = self.f.get(k)
            if w is None or v is None:
                continue
            if isinstance(w, QCheckBox):
                w.setChecked(bool(v))
            elif isinstance(w, QSpinBox):
                w.setValue(int(round(float(v))))
            elif isinstance(w, QDoubleSpinBox):
                w.setValue(float(v))
            else:
                if w.findText(str(v)) < 0:
                    w.addItem(str(v))
                w.setCurrentText(str(v))

    def predict_individual(self):
        rec = self._record()
        try:
            r = services.predict_record(self.ctx.db, rec, self.selected_model_id())
        except (DatabaseError, PredictionInputError, ValueError) as exc:
            show_error(self, "Prediction failed", str(exc))
            return
        p = theme.current()
        self.r_prob.set(f"{r['probability']:.1%}", f"decision threshold {r['threshold']:.2f}")
        self.r_out.set("Will attend" if r["predicted_attend"] else "Will not attend", r["model"],
                       tone="good" if r["predicted_attend"] else "gold")
        self.r_cert.set(r["certainty"], "distance of the probability from the threshold")
        msg = LIMITATIONS
        if r["forecast_unavailable"]:
            msg = "No weather forecast was used ('Unavailable'). " + msg
        self.r_note.set(msg, "info")
        fac = r["factors"]
        ups = [f for f in fac if f.effect > 0][:4]
        downs = [f for f in fac if f.effect < 0][:4]
        self.r_up.setText("\n".join(f"• {f.text()}" for f in ups) or "No input raised the probability noticeably.")
        self.r_down.setText("\n".join(f"• {f.text()}" for f in downs) or "No input lowered the probability noticeably.")
        if fac:
            labels_ = [f"{f.label} = {f.value}" for f in fac][::-1]
            vals = [f.effect for f in fac][::-1]
            fig = C.bar(labels_, vals, "Main contributing factors (probability change vs typical)", p.chart,
                        horizontal=True, value_labels=False, ylabel="Change in probability",
                        size=(6.4, max(2.6, 0.42 * len(fac) + 1.2)))
            ax = fig.axes[0]
            for patch, v in zip(ax.patches, vals):
                patch.set_color(p.chart.series[0] if v > 0 else p.chart.series[1])
            ax.axvline(0, color=p.chart.text_secondary, linewidth=0.8)
            self.r_factors.set_figure(fig)
        else:
            self.r_factors.set_figure(C.bar([], [], "Every input is at its typical value", p.chart))
        self.ctx.notify("predictions")

    # ================================================================== event
    def _build_event(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        self.e_event = QComboBox()
        self.e_k = [KpiCard("Registrations", tone="blue", icon="students"),
                    KpiCard("Expected attendees", tone="purple", icon="check",
                            tooltip="Sum of the attendance probabilities, with an approximate 95% range."),
                    KpiCard("Expected no-shows", tone="gold", icon="cross",
                            tooltip="Registrations minus expected attendees."),
                    KpiCard("Predicted to attend", tone="good", icon="prediction",
                            tooltip="Registrations at or above the decision threshold.")]
        self.e_hist = ChartView()
        self.e_table = DataTable()
        self.e_table.setMinimumHeight(300)
        self.e_btn = button("Predict all registrations", "Primary", self.predict_event)
        self.e_actual = Notice()
        lay.addWidget(card(hbox(label("Event", wrap=False), (self.e_event, 1), self.e_btn),
                           label("Uses each student's stored profile, the event details and its latest forecast. "
                                 "Results are stored and appear on the Overview and in Event Management.", "Muted"),
                           title="Predict a whole event"))
        lay.addWidget(ResponsiveGrid(self.e_k, min_width=150, allowed=(4, 2), spacing=14))
        lay.addWidget(self.e_actual)
        lay.addWidget(card(self.e_hist, title="Probability distribution"))
        lay.addWidget(card(self.e_table, title="Predictions"))
        return w

    def predict_event(self):
        eid = self.e_event.currentText().split("  ")[0]
        if not eid:
            return
        mv_id = self.selected_model_id()
        self.e_btn.setEnabled(False)

        def work():
            with self.ctx.open_db() as db:
                s = services.predict_event(db, eid, mv_id)
                rows = pd.DataFrame(db.query(
                    "SELECT r.student_id, p.probability, p.predicted_attend, p.certainty, p.forecast_used "
                    "FROM predictions p JOIN registrations r USING(registration_id) WHERE p.run_id = ? "
                    "ORDER BY p.probability DESC", (s["run_id"],)))
                return s, rows

        def done(res):
            s, rows = res
            self.e_btn.setEnabled(True)
            lo, hi = s["expected_attendance_range_95"]
            n = s["registrations"]
            self.e_k[0].set(f"{n}", f"active registrations · {s['model']}")
            self.e_k[1].set(f"{s['expected_attendance']:.1f}", f"approx. 95% range {lo:.0f}–{hi:.0f}")
            self.e_k[2].set(f"{n - s['expected_attendance']:.1f}", f"approx. range {max(n - hi, 0):.0f}–{n - lo:.0f}")
            self.e_k[3].set(f"{s['predicted_attendees']}", f"{s['predicted_non_attendees']} below threshold "
                                                          f"{s['threshold']:.2f}")
            self._show_recorded(eid)
            self.e_hist.set_figure(C.histogram(s["histogram"], [i / 10 for i in range(11)],
                                               f"{eid}: predicted probabilities", theme.current().chart,
                                               threshold=s["threshold"]))
            rows["predicted_attend"] = rows["predicted_attend"].map({1: "Will attend", 0: "Will not attend"})
            self.e_table.set_frame(rows, formats={"probability": lambda v: f"{v:.3f}"},
                                   tones={"predicted_attend": outcome_tone})
            self.ctx.status.emit(f"Stored {s['registrations']} predictions for {eid}")
            self.ctx.notify("predictions")

        def failed(m, d):
            self.e_btn.setEnabled(True)
            show_error(self, "Prediction failed", m, d)

        run_task(work, on_done=done, on_error=failed)

    # ================================================================== bulk
    def _build_bulk(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        self.b_file = label("No file selected.", "Muted")
        self.b_valid = Notice()
        self.b_k = [KpiCard("Rows predicted", tone="blue", icon="dataset"),
                    KpiCard("Expected attendees", tone="purple", icon="check"),
                    KpiCard("Predicted to attend", tone="good", icon="prediction"),
                    KpiCard("Predicted no-shows", tone="gold", icon="cross")]
        self.b_hist = ChartView()
        self.b_table = DataTable()
        self.b_table.setMinimumHeight(320)
        self.b_export_csv = button("Export CSV…", None, lambda: self.export_bulk("csv"))
        self.b_export_xlsx = button("Export Excel…", None, lambda: self.export_bulk("xlsx"))
        for b in (self.b_export_csv, self.b_export_xlsx):
            b.setEnabled(False)
        req = ", ".join(c for c in MODEL_INPUTS if c not in ("Previous_Attendance_Rate", "Weather_Forecast"))
        lay.addWidget(card(hbox(button("Choose file and predict…", "Primary", self.run_bulk), self.b_export_csv,
                                self.b_export_xlsx, "stretch"), self.b_file, self.b_valid,
                           label(f"Required columns: {req}. Optional: Student_ID, Event_ID, Previous_Attendance_Rate, "
                                 "Weather_Forecast (blank = 'Unavailable').", "Muted"), title="Predict from a file"))
        lay.addWidget(ResponsiveGrid(self.b_k, min_width=150, allowed=(4, 2), spacing=14))
        lay.addWidget(card(self.b_hist, title="Probability distribution"))
        lay.addWidget(card(self.b_table, title="Predictions"))
        return w

    def run_bulk(self, path: str | None = None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Choose file to predict", str(Path.home()),
                                                  "Data files (*.csv *.xlsx *.xlsm)")
        if not path:
            return
        try:
            loaded = load_table(path)
        except DataImportError as exc:
            show_error(self, "Cannot read file", str(exc))
            return
        rep = validate(loaded.df, "prediction")
        self.b_file.setText(f"{Path(path).name}: {len(loaded.df):,} rows")
        if not rep.ok:
            self.b_valid.set("The file failed validation:\n" + "\n".join(rep.errors), "error")
            return
        self.b_valid.set(f"Validation passed ({len(rep.warnings)} warning(s)): " + " ".join(rep.warnings[:3]), "ok")
        mv_id = self.selected_model_id()

        def work():
            with self.ctx.open_db() as db:
                return services.predict_file(db, path, mv_id)

        def done(res):
            out, s = res
            self.bulk_result = out
            lo, hi = s["expected_attendance_range_95"]
            self.b_k[0].set(f"{s['registrations']:,}")
            self.b_k[1].set(f"{s['expected_attendance']:,.1f}", f"approx. 95% range {lo:,.0f}–{hi:,.0f}")
            self.b_k[2].set(f"{s['predicted_attendees']:,}", f"probability ≥ {s['threshold']:.2f}")
            self.b_k[3].set(f"{s['predicted_non_attendees']:,}", "below the threshold")
            self.b_hist.set_figure(C.histogram(s["histogram"], [i / 10 for i in range(11)],
                                               "Predicted probabilities", theme.current().chart,
                                               threshold=s["threshold"]))
            show = [c for c in ("Student_ID", "Event_ID") if c in out.columns] + \
                   ["Attendance_Probability", "Predicted_Attend", "Prediction_Certainty", "Forecast_Used"]
            shown = out[show].copy()
            shown["Predicted_Attend"] = shown["Predicted_Attend"].map({1: "Will attend", 0: "Will not attend"})
            self.b_table.set_frame(shown, formats={"Attendance_Probability": lambda v: f"{v:.3f}"},
                                   tones={"Predicted_Attend": outcome_tone})
            for b in (self.b_export_csv, self.b_export_xlsx):
                b.setEnabled(True)
            self.ctx.notify("predictions")

        run_task(work, on_done=done, on_error=lambda m, d: show_error(self, "Bulk prediction failed", m, d))

    def export_bulk(self, kind: str, path: str | None = None):
        if self.bulk_result is None:
            return
        if not path:
            default = str(settings.EXPORTS_DIR / f"bulk_predictions.{kind}")
            path, _ = QFileDialog.getSaveFileName(self, "Export predictions", default,
                                                  "CSV (*.csv)" if kind == "csv" else "Excel (*.xlsx)")
        if not path:
            return
        out = export_frame(self.bulk_result, path, "Predictions")
        self.notice.set(f"Predictions exported to {out}", "ok")

    # ================================================================== refresh
    def selected_model_id(self) -> int | None:
        """None = the active model."""
        v = self.model_box.currentData()
        return int(v) if v else None

    def _show_recorded(self, eid: str) -> None:
        """Recorded attendance for the event, shown apart from the prediction and labelled by source."""
        r = self.ctx.db.query_one(
            "SELECT COUNT(a.attendance_id) n, SUM(a.attended) a, SUM(a.source IN ('ORIGINAL_SIMULATED','SYNTHETIC')) s "
            "FROM registrations r LEFT JOIN attendance_records a USING(registration_id) "
            "WHERE r.event_id = ? AND r.status = 'Registered'", (eid,))
        head = self.ctx.db.query_one("SELECT method, actual_attendees, walk_ins FROM event_outcomes WHERE event_id = ?",
                                     (eid,))
        if r and r["n"]:
            if r["s"]:
                self.e_actual.set(f"Recorded outcome for {eid}: {int(r['a'] or 0)} of {r['n']} attended - SIMULATED "
                                  "by the dataset, not observed. The figures above are predictions.", "info")
            else:
                self.e_actual.set(f"Recorded attendance for {eid}: {int(r['a'] or 0)} of {r['n']} registered "
                                  "students attended (entered after the event). The figures above are predictions; "
                                  "compare them on the Analytics page.", "note")
        elif head:
            self.e_actual.set(f"Recorded head count for {eid}: {head['actual_attendees']} registered attendees"
                              f" + {head['walk_ins']} walk-ins. The figures above are predictions.", "note")
        else:
            self.e_actual.set(f"No attendance recorded for {eid} yet - these are predictions only. Record actual "
                              "attendance in Event Management after the event.", "note")

    def refresh(self):
        db = self.ctx.db
        if not ModelVersionRepository(db).active():
            self.notice.set("No active model. Train or activate one in the Model Laboratory first.", "error")
        else:
            self.notice.set("")
        cur_model = self.model_box.currentData()
        self.model_box.blockSignals(True)
        self.model_box.clear()
        active = ModelVersionRepository(db).active()
        self.model_box.addItem(f"Active: {active['name']} v{active['version']}" if active else "Active model",
                               None)
        for mv in ModelVersionRepository(db).list():
            if active and mv["model_version_id"] == active["model_version_id"]:
                continue
            self.model_box.addItem(f"{mv['name']} v{mv['version']}", mv["model_version_id"])
        i = self.model_box.findData(cur_model) if cur_model else 0
        self.model_box.setCurrentIndex(max(i, 0))
        self.model_box.blockSignals(False)
        cur_s, cur_e = self.i_student.currentText(), self.i_event.currentText()
        self.i_student.blockSignals(True)
        self.i_event.blockSignals(True)
        self.i_student.clear()
        self.i_student.addItems([""] + [r["student_id"] for r in db.query("SELECT student_id FROM students "
                                                                         "ORDER BY student_id")])
        events = [r["event_id"] for r in db.query("SELECT event_id FROM events ORDER BY event_id")]
        self.i_event.clear()
        self.i_event.addItems(["(none)"] + events)
        self.i_student.setCurrentText(cur_s)
        self.i_event.setCurrentText(cur_e or "(none)")
        self.i_student.blockSignals(False)
        self.i_event.blockSignals(False)
        self.whatif.refresh(db)
        cur = self.e_event.currentText()
        self.e_event.clear()
        for r in db.query("SELECT event_id, event_type, status, registrations FROM v_event_participation "
                          "ORDER BY event_id"):
            self.e_event.addItem(f"{r['event_id']}  ·  {r['event_type']} · {r['status']} · "
                                 f"{r['registrations']} registrations")
        if cur:
            self.e_event.setCurrentText(cur)
