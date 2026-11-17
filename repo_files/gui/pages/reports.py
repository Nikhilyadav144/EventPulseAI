"""Reports & Export: PDF / Excel reports and filtered CSV / Excel table exports."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
from PySide6.QtCore import QDate, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QComboBox, QDateEdit, QFileDialog, QGridLayout, QWidget

from config import settings
from core import analytics as A
from core import reports as R
from gui.pages.base import Page
from gui.widgets import (DataTable, KpiCard, Notice, ResponsiveGrid, button, card, hbox, label, run_task,
                         show_error)

SECTION_LABELS = {"project": "Project information & key indicators", "dataset": "Dataset summary",
                  "models": "Model performance (ROC, confusion matrix, importance)",
                  "predictions": "Prediction summary", "events": "Event-wise analysis & predicted vs actual",
                  "analytics": "Participation analysis charts", "findings": "Key findings"}
PRESETS = {"Full report": list(SECTION_LABELS),
           "Model performance": ["project", "models", "findings"],
           "Events & attendance": ["project", "predictions", "events", "findings"],
           "Dataset & participation": ["project", "dataset", "analytics"],
           "Custom": None}
TABLES = {"Event analysis": lambda db: A.event_trends(db),
          "Department participation": lambda db: A.participation_by(db, "Department"),
          "Year participation": lambda db: A.participation_by(db, "Year"),
          "Event-type participation": lambda db: A.participation_by(db, "Event_Type"),
          "Model comparison": lambda db: A.model_comparison(db),
          "Predicted vs actual": lambda db: A.predicted_vs_actual(db),
          "Recent predictions (500)": lambda db: A.recent_predictions(db, 500),
          "Attendance - recorded (real events)": lambda db: _att(db, False),
          "Attendance - simulated dataset outcomes": lambda db: _att(db, True),
          "Registrations": lambda db: _regs(db),
          "Event calendar (dated events)": lambda db: _cal(db),
          "MLflow experiment runs": lambda db: A.mlflow_runs()}


def _att(db, simulated: bool) -> pd.DataFrame:
    from core.checkin import SIMULATED_SOURCES, attendance_table
    df = attendance_table(db, None, include_simulated=simulated)
    if df.empty:
        return df
    return df[df["source"].isin(SIMULATED_SOURCES)] if simulated else df[~df["source"].isin(SIMULATED_SOURCES)]


def _regs(db) -> pd.DataFrame:
    from core.workflow import registration_table
    return registration_table(db)


def _cal(db) -> pd.DataFrame:
    from core.workflow import calendar_events
    return calendar_events(db)
DATE_COLUMNS = ("event_date", "predicted_utc", "computed_utc", "recorded_utc", "start_utc")


class ReportsPage(Page):
    title = "Reports & Export"
    subtitle = "Generate PDF and Excel reports from the current data, or export any analysis table as CSV or Excel."
    areas = ("all",)

    def build(self):
        self.last_report: Path | None = None
        # ---------------------------------------------------------------- report
        self.preset = QComboBox()
        self.preset.addItems(list(PRESETS))
        self.preset.currentTextChanged.connect(self.apply_preset)
        self.preset.setMinimumWidth(220)
        self.checks = {}
        g = QGridLayout()
        g.setHorizontalSpacing(24)
        g.setVerticalSpacing(8)
        for i, (k, text) in enumerate(SECTION_LABELS.items()):
            cb = QCheckBox(text.replace("&", "&&"))
            cb.setChecked(True)
            cb.toggled.connect(self._custom)
            self.checks[k] = cb
            g.addWidget(cb, i // 2, i % 2)
        gw = QWidget()
        gw.setLayout(g)
        self.b_pdf = button("Generate PDF report", "Primary", lambda: self.generate("pdf"))
        self.b_xlsx = button("Generate Excel workbook", None, lambda: self.generate("xlsx"))
        self.k_events = KpiCard("Events", tone="blue", icon="events")
        self.k_regs = KpiCard("Registrations", tone="purple", icon="students")
        self.k_preds = KpiCard("Predicted registrations", tone="good", icon="prediction")
        self.k_outcomes = KpiCard("Outcomes", tone="gold", icon="check",
                                  tooltip="Simulated outcomes come from the dataset; recorded ones were entered after "
                                          "real events. Reports label them separately.")
        self.status = Notice()
        self.b_open = button("Open report", None, lambda: self.last_report and self._open(self.last_report))
        self.b_reveal = button("Show in Finder", None, lambda: self.last_report and self._open(self.last_report.parent))
        for b in (self.b_open, self.b_reveal):
            b.setEnabled(False)
        self.body.addWidget(card(
            hbox(label("Report type", wrap=False), (self.preset, 1), "stretch"), gw,
            label("Included figures (taken from the database at generation time):", "Muted"),
            ResponsiveGrid([self.k_events, self.k_regs, self.k_preds, self.k_outcomes], min_width=150,
                           allowed=(4, 2), spacing=12),
            hbox(self.b_pdf, self.b_xlsx, "stretch", self.b_open, self.b_reveal), self.status,
            title="Generate a report", eyebrow="PDF · Excel",
            subtitle="The PDF contains the sections ticked below; the Excel workbook always contains every sheet. "
                     "Both carry the simulated-target notice and label simulated and recorded outcomes separately."))

        # ---------------------------------------------------------------- table export
        self.table_choice = QComboBox()
        self.table_choice.addItems(list(TABLES))
        self.table_choice.currentTextChanged.connect(lambda _: self.update_preview())
        self.event_filter = QComboBox()
        self.event_filter.setToolTip("Keep only rows for one event (tables with an event column).")
        self.event_filter.currentTextChanged.connect(lambda _: self.update_preview())
        self.use_dates = QCheckBox("Limit dates")
        self.use_dates.setToolTip("Keep rows whose event / prediction / evaluation date lies in the range. "
                                  "Undated imported events are excluded while this is on.")
        self.d_from = QDateEdit(QDate.currentDate().addMonths(-3))
        self.d_to = QDateEdit(QDate.currentDate().addMonths(3))
        for d in (self.d_from, self.d_to):
            d.setCalendarPopup(True)
            d.setDisplayFormat("yyyy-MM-dd")
            d.setEnabled(False)
            d.dateChanged.connect(lambda _: self.update_preview())
        self.use_dates.toggled.connect(lambda on: (self.d_from.setEnabled(on), self.d_to.setEnabled(on),
                                                   self.update_preview()))
        self.preview = DataTable(filterable=False)
        self.preview.setMinimumHeight(220)
        self.preview_note = label("", "Muted")
        self.body.addWidget(card(
            hbox(label("Table", wrap=False), (self.table_choice, 2), label("Event", wrap=False),
                 (self.event_filter, 1)),
            hbox(self.use_dates, label("from", wrap=False), self.d_from, label("to", wrap=False), self.d_to,
                 "stretch", button("Export CSV…", None, lambda: self.export_table("csv")),
                 button("Export Excel…", None, lambda: self.export_table("xlsx"))),
            self.preview_note, self.preview,
            title="Export a table", subtitle="Preview the rows that will be exported, filtered by event or date."))

        # ---------------------------------------------------------------- files
        self.files = DataTable(filterable=False)
        self.files.setMinimumHeight(220)
        self.body.addWidget(card(self.files, hbox(button("Open selected", None, self.open_selected),
                                                  button("Show reports folder", None,
                                                         lambda: self._open(settings.REPORTS_DIR)),
                                                  "stretch"),
                                 title="Generated files", subtitle="Reports and exports saved in the project folder."))

    # ------------------------------------------------------------------ data
    def refresh(self):
        db = self.ctx.db
        k = A.overview_kpis(db)
        src = A.data_source_summary(db)
        self.k_events.set(f"{k.events:,}", f"{k.completed_events} completed · {k.upcoming_events} scheduled")
        self.k_regs.set(f"{k.registrations:,}", f"{k.students:,} students")
        self.k_preds.set(f"{k.predicted_registrations:,}", f"active model {k.model_name or '—'}")
        self.k_outcomes.set(f"{src['simulated_outcomes'] + src['observed_outcomes']:,}",
                            f"{src['simulated_outcomes']:,} simulated · {src['observed_outcomes']:,} recorded")
        cur = self.event_filter.currentText()
        self.event_filter.blockSignals(True)
        self.event_filter.clear()
        self.event_filter.addItem("All events")
        self.event_filter.addItems([r["event_id"] for r in db.query("SELECT event_id FROM events ORDER BY event_id")])
        if cur:
            self.event_filter.setCurrentText(cur)
        self.event_filter.blockSignals(False)
        self.update_preview()
        rows = []
        for d in (settings.REPORTS_DIR, settings.EXPORTS_DIR):
            d.mkdir(parents=True, exist_ok=True)
            for p in d.iterdir():
                if p.is_file() and p.suffix.lower() in (".pdf", ".xlsx", ".csv"):
                    st = p.stat()
                    rows.append({"file": p.name, "type": p.suffix[1:].upper(), "folder": d.name,
                                 "size_kb": round(st.st_size / 1024, 1),
                                 "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                                 "path": str(p)})
        df = pd.DataFrame(rows, columns=["file", "type", "folder", "size_kb", "modified", "path"])
        self.files.set_frame(df.sort_values("modified", ascending=False) if len(df) else df)

    def apply_preset(self, name: str) -> None:
        keys = PRESETS.get(name)
        if keys is None:
            return
        for k, cb in self.checks.items():
            cb.blockSignals(True)
            cb.setChecked(k in keys)
            cb.blockSignals(False)

    def _custom(self, *_):
        chosen = [k for k, cb in self.checks.items() if cb.isChecked()]
        match = next((n for n, keys in PRESETS.items() if keys is not None and sorted(keys) == sorted(chosen)),
                     "Custom")
        self.preset.blockSignals(True)
        self.preset.setCurrentText(match)
        self.preset.blockSignals(False)

    def filtered_table(self, raw: pd.DataFrame | None = None) -> pd.DataFrame:
        df = TABLES[self.table_choice.currentText()](self.ctx.db) if raw is None else raw
        ev = self.event_filter.currentText()
        if ev and ev != "All events" and "event_id" in df.columns:
            df = df[df["event_id"] == ev]
        if self.use_dates.isChecked():
            col = next((c for c in DATE_COLUMNS if c in df.columns), None)
            if col is not None:
                lo, hi = self.d_from.date().toString("yyyy-MM-dd"), self.d_to.date().toString("yyyy-MM-dd")
                day = df[col].astype("string").str.slice(0, 10)
                df = df[day.notna() & (day >= lo) & (day <= hi)]
        return df.reset_index(drop=True)

    def update_preview(self) -> None:
        try:
            raw = TABLES[self.table_choice.currentText()](self.ctx.db)
            df = self.filtered_table(raw)
        except Exception as exc:              # a broken query must not break the page
            self.preview_note.setText(f"Preview unavailable: {exc}")
            return
        notes = []
        if self.event_filter.currentText() not in ("", "All events") and "event_id" not in raw.columns:
            notes.append("this table has no event column, so the event filter does not apply")
        self.preview_note.setText(f"{len(df):,} row(s) will be exported" + (f" ({'; '.join(notes)})" if notes else "")
                                  + (". Showing the first 200." if len(df) > 200 else "."))
        self.preview.set_frame(df.head(200))

    # ------------------------------------------------------------------ actions
    def generate(self, kind: str, path: str | None = None, wait: bool = False):
        sections = tuple(k for k, cb in self.checks.items() if cb.isChecked())
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = path or str(settings.REPORTS_DIR / f"EventPulse_report_{stamp}.{kind}")
        for b in (self.b_pdf, self.b_xlsx):
            b.setEnabled(False)
        self.status.set(f"Generating {Path(path).name} …", "note")

        def work():
            with self.ctx.open_db() as db:
                if kind == "pdf":
                    return R.generate_pdf(db, path, sections or R.SECTIONS)
                return R.generate_excel(db, path)

        def done(p):
            for b in (self.b_pdf, self.b_xlsx):
                b.setEnabled(True)
            self.last_report = Path(p)
            size = self.last_report.stat().st_size / 1024 if self.last_report.exists() else 0
            self.status.set(f"Saved {self.last_report.name} ({size:,.0f} KB) in {self.last_report.parent}", "ok")
            for b in (self.b_open, self.b_reveal):
                b.setEnabled(True)
            self.ctx.status.emit(f"Report saved: {p}")
            self.refresh()

        def failed(m, d):
            for b in (self.b_pdf, self.b_xlsx):
                b.setEnabled(True)
            self.status.set(f"Report generation failed: {m}", "error")
            show_error(self, "Report generation failed", m, d)

        if wait:                                   # synchronous path used by automated GUI tests
            try:
                done(work())
            except Exception as exc:
                failed(str(exc), "")
            return
        run_task(work, on_done=done, on_error=failed)

    def export_table(self, kind: str, path: str | None = None):
        name = self.table_choice.currentText()
        df = self.filtered_table()
        if not path:
            default = str(settings.EXPORTS_DIR / (name.split(" (")[0].lower().replace(" ", "_") + f".{kind}"))
            path, _ = QFileDialog.getSaveFileName(self, f"Export {name}", default,
                                                  "CSV (*.csv)" if kind == "csv" else "Excel (*.xlsx)")
        if not path:
            return
        try:
            out = R.export_frame(df, path, name)
        except Exception as exc:
            show_error(self, "Export failed", str(exc))
            return
        self.notice.set(f"Exported {len(df):,} rows to {out}", "ok")
        self.refresh()

    def open_selected(self):
        rec = self.files.selected_record()
        if rec:
            self._open(Path(rec["path"]))

    def _open(self, path: Path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
