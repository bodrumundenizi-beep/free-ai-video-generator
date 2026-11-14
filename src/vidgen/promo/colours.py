"""The promo video's colours, taken from the product itself: its screenshots, or its CSS.

``background`` is the product's own backdrop, ``accent`` its brand colour, ``text`` whatever
reads on the background. The accent is nudged until it stands out from the background.
"""
from __future__ import annotations

import colorsys
import re
from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    background: str
    accent: str
    text: str
    muted: str


FALLBACK = Palette("#0B0D12", "#5BC8FF", "#FFFFFF", "#A9B4C8")


def _hex(rgb) -> str:
    return "#%02X%02X%02X" % tuple(max(0, min(255, round(v))) for v in rgb[:3])


def _rgb(text: str):
    text = text.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def luminance(rgb) -> float:
    def channel(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(v) for v in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _make(background, accent) -> Palette:
    dark = luminance(background) < 0.4
    text = (255, 255, 255) if dark else (17, 20, 28)
    muted = tuple(t * 0.7 + b * 0.3 for t, b in zip(text, background))
    # A brand colour that disappears on its own background is no use: lighten or darken it.
    h, l, s = colorsys.rgb_to_hls(*(v / 255 for v in accent))
    for _ in range(12):
        if contrast(accent, background) >= 3.0:
            break
        l = min(0.85, l + 0.06) if dark else max(0.2, l - 0.06)
        accent = tuple(v * 255 for v in colorsys.hls_to_rgb(h, l, max(s, 0.55)))
    return Palette(_hex(background), _hex(accent), _hex(text), _hex(muted))


def from_screenshots(paths) -> Palette:
    """The backdrop is the commonest colour along the screenshots' edges; the accent is the
    most colourful colour that covers a real share of them."""
    from PIL import Image

    edges, colours = Counter(), Counter()
    for path in paths:
        try:
            with Image.open(path) as image:
                small = image.convert("RGB").resize((96, 96))
        except OSError:
            continue
        pixels = list(small.getdata())
        for i, rgb in enumerate(pixels):
            x, y = i % 96, i // 96
            key = tuple(v // 16 * 16 + 8 for v in rgb)
            colours[key] += 1
            if x < 4 or y < 4 or x > 91 or y > 91:
                edges[key] += 1
    if not colours:
        return FALLBACK
    background = edges.most_common(1)[0][0] if edges else colours.most_common(1)[0][0]
    total = sum(colours.values())

    def vivid(rgb):
        _h, l, s = colorsys.rgb_to_hls(*(v / 255 for v in rgb))
        return s * (1 - abs(l - 0.5) * 1.6)

    candidates = [(vivid(c), c) for c, n in colours.items()
                  if n / total >= 0.004 and vivid(c) > 0.25 and contrast(c, background) > 1.6]
    accent = max(candidates)[1] if candidates else _rgb(FALLBACK.accent)
    return _make(background, accent)


_VAR = re.compile(r"--([\w-]+)\s*:\s*(#[0-9a-fA-F]{3,6})\b")
_ANY = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")


def from_css(css: str) -> Palette | None:
    """From custom properties (--bg, --accent...) or, without them, the commonest colours."""
    named = {k.lower(): v for k, v in _VAR.findall(css)}

    def pick(*words):
        for key, value in named.items():
            if any(w in key for w in words):
                return _rgb(value)
        return None

    background = pick("bg", "background", "surface", "base")
    accent = pick("accent", "primary", "brand", "highlight")
    if background is None or accent is None:
        seen = [_rgb(c) for c in _ANY.findall(css)]
        if not seen:
            return None
        common = [c for c, _n in Counter(seen).most_common()]
        background = background or min(common[:3], key=lambda c: abs(luminance(c) - 0.02))
        rest = [c for c in common if c != background]
        if accent is None:
            if not rest:
                return None
            accent = max(rest[:8], key=lambda c: colorsys.rgb_to_hls(*(v / 255 for v in c))[2])
    return _make(background, accent)


def backdrop_veil(path: str, palette: Palette, need: float = 4.5) -> float:
    """How much of the background colour to lay over a blurred screenshot (0 to 0.92) so the
    text still reads (contrast ``need`` against the backdrop's bright parts). The blur is
    taken into account: a 32 px picture's 90th-percentile brightness is roughly what remains."""
    from PIL import Image, ImageFilter

    try:
        with Image.open(path) as image:
            small = image.convert("RGB").resize((32, 32)).filter(ImageFilter.GaussianBlur(2))
    except OSError:
        return 0.92
    lums = sorted(luminance(px) for px in small.getdata())
    bright = lums[int(len(lums) * 0.9)]
    dark_bright = lums[int(len(lums) * 0.1)]
    text, bg = _rgb(palette.text), _rgb(palette.background)
    text_l, bg_l = luminance(text), luminance(bg)
    worst = bright if text_l > bg_l else dark_bright   # light text fears bright spots
    for veil in [v / 100 for v in range(45, 93, 3)]:
        mixed = veil * bg_l + (1 - veil) * worst
        lighter, darker = max(text_l, mixed), min(text_l, mixed)
        if (lighter + 0.05) / (darker + 0.05) >= need:
            return veil
    return 0.92
