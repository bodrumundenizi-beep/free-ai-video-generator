"""Stacking a video's layers into frames: shots, cards and captions, without MoviePy's general path.

MoviePy's CompositeVideoClip turns every layer of every frame into a full-size RGBA picture
and alpha-blends it over the whole frame, even for a caption strip 136 pixels high: about
40 ms of a 90 ms frame in 4.5 (measured with cProfile, a 15 s 1080x1920 video). Our layers
are simple: shots fill the frame and are opaque except while they fade in, and cards and
captions are strips with a mask. So an opaque layer is copied, and a masked one is blended
only where it sits, with the same 8-bit mask MoviePy uses. The pictures come out the same.
"""

from __future__ import annotations

import numpy as np


def _place(size, frame_size, pos):
    """(left, top) of a layer, the way MoviePy computes it."""
    from moviepy.tools import compute_position

    return compute_position(size, frame_size, pos, False)


def _over(part, under, a):
    """Pillow's alpha_composite over an opaque picture, (src*a + dst*(255-a)) / 255, rounded.

    The division is done with shifts (exact for these numbers): numpy's integer division is
    several times slower, and a crossfade blends the whole frame.
    """
    x = part * a + under * (255 - a) + 128          # at most 65280: fits 16 bits
    return ((x + (x >> 8)) >> 8).astype(np.uint8)


def layered(layers, size, duration):
    """One clip showing ``layers`` (MoviePy clips with start, position and maybe a mask)."""
    from moviepy import VideoClip

    width, height = size
    clips = sorted(layers, key=lambda clip: clip.layer_index)   # stable: as given, like MoviePy
    black = np.zeros((height, width, 3), dtype=np.uint8)

    def frame(t):
        out = None
        for clip in clips:
            if not clip.is_playing(t):
                continue
            ct = t - clip.start
            picture = clip.get_frame(ct)
            if picture.dtype != np.uint8:
                picture = picture.astype("uint8")
            ph, pw = picture.shape[:2]
            left, top = (int(v) for v in _place((pw, ph), (width, height), clip.pos(ct)))
            if out is None:
                if clip.mask is None and (left, top, pw, ph) == (0, 0, width, height):
                    out = picture[:, :, :3].copy()   # an opaque shot: nothing under it shows
                    continue
                out = black.copy()
            # The part of the layer that lands on the frame.
            x0, y0 = max(left, 0), max(top, 0)
            x1, y1 = min(left + pw, width), min(top + ph, height)
            if x0 >= x1 or y0 >= y1:
                continue
            part = picture[y0 - top:y1 - top, x0 - left:x1 - left, :3]
            if clip.mask is None:
                out[y0:y1, x0:x1] = part
                continue
            # The mask as MoviePy hands it to Pillow: 0-255, cut down, not rounded.
            alpha = (clip.mask.get_frame(ct) * 255).astype("uint8")
            alpha = alpha[y0 - top:y1 - top, x0 - left:x1 - left]
            if alpha.shape != part.shape[:2]:   # a mask smaller than its picture: the rest shows nothing
                padded = np.zeros(part.shape[:2], dtype=np.uint8)
                padded[:alpha.shape[0], :alpha.shape[1]] = alpha[:part.shape[0], :part.shape[1]]
                alpha = padded
            low, high = int(alpha.min()), int(alpha.max())
            if high == 0:
                continue
            if low == 255:
                out[y0:y1, x0:x1] = part
                continue
            # A fade's mask is one value all over: blend with a number, not a whole array.
            a = np.uint16(low) if low == high else alpha[:, :, None].astype(np.uint16)
            under = out[y0:y1, x0:x1]
            out[y0:y1, x0:x1] = _over(part, under, a)
        return black.copy() if out is None else out

    return VideoClip(frame_function=frame, duration=duration)
