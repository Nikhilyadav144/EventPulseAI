"""Application bootstrap."""
from __future__ import annotations

import logging
import os
import sys
import traceback
from pathlib import Path

from config import settings

LOG_DIR = settings.PROJECT_ROOT / "logs"


def _setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(filename=LOG_DIR / "app.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return logging.getLogger("eventpulse")


def first_run_setup(ctx) -> list[str]:
    """Import the processed dataset and register models when the database is empty."""
    from core.db import services
    from core.db.repositories import ModelVersionRepository
    msgs = []
    if ctx.db.scalar("SELECT COUNT(*) FROM students") == 0 and settings.PROCESSED_DATASET.exists():
        services.bootstrap(ctx.db)
        msgs.append("First run: imported the processed dataset and registered the trained models.")
    elif not ModelVersionRepository(ctx.db).list():
        services.sync_models(ctx.db)
    return msgs


def check_active_model(ctx) -> str | None:
    from core.db.repositories import ModelVersionRepository, resolve_path
    from core.model_registry import ModelCompatibilityError, load_model
    mv = ModelVersionRepository(ctx.db).active()
    if not mv:
        return "No active model is registered. Open the Model Laboratory and train models."
    try:
        load_model(resolve_path(mv["artifact_path"]))
    except ModelCompatibilityError as exc:
        return str(exc) + " Use Model Laboratory → Retrain."
    except FileNotFoundError:
        return f"The active model's files are missing ({mv['artifact_path']}). Retrain in the Model Laboratory."
    return None


def _persistent_matplotlib_cache() -> None:
    """The packaged app's PyInstaller hook points matplotlib at a fresh temporary folder, which
    makes it rebuild its font cache (about 20 s) on every launch. Keep the cache in the project."""
    if getattr(sys, "frozen", False):
        cache = settings.PROJECT_ROOT / ".cache" / "matplotlib"
        try:
            cache.mkdir(parents=True, exist_ok=True)
            os.environ["MPLCONFIGDIR"] = str(cache)
        except OSError:
            pass                                   # fall back to the temporary folder


def main(argv: list[str] | None = None, db_path: str | Path | None = None) -> int:
    log = _setup_logging()
    _persistent_matplotlib_cache()
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setApplicationName(settings.APP_NAME)
    app.setApplicationDisplayName(settings.APP_NAME)
    app.setOrganizationName("EventPulse")
    icon = settings.PROJECT_ROOT / "assets" / "icon.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    f = QFont()
    f.setPointSize(13)
    app.setFont(f)

    # show the start-up screen before the heavy imports (matplotlib, scikit-learn, pages)
    splash = None
    if "--no-splash" not in (argv or sys.argv):
        from PySide6.QtWidgets import QSplashScreen

        from gui.branding import SPLASH_MUTED, splash_pixmap
        splash = QSplashScreen(splash_pixmap())
        splash.show()
        splash.showMessage("Loading data and models…", Qt.AlignBottom | Qt.AlignRight, QColor(SPLASH_MUTED))
        app.processEvents()

    import matplotlib
    matplotlib.use("QtAgg")

    from gui import theme
    from gui.context import AppContext
    from gui.main_window import MainWindow

    def excepthook(etype, value, tb):
        text = "".join(traceback.format_exception(etype, value, tb))
        log.error("Unhandled exception:\n%s", text)
        box = QMessageBox(QMessageBox.Critical, "Unexpected error",
                          f"{value}\n\nThe error was logged to logs/app.log. The application keeps running.")
        box.setDetailedText(text)
        box.exec()

    sys.excepthook = excepthook
    settings.ensure_dirs()
    try:
        ctx = AppContext(db_path)
    except Exception as exc:
        log.exception("database open failed")
        QMessageBox.critical(None, "Database error", f"Could not open the database:\n{exc}")
        return 1
    theme.set_current(ctx.settings.get("theme", "cream"))
    app.setStyleSheet(theme.stylesheet(theme.current()))
    msgs = first_run_setup(ctx)
    win = MainWindow(ctx)
    win.show()
    if splash is not None:
        splash.finish(win)
    for m in msgs:
        ctx.status.emit(m)
    problem = check_active_model(ctx)
    if problem:
        log.warning(problem)
        win.statusBar().showMessage(problem)
    log.info("EventPulse AI started (db=%s)", ctx.db_path)
    app.setAttribute(Qt.AA_DontShowIconsInMenus, False)
    if "--selftest" in (argv or sys.argv):
        _schedule_selftest(app, win)
    return app.exec()


def _schedule_selftest(app, win) -> None:
    """Packaged-app smoke test: open every page at 1100, 1280 and 1920 px wide, check for horizontal
    overflow, load a model, render charts, then quit. Writes logs/selftest.txt and window captures in
    logs/selftest_screens/ (used by the build script to verify the frozen app)."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QScrollArea

    def run():
        lines, ok = [f"version={settings.APP_VERSION}"], True
        try:
            from core.prediction import Predictor
            from core.db.repositories import ModelVersionRepository, resolve_path
            mv = ModelVersionRepository(win.ctx.db).active()
            p = Predictor(resolve_path(mv["artifact_path"]))
            lines.append(f"model_loaded={p.name}")
            shots = LOG_DIR / "selftest_screens"
            shots.mkdir(exist_ok=True)
            for old in shots.glob("*.png"):
                old.unlink()
            for width, height in ((1280, 800), (1100, 720), (1920, 1080)):
                win.resize(width, height)
                for _ in range(10):
                    app.processEvents()
                lines.append(f"window_{width}={win.width()}x{win.height()}")
                for i, page in enumerate(win.pages):
                    win.go(i)
                    for _ in range(12):
                        app.processEvents()
                    err = page.notice.objectName() == "ErrorNotice"
                    bar = page.findChild(QScrollArea).horizontalScrollBar()
                    over = bar.maximum()
                    ok &= not err and over == 0
                    lines.append(f"page_{width}_{i}={type(page).__name__}:"
                                 f"{'ERROR ' + page.notice.lbl.text() if err else 'ok'}"
                                 f"{' OVERFLOW ' + str(over) + 'px' if over else ''}")
                    win.grab().save(str(shots / f"{width}_{i + 1:02d}_{type(page).__name__}.png"))
            win.resize(1280, 800)
        except Exception:
            ok = False
            lines.append("exception=" + traceback.format_exc().replace("\n", " | "))
        lines.append(f"result={'OK' if ok else 'FAILED'}")
        (LOG_DIR / "selftest.txt").write_text("\n".join(lines) + "\n")
        app.quit()

    QTimer.singleShot(1500, run)
