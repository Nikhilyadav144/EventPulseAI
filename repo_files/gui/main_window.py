"""Main window: branded sidebar navigation, Demo Mode banner and stacked pages."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton,
                               QScrollArea, QSizePolicy, QStackedWidget, QStatusBar, QVBoxLayout, QWidget)

from config import settings
from core.db import services
from core.db.repositories import ModelVersionRepository
from gui import icons, theme
from gui.branding import AboutDialog, logo_pixmap
from gui.context import AppContext
from gui.pages.analytics import AnalyticsPage
from gui.pages.attendance import AttendanceRecordsPage
from gui.pages.calendar_page import EventCalendarPage
from gui.pages.dataset_explorer import DatasetExplorerPage
from gui.pages.events import EventsPage
from gui.pages.insights import InsightsPage
from gui.pages.model_lab import ModelLabPage
from gui.pages.monitoring import MonitoringPage
from gui.pages.notifications import NotificationsPage
from gui.pages.overview import OverviewPage
from gui.pages.prediction import PredictionPage
from gui.pages.registrations import RegistrationsPage
from gui.pages.reports import ReportsPage
from gui.pages.settings_page import SettingsPage
from gui.widgets import Badge, confirm, polish_forms, run_task, show_error

NAV = [("Overview", "overview", OverviewPage), ("Dataset Explorer", "dataset", DatasetExplorerPage),
       ("Event Management", "events", EventsPage), ("Event Calendar", "calendar", EventCalendarPage),
       ("Registration Management", "students", RegistrationsPage),
       ("Attendance Prediction", "prediction", PredictionPage),
       ("Attendance Records", "attendance", AttendanceRecordsPage),
       ("Model Laboratory", "model", ModelLabPage), ("Analytics", "analytics", AnalyticsPage),
       ("Insights", "insight", InsightsPage), ("Model Monitoring", "monitor", MonitoringPage),
       ("Reports & Export", "reports", ReportsPage), ("Notifications", "bell", NotificationsPage),
       ("Settings", "settings", SettingsPage)]
SECTIONS = {0: "Workspace", 7: "Intelligence", 11: "Manage"}


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(f"{settings.APP_NAME} — {settings.APP_TAGLINE}")
        self.setMinimumSize(1024, 640)
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen is not None:                      # fit smaller displays instead of overflowing them
            # leave room for Stage Manager's strip and the Dock on smaller displays
            w, h = min(1440, int(screen.width() * 0.80)), min(900, int(screen.height() * 0.88))
            self.resize(w, h)
            self.move(screen.x() + (screen.width() - w) // 2, screen.y() + (screen.height() - h) // 2)
        else:
            self.resize(1440, 900)
        root = QWidget()
        root.setObjectName("Root")
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ---------------------------------------------------------------- sidebar
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(232)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 22, 14, 16)
        sl.setSpacing(2)
        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(6, 0, 0, 0)
        brand_row.setSpacing(10)
        self.logo = QLabel()
        self.logo.setPixmap(logo_pixmap(36))
        self.logo.setFixedSize(36, 36)
        brand_row.addWidget(self.logo)
        bcol = QVBoxLayout()
        bcol.setSpacing(0)
        brand = QLabel(settings.APP_NAME)
        brand.setObjectName("Brand")
        tag = QLabel(settings.APP_TAGLINE)
        tag.setObjectName("Tagline")
        bcol.addWidget(brand)
        bcol.addWidget(tag)
        brand_row.addLayout(bcol, 1)
        sl.addLayout(brand_row)
        sl.addSpacing(22)

        nav_w = QWidget()
        nav_w.setObjectName("SidebarNav")
        nl = QVBoxLayout(nav_w)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(2)
        nav_scroll = QScrollArea()
        nav_scroll.setObjectName("SidebarScroll")
        nav_scroll.setWidgetResizable(True)
        nav_scroll.setFrameShape(QFrame.NoFrame)
        nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        nav_scroll.setWidget(nav_w)
        self.stack = QStackedWidget()
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.pages = []
        self.nav_buttons = []
        for i, (name, icon_name, cls) in enumerate(NAV):
            if i in SECTIONS:
                if i:
                    nl.addSpacing(12)
                sec = QLabel(SECTIONS[i].upper())
                sec.setObjectName("NavSection")
                nl.addWidget(sec)
                nl.addSpacing(3)
            page = cls(ctx)
            page.build()
            self.pages.append(page)
            self.stack.addWidget(page)
            b = QPushButton(f"  {name}".replace("&", "&&"))
            b.setObjectName("NavButton")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(f"{name}  (⌘{i + 1})" if i < 9 else name)
            # toggled (not clicked): accessibility actions (VoiceOver, automation) toggle the button
            # without a click, and must switch the page too
            b.toggled.connect(lambda on, idx=i: on and self.go(idx))
            b._icon_name = icon_name
            self.group.addButton(b, i)
            self.nav_buttons.append(b)
            nl.addWidget(b)
            if i < 9:
                act = QAction(self)
                act.setShortcut(QKeySequence(f"Ctrl+{i + 1}"))
                act.triggered.connect(lambda _=False, idx=i: self.go(idx))
                self.addAction(act)
        nl.addStretch(1)
        sl.addWidget(nav_scroll, 1)
        self.apply_icons()

        info = QFrame()
        info.setObjectName("SidebarCard")
        il = QVBoxLayout(info)
        il.setContentsMargins(12, 10, 12, 11)
        il.setSpacing(3)
        k = QLabel("ACTIVE MODEL")
        k.setObjectName("SidebarKey")
        self.model_label = QLabel()
        self.model_label.setObjectName("SidebarValue")
        self.model_label.setWordWrap(True)
        self.mode_badge = Badge("Live data", "purple")
        il.addWidget(k)
        il.addWidget(self.model_label)
        il.addSpacing(4)
        il.addWidget(self.mode_badge)
        sl.addWidget(info)
        sl.addSpacing(8)
        foot = QHBoxLayout()
        foot.setContentsMargins(6, 0, 0, 0)
        ver = QLabel(f"Version {settings.APP_VERSION}")
        ver.setObjectName("SidebarFooter")
        about = QPushButton("About")
        about.setObjectName("Ghost")
        about.setCursor(Qt.PointingHandCursor)
        about.clicked.connect(self.show_about)
        foot.addWidget(ver)
        foot.addStretch(1)
        foot.addWidget(about)
        sl.addLayout(foot)

        # ---------------------------------------------------------------- content + demo banner
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        self.demo_banner = QFrame()
        self.demo_banner.setObjectName("DemoBanner")
        bl = QHBoxLayout(self.demo_banner)
        bl.setContentsMargins(32, 8, 24, 8)
        self.demo_text = QLabel("Demo Mode — you are viewing SYNTHETIC demonstration data (generated students and "
                                "events, simulated outcomes). Your own database is untouched.")
        self.demo_text.setWordWrap(True)
        self.demo_text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        exit_btn = QPushButton("Exit Demo Mode")
        exit_btn.setObjectName("DemoButton")
        exit_btn.setCursor(Qt.PointingHandCursor)
        exit_btn.clicked.connect(lambda: self.set_demo(False))
        bl.addWidget(self.demo_text, 1)
        bl.addWidget(exit_btn)
        self.demo_banner.setVisible(False)
        cl.addWidget(self.demo_banner)
        cl.addWidget(self.stack, 1)

        # Drop-downs must not size themselves to their longest entry (that forces side-scrolling
        # on narrow windows); the popup list still shows full text.
        for page in self.pages:
            for combo in page.findChildren(QComboBox):
                combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
                combo.setMinimumContentsLength(8)
            polish_forms(page)
        lay.addWidget(side)
        lay.addWidget(content, 1)
        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())

        help_menu = self.menuBar().addMenu("Help")
        about_act = QAction(f"About {settings.APP_NAME}", self)
        about_act.setMenuRole(QAction.AboutRole)
        about_act.triggered.connect(self.show_about)
        help_menu.addAction(about_act)

        ctx.status.connect(lambda m: self.statusBar().showMessage(m, 8000))
        ctx.changed.connect(lambda _a: self.update_model_label())
        ctx.demo_requested.connect(self.set_demo)
        ctx.mode_changed.connect(self._mode_changed)
        ctx.open_event.connect(self.open_event)
        self.update_model_label()
        self.go(0)

    # ------------------------------------------------------------------ navigation
    def apply_icons(self) -> None:
        p = theme.current()
        for b in self.nav_buttons:
            b.setIcon(icons.nav_icon(b._icon_name, p.text_muted, p.accent, 18))

    def go(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)
        self.statusBar().showMessage(NAV[index][0], 2500)

    def open_event(self, event_id: str) -> None:
        ev = self.page(EventsPage)
        self.go(self.pages.index(ev))
        ev.load_event(event_id)

    def page(self, cls):
        return next(p for p in self.pages if isinstance(p, cls))

    def update_model_label(self) -> None:
        mv = ModelVersionRepository(self.ctx.db).active()
        self.model_label.setText(f"{mv['name']} v{mv['version']}" if mv else "No active model")
        self.apply_icons()
        self.logo.setPixmap(logo_pixmap(36))

    def show_about(self) -> None:
        mv = ModelVersionRepository(self.ctx.db).active()
        AboutDialog(self, f"{mv['name']} v{mv['version']}" if mv else None).exec()

    # ------------------------------------------------------------------ demo mode
    def set_demo(self, on: bool, ask: bool = True, wait: bool = False) -> None:
        if on == self.ctx.demo:
            return
        if not on:
            self.ctx.use_database(None, demo=False)
            self.ctx.status.emit("Demo Mode ended - back to your data.")
            return
        path = settings.DEMO_DATABASE
        if ask and not confirm(self, "Start Demo Mode",
                               "Demo Mode opens a SEPARATE demonstration database built from the labelled synthetic "
                               "dataset (generated students and events with simulated outcomes). Your own data is not "
                               "changed, and nothing you do in Demo Mode is saved to it.", "Start Demo Mode"):
            return

        def work():
            return services.create_demo_database(path)

        def done(_res):
            self.ctx.use_database(path, demo=True)
            self.ctx.status.emit("Demo Mode - synthetic demonstration data.")

        def failed(m, d):
            show_error(self, "Demo Mode unavailable", m, d)

        self.ctx.status.emit("Preparing the demonstration database…")
        if wait:
            try:
                done(work())
            except Exception as exc:          # surfaced to the user, never swallowed
                failed(str(exc), "")
            return
        run_task(work, on_done=done, on_error=failed)

    def _mode_changed(self, demo: bool) -> None:
        self.demo_banner.setVisible(demo)
        self.mode_badge.set("Demo Mode · synthetic" if demo else "Live data", "gold" if demo else "purple")
        title = f"{settings.APP_NAME} — {settings.APP_TAGLINE}"
        self.setWindowTitle(title + ("  ·  Demo Mode" if demo else ""))
        self.update_model_label()

    def closeEvent(self, e):
        self.ctx.close()
        super().closeEvent(e)
