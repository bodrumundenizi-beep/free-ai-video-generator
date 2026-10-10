"""Cards: the one thing a scene is about, drawn over its footage.

Stock footage cannot show "Win + V" or "$120,000". A scene's ``Card:`` line
puts it on screen. What the line says picks the look, so there is nothing to
learn:

    Card: Win + V                       keycaps, one per part
    Card: $120,000 | after 30 years     a big line, with a small one under it
    Card: Paste; Pick a length; Done    a list, the items appearing one by one

The card pops in as its scene starts and fades out just before it ends. It is
a dark panel in the half of the frame the captions are not using.

Drawing is PIL; the clip is built the way captions.caption_layer builds its
own. Pure functions except layer(), which needs MoviePy.
"""

from __future__ import annotations

import re
from functools import lru_cache

from . import captions
from .motion import smoothstep

MAX_WIDTH = 0.86  # of the frame; a wider card is drawn smaller
POP = 0.25  # seconds to grow and fade in
FADE = 0.2  # seconds to fade out
MARGIN = 0.1  # seconds of bare footage before the card and after it
POP_FROM = 0.82  # the size it grows from
PANEL = (10, 10, 14, 185)
KEY_FACE, KEY_EDGE, KEY_TEXT = "#F5F5F7", "#9A9AA2", "#15151A"
MAX_ITEMS = 5
MAX_KEYS = 4


def kind(text: str) -> str:
    """"keys", "list" or "big" for a Card: line."""
    if ";" in text:
        return "list"
    parts = [p.strip() for p in text.split("+")]
    if 2 <= len(parts) <= MAX_KEYS and all(p and len(p) <= 10 for p in parts):
        return "keys"
    return "big"


def parts(text: str) -> list[str]:
    """The pieces of a Card: line: keys, list items, or the big line and its small one."""
    which = kind(text)
    if which == "keys":
        return [p.strip() for p in text.split("+")]
    if which == "list":
        return [p.strip() for p in text.split(";") if p.strip()][:MAX_ITEMS]
    return [p.strip() for p in text.split("|", 1) if p.strip()]


def base_pixels(frame_w: int, frame_h: int) -> int:
    """Card text height: a little larger than a medium caption."""
    return round(captions.font_pixels(frame_w, frame_h, captions.CaptionOptions()) * 1.1)


def _fit(texts, pixels: int, limit: float):
    """The largest font up to ``pixels`` at which the widest of ``texts`` fits ``limit``."""
    size = pixels
    while True:
        font = captions._font(size)
        widest = max(font.getlength(t) for t in texts)
        if widest <= limit or size <= 12:
            return font, size
        size = max(12, int(size * limit / widest) - 1)


def draw(text: str, frame_size, accent: str = "#FFE14D", shown: int | None = None):
    """The card as an RGBA picture, no larger than the frame allows.

    ``shown`` is how many list items are visible yet (None: all). The picture
    is the same size whatever is shown, so a list does not jump as it fills.
    """
    from PIL import Image, ImageDraw

    frame_w, frame_h = frame_size
    pixels = base_pixels(frame_w, frame_h)
    pad = round(pixels * 0.55)
    limit = frame_w * MAX_WIDTH - 2 * pad
    which, pieces = kind(text), parts(text)
    if not pieces:
        raise ValueError("the card is empty")

    if which == "keys":
        gap = round(pixels * 0.3)
        while True:  # shrink until the whole row fits
            font = captions._font(pixels)
            height = round(pixels * 1.7)
            widths = [max(height, round(font.getlength(p) + pixels * 0.9)) for p in pieces]
            plus = round(font.getlength("+"))
            total = sum(widths) + (len(pieces) - 1) * (plus + 2 * gap)
            if total <= limit or pixels <= 12:
                break
            pixels = max(12, int(pixels * limit / total) - 1)
            gap = round(pixels * 0.3)
        depth = max(3, round(pixels * 0.16))  # the key's lower edge, which makes it a key
        inner_w, inner_h = total, height + depth
    elif which == "list":
        # Five items must still leave room in a landscape frame, which is not tall.
        tallest = int((frame_h * 0.45 - 2 * pad) / (1.45 * len(pieces)))
        font, pixels = _fit([f"0  {p}" for p in pieces],
                            max(12, min(round(pixels * 0.82), tallest)), limit)
        line = round(pixels * 1.45)
        number_w = round(font.getlength("0") + pixels * 0.6)
        inner_w = number_w + round(max(font.getlength(p) for p in pieces))
        inner_h = line * len(pieces)
    else:
        font, big = _fit(pieces[:1], round(pixels * 1.7), limit)
        small_font, small = _fit(pieces[1:] or [" "], round(pixels * 0.62), limit)
        stroke = captions._stroke(big)
        inner_w = round(max(font.getlength(pieces[0]) + 2 * stroke,
                            small_font.getlength(pieces[1]) if len(pieces) > 1 else 0))
        inner_h = round(big * 1.15) + (round(small * 1.5) if len(pieces) > 1 else 0)

    width, height_all = inner_w + 2 * pad, inner_h + 2 * pad
    image = Image.new("RGBA", (width, height_all), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    pen.rounded_rectangle((0, 0, width - 1, height_all - 1), radius=round(pad * 0.8), fill=PANEL)

    if which == "keys":
        x = pad
        radius = round(height * 0.22)
        for i, (piece, key_w) in enumerate(zip(pieces, widths)):
            pen.rounded_rectangle((x, pad + depth, x + key_w, pad + depth + height),
                                  radius=radius, fill=KEY_EDGE)
            pen.rounded_rectangle((x, pad, x + key_w, pad + height), radius=radius,
                                  fill=KEY_FACE)
            pen.text((x + key_w / 2, pad + height / 2), piece, font=font, fill=KEY_TEXT,
                     anchor="mm")
            x += key_w
            if i < len(pieces) - 1:
                pen.text((x + gap + plus / 2, pad + height / 2), "+", font=font, fill=accent,
                         anchor="mm", stroke_width=captions._stroke(pixels) // 2,
                         stroke_fill=captions.STROKE)
                x += plus + 2 * gap
    elif which == "list":
        visible = len(pieces) if shown is None else max(0, min(shown, len(pieces)))
        for i, piece in enumerate(pieces[:visible]):
            y = pad + line * i + line / 2
            pen.text((pad, y), str(i + 1), font=font, fill=accent, anchor="lm")
            pen.text((pad + number_w, y), piece, font=font, fill=captions.FILL, anchor="lm")
    else:
        pen.text((width / 2, pad + big * 0.575), pieces[0], font=font, fill=accent, anchor="mm",
                 stroke_width=stroke, stroke_fill=captions.STROKE)
        if len(pieces) > 1:
            pen.text((width / 2, pad + round(big * 1.15) + small * 0.75), pieces[1],
                     font=small_font, fill=captions.FILL, anchor="mm")
    return image


def steps(text: str) -> int:
    """How many pictures the card goes through: one, or one per list item."""
    return len(parts(text)) if kind(text) == "list" else 1


def visible(t: float, length: float) -> float:
    """How solid the card is ``t`` seconds into a scene of ``length`` seconds, 0 to 1."""
    start, end = MARGIN, length - MARGIN
    if t <= start or t >= end:
        return 0.0
    return min(smoothstep((t - start) / POP), smoothstep((end - t) / FADE))


def centre(caption_position: str) -> float:
    """Where the card's middle sits, as a share of the frame height: clear of the captions."""
    return 0.66 if captions.POSITIONS.get(caption_position, 1.0) < 0.4 else 0.3


def layer(text: str, frame_size, start: float, length: float, accent: str = "#FFE14D",
          caption_position: str = captions.DEFAULT_POSITION):
    """A MoviePy clip of the card for a scene that starts at ``start`` and lasts ``length``."""
    import numpy as np
    from moviepy import VideoClip
    from PIL import Image

    count = steps(text)
    full = draw(text, frame_size, accent)
    width, height = full.size
    blank_rgb = np.zeros((height, width, 3), dtype=np.uint8)
    blank_alpha = np.zeros((height, width), dtype=float)
    showing = max(length - 2 * MARGIN, 0.01)

    @lru_cache(maxsize=MAX_ITEMS + 1)
    def picture_for(step):
        return full if count == 1 else draw(text, frame_size, accent, shown=step)

    def frame(t):
        """(rgb, alpha) at ``t``: the right picture, at the right size and strength."""
        strength = visible(t, length)
        if strength <= 0:
            return blank_rgb, blank_alpha
        # A list fills over the first three quarters of the scene, then rests.
        step = min(count, 1 + int((t - MARGIN) / (showing * 0.75 / count))) if count > 1 else 1
        image = picture_for(step)
        grown = smoothstep((t - MARGIN) / POP)
        if grown < 1:
            scale = POP_FROM + (1 - POP_FROM) * grown
            small = image.resize((max(1, round(width * scale)), max(1, round(height * scale))),
                                 Image.LANCZOS)
            image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            image.paste(small, ((width - small.width) // 2, (height - small.height) // 2))
        rgba = np.asarray(image)
        return rgba[:, :, :3], rgba[:, :, 3].astype(float) / 255.0 * strength

    frame_w, frame_h = frame_size
    top = round(frame_h * centre(caption_position) - height / 2)
    mask = VideoClip(frame_function=lambda t: frame(t)[1], is_mask=True, duration=length)
    clip = VideoClip(frame_function=lambda t: frame(t)[0], duration=length).with_mask(mask)
    return clip.with_position(((frame_w - width) // 2, max(0, top))).with_start(start)
