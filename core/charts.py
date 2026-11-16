"""Chart builders shared by the desktop app and the PDF/Excel reports (EventPulse cream theme).

Every builder returns a matplotlib ``Figure``. The GUI embeds it in a canvas and adds
hover tooltips; the report generator saves it as PNG. Colours follow a validated
categorical palette (fixed slot order, CVD-checked against both chart surfaces).
"""
from __future__ import annotations

from dataclasses import dataclass

import matplotlib

matplotlib.use("Agg", force=False)
import numpy as np  # noqa: E402
from matplotlib import font_manager, rcParams  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.ticker import FuncFormatter, MaxNLocator  # noqa: E402


@dataclass(frozen=True)
class Theme:
    name: str
    surface: str
    text: str
    text_secondary: str
    grid: str
    series: tuple[str, ...]
    seq: tuple[str, ...]          # sequential blue, light -> dark (on this surface)
    critical: str


# Palettes validated with the dataviz palette checker (lightness band, chroma floor, colour-vision
# separation, contrast) against each surface: slot 0 = attend (purple), slot 1 = not attend (amber).
CREAM = Theme("cream", surface="#ffffff", text="#29263a", text_secondary="#6b6680", grid="#ece8f1",
              series=("#6956a5", "#c47f14", "#1e8fa8", "#b8496e"),
              seq=("#f4f1fb", "#d9d1f0", "#b1a3de", "#8572c2", "#5a479a"), critical="#c2413b")
EVENING = Theme("evening", surface="#262231", text="#eeebf5", text_secondary="#b3adc6", grid="#38334a",
                series=("#8c7ad6", "#c4861a", "#1f9cb8", "#d0668b"),
                seq=("#2e2a3b", "#463c72", "#6956a5", "#9282d4", "#c9bef0"), critical="#e0645c")
LIGHT = CREAM        # reports and exported charts
DARK = EVENING       # kept for compatibility with older callers

ATTEND, NOT_ATTEND = 0, 1      # series slots used for "attend" / "not attend" throughout the app


def _fig(theme: Theme, size=(6.4, 3.6)) -> tuple[Figure, object]:
    fig = Figure(figsize=size, dpi=100, facecolor=theme.surface, layout="constrained")
    ax = fig.add_subplot(111)
    _style(ax, theme)
    return fig, ax


def _style(ax, theme: Theme) -> None:
    ax.set_facecolor(theme.surface)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(theme.grid)
    ax.tick_params(colors=theme.text_secondary, labelsize=8.5, length=0)
    ax.yaxis.grid(True, color=theme.grid, linewidth=0.7)
    ax.set_axisbelow(True)
    ax.title.set_color(theme.text)
    ax.xaxis.label.set_color(theme.text_secondary)
    ax.yaxis.label.set_color(theme.text_secondary)


def _title(ax, theme, title, subtitle=None):
    ax.set_title(title, loc="left", fontsize=10.5, fontweight="semibold", color=theme.text,
                 pad=15 if subtitle else 9)
    if subtitle:
        ax.text(0, 1.015, subtitle, transform=ax.transAxes, fontsize=8, color=theme.text_secondary, va="bottom")


def _empty(theme: Theme, title: str, message: str, size=(6.4, 3.6)) -> Figure:
    fig, ax = _fig(theme, size)
    _title(ax, theme, title)
    ax.axis("off")
    ax.text(0.5, 0.5, message, ha="center", va="center", color=theme.text_secondary, fontsize=10,
            transform=ax.transAxes, wrap=True)
    return fig


def _pick_font() -> list[str]:
    """Use a clean system sans-serif when present (macOS ships Helvetica Neue/Arial),
    falling back to matplotlib's bundled DejaVu Sans. Only installed families are listed,
    so matplotlib never warns about missing fonts."""
    have = {f.name for f in font_manager.fontManager.ttflist}
    wanted = [f for f in ("Inter", "Helvetica Neue", "Avenir Next", "Arial") if f in have]
    return wanted + ["DejaVu Sans"]


rcParams["font.family"] = "sans-serif"
rcParams["font.sans-serif"] = _pick_font()

pct = FuncFormatter(lambda v, _: f"{v:.0%}")


def bar(categories, values, title, theme=CREAM, ylabel=None, subtitle=None, percent=False, slot=0,
        horizontal=False, value_labels=True, errors=None, size=(6.4, 3.6)) -> Figure:
    categories = [str(c) for c in categories]
    values = [float(v) if v is not None and not (isinstance(v, float) and np.isnan(v)) else 0.0 for v in values]
    if not categories:
        return _empty(theme, title, "No data yet", size)
    fig, ax = _fig(theme, size)
    color = theme.series[slot % len(theme.series)]
    if horizontal:
        y = np.arange(len(categories))[::-1]
        bars = ax.barh(y, values, color=color, height=0.64, xerr=errors, edgecolor=theme.surface, linewidth=1,
                       error_kw=dict(ecolor=theme.text_secondary, elinewidth=1, capsize=2))
        ax.set_yticks(y, categories)
        ax.xaxis.grid(True, color=theme.grid, linewidth=0.7)
        ax.yaxis.grid(False)
        if percent:
            ax.xaxis.set_major_formatter(pct)
        if ylabel:
            ax.set_xlabel(ylabel, fontsize=8.5)
    else:
        x = np.arange(len(categories))
        bars = ax.bar(x, values, color=color, width=0.64, yerr=errors, edgecolor=theme.surface, linewidth=1)
        many, long_ = len(categories) > 12, max(map(len, categories)) > 8
        if long_ and len(categories) <= 8:
            # few, long categories: wrap onto two lines instead of rotating (rotated labels can be
            # clipped at the figure edge with wide system fonts)
            import textwrap
            ax.set_xticks(x, [textwrap.fill(c, 13) for c in categories], rotation=0, ha="center", fontsize=8)
        else:
            ax.set_xticks(x, categories, rotation=90 if many else (30 if long_ else 0),
                          ha="center" if many or not long_ else "right", fontsize=7 if many else 8.5)
        if percent:
            ax.yaxis.set_major_formatter(pct)
            ax.set_ylim(0, max(1.0, max(values) * 1.12))
        if ylabel:
            ax.set_ylabel(ylabel, fontsize=8.5)
    if value_labels and len(categories) <= 12:
        fmt = (lambda v: f"{v:.0%}") if percent else (lambda v: f"{v:,.0f}" if abs(v) >= 10 else f"{v:,.2f}".rstrip("0").rstrip("."))
        ax.bar_label(bars, labels=[fmt(v) for v in values], padding=2, fontsize=7.5, color=theme.text_secondary)
    _title(ax, theme, title, subtitle)
    fig._ep_hover = [(b, f"{c}: {('%.1f%%' % (v * 100)) if percent else format(v, ',.4g')}")
                     for b, c, v in zip(bars, categories, values)]
    return fig


def grouped_bar(categories, series: dict[str, list[float]], title, theme=CREAM, ylabel=None, subtitle=None,
                size=(6.4, 3.6)) -> Figure:
    if not categories:
        return _empty(theme, title, "No data yet", size)
    fig, ax = _fig(theme, size)
    n = len(series)
    width = 0.8 / n
    x = np.arange(len(categories))
    hover = []
    for i, (name, vals) in enumerate(series.items()):
        b = ax.bar(x + (i - (n - 1) / 2) * width, vals, width * 0.92, label=name,
                   color=theme.series[i % len(theme.series)])
        hover += [(bb, f"{c} - {name}: {v:,.1f}") for bb, c, v in zip(b, categories, vals)]
    tilt = len(categories) > 6 or max(len(str(c)) for c in categories) * len(categories) > 60
    ax.set_xticks(x, [str(c) for c in categories], rotation=25 if tilt else 0, ha="right" if tilt else "center",
                  fontsize=8 if tilt else 8.5)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=8.5)
    leg = ax.legend(frameon=False, fontsize=8, loc="lower right", bbox_to_anchor=(1.0, 1.0), ncol=n,
                    borderaxespad=0.2)
    for t in leg.get_texts():
        t.set_color(theme.text)
    ax.set_ylim(0, max(max(v) for v in series.values()) * 1.08 or 1)
    _title(ax, theme, title, subtitle)
    fig._ep_hover = hover
    return fig


def histogram(counts, edges, title, theme=CREAM, threshold=None, subtitle=None, size=(6.4, 3.6)) -> Figure:
    if not counts or sum(counts) == 0:
        return _empty(theme, title, "No predictions yet", size)
    fig, ax = _fig(theme, size)
    edges = np.asarray(edges, float)
    lefts, widths = edges[:-1], np.diff(edges)
    hover = []
    for left, w, c in zip(lefts, widths, counts):
        slot = ATTEND if threshold is None or left + w / 2 >= threshold else NOT_ATTEND
        b = ax.bar(left, c, width=w * 0.94, align="edge", color=theme.series[slot])
        hover.append((b[0], f"{left:.1f}-{left + w:.1f}: {int(c)} registration(s)"))
    if threshold is not None:
        ax.axvline(threshold, color=theme.text, linewidth=1, linestyle=(0, (3, 3)))
        ax.text(threshold, ax.get_ylim()[1] * 0.97, f" threshold {threshold:.2f}", color=theme.text_secondary,
                fontsize=7.5, va="top")
    ax.set_xlim(0, 1)
    ax.set_xlabel("Predicted attendance probability", fontsize=8.5)
    ax.set_ylabel("Registrations", fontsize=8.5)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    _title(ax, theme, title, subtitle)
    fig._ep_hover = hover
    return fig


def roc(curves: dict[str, dict], title="ROC curve (holdout)", theme=CREAM, subtitle=None, size=(4.8, 4.0)) -> Figure:
    curves = {k: v for k, v in curves.items() if v}
    if not curves:
        return _empty(theme, title, "No ROC data", size)
    fig, ax = _fig(theme, size)
    ax.plot([0, 1], [0, 1], color=theme.text_secondary, linewidth=1, linestyle=(0, (3, 3)), label="Chance (0.50)")
    for i, (name, c) in enumerate(list(curves.items())[:4]):
        ax.plot(c["fpr"], c["tpr"], color=theme.series[i], linewidth=2, label=name, drawstyle="steps-post")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.01)
    ax.set_xlabel("False positive rate", fontsize=8.5)
    ax.set_ylabel("True positive rate", fontsize=8.5)
    ax.xaxis.grid(True, color=theme.grid, linewidth=0.7)
    leg = ax.legend(frameon=False, fontsize=7.5, loc="lower right")
    for t in leg.get_texts():
        t.set_color(theme.text)
    _title(ax, theme, title, subtitle)
    return fig


def confusion(cm, title="Confusion matrix (holdout)", theme=CREAM, size=(4.2, 3.6)) -> Figure:
    if not cm:
        return _empty(theme, title, "No confusion matrix", size)
    cm = np.asarray(cm, float)
    fig, ax = _fig(theme, size)
    ax.grid(False)
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list("ep_seq", theme.seq)
    ax.imshow(cm, cmap=cmap, vmin=0, vmax=max(cm.max(), 1))
    labels = ["Not attend", "Attend"]
    ax.set_xticks([0, 1], labels)
    ax.set_yticks([0, 1], labels)
    ax.set_xlabel("Predicted", fontsize=8.5)
    ax.set_ylabel("Actual", fontsize=8.5)
    names = [["True negative", "False positive"], ["False negative", "True positive"]]
    total = cm.sum()
    for i in range(2):
        for j in range(2):
            r, g, b, _ = cmap(cm[i, j] / max(cm.max(), 1))
            luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
            col = "#0b0b0b" if luminance > 0.5 else "#ffffff"
            ax.text(j, i, f"{int(cm[i, j])}\n{names[i][j]}\n{cm[i, j] / total:.0%}", ha="center", va="center",
                    fontsize=8.5, color=col)
    for s in ax.spines.values():
        s.set_visible(False)
    _title(ax, theme, title)
    return fig


def line(x_labels, values, title, theme=CREAM, ylabel=None, subtitle=None, percent=False, size=(6.4, 3.6)) -> Figure:
    vals = np.array([np.nan if v is None else float(v) for v in values])
    if not len(vals) or np.all(np.isnan(vals)):
        return _empty(theme, title, "No data yet", size)
    fig, ax = _fig(theme, size)
    x = np.arange(len(vals))
    ax.plot(x, vals, color=theme.series[0], linewidth=2, marker="o", markersize=5,
            markeredgecolor=theme.surface, markeredgewidth=1.5)
    ax.fill_between(x, vals, 0, color=theme.series[0], alpha=0.07, linewidth=0)
    step = max(1, len(x_labels) // 12)
    ax.set_xticks(x[::step], [str(v) for v in x_labels][::step], rotation=45, ha="right")
    if percent:
        ax.yaxis.set_major_formatter(pct)
        ax.set_ylim(0, 1.05)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=8.5)
    _title(ax, theme, title, subtitle)
    fig._ep_points = [(xi, vi, f"{lbl}: {('%.0f%%' % (vi * 100)) if percent else format(vi, ',.3g')}")
                      for xi, vi, lbl in zip(x, vals, x_labels) if not np.isnan(vi)]
    return fig


def attendance_split(attend: int, not_attend: int, title, theme=CREAM, labels=("Will attend", "Will not attend"),
                     subtitle=None, size=(6.4, 1.9)) -> Figure:
    total = attend + not_attend
    if not total:
        return _empty(theme, title, "No data yet", size)
    fig, ax = _fig(theme, size)
    ax.grid(False)
    ax.axis("off")
    a = ax.barh([0], [attend], color=theme.series[ATTEND], height=0.5)
    b = ax.barh([0], [not_attend], left=[attend + total * 0.004], color=theme.series[NOT_ATTEND], height=0.5)
    ax.set_xlim(0, total * 1.004)
    ax.text(0, -0.5, f"{labels[0]}: {attend:,} ({attend / total:.0%})", color=theme.text, fontsize=9, va="top")
    ax.text(total, -0.5, f"{labels[1]}: {not_attend:,} ({not_attend / total:.0%})", color=theme.text, fontsize=9,
            va="top", ha="right")
    ax.set_ylim(-1.0, 0.5)
    _title(ax, theme, title, subtitle)
    fig._ep_hover = [(a[0], f"{labels[0]}: {attend:,}"), (b[0], f"{labels[1]}: {not_attend:,}")]
    return fig


def save_png(fig: Figure, path, dpi=160) -> None:
    fig.savefig(path, dpi=dpi, facecolor=fig.get_facecolor())
