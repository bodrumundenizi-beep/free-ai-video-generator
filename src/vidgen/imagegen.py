"""AI pictures, on this PC, for the scenes stock footage has nothing for.

Optional: nothing here is installed with the app. The "AI images" tab downloads
three files once - the picture model (Z-Image Turbo), its decoder, and
stable-diffusion.cpp, the program that runs them - and the model reads the
prompt with the Smart writer's Qwen file, which the app already has.

A scene asks for a picture with ``Visual: ai: <description>``. make() runs the
program once per picture, as a separate process that is stopped the moment the
user cancels, and the renderer treats the result like any still image.

It wants a graphics card: about 20 seconds a picture on an RTX 4060, minutes
without one. Every ordinary failure raises ImageError with a sentence for the
user, so the renderer can fall back to stock footage and say why.

Pure of Tk. The download and the program can both be replaced in tests.
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import time
import zipfile

from . import writer
from .footage import Stopped
from .writer import Model

__all__ = ["FILES", "LOOKS", "ImageError", "Stopped", "download", "installed", "make",
           "ready", "remove", "size_for"]


class ImageError(Exception):
    """An AI picture could not be made. The message is for the user."""


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
STEPS = "8"  # what this model is distilled for
# Free memory a picture needs: the three models sit in RAM and move to the card in turn.
NEEDED_MEMORY = 7_000_000_000
TIMEOUT = 900.0  # seconds for one picture before the PC is judged too slow
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
    if free is not None and free < NEEDED_MEMORY:
        return False, ("This PC doesn't have enough free memory for an AI picture right now "
                       f"(it needs about {NEEDED_MEMORY / 1e9:.0f} GB). Closing other programs "
                       "may help.")
    return True, ""


# --- making a picture ----------------------------------------------------------------

def size_for(aspect: str) -> tuple[int, int]:
    return SIZES.get(aspect, SIZES["9:16"])


def full_prompt(prompt: str, look: str = DEFAULT_LOOK) -> str:
    """One line for the model: what to show, then the video's look."""
    words = " ".join((prompt or "").split())[:MAX_PROMPT].rstrip(" ,.")
    if not words:
        raise ImageError("The picture has no description.")
    return f"{words}, {LOOKS.get(look, LOOKS[DEFAULT_LOOK])}"


def command(exe: str, prompt: str, size, out_path: str, seed: int) -> list[str]:
    width, height = size
    return [exe, "--diffusion-model", writer.model_path(MODEL),
            "--vae", writer.model_path(DECODER), "--llm", writer.model_path(),
            "-p", prompt, "--cfg-scale", "1.0", "--steps", STEPS,
            "-W", str(width), "-H", str(height), "-s", str(seed),
            # Keeps the models in RAM and lends the card one at a time: what lets 8 GB do it.
            "--offload-to-cpu", "--diffusion-fa", "-o", out_path]


def _launch(args, log_path):
    """Start the program with no window of its own; what it says goes to ``log_path``."""
    with open(log_path, "wb") as log:
        return subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, cwd=os.path.dirname(args[0]),
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _last_line(log_path) -> str:
    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            lines = [line.strip() for line in fh if line.strip()]
    except OSError:
        return ""
    return lines[-1][:200] if lines else ""


def make(prompt: str, size, out_path: str, look: str = DEFAULT_LOOK, seed: int | None = None,
         should_stop=None, launch=_launch, check=ready) -> float:
    """Make one picture at ``out_path`` (a .png). Returns the seconds it took.

    Raises Stopped when ``should_stop`` says so (the program is killed at
    once) and ImageError for anything else.
    """
    ok, why = check()
    if not ok:
        raise ImageError(why)
    text = full_prompt(prompt, look)
    seed = random.randrange(1, 2**31) if seed is None else seed
    log_path = out_path + ".log"
    began = time.monotonic()
    try:
        process = launch(command(program_path() or EXE, text, size, out_path, seed), log_path)
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
    if process.returncode != 0 or not os.path.isfile(out_path) or not os.path.getsize(out_path):
        detail = _last_line(log_path)
        raise ImageError("The AI picture could not be made"
                         + (f" ({detail})." if detail else "."))
    return time.monotonic() - began
