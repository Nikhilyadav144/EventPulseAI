"""Visual theme: EventPulse design tokens and the Qt style sheet.

Two palettes share one stylesheet:
* ``cream``   (default) - warm off-white canvas, white surfaces, lavender highlights, purple
                          primary, soft blue information and gold highlights.
* ``evening`` - the same identity on a deep plum canvas for low-light use.
"""
from __future__ import annotations

from dataclasses import dataclass

from core import charts


@dataclass(frozen=True)
class Palette:
    name: str
    window: str          # app canvas
    sidebar: str
    surface: str         # cards
    surface_alt: str     # table header, alternate rows, subtle fills
    input: str           # text fields and drop-downs
    border: str
    border_strong: str
    text: str
    text_secondary: str
    text_muted: str
    accent: str          # primary purple
    accent_hover: str
    accent_pressed: str
    accent_text: str
    accent_soft: str     # lavender: active navigation, selection, chips
    info: str            # soft blue family
    info_soft: str
    gold: str
    gold_soft: str
    gold_text: str
    good: str
    good_soft: str
    warning: str
    critical: str
    critical_soft: str
    chart: charts.Theme

    # legacy name used by older code
    @property
    def accent_light(self) -> str:
        return self.accent_soft


CREAM = Palette(
    "cream", window="#f8f6f2", sidebar="#fdfcfa", surface="#ffffff", surface_alt="#faf9fc", input="#ffffff",
    border="#ebe7ef", border_strong="#d9d4e3", text="#29263a", text_secondary="#57526b", text_muted="#8a859c",
    accent="#6956a5", accent_hover="#5b4895", accent_pressed="#4d3d82", accent_text="#ffffff",
    accent_soft="#f0edfa", info="#2f5f96", info_soft="#dce8f8", gold="#e9bc65", gold_soft="#fbf3e2",
    gold_text="#7a5714", good="#2f7a52", good_soft="#e5f3ea", warning="#9a6a0b", critical="#b5393b",
    critical_soft="#fbe9e8", chart=charts.CREAM)

EVENING = Palette(
    "evening", window="#1c1924", sidebar="#211e2a", surface="#262231", surface_alt="#2c2839", input="#2e2a3b",
    border="#383346", border_strong="#4a4460", text="#eeebf5", text_secondary="#c6c0d8", text_muted="#938da8",
    accent="#8c7ad6", accent_hover="#9d8de0", accent_pressed="#7a68c4", accent_text="#ffffff",
    accent_soft="#352f4d", info="#a9c6ee", info_soft="#23324a", gold="#e9bc65", gold_soft="#382e1d",
    gold_text="#f0cd88", good="#7fcda3", good_soft="#1e3329", warning="#e9bc65", critical="#f0a09b",
    critical_soft="#3c2224", chart=charts.EVENING)

THEMES = {"cream": CREAM, "evening": EVENING}
LEGACY = {"dark": "cream", "light": "cream"}     # v1.0 settings move to the new default design
DARK, LIGHT = EVENING, CREAM                     # compatibility aliases

_current = CREAM


def current() -> Palette:
    return _current


def normalise(name: str | None) -> str:
    name = (name or "cream").lower()
    name = LEGACY.get(name, name)
    return name if name in THEMES else "cream"


def set_current(name: str | None) -> Palette:
    global _current
    _current = THEMES[normalise(name)]
    return _current


def tone_colors(tone: str, p: Palette | None = None) -> tuple[str, str]:
    """(foreground, background) for a semantic tone used by badges, chips and table cells."""
    p = p or _current
    return {
        "purple": (p.accent, p.accent_soft), "accent": (p.accent, p.accent_soft),
        "blue": (p.info, p.info_soft), "info": (p.info, p.info_soft),
        "gold": (p.gold_text, p.gold_soft), "warning": (p.gold_text, p.gold_soft),
        "good": (p.good, p.good_soft), "green": (p.good, p.good_soft),
        "critical": (p.critical, p.critical_soft), "red": (p.critical, p.critical_soft),
        "neutral": (p.text_secondary, p.surface_alt),
    }.get(tone, (p.text_secondary, p.surface_alt))


def stylesheet(p: Palette) -> str:
    badge_rules = "\n".join(
        f'QLabel#Badge[tone="{t}"] {{ color: {fg}; background: {bg}; }}'
        for t in ("purple", "blue", "gold", "good", "critical", "neutral")
        for fg, bg in [tone_colors(t, p)])
    chip_rules = "\n".join(
        f'QLabel#KpiChip[tone="{t}"] {{ color: {fg}; background: {bg}; }}'
        for t in ("purple", "blue", "gold", "good", "critical", "neutral")
        for fg, bg in [tone_colors(t, p)])
    return f"""
* {{ font-size: 13px; color: {p.text}; }}
QMainWindow, QWidget#Root {{ background: {p.window}; }}
QWidget#Page {{ background: {p.window}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QWidget#PageHeader, QWidget#NoticeHolder {{ background: transparent; }}

/* ---------- sidebar ---------- */
QFrame#Sidebar {{ background: {p.sidebar}; border-right: 1px solid {p.border}; }}
QScrollArea#SidebarScroll, QWidget#SidebarNav {{ background: transparent; border: none; }}
QLabel#Brand {{ font-size: 17px; font-weight: 700; color: {p.text}; }}
QLabel#Tagline {{ font-size: 11px; color: {p.text_muted}; }}
QLabel#NavSection {{ font-size: 10px; font-weight: 600; color: {p.text_muted}; letter-spacing: 0.8px;
    padding-left: 12px; }}
QPushButton#NavButton {{ text-align: left; padding: 9px 12px; border: none; border-radius: 10px;
    color: {p.text_secondary}; background: transparent; font-size: 13px; }}
QPushButton#NavButton:hover {{ background: {p.surface_alt}; color: {p.text}; }}
QPushButton#NavButton:checked {{ background: {p.accent_soft}; color: {p.accent}; font-weight: 600; }}
QFrame#SidebarCard {{ background: {p.surface_alt}; border: 1px solid {p.border}; border-radius: 12px; }}
QLabel#SidebarKey {{ color: {p.text_muted}; font-size: 10px; font-weight: 600; letter-spacing: 0.6px; }}
QLabel#SidebarValue {{ color: {p.text}; font-size: 12px; font-weight: 600; }}
QLabel#SidebarFooter {{ color: {p.text_muted}; font-size: 11px; }}

/* ---------- demo banner ---------- */
QFrame#DemoBanner {{ background: {p.gold_soft}; border-bottom: 1px solid {p.gold}; }}
QFrame#DemoBanner QLabel {{ color: {p.gold_text}; }}

/* ---------- header & text ---------- */
QLabel#PageTitle {{ font-size: 25px; font-weight: 700; color: {p.text}; }}
QLabel#PageSubtitle {{ color: {p.text_secondary}; font-size: 13px; }}
QLabel#SectionTitle {{ font-size: 15px; font-weight: 600; color: {p.text}; }}
QLabel#SubTitle {{ font-size: 13px; font-weight: 600; color: {p.text}; }}
QLabel#Muted {{ color: {p.text_muted}; font-size: 12px; }}
QLabel#Secondary {{ color: {p.text_secondary}; }}
QLabel#InfoKey {{ color: {p.text_muted}; font-size: 12px; }}
QLabel#InfoValue {{ color: {p.text}; }}
QLabel#Eyebrow {{ color: {p.accent}; font-size: 11px; font-weight: 700; letter-spacing: 0.8px; }}
QLabel#AboutName {{ font-size: 22px; font-weight: 700; }}
QLabel#AboutTagline {{ font-size: 14px; color: {p.accent}; font-weight: 600; }}
QLabel#Badge {{ border-radius: 9px; padding: 2px 9px; font-size: 11px; font-weight: 600; }}
{badge_rules}

/* ---------- cards ---------- */
QFrame#Card {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 14px; }}
QFrame#SubtleCard {{ background: {p.surface_alt}; border: 1px solid {p.border}; border-radius: 12px; }}
QFrame#KpiCard {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 14px; }}
QLabel#KpiTitle {{ color: {p.text_secondary}; font-size: 12px; font-weight: 600; }}
QLabel#KpiValue {{ font-size: 26px; font-weight: 700; color: {p.text}; }}
QLabel#KpiCaption {{ color: {p.text_muted}; font-size: 11px; }}
QLabel#KpiChip {{ border-radius: 8px; font-size: 13px; font-weight: 700; }}
{chip_rules}
QFrame#Notice {{ background: {p.gold_soft}; border: 1px solid {p.gold}; border-radius: 10px; }}
QFrame#Notice QLabel {{ color: {p.gold_text}; }}
QFrame#NoteNotice {{ background: {p.info_soft}; border: 1px solid {p.info_soft}; border-radius: 10px; }}
QFrame#NoteNotice QLabel {{ color: {p.info}; }}
QFrame#ErrorNotice {{ background: {p.critical_soft}; border: 1px solid {p.critical}; border-radius: 10px; }}
QFrame#ErrorNotice QLabel {{ color: {p.critical}; }}
QFrame#OkNotice {{ background: {p.good_soft}; border: 1px solid {p.good}; border-radius: 10px; }}
QFrame#OkNotice QLabel {{ color: {p.good}; }}
QFrame#Divider {{ background: {p.border}; border: none; max-height: 1px; min-height: 1px; }}

/* ---------- buttons ---------- */
QPushButton {{ background: {p.surface}; border: 1px solid {p.border_strong}; border-radius: 9px; padding: 7px 15px;
    color: {p.text}; font-weight: 500; }}
QPushButton:hover {{ background: {p.accent_soft}; border-color: {p.accent}; color: {p.accent}; }}
QPushButton:pressed {{ background: {p.accent_soft}; }}
QPushButton:disabled {{ color: {p.text_muted}; background: {p.surface_alt}; border-color: {p.border}; }}
QPushButton#Primary {{ background: {p.accent}; border: 1px solid {p.accent}; color: {p.accent_text}; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {p.accent_hover}; border-color: {p.accent_hover}; color: {p.accent_text}; }}
QPushButton#Primary:pressed {{ background: {p.accent_pressed}; }}
QPushButton#Primary:disabled {{ background: {p.border}; border-color: {p.border}; color: {p.text_muted}; }}
QPushButton#Ghost {{ background: transparent; border: none; color: {p.accent}; padding: 7px 8px; font-weight: 600; }}
QPushButton#Ghost:hover {{ background: {p.accent_soft}; }}
QPushButton#Danger {{ background: transparent; border: 1px solid {p.critical}; color: {p.critical}; }}
QPushButton#Danger:hover {{ background: {p.critical_soft}; color: {p.critical}; }}
QPushButton#DemoButton {{ background: {p.surface}; border: 1px solid {p.gold}; color: {p.gold_text}; padding: 4px 12px;
    font-weight: 600; }}
QPushButton#DemoButton:hover {{ background: {p.gold_soft}; }}

/* ---------- inputs ---------- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QDateTimeEdit, QTimeEdit, QPlainTextEdit, QTextEdit {{
    background: {p.input}; border: 1px solid {p.border_strong}; border-radius: 8px; padding: 5px 9px;
    selection-background-color: {p.accent_soft}; selection-color: {p.text}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus, QDateTimeEdit:focus,
QTimeEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {p.accent}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QDateEdit:disabled,
QTimeEdit:disabled {{ color: {p.text_muted}; background: {p.surface_alt}; }}
QPlainTextEdit {{ background: {p.surface_alt}; }}
QComboBox QAbstractItemView {{ background: {p.surface}; border: 1px solid {p.border}; outline: none;
    selection-background-color: {p.accent_soft}; selection-color: {p.accent}; }}
QCheckBox, QRadioButton {{ spacing: 7px; color: {p.text}; }}
QGroupBox {{ border: 1px solid {p.border}; border-radius: 12px; margin-top: 14px; padding: 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {p.text_secondary}; }}

/* ---------- tables ---------- */
QTableView, QTableWidget {{ background: {p.surface}; alternate-background-color: {p.surface_alt};
    border: 1px solid {p.border}; border-radius: 10px; gridline-color: {p.border};
    selection-background-color: {p.accent_soft}; selection-color: {p.text}; outline: none; }}
QHeaderView {{ background: {p.surface_alt}; border: none; }}
QHeaderView::section {{ background: {p.surface_alt}; color: {p.text_secondary}; border: none;
    border-bottom: 1px solid {p.border}; padding: 7px 8px; font-weight: 600; font-size: 12px; }}
QTableCornerButton::section {{ background: {p.surface_alt}; border: none; }}

/* ---------- tabs (segmented) ---------- */
QTabWidget::pane {{ border: none; top: 4px; }}
QTabWidget::tab-bar {{ alignment: left; }}
QTabBar::tab {{ background: transparent; color: {p.text_secondary}; padding: 7px 14px; border: none;
    border-radius: 8px; margin-right: 4px; font-weight: 500; }}
QTabBar::tab:selected {{ background: {p.accent_soft}; color: {p.accent}; font-weight: 600; }}
QTabBar::tab:hover:!selected {{ background: {p.surface_alt}; color: {p.text}; }}
QTabBar::tab:disabled {{ color: {p.text_muted}; }}

/* ---------- misc ---------- */
QProgressBar {{ background: {p.accent_soft}; border: none; border-radius: 4px; max-height: 8px;
    text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 4px; }}
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 3px; }}
QScrollBar::handle:vertical {{ background: {p.border_strong}; border-radius: 4px; min-height: 36px; }}
QScrollBar::handle:vertical:hover {{ background: {p.text_muted}; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 3px; }}
QScrollBar::handle:horizontal {{ background: {p.border_strong}; border-radius: 4px; min-width: 36px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolTip {{ background: {p.surface}; color: {p.text}; border: 1px solid {p.border_strong}; padding: 7px;
    border-radius: 8px; }}
QStatusBar {{ background: {p.sidebar}; color: {p.text_muted}; border-top: 1px solid {p.border}; }}
QStatusBar QLabel {{ color: {p.text_muted}; }}
QMenu {{ background: {p.surface}; border: 1px solid {p.border}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {p.accent_soft}; color: {p.accent}; }}
QMessageBox, QDialog {{ background: {p.window}; }}
QSplitter::handle {{ background: {p.border}; }}
"""
