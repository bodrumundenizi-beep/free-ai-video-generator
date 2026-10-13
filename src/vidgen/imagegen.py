"""AI pictures, on this PC, for the scenes stock footage has nothing for.

Optional: nothing here is installed with the app. The "AI images" tab downloads
three files once - the picture model (Z-Image Turbo), its decoder, and
stable-diffusion.cpp, the program that runs them - and the model reads the
prompt with the Smart writer's Qwen file, which the app already has.

A scene asks for a picture with ``Visual: ai: <description>``. make() runs the
program once per picture, as a separate process that is stopped the moment the
user cancels, and the renderer treats the result like any still image.

It wants a graphics card: about 20 seconds a picture on an RTX 4060, minutes
without one. If the card cannot make the picture (issue #4: an Intel laptop
chip with 16 GB) it is tried again on the processor, smaller. Every ordinary
failure raises ImageError with a sentence for the user and the program's own
words in ``detail``, so the renderer can fall back to stock footage, say why,
and leave something in the Log to diagnose it by.

Pure of Tk. The download and the program can both be replaced in tests.
"""

from __future__ import annotations

import os
import random
import re
import shutil
import subprocess
import time
import zipfile
from dataclasses import dataclass

from . import writer
from .footage import Stopped
from .writer import Model

__all__ = ["CPU", "FILES", "GPU", "LOOKS", "ImageError", "Made", "Stopped", "download",
           "installed", "make", "ready", "remove", "size_for"]


class ImageError(Exception):
    """An AI picture could not be made. The message is for the user.

    ``detail`` is what the program itself said, scrubbed, for the Log and a bug report.
    """

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail


@dataclass(frozen=True)
class Made:
    """How a picture was made, for the Log and for remembering what works on this PC."""
    seconds: float
    backend: str  # GPU or CPU
    size: tuple[int, int]


# Chosen on 2026-10-10: the best pictures of the open models that fit an 8 GB card,
# free for any use, and it shares the Smart writer's text model.
MODEL = Model(
    name="Z-Image Turbo",
    file="z_image_turbo-Q4_K.gguf",
    url="https://huggingface.co/leejet/Z-Image-Turbo-GGUF/resolve/main/z_image_turbo-Q4_K.gguf",
    size=3_864_250_304,
    sha256="14b375ab4f226bc5378f68f37e899ef3c2242b8541e61e2bc1aff40976086fbd",
    licence="Apache 2.0",
)
DECODER = Model(
    name="Z-Image decoder",
    file="z_image_ae.safetensors",
    url="https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/vae/"
        "ae.safetensors",
    size=335_304_388,
    sha256="afc8e28272cd15db3919bacdb6918ce9c1ed22e96cb12c4d5ed0fba823529e38",
    licence="Apache 2.0",
)
# The Vulkan build: one program for NVIDIA, AMD and Intel cards, and it still runs
# (slowly) with none. To move to a newer one, change all four values together and
# make a picture before releasing.
PROGRAM = Model(
    name="stable-diffusion.cpp",
    file="sd-master-05845fd-bin-win-vulkan-x64.zip",
    url="https://github.com/leejet/stable-diffusion.cpp/releases/download/"
        "master-954-05845fd/sd-master-05845fd-bin-win-vulkan-x64.zip",
    size=30_120_425,
    sha256="9333f82ad437c445b51053dec4ffa548a9c94d8001dc26dd7416de7b98f49de1",
    licence="MIT",
)
FILES = (PROGRAM, DECODER, MODEL)  # smallest first
SIZE = sum(m.size for m in FILES)

EXE = "sd-cli.exe"
GPU, CPU = "gpu", "cpu"  # the graphics card (whichever the program picks), or the processor
STEPS = 8  # what this model is distilled for
CPU_STEPS = 4  # half the wait on a processor, for a picture that is a little rougher
# Free memory needed, measured on 2026-10-13 with the weights kept on disk: the program's
# peak was 3.1 GB at full size on a graphics card and 4.2 GB small on the processor. On a
# laptop chip the "graphics memory" is ordinary RAM too, hence the room above the 3.1.
FULL_MEMORY = 5_000_000_000  # at or above this: the full size
SMALL_MEMORY = 3_500_000_000  # down to this: a smaller picture; below it, none
CPU_MEMORY = 4_500_000_000
TIMEOUT = 900.0  # seconds for one picture before the PC is judged too slow
DETAIL_LINES = 15  # of the program's output kept when it fails
MAX_PROMPT = 400  # characters; the model's text limit is far above what a scene needs
SIZES = {"9:16": (576, 1024), "16:9": (1024, 576)}
# One look for a whole video, so its pictures match each other.
LOOKS = {
    "Realistic photo": "realistic photo, natural light, sharp focus",
    "3D render": "3D render, soft studio lighting, clean and colourful",
    "Illustration": "flat digital illustration, bold colours, clean lines",
}
DEFAULT_LOOK = "Realistic photo"


# --- the files ---------------------------------------------------------------------

def program_dir() -> str:
    """Beside the models, outside the install folder, so an app update keeps it."""
    return os.path.join(os.path.dirname(writer.models_dir()), "sd")


def program_path() -> str | None:
    """The unpacked program, if it is the pinned one."""
    exe, stamp = os.path.join(program_dir(), EXE), os.path.join(program_dir(), ".version")
    try:
        with open(stamp, encoding="utf-8") as fh:
            pinned = fh.read().strip() == PROGRAM.sha256
    except OSError:
        return None
    return exe if pinned and os.path.isfile(exe) else None


def installed() -> bool:
    return program_path() is not None and writer.installed(DECODER) and writer.installed(MODEL)


def _unpack() -> None:
    """Unzip the (already checked) program. Names only: no path in the zip is trusted."""
    target = program_dir()
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target)
    with zipfile.ZipFile(writer.model_path(PROGRAM)) as archive:
        for member in archive.infolist():
            name = os.path.basename(member.filename)
            if member.is_dir() or not name:
                continue
            with archive.open(member) as source, open(os.path.join(target, name), "wb") as out:
                shutil.copyfileobj(source, out)
    if not os.path.isfile(os.path.join(target, EXE)):
        raise ImageError("The AI images download did not contain its program.")
    with open(os.path.join(target, ".version"), "w", encoding="utf-8") as fh:
        fh.write(PROGRAM.sha256)


def needed_files() -> tuple[Model, ...]:
    """What download() will fetch: the three files, and the Smart writer's if it is missing."""
    return FILES + (() if writer.installed() else (writer.MODEL,))


def download(progress=None, should_stop=None, session=None) -> None:
    """Fetch everything AI images need (resumable, checked). ``progress(done, total)``."""
    files = needed_files()
    total, before = sum(m.size for m in files), 0
    for model in files:
        def report(done, _total, base=before):
            if progress is not None:
                progress(base + done, total)
        try:
            writer.download(report, should_stop, session, model)
        except writer.WriterError as exc:
            raise ImageError(str(exc).replace("The Smart writer", "AI images")) from exc
        before += model.size
    if program_path() is None:
        try:
            _unpack()
        except (OSError, zipfile.BadZipFile) as exc:
            raise ImageError(f"AI images could not be unpacked: {exc}") from exc


def remove() -> None:
    """Delete the three files and the unpacked program. The Smart writer's file stays."""
    for model in FILES:
        writer.remove(model)
    shutil.rmtree(program_dir(), ignore_errors=True)


def ready(memory=writer.free_memory) -> tuple[bool, str]:
    """(whether a picture can be made now, and if not, why - in words for the user)."""
    if not installed():
        return False, "AI images are not downloaded yet. Open the AI images tab to get them."
    if not writer.installed():
        return False, ("AI images need the Smart writer's file, which is missing. "
                       "Download it in Settings.")
    free = memory()
    if free is not None and free < SMALL_MEMORY:
        return False, _too_little(SMALL_MEMORY)
    return True, ""


def _too_little(needed: int) -> str:
    return ("This PC doesn't have enough free memory for an AI picture right now "
            f"(it needs about {needed / 1e9:.1f} GB). Closing other programs may help.")


# --- making a picture ----------------------------------------------------------------

def size_for(aspect: str) -> tuple[int, int]:
    return SIZES.get(aspect, SIZES["9:16"])


def full_prompt(prompt: str, look: str = DEFAULT_LOOK) -> str:
    """One line for the model: what to show, then the video's look."""
    words = " ".join((prompt or "").split())[:MAX_PROMPT].rstrip(" ,.")
    if not words:
        raise ImageError("The picture has no description.")
    return f"{words}, {LOOKS.get(look, LOOKS[DEFAULT_LOOK])}"


def smaller(size) -> tuple[int, int]:
    """The reduced size for a PC short of memory, or for the processor: the same shape."""
    width, height = size
    return (384, 672) if height >= width else (672, 384)


def fit(size, backend: str, free: int | None) -> tuple[tuple[int, int], int]:
    """(size, steps) this PC can manage now on ``backend``; ImageError if it cannot."""
    if backend == CPU:
        if free is not None and free < CPU_MEMORY:
            raise ImageError(_too_little(CPU_MEMORY))
        return smaller(size), CPU_STEPS
    if free is None or free >= FULL_MEMORY:
        return tuple(size), STEPS
    if free >= SMALL_MEMORY:
        return smaller(size), STEPS
    raise ImageError(_too_little(SMALL_MEMORY))


def command(exe: str, prompt: str, size, out_path: str, seed: int, backend: str = GPU,
            steps: int = STEPS) -> list[str]:
    width, height = size
    return [exe, "--diffusion-model", writer.model_path(MODEL),
            "--vae", writer.model_path(DECODER), "--llm", writer.model_path(),
            "-p", prompt, "--cfg-scale", "1.0", "--steps", str(steps),
            "-W", str(width), "-H", str(height), "-s", str(seed),
            # Leaves the weights in their files and reads each model in when its turn comes:
            # a third of the memory of holding them all in RAM, at the same speed.
            "--params-backend", "disk", "--diffusion-fa", "-o", out_path,
            # With nothing said the program takes the first graphics card it finds.
            *(["--backend", "cpu"] if backend == CPU else [])]


def _launch(args, log_path):
    """Start the program with no window of its own; what it says goes to ``log_path``."""
    with open(log_path, "wb") as log:
        return subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, cwd=os.path.dirname(args[0]),
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _output(log_path) -> list[str]:
    """What the program printed, one entry per line; progress bars are dropped."""
    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return []
    lines = [line.strip() for line in text.replace("\r", "\n").split("\n")]
    return [line for line in lines if line and not line.startswith("|")]


def _scrub(text: str) -> str:
    """``text`` without this PC's user name: the program prints full paths."""
    home = os.path.expanduser("~")
    name = os.path.basename(home)
    text = text.replace(home, "~")
    return re.sub(re.escape(name), "<user>", text, flags=re.I) if len(name) > 2 else text


def device_name(lines) -> str:
    """The graphics card the program picked, as it named it, or ""."""
    for line in lines:
        found = re.match(r"ggml_vulkan: 0 = (.+?) \|", line)
        if found:
            return found.group(1).strip()
    return ""


def _gigabytes(count) -> str:
    return "?" if count is None else f"{count / 1e9:.1f}"


def _file_sizes() -> str:
    def size(model):
        try:
            return f"{os.path.getsize(writer.model_path(model)) / 1e9:.2f} GB"
        except OSError:
            return "missing"

    return f"picture model {size(MODEL)}, decoder {size(DECODER)}, text model {size(writer.MODEL)}"


def damaged_files(should_stop=None) -> list[str]:
    """Names of model files whose contents no longer match their SHA-256. Slow: reads them all."""
    bad = []
    for model in (DECODER, MODEL, writer.MODEL):
        try:
            if writer._hash_file(writer.model_path(model), should_stop).hexdigest() != model.sha256:
                bad.append(model.name)
                writer.remove(model)
        except OSError:
            bad.append(model.name)
    return bad


def _run(args, log_path, should_stop, launch, began):
    """Run the program to its end. Returns its exit code; Stopped or ImageError otherwise."""
    try:
        process = launch(args, log_path)
    except OSError as exc:
        raise ImageError(f"The AI images program could not start ({exc}).") from exc
    try:
        while process.poll() is None:
            if should_stop is not None and should_stop():
                raise Stopped()
            if time.monotonic() - began > TIMEOUT:
                raise ImageError("The AI picture took too long on this PC.")
            time.sleep(0.2)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    return process.returncode


def make(prompt: str, size, out_path: str, look: str = DEFAULT_LOOK, seed: int | None = None,
         should_stop=None, launch=_launch, check=ready, backend: str = "", log=None,
         memory=writer.free_memory, verify=damaged_files) -> Made:
    """Make one picture at ``out_path`` (a .png) and say how it was made.

    The graphics card is tried first, then the processor: a card can start
    cleanly and still fail part-way (issue #4). ``backend=CPU`` goes straight
    to the processor, for a PC where the card is known not to work. Short of
    memory, the picture is made smaller rather than not at all. ``log`` is
    told what is being tried and, on failure, what the program said.

    Raises Stopped when ``should_stop`` says so (the program is killed at
    once) and ImageError when no attempt produced a picture.
    """
    say = log or (lambda _line: None)
    ok, why = check()
    if not ok:
        raise ImageError(why)
    text = full_prompt(prompt, look)
    seed = random.randrange(1, 2**31) if seed is None else seed
    log_path = out_path + ".log"
    failure, ran = None, False
    for attempt in ([CPU] if backend == CPU else [GPU, CPU]):
        where = "processor" if attempt == CPU else "graphics card"
        free = memory()
        try:
            fitted, steps = fit(size, attempt, free)
        except ImageError as exc:
            failure = failure or exc
            say(f"🎨 Not trying the {where}: {_gigabytes(free)} GB of memory free.")
            continue
        say(f"🎨 AI picture on the {where}: {fitted[0]}x{fitted[1]}, {steps} steps · memory "
            f"{_gigabytes(free)} of {_gigabytes(writer.total_memory())} GB free · {_file_sizes()}")
        began, ran = time.monotonic(), True
        if os.path.isfile(out_path):
            os.remove(out_path)   # a picture left by an earlier try must not pass for this one
        code = _run(command(program_path() or EXE, text, fitted, out_path, seed, attempt, steps),
                    log_path, should_stop, launch, began)
        lines = _output(log_path)
        device = device_name(lines) if attempt == GPU else ""
        if device:
            say(f"🎨 Graphics card used: {device}")
        if code == 0 and os.path.isfile(out_path) and os.path.getsize(out_path):
            return Made(time.monotonic() - began, attempt, fitted)
        detail = _scrub("\n".join(
            [f"AI picture failed on the {where} after {time.monotonic() - began:.0f} s: exit "
             f"code {code}, {_gigabytes(memory())} GB free at that moment"]
            + lines[-DETAIL_LINES:]))
        for line in detail.split("\n"):
            say("   " + line)
        failure = ImageError(f"The AI picture could not be made on the {where}.", detail)
    if ran:
        bad = verify(should_stop)
        if bad:
            raise ImageError(f"An AI images file was damaged and has been removed "
                             f"({', '.join(bad)}). Download it again in the AI images tab.",
                             failure.detail)
    raise failure
