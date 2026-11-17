"""Insights: observations derived automatically from stored outcomes (Analytics shows the data;
Insights says what stands out). Every insight shows its sample size and whether the outcomes
are SIMULATED (dataset) or RECORDED (after real events)."""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QFrame, QVBoxLayout, QWidget

from core import intelligence as I
from gui.pages.base import Page
from gui.widgets import Badge, Notice, ResponsiveGrid, card, hbox, label

KIND_LABEL = {"event": "Event", "department": "Department", "timing": "Timing", "planning": "Planning",
              "prediction_error": "Prediction error", "data": "Overview"}
LEVEL_TONE = {"investigate": "critical", "notable": "purple", "info": "blue"}


def _insight_card(i: I.Insight) -> QFrame:
    f = QFrame()
    f.setObjectName("SubtleCard")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(6)
    lay.addWidget(hbox(Badge(KIND_LABEL.get(i.kind, i.kind), LEVEL_TONE.get(i.level, "neutral")),
                       Badge(i.source.title(), "gold" if i.source == "SIMULATED" else "good"),
                       Badge(f"n = {i.n}", "neutral"), "stretch", spacing=6))
    t = label(i.title, "SectionTitle")
    lay.addWidget(t)
    lay.addWidget(label(i.detail, "Muted"))
    lay.addStretch(1)
    return f


class InsightsPage(Page):
    title = "Insights"
    subtitle = ("What stands out in the stored data: unusual events, department and timing patterns, prediction "
                "errors to investigate and planning notes. Each insight shows its evidence.")
    areas = ("events", "predictions", "datasets")

    def build(self):
        self.filter = QComboBox()
        self.filter.addItems(["All insights", "Recorded outcomes only", "Simulated outcomes only",
                              "Needs investigation"])
        self.filter.currentTextChanged.connect(lambda _: self.reload())
        self.limits = Notice()
        self.body.addWidget(card(hbox(label("Show", wrap=False), self.filter, "stretch"), self.limits,
                                 title="How these are produced",
                                 subtitle="A difference is reported only when it is at least 10 percentage points AND "
                                          "the group's 95 % interval excludes the rate of the rest. Groups under 20 "
                                          "registrations (events under 8) are skipped."))
        self.holder = QWidget()
        self.hl = QVBoxLayout(self.holder)
        self.hl.setContentsMargins(0, 0, 0, 0)
        self.hl.setSpacing(16)
        self.body.addWidget(self.holder)
        self.body.addStretch(1)

    def refresh(self):
        res = I.insights(self.ctx.db)
        self.limits.set(" ".join(res["limitations"]), "info")
        items = res["insights"]
        f = self.filter.currentText()
        if f.startswith("Recorded"):
            items = [i for i in items if i.source == "RECORDED"]
        elif f.startswith("Simulated"):
            items = [i for i in items if i.source == "SIMULATED"]
        elif f.startswith("Needs"):
            items = [i for i in items if i.level == "investigate"]
        while self.hl.count():
            w = self.hl.takeAt(0).widget()
            if w:
                w.deleteLater()
        c = res["counts"]
        self.notice.set(f"Based on {c['RECORDED']} recorded and {c['SIMULATED']} simulated outcomes.", "note")
        if not items:
            self.hl.addWidget(card(label("No insight meets the evidence threshold for this selection. Add events, "
                                         "registrations and recorded attendance to get data-backed observations.",
                                         "Muted"), title="Nothing to report"))
            return
        groups: dict[str, list[I.Insight]] = {}
        for i in items:
            groups.setdefault(i.source, []).append(i)
        for src in ("RECORDED", "SIMULATED"):
            if src in groups:
                title = ("From attendance recorded after real events" if src == "RECORDED" else
                         "From SIMULATED dataset outcomes (describe the simulation rules, not real students)")
                self.hl.addWidget(card(ResponsiveGrid([_insight_card(i) for i in groups[src]], min_width=320,
                                                      allowed=(3, 2, 1), spacing=14), title=title))
