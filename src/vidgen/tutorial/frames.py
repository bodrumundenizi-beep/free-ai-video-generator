"""The recording, cut to the tutorial's clock (5.4, plan step 4).

One picture per frame of the tutorial: frame k shows the recording at to_recording(k / fps), so
pauses vanish and a drag plays at the house pace, while menus open and shapes grow for real.
Each picture keeps only what belongs in the video: the recorded window (rounded corners) and
the program's own menus open at that moment. The rest of the recorded margin is someone else's
screen, so it becomes see-through (the scene's blurred backdrop shows there). The account's
name and picture are made into big blocks. Pictures are WebP with transparency, which the
headless browser and WebView2 both read.
"""
from __future__ import annotations

import os
import subprocess

import numpy as np
from PIL import Image, ImageDraw

from ..mastering import ffmpeg_exe
from . import build as B

FPS = 30
CORNER = 10        # the window's rounded corners, in recording pixels
QUALITY = 82


def _popups_at(log: dict, t: float):
    current = []
    for when, rects in log.get("popups", []):
        if when > t:
            break
        current = rects
    return current


def mask_for(size, window, popups, corner=CORNER) -> Image.Image:
    """The "L" mask: 255 where the window and its menus are, 0 elsewhere."""
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    x, y, w, h = window
    draw.rounded_rectangle((x, y, x + w - 1, y + h - 1), radius=corner, fill=255)
    for px, py, pw, ph in popups:
        draw.rectangle((px, py, px + pw - 1, py + ph - 1), fill=255)
    return mask


def pixelate(picture: Image.Image, rects, block: int = 10) -> Image.Image:
    """Big blocks over each rectangle: unreadable, unlike a light blur (see screen._blur_chain)."""
    for x, y, w, h in rects:
        x, y = max(int(x) - 4, 0), max(int(y) - 4, 0)
        w, h = min(int(w) + 8, picture.width - x), min(int(h) + 8, picture.height - y)
        if w < 2 or h < 2:
            continue
        piece = picture.crop((x, y, x + w, y + h))
        small = piece.resize((max(1, w // block), max(1, h // block)), Image.BILINEAR)
        picture.paste(small.resize((w, h), Image.NEAREST), (x, y))
    return picture


def sources(anchors, length: float, fps: int = FPS, covered=()) -> list[float]:
    """For each tutorial frame, the place in the recording it shows, in recording frames
    (with a fraction: between two frames they are blended), never going backwards. While
    another window ``covered`` the app ([[start, end], ...] seconds), the last clean frame."""
    count = max(1, round(length * fps))
    out = []
    for k in range(count):
        rec_t = B.to_recording(anchors, k / fps)
        for start, end in covered:
            if start <= rec_t <= end:
                rec_t = max(start - 1 / fps, 0.0)
                break
        place = max(rec_t * fps, 0.0)
        out.append(max(place, out[-1]) if out and not covered else place)
    return out


BLEND = 0.15   # closer than this to a recording frame: that frame alone


def cut(recording: str, log: dict, timeline: dict, out_dir: str, should_stop=None,
        progress=None, fps: int = FPS) -> dict:
    """Write frame_00000.webp... into ``out_dir``; returns the timeline's "frames" entry."""
    os.makedirs(out_dir, exist_ok=True)
    width, height = map(int, B._picture_size(log))
    window = log["window"]
    wanted = sources(timeline["anchors"], timeline["length"], fps, log.get("covered", []))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # the recording at a steady fps (28 a second here, made 30 by blending, not by repeating
    # every 15th frame), as raw pictures, read once from start to end
    reader = subprocess.Popen(
        [ffmpeg_exe(), "-v", "error", "-i", recording, "-vf",
         f"minterpolate=fps={fps}:mi_mode=blend", "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE, creationflags=flags)
    frame_bytes = width * height * 3
    # pixelating, masking and WebP-saving run on several cores (19 s -> less, 2026-11-14)
    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(max_workers=max(1, min(4, (os.cpu_count() or 2) - 1)))
    jobs, links = [], []
    before, current, index, last, ended = None, None, -1, None, False
    try:
        for k, place in enumerate(wanted):
            if should_stop is not None and should_stop():
                from ..footage import Stopped
                raise Stopped()
            base, share = int(place), place - int(place)
            if share > 1 - BLEND:
                base, share = base + 1, 0.0
            elif share < BLEND:
                share = 0.0
            need = base + (1 if share else 0)
            while index < need and not ended:
                data = reader.stdout.read(frame_bytes)
                if len(data) < frame_bytes:
                    ended = True   # the recording is shorter: keep showing its last picture
                    break
                before, current, index = current, data, index + 1
            if current is None:
                raise OSError("The recording has no pictures in it.")
            if share and before is not None and index == need and not ended:
                key = (base, round(share, 2))
            else:
                key, share = (min(base, index), 0.0), 0.0
            if key != last:   # a new picture: make it once
                rec_t = key[0] / fps
                picture = Image.frombuffer("RGB", (width, height), current, "raw", "RGB", 0, 1)
                if share:   # between two recording frames: a mix, not a repeat
                    earlier = Image.frombuffer("RGB", (width, height), before, "raw", "RGB", 0, 1)
                    picture = Image.blend(earlier, picture, share)
                jobs.append(pool.submit(_finish, picture, log.get("private", []),
                                        mask_for((width, height), window, _popups_at(log, rec_t)),
                                        os.path.join(out_dir, f"frame_{k:05d}.webp")))
                last, first_of = key, k
            else:   # the same picture again (a hold): point at the file, once it is written
                links.append((os.path.join(out_dir, f"frame_{first_of:05d}.webp"),
                              os.path.join(out_dir, f"frame_{k:05d}.webp")))
            if progress is not None:
                progress(k + 1, len(wanted))
    finally:
        reader.kill()
        reader.wait()
        pool.shutdown(wait=True)
    for job in jobs:
        job.result()   # a failed save raises here
    for source, dest in links:
        _link(source, dest)
    backdrop(os.path.join(out_dir, "frame_00000.webp"), os.path.join(out_dir, "back.jpg"))
    return {"pattern": "frame_%05d.webp", "count": len(wanted), "fps": fps,
            "back": "back.jpg", "window": list(window)}


def backdrop(first: str, out_path: str) -> str:
    """The scene's blurred backdrop, blurred once here: the browser's CSS blur redrew it on
    every frame and, with the window's drop shadow and the blobs, made frames 3 times slower
    (164 ms against 54 ms, 2026-11-14). Same look: blur, brightness .42, saturation 1.1."""
    from PIL import ImageEnhance, ImageFilter

    with Image.open(first) as im:
        picture = im.convert("RGB")
    small = picture.resize((max(1, picture.width // 4), max(1, picture.height // 4)))
    small = small.filter(ImageFilter.GaussianBlur(11))   # 44 px at full size
    small = ImageEnhance.Brightness(ImageEnhance.Color(small).enhance(1.1)).enhance(0.42)
    small.save(out_path, quality=85)
    return out_path


def _finish(picture, private, mask, path):
    picture = pixelate(picture.copy(), private)
    picture.putalpha(mask)
    picture.save(path, quality=QUALITY, method=0)


def _link(source: str, dest: str) -> None:
    try:
        os.link(source, dest)   # no copy on the same drive
    except OSError:
        import shutil
        shutil.copyfile(source, dest)


# ---- drags that draw a shape -------------------------------------------------------------
DRAWN = 40   # how much the middle of a drag's box must change for it to have drawn a shape


def _pixel(recording: str, t: float, x: int, y: int, folder: str):
    from .record import frame_at

    path = frame_at(recording, t, os.path.join(folder, "probe.png"))
    with Image.open(path) as im:
        return im.convert("RGB").getpixel((min(x, im.width - 1), min(y, im.height - 1)))


def smooth_drawing(timeline: dict, recording: str, folder: str) -> dict:
    """Drags that draw a new shape, shown the hand-made way: the recording holds the moment
    before the press, a smooth shape grows with the cursor in the real shape's colour, then
    the real result appears. Apps redraw a shape being drawn only 8 to 19 times a second
    (PowerPoint, 2026-11-14), which looked choppy; the reference's growing shape is smooth.
    A drag that moves something keeps the real video."""
    pairs = timeline["anchors"]
    for drag in timeline.get("drags", []):
        press, release = drag["from"], drag["press_until"]
        end = B.sample(timeline["cursor"], release)
        x, y = (drag["x"] + int(end[0])) // 2, (drag["y"] + int(end[1])) // 2
        rec_press, rec_up = B.to_recording(pairs, press), B.to_recording(pairs, release)
        try:
            before = _pixel(recording, rec_press - 0.15, x, y, folder)
            after = _pixel(recording, rec_up + 0.3, x, y, folder)
        except (OSError, ValueError):
            continue
        if sum(abs(a - b) for a, b in zip(before, after)) < DRAWN:
            continue   # nothing new appeared in the middle: a move, not a drawing
        drag["fill"] = "#%02x%02x%02x" % after
        drag["edge"] = "#%02x%02x%02x" % tuple(round(v * 0.75) for v in after)
        held = rec_press - 0.15
        kept = [p for p in pairs if p[0] < press - 1e-6 or p[0] > release + 0.15]
        kept += [[press, held], [release, held], [round(release + 0.15, 3), rec_up + 0.15]]
        timeline["anchors"] = pairs = sorted(kept)
    return timeline

