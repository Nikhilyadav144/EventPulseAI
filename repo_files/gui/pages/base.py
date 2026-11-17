"""Base class for the eight pages."""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from gui.context import AppContext
from gui.widgets import Notice


class Page(QWidget):
    title = "Page"
    subtitle = ""
    areas: tuple[str, ...] = ("all",)      # data areas that make this page stale

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.setObjectName("Page")
        self._dirty = True
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        head = QWidget()
        head.setObjectName("PageHeader")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(32, 26, 32, 10)
        hl.setSpacing(16)
        tcol = QVBoxLayout()
        tcol.setSpacing(4)
        t = QLabel(self.title)
        t.setObjectName("PageTitle")
        s = QLabel(self.subtitle)
        s.setObjectName("PageSubtitle")
        s.setWordWrap(True)
        tcol.addWidget(t)
        tcol.addWidget(s)
        hl.addLayout(tcol, 1)
        self.header_actions = QHBoxLayout()
        self.header_actions.setSpacing(8)
        hl.addLayout(self.header_actions)
        outer.addWidget(head)
        self.notice = Notice()
        nw = QWidget()
        nw.setObjectName("NoticeHolder")
        nl = QVBoxLayout(nw)
        nl.setContentsMargins(32, 0, 32, 4)
        nl.addWidget(self.notice)
        outer.addWidget(nw)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(32, 12, 32, 32)
        self.body.setSpacing(18)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        ctx.changed.connect(self._on_changed)

    # pages implement build() once and refresh() whenever data changes
    def build(self) -> None:
        pass

    def refresh(self) -> None:
        pass

    def _on_changed(self, area: str) -> None:
        if area == "all" or area in self.areas or "all" in self.areas:
            self._dirty = True
            if self.isVisible():
                self.reload()

    def reload(self) -> None:
        try:
            self.refresh()
            self._dirty = False
        except Exception as exc:          # a broken query must not crash the app
            import traceback
            traceback.print_exc()
            self.notice.set(f"Could not load this page: {exc}", "error")

    def showEvent(self, e):
        super().showEvent(e)
        if self._dirty:
            self.reload()
