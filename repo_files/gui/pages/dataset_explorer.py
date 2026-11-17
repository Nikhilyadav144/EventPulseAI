"""Dataset Explorer: import, inspect, validate, clean and export datasets.

The loaded file is never modified; cleaning produces a separate frame that is
only written when the user exports it.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from PySide6.QtWidgets import (QComboBox, QFileDialog, QPlainTextEdit, QTabWidget, QVBoxLayout,
                               QWidget)

from config import settings
from core import analytics as A
from core import charts as C
from core.data_loader import DataImportError, load_table
from core.data_validation import validate
from core.db import services
from core.reports import export_frame
from gui import theme
from gui.pages.base import Page
from gui.widgets import (Badge, ChartView, DataTable, KpiCard, ResponsiveGrid, button, card, confirm, hbox, label,
                         run_task, show_error)


class DatasetExplorerPage(Page):
    title = "Dataset Explorer"
    subtitle = ("Import CSV or Excel files, inspect quality, validate against the schema and export a cleaned copy. "
                "Original files are never modified; personal-data columns are refused.")
    areas = ("datasets",)

    def build(self):
        self.df: pd.DataFrame | None = None
        self.cleaned: pd.DataFrame | None = None
        self.source: str = ""
        for text, kind, fn in (("Open file…", "Primary", self.open_file),
                               ("Processed dataset", None, lambda: self.load_reference("processed")),
                               ("Synthetic dataset", None, lambda: self.load_reference("synthetic"))):
            self.header_actions.addWidget(button(text, kind, fn))

        self.k_rows = KpiCard("Rows", tone="purple", icon="dataset")
        self.k_cols = KpiCard("Columns", tone="blue", icon="analytics")
        self.k_missing = KpiCard("Missing cells", tone="neutral", icon="info",
                                 tooltip="Empty cells. They are tinted gold in the preview table.")
        self.k_dups = KpiCard("Duplicate rows", tone="neutral", icon="cross")
        self.k_balance = KpiCard("Class balance", tone="gold", icon="check",
                                 tooltip="Share of Will_Attend = 1. In the project datasets this outcome is simulated.")
        self.body.addWidget(ResponsiveGrid([self.k_rows, self.k_cols, self.k_missing, self.k_dups, self.k_balance],
                                           min_width=140, allowed=(5, 3, 2), spacing=14))
        self.source_badge = Badge("No dataset loaded", "neutral")
        self.source_label = label("Open a CSV or Excel file, or load one of the project datasets. Personal-data "
                                  "columns and the Raw_Merged_Data sheet are always refused.", "Muted")
        self.body.addWidget(hbox(self.source_badge, (self.source_label, 1), spacing=10))

        self.tabs = QTabWidget()
        self.preview = DataTable()
        self.preview.model.highlight_missing = True
        self.types = DataTable(filterable=False)
        self.missing = DataTable(filterable=False)
        self.missing_chart = ChartView(min_height=220)
        self.stats = DataTable(filterable=False)
        self.dist_col = QComboBox()
        self.dist_col.currentTextChanged.connect(self.draw_distribution)
        self.dist_chart = ChartView(min_height=320)
        self.balance_chart = ChartView(min_height=180)
        self.validation_text = QPlainTextEdit()
        self.validation_text.setReadOnly(True)
        self.mode = QComboBox()
        self.mode.addItems(["training", "prediction"])
        self.mode.currentTextChanged.connect(lambda _: self.run_validation())
        self.clean_log = QPlainTextEdit()
        self.clean_log.setReadOnly(True)
        self.clean_log.setMaximumHeight(170)
        self.clean_table = DataTable()

        self.tabs.addTab(self.preview, "Preview")
        self.tabs.addTab(self.types, "Columns && types")
        mt = QWidget()
        ml = QVBoxLayout(mt)
        ml.addWidget(self.missing_chart)
        ml.addWidget(self.missing)
        self.tabs.addTab(mt, "Missing values")
        self.tabs.addTab(self.stats, "Statistics")
        dt = QWidget()
        dl = QVBoxLayout(dt)
        dl.addWidget(hbox(label("Column", wrap=False), (self.dist_col, 1), "stretch"))
        dl.addWidget(self.dist_chart)
        dl.addWidget(self.balance_chart)
        self.tabs.addTab(dt, "Distributions")
        vt = QWidget()
        vl = QVBoxLayout(vt)
        vl.addWidget(hbox(label("Validate for", wrap=False), self.mode, "stretch"))
        vl.addWidget(self.validation_text)
        self.tabs.addTab(vt, "Validation")
        ct = QWidget()
        cl = QVBoxLayout(ct)
        self.btn_clean = button("Clean (preview)", "Primary", self.do_clean)
        self.btn_export = button("Export cleaned…", None, self.export_cleaned)
        self.btn_import = button("Import into database…", None, self.import_db)
        cl.addWidget(label("Cleaning removes exact duplicates and repeated Student_ID + Event_ID rows, sets blank "
                           "forecasts to 'Unavailable' and fills blanks only from the same student's or event's other "
                           "rows. Remaining blanks are imputed later inside the model pipeline (training data only).",
                           "Muted"))
        cl.addWidget(hbox(self.btn_clean, self.btn_export, self.btn_import, "stretch"))
        cl.addWidget(self.clean_log)
        cl.addWidget(self.clean_table)
        self.tabs.addTab(ct, "Clean && export")
        self.tabs.setMinimumHeight(560)
        self.body.addWidget(card(self.tabs, margins=16))
        self._set_enabled(False)

    def _set_enabled(self, on: bool):
        for w in (self.btn_clean, self.btn_import):
            w.setEnabled(on)
        self.btn_export.setEnabled(on and self.cleaned is not None)

    # ------------------------------------------------------------------ loading
    def open_file(self, path: str | None = None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Open dataset", str(Path.home()),
                                                  "Data files (*.csv *.xlsx *.xlsm);;All files (*)")
        if not path:
            return
        try:
            r = load_table(path)
        except DataImportError as exc:
            show_error(self, "Cannot import file", str(exc))
            return
        self.set_dataset(r.df, f"{Path(path).name}" + (f" · sheet {r.sheet}" if r.sheet else ""), r.warnings,
                         kind="file")

    def load_reference(self, kind: str):
        df = A.load_reference_dataset(kind)
        label_ = {"processed": "Processed original dataset (simulated target)",
                  "synthetic": "SYNTHETIC dataset - not real student data"}[kind]
        self.set_dataset(df, label_, kind=kind)

    def set_dataset(self, df: pd.DataFrame, source: str, warnings: list[str] | None = None, kind: str = "file"):
        self.df, self.cleaned, self.source = df, None, source
        synthetic = kind == "synthetic" or ("Data_Source" in df.columns and
                                            set(df["Data_Source"].dropna().astype(str)) == {"SYNTHETIC"})
        badge = {"processed": ("Original dataset · simulated outcomes", "purple")}.get(kind) or \
            (("Synthetic demo data", "gold") if synthetic else ("Imported file", "blue"))
        self.source_badge.set(*badge)
        self.source_label.setText(f"Loaded: {source}" + (" — " + "; ".join(warnings) if warnings else ""))
        self.clean_log.clear()
        self.clean_table.set_frame(pd.DataFrame())
        self._set_enabled(True)
        self.render()
        self.ctx.status.emit(f"Loaded {source}: {len(df):,} rows")

    def render(self):
        df = self.df
        p = A.profile_dataset(df)
        th = theme.current().chart
        self.k_rows.set(f"{p.rows:,}")
        self.k_cols.set(f"{p.columns}")
        cells = int(p.missing["missing"].sum()) if len(p.missing) else 0
        self.k_missing.set(f"{cells:,}", f"in {len(p.missing)} column(s)", tone="gold" if cells else "good")
        self.k_dups.set(f"{p.duplicate_rows:,}", f"{p.duplicate_registrations} repeated registrations"
                        if p.duplicate_registrations is not None else "",
                        tone="gold" if p.duplicate_rows or p.duplicate_registrations else "good")
        if p.class_balance:
            tot = sum(p.class_balance.values())
            self.k_balance.set(f"{p.class_balance.get(1, 0) / tot:.0%}",
                               f"attend · {p.class_balance.get(1, 0):,} : {p.class_balance.get(0, 0):,}")
        else:
            self.k_balance.set("—", "no Will_Attend column")
        self.preview.set_frame(df)
        self.types.set_frame(p.dtypes)
        self.missing.set_frame(p.missing, formats={"missing_pct": lambda v: f"{v:.2f}%"})
        self.missing_chart.set_figure(C.bar(p.missing["column"], p.missing["missing"], "Missing values per column", th,
                                            horizontal=True, size=(6.4, 2.6)) if len(p.missing)
                                      else C.bar([], [], "No missing values", th))
        self.stats.set_frame(p.describe)
        self.dist_col.blockSignals(True)
        self.dist_col.clear()
        self.dist_col.addItems([c for c in df.columns if c not in ("Student_ID", "Event_ID")])
        self.dist_col.blockSignals(False)
        self.draw_distribution(self.dist_col.currentText())
        if p.class_balance:
            self.balance_chart.set_figure(C.attendance_split(p.class_balance.get(1, 0), p.class_balance.get(0, 0),
                                                             "Class balance (Will_Attend)", th,
                                                             labels=("Attend (1)", "Not attend (0)")))
        else:
            self.balance_chart.set_figure(C.bar([], [], "Class balance", th))
        self.mode.setCurrentText("training" if "Will_Attend" in df.columns else "prediction")
        self.run_validation()

    def draw_distribution(self, col: str):
        if self.df is None or not col or col not in self.df.columns:
            return
        th = theme.current().chart
        s = self.df[col]
        num = pd.to_numeric(s, errors="coerce")
        if num.notna().sum() >= max(1, 0.9 * s.notna().sum()) and s.nunique() > 12:
            counts, edges = np.histogram(num.dropna(), bins=20)
            fig = C.bar([f"{edges[i]:.3g}–{edges[i + 1]:.3g}" for i in range(len(counts))], counts,
                        f"Distribution of {col}", th, value_labels=False, ylabel="Rows")
        else:
            vc = s.astype("string").fillna("(blank)").value_counts().head(20)
            fig = C.bar(vc.index.tolist(), vc.values.tolist(), f"Distribution of {col}", th, ylabel="Rows",
                        horizontal=len(vc) > 6, size=(6.4, max(3.0, 0.28 * len(vc) + 1)))
        self.dist_chart.set_figure(fig)

    def run_validation(self):
        if self.df is None:
            return
        rep = validate(self.df, self.mode.currentText())
        self.validation_text.setPlainText(rep.summary())
        self.notice.set("Validation passed." if rep.ok else f"Validation found {len(rep.errors)} error(s); see the "
                        "Validation tab.", "ok" if rep.ok else "error")

    # ------------------------------------------------------------------ cleaning / export / import
    def do_clean(self):
        try:
            self.cleaned, log = A.clean_dataset(self.df)
        except Exception as exc:
            show_error(self, "Cleaning failed", str(exc))
            return
        self.clean_log.setPlainText("\n".join(f"• {line}" for line in log) or "Nothing to change.")
        self.clean_table.set_frame(self.cleaned)
        self._set_enabled(True)
        self.ctx.status.emit("Cleaned copy ready - the loaded data was not changed.")

    def export_cleaned(self, path: str | None = None):
        if self.cleaned is None:
            return
        if not path:
            default = str(settings.EXPORTS_DIR / "cleaned_dataset.csv")
            path, _ = QFileDialog.getSaveFileName(self, "Export cleaned dataset", default,
                                                  "CSV (*.csv);;Excel (*.xlsx)")
        if not path:
            return
        try:
            out = export_frame(self.cleaned, path, "Cleaned")
        except Exception as exc:
            show_error(self, "Export failed", str(exc))
            return
        self.ctx.status.emit(f"Exported {out}")
        self.notice.set(f"Cleaned dataset exported to {out}", "ok")

    def import_db(self, kind: str | None = None, ask: bool = True):
        if self.df is None:
            return
        kind = kind or ("SYNTHETIC" if "SYNTHETIC" in self.source.upper() else "UPLOAD")
        if ask and not confirm(self, "Import into database",
                               f"Import {len(self.df):,} rows ({self.source}) as {kind} records? Students, events, "
                               "registrations and any Will_Attend outcomes (labelled as simulated) will be added. "
                               "Existing records are kept; repeated registrations are skipped.", "Import"):
            return
        df = self.df

        def work():
            with self.ctx.open_db() as db:
                return services.import_dataset(db, df, kind, self.source[:80])

        def done(s):
            msg = ("This file was already imported." if s.already_imported else
                   f"Imported {s.students} students, {s.events} events, {s.registrations} registrations "
                   f"({s.skipped_duplicates} skipped).")
            self.notice.set(msg, "ok")
            self.ctx.notify("all")

        run_task(work, on_done=done, on_error=lambda m, d: show_error(self, "Import failed", m, d))
