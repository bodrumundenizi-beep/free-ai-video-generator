"""Burned-in captions, timed from the voice engine's word timings.

Most Shorts are watched muted, so the words go on screen: a few at a time, in
heavy capitals with a black outline, the one being spoken picked out in colour.

Three stages, the first two pure so they can be tested without a video:

    group_words   word timings -> cues (the few words shown together)
    cue_states    cues -> one state per spoken word (which word is lit)
    caption_layer states -> a single MoviePy clip to composite on top

The layer is one clip the height of a line of text, not one clip per word:
its frame function looks up the state for the time asked and returns a cached
image, so a 200-word video costs the encoder one extra layer, not 200.

The option tables below are the single source for both the Settings page and
the renderer; a new choice is one more entry here.
"""

from __future__ import annotations

import bisect
import math
import os
from dataclasses import dataclass
from functools import lru_cache

STYLES = ("Highlight", "One word", "Plain")
SIZES = {"Small": 0.8, "Medium": 1.0, "Large": 1.25}
COLORS = {"Yellow": "#FFE14D", "Green": "#5CFF7A", "Cyan": "#5CE1FF", "Pink": "#FF6FB5"}
# Vertical centre of the caption, as a fraction of the frame height. "Lower"
# sits above the buttons and description Shorts, Reels and TikTok draw there.
POSITIONS = {"Lower": 0.72, "Center": 0.5, "Top": 0.16}

DEFAULT_STYLE, DEFAULT_SIZE, DEFAULT_COLOR, DEFAULT_POSITION = (
    "Highlight", "Medium", "Yellow", "Lower")

MAX_WORDS = 3
MAX_CHARS = 18
MAX_GAP = 0.2  # a silence longer than this ends the cue: a breath, a full stop
HOLD = 0.25  # a cue lingers this long after its last word, if nothing follows
FILL = "#FFFFFF"
STROKE = "#000000"
MAX_WIDTH = 0.88  # of the frame; wider text is shrunk to fit
ONE_WORD_SCALE = 1.35
FONT_FILES = ("seguibl.ttf", "arialbd.ttf", "impact.ttf")  # heaviest first


@dataclass(frozen=True)
class CaptionOptions:
    style: str = DEFAULT_STYLE
    size: str = DEFAULT_SIZE
    color: str = DEFAULT_COLOR
    position: str = DEFAULT_POSITION

    @classmethod
    def from_cfg(cls, cfg) -> "CaptionOptions":
        """Options from a settings dict; anything unknown falls back to the default."""
        def pick(key, allowed, default):
            value = cfg.get(key)
            return value if value in allowed else default

        return cls(
            pick("caption_style", STYLES, DEFAULT_STYLE),
            pick("caption_size", SIZES, DEFAULT_SIZE),
            pick("caption_color", COLORS, DEFAULT_COLOR),
            pick("caption_position", POSITIONS, DEFAULT_POSITION),
        )


@dataclass(frozen=True)
class Cue:
    """Words shown together. ``words`` are (start, end, text) on the timeline."""
    start: float
    end: float
    words: tuple

    @property
    def text(self) -> str:
        return " ".join(word for _s, _e, word in self.words)


@dataclass(frozen=True)
class State:
    """What is on screen from ``start`` to ``end``: a cue, and its lit word."""
    start: float
    end: float
    cue: Cue
    active: int


# --- planning ------------------------------------------------------------------

def group_words(words, max_words: int = MAX_WORDS, max_chars: int = MAX_CHARS,
                max_gap: float = MAX_GAP):
    """Cues from (start, duration, text) word timings, in timeline order.

    Words are first split into phrases wherever the speaker pauses, then each
    phrase is divided evenly: four words become two and two, not three and a
    stranded one. A cue never exceeds ``max_words``, nor ``max_chars`` unless a
    single word is longer than that - the drawing shrinks it to fit.
    """
    spans = sorted(
        (start, start + max(duration, 0.0), text.strip())
        for start, duration, text in words if text and text.strip()
    )
    phrases = []
    for span in spans:
        if phrases and span[0] - phrases[-1][-1][1] <= max_gap:
            phrases[-1].append(span)
        else:
            phrases.append([span])

    def chars(group):
        return sum(len(w[2]) for w in group) + len(group) - 1

    groups = []
    for phrase in phrases:
        i = 0
        while i < len(phrase):
            remaining = len(phrase) - i
            share = math.ceil(remaining / math.ceil(remaining / max_words))
            take = 1
            while take < share and chars(phrase[i:i + take + 1]) <= max_chars:
                take += 1
            groups.append(phrase[i:i + take])
            i += take

    cues = []
    for i, group in enumerate(groups):
        end = group[-1][1] + HOLD
        if i + 1 < len(groups):
            end = min(end, groups[i + 1][0][0])
        cues.append(Cue(group[0][0], max(end, group[-1][1]), tuple(group)))
    return cues


def cue_states(cues):
    """One State per word: lit from when it starts until the next word does.

    States inside a cue are contiguous, so the caption never blinks off between
    two words of the same phrase.
    """
    states = []
    for cue in cues:
        for i, (start, _end, _text) in enumerate(cue.words):
            until = cue.words[i + 1][0] if i + 1 < len(cue.words) else cue.end
            states.append(State(start, max(until, start), cue, i))
    return states


def _timestamp(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def to_srt(cues) -> str:
    """The cues as a SubRip file, for uploading as real subtitles."""
    blocks = [
        f"{number}\n{_timestamp(cue.start)} --> {_timestamp(cue.end)}\n{cue.text}\n"
        for number, cue in enumerate(cues, 1)
    ]
    return "\n".join(blocks)


def write_srt(video_path: str, cues) -> str:
    """Save the cues beside ``video_path`` as <name>.srt; returns the path."""
    path = os.path.splitext(video_path)[0] + ".srt"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(to_srt(cues))
    return path


# --- drawing -------------------------------------------------------------------

@lru_cache(maxsize=32)
def _font(pixels: int):
    from PIL import ImageFont

    folder = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for name in FONT_FILES:
        try:
            return ImageFont.truetype(os.path.join(folder, name), pixels)
        except OSError:
            continue
    try:
        return ImageFont.load_default(pixels)
    except TypeError:  # Pillow before 10.1 has no sized default
        return ImageFont.load_default()


def font_pixels(frame_w: int, frame_h: int, options: CaptionOptions) -> int:
    """Caption text height for a frame: larger in portrait, where it is the star."""
    share = 0.075 if frame_h >= frame_w else 0.045
    scale = SIZES[options.size] * (ONE_WORD_SCALE if options.style == "One word" else 1.0)
    return max(12, round(frame_w * share * scale))


def band_height(pixels: int) -> int:
    """Height of the strip every caption image is drawn on, for a font size."""
    return round(pixels * 1.5) + 2 * _stroke(pixels)


def _stroke(pixels: int) -> int:
    return max(2, round(pixels * 0.09))


def render_state(texts, active, frame_w: int, pixels: int, color: str | None):
    """An RGBA image, ``frame_w`` wide, of ``texts`` on one centred line.

    ``active`` is the index of the word to draw in ``color``; pass ``color`` as
    None for no highlight. Text wider than the frame allows is shrunk to fit,
    but the image keeps the height of the full-size font so every caption sits
    on the same line.
    """
    from PIL import Image, ImageDraw

    words = [text.upper() for text in texts]
    height = band_height(pixels)
    limit = frame_w * MAX_WIDTH

    size = pixels
    while True:
        font = _font(size)
        stroke = _stroke(size)
        space = font.getlength(" ")
        widths = [font.getlength(word) for word in words]
        total = sum(widths) + space * (len(words) - 1) + 2 * stroke
        if total <= limit or size <= 12:
            break
        size = max(12, int(size * limit / total) - 1)

    image = Image.new("RGBA", (frame_w, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    x = (frame_w - total) / 2 + stroke
    y = height / 2
    for i, (word, width) in enumerate(zip(words, widths)):
        fill = color if (color and i == active) else FILL
        draw.text((x, y), word, font=font, fill=fill, anchor="lm",
                  stroke_width=stroke, stroke_fill=STROKE)
        x += width + space
    return image


def plan(words, options: CaptionOptions):
    """(cues, states) for timeline ``words`` under ``options``."""
    max_words = 1 if options.style == "One word" else MAX_WORDS
    cues = group_words(words, max_words=max_words)
    return cues, cue_states(cues)


def caption_layer(states, size, options: CaptionOptions, duration: float):
    """One MoviePy clip showing ``states``, positioned for a ``size`` frame.

    Returns None when there is nothing to show.
    """
    if not states:
        return None
    import numpy as np
    from moviepy import VideoClip

    frame_w, frame_h = size
    pixels = font_pixels(frame_w, frame_h, options)
    height = band_height(pixels)
    color = None if options.style == "Plain" else COLORS[options.color]
    starts = [state.start for state in states]
    blank_rgb = np.zeros((height, frame_w, 3), dtype=np.uint8)
    blank_alpha = np.zeros((height, frame_w), dtype=float)

    def index_at(t):
        i = bisect.bisect_right(starts, t) - 1
        return i if i >= 0 and t < states[i].end else None

    # Playback is sequential, so a tiny cache means each state is drawn once.
    @lru_cache(maxsize=4)
    def drawn(i):
        state = states[i]
        image = render_state([w[2] for w in state.cue.words], state.active,
                             frame_w, pixels, color)
        rgba = np.asarray(image)
        return rgba[:, :, :3].copy(), rgba[:, :, 3].astype(float) / 255.0

    def picture(t):
        i = index_at(t)
        return blank_rgb if i is None else drawn(i)[0]

    def alpha(t):
        i = index_at(t)
        return blank_alpha if i is None else drawn(i)[1]

    top = round(frame_h * POSITIONS[options.position] - height / 2)
    top = max(0, min(top, frame_h - height))
    mask = VideoClip(frame_function=alpha, is_mask=True, duration=duration)
    layer = VideoClip(frame_function=picture, duration=duration).with_mask(mask)
    return layer.with_position((0, top))
