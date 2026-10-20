"""Drawn videos: each scene's picture redrawn as chalk on a blackboard, or as a flat-colour
drawing, appearing stroke by stroke as if someone were drawing it.

No AI and no graphics card: it is image maths on one still per scene (a stock clip's middle
frame, an AI picture, or the user's own image), so it is quick on any laptop. The still is
turned into lines (and, for Drawing, flat colours) once; every frame of the clip is then a
mask of how much has been drawn so far.
"""

from __future__ import annotations

import numpy as np

NORMAL, BLACKBOARD, DRAWING = "Normal", "Blackboard", "Drawing"
STYLES = (NORMAL, BLACKBOARD, DRAWING)
DRAW_SECONDS = 1.8   # how long the drawing takes; the finished picture then stays
BOARD = np.array([28, 38, 33], dtype=np.float32)       # dark green-black
CHALK = np.array([238, 238, 230], dtype=np.float32)
PAPER = np.array([250, 247, 240], dtype=np.float32)
INK = np.array([30, 30, 34], dtype=np.float32)
LINE_SHARE = 0.16   # the strongest edges that become lines
DRAW_FPS = 15       # while drawing, a new frame this often (each is held): half the work
CELLS = (9, 16)     # the drawing order: blocks of the picture, top-left to bottom-right


def _edges(image) -> np.ndarray:
    """0..1 line strength: the picture's outlines, from a softened grey copy."""
    from PIL import ImageFilter

    grey = np.asarray(image.convert("L").filter(ImageFilter.GaussianBlur(1.6)), np.float32)
    gx = np.abs(np.diff(grey, axis=1, prepend=grey[:, :1]))
    gy = np.abs(np.diff(grey, axis=0, prepend=grey[:1, :]))
    strength = gx + gy
    cut = np.quantile(strength, 1 - LINE_SHARE)
    # Firm lines: anything well above the cut is fully drawn, not a faint grey.
    return np.clip((strength - cut) / max(float(cut), 1.0) * 1.5, 0, 1)


def _thicken(lines: np.ndarray, size: int) -> np.ndarray:
    from PIL import Image, ImageFilter

    image = Image.fromarray((lines * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(size))
    return np.asarray(image.filter(ImageFilter.GaussianBlur(0.8)), np.float32) / 255


def chalk(image) -> tuple[np.ndarray, np.ndarray]:
    """(finished frame, line mask): chalk lines on a blackboard."""
    h, w = image.size[1], image.size[0]
    lines = _thicken(_edges(image), 5 if w < 1500 else 7)
    rng = np.random.default_rng(3)
    dust = rng.normal(0, 4, (h, w, 1)).astype(np.float32)   # the board is never quite even
    grain = 0.85 + 0.15 * rng.random((h, w), dtype=np.float32)   # chalk is patchy
    alpha = (lines * grain)[..., None]
    board = BOARD + dust
    frame = board * (1 - alpha) + CHALK * alpha
    return np.clip(frame, 0, 255).astype(np.uint8), lines


def cartoon(image, colours: int = 8) -> tuple[np.ndarray, np.ndarray]:
    """(finished frame, line mask): a few flat colours with dark outlines.

    Worked out at half size and enlarged: flat colours need no fine detail, and it is four
    times less work (1.2 s a scene at full size, measured)."""
    from PIL import Image, ImageFilter

    full = image.size
    half = image.convert("RGB").resize((full[0] // 2, full[1] // 2), Image.Resampling.BILINEAR)
    flat = half.filter(ImageFilter.ModeFilter(5)).quantize(colours).convert("RGB")
    flat = flat.resize(full, Image.Resampling.NEAREST)
    lines = np.asarray(Image.fromarray((_thicken(_edges(half), 3) * 255).astype(np.uint8))
                       .resize(full, Image.Resampling.BILINEAR), np.float32) / 255
    colour = np.asarray(flat, np.float32) * 0.85 + PAPER * 0.15   # a little paper shows through
    alpha = lines[..., None]
    frame = colour * (1 - alpha) + INK * alpha
    return np.clip(frame, 0, 255).astype(np.uint8), lines


def draw_order(shape) -> np.ndarray:
    """0..1 per pixel: when it gets drawn. Blocks in reading order, each block's strokes
    sweeping across it, so it looks drawn rather than wiped."""
    h, w = shape[:2]
    rows, cols = CELLS if h >= w else CELLS[::-1]
    y = np.linspace(0, rows, h, endpoint=False)[:, None]
    x = np.linspace(0, cols, w, endpoint=False)[None, :]
    cell = np.floor(y) * cols + np.where(np.floor(y) % 2 == 0, np.floor(x), cols - 1 - np.floor(x))
    inside = (x % 1) * 0.7 + (y % 1) * 0.3   # the stroke inside a block
    return ((cell + inside) / (rows * cols)).astype(np.float32)


def frame_at(t: float, finished: np.ndarray, sketch: np.ndarray, order: np.ndarray,
             style: str) -> np.ndarray:
    """The picture ``t`` seconds into the scene: lines appearing, then (Drawing) the colours.
    ``sketch`` is the lines alone on the empty background (see prepare)."""
    t = int(t * DRAW_FPS) / DRAW_FPS
    progress = min(max(t / DRAW_SECONDS, 0.0), 1.0)
    if progress >= 1.0:
        return finished
    drawn = (order <= progress * 1.15)[..., None]   # lines run slightly ahead of the colour
    empty = (BOARD if style == BLACKBOARD else PAPER).astype(np.uint8)[None, None]
    if style == BLACKBOARD:
        return np.where(drawn, finished, empty)
    fill = int(min(max((progress - 0.55) / 0.45, 0.0), 1.0) * 256)   # colours fade in last
    if fill:
        mixed = ((sketch.astype(np.uint16) * (256 - fill) + finished.astype(np.uint16) * fill)
                 >> 8).astype(np.uint8)
    else:
        mixed = sketch
    return np.where(drawn, mixed, empty)


def prepare(image, size, style: str):
    """The still, filled to ``size``, turned into (finished, lines, order) for frame_at."""
    from PIL import Image, ImageOps

    filled = ImageOps.fit(image.convert("RGB"), size, Image.Resampling.LANCZOS)
    finished, lines = chalk(filled) if style == BLACKBOARD else cartoon(filled)
    if style == BLACKBOARD:
        sketch = finished   # chalk is lines only: drawing it is revealing it
    else:
        alpha = lines[..., None]
        sketch = np.clip(PAPER * (1 - alpha) + INK * alpha, 0, 255).astype(np.uint8)
    return finished, sketch, draw_order(finished.shape)


def clip(image, length: float, size, style: str):
    """A MoviePy clip of the scene being drawn, ``length`` seconds, ``size`` pixels."""
    from moviepy import VideoClip

    finished, sketch, order = prepare(image, size, style)
    held = {}   # the last frame made: while drawing, frames repeat at DRAW_FPS

    def make(t):
        step = int(t * DRAW_FPS)
        if step not in held:
            held.clear()
            held[step] = frame_at(t, finished, sketch, order, style)
        return held[step]

    return VideoClip(make, duration=length)
