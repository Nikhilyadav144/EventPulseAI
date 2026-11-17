"""Brand elements: logo mark, start-up splash screen and the About dialog."""
from __future__ import annotations

import platform

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from config import settings

LOGO_FILE = settings.PROJECT_ROOT / "assets" / "logo_mark.png"
# splash colours (the cream palette) kept here so the start-up screen needs no heavy imports
SPLASH_WINDOW, SPLASH_BORDER, SPLASH_BAND = "#f8f6f2", "#d9d4e3", "#f0edfa"
SPLASH_TEXT, SPLASH_ACCENT, SPLASH_SECONDARY, SPLASH_MUTED = "#29263a", "#6956a5", "#57526b", "#8a859c"
ABOUT_TEXT = ("EventPulse AI helps event coordinators estimate how many registered students will attend a "
              "college event, plan for no-shows, and compare predictions with the attendance recorded "
              "afterwards.")
CREDITS = ("Built on the CSL236 AI-Based College Event Attendance Prediction System. Uses Python, PySide6 (Qt), "
           "scikit-learn, pandas, Matplotlib, ReportLab and openpyxl.")


def _dpr() -> float:
    app = QGuiApplication.instance()
    return max(app.devicePixelRatio() if app else 1.0, 2.0)


def _painted_mark(size: int) -> QPixmap:
    """Fallback drawing of the mark if the asset file is missing (same geometry as the asset)."""
    dpr = _dpr()
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, 0, size)
    g.setColorAt(0, QColor("#7c6abf"))
    g.setColorAt(1, QColor("#584696"))
    p.setBrush(g)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.225, size * 0.225)
    from PySide6.QtGui import QPen
    pen = QPen(QColor("#ffffff"), size * 0.058)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    pts = [(0.13, 0.58), (0.27, 0.58), (0.34, 0.47), (0.42, 0.69), (0.51, 0.29), (0.58, 0.53)]
    path = QPainterPath()
    path.moveTo(pts[0][0] * size, pts[0][1] * size)
    for x, y in pts[1:]:
        path.lineTo(x * size, y * size)
    p.drawPath(path)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#e9bc65"))
    p.drawEllipse(QRectF(0.84 * size - size * 0.068, 0.44 * size - size * 0.068, size * 0.136, size * 0.136))
    p.end()
    return pm


def logo_pixmap(size: int = 36) -> QPixmap:
    if LOGO_FILE.exists():
        dpr = _dpr()
        pm = QPixmap(str(LOGO_FILE))
        if not pm.isNull():
            pm = pm.scaled(int(size * dpr), int(size * dpr), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            return pm
    return _painted_mark(size)


def splash_pixmap(width: int = 560, height: int = 320) -> QPixmap:
    """Start-up card in the brand colours (shown while models and the database load)."""
    dpr = _dpr()
    pm = QPixmap(int(width * dpr), int(height * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    p.setPen(QColor(SPLASH_BORDER))
    p.setBrush(QColor(SPLASH_WINDOW))
    p.drawRoundedRect(QRectF(0.5, 0.5, width - 1, height - 1), 18, 18)
    # soft lavender band
    band = QPainterPath()
    band.addRoundedRect(QRectF(0.5, height - 64, width - 1, 63.5), 18, 18)
    p.fillPath(band, QColor(SPLASH_BAND))
    p.fillRect(QRectF(0.5, height - 64, width - 1, 20), QColor(SPLASH_BAND))
    p.drawPixmap(40, 52, logo_pixmap(76))
    f = QFont()
    f.setPixelSize(30)
    f.setWeight(QFont.Weight.Bold)
    p.setFont(f)
    p.setPen(QColor(SPLASH_TEXT))
    p.drawText(QRectF(134, 54, width - 160, 40), Qt.AlignLeft | Qt.AlignVCenter, settings.APP_NAME)
    f.setPixelSize(15)
    f.setWeight(QFont.Weight.DemiBold)
    p.setFont(f)
    p.setPen(QColor(SPLASH_ACCENT))
    p.drawText(QRectF(136, 94, width - 160, 26), Qt.AlignLeft | Qt.AlignVCenter, settings.APP_TAGLINE)
    f.setPixelSize(12)
    f.setWeight(QFont.Weight.Normal)
    p.setFont(f)
    p.setPen(QColor(SPLASH_SECONDARY))
    p.drawText(QRectF(40, 160, width - 80, 60), Qt.AlignLeft | Qt.TextWordWrap,
               "Attendance forecasting for college events - plan for turnout, compare predictions with what "
               "actually happened.")
    p.setPen(QColor(SPLASH_MUTED))
    p.drawText(QRectF(40, height - 52, width - 80, 40), Qt.AlignLeft | Qt.AlignVCenter,
               f"Version {settings.APP_VERSION}")
    p.end()
    return pm


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, active_model: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"About {settings.APP_NAME}")
        self.setMinimumWidth(500)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 26, 28, 22)
        lay.setSpacing(12)
        top = QHBoxLayout()
        top.setSpacing(16)
        mark = QLabel()
        mark.setPixmap(logo_pixmap(72))
        mark.setFixedSize(72, 72)
        top.addWidget(mark, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        name = QLabel(settings.APP_NAME)
        name.setObjectName("AboutName")
        tag = QLabel(settings.APP_TAGLINE)
        tag.setObjectName("AboutTagline")
        ver = QLabel(f"Version {settings.APP_VERSION}")
        ver.setObjectName("Muted")
        for w in (name, tag, ver):
            col.addWidget(w)
        top.addLayout(col, 1)
        lay.addLayout(top)
        for text, obj in ((ABOUT_TEXT, "Secondary"), (settings.SIMULATED_TARGET_NOTICE, "Muted"),
                          (CREDITS, "Muted")):
            lb = QLabel(text)
            lb.setWordWrap(True)
            lb.setObjectName(obj)
            lay.addWidget(lb)
        sysline = QLabel(f"Active model: {active_model or 'none'}  ·  Python {platform.python_version()}  ·  "
                         f"{platform.system()} {platform.machine()}")
        sysline.setObjectName("Muted")
        sysline.setWordWrap(True)
        lay.addWidget(sysline)
        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton("Close")
        ok.setObjectName("Primary")
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        lay.addLayout(row)
