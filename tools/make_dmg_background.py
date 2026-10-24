"""Draw the background of the installer window (the DMG) at 1x and 2x.

    python tools/make_dmg_background.py

Writes assets/dmg/background.png and background@2x.png (660 × 400 points). The app icon sits on the left,
the Applications folder on the right (positions in packaging/dmg_settings.py); an arrow points from one
to the other. Light background, so Finder's icon labels stay readable.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "dmg"
W, H = 660, 400
APP_X, APPS_X, ICON_Y = 170, 490, 205  # icon centres, shared with packaging/dmg_settings.py
INK = (14, 15, 17)
MUTED = (110, 115, 125)
TEAL = (0, 214, 180)


def font(size: int, bold: bool = False):
    for path in ("/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        try:
            f = ImageFont.truetype(path, size)
            if bold and path.endswith("SFNS.ttf"):
                f.set_variation_by_name("Bold")
            return f
        except (OSError, ValueError):
            continue
    return ImageFont.load_default()


def draw(scale: int) -> Image.Image:
    s = scale
    img = Image.new("RGB", (W * s, H * s), (244, 245, 247))
    d = ImageDraw.Draw(img)
    # Soft vertical fade
    for y in range(H * s):
        t = y / (H * s)
        c = tuple(int(a + (b - a) * t) for a, b in zip((250, 250, 251), (232, 234, 238)))
        d.line([(0, y), (W * s, y)], fill=c)

    # Logo (white artwork, drawn in ink on the light background)
    logo = Image.open(ROOT / "bakkis_sorter" / "web" / "img" / "logo-boxed.png").convert("RGBA")
    lw = 150 * s
    logo = logo.resize((lw, int(logo.height * lw / logo.width)), Image.LANCZOS)
    tinted = Image.new("RGBA", logo.size, INK + (0,))
    tinted.putalpha(logo.getchannel("A"))
    img.paste(tinted, ((W * s - lw) // 2, 34 * s), tinted)

    title = font(15 * s, bold=True)
    sub = font(12 * s)
    # Everything sits above the icons: Finder may show its own bar along the bottom edge.
    for text, f, y, col in (("MUSIC SORTER", title, 82, INK), ("Drag the app to Applications to install", sub, 108, MUTED)):
        tw = d.textlength(text, font=f)
        d.text(((W * s - tw) / 2, y * s), text, font=f, fill=col)

    # Arrow between the two icons
    y = ICON_Y * s
    x0, x1 = (APP_X + 70) * s, (APPS_X - 78) * s
    d.line([(x0, y), (x1, y)], fill=TEAL, width=4 * s)
    d.polygon([(x1 + 14 * s, y), (x1 - 2 * s, y - 10 * s), (x1 - 2 * s, y + 10 * s)], fill=TEAL)
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    draw(1).save(OUT / "background.png", optimize=True)
    draw(2).save(OUT / "background@2x.png", optimize=True)
    print("wrote", ", ".join(p.name for p in sorted(OUT.iterdir())))


if __name__ == "__main__":
    main()
