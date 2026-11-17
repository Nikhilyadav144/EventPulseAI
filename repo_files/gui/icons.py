"""Line icons drawn with QPainter on a 24-unit grid (crisp on Retina, no font or image files).

``pixmap(name, color, size)`` returns a device-pixel-ratio aware QPixmap; ``nav_icon`` returns a
QIcon whose checked state uses the accent colour.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap


def _pen(p: QPainter, color: QColor, w: float = 1.7) -> None:
    pen = QPen(color, w)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)


def _overview(p, c):
    _pen(p, c)
    for x, y in ((3.5, 3.5), (13.5, 3.5), (3.5, 13.5), (13.5, 13.5)):
        p.drawRoundedRect(QRectF(x, y, 7, 7), 2, 2)


def _dataset(p, c):
    _pen(p, c)
    p.drawRoundedRect(QRectF(3.5, 4.5, 17, 15), 2.5, 2.5)
    p.drawLine(QPointF(3.5, 9.5), QPointF(20.5, 9.5))
    p.drawLine(QPointF(3.5, 14.5), QPointF(20.5, 14.5))
    p.drawLine(QPointF(9.5, 9.5), QPointF(9.5, 19.5))


def _events(p, c):
    _pen(p, c)
    p.drawRoundedRect(QRectF(3.5, 5.5, 17, 15), 2.5, 2.5)
    p.drawLine(QPointF(3.5, 10), QPointF(20.5, 10))
    p.drawLine(QPointF(8, 3.5), QPointF(8, 7))
    p.drawLine(QPointF(16, 3.5), QPointF(16, 7))
    p.setBrush(c)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(13, 13.5, 4, 3.5), 1, 1)


def _prediction(p, c):
    _pen(p, c)
    p.drawEllipse(QPointF(12, 12), 8.5, 8.5)
    p.drawEllipse(QPointF(12, 12), 4.5, 4.5)
    p.setBrush(c)
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPointF(12, 12), 1.6, 1.6)


def _model(p, c):
    _pen(p, c)
    path = QPainterPath()
    path.moveTo(9.5, 3.5)
    path.lineTo(9.5, 9.5)
    path.lineTo(4.5, 18.5)
    path.quadTo(3.8, 20.5, 6, 20.5)
    path.lineTo(18, 20.5)
    path.quadTo(20.2, 20.5, 19.5, 18.5)
    path.lineTo(14.5, 9.5)
    path.lineTo(14.5, 3.5)
    p.drawPath(path)
    p.drawLine(QPointF(8, 3.5), QPointF(16, 3.5))
    p.drawLine(QPointF(7, 15), QPointF(17, 15))


def _analytics(p, c):
    _pen(p, c)
    p.drawLine(QPointF(4, 20), QPointF(20, 20))
    for x, top in ((6.5, 14), (11.5, 9), (16.5, 12)):
        p.drawRoundedRect(QRectF(x - 1.5, top, 3, 20 - top - 2), 1, 1)
    path = QPainterPath()
    path.moveTo(5, 9)
    path.lineTo(10, 5.5)
    path.lineTo(14, 7.5)
    path.lineTo(19.5, 3.8)
    p.drawPath(path)


def _reports(p, c):
    _pen(p, c)
    path = QPainterPath()
    path.moveTo(14, 3.5)
    path.lineTo(6.5, 3.5)
    path.quadTo(5, 3.5, 5, 5)
    path.lineTo(5, 19)
    path.quadTo(5, 20.5, 6.5, 20.5)
    path.lineTo(17.5, 20.5)
    path.quadTo(19, 20.5, 19, 19)
    path.lineTo(19, 8.5)
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(QPointF(14, 3.5), QPointF(19, 8.5))
    p.drawLine(QPointF(8.5, 12.5), QPointF(15.5, 12.5))
    p.drawLine(QPointF(8.5, 16.5), QPointF(13, 16.5))


def _settings(p, c):
    _pen(p, c)
    for y, x in ((6, 15), (12, 8.5), (18, 13.5)):
        p.drawLine(QPointF(3.5, y), QPointF(20.5, y))
        p.setBrush(QColor(0, 0, 0, 0))
        p.drawEllipse(QPointF(x, y), 2.2, 2.2)


def _students(p, c):
    _pen(p, c)
    p.drawEllipse(QPointF(9, 8), 3.3, 3.3)
    path = QPainterPath()
    path.moveTo(3, 19.5)
    path.cubicTo(3, 14, 15, 14, 15, 19.5)
    p.drawPath(path)
    p.drawEllipse(QPointF(16.5, 9), 2.6, 2.6)
    path2 = QPainterPath()
    path2.moveTo(15.5, 14.3)
    path2.cubicTo(18.5, 14, 21, 15.5, 21, 19)
    p.drawPath(path2)


def _check(p, c):
    _pen(p, c)
    p.drawEllipse(QPointF(12, 12), 8.5, 8.5)
    path = QPainterPath()
    path.moveTo(8, 12.3)
    path.lineTo(11, 15.2)
    path.lineTo(16.2, 9.2)
    p.drawPath(path)


def _cross(p, c):
    _pen(p, c)
    p.drawEllipse(QPointF(12, 12), 8.5, 8.5)
    p.drawLine(QPointF(9, 9), QPointF(15, 15))
    p.drawLine(QPointF(15, 9), QPointF(9, 15))


def _curve(p, c):
    _pen(p, c)
    p.drawLine(QPointF(4, 20), QPointF(20, 20))
    p.drawLine(QPointF(4, 20), QPointF(4, 4))
    path = QPainterPath()
    path.moveTo(4, 20)
    path.cubicTo(5.5, 9, 10, 6, 20, 4.5)
    p.drawPath(path)


def _spark(p, c):
    _pen(p, c)
    path = QPainterPath()
    path.moveTo(3, 13)
    path.lineTo(7, 13)
    path.lineTo(9.5, 8)
    path.lineTo(12.5, 17)
    path.lineTo(15.5, 6)
    path.lineTo(17.5, 12)
    path.lineTo(21, 12)
    p.drawPath(path)


def _shield(p, c):
    _pen(p, c)
    path = QPainterPath()
    path.moveTo(12, 3.5)
    path.lineTo(19.5, 6.5)
    path.lineTo(19.5, 11.5)
    path.cubicTo(19.5, 16.5, 16, 19.5, 12, 20.8)
    path.cubicTo(8, 19.5, 4.5, 16.5, 4.5, 11.5)
    path.lineTo(4.5, 6.5)
    path.closeSubpath()
    p.drawPath(path)
    path2 = QPainterPath()
    path2.moveTo(8.8, 12)
    path2.lineTo(11.2, 14.4)
    path2.lineTo(15.5, 9.8)
    p.drawPath(path2)


def _database(p, c):
    _pen(p, c)
    p.drawEllipse(QRectF(5, 3.5, 14, 5))
    p.drawLine(QPointF(5, 6), QPointF(5, 18))
    p.drawLine(QPointF(19, 6), QPointF(19, 18))
    p.drawArc(QRectF(5, 9.5, 14, 5), 180 * 16, 180 * 16)
    p.drawArc(QRectF(5, 15.5, 14, 5), 180 * 16, 180 * 16)


def _info(p, c):
    _pen(p, c)
    p.drawEllipse(QPointF(12, 12), 8.5, 8.5)
    p.drawLine(QPointF(12, 11), QPointF(12, 16.5))
    p.setBrush(c)
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPointF(12, 7.8), 1.2, 1.2)


def _calendar(p, c):
    _pen(p, c)
    p.drawRoundedRect(QRectF(3.5, 5.5, 17, 15), 2.5, 2.5)
    p.drawLine(QPointF(8, 3.5), QPointF(8, 7))
    p.drawLine(QPointF(16, 3.5), QPointF(16, 7))
    p.setBrush(c)
    p.setPen(Qt.NoPen)
    for x in (7.2, 12, 16.8):
        for y in (11.5, 16):
            p.drawEllipse(QPointF(x, y), 1.15, 1.15)


def _attendance(p, c):
    _pen(p, c)
    p.drawRoundedRect(QRectF(4.5, 3.5, 15, 17), 2.5, 2.5)
    path = QPainterPath()
    path.moveTo(8.3, 12.2)
    path.lineTo(10.9, 14.8)
    path.lineTo(15.8, 9.4)
    p.drawPath(path)


def _insight(p, c):
    _pen(p, c)
    path = QPainterPath()
    path.moveTo(12, 3.5)
    path.cubicTo(7.6, 3.5, 5.5, 6.8, 5.5, 9.6)
    path.cubicTo(5.5, 12.4, 7.6, 13.8, 8.8, 15.6)
    path.lineTo(15.2, 15.6)
    path.cubicTo(16.4, 13.8, 18.5, 12.4, 18.5, 9.6)
    path.cubicTo(18.5, 6.8, 16.4, 3.5, 12, 3.5)
    p.drawPath(path)
    p.drawLine(QPointF(9.3, 18.3), QPointF(14.7, 18.3))
    p.drawLine(QPointF(10.3, 20.8), QPointF(13.7, 20.8))


def _monitor(p, c):
    _pen(p, c)
    p.drawRoundedRect(QRectF(3.5, 4.5, 17, 12.5), 2.5, 2.5)
    p.drawLine(QPointF(9, 20.5), QPointF(15, 20.5))
    p.drawLine(QPointF(12, 17), QPointF(12, 20.5))
    path = QPainterPath()
    path.moveTo(6.5, 12.5)
    path.lineTo(9.5, 9.5)
    path.lineTo(12, 11.5)
    path.lineTo(17.5, 7.5)
    p.drawPath(path)


def _bell(p, c):
    _pen(p, c)
    path = QPainterPath()
    path.moveTo(6, 16.5)
    path.lineTo(6, 11)
    path.cubicTo(6, 7.2, 8.6, 4.8, 12, 4.8)
    path.cubicTo(15.4, 4.8, 18, 7.2, 18, 11)
    path.lineTo(18, 16.5)
    path.lineTo(19.5, 18)
    path.lineTo(4.5, 18)
    path.closeSubpath()
    p.drawPath(path)
    p.drawArc(QRectF(10, 18.5, 4, 3), 180 * 16, 180 * 16)
    p.drawLine(QPointF(12, 3), QPointF(12, 4.8))


def _flask_spark(p, c):
    _model(p, c)


ICONS: dict[str, Callable] = {
    "overview": _overview, "dataset": _dataset, "events": _events, "prediction": _prediction,
    "model": _model, "analytics": _analytics, "reports": _reports, "settings": _settings,
    "students": _students, "check": _check, "cross": _cross, "curve": _curve, "spark": _spark,
    "shield": _shield, "database": _database, "info": _info, "calendar": _calendar,
    "attendance": _attendance, "insight": _insight, "monitor": _monitor, "bell": _bell,
}


def pixmap(name: str, color: str, size: int = 20) -> QPixmap:
    app = QGuiApplication.instance()
    dpr = app.devicePixelRatio() if app else 1.0
    dpr = max(dpr, 2.0)                           # always render at Retina density
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 24.0, size / 24.0)
    ICONS.get(name, _info)(p, QColor(color))
    p.end()
    return pm


def nav_icon(name: str, normal: str, active: str, size: int = 18) -> QIcon:
    icon = QIcon()
    icon.addPixmap(pixmap(name, normal, size), QIcon.Normal, QIcon.Off)
    icon.addPixmap(pixmap(name, active, size), QIcon.Normal, QIcon.On)
    icon.addPixmap(pixmap(name, active, size), QIcon.Active, QIcon.On)
    return icon
