"""Reusable UI components."""
from __future__ import annotations

import traceback
from typing import Any, Callable

import numpy as np
import pandas as pd
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QObject, QRunnable, QSortFilterProxyModel, Qt,
                            QThreadPool, Signal, Slot)
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QSizePolicy, QTableView, QToolTip, QVBoxLayout,
                               QWidget)

from gui import icons, theme


# ======================================================================== layout helpers
class WrapLabel(QLabel):
    """Word-wrapping label that always reserves the height its text needs at the current width.

    Qt does not reliably propagate height-for-width through nested grid layouts, which can clip
    wrapped text to one line; this label sets its own minimum height whenever its width changes."""

    def __init__(self, text: str = ""):
        super().__init__(text)
        self.setWordWrap(True)

    def _fit_height(self) -> None:
        if self.wordWrap() and self.width() > 0:
            h = self.heightForWidth(self.width())
            if h > 0 and h != self.minimumHeight():
                self.setMinimumHeight(h)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit_height()

    def setText(self, text: str) -> None:
        super().setText(text)
        self._fit_height()


def card(*widgets: QWidget, title: str | None = None, subtitle: str | None = None, margins=20, spacing=12,
         actions: list[QWidget] | None = None, eyebrow: str | None = None, name: str = "Card") -> QFrame:
    """Content section: optional eyebrow, title, subtitle and right-aligned header actions."""
    f = QFrame()
    f.setObjectName(name)
    lay = QVBoxLayout(f)
    lay.setContentsMargins(margins, margins - 2, margins, margins)
    lay.setSpacing(spacing)
    if title or actions or eyebrow:
        head_w = QWidget()
        head = QHBoxLayout(head_w)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(10)
        col_w = QWidget()
        col = QVBoxLayout(col_w)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        if eyebrow:
            e = QLabel(eyebrow.upper())
            e.setObjectName("Eyebrow")
            col.addWidget(e)
        if title:
            t = QLabel(title)                      # titles are short: one line, exact height
            t.setObjectName("SectionTitle")
            col.addWidget(t)
        if subtitle:
            st = WrapLabel(subtitle)
            st.setObjectName("Muted")
            col.addWidget(st)
        head.addWidget(col_w, 1)
        for a in actions or []:
            head.addWidget(a, 0, Qt.AlignTop)
        head_w.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        lay.addWidget(head_w)
    elif subtitle:
        st = WrapLabel(subtitle)
        st.setObjectName("Muted")
        lay.addWidget(st)
    for w in widgets:
        lay.addWidget(w)
    growing = (QSizePolicy.Expanding, QSizePolicy.MinimumExpanding, QSizePolicy.Ignored)
    if not any(w.sizePolicy().verticalPolicy() in growing for w in widgets):
        lay.addStretch(1)               # a card stretched to match its row neighbour keeps content at the top
    return f


def polish_forms(root: QWidget) -> None:
    """macOS form layouts keep fields at their minimum width and centre the form by default;
    let fields use the available width and align forms to the top-left like the rest of the UI."""
    from PySide6.QtWidgets import QFormLayout
    for f in root.findChildren(QFormLayout):
        f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        f.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)


def divider() -> QFrame:
    d = QFrame()
    d.setObjectName("Divider")
    d.setFrameShape(QFrame.NoFrame)
    return d


class Badge(QLabel):
    """Small pill label; tone = purple | blue | gold | good | critical | neutral."""

    def __init__(self, text: str = "", tone: str = "neutral", tooltip: str | None = None):
        super().__init__(text)
        self.setObjectName("Badge")
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        if tooltip:
            self.setToolTip(tooltip)
        self.set_tone(tone)

    def set_tone(self, tone: str) -> None:
        self.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)

    def set(self, text: str, tone: str | None = None) -> None:
        self.setText(text)
        self.setVisible(bool(text))
        if tone:
            self.set_tone(tone)


class ResponsiveGrid(QWidget):
    """Lays its widgets out in as many equal columns as fit (choosing from ``allowed`` column
    counts), so rows reflow instead of forcing horizontal scrolling on narrow windows.

    Each row is its own widget with a horizontal box layout: when wrapped text inside a card grows,
    Qt invalidates exactly that row, so the new height propagates (nested layouts or a QGridLayout
    can keep a stale height-for-width cache and squeeze the cards)."""

    def __init__(self, widgets: list[QWidget], min_width: int = 280, allowed: tuple[int, ...] | None = None,
                 spacing: int = 16):
        super().__init__()
        self.items = list(widgets)
        self.min_width = min_width
        self.allowed = tuple(sorted(allowed or range(1, len(self.items) + 1), reverse=True))
        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(0, 0, 0, 0)
        self.vbox.setSpacing(spacing)
        self.spacing = spacing
        self.columns = 0
        self._rows: list[QWidget] = []
        self._relayout(self.allowed[-1] if len(self.allowed) else 1)

    def _fit(self, width: int) -> int:
        fit = max(1, (width + self.spacing) // (self.min_width + self.spacing))
        return next((c for c in self.allowed if c <= fit), self.allowed[-1])

    def _relayout(self, cols: int) -> None:
        if cols == self.columns:
            return
        old_rows = self._rows
        self._rows = []
        for start in range(0, len(self.items), cols):
            rw = QWidget(self)                          # each row is a widget: its layout is invalidated
            row = QHBoxLayout(rw)                       # directly when a child grows (no stale caches)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(self.spacing)
            chunk = self.items[start:start + cols]
            for w in chunk:
                row.addWidget(w, 1)
            for _ in range(cols - len(chunk)):          # keep equal column widths on a short last row
                row.addStretch(1)
            self.vbox.addWidget(rw)
            self._rows.append(rw)
            if self.isVisible():
                rw.show()
        for rw in old_rows:                            # now empty: their widgets moved to the new rows
            self.vbox.removeWidget(rw)
            rw.hide()
            rw.deleteLater()
        self.columns = cols
        self.updateGeometry()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout(self._fit(e.size().width()))


class InfoList(QWidget):
    """Readable key / value rows (keys muted, values wrap to their full height).

    Built from one horizontal row per key inside a vertical layout: height-for-width of wrapped
    values propagates reliably this way (a grid layout can squeeze wrapped rows together)."""

    def __init__(self, keys: list[str], key_width: int = 150):
        super().__init__()
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)
        self.values: dict[str, QLabel] = {}
        for k in keys:
            rw = QWidget()
            row = QHBoxLayout(rw)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(16)
            kl = WrapLabel(k)
            kl.setObjectName("InfoKey")
            kl.setFixedWidth(key_width)
            v = WrapLabel("—")
            v.setObjectName("InfoValue")
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            v.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            row.addWidget(kl, 0, Qt.AlignTop)
            row.addWidget(v, 1)
            col.addWidget(rw)
            self.values[k] = v

    def set(self, key: str, text: str) -> None:
        self.values[key].setText(text)

    def __getitem__(self, key: str) -> QLabel:
        return self.values[key]


def hbox(*items, spacing=12, margins=0) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(margins, margins, margins, margins)
    lay.setSpacing(spacing)
    for it in items:
        if it == "stretch":
            lay.addStretch(1)
        elif isinstance(it, tuple):
            lay.addWidget(it[0], it[1])
        else:
            lay.addWidget(it)
    return w


def label(text: str, name: str | None = None, wrap: bool = True) -> QLabel:
    lb = WrapLabel(text) if wrap else QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lb


def button(text: str, kind: str | None = None, on_click: Callable | None = None, tooltip: str | None = None):
    b = QPushButton(text.replace("&", "&&"))
    if kind:
        b.setObjectName(kind)
    b.setCursor(Qt.PointingHandCursor)
    if on_click:
        b.clicked.connect(on_click)
    if tooltip:
        b.setToolTip(tooltip)
    return b


class Notice(QFrame):
    """Inline banner: kind = info (gold - simulated data / caveats), note (blue), error, ok."""

    def __init__(self, text: str = "", kind: str = "info"):
        super().__init__()
        self.lbl = WrapLabel(text)
        self.lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.addWidget(self.lbl)
        self.set(text, kind)

    def set(self, text: str, kind: str = "info") -> None:
        self.setObjectName({"info": "Notice", "note": "NoteNotice", "error": "ErrorNotice", "ok": "OkNotice"}[kind])
        icon = {"info": "ⓘ", "note": "ⓘ", "error": "⚠", "ok": "✓"}[kind]
        self.lbl.setText(f"{icon}  {text}" if text else "")
        self.setVisible(bool(text))
        self.style().unpolish(self)
        self.style().polish(self)


class KpiCard(QFrame):
    """Metric card: icon chip in a semantic tone, title, large value and a caption."""

    def __init__(self, title: str, value: str = "—", caption: str = "", tone: str = "purple",
                 icon: str | None = None, tooltip: str | None = None):
        super().__init__()
        self.setObjectName("KpiCard")
        self.setMinimumWidth(96)              # cards must fit narrow windows without side-scrolling
        self.setFixedHeight(126)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 13)
        lay.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.chip = QLabel()
        self.chip.setObjectName("KpiChip")
        self.chip.setFixedSize(26, 26)
        self.chip.setAlignment(Qt.AlignCenter)
        self.icon_name = icon
        self.t = QLabel(title.replace("-", "\u2011"))      # non-breaking hyphen: wrap at spaces only
        self.t.setObjectName("KpiTitle")
        self.t.setWordWrap(True)
        self.t.setFixedHeight(32)                         # two lines, so values line up across cards
        self.t.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.t.setToolTip(tooltip or title)
        top.addWidget(self.chip, 0, Qt.AlignVCenter)
        top.addWidget(self.t, 1)
        self.v = QLabel(value)
        self.v.setObjectName("KpiValue")
        self.c = QLabel(caption)
        self.c.setObjectName("KpiCaption")
        self.c.setWordWrap(True)
        lay.addLayout(top)
        lay.addSpacing(2)
        lay.addWidget(self.v)
        lay.addWidget(self.c)
        lay.addStretch(1)
        if tooltip:
            self.setToolTip(tooltip)
        self.set_tone(tone)

    def set_tone(self, tone: str) -> None:
        self.tone = tone
        self.chip.setProperty("tone", tone)
        if self.icon_name:
            fg, _bg = theme.tone_colors(tone)
            self.chip.setPixmap(icons.pixmap(self.icon_name, fg, 16))
        self.chip.style().unpolish(self.chip)
        self.chip.style().polish(self.chip)

    def set(self, value: str, caption: str | None = None, tooltip: str | None = None,
            tone: str | None = None) -> None:
        self.v.setText(value)
        if caption is not None:
            self.c.setText(caption)
        if tooltip:
            self.setToolTip(tooltip)
        if tone and tone != self.tone:
            self.set_tone(tone)


# ======================================================================== charts
class ChartView(FigureCanvasQTAgg):
    """Matplotlib canvas with per-mark hover tooltips (bars and line points)."""

    def __init__(self, fig: Figure | None = None, min_height: int = 260):
        if fig is None:                      # themed blank canvas until data arrives
            fig = Figure(facecolor=theme.current().chart.surface)
        super().__init__(fig)
        self.setMinimumHeight(min_height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.mpl_connect("motion_notify_event", self._hover)
        self._last = None

    def set_figure(self, fig: Figure) -> None:
        """Swap in a new figure and size it to this canvas (a fresh figure keeps its own
        default size otherwise, leaving unpainted areas)."""
        self.figure = fig
        fig.set_canvas(self)
        self._fit()
        self.draw_idle()

    def _fit(self) -> None:
        ratio = self.device_pixel_ratio or 1
        orig = getattr(self.figure, "_original_dpi", self.figure.dpi)
        self.figure._set_dpi(orig * ratio, forward=False)
        w, h = max(self.width(), 50) * ratio, max(self.height(), 50) * ratio
        self.figure.set_size_inches(w / self.figure.dpi, h / self.figure.dpi, forward=False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.draw_idle()

    def _hover(self, event):
        fig = self.figure
        text = None
        for patch, tip in getattr(fig, "_ep_hover", []):
            if patch.contains(event)[0]:
                text = tip
                break
        if text is None and event.inaxes is not None and getattr(fig, "_ep_points", None):
            ax = event.inaxes
            best, best_d = None, 1e9
            for x, y, tip in fig._ep_points:
                px, py = ax.transData.transform((x, y))
                d = (px - event.x) ** 2 + (py - event.y) ** 2
                if d < best_d:
                    best, best_d = tip, d
            if best_d < 15 ** 2:
                text = best
        if text != self._last:
            self._last = text
            if text:
                QToolTip.showText(self.mapToGlobal(event.guiEvent.position().toPoint()), text, self)
            else:
                QToolTip.hideText()


# ======================================================================== tables
_ACRONYMS = {"roc": "ROC", "auc": "AUC", "cv": "CV", "utc": "UTC", "id": "ID", "kb": "KB", "f1": "F1",
             "sd": "SD", "pct": "%", "std": "SD", "uba": "UBA", "km": "km", "sklearn": "scikit-learn"}


def pretty_header(col: str) -> str:
    """'cv_roc_auc_std' -> 'CV ROC AUC SD'; mixed-case dataset columns keep their spelling."""
    if col != col.lower():
        return col.replace("_", " ")
    words = [_ACRONYMS.get(w, w) for w in col.split("_") if w]
    if words and words[0] == words[0].lower():
        words[0] = words[0][:1].upper() + words[0][1:]
    return " ".join(words)


def _is_missing(v) -> bool:
    return v is None or v is pd.NA or (isinstance(v, (float, np.floating)) and np.isnan(v))


class DataFrameModel(QAbstractTableModel):
    """pandas frame -> Qt table. ``tones`` maps a column to fn(value) -> tone (coloured, bold text);
    ``row_tone`` maps a row (Series) to a tone used as a soft row background;
    ``highlight_missing`` tints empty cells so data gaps are visible."""

    def __init__(self, df: pd.DataFrame | None = None, formats: dict[str, Callable] | None = None):
        super().__init__()
        self._df = pd.DataFrame() if df is None else df.reset_index(drop=True)
        self._formats = formats or {}
        self._tones: dict[str, Callable] = {}
        self._row_tone: Callable | None = None
        self.highlight_missing = False

    def set_frame(self, df: pd.DataFrame, formats: dict[str, Callable] | None = None,
                  tones: dict[str, Callable] | None = None, row_tone: Callable | None = None) -> None:
        self.beginResetModel()
        self._df = df.reset_index(drop=True)
        if formats is not None:
            self._formats = formats
        self._tones = tones or {}
        self._row_tone = row_tone
        self.endResetModel()

    def _tone_for(self, row: int, col: str, v):
        fn = self._tones.get(col)
        if fn is None:
            return None
        try:
            return fn(v)
        except Exception:
            return None

    @property
    def frame(self) -> pd.DataFrame:
        return self._df

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._df)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self._df.shape[1]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        v = self._df.iat[index.row(), index.column()]
        col = self._df.columns[index.column()]
        if role == Qt.DisplayRole:
            if _is_missing(v):
                return "—"
            if col in self._formats:
                try:
                    return self._formats[col](v)
                except Exception:
                    return str(v)
            if isinstance(v, (float, np.floating)):
                return f"{v:,.4g}" if abs(v) < 1e6 else f"{v:,.0f}"
            return str(v)
        if role == Qt.UserRole:      # raw value for sorting
            if isinstance(v, (np.integer, np.floating)):
                return float(v)
            return v
        if role == Qt.TextAlignmentRole and isinstance(v, (int, float, np.integer, np.floating)):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ForegroundRole:
            if _is_missing(v):
                return QColor(theme.current().text_muted)
            tone = self._tone_for(index.row(), col, v)
            if tone:
                return QColor(theme.tone_colors(tone)[0])
        if role == Qt.FontRole and self._tones.get(col) is not None and self._tone_for(index.row(), col, v):
            f = QFont()
            f.setWeight(QFont.Weight.DemiBold)
            return f
        if role == Qt.BackgroundRole:
            if self.highlight_missing and _is_missing(v):
                return QColor(theme.current().gold_soft)
            if self._row_tone is not None:
                try:
                    tone = self._row_tone(self._df.iloc[index.row()])
                except Exception:
                    tone = None
                if tone:
                    return QColor(theme.tone_colors(tone)[1])
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return pretty_header(str(self._df.columns[section]))
        return str(section + 1)


class _Proxy(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.column_filter: tuple[int, str] | None = None
        self.setSortRole(Qt.UserRole)
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.setFilterKeyColumn(-1)

    def lessThan(self, left, right):
        a, b = left.data(Qt.UserRole), right.data(Qt.UserRole)
        try:
            if a is None or (isinstance(a, float) and np.isnan(a)):
                return True
            if b is None or (isinstance(b, float) and np.isnan(b)):
                return False
            return a < b
        except TypeError:
            return str(a) < str(b)

    def filterAcceptsRow(self, row, parent):
        if self.column_filter:
            col, val = self.column_filter
            idx = self.sourceModel().index(row, col, parent)
            if str(self.sourceModel().data(idx, Qt.DisplayRole)) != val:
                return False
        return super().filterAcceptsRow(row, parent)


class DataTable(QWidget):
    """Table with text search, a per-column value filter, sorting and a row counter."""

    row_selected = Signal(int)      # source row index

    def __init__(self, searchable: bool = True, filterable: bool = True, selectable_rows: bool = True):
        super().__init__()
        self.model = DataFrameModel()
        self.proxy = _Proxy()
        self.proxy.setSourceModel(self.model)
        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.setSortingEnabled(True)
        self.view.setAlternatingRowColors(True)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(32)
        self.view.setShowGrid(False)
        self.view.setWordWrap(False)
        self.view.horizontalHeader().setHighlightSections(False)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.view.horizontalHeader().setStretchLastSection(True)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SingleSelection if selectable_rows
                                   else QAbstractItemView.NoSelection)
        self.view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.view.selectionModel().currentRowChanged.connect(self._row_changed)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        self.search.textChanged.connect(self._update_count)
        self.filter_col = QComboBox()
        self.filter_val = QComboBox()
        self.filter_col.currentIndexChanged.connect(self._fill_values)
        self.filter_val.currentIndexChanged.connect(self._apply_filter)
        self.count = QLabel()
        self.count.setObjectName("Muted")
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)
        if searchable:
            bar.addWidget(self.search, 2)
        if filterable:
            fl = QLabel("Filter")
            fl.setObjectName("Muted")
            bar.addWidget(fl)
            bar.addWidget(self.filter_col, 1)
            bar.addWidget(self.filter_val, 1)
        bar.addStretch(0)
        bar.addWidget(self.count)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        if searchable or filterable:
            lay.addLayout(bar)
        lay.addWidget(self.view)
        self.filterable = filterable

    def set_frame(self, df: pd.DataFrame, formats: dict[str, Callable] | None = None,
                  tones: dict[str, Callable] | None = None, row_tone: Callable | None = None) -> None:
        self.proxy.column_filter = None
        self.model.set_frame(df, formats, tones, row_tone)
        self.filter_col.blockSignals(True)
        self.filter_col.clear()
        self.filter_col.addItem("(no filter)")
        for c in df.columns:
            if df[c].nunique(dropna=False) <= 40:
                self.filter_col.addItem(str(c))
        self.filter_col.blockSignals(False)
        self._fill_values()
        self.view.resizeColumnsToContents()
        for i in range(df.shape[1]):
            if self.view.columnWidth(i) > 260:
                self.view.setColumnWidth(i, 260)
        self._update_count()

    def frame(self) -> pd.DataFrame:
        return self.model.frame

    def visible_frame(self) -> pd.DataFrame:
        rows = [self.proxy.mapToSource(self.proxy.index(r, 0)).row() for r in range(self.proxy.rowCount())]
        return self.model.frame.iloc[rows].reset_index(drop=True)

    def selected_row(self) -> int | None:
        idx = self.view.currentIndex()
        return self.proxy.mapToSource(idx).row() if idx.isValid() else None

    def selected_record(self) -> dict | None:
        r = self.selected_row()
        return None if r is None else self.model.frame.iloc[r].to_dict()

    def _row_changed(self, cur, _prev):
        if cur.isValid():
            self.row_selected.emit(self.proxy.mapToSource(cur).row())

    def _fill_values(self):
        self.filter_val.blockSignals(True)
        self.filter_val.clear()
        col = self.filter_col.currentText()
        df = self.model.frame
        if col and col != "(no filter)" and col in df.columns:
            ci = list(df.columns).index(col)
            vals = sorted({str(self.model.data(self.model.index(r, ci))) for r in range(len(df))})
            self.filter_val.addItems(["(all)"] + vals)
        self.filter_val.setEnabled(self.filter_val.count() > 0)
        self.filter_val.blockSignals(False)
        self._apply_filter()

    def _apply_filter(self):
        col, val = self.filter_col.currentText(), self.filter_val.currentText()
        df = self.model.frame
        if col in df.columns and val and val != "(all)":
            self.proxy.column_filter = (list(df.columns).index(col), val)
        else:
            self.proxy.column_filter = None
        self.proxy.invalidate()
        self._update_count()

    def _update_count(self, *_):
        total, shown = self.model.rowCount(), self.proxy.rowCount()
        self.count.setText(f"{shown:,} of {total:,} rows" if shown != total else f"{total:,} rows")


# ======================================================================== background work
class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str, str)
    progress = Signal(str)


class Task(QRunnable):
    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _Signals()
        self.setAutoDelete(True)

    @Slot()
    def run(self):
        try:
            if self.kwargs.pop("_with_progress", False):
                self.kwargs["progress"] = self.signals.progress.emit
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # reported to the UI, never swallowed
            self.signals.failed.emit(str(exc), traceback.format_exc())
        else:
            self.signals.done.emit(result)


_running: set = set()


def run_task(fn: Callable, *args, on_done: Callable[[Any], None] | None = None,
             on_error: Callable[[str, str], None] | None = None, on_progress: Callable[[str], None] | None = None,
             **kwargs) -> Task:
    """Run ``fn`` in the thread pool; callbacks run on the GUI thread."""
    if on_progress:
        kwargs["_with_progress"] = True
    t = Task(fn, *args, **kwargs)
    _running.add(t.signals)
    if on_done:
        t.signals.done.connect(on_done)
    if on_error:
        t.signals.failed.connect(on_error)
    if on_progress:
        t.signals.progress.connect(on_progress)
    t.signals.done.connect(lambda *_: _running.discard(t.signals))
    t.signals.failed.connect(lambda *_: _running.discard(t.signals))
    QThreadPool.globalInstance().start(t)
    return t


def show_error(parent: QWidget, title: str, message: str, details: str | None = None) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle(title)
    box.setText(message)
    if details:
        box.setDetailedText(details)
    box.exec()


def confirm(parent: QWidget, title: str, message: str, ok_text: str = "Continue", danger: bool = False) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning if danger else QMessageBox.Question)
    box.setWindowTitle(title)
    box.setText(message)
    ok = box.addButton(ok_text, QMessageBox.DestructiveRole if danger else QMessageBox.AcceptRole)
    box.addButton("Cancel", QMessageBox.RejectRole)
    box.exec()
    return box.clickedButton() is ok


def fmt_pct(v) -> str:
    return "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.1%}"


def fmt_num(v, d=3) -> str:
    return "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.{d}f}"


# ======================================================================== semantic tones for table cells
DATA_SOURCE_LABELS = {"ORIGINAL_HYBRID": "Original dataset", "SYNTHETIC": "Synthetic demo",
                      "MANUAL": "Entered in app", "UPLOAD": "Uploaded file"}
ATTENDANCE_SOURCE_LABELS = {"ORIGINAL_SIMULATED": "Simulated (dataset)", "SYNTHETIC": "Synthetic (demo)",
                            "MANUAL": "Recorded actual", "UPLOAD": "Recorded actual (file)"}


def status_tone(v) -> str | None:
    return {"Completed": "good", "Scheduled": "blue", "Cancelled": "critical", "Registered": "purple",
            "Attended": "good", "No-show": "gold", "Upcoming": "blue", "Ongoing": "purple", "Waitlisted": "gold",
            "Absent": "gold", "critical": "critical", "warning": "gold", "info": "blue"}.get(str(v))


def attendance_tone(v) -> str | None:
    s = str(v)
    if "simulated" in s:
        return "purple"
    return {"Attended": "good", "Absent": "gold"}.get(s)


def source_tone(v) -> str | None:
    s = str(v)
    if "SYNTHETIC" in s.upper() or s.startswith("Synthetic"):
        return "gold"
    if s in ("ORIGINAL_HYBRID", "Original dataset", "ORIGINAL_SIMULATED", "Simulated (dataset)"):
        return "purple"
    if s in ("MANUAL", "UPLOAD", "Entered in app", "Uploaded file") or s.startswith("Recorded actual"):
        return "blue"
    return None


def outcome_tone(v) -> str | None:
    s = str(v)
    if s in ("Will attend", "attended", "1") or s.startswith("Will attend"):
        return "good"
    if s in ("Will not attend", "absent", "0") or s.startswith("Will not"):
        return "gold"
    return None


def yes_tone(v) -> str | None:
    return "gold" if str(v).lower() in ("yes", "1", "true") else None
