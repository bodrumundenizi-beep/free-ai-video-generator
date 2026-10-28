"""Moving pictures: an AI picture turned into a short clip, on the graphics card.

Optional, on top of AI images: Wan 2.2 TI2V 5B (Apache 2.0), its video decoder and the
umt5 text encoder, run by the same stable-diffusion.cpp program in its video mode, with
the scene's picture as the first frame. The clip is played forward and then backward
(ping-pong), so it can fill a longer scene without a jump.

Graphics card only: on a processor one clip would take far too long. Every failure raises
imagegen.ImageError, and the renderer keeps the still picture with its slow zoom.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
import random
import time

from . import imagegen, writer
from .footage import Stopped
from .imagegen import ImageError
from .writer import Model

_QUANTSTACK = ("https://huggingface.co/QuantStack/Wan2.2-TI2V-5B-GGUF/resolve/"
               "57437632ddd08bdcbd1508c866aa22e126ed51d2/")
VIDEO_MODEL = Model(
    name="Wan 2.2 TI2V 5B",
    file="wan2.2-ti2v-5b-Q4_K_M.gguf",
    url=_QUANTSTACK + "Wan2.2-TI2V-5B-Q4_K_M.gguf",
    size=3_433_116_000,
    sha256="95b19697b7f98e65b0a543640e9ca7b4dfec32e2a6e3731e8e10708be52655e2",
    licence="Apache 2.0",
)
VIDEO_DECODER = Model(
    name="Wan 2.2 video decoder",
    file="wan2.2-vae.safetensors",
    url=_QUANTSTACK + "VAE/Wan2.2_VAE.safetensors",
    size=1_409_400_960,
    sha256="e40321bd36b9709991dae2530eb4ac303dd168276980d3e9bc4b6e2b75fed156",
    licence="Apache 2.0",
)
TEXT_ENCODER = Model(
    name="umt5-xxl text encoder",
    file="umt5-xxl-encoder-Q4_K_M.gguf",
    url="https://huggingface.co/city96/umt5-xxl-encoder-gguf/resolve/"
        "b535255bee98c2b0a59ea7c0ae2dcd0c6657b3b7/umt5-xxl-encoder-Q4_K_M.gguf",
    size=3_655_145_312,
    sha256="17cf97a5bbbc60a646d6105b832b6f657ce904a8a1ad970e4b59df0c67584a40",
    licence="Apache 2.0",
)
# The tiny decoder (MIT): a few seconds instead of 78 to turn a clip into frames.
FAST_DECODER = Model(
    name="Wan 2.2 tiny decoder",
    file="taew2_2.safetensors",
    url="https://github.com/madebyollin/taehv/raw/011dfc2112197741c540e0bdd5b7b67bcc930771/"
        "safetensors/taew2_2.safetensors",
    size=22_848_048,
    sha256="b84609b2a133d48434bd9636bfcb44bf05168dc436e2d3cecf26256faa1f5325",
    licence="MIT",
)
FILES = (FAST_DECODER, VIDEO_DECODER, VIDEO_MODEL, TEXT_ENCODER)
SIZE = sum(m.size for m in FILES)

# Set from measurements on the RTX 4060 (see README "Moving pictures"); frames are 4n + 1.
SIZES = {"9:16": (480, 832), "16:9": (832, 480)}
FRAMES, FPS, STEPS = 33, 16, 20
# The last frames of a clip turn glitchy (colour blobs; measured 2026-10-17: frames 29 to 32
# of 33, at 20 and 30 steps alike), so they are dropped before the clip is looped.
GOOD_FRAMES = 29
TIMEOUT = 1200.0   # seconds for one clip before the PC is judged too slow
NEGATIVE = "blurry, distorted, deformed, static, still image, text, watermark, low quality"
# Free memory needed, from the 4.4.1 measurements (engine peak: 1.9 GB of RAM; on the card
# 4.3 GB at full size, 3.8 GB small). Integrated graphics take the card's share from the same
# RAM, so both count there; a card with its own memory needs only the RAM part. A margin of
# about 0.8 GB is kept for Windows and the app.
SMALL_SIZES = {"9:16": (384, 672), "16:9": (672, 384)}
SMALL_FRAMES = 25
SHARED_FULL, SHARED_SMALL, DEDICATED_MIN = 7.0e9, 6.5e9, 3.0e9
FIRST, ALL = "First AI scene", "Every AI scene"
CHOICES = ("Off", FIRST, ALL)


def installed() -> bool:
    return imagegen.installed() and all(writer.installed(m) for m in FILES)


def download(progress=None, should_stop=None, session=None) -> None:
    """Fetch the three files (resumable, checked). ``progress(done, total)``."""
    before = 0
    for model in FILES:
        def report(done, _total, base=before):
            if progress is not None:
                progress(base + done, SIZE)
        try:
            writer.download(report, should_stop, session, model)
        except writer.WriterError as exc:
            raise ImageError(str(exc).replace("The Smart writer", "Moving pictures")) from exc
        before += model.size


def remove() -> None:
    for model in FILES:
        writer.remove(model)


def wanted(choice: str, index: int, ai_before: int) -> bool:
    """Whether the AI scene at ``index`` moves: the first one only, every one, or none.
    ``ai_before`` is how many AI scenes come before it."""
    return choice == ALL or (choice == FIRST and ai_before == 0)


FAST = True   # decode with the tiny decoder (see FAST_DECODER)


def command(exe: str, picture: str, prompt: str, size, out_path: str, seed: int,
            frames: int = FRAMES, steps: int = STEPS) -> list[str]:
    width, height = size
    # The full decoder still encodes the first picture; the tiny one decodes the clip.
    decode = ["--tae", writer.model_path(FAST_DECODER)] if FAST else []
    return [exe, "-M", "vid_gen",
            "--diffusion-model", writer.model_path(VIDEO_MODEL),
            "--vae", writer.model_path(VIDEO_DECODER),
            "--t5xxl", writer.model_path(TEXT_ENCODER),
            "-i", picture, "-p", prompt, "-n", NEGATIVE,
            "--cfg-scale", "5.0", "--sampling-method", "euler", "--steps", str(steps),
            "-W", str(width), "-H", str(height), "--video-frames", str(frames),
            "--fps", str(FPS), "--flow-shift", "5.0", "-s", str(seed), *decode,
            # One model at a time, read in when its turn comes (as stills do since 3.9.2): the
            # text encoder runs, its result is kept, and it is released before the video model.
            # "--offload-to-cpu" held both (6.8 GB) and crashed on shared memory (4.4.1,
            # 0xC0000409 on an Intel laptop). Measured on an RTX 4060: peak commit 12.1 -> 5.2
            # GB, RAM in use 8.4 -> 1.9 GB, the same 4.3 GB on the card, identical frames.
            # Decoding a whole clip at once needs 6.6 GB of the card; in pieces from the start.
            "--params-backend", "disk", "--vae-tiling", "--vae-tile-size", "128",
            "--temporal-tiling",
            "--diffusion-fa",
            "-o", out_path]


def fit(aspect: str, free: int | None, shared: bool):
    """(size, frames) the free memory allows, or None for "keep the still picture"."""
    full = SIZES.get(aspect, SIZES["9:16"]), FRAMES
    if free is None:
        return full   # Windows wouldn't say: try, and fall back to the still if it fails
    if not shared:
        return full if free >= DEDICATED_MIN else None
    if free >= SHARED_FULL:
        return full
    if free >= SHARED_SMALL:
        return SMALL_SIZES.get(aspect, SMALL_SIZES["9:16"]), SMALL_FRAMES
    return None


def exit_name(code) -> str:
    """'exit code 3221226505 (0xC0000409: the engine crashed)' for a Windows crash code."""
    if code is None:
        return "no exit code"
    text = f"exit code {code}"
    return text + (f" (0x{code & 0xFFFFFFFF:08X}: the engine crashed)"
                   if code < 0 or code > 255 else "")


@dataclass(frozen=True)
class Moved:
    seconds: float
    frames: int


def make(picture: str, prompt: str, aspect: str, out_path: str, should_stop=None,
         launch=imagegen._launch, log=None, seed: int | None = None, shared: bool = False,
         memory=writer.free_memory) -> Moved:
    """Animate ``picture`` into ``out_path`` (an .avi). ``shared``: the graphics card uses the
    PC's own memory (integrated graphics). Raises ImageError when it can't, for any reason,
    including too little memory; it is then never run again with the same settings."""
    say = log or (lambda _line: None)
    if not installed():
        raise ImageError("Moving pictures are not downloaded. Open Settings > AI pictures.")
    free = memory()
    fitted = fit(aspect, free, shared)
    graphics = "integrated graphics, shared memory" if shared else "graphics card"
    if fitted is None:
        raise ImageError(f"Not enough free memory to make the picture move "
                         f"({imagegen._gigabytes(free)} GB free); the still picture is used.")
    size, frames = fitted
    seed = random.randrange(1, 2**31) if seed is None else seed
    log_path = out_path + ".log"
    say(f"🎞️ Moving picture on the {graphics}: {size[0]}x{size[1]}, {frames} frames, "
        f"{STEPS} steps · memory {imagegen._gigabytes(free)} of "
        f"{imagegen._gigabytes(writer.total_memory())} GB free"
        + (" · smaller, to fit the memory" if frames != FRAMES else ""))
    began = time.monotonic()
    if os.path.isfile(out_path):
        os.remove(out_path)
    first = fit_picture(picture, size, out_path + ".first.png")
    args = command(imagegen.program_path() or imagegen.EXE, first,
                   f"{prompt}, gentle natural motion, the camera moves slowly", size, out_path,
                   seed, frames)
    code = imagegen._run(args, log_path, should_stop, launch, began, TIMEOUT)
    if code == 0 and os.path.isfile(out_path) and os.path.getsize(out_path):
        return Moved(time.monotonic() - began, frames)
    lines = imagegen._output(log_path)
    detail = imagegen._scrub("\n".join(
        [f"Moving picture failed after {time.monotonic() - began:.0f} s: {exit_name(code)}"]
        + lines[-imagegen.DETAIL_LINES:]))
    for line in detail.split("\n"):
        say("   " + line)
    raise ImageError("The picture could not be made to move; the still picture is used.",
                     detail)


def fit_picture(picture: str, size, dest: str) -> str:
    """The picture cropped to the clip's shape and scaled to its size: the first frame."""
    from PIL import Image, ImageOps

    with Image.open(picture) as image:
        ImageOps.fit(image.convert("RGB"), size, Image.Resampling.LANCZOS).save(dest)
    return dest


def ping_pong(clip_path: str, out_path: str, good: int = GOOD_FRAMES) -> str:
    """The clip's good frames forward then backward, so a longer scene can loop it without
    a jump."""
    from . import mastering

    mastering.run_ffmpeg(["-y", "-i", clip_path, "-filter_complex",
                          f"[0:v]trim=end_frame={good},setpts=PTS-STARTPTS,split[a][b];"
                          "[b]reverse[r];[a][r]concat=n=2:v=1[v]", "-map", "[v]",
                          "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", out_path])
    return out_path


__all__ = ["ALL", "CHOICES", "FILES", "FIRST", "ImageError", "Stopped", "download",
           "installed", "make", "ping_pong", "remove", "wanted"]
