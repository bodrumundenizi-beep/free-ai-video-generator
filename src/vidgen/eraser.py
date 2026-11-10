"""Erase: LaMa (Apache 2.0, bundled) fills a painted part with what is around it; then, if AI
pictures are downloaded, a light Z-Image touch-up makes the fill look like a real photo.

Tested 2026-11-07..10 on 19 photos: Z-Image alone drew new things in the hole (toilets, a
water bottle); LaMa never does, but leaves a soft smudge, mostly where the thing's soft edge
was left outside the painting and where its 512 px result was stretched. So the painted part
is grown past the edge and LaMa is given a tight square. The touch-up (Deniz's idea): describe
what is around the hole, redraw only the hole at 0.3 with those words, match the grain.
"""

from __future__ import annotations

import os
import threading
import time

from . import imagegen, writer
from .cutout import RELEASE, flatten, open_model, run_model
from .footage import Stopped

FOLDER = "lama"
WEIGHTS = writer.Model("eraser", "lama-fp16w.onnx", RELEASE + "lama-fp16w.onnx", 106291857,
                       "cf36c035f814cc3f224e02fcafdacc97672b564594af387e0e53c6dce4edbdda",
                       "Apache-2.0")
MODEL = WEIGHTS.file
SIDE = 512   # the model works on squares this size
MISSING = "The eraser isn't downloaded yet."
BIG = 0.35   # a painted part this share of the photo or more tends to leave a smudge

_session = None
_lock = threading.Lock()


class EraseError(Exception):
    pass


def model_path() -> str | None:
    """The downloaded model, or vendor/'s when running from source; None before the download."""
    if writer.installed(WEIGHTS):
        return writer.model_path(WEIGHTS)
    return writer.bundled(FOLDER, MODEL)


def installed() -> bool:
    return model_path() is not None


def download(progress=None, should_stop=None, session=None) -> str:
    """Fetch the model once (writer.download: resumes, checks the SHA-256). Stopped, WriterError."""
    return writer.download(progress, should_stop, session, WEIGHTS)


def _load():
    global _session
    with _lock:
        if _session is None:
            path = model_path()
            if path is None:
                raise EraseError(MISSING)
            _session = open_model(path)
        return _session


def release() -> None:
    global _session
    with _lock:
        _session = None


def hole(painted, size):
    """(hard, soft) masks at ``size`` from the painting: grown past the thing's soft edge (hair,
    fur, blur, which otherwise smear into the fill), soft at the rim. None if nothing painted."""
    from PIL import Image, ImageFilter

    painted = painted.convert("L").resize(size, Image.NEAREST)
    box = painted.point(lambda v: 255 if v > 127 else 0).getbbox()
    if not box:
        return None
    grow = max(8, max(box[2] - box[0], box[3] - box[1]) // 25)
    hard = painted.filter(ImageFilter.GaussianBlur(grow)).point(lambda v: 255 if v > 8 else 0)
    return hard, hard.filter(ImageFilter.GaussianBlur(max(2, grow // 4)))


def square(box, size):
    """The square LaMa sees: about 1.4 times the hole, at least SIDE px (so a small hole isn't
    stretched), kept inside the photo."""
    x0, y0, x1, y1 = box
    side = min(max(size), max(SIDE, int(max(x1 - x0, y1 - y0) * 1.4)))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    left = int(min(max(0, cx - side / 2), max(0, size[0] - side)))
    top = int(min(max(0, cy - side / 2), max(0, size[1] - side)))
    return left, top, min(size[0], left + side), min(size[1], top + side)


def share(mask_path: str) -> float:
    """How much of the photo is painted (0-1), for the "very big" warning."""
    from PIL import Image

    with Image.open(mask_path) as painted:
        small = painted.convert("L").resize((64, 64))
    return sum(1 for v in small.getdata() if v > 127) / (64 * 64)


def erase(photo: str, mask_path: str, out_path: str, should_stop=None, session=None):
    """``photo`` with the painted part (white in ``mask_path``) filled from around it, as a PNG
    at ``out_path``, the photo's size; the rest pixel for pixel. (seconds, soft hole mask).
    Raises EraseError and Stopped."""
    import numpy as np
    from PIL import Image, ImageOps

    began = time.monotonic()
    try:
        with Image.open(photo) as opened:
            image = flatten(ImageOps.exif_transpose(opened))
        with Image.open(mask_path) as painted:
            found = hole(painted, image.size)
    except OSError as exc:
        raise EraseError(f"The photo could not be read ({exc}).") from exc
    if found is None:
        raise EraseError("Paint over what to erase first.")
    hard, soft = found
    box = square(hard.getbbox(), image.size)
    crop = image.crop(box)
    x = np.asarray(crop.resize((SIDE, SIDE), Image.LANCZOS), np.float32).transpose(2, 0, 1)[None] / 255
    m = hard.crop(box).resize((SIDE, SIDE), Image.NEAREST)
    m = (np.asarray(m, np.float32) > 127).astype(np.float32)[None, None]
    if should_stop and should_stop():
        raise Stopped()
    out = run_model(session or _load(), {"image": x, "mask": m}, should_stop)[0][0]
    out = out.transpose(1, 2, 0)
    if out.max() <= 1.5:   # this export answers in 0-255; others in 0-1
        out = out * 255
    filled = Image.fromarray(out.clip(0, 255).astype(np.uint8)).resize(crop.size, Image.LANCZOS)
    image.paste(Image.composite(filled, crop, soft.crop(box)), box[:2])
    image.save(out_path, "PNG")
    return time.monotonic() - began, soft


def grain(photo, result, soft):
    """``result`` with the photo's own fine noise added inside the hole, so the spot isn't
    cleaner than everything around it."""
    import numpy as np
    from PIL import Image, ImageFilter

    def fine(x):
        blurred = Image.fromarray(x.clip(0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.5))
        return x - np.asarray(blurred, np.float32)

    a, r = np.asarray(photo, np.float32), np.asarray(result, np.float32)
    s = np.asarray(soft, np.float32) / 255
    ring = (np.asarray(soft.filter(ImageFilter.MaxFilter(31)), np.float32) > 0) & (s == 0)
    inside = s > 0.9
    if ring.sum() < 100 or not inside.any():
        return result
    extra = max(0.0, fine(a)[ring].std() ** 2 - fine(r)[inside].std() ** 2) ** 0.5
    extra = min(extra, 5.0)   # texture around (bricks) isn't camera noise: a blank sign got speckled
    noise = np.random.default_rng(0).normal(0, extra, a.shape[:2])[..., None]
    return Image.fromarray((r + noise * s[..., None]).clip(0, 255).astype(np.uint8))


def touch_up(photo: str, filled: str, soft, out_path: str, describe=None, **kwargs):
    """The erased spot redrawn lightly by Z-Image, from ``describe(path)``'s words for the area
    around it (the photo model), so it looks like a real photo. imagegen.redraw's Made;
    ``kwargs`` go to it (should_stop, log, backend...)."""
    from PIL import Image, ImageOps

    x0, y0, x1, y1 = soft.getbbox()
    pad = max(x1 - x0, y1 - y0)
    around, spot = out_path + ".around.png", out_path + ".hole.png"
    try:
        with Image.open(filled) as image:
            width, height = image.size
            image.crop((max(0, x0 - pad), max(0, y0 - pad), min(width, x1 + pad),
                        min(height, y1 + pad))).save(around)
        words = (describe(around) if describe else "") or "the same place, empty"
        soft.point(lambda v: 255 if v else 0).save(spot)
        made = imagegen.redraw(filled, f"{words}, a natural photo with real detail", out_path,
                               imagegen.TOUCH_UP, mask=spot, **kwargs)
        with Image.open(photo) as original, Image.open(out_path) as drawn:
            original = flatten(ImageOps.exif_transpose(original))
            _hard, spread = imagegen.painted_area(original.size, spot)
            grain(original, drawn.convert("RGB"), spread).save(out_path)
        return made
    finally:
        for leftover in (around, spot):
            try:
                os.remove(leftover)
            except OSError:
                pass
