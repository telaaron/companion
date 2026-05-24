"""Generate a 1024x1024 placeholder Companion icon.

Solid gradient background with a centered "C" glyph. Designed so it tiles
down to favicon sizes without losing its identity. Aaron can replace with
a real logo by overwriting ``assets/companion-icon.png`` and re-running
``cargo tauri icon`` from ``tauri/src-tauri``.

Run via: ``uv run python scripts/gen_icon.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 1024
OUT = Path(__file__).resolve().parent.parent / "assets" / "companion-icon.png"


def _gradient(
    size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]
) -> Image.Image:
    """Vertical RGB gradient ``top`` -> ``bottom`` of ``size x size`` pixels."""
    base = Image.new("RGB", (size, size), top)
    pixels = base.load()
    if pixels is None:
        raise RuntimeError("PIL refused to give us a pixel access object")
    for y in range(size):
        t = y / (size - 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        for x in range(size):
            pixels[x, y] = (r, g, b)
    return base


def _rounded_square_mask(size: int, radius: int) -> Image.Image:
    """Alpha mask: rounded-square so the icon reads as an app tile."""
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size, size), radius=radius, fill=255)
    return mask


def _find_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Try a few well-known macOS / Linux font paths; fall back to default."""
    candidates = [
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVu-Sans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def main() -> int:
    # Indigo -> deep-violet gradient ties to the dashboard's `--color-accent`
    # of #6366f1 (indigo-500). Slightly desaturated to look adult, not toy.
    bg = _gradient(SIZE, top=(99, 102, 241), bottom=(67, 56, 202))
    mask = _rounded_square_mask(SIZE, radius=int(SIZE * 0.22))
    tile = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    tile.paste(bg, (0, 0), mask)

    # Centered "C" glyph, off-white so it pops without harsh contrast.
    glyph = "C"
    font = _find_font(int(SIZE * 0.72))
    draw = ImageDraw.Draw(tile)
    bbox = draw.textbbox((0, 0), glyph, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    # textbbox returns offsets that bias the glyph; recenter using them.
    pos = (
        (SIZE - text_w) / 2 - bbox[0],
        (SIZE - text_h) / 2 - bbox[1] - SIZE * 0.02,  # tiny optical lift
    )
    draw.text(pos, glyph, fill=(245, 244, 255, 255), font=font)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    tile.save(OUT, format="PNG", optimize=True)
    print(f"Wrote {OUT} ({SIZE}x{SIZE})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
