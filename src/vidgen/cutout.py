"""Remove a photo's background with BiRefNet lite (MIT), which ships inside the app.

The model (91 MB, its weights stored 8-bit; see packaging/fetch_birefnet.py) is downloaded
the first time it's needed, from the app's own GitHub release, into the models folder, and
runs on the processor with onnxruntime, which the offline voice already uses: about 7 seconds
a photo. It is loaded on first use and kept until release() (the Image editor calls that when
it closes).
"""

from __future__ import annotations

import threading
import time

from . import writer
from .footage import Stopped

FOLDER = "birefnet"
# The editor's models are files on the 5.2.0 release, checked by SHA-256 when they arrive
# (packaging/fetch_*.py make them; the release workflow uploads them). Kept out of the
# installer: 216 MB with them, about 85 MB without.
RELEASE = ("https://github.com/bodrumundenizi-beep/free-ai-video-generator/releases/download/"
           "v5.2.0/")
WEIGHTS = writer.Model("background remover", "birefnet-lite-int8w.onnx",
                       RELEASE + "birefnet-lite-int8w.onnx", 90708050,
                       "7c46c4de0ae37256f8b4cf25d708c1f163a5f2551a21e1babba839f2867b48af", "MIT")
MODEL = WEIGHTS.file
SIDE = 1024   # the model sees every photo at this size
MEAN, STD = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)   # ImageNet's, which it was trained on
MISSING = "The background remover isn't downloaded yet."

_session = None
_lock = threading.Lock()


class CutoutError(Exception):
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


def prepare(image):
    """``image`` (PIL) as the model's input: 1x3xSIDExSIDE, normalised, float32."""
    import numpy as np
    from PIL import Image

    x = np.asarray(image.convert("RGB").resize((SIDE, SIDE), Image.BILINEAR), np.float32) / 255
    x = (x - np.array(MEAN, np.float32)) / np.array(STD, np.float32)
    return x.transpose(2, 0, 1)[None]


def alpha_from(logits, size):
    """The model's output as a see-through mask (PIL "L", 0-255) at ``size``."""
    import numpy as np
    from PIL import Image

    keep = 1 / (1 + np.exp(-logits[0, 0].astype(np.float32)))
    return Image.fromarray((keep * 255 + 0.5).clip(0, 255).astype(np.uint8)).resize(
        size, Image.BILINEAR)


def _load():
    global _session
    with _lock:
        if _session is None:
            path = model_path()
            if path is None:
                raise CutoutError(MISSING)
            _session = open_model(path)
        return _session


def open_model(path: str):
    """An onnxruntime session for a bundled model (also vidgen.eraser's)."""
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.log_severity_level = 3   # its notes on the fp16 graph are not for users
    options.intra_op_num_threads = fast_cores()
    # With its memory pool on, onnxruntime kept 14 GB after one photo; off, 0.3 GB
    # (a run still needs ~6.5 GB for a few seconds). Basic graph tuning: as fast here.
    options.enable_cpu_mem_arena = False
    options.enable_mem_pattern = False
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
    return ort.InferenceSession(path, options, providers=["CPUExecutionProvider"])


def fast_cores(cores=None) -> int:
    """One thread per fastest core (0: onnxruntime's own choice). On a chip with slow
    "efficiency" cores, every core took 13 s a photo and the 8 fast ones 7.5 s (i7-13700F)."""
    from . import imagegen

    cores = imagegen.cpu_cores() if cores is None else cores
    if not cores:
        return 0
    fastest = max(kind for kind, _mask, _two in cores)
    return sum(1 for kind, _mask, _two in cores if kind == fastest)


def release() -> None:
    """Free the model's memory; it is loaded again when next needed."""
    global _session
    with _lock:
        _session = None


def run_model(session, feeds, should_stop):
    """``session.run`` on ``feeds``; Stopped when ``should_stop`` says so, even mid-way."""
    import onnxruntime as ort

    options, done = ort.RunOptions(), threading.Event()

    def watch():   # the model can't be asked mid-way, but onnxruntime can be told to give up
        while not done.wait(0.1):
            if should_stop():
                options.terminate = True
                return

    if should_stop:
        threading.Thread(target=watch, daemon=True).start()
    try:
        return session.run(None, feeds, options)
    except Exception:
        if should_stop and should_stop():
            raise Stopped() from None
        raise
    finally:
        done.set()


def cut_out(photo: str, out_path: str, should_stop=None, session=None) -> float:
    """``photo`` with its background see-through, as a PNG at ``out_path`` (the photo's size
    and colours, the background in the alpha only). The seconds it took. Raises CutoutError
    and Stopped."""
    from PIL import Image, ImageChops, ImageOps

    began = time.monotonic()
    try:
        with Image.open(photo) as opened:
            image = ImageOps.exif_transpose(opened)
            if image.mode == "P" and "transparency" in image.info:
                image = image.convert("RGBA")
            had = image.getchannel("A") if "A" in image.getbands() else None
            image = flatten(image)
    except OSError as exc:
        raise CutoutError(f"The photo could not be read ({exc}).") from exc
    if should_stop and should_stop():
        raise Stopped()
    session = session or _load()
    entry, x = session.get_inputs()[0], prepare(image)
    if "float16" in entry.type:
        x = x.astype("float16")
    logits = run_model(session, {entry.name: x}, should_stop)[0]
    if should_stop and should_stop():
        raise Stopped()
    alpha = alpha_from(logits, image.size)
    if had is not None:   # already see-through parts stay see-through
        alpha = ImageChops.darker(alpha, had)
    image.putalpha(alpha)
    image.save(out_path, "PNG")
    return time.monotonic() - began


def clear_subject(alpha) -> bool:
    """Whether the cut-out kept a real subject: not almost nothing, and not just a thin strip
    along one edge (a sea screenshot kept only the app's sidebar)."""
    small = alpha.convert("L").resize((100, 100)).point(lambda v: 255 if v > 128 else 0)
    box = small.getbbox()
    # under 0.3% of the photo: a walker far off on a beach is 0.5% and still a subject
    if not box or sum(1 for v in small.getdata() if v) < 30:
        return False
    x0, y0, x1, y1 = box
    side = x0 == 0 or x1 == 100
    flat = y0 == 0 or y1 == 100
    return not ((side and x1 - x0 < 20 and y1 - y0 > 80) or (flat and y1 - y0 < 20 and x1 - x0 > 80))


def flatten(image, colour=(255, 255, 255)):
    """A see-through picture on a plain ``colour``, for places without see-through (a video)."""
    from PIL import Image

    if image.mode == "P" and "transparency" in image.info:
        image = image.convert("RGBA")
    if "A" not in image.getbands():
        return image.convert("RGB")
    image = image.convert("RGBA")
    back = Image.new("RGBA", image.size, (*colour, 255))
    back.alpha_composite(image)
    return back.convert("RGB")


def has_see_through(image) -> bool:
    return "A" in image.getbands() and image.getchannel("A").getextrema()[0] < 255
