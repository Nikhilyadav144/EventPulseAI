"""Model Monitoring: incoming prediction inputs vs training data (drift), missing values,
schema violations, accuracy on recorded events, and when to consider retraining."""
from __future__ import annotations

from config import settings
from core import intelligence as I
from core.db.repositories import ModelVersionRepository
from gui.pages.base import Page
from gui.widgets import DataTable, KpiCard, Notice, ResponsiveGrid, card, label

API_LOG = settings.PROJECT_ROOT / "logs" / "predictions.jsonl"
REJECTED_LOG = settings.PROJECT_ROOT / "logs" / "rejected.jsonl"


class MonitoringPage(Page):
    title = "Model Monitoring"
    subtitle = ("Are the inputs the model sees still like its training data? Drift is a reason to look closer - "
                "not proof that predictions got worse.")
    areas = ("predictions", "models")

    def build(self):
        self.k = [KpiCard("Inputs monitored", tone="blue", icon="database",
                          tooltip="Stored desktop predictions for the active model plus the local API log."),
                  KpiCard("Features drifting", tone="gold", icon="monitor"),
                  KpiCard("Schema violations", tone="critical", icon="cross",
                          tooltip="Invalid stored inputs plus requests the API rejected (HTTP 422)."),
                  KpiCard("Recorded events evaluated", tone="good", icon="check")]
        self.body.addWidget(ResponsiveGrid(self.k, min_width=160, allowed=(4, 2), spacing=14))
        self.rec = Notice()
        self.reasons = label("", "Muted")
        self.body.addWidget(card(self.rec, self.reasons, title="Retraining recommendation"))
        self.table = DataTable(filterable=False)
        self.table.setMinimumHeight(420)
        self.body.addWidget(card(self.table, title="Input distributions: training vs incoming",
                                 subtitle="Numeric: standardised mean difference (drift if |score| > 0.5). "
                                          "Categories: largest change in a category's share (drift if > 15 points). "
                                          "Missing-value rates for both."))
        self.acc = DataTable(filterable=False, searchable=False)
        self.acc.setMinimumHeight(180)
        self.body.addWidget(card(self.acc, title="Accuracy on events with RECORDED attendance",
                                 subtitle="Only real outcomes; simulated dataset outcomes are excluded here."))
        self.body.addWidget(card(label("Reproduce outside the app: python drift_check.py "
                                       "(compares logs/predictions.jsonl from the API with data/event_attendance.csv).",
                                       "Muted"), title="Reproducible check"))

    def refresh(self):
        db = self.ctx.db
        mv = ModelVersionRepository(db).active()
        m = I.monitor(db, None, API_LOG, REJECTED_LOG)
        self.k[0].set(str(m.n_incoming), f"for {mv['name']} v{mv['version']}" if mv else "no active model")
        self.k[1].set(str(len(m.drifted)) if m.report is not None else "—",
                      ", ".join(m.drifted[:3]) if m.drifted else "none flagged" if m.report is not None
                      else "not enough inputs")
        self.k[2].set(str(m.schema_violations), "invalid or rejected inputs")
        self.k[3].set(str(0 if m.accuracy is None or m.accuracy.empty else len(m.accuracy)), "real outcomes only")
        retrain = m.recommendation.startswith("Consider")
        self.rec.set(m.recommendation, "info" if retrain else "ok")
        self.reasons.setText("\n".join(f"• {r}" for r in m.reasons) or "No warnings.")
        if m.report is not None:
            df = m.report.copy()
            df["missing_training"] = (df["missing_training"] * 100).round(1).astype(str) + " %"
            df["missing_incoming"] = (df["missing_incoming"] * 100).round(1).astype(str) + " %"
            df["drift"] = df["drift"].map({True: "Drift", False: "OK"})
            self.table.set_frame(df, tones={"drift": lambda v: "gold" if v == "Drift" else "good"})
        else:
            import pandas as pd
            self.table.set_frame(pd.DataFrame({"status": m.reasons or ["No data"]}))
        if m.accuracy is not None and not m.accuracy.empty:
            self.acc.set_frame(m.accuracy)
        else:
            import pandas as pd
            self.acc.set_frame(pd.DataFrame({"status": ["No predicted event has recorded attendance yet."]}))
        self.notice.set("The active model is trained on SIMULATED labels; monitoring compares inputs, which are "
                        "real, with its training inputs.", "info")
