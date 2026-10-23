"""Draw the app icon (a track card swiping right) and build the macOS .icns.

    python tools/make_icon.py

Writes assets/icon/icon-1024.png and assets/icon/AppIcon.icns (needs macOS `iconutil`).
Follows Apple's icon grid: an 824 px rounded body on a 1024 px canvas, with a soft shadow.
"""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "icon"

S = 2048  # drawn at 2x, then downscaled for smooth edges
BODY = S * 824 // 1024
OFF = (S - BODY) // 2
RADIUS = int(BODY * 0.225)
TEAL = (0, 214, 180)
BAR_GRAY = (150, 155, 163)


def vertical_gradient(top: tuple, bottom: tuple) -> Image.Image:
    col = Image.new("RGB", (1, S))
    for y in range(S):
        t = y / (S - 1)
        col.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return col.resize((S, S)).convert("RGBA")


def rounded(w: int, h: int, fill: tuple) -> Image.Image:
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle([0, 0, w - 1, h - 1], int(h * 0.12), fill=fill)
    return img


def draw() -> Image.Image:
    art = vertical_gradient((28, 31, 36), (9, 10, 12))
    cw, ch = int(BODY * 0.66), int(BODY * 0.46)

    # The previous track, left behind
    ghost = rounded(cw, ch, (46, 50, 56, 255)).rotate(-4, resample=Image.BICUBIC, expand=True)
    art.alpha_composite(ghost, ((S - ghost.width) // 2 - int(S * 0.03), int(S * 0.30)))

    # The current track: a card with a waveform, played part in teal
    card = rounded(cw, ch, (242, 243, 245, 255))
    d = ImageDraw.Draw(card)
    bars, pad = 26, int(cw * 0.09)
    bw = (cw - 2 * pad) / bars
    for i in range(bars):
        amp = 0.25 + 0.75 * abs(math.sin(i * 0.55) * math.cos(i * 0.21))
        bh = amp * ch * 0.5
        x0 = pad + i * bw
        d.rounded_rectangle(
            [x0, ch / 2 - bh / 2, x0 + bw * 0.58, ch / 2 + bh / 2], int(bw * 0.29), fill=TEAL if i < bars * 0.45 else BAR_GRAY
        )
    card = card.rotate(9, resample=Image.BICUBIC, expand=True)
    pos = ((S - card.width) // 2 + int(S * 0.06), int(S * 0.26))

    # Teal glow around the card
    glow_mask = Image.new("L", (S, S), 0)
    glow_mask.paste(card.getchannel("A"), pos)
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    glow.paste(TEAL + (120,), (0, 0), glow_mask.filter(ImageFilter.GaussianBlur(S * 0.03)))
    art = Image.alpha_composite(art, glow)
    art.alpha_composite(card, pos)

    # Swipe arrow
    d = ImageDraw.Draw(art)
    y, x0, x1 = int(S * 0.735), int(S * 0.36), int(S * 0.62)
    d.line([(x0, y), (x1, y)], fill=TEAL, width=int(S * 0.03))
    d.polygon([(x1 + int(S * 0.055), y), (x1 - int(S * 0.005), y - int(S * 0.048)), (x1 - int(S * 0.005), y + int(S * 0.048))], fill=TEAL)

    # Clip to the rounded body and add the shadow
    body = Image.new("L", (S, S), 0)
    ImageDraw.Draw(body).rounded_rectangle([OFF, OFF, OFF + BODY, OFF + BODY], RADIUS, fill=255)
    art.putalpha(ImageChops.multiply(art.getchannel("A"), body))
    icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    icon.paste((0, 0, 0, 110), (0, int(S * 0.012)), body.filter(ImageFilter.GaussianBlur(S * 0.018)))
    icon = Image.alpha_composite(icon, art)
    return icon.resize((1024, 1024), Image.LANCZOS)


def build_icns(png: Image.Image, target: Path) -> bool:
    if shutil.which("iconutil") is None:
        print("iconutil not found (macOS only); skipped AppIcon.icns")
        return False
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            png.resize((size, size), Image.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
            png.resize((size * 2, size * 2), Image.LANCZOS).save(iconset / f"icon_{size}x{size}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(target)], check=True)
    return True


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    icon = draw()
    icon.save(OUT / "icon-1024.png", optimize=True)
    build_icns(icon, OUT / "AppIcon.icns")
    print("wrote", ", ".join(p.name for p in sorted(OUT.iterdir())))


if __name__ == "__main__":
    main()
