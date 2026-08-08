"""Deal-card PNG renderer (M3, D8): 1080×1350, auto-rendered from deal
data — the forwardable referral loop. Posting is manual-from-phone in v1.

Visual direction (D7, editorial data-design): warm paper background,
near-black ink, ONE amber signal color reserved for the price, serif
display + sans text, and the price-history sparkline as the visual
signature ("el precio normal, demostrado").

Fonts: serif display prefers Georgia (a D7 candidate class) / DejaVu
Serif; text prefers Segoe UI / Arial / DejaVu Sans. Vendoring Fraunces +
Inter (OFL) into assets/fonts/ is the branding upgrade — drop the files
in and _FONT_CANDIDATES picks them up first.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1350

# D7 palette (editorial, deliberately NOT the phosphor dark theme).
PAPER = (250, 246, 239)      # warm paper
INK = (28, 25, 23)           # near-black
INK_SOFT = (110, 104, 96)    # secondary text
AMBER = (217, 119, 6)        # THE signal color — prices/CTA only
LINE = (225, 218, 207)       # hairlines

REPO = Path(__file__).resolve().parents[1]

_FONT_CANDIDATES: dict[str, list[str]] = {
    "display": [
        str(REPO / "assets" / "fonts" / "Fraunces-SemiBold.ttf"),
        r"C:\Windows\Fonts\georgiab.ttf",
        r"C:\Windows\Fonts\georgia.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    ],
    "text": [
        str(REPO / "assets" / "fonts" / "Inter-Regular.ttf"),
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
    "text-bold": [
        str(REPO / "assets" / "fonts" / "Inter-SemiBold.ttf"),
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
}


def _font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES[kind]:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)  # type: ignore[return-value]


@dataclass(frozen=True)
class CardData:
    origin: str
    dest: str
    price: int
    currency: str
    normal: int | None          # baseline median, when known
    pct_below: float | None
    dates_line: str             # "10–14 sep · ida y vuelta"
    carrier: str | None


def _sparkline(draw: ImageDraw.ImageDraw, points: list[int],
               box: tuple[int, int, int, int], price: int) -> None:
    """Price history line + a dot on today's price. Empty/thin history
    draws the frame only — never fake data (D7)."""
    x0, y0, x1, y1 = box
    draw.rectangle(box, outline=LINE, width=2)
    if len(points) < 2:
        draw.text(((x0 + x1) // 2, (y0 + y1) // 2),
                  "histórico en construcción", font=_font("text", 28),
                  fill=INK_SOFT, anchor="mm")
        return
    lo, hi = min(points + [price]), max(points + [price])
    span = max(1, hi - lo)
    pad = 30
    n = len(points)
    xs = [x0 + pad + (x1 - x0 - 2 * pad) * i / (n - 1) for i in range(n)]
    ys = [y1 - pad - (y1 - y0 - 2 * pad) * (p - lo) / span for p in points]
    draw.line(list(zip(xs, ys)), fill=INK, width=4, joint="curve")
    # Today's price: the one amber accent inside the chart.
    draw.ellipse((xs[-1] - 10, ys[-1] - 10, xs[-1] + 10, ys[-1] + 10),
                 fill=AMBER)
    draw.text((x0 + pad, y0 + 16), f"máx {hi}", font=_font("text", 26),
              fill=INK_SOFT)
    draw.text((x0 + pad, y1 - pad - 34), f"mín {lo}",
              font=_font("text", 26), fill=INK_SOFT)


def render_deal_card(card: CardData, spark_prices: list[int],
                     out_path: str | Path | None = None) -> Image.Image:
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    margin = 84

    # Brand eyebrow.
    d.text((margin, 72), "VUELAZO", font=_font("text-bold", 34), fill=INK)
    d.text((W - margin, 72), "vuelazo.es", font=_font("text", 30),
           fill=INK_SOFT, anchor="ra")
    d.line((margin, 130, W - margin, 130), fill=LINE, width=2)

    # Route (serif display — the editorial voice). The arrow is drawn as
    # a line: serif faces like Georgia have no U+2192 glyph (tofu).
    display = _font("display", 110)
    x = margin
    d.text((x, 190), card.origin, font=display, fill=INK)
    x += int(d.textlength(card.origin, font=display)) + 36
    ay = 190 + 78
    d.line((x, ay, x + 70, ay), fill=INK, width=8)
    d.line((x + 70, ay, x + 48, ay - 20), fill=INK, width=8)
    d.line((x + 70, ay, x + 48, ay + 20), fill=INK, width=8)
    x += 70 + 36
    d.text((x, 190), card.dest, font=display, fill=INK)
    d.text((margin, 330), card.dates_line, font=_font("text", 40),
           fill=INK_SOFT)

    # Price: the ONE signal color.
    d.text((margin, 430), f"{card.price} {card.currency}",
           font=_font("display", 170), fill=AMBER)
    y = 640
    if card.normal:
        normal_line = f"precio normal {card.normal} {card.currency}"
        if card.pct_below:
            normal_line += f"  ·  −{round(card.pct_below)}%"
        d.text((margin, y), normal_line, font=_font("text-bold", 44),
               fill=INK)
        y += 70
    if card.carrier:
        d.text((margin, y), f"con {card.carrier}", font=_font("text", 36),
               fill=INK_SOFT)

    # The signature: price history.
    _sparkline(d, spark_prices, (margin, 790, W - margin, 1150), card.price)

    d.line((margin, 1210, W - margin, 1210), fill=LINE, width=2)
    d.text((margin, 1240), "El precio normal, demostrado — y el chollo, "
           "a tiempo.", font=_font("text", 32), fill=INK)

    if out_path:
        img.save(out_path, "PNG")
    return img
