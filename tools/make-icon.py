#!/usr/bin/env python3
"""Generate assets/icon.png (128x128): a speech bubble with "Aa" on a rounded tile.

Drawn with Pillow only, so the icon carries no third-party licence. Run
again after changing it.
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf")


def make(size: int = 128) -> Image.Image:
    big = size * 4
    tile = Image.new("RGB", (big, big))
    draw = ImageDraw.Draw(tile)
    for y in range(big):                      # deep blue to teal
        f = y / big
        draw.line([(0, y), (big, y)], fill=(int(16 + 10 * f), int(42 + 70 * f), int(110 + 30 * f)))
    # speech bubble
    bubble = (big * 0.16, big * 0.18, big * 0.84, big * 0.70)
    draw.rounded_rectangle(bubble, radius=big * 0.14, fill=(250, 252, 255))
    draw.polygon([(big * 0.30, big * 0.66), (big * 0.26, big * 0.86), (big * 0.48, big * 0.68)],
                 fill=(250, 252, 255))
    font = None
    for path in FONTS:
        if os.path.exists(path):
            font = ImageFont.truetype(path, int(big * 0.28))
            break
    font = font or ImageFont.load_default()
    text = "Aa"
    box = draw.textbbox((0, 0), text, font=font)
    x = (bubble[0] + bubble[2]) / 2 - (box[2] - box[0]) / 2 - box[0]
    y = (bubble[1] + bubble[3]) / 2 - (box[3] - box[1]) / 2 - box[1]
    draw.text((x, y), text, font=font, fill=(36, 112, 230))
    # sound waves
    for i, radius in enumerate((0.07, 0.12)):
        cx, cy = big * 0.80, big * 0.82
        r = big * radius
        draw.arc((cx - r, cy - r, cx + r, cy + r), 300, 60, fill=(120, 230, 200), width=int(big * 0.025))
    image = tile.resize((size, size), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, big - 1, big - 1), radius=big * 0.22, fill=255)
    image.putalpha(mask.resize((size, size), Image.LANCZOS))
    return image


if __name__ == "__main__":
    path = os.path.join(ROOT, "assets", "icon.png")
    make().save(path)
    print(path)
