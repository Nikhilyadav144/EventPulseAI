"""Shared application state handed to every page."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from core.db.connection import DEFAULT_DB_PATH, Database
from core.db.repositories import SettingsRepository


class AppContext(QObject):
    """Owns the GUI-thread database connection and broadcasts data changes.

    Background tasks must use ``open_db()`` to get their own connection (SQLite
    connections are not shared across threads here; WAL mode lets them coexist).

    Demo Mode swaps ``db`` to the separate synthetic demonstration database; the user's
    database stays open (as ``main_db``) for preferences and is never written with demo rows.
    """

    changed = Signal(str)      # area: events | predictions | models | datasets | settings | all
    status = Signal(str)       # status-bar message
    demo_requested = Signal(bool)
    mode_changed = Signal(bool)
    open_event = Signal(str)   # ask the main window to show one event in Event Management

    def __init__(self, db_path: str | Path | None = None):
        super().__init__()
        self.main_db_path = Path(db_path or DEFAULT_DB_PATH)
        self.db_path = self.main_db_path
        self.main_db = Database(self.main_db_path)
        self.db = self.main_db
        self.settings = SettingsRepository(self.main_db)     # UI preferences always live in the user's database
        self.demo = False

    def open_db(self) -> Database:
        return Database(self.db_path)

    def notify(self, area: str) -> None:
        self.changed.emit(area)

    def use_database(self, path: str | Path | None, demo: bool) -> None:
        """Switch the working database (``None`` = back to the user's database)."""
        if self.db is not self.main_db:
            self.db.close()
        if path is None or Path(path) == self.main_db_path:
            self.db, self.db_path, self.demo = self.main_db, self.main_db_path, False
        else:
            self.db_path = Path(path)
            self.db = Database(self.db_path)
            self.demo = demo
        self.mode_changed.emit(self.demo)
        self.changed.emit("all")

    def close(self) -> None:
        if self.db is not self.main_db:
            self.db.close()
        self.main_db.close()
