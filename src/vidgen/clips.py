"""Stock clips, downloaded once and cut to the video's frame once.

Before 4.6 every frame of a 4K clip was decoded at 4K, piped into Python (25 MB a frame)
and resampled there. Now each clip goes through ffmpeg once: cropped to the part the video
shows (the same crop, focus included, that motion.framed_shot would take) and scaled to the
frame size plus room for the slow zoom, so the zoom still only ever shrinks pixels. The
framing on screen is the same; only where the work happens changes.

Both the download and the cut clip are kept in the cache (cache.py), keyed by the clip's link
and the frame size, so a second render, a second version or the same clip in another project
reads small files and downloads nothing.
"""

from __future__ import annotations

import os

from . import cache, footage, motion

PREP_VERSION = 1      # bump when the cut changes, so old cut clips aren't reused
MARGIN = motion.ZOOM_EXPLICIT   # the deepest zoom: the cut keeps that much extra detail
MAX_FPS = 30          # the video is made at 30 fps; more frames are only more decoding
CRF = 12              # near-lossless: the final encode is where quality is decided


def fetch(url: str, store: cache.Cache, dest: str, should_stop=None) -> str:
    """The downloaded clip: from the cache, or downloaded (and kept) now."""
    name = cache.key("clip", url) + ".mp4"
    kept = store.get(name)
    if kept:
        return kept
    footage.download(url, dest, should_stop=should_stop)
    return store.put(name, dest, move=True) or dest


def crop_box(size, frame, focus=None):
    """(left, top, width, height) of the part of a ``size`` clip a ``frame`` video shows.

    The same box framed_shot uses at no zoom: the largest one with the frame's shape,
    moved toward ``focus`` and kept on the picture.
    """
    source_w, source_h = size
    aspect = frame[0] / frame[1]
    if source_w / source_h > aspect:
        box_w, box_h = source_h * aspect, float(source_h)
    else:
        box_w, box_h = float(source_w), source_w / aspect
    centre_x, centre_y = source_w / 2, source_h / 2
    if focus is not None:
        centre_x += (focus[0] * source_w - centre_x) * motion.FOCUS_PULL
        centre_y += (focus[1] * source_h - centre_y) * motion.FOCUS_PULL
    x = min(max(centre_x, box_w / 2), source_w - box_w / 2)
    y = min(max(centre_y, box_h / 2), source_h - box_h / 2)
    return x - box_w / 2, y - box_h / 2, box_w, box_h, (centre_x, centre_y)


def plan_cut(size, frame, focus=None):
    """What ffmpeg should do: ((left, top, w, h) in whole pixels, (out_w, out_h), new focus).

    None when the clip is already exactly the frame: nothing to cut. ``new focus`` is the
    focus to hand framed_shot on the cut clip so its zoom heads the same way as before.
    """
    left, top, box_w, box_h, (cx, cy) = crop_box(size, frame, focus)
    scale = min(1.0, frame[0] * MARGIN / box_w)
    out_w = max(2, int(round(box_w * scale / 2)) * 2)
    out_h = max(2, int(round(box_h * scale / 2)) * 2)
    crop = (int(round(left)), int(round(top)), int(round(box_w)), int(round(box_h)))
    if crop == (0, 0, size[0], size[1]) and (out_w, out_h) == tuple(size):
        return None
    new_focus = None
    if focus is not None:   # the centre framed_shot pulled toward, in the cut clip's pixels
        fx = ((cx - crop[0]) * out_w / crop[2] - out_w / 2) / (motion.FOCUS_PULL * out_w) + 0.5
        fy = ((cy - crop[1]) * out_h / crop[3] - out_h / 2) / (motion.FOCUS_PULL * out_h) + 0.5
        new_focus = (fx, fy)
    return crop, (out_w, out_h), new_focus


def look(path: str, smart: bool):
    """(size, fps, how much it moves, where its detail is) for a downloaded clip."""
    from moviepy import VideoFileClip

    clip = VideoFileClip(path, audio=False)
    try:
        movement = motion.measure_motion(clip)
        focus = None
        if smart:
            length = clip.duration or 0.0
            try:
                focus = motion.busiest_point(
                    [clip.get_frame(length * k) for k in (0.2, 0.5, 0.8)] if length
                    else [clip.get_frame(0)])
            except Exception:  # noqa: BLE001 - the middle, as before
                focus = None
        return tuple(clip.size), float(clip.fps or MAX_FPS), movement, focus
    finally:
        clip.close()


def cut(path: str, dest: str, crop, out_size, fps: float) -> None:
    from . import mastering

    left, top, width, height = crop
    filters = f"crop={width}:{height}:{left}:{top},scale={out_size[0]}:{out_size[1]}:flags=lanczos"
    if fps > MAX_FPS + 1:
        filters += f",fps={MAX_FPS}"
    mastering.run_ffmpeg(["-y", "-i", path, "-an", "-vf", filters, "-c:v", "libx264",
                          "-preset", "ultrafast", "-crf", str(CRF), "-pix_fmt", "yuv420p", dest])


def ready(url: str, frame, smart: bool, store: cache.Cache, work_dir: str, name: str,
          should_stop=None) -> dict:
    """A stock clip ready for the video: {"path", "original", "motion", "focus"}.

    ``path`` is the cut clip (or the download itself when it needed no cut); ``motion`` and
    ``focus`` are what framed_shot should use with it. Everything comes from the cache when
    this clip was used at this size before.
    """
    tag = cache.key("cut", url, frame[0], frame[1], smart, MARGIN, PREP_VERSION)
    info, kept = store.get_json(tag + ".json"), store.get(tag + ".mp4")
    original = fetch(url, store, os.path.join(work_dir, name + "_src.mp4"), should_stop)
    if info and (kept or info.get("uncut")):
        return dict(info, path=original if info.get("uncut") else kept, original=original)

    size, fps, movement, focus = look(original, smart)
    planned = plan_cut(size, frame, focus)
    if planned is None:
        info = {"uncut": True, "motion": movement, "focus": list(focus) if focus else None}
        store.put_json(tag + ".json", info)
        return dict(info, path=original, original=original)
    crop, out_size, new_focus = planned
    out = os.path.join(work_dir, name + "_cut.mp4")
    cut(original, out, crop, out_size, fps)
    info = {"motion": movement, "focus": list(new_focus) if new_focus else None}
    path = store.put(tag + ".mp4", out) or out
    store.put_json(tag + ".json", info)
    return dict(info, path=path, original=original)
