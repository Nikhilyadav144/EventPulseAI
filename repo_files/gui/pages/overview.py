"""Overview dashboard: KPIs, data provenance, charts and recent activity - all computed live
from the database."""
from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from core import analytics as A
from core import charts as C
from gui import icons, theme
from gui.pages.base import Page
from gui.widgets import (DATA_SOURCE_LABELS, Badge, ChartView, DataTable, KpiCard, ResponsiveGrid, WrapLabel, card,
                         fmt_pct, outcome_tone)

ACTIVITY_TONES = {"prediction": ("prediction", "purple"), "evaluation": ("check", "good"),
                  "event": ("events", "blue"), "student": ("students", "blue"), "dataset": ("dataset", "gold"),
                  "model_version": ("model", "purple"), "database": ("database", "neutral"),
                  "attendance": ("check", "good"), "demo_database": ("dataset", "gold")}


def _count(n: int, noun: str) -> str:
    return f"{n:,} {noun}{'' if n == 1 else 's'}"


def local_time(ts: str) -> str:
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone()
    except (TypeError, ValueError):
        return ts or ""
    return dt.strftime("%d %b, %H:%M")


class ActivityList(QWidget):
    """Compact list of recent stored actions (rebuilt on refresh)."""

    def __init__(self):
        super().__init__()
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(10)
        self.lay.addStretch(1)                  # rows stay at the top when the card is taller
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.rows: list[QWidget] = []

    def _add(self, w: QWidget) -> None:
        self.lay.insertWidget(self.lay.count() - 1, w)
        self.rows.append(w)

    def set_rows(self, df) -> None:
        for w in self.rows:
            self.lay.removeWidget(w)
            w.deleteLater()
        self.rows = []
        if df is None or not len(df):
            empty = WrapLabel("No activity yet. Predictions, recorded attendance, imports and model changes appear here.")
            empty.setObjectName("Muted")
            self._add(empty)
            return
        for r in df.itertuples():
            icon_name, tone = ACTIVITY_TONES.get(r.kind, ("info", "neutral"))
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(10)
            chip = QLabel()
            chip.setObjectName("KpiChip")
            chip.setProperty("tone", tone)
            chip.setFixedSize(28, 28)
            chip.setAlignment(Qt.AlignCenter)
            chip.setPixmap(icons.pixmap(icon_name, theme.tone_colors(tone)[0], 15))
            col = QVBoxLayout()
            col.setSpacing(1)
            t = WrapLabel(r.activity)
            d = WrapLabel(" · ".join(x for x in (local_time(r.when_utc), r.detail) if x))
            d.setObjectName("Muted")
            col.addWidget(t)
            col.addWidget(d)
            rl.addWidget(chip, 0, Qt.AlignTop)
            rl.addLayout(col, 1)
            self._add(row)


class SourcesPanel(QWidget):
    """Where the numbers come from: original / synthetic / app-entered data, simulated vs recorded outcomes."""

    def __init__(self):
        super().__init__()
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(10)
        self.lay.addStretch(1)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.rows: list[QWidget] = []

    def _row(self, badge: Badge, text: str) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(12)
        badge.setFixedWidth(148)
        badge.setAlignment(Qt.AlignCenter)
        h.addWidget(badge, 0, Qt.AlignTop)
        lb = WrapLabel(text)
        lb.setObjectName("Secondary")
        h.addWidget(lb, 1)
        return w

    def set_summary(self, s: dict) -> None:
        for w in self.rows:
            self.lay.removeWidget(w)
            w.deleteLater()
        self.rows = []
        sources = sorted(set(s["students"]) | set(s["events"]) | set(s["registrations"]),
                         key=lambda k: ["ORIGINAL_HYBRID", "SYNTHETIC", "MANUAL", "UPLOAD"].index(k)
                         if k in ("ORIGINAL_HYBRID", "SYNTHETIC", "MANUAL", "UPLOAD") else 9)
        tone = {"ORIGINAL_HYBRID": "purple", "SYNTHETIC": "gold", "MANUAL": "blue", "UPLOAD": "blue"}
        for src in sources:
            text = " · ".join(_count(s[k].get(src, 0), k[:-1]) for k in ("students", "events", "registrations"))
            self.rows.append(self._row(Badge(DATA_SOURCE_LABELS.get(src, src), tone.get(src, "neutral")), text))
        if not sources:
            self.rows.append(self._row(Badge("No data", "neutral"), "Import a dataset or create events."))
        self.rows.append(self._row(Badge("Simulated outcomes", "gold"),
                                   f"{s['simulated_outcomes']:,} attendance outcomes come from the dataset's "
                                   "simulation, not from observed attendance."))
        self.rows.append(self._row(Badge("Recorded attendance", "good"),
                                   f"{s['observed_outcomes']:,} outcomes entered after real events"
                                   + (f" · {s['headcount_events']} head-count events" if s["headcount_events"] else "")
                                   + "."))
        self.rows.append(self._row(Badge("Upcoming events", "blue"),
                                   f"{s['future_events']:,} scheduled event(s) awaiting attendance."))
        for w in self.rows:
            self.lay.insertWidget(self.lay.count() - 1, w)


class OverviewPage(Page):
    title = "Overview"
    subtitle = "A live summary of students, events, the active model and stored predictions."

    def build(self):
        self.k_students = KpiCard("Students", tone="purple", icon="students")
        self.k_events = KpiCard("Events", tone="blue", icon="events")
        self.k_attend = KpiCard("Predicted attendees", tone="good", icon="check",
                                tooltip="Registrations whose predicted probability is at or above the decision "
                                        "threshold (latest prediction per registration, active model).")
        self.k_not = KpiCard("Predicted no-shows", tone="gold", icon="cross")
        self.k_auc = KpiCard("CV ROC-AUC", tone="purple", icon="curve",
                             tooltip="Mean ROC-AUC over 5 student-grouped cross-validation folds - the more "
                                     "reliable estimate. The single holdout split is shown for reference.")
        self.k_acc = KpiCard("Holdout accuracy", tone="blue", icon="prediction",
                             tooltip="Accuracy on the student-grouped holdout, next to the majority-class baseline "
                                     "(always predicting 'attend').")
        self.body.addWidget(ResponsiveGrid([self.k_students, self.k_events, self.k_attend, self.k_not, self.k_auc,
                                            self.k_acc], min_width=140, allowed=(6, 3, 2), spacing=14))

        self.split_chart = ChartView(min_height=150)
        self.split_chart.setMaximumHeight(180)
        self.split_caption = WrapLabel()
        self.split_caption.setObjectName("Muted")
        self.sources = SourcesPanel()
        self.split_card = card(self.split_chart, self.split_caption, title="Attendance distribution",
                               subtitle="Predicted outcomes of the active model, or recorded outcomes when nothing "
                                        "has been predicted yet.")
        self.body.addWidget(ResponsiveGrid([self.split_card,
                                            card(self.sources, title="Data sources",
                                                 subtitle="Every record keeps its origin; nothing is mixed silently.")],
                                           min_width=400, allowed=(2, 1)))
        self.trend_chart = ChartView(min_height=270)
        self.dept_chart = ChartView(min_height=270)
        self.body.addWidget(ResponsiveGrid([card(self.trend_chart, title="Event-wise attendance"),
                                            card(self.dept_chart, title="Department-wise participation")],
                                           min_width=400, allowed=(2, 1)))
        self.activity = ActivityList()
        self.recent = DataTable(filterable=False)
        self.recent.setMinimumHeight(300)
        self.body.addWidget(ResponsiveGrid(
            [card(self.activity, title="Recent activity", subtitle="Latest stored actions."),
             card(self.recent, title="Recent predictions", subtitle="Latest stored predictions from every model.")],
            min_width=420, allowed=(2, 1)))

    def refresh(self):
        db = self.ctx.db
        th = theme.current().chart
        k = A.overview_kpis(db)
        self.k_students.set(f"{k.students:,}", f"{k.registrations:,} registrations")
        self.k_events.set(f"{k.events:,}", f"{k.completed_events} completed · {k.upcoming_events} scheduled")
        if k.predicted_registrations:
            self.k_attend.set(f"{k.predicted_attendees:,}", f"expected {k.expected_attendance:,.1f} "
                                                             "(sum of probabilities)")
            self.k_not.set(f"{k.predicted_non_attendees:,}", f"of {k.predicted_registrations:,} predicted "
                                                              "registrations")
        else:
            self.k_attend.set("—", "no predictions yet")
            self.k_not.set("—", "no predictions yet")
        if k.model_name:
            self.k_auc.set(f"{k.cv_roc_auc:.3f}" if k.cv_roc_auc is not None else "—",
                           (f"± {k.cv_roc_auc_std:.3f} · holdout {k.roc_auc:.3f}" if k.roc_auc is not None
                            else f"± {k.cv_roc_auc_std:.3f}") if k.cv_roc_auc is not None else "")
            better = k.accuracy is not None and k.baseline_accuracy is not None and k.accuracy > k.baseline_accuracy
            self.k_acc.set(fmt_pct(k.accuracy), f"baseline {fmt_pct(k.baseline_accuracy)} (always 'attend')",
                           tone="good" if better else "gold")
        else:
            self.k_auc.set("—", "no active model")
            self.k_acc.set("—", "no active model")
        self.notice.set(" ".join(k.notes), "info")

        if k.predicted_registrations:
            self.split_chart.set_figure(C.attendance_split(k.predicted_attendees, k.predicted_non_attendees,
                                                           "Predicted outcomes (active model)", th))
            self.split_caption.setText(f"Predictions only - {k.model_name} v{k.model_version}. Recorded attendance "
                                       "is compared on the Analytics page.")
        else:
            d = A.attendance_distribution(db)
            self.split_chart.set_figure(C.attendance_split(
                d["attended"], d["did_not_attend"], "Recorded outcomes", th, labels=("Attended", "Did not attend"),
                subtitle="Simulated outcomes" if d["simulated"] else None))
            self.split_caption.setText("Recorded outcomes" + (" - simulated by the dataset, not observed."
                                                              if d["simulated"] else "."))
        self.sources.set_summary(A.data_source_summary(db))
        ev = A.event_trends(db)
        dated = ev["event_date"].notna().sum() if len(ev) else 0
        if len(ev) and dated >= 3:
            e = ev[ev["event_date"].notna()]
            self.trend_chart.set_figure(C.line(e["event_date"], e["attendance_rate"], "Attendance rate over time", th,
                                               percent=True))
        else:
            self.trend_chart.set_figure(C.bar(ev["event_id"] if len(ev) else [], ev["attendance_rate"] if len(ev) else [],
                                              "Recorded attendance rate by event", th, percent=True, value_labels=False,
                                              subtitle="Simulated outcomes" if k.recorded_attendance_simulated else None))
        dep = A.participation_by(db, "Department")
        self.dept_chart.set_figure(C.bar(dep["Department"], dep["registrations"], "Registrations by department", th))
        self.activity.set_rows(A.recent_activity(db, 7))
        rec = A.recent_predictions(db, 20)
        self.recent.set_frame(rec.drop(columns=["prediction_id"]), formats={"probability": lambda v: f"{v:.3f}"},
                              tones={"outcome": outcome_tone})
