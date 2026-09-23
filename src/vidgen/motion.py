"""Framing and Ken Burns motion.

Every shot is built by ``framed_shot``: the source is looped or trimmed to
length, then each frame gets one PIL resample that does both jobs - the centre
crop to the output aspect ratio and the zoom. The crop box is in float
coordinates, so a slow 1.00 -> 1.06 zoom glides instead of stepping a pixel at
a time. The previous pipeline ran MoviePy's resize and then its crop, which is
two resamples per frame; with the zoom switched off the framing is identical.
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageOps

ZOOM_EXPLICIT = 1.10  # Zoom: in / Zoom: out
ZOOM_STILL = 1.08  # auto, on a photo or screenshot
ZOOM_SLOW = 1.06  # auto, on footage that barely moves
ZOOM_MOVING = 1.03  # auto, on footage that already moves

# Mean absolute change in grey level (0-255) between frames 0.2s apart, at
# thumbnail size. Below this a clip reads as static. A heuristic: locked-off
# shots measure 0-2, slow pans 2-5, handheld or busy footage well above.
SLOW_MOTION = 4.0

_RESAMPLE = Image.Resampling.BILINEAR


def _to_image(frame) -> Image.Image:
    """PIL image from a MoviePy frame. Generated clips (ColorClip, for one)
    produce int64 arrays, which PIL refuses, so clamp them to 8-bit first."""
    if frame.dtype != np.uint8:
        frame = np.clip(frame, 0, 255).astype(np.uint8)
    return Image.fromarray(frame)


def smoothstep(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)


def zoom_range(mode: str, kind: str, motion: float | None, flip: bool):
    """(start, end) scale for one shot.

    ``flip`` alternates the direction of the default zoom from shot to shot,
    which reads as deliberate camera work rather than a filter.
    """
    if mode == "none":
        return (1.0, 1.0)
    if mode == "in":
        return (1.0, ZOOM_EXPLICIT)
    if mode == "out":
        return (ZOOM_EXPLICIT, 1.0)
    if kind == "image":
        peak = ZOOM_STILL
    elif motion is not None and motion < SLOW_MOTION:
        peak = ZOOM_SLOW
    else:
        peak = ZOOM_MOVING
    return (peak, 1.0) if flip else (1.0, peak)


def measure_motion(clip) -> float:
    """How much a video moves on its own, from three sampled frame pairs."""
    duration = clip.duration or 0.0
    if duration < 0.5:
        return 0.0
    gap = 0.2
    diffs = []
    for fraction in (0.25, 0.5, 0.75):
        t = min(duration * fraction, duration - gap - 0.05)
        a = _thumb(clip.get_frame(t))
        b = _thumb(clip.get_frame(t + gap))
        diffs.append(float(np.mean(np.abs(a - b))))
    return sum(diffs) / len(diffs)


def _thumb(frame) -> np.ndarray:
    image = _to_image(frame).convert("L")
    width = 64
    height = max(1, round(image.height * width / image.width))
    return np.asarray(image.resize((width, height), _RESAMPLE), dtype=np.float32)


def load_image(path: str, target_w: int, target_h: int) -> np.ndarray:
    """A still as an RGB array, upright, pre-shrunk so frames aren't resampled from 24 MP.

    Kept at twice the size needed to cover the frame, which leaves headroom
    for the zoom without resampling a phone photo on every single frame.
    """
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    cover = max(target_w / image.width, target_h / image.height)
    if cover < 0.5:
        size = (round(image.width * cover * 2), round(image.height * cover * 2))
        image = image.resize(size, Image.Resampling.LANCZOS)
    return np.asarray(image)


def fit_length(clip, length: float):
    """``clip`` looped or trimmed to exactly ``length`` seconds."""
    from moviepy import concatenate_videoclips

    if clip.duration is None:  # an ImageClip with no duration yet
        return clip.with_duration(length)
    if clip.duration < length:
        loops = math.ceil(length / clip.duration)
        clip = concatenate_videoclips([clip] * loops)
    return clip.subclipped(0, length)


def framed_shot(source, length: float, size, zoom=(1.0, 1.0)):
    """``source`` looped or trimmed to ``length``, filling ``size``, zooming.

    The centre crop and the Ken Burns zoom are one PIL resample per frame.
    """
    from moviepy import VideoClip

    target_w, target_h = size
    fitted = fit_length(source, length)
    source_w, source_h = fitted.size

    # The largest centred box with the output's aspect ratio.
    aspect = target_w / target_h
    if source_w / source_h > aspect:
        box_h = float(source_h)
        box_w = box_h * aspect
    else:
        box_w = float(source_w)
        box_h = box_w / aspect
    centre_x, centre_y = source_w / 2, source_h / 2
    z_start, z_end = zoom
    passthrough = z_start == z_end == 1.0 and (source_w, source_h) == (target_w, target_h)

    def frame_at(t):
        frame = fitted.get_frame(t)
        if passthrough and frame.dtype == np.uint8:
            return frame
        scale = z_start + (z_end - z_start) * smoothstep(t / length if length else 0.0)
        half_w, half_h = box_w / scale / 2, box_h / scale / 2
        crop = (centre_x - half_w, centre_y - half_h, centre_x + half_w, centre_y + half_h)
        return np.asarray(_to_image(frame).resize((target_w, target_h), _RESAMPLE, box=crop))

    return VideoClip(frame_at, duration=length)
