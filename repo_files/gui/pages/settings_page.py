"""Settings: application information, dataset & Demo Mode, model configuration, database & backup,
data privacy, appearance and About."""
from __future__ import annotations

import platform
from pathlib import Path

import pandas as pd
import sklearn
from PySide6 import __version__ as pyside_version
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QComboBox, QFileDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from config import settings
from core.db.backup import backup_database, list_backups, restore_database
from core.db.errors import DatabaseError
from core.db.migrations import current_version
from core.db.repositories import ModelVersionRepository
from core.schema import PII_COLUMNS
from gui import theme
from gui.branding import ABOUT_TEXT, CREDITS, AboutDialog, logo_pixmap
from gui.pages.base import Page
from gui.widgets import (Badge, DataTable, InfoList, ResponsiveGrid, button, card, confirm, hbox, label,
                         show_error)

THEME_CHOICES = [("Cream (default)", "cream"), ("Evening", "evening")]


class _Info(InfoList):
    def items(self):
        return self.values.items()


class SettingsPage(Page):
    title = "Settings"
    subtitle = "Application information, data and model configuration, backups, privacy and appearance."
    areas = ("all",)

    def build(self):
        # ---------------------------------------------------------------- application + model
        self.i = _Info(["Application", "Version", "Python", "scikit-learn", "PySide6", "Project folder"])
        self.m = _Info(["Active model", "Decision threshold", "Training data", "Evaluation", "Registered versions"])
        self.body.addWidget(ResponsiveGrid([
            card(self.i, title="Application information", eyebrow="System"),
            card(self.m, label("Change the active model or retrain in the Model Laboratory.", "Muted"),
                 title="Model configuration", eyebrow="Machine learning")], min_width=420, allowed=(2, 1)))

        # ---------------------------------------------------------------- dataset + demo mode
        self.d = _Info(["Original workbook", "Processed dataset", "Synthetic dataset", "Working database"])
        self.demo_badge = Badge("Live data", "purple")
        self.demo_btn = button("Start Demo Mode", "Primary", self.toggle_demo)
        self.demo_text = label("Demo Mode switches to a separate database built only from the labelled synthetic "
                               "dataset (generated students and events with simulated outcomes), so you can explore "
                               "every feature without touching your own data. Nothing is mixed or copied between "
                               "the two databases.", "Muted")
        demo = card(hbox(self.demo_badge, "stretch", self.demo_btn), self.demo_text, title="Demo Mode",
                    name="SubtleCard", margins=16)
        self.body.addWidget(card(self.d, demo, title="Dataset configuration", eyebrow="Data"))

        # ---------------------------------------------------------------- database & backup
        self.b = _Info(["Database file", "Schema version", "Size", "Integrity", "Rows"])
        self.backups = DataTable(filterable=False)
        self.backups.setMinimumHeight(200)
        self.body.addWidget(card(self.b, hbox(button("Back up now", "Primary", self.backup),
                                              button("Restore selected backup…", None, self.restore_selected),
                                              button("Restore from file…", None, self.restore_file),
                                              button("Run integrity check", None, self.integrity), "stretch"),
                                 self.backups, title="Database & backup", eyebrow="Storage",
                                 subtitle="Backups are verified after writing. Restoring first saves a safety copy "
                                          "of the current database, so a restore can always be undone."))

        # ---------------------------------------------------------------- privacy
        pii = ", ".join(sorted(PII_COLUMNS))
        privacy = label(
            "• Students are identified only by pseudonymous IDs (for example STU001); roll-number-like IDs are "
            "rejected by the database.\n"
            "• The Raw_Merged_Data sheet of the original workbook contains personal information and is never "
            "imported or displayed.\n"
            f"• Files containing personal-data columns ({pii}) are refused on import.\n"
            "• Everything stays on this Mac: the app makes no network connections and sends no data anywhere.\n"
            "• The original reference files are kept read-only; cleaning and exports always write new files.",
            "Secondary")
        self.body.addWidget(card(privacy, title="Data privacy", eyebrow="Safeguards"))

        # ---------------------------------------------------------------- appearance
        self.theme_box = QComboBox()
        for text, _key in THEME_CHOICES:
            self.theme_box.addItem(text)
        self.theme_box.currentIndexChanged.connect(self.change_theme)
        self.theme_box.setMinimumWidth(190)
        self.body.addWidget(card(hbox(label("Theme", wrap=False), self.theme_box, "stretch"),
                                 label("Cream is the standard EventPulse look; Evening uses the same colours on a "
                                       "dark plum background for low-light rooms.", "Muted"),
                                 title="Appearance"))

        # ---------------------------------------------------------------- about
        about = QWidget()
        al = QHBoxLayout(about)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(18)
        mark = QLabel()
        mark.setPixmap(logo_pixmap(64))
        mark.setFixedSize(64, 64)
        al.addWidget(mark, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(4)
        name = QLabel(settings.APP_NAME)
        name.setObjectName("AboutName")
        tag = QLabel(settings.APP_TAGLINE)
        tag.setObjectName("AboutTagline")
        col.addWidget(name)
        col.addWidget(tag)
        col.addWidget(label(f"Version {settings.APP_VERSION}", "Muted"))
        col.addSpacing(6)
        col.addWidget(label(ABOUT_TEXT, "Secondary"))
        col.addWidget(label(settings.SIMULATED_TARGET_NOTICE, "Muted"))
        col.addWidget(label(CREDITS, "Muted"))
        al.addLayout(col, 1)
        self.body.addWidget(card(about, hbox(button("About EventPulse AI…", None, self.show_about), "stretch"),
                                 title="About EventPulse AI"))

    # ------------------------------------------------------------------ data
    def refresh(self):
        db = self.ctx.db
        self.i.set("Application", f"{settings.APP_NAME} — {settings.APP_TAGLINE}")
        self.i.set("Version", settings.APP_VERSION)
        self.i.set("Python", f"{platform.python_version()} ({platform.machine()})")
        self.i.set("scikit-learn", sklearn.__version__)
        self.i.set("PySide6", pyside_version)
        self.i.set("Project folder", str(settings.PROJECT_ROOT))
        for k, p in (("Original workbook", settings.ORIGINAL_WORKBOOK), ("Processed dataset", settings.PROCESSED_DATASET),
                     ("Synthetic dataset", settings.SYNTHETIC_DATASET)):
            self.d.set(k, f"{p.relative_to(settings.PROJECT_ROOT)}" + ("" if p.exists() else "  (missing)"))
        demo = self.ctx.demo
        self.d.set("Working database", ("Demo database (synthetic data only) — " if demo else "Your database — ")
                   + self.ctx.db_path.name)
        self.demo_badge.set("Demo Mode is on · synthetic data" if demo else "Live data · Demo Mode is off",
                            "gold" if demo else "purple")
        self.demo_btn.setText("Exit Demo Mode" if demo else "Start Demo Mode")
        mv = ModelVersionRepository(db).active()
        if mv:
            self.m.set("Active model", f"{mv['name']} v{mv['version']} ({mv['model_key']})")
            self.m.set("Decision threshold", f"{mv['threshold']:.2f} (best balanced accuracy on out-of-fold training "
                                             "predictions)")
            self.m.set("Training data", mv["training_data_label"] or "—")
        else:
            for k in ("Active model", "Decision threshold", "Training data"):
                self.m.set(k, "—")
        self.m.set("Evaluation", "Student-grouped 20% holdout · 5-fold student-grouped CV · majority baseline")
        self.m.set("Registered versions", str(len(ModelVersionRepository(db).list())))
        self.theme_box.blockSignals(True)
        self.theme_box.setCurrentIndex(next(i for i, (_t, k) in enumerate(THEME_CHOICES)
                                            if k == theme.current().name))
        self.theme_box.blockSignals(False)
        p = self.ctx.db_path
        self.b.set("Database file", str(p))
        self.b.set("Schema version", str(current_version(db)))
        self.b.set("Size", f"{p.stat().st_size / 1024:,.0f} KB" if p.exists() else "—")
        self.b.set("Rows", ", ".join(f"{t}: {db.scalar(f'SELECT COUNT(*) FROM {t}'):,}" for t in
                                     ("students", "events", "registrations", "predictions", "attendance_records")))
        self.b.set("Integrity", "not checked yet")
        rows = [{"label": b.label, "created_utc": b.created_utc, "schema": b.schema_version,
                 "size_kb": round(b.size_bytes / 1024), "file": b.path.name, "path": str(b.path)}
                for b in list_backups()]
        self.backups.set_frame(pd.DataFrame(rows, columns=["label", "created_utc", "schema", "size_kb", "file", "path"]))

    # ------------------------------------------------------------------ actions
    def toggle_demo(self):
        self.ctx.demo_requested.emit(not self.ctx.demo)

    def show_about(self):
        mv = ModelVersionRepository(self.ctx.db).active()
        AboutDialog(self, f"{mv['name']} v{mv['version']}" if mv else None).exec()

    def change_theme(self, idx: int):
        name = THEME_CHOICES[idx][1]
        theme.set_current(name)
        QApplication.instance().setStyleSheet(theme.stylesheet(theme.current()))
        self.ctx.settings.set("theme", name)
        self.ctx.notify("all")

    def backup(self):
        try:
            b = backup_database(self.ctx.db, "demo" if self.ctx.demo else "manual")
        except DatabaseError as exc:
            show_error(self, "Backup failed", str(exc))
            return
        self.notice.set(f"Backup written and verified: {b.path.name}", "ok")
        self.refresh()

    def restore_selected(self):
        rec = self.backups.selected_record()
        if not rec:
            show_error(self, "No backup selected", "Select a backup in the table first.")
            return
        self._restore(Path(rec["path"]))

    def restore_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Restore database", str(Path.home()), "SQLite database (*.db)")
        if path:
            self._restore(Path(path))

    def _restore(self, path: Path):
        if self.ctx.demo:
            show_error(self, "Restore not available in Demo Mode",
                       "Exit Demo Mode first; restores apply to your own database only.")
            return
        if not confirm(self, "Restore database", f"Replace the current database with {path.name}? A safety backup "
                                                 "of the current state is taken first.", "Restore", danger=True):
            return
        try:
            safety = restore_database(self.ctx.db, path)
        except DatabaseError as exc:
            show_error(self, "Restore failed", str(exc))
            return
        self.notice.set(f"Restored {path.name}. Previous state saved as {safety.path.name}.", "ok")
        self.ctx.notify("all")

    def integrity(self):
        problems = self.ctx.db.integrity_check()
        self.b.set("Integrity", "ok" if not problems else "; ".join(problems[:3]))
