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

import ctypes
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
# Speed settings: the picture's size (portrait; turned for 16:9) and steps. The app enlarges
# the picture to the video's size once, carefully, before the zoom (motion.load_image).
FAST, BALANCED, BEST = "Fast", "Balanced", "Best"
SPEEDS = {FAST: ((384, 672), 4), BALANCED: ((480, 848), 6), BEST: ((576, 1024), 8)}
# Time per picture relative to Fast, measured 2026-10-14 on an RTX 4060 (7.8, 12.5, 24.3 s);
# on the processor Balanced was 2.3 times Fast (288 against 127 s), but it never runs there.
SPEED_COST = {FAST: 1.0, BALANCED: 1.6, BEST: 3.1}
# Free memory needed, measured on 2026-10-13 with the weights kept on disk: the program's
# peak was 3.1 GB at full size on a graphics card and 4.2 GB small on the processor. On a
# laptop chip the "graphics memory" is ordinary RAM too, hence the room above the 3.1.
FULL_MEMORY = 5_000_000_000  # at or above this: the full size
SMALL_MEMORY = 3_500_000_000  # down to this: a smaller picture; below it, none
CPU_MEMORY = 4_500_000_000
TIMEOUT = 900.0  # seconds for one picture before the PC is judged too slow
BENCH_SIZE, BENCH_STEPS = (256, 448), 2   # "Test speed": tiny, but every part of a real run
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
    """The reduced size for a PC short of memory: the same shape."""
    width, height = size
    return (384, 672) if height >= width else (672, 384)


def _shaped(portrait, size) -> tuple[int, int]:
    """``portrait`` (width, height) turned to the shape of ``size``."""
    width, height = portrait
    return (width, height) if size[1] >= size[0] else (height, width)


def fit(size, backend: str, free: int | None, speed: str = BEST) -> tuple[tuple[int, int], int]:
    """(size, steps) this PC can manage now on ``backend``; ImageError if it cannot.

    The speed setting and the free memory each allow a size; the smaller wins. The
    processor never goes above Fast: anything more takes many minutes a picture.
    """
    if backend == CPU:
        if free is not None and free < CPU_MEMORY:
            raise ImageError(_too_little(CPU_MEMORY))
        speed = FAST if SPEEDS.get(speed, SPEEDS[BEST])[0][0] > SPEEDS[FAST][0][0] else speed
    portrait, steps = SPEEDS.get(speed, SPEEDS[BEST])
    wanted = _shaped(portrait, size)
    if backend == CPU or free is None or free >= FULL_MEMORY:
        allowed = tuple(size)
    elif free >= SMALL_MEMORY:
        allowed = smaller(size)
    else:
        raise ImageError(_too_little(SMALL_MEMORY))
    return min(wanted, allowed, key=lambda s: s[0] * s[1]), steps


def cost(speed: str) -> float:
    """How long a picture at ``speed`` takes, relative to Fast (measured, see SPEEDS)."""
    return SPEED_COST.get(speed, 1.0)


def command(exe: str, prompt: str, size, out_path: str, seed: int, backend: str = GPU,
            steps: int = STEPS, threads: int = 0) -> list[str]:
    width, height = size
    return [exe, "--diffusion-model", writer.model_path(MODEL),
            "--vae", writer.model_path(DECODER), "--llm", writer.model_path(),
            "-p", prompt, "--cfg-scale", "1.0", "--steps", str(steps),
            "-W", str(width), "-H", str(height), "-s", str(seed),
            # Leaves the weights in their files and reads each model in when its turn comes:
            # a third of the memory of holding them all in RAM, at the same speed.
            "--params-backend", "disk", "--diffusion-fa", "-o", out_path,
            # With nothing said the program takes the first graphics card it finds.
            *(["--backend", "cpu"] if backend == CPU else []),
            *(["-t", str(threads)] if threads and backend == CPU else [])]


# --- the processor's cores ----------------------------------------------------------
# Laptop chips mix fast "performance" cores with slower "efficiency" ones. A picture is
# made in parallel steps that each wait for their slowest thread, so where the threads
# run matters. Windows says which core is which (its "efficiency class": higher is faster).

def parse_cores(buffer: bytes) -> list[tuple[int, int, bool]]:
    """(efficiency class, affinity mask, has two threads) for each core, from the bytes of
    GetLogicalProcessorInformationEx(RelationProcessorCore). Group 0 only: a PC with more
    than 64 logical processors keeps the program on its first 64."""
    import struct

    cores, at = [], 0
    while at + 48 <= len(buffer):
        relation, size = struct.unpack_from("<II", buffer, at)
        if size <= 0:
            break
        if relation == 0:   # RelationProcessorCore
            flags, efficiency = struct.unpack_from("<BB", buffer, at + 8)
            mask, group = struct.unpack_from("<QH", buffer, at + 32)
            if group == 0:
                cores.append((efficiency, mask, bool(flags & 1)))
        at += size
    return cores


def cpu_cores() -> list[tuple[int, int, bool]]:
    """This PC's cores (see parse_cores), or [] where Windows can't say."""
    try:
        kernel = ctypes.windll.kernel32
        length = ctypes.c_ulong(0)
        kernel.GetLogicalProcessorInformationEx(0, None, ctypes.byref(length))
        buffer = ctypes.create_string_buffer(length.value)
        if not kernel.GetLogicalProcessorInformationEx(0, buffer, ctypes.byref(length)):
            return []
        return parse_cores(buffer.raw[:length.value])
    except (AttributeError, OSError, ValueError):
        return []


def performance_mask(cores) -> int:
    """The logical processors of the fastest kind of core; 0 if all cores are one kind."""
    classes = {efficiency for efficiency, _mask, _smt in cores}
    if len(classes) < 2:
        return 0
    best = max(classes)
    mask = 0
    for efficiency, core_mask, _smt in cores:
        if efficiency == best:
            mask |= core_mask
    return mask


def thread_choices(cores) -> list[tuple[str, int, int]]:
    """(label, threads, affinity) worth timing on this PC: the program's own choice, the
    performance cores alone (on a mixed chip), and every logical processor."""
    logical = sum(2 if smt else 1 for _e, _m, smt in cores) or os.cpu_count() or 4
    choices = [("program's default", 0, 0)]
    fast = performance_mask(cores)
    if fast:
        choices.append(("performance cores only", bin(fast).count("1"), fast))
    choices.append((f"all {logical} threads", logical, 0))
    return choices


def benchmark(choices, folder: str, launch=None, should_stop=None, log=None):
    """Time one tiny picture on the processor for each (label, threads, affinity).

    Returns [(label, threads, affinity, seconds or None)]. Tiny, so the whole test takes a
    few minutes even on a slow laptop; the fastest choice is what "Auto" then uses.
    """
    say = log or (lambda _line: None)
    os.makedirs(folder, exist_ok=True)
    results = []
    for label, threads, affinity in choices:
        out = os.path.join(folder, f"speed_test_{threads}_{affinity}.png")
        args = command(program_path() or EXE, full_prompt("a red apple on a wooden table"),
                       BENCH_SIZE, out, 7, CPU, BENCH_STEPS, threads)
        start = launch or (lambda a, p, m=affinity: _launch(a, p, m))
        began = time.monotonic()
        code = _run(args, out + ".log", should_stop, start, began)
        took = time.monotonic() - began if code == 0 and os.path.isfile(out) else None
        say(f"⏱️ {label}: " + (f"{took:.0f} s" if took is not None else "failed"))
        results.append((label, threads, affinity, took))
    return results


def graphics(run=subprocess.run) -> str:
    """"dedicated", "integrated" or "none": the best graphics the picture program can use.

    It lists its devices and marks a chip that shares the PC's memory with "uma: 1".
    "" when the program could not be asked.
    """
    exe = program_path()
    if exe is None:
        return ""
    try:
        listed = run([exe, "--list-devices"], capture_output=True, timeout=60,
                     cwd=os.path.dirname(exe),
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return ""
    text = (listed.stdout or b"").decode("utf-8", "replace") + \
        (listed.stderr or b"").decode("utf-8", "replace")
    shared = re.findall(r"ggml_vulkan: \d+ = .*?\| uma: (\d)", text)
    if not shared:
        return "none"
    return "dedicated" if "0" in shared else "integrated"


def _launch(args, log_path, affinity: int = 0):
    """Start the program with no window of its own; what it says goes to ``log_path``."""
    with open(log_path, "wb") as log:
        process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, cwd=os.path.dirname(args[0]),
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if affinity:
        # Keep its threads on the chosen cores. If Windows refuses, it simply runs anywhere.
        ctypes.windll.kernel32.SetProcessAffinityMask(int(process._handle),
                                                      ctypes.c_size_t(affinity))
    return process


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
    """``text`` without this PC's user name: the program prints full paths. Whole words only
    (it used to turn "available" into "avai<user>le" for the user "Lab")."""
    from .diagnostics import scrub_user

    return scrub_user(text, os.path.basename(os.path.expanduser("~")))


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


def _run(args, log_path, should_stop, launch, began, timeout: float | None = None):
    """Run the program to its end. Returns its exit code; Stopped or ImageError otherwise."""
    try:
        process = launch(args, log_path)
    except OSError as exc:
        raise ImageError(f"The AI images program could not start ({exc}).") from exc
    try:
        while process.poll() is None:
            if should_stop is not None and should_stop():
                raise Stopped()
            if time.monotonic() - began > (timeout or TIMEOUT):
                raise ImageError("The AI picture took too long on this PC.")
            time.sleep(0.2)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    return process.returncode


def make(prompt: str, size, out_path: str, look: str = DEFAULT_LOOK, seed: int | None = None,
         should_stop=None, launch=_launch, check=ready, backend: str = "", log=None,
         memory=writer.free_memory, verify=damaged_files, speed: str = BEST,
         threads: tuple[int, int] = (0, 0)) -> Made:
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
    count, affinity = threads
    if affinity and launch is _launch:
        launch = lambda args, path: _launch(args, path, affinity)  # noqa: E731
    ok, why = check()
    if not ok:
        raise ImageError(why)
    text = full_prompt(prompt, look)
    seed = random.randrange(1, 2**31) if seed is None else seed
    log_path = out_path + ".log"
    failure, ran = None, False
    # GPU: the graphics card only (the user chose it); CPU: the processor only; "": the
    # card first, then the processor if the card fails.
    for attempt in {CPU: [CPU], GPU: [GPU]}.get(backend, [GPU, CPU]):
        where = "processor" if attempt == CPU else "graphics card"
        free = memory()
        try:
            fitted, steps = fit(size, attempt, free, speed)
        except ImageError as exc:
            failure = failure or exc
            say(f"🎨 Not trying the {where}: {_gigabytes(free)} GB of memory free.")
            continue
        say(f"🎨 AI picture on the {where}: {fitted[0]}x{fitted[1]}, {steps} steps · memory "
            f"{_gigabytes(free)} of {_gigabytes(writer.total_memory())} GB free · {_file_sizes()}"
            + (f" · {count or 'all'} threads{' on the performance cores' if affinity else ''}"
               if attempt == CPU else ""))
        began, ran = time.monotonic(), True
        if os.path.isfile(out_path):
            os.remove(out_path)   # a picture left by an earlier try must not pass for this one
        code = _run(command(program_path() or EXE, text, fitted, out_path, seed, attempt, steps,
                            count), log_path, should_stop, launch, began)
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


def auto_threads(cores) -> tuple[int, int]:
    """(threads, affinity) before "Test speed" has run: the program's own choice."""
    return 0, 0
