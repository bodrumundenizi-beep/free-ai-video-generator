"""A tutorial's look from the recorded app (5.4, plan F): its accent colour, light or dark.

Taken from the recording's first frame, before the user's own content changes anything (the
promo's picker, given later frames of the PowerPoint test, chose the gold rectangle the user
drew). The accent is the commonest strong colour, the app's top band (title bar, tabs: where
apps put their own colour) counting three times: PowerPoint's Share-button orange (#CC6C3C
here, the reference used #E8763A) beat the salmon of a selected slide, and AI Video Studio's
blue Make video button still wins over the browns at its top (2026-11-14).
"""
from __future__ import annotations

import colorsys
from collections import Counter

TOP_BAND = 0.18      # share of the window's height that is its "top": title bar, tabs, ribbon
TOP_WEIGHT = 3
FONT = "Segoe UI"    # Windows apps' own font


def _strong(rgb) -> bool:
    _h, light, sat = colorsys.rgb_to_hls(*(v / 255 for v in rgb))
    return sat > 0.45 and 0.3 < light < 0.75


def _luminance(rgb) -> float:
    def channel(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = map(channel, rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _hex(rgb) -> str:
    return "#%02X%02X%02X" % tuple(int(v) for v in rgb)


def from_frame(picture, window=None) -> dict:
    """The look of the app in ``picture`` (a PIL image of the recording; ``window`` = its
    (x, y, w, h) in it). {} when nothing usable is found: the house style stays."""
    if window:
        x, y, w, h = window
        picture = picture.crop((x, y, x + w, y + h))
    small = picture.convert("RGB").resize((max(1, picture.width // 2), max(1, picture.height // 2)))
    band = int(small.height * TOP_BAND)
    counts, light = Counter(), 0
    pixels = list(small.getdata())
    for i, rgb in enumerate(pixels):
        top = i // small.width < band
        if top and _luminance(rgb) > 0.5:   # light or dark: the app's frame, not the document
            light += 1                       # (PowerPoint's white slide is not its theme)
        if _strong(rgb):
            key = tuple(v // 24 * 24 + 12 for v in rgb)
            counts[key] += TOP_WEIGHT if top else 1
    look = {"font": FONT, "theme": "light" if light > band * small.width / 2 else "dark"}
    if not counts:
        return look
    (accent, n), = counts.most_common(1)
    if n < 30:   # a few stray pixels are not a brand colour
        return look
    look["accent"] = _hex(accent)
    # text on the accent: dark or white, whichever reads better
    lum = _luminance(accent)
    look["on_accent"] = "#1A0D05" if (lum + 0.05) / 0.05 > 1.05 / (lum + 0.05) else "#FFFFFF"
    return look
