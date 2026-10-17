"""A thumbnail for a finished video: its sharpest frame with the title in big letters.

Frames are taken from the middle of the video (the start and end may be the channel and
end cards), the one with the most detail wins, and the title is drawn over its lower part
in the caption font, white with a black outline. The shape follows the video: 1280x720 for
16:9, 720x1280 for 9:16. Pure PIL, apart from reading frames with preview.poster.
"""

from __future__ import annotations

import textwrap

from . import captions, preview

LANDSCAPE, PORTRAIT = (1280, 720), (720, 1280)
FRAMES = 8
SKIP = 2.5  # seconds left out at each end: the start and end cards


def sharpness(image) -> float:
    """How much detail a frame has: the spread of its edges. Blurry or dark frames score low."""
    from PIL import ImageFilter, ImageStat

    return ImageStat.Stat(image.convert("L").filter(ImageFilter.FIND_EDGES)).var[0]


def moments(length: float, count: int = FRAMES) -> list[float]:
    """Where to look: evenly through the video, away from its start and end cards."""
    first, last = (SKIP, length - SKIP) if length > 3 * SKIP else (0.2, max(length - 0.2, 0.2))
    return [first + (last - first) * (i + 0.5) / count for i in range(count)]


def lines(title: str, width: int) -> list[str]:
    """The title in at most three lines that fit ``width`` characters."""
    wrapped = textwrap.wrap(" ".join(title.split()).upper(), width) or [""]
    if len(wrapped) > 3:
        wrapped = wrapped[:3]
        wrapped[-1] = wrapped[-1].rstrip(".,;:") + "..."
    return wrapped


def draw_title(image, title: str):
    """``image`` with ``title`` across its lower third."""
    from PIL import ImageDraw

    w, h = image.size
    portrait = h > w
    text = lines(title, 14 if portrait else 22)
    pixels = int(w * (0.11 if portrait else 0.075))
    font = captions._font(pixels, captions.devanagari(*text))
    spacing = int(pixels * 1.15)
    y = int(h * (0.62 if portrait else 0.58)) - (len(text) - 1) * spacing // 2
    # A dark band behind the title: it reads on any picture, and covers the video's own
    # captions, which sit in this part of the frame.
    from PIL import Image

    top = max(y - spacing, 0)
    bottom = min(max(y + len(text) * spacing, int(h * 0.8)), h)
    band = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(band).rectangle((0, top, w, bottom), fill=(0, 0, 0, 175))
    image.paste(Image.alpha_composite(image.convert("RGBA"), band).convert("RGB"))
    draw = ImageDraw.Draw(image)
    for line in text:
        draw.text((w // 2, y), line, font=font, fill="white", anchor="mm",
                  stroke_width=max(pixels // 9, 3), stroke_fill="black")
        y += spacing
    return image


def make(video: str, title: str, dest: str, pauses=()) -> str:
    """Write the thumbnail for ``video`` to ``dest`` (a .jpg) and return ``dest``.

    ``pauses`` are moments between spoken lines, when no caption is on screen; they are
    preferred, the even spread through the video is the fallback.
    """
    width, height, length = preview.probe(video)
    size = PORTRAIT if height > width else LANDSCAPE
    small = preview.fit((width, height), (size[0] // 2, size[1] // 2))
    times = [t for t in pauses if SKIP < t < length - SKIP][:FRAMES] or moments(length)
    best = max(times, key=lambda t: sharpness(preview.poster(video, small, at=t)))
    frame = preview.poster(video, preview.fit((width, height), size), at=best)
    from PIL import Image

    canvas = Image.new("RGB", size)
    canvas.paste(frame.resize(size) if frame.size != size else frame)
    draw_title(canvas, title).save(dest, quality=90)
    return dest
