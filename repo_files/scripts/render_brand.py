"""Render the EventPulse AI brand assets (logo mark, app icon PNG and macOS .icns).

The mark: a purple tile with a white attendance "pulse" (recorded history, solid) that
continues as a dotted segment to a gold point - the prediction. No robots or brains.

    python scripts/render_brand.py          # writes assets/
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

PURPLE_TOP, PURPLE_BOTTOM = (124, 106, 191), (88, 70, 150)    # around #6956A5
WHITE = (255, 255, 255)
GOLD = (233, 188, 101)                                        # #E9BC65
LAVENDER = (240, 237, 250)

# pulse in tile coordinates (0..1): solid history, then a dotted forecast to the gold point
HISTORY = [(0.13, 0.58), (0.27, 0.58), (0.34, 0.47), (0.42, 0.69), (0.51, 0.29), (0.58, 0.53)]
FORECAST_END = (0.84, 0.44)


def _gradient(size: int) -> Image.Image:
    g = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / max(size - 1, 1)
        g.putpixel((0, y), tuple(int(a + (b - a) * t) for a, b in zip(PURPLE_TOP, PURPLE_BOTTOM)))
    return g.resize((size, size))


def tile(size: int, margin: float = 0.0, radius: float = 0.225, shadow: bool = False) -> Image.Image:
    """Square RGBA image of the mark. ``margin`` is the transparent border (fraction of size)."""
    ss = 4                                      # supersampling for smooth edges
    S = size * ss
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    m = int(S * margin)
    box = (m, m, S - m, S - m)
    side = S - 2 * m
    r = int(side * radius)
    if shadow:
        sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle((box[0], box[1] + int(S * 0.012), box[2], box[3] + int(S * 0.012)),
                                             r, fill=(41, 38, 58, 70))
        img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(S * 0.018)))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, r, fill=255)
    grad = _gradient(side)
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    layer.paste(grad, (m, m))
    layer.putalpha(mask)
    img = Image.alpha_composite(img, layer)

    d = ImageDraw.Draw(img)
    P = lambda p: (m + p[0] * side, m + p[1] * side)  # noqa: E731
    w = max(2, int(side * 0.058))
    pts = [P(p) for p in HISTORY]
    d.line(pts, fill=WHITE, width=w, joint="curve")
    for p in (pts[0], pts[-1]):
        d.ellipse((p[0] - w / 2, p[1] - w / 2, p[0] + w / 2, p[1] + w / 2), fill=WHITE)
    # dotted forecast segment
    (x0, y0), (x1, y1) = pts[-1], P(FORECAST_END)
    n = 3
    for i in range(1, n + 1):
        t = i / (n + 1)
        cx, cy = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
        rr = w * 0.40
        d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=(255, 255, 255, 215))
    gr = side * 0.068
    halo = side * 0.098
    d.ellipse((x1 - halo, y1 - halo, x1 + halo, y1 + halo), fill=(233, 188, 101, 60))
    d.ellipse((x1 - gr, y1 - gr, x1 + gr, y1 + gr), fill=GOLD)
    return img.resize((size, size), Image.LANCZOS)


def main(out: Path = ASSETS) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written = []
    # in-app logo mark (no margin) and the macOS icon (Apple grid: art ~80% of the canvas, soft shadow)
    tile(512).save(out / "logo_mark.png")
    written.append(out / "logo_mark.png")
    icon = tile(1024, margin=0.098, radius=0.225, shadow=True)
    icon.save(out / "icon.png")
    written.append(out / "icon.png")
    icon.save(out / "AppIcon.icns", format="ICNS",
              append_images=[icon.resize((s, s), Image.LANCZOS) for s in (16, 32, 64, 128, 256, 512)])
    written.append(out / "AppIcon.icns")
    return written


if __name__ == "__main__":
    for p in main(Path(sys.argv[1]) if len(sys.argv) > 1 else ASSETS):
        print("wrote", p)
