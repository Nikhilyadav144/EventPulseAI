"""Analytics: what the data shows - participation, trends, predicted vs actual, model performance.
(Derived observations live on the separate Insights page.)"""
from __future__ import annotations

import pandas as pd
from PySide6.QtWidgets import QCheckBox, QComboBox

from core import analytics as A
from core import charts as C
from gui import theme
from gui.pages.base import Page
from gui.widgets import ChartView, DataTable, KpiCard, ResponsiveGrid, card, hbox, label, yes_tone

DIMENSIONS = {"Department": "Department", "Academic year": "Year", "Event category": "Event_Type",
              "Event day": "Event_Day", "Time slot": "Event_Time", "Weather forecast": "Weather_Forecast"}
SOURCES = {"All data sources": None, "Original dataset": "ORIGINAL_HYBRID", "Synthetic demo": "SYNTHETIC",
           "Entered in app": "MANUAL", "Uploaded file": "UPLOAD"}


class AnalyticsPage(Page):
    title = "Analytics"
    subtitle = ("What the data shows: participation by group, event-wise trends, predicted versus actual attendance, "
                "prediction error and model performance. Every figure names its data source.")
    areas = ("events", "predictions", "models", "datasets")

    def build(self):
        self.dim = QComboBox()
        self.dim.addItems(list(DIMENSIONS))
        self.dim.currentTextChanged.connect(lambda _: self.draw_dimension())
        self.src = QComboBox()
        self.src.addItems(list(SOURCES))
        self.src.setToolTip("Limit participation figures to one data source.")
        self.dim.setMinimumWidth(170)
        self.src.setMinimumWidth(180)
        self.src.currentTextChanged.connect(lambda _: self.draw_dimension())
        self.d_regs, self.d_rate = ChartView(), ChartView()
        self.d_table = DataTable(filterable=False, searchable=False)
        self.d_table.setMinimumHeight(200)
        self.body.addWidget(card(hbox(label("Group by", wrap=False), self.dim, label("Source", wrap=False), self.src,
                                      "stretch"),
                                 ResponsiveGrid([self.d_regs, self.d_rate], min_width=360, allowed=(2, 1)),
                                 self.d_table, title="Department-wise and group participation",
                                 subtitle="Registrations and recorded attendance rate per group. Rates come from "
                                          "recorded outcomes, which are simulated for the imported dataset."))
        self.trend = ChartView(min_height=300)
        self.body.addWidget(card(self.trend, title="Event-wise analysis and attendance trend",
                                 subtitle="Dated events are shown over time; undated imported events by ID."))

        self.e_k = [KpiCard("Events evaluated", tone="blue", icon="events"),
                    KpiCard("Mean error", tone="purple", icon="spark",
                            tooltip="Expected minus actual attendees, averaged (negative = under-predicted)."),
                    KpiCard("Mean absolute error", tone="gold", icon="curve"),
                    KpiCard("Mean % error", tone="gold", icon="analytics")]
        self.include_sim = QCheckBox("Include simulated outcomes")
        self.include_sim.setChecked(True)
        self.include_sim.setToolTip("Untick to keep only attendance recorded after real events.")
        self.include_sim.toggled.connect(lambda _: self.draw_pva())
        self.pva = ChartView(min_height=300)
        self.pva_table = DataTable(filterable=False)
        self.pva_table.setMinimumHeight(200)
        self.body.addWidget(card(self.include_sim, ResponsiveGrid(self.e_k, min_width=150, allowed=(4, 2), spacing=12),
                                 self.pva, self.pva_table,
                                 title="Predicted vs actual attendance",
                                 subtitle="Stored comparisons made when attendance is entered after an event "
                                          "(Event Management → Actual attendance). Error = expected − actual."))
        self.models_chart = ChartView(min_height=300)
        self.body.addWidget(card(self.models_chart, title="Model performance comparison",
                                 subtitle="Cross-validated ROC-AUC (primary estimate) next to the single holdout "
                                          "split for every registered model version."))
        self.prob, self.imp = ChartView(), ChartView(min_height=330)
        self.body.addWidget(ResponsiveGrid(
            [card(self.prob, title="Attendance probability distribution",
                  subtitle="Latest prediction per registration, active model."),
             card(self.imp, title="Feature importance (active model)",
                  subtitle="Permutation importance: drop in ROC-AUC when one input is shuffled.")],
            min_width=400, allowed=(2, 1)))

    def refresh(self):
        db = self.ctx.db
        th = theme.current().chart
        k = A.overview_kpis(db)
        self.notice.set(A.SIMULATED_NOTE if k.recorded_attendance_simulated else "", "info")
        self.draw_dimension()
        ev = A.event_trends(db)
        if len(ev) and ev["event_date"].notna().sum() >= 3:
            e = ev[ev["event_date"].notna()]
            self.trend.set_figure(C.line(e["event_date"], e["attendance_rate"], "Attendance rate over time", th,
                                         percent=True))
        else:
            self.trend.set_figure(C.bar(ev["event_id"] if len(ev) else [], ev["attendance_rate"] if len(ev) else [],
                                        "Recorded attendance rate by event (undated events, by ID)", th, percent=True,
                                        value_labels=False))
        self.draw_pva()
        d = A.probability_distribution(db)
        mc = A.model_comparison(db)
        thr = float(mc[mc["active"]]["threshold"].iloc[0]) if len(mc) and mc["active"].any() else None
        self.prob.set_figure(C.histogram(d["counts"], d["edges"], "Predicted probabilities", th, threshold=thr))
        imp = A.feature_importance(db)
        if len(mc):
            mc2 = mc.sort_values("cv_roc_auc", ascending=False)
            names = [f"{m} v{v}" for m, v in zip(mc2["model"], mc2["version"])]
            self.models_chart.set_figure(C.grouped_bar(
                names, {"CV ROC-AUC (5 folds)": mc2["cv_roc_auc"].fillna(0).tolist(),
                        "Holdout ROC-AUC": mc2["holdout_roc_auc"].fillna(0).tolist()},
                "ROC-AUC by model version", th))
        else:
            self.models_chart.set_figure(C.bar([], [], "ROC-AUC by model version", th))
        self.imp.set_figure(C.bar(imp["feature"], imp["mean"], "Permutation importance", th, horizontal=True,
                                  errors=imp["std"], value_labels=False, ylabel="ROC-AUC drop", size=(6.0, 4.2))
                            if len(imp) else C.bar([], [], "Feature importance", th))

    def draw_dimension(self):
        th = theme.current().chart
        dim = DIMENSIONS[self.dim.currentText()]
        df = A.participation_by(self.ctx.db, dim, SOURCES[self.src.currentText()])
        cats = df[dim].astype(str) if len(df) else []
        self.d_regs.set_figure(C.bar(cats, df["registrations"] if len(df) else [], f"Registrations by {self.dim.currentText().lower()}", th))
        self.d_rate.set_figure(C.bar(cats, df["attendance_rate"] if len(df) else [],
                                     f"Attendance rate by {self.dim.currentText().lower()}", th, percent=True, slot=2))
        self.d_table.set_frame(df, formats={"attendance_rate": lambda v: f"{v:.1%}"})

    def draw_pva(self):
        th = theme.current().chart
        inc = self.include_sim.isChecked()
        pva = A.predicted_vs_actual(self.ctx.db, include_simulated=inc)
        s = A.prediction_error_summary(self.ctx.db, include_simulated=inc)
        if s.get("events"):
            self.e_k[0].set(f"{s['events']}", "includes simulated" if s["includes_simulated"] else "real outcomes only")
            self.e_k[1].set(f"{s['mean_error']:+.1f}", "attendees (negative = under-predicted)")
            self.e_k[2].set(f"{s['mean_absolute_error']:.1f}", "attendees")
            self.e_k[3].set(f"{s['mean_percentage_error']:.1f}%" if s["mean_percentage_error"] is not None else "—")
            self.pva.set_figure(C.grouped_bar(pva["event_id"].tolist(),
                                              {"Expected": pva["expected_attendance"].tolist(),
                                               "Actual": pva["actual_attendees"].tolist()},
                                              "Expected vs actual attendees", th))
            cols = ["event_id", "model", "method", "expected_attendance", "actual_attendees", "error",
                    "percentage_error", "accuracy", "brier", "target_is_simulated", "computed_utc"]
            self.pva_table.set_frame(pva[cols], formats={"expected_attendance": lambda v: f"{v:.1f}",
                                                         "error": lambda v: f"{v:+.1f}",
                                                         "percentage_error": lambda v: f"{v:.1f}%",
                                                         "accuracy": lambda v: f"{v:.1%}",
                                                         "brier": lambda v: f"{v:.3f}",
                                                         "target_is_simulated": lambda v: "yes" if v else "no"},
                                   tones={"target_is_simulated": yes_tone})
        else:
            for c in self.e_k:
                c.set("—", "")
            self.e_k[0].set("0", "record actual attendance after an event")
            self.pva.set_figure(C.bar([], [], "Expected vs actual attendees", th))
            self.pva_table.set_frame(pd.DataFrame())
