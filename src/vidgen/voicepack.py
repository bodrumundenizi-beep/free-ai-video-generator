"""The voice pack for "My voice": KittenTTS 2 with PyTorch, installed on demand, graphics card only.

KittenTTS 2 clones a voice from 5-30 s of speech. It runs on PyTorch, which for NVIDIA cards is
about 3 GB: too big to ship inside the app (and over GitHub's 2 GB per-file limit), so the pack
is installed on the user's PC when they ask for it, into its own folder:

    1. Python 3.12 (python.org's embeddable zip, checked by SHA-256)
    2. pip, then the exact packages in voicepack_lock (each pinned and checked by SHA-256)
    3. the two models, at fixed revisions (KittenTTS 2, and chatterbox-turbo's decoder)
    4. speak.py, the small program the app talks to

Every step can be stopped and started again; a finished step is not repeated. Once installed,
the pack runs offline. Measured 2026-10-23 on an RTX 4060: about real time (20.8 s of speech in
20.7 s), 4.9 GB of graphics memory, 26 s to load. On the processor it is 10x slower than real
time, so the pack is only offered with an NVIDIA card with 6 GB or more.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

from . import writer
from .writer import Model

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

PYTHON = Model(
    name="Python 3.12.10 (embeddable)",
    file="python-3.12.10-embed-amd64.zip",
    url="https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip",
    size=11_133_606,
    sha256="4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3",
    licence="PSF",
)
PIP = Model(
    name="pip 25.2",
    file="pip-25.2-py3-none-any.whl",
    url="https://files.pythonhosted.org/packages/b7/3f/945ef7ab14dc4f9d7f40288d2df998d1837ee08"
        "88ec3659c813487572faa/pip-25.2-py3-none-any.whl",
    size=1_752_557,
    sha256="6d67a2b4e7f14d8b31b8b52648866fa717f45a1eb70e83002f4331d07e953717",
    licence="MIT",
)
MODEL_REPO, MODEL_REVISION = "KittenML/kitten-tts-2", "1f00fee525c7b2e30bbe9ca9ba67529734fce9c9"
DECODER_REPO, DECODER_REVISION = ("ResembleAI/chatterbox-turbo",
                                  "749d1c1a46eb10492095d68fbcf55691ccf137cd")
DECODER_FILE = "s3gen_meanflow.safetensors"
SIZE = 7_200_000_000   # measured installed 2026-10-24: PyTorch and libraries ~5 GB, models ~2 GB
MIN_GRAPHICS_MB = 6000  # 4.9 GB measured at peak, plus room for Windows and the screen
PACK_VERSION = "1"     # bump when the pack's contents change: an old pack is installed again


class PackError(Exception):
    """The pack can't be installed or used; the message is for the user."""


Stopped = writer.Stopped


# --- where it lives ----------------------------------------------------------------------

def folder() -> str:
    """A short path: pip failed on a long one (Windows' 260-character limit, 2026-10-23)."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "AIVideoStudio", "voicepack")


def python_exe() -> str:
    return os.path.join(folder(), "python", "python.exe")


def models_dir() -> str:
    return os.path.join(folder(), "models")


def _marker() -> str:
    return os.path.join(folder(), "installed.json")


def installed() -> bool:
    """Whether every step finished (the marker is written last) for this pack version."""
    try:
        with open(_marker(), encoding="utf-8") as fh:
            return json.load(fh).get("version") == PACK_VERSION and os.path.isfile(python_exe())
    except (OSError, ValueError):
        return False


def remove() -> None:
    """Delete the whole pack."""
    shutil.rmtree(folder(), ignore_errors=True)
    for model in (PYTHON, PIP):
        writer.remove(model)


# --- can this PC use it ------------------------------------------------------------------

def graphics(run=subprocess.run) -> tuple[str, int] | None:
    """(name, memory in MB) of the NVIDIA card, or None when there is none."""
    try:
        result = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                     capture_output=True, text=True, creationflags=NO_WINDOW, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    best = None
    for line in result.stdout.splitlines():
        name, _, memory = line.rpartition(",")
        try:
            card = (name.strip(), int(float(memory)))
        except ValueError:
            continue
        if best is None or card[1] > best[1]:
            best = card
    return best


def usable(card) -> str:
    """"" when the pack can run on ``card`` (from graphics()), else why not, for the user."""
    if card is None:
        return ("My voice needs an NVIDIA graphics card. On the processor it is about 10 times "
                "slower than the video, so it is not offered on this PC.")
    name, memory = card
    if memory < MIN_GRAPHICS_MB:
        return (f"My voice needs a graphics card with {MIN_GRAPHICS_MB // 1000} GB of memory or "
                f"more; this PC's {name} has {memory / 1000:.0f} GB.")
    return ""


# --- installing ----------------------------------------------------------------------------

def _run(args, should_stop, on_line=None, cwd=None):
    """Run a step, reading its output line by line; stop it when asked. Raises PackError."""
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
                               cwd=cwd, env=_env())
    tail = []
    try:
        for line in process.stdout:
            if should_stop is not None and should_stop():
                process.kill()
                raise Stopped()
            line = line.rstrip()
            tail = (tail + [line])[-6:]
            if on_line is not None:
                on_line(line)
    finally:
        process.wait()
    if process.returncode != 0:
        raise PackError("The voice pack could not be installed: " + " | ".join(tail[-3:]))


def _env() -> dict:
    env = dict(os.environ)
    env.update(HF_HOME=models_dir(), PYTHONNOUSERSITE="1", PIP_DISABLE_PIP_VERSION_CHECK="1",
               HF_HUB_DISABLE_TELEMETRY="1")
    env.pop("PYTHONPATH", None)
    return env


def _unpack_python(zip_path: str) -> None:
    """The embeddable Python, set up to use its own site-packages (it ignores them by default)."""
    target = os.path.dirname(python_exe())
    shutil.rmtree(target, ignore_errors=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(target)
    pth = next(f for f in os.listdir(target) if f.endswith("._pth"))
    with open(os.path.join(target, pth), "a", encoding="utf-8") as fh:
        fh.write("\nLib\\site-packages\nimport site\n")


def _packages_done() -> bool:
    return os.path.isfile(os.path.join(folder(), "packages.ok"))


def _models_done() -> bool:
    return os.path.isfile(os.path.join(folder(), "models.ok"))


FETCH_MODELS = r"""
import json, os, pathlib
from huggingface_hub import hf_hub_download, snapshot_download
from kittenml.get_model import _kitten2_files
repo, rev, drepo, drev, dfile, cache = json.loads(os.environ["VOICEPACK_MODELS"])
config = json.load(open(hf_hub_download(repo, "config.json", revision=rev, cache_dir=cache)))
local = snapshot_download(repo, revision=rev, cache_dir=cache, allow_patterns=_kitten2_files(config))
hf_hub_download(drepo, dfile, revision=drev, cache_dir=cache)
# Offline later, the libraries look the repositories up by "main": point it at the pinned ones.
for name, sha in ((repo, rev), (drepo, drev)):
    refs = pathlib.Path(cache, "models--" + name.replace("/", "--"), "refs")
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "main").write_text(sha)
print("MODEL_DIR=" + local, flush=True)
"""


def bootstrap(should_stop=None, session=None, report=lambda text, fraction: None) -> None:
    """The pack's own Python with pip in it (also used to make voicepack_lock)."""
    os.makedirs(folder(), exist_ok=True)
    if not os.path.isfile(python_exe()):
        report("Downloading Python...", 0.0)
        _unpack_python(writer.download(should_stop=should_stop, session=session, model=PYTHON))
    site = os.path.join(os.path.dirname(python_exe()), "Lib", "site-packages")
    if not os.path.isdir(os.path.join(site, "pip")):
        report("Downloading pip...", 0.02)
        wheel = writer.download(should_stop=should_stop, session=session, model=PIP)
        # A wheel is a zip of the package: unpacked, pip is installed. (Running pip from its
        # wheel to install itself is refused on Windows.) Always used as "python -m pip".
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(site)


def install(progress=None, should_stop=None, session=None) -> None:
    """Install the pack, carrying on where an earlier try stopped.

    ``progress(text, fraction)`` is called with what is happening and how far (0-1) overall.
    Raises Stopped when ``should_stop`` says so and PackError for anything else.
    """
    from . import voicepack_lock

    report = progress or (lambda text, fraction: None)
    os.makedirs(folder(), exist_ok=True)
    try:
        bootstrap(should_stop, session, report)
        if not _packages_done():
            lock = os.path.join(folder(), "requirements.txt")
            first = os.path.join(folder(), "build-tools.txt")
            lines = voicepack_lock.LOCK.splitlines()
            tools = [line for line in lines if line.split(" @ ")[0] in ("setuptools", "wheel")]
            with open(first, "w", encoding="utf-8") as fh:
                fh.write("\n".join(tools) + "\n")
            with open(lock, "w", encoding="utf-8") as fh:
                fh.write("\n".join(line for line in lines if line not in tools) + "\n")
            # The source-only package (antlr4) is built with these, without pip's separate
            # build environment, which the embeddable Python can't make.
            _run([python_exe(), "-m", "pip", "install", "--no-deps", "--require-hashes",
                  "--no-cache-dir", "--quiet", "-r", first], should_stop)
            total = max(sum(1 for line in voicepack_lock.LOCK.splitlines() if " @ " in line), 1)
            seen = [0]

            def on_line(line):
                found = re.match(r"\s*(Collecting|Downloading)\s+(\S+)", line)
                if found and found.group(1) == "Collecting":
                    seen[0] += 1
                if found:
                    report(f"Installing PyTorch and KittenTTS ({seen[0]} of {total})...",
                           0.05 + 0.6 * min(seen[0], total) / total)
                    return
                # PyTorch alone is about 2.5 GB: without this the window sits still for minutes.
                size = re.match(r"\s*Progress (\d+) of (\d+)", line)
                if size and int(size.group(2)) > 50_000_000:
                    report(f"Downloading PyTorch: {int(size.group(1)) / 1e9:.1f} of "
                           f"{int(size.group(2)) / 1e9:.1f} GB...",
                           0.05 + 0.6 * min(seen[0], total) / total)

            _run([python_exe(), "-m", "pip", "install", "--no-deps", "--require-hashes",
                  "--no-build-isolation", "--no-cache-dir", "--progress-bar", "raw", "-r", lock],
                 should_stop, on_line)
            open(os.path.join(folder(), "packages.ok"), "w").close()
        if not _models_done():
            report("Downloading the voice models (about 2 GB)...", 0.66)
            env_models = json.dumps([MODEL_REPO, MODEL_REVISION, DECODER_REPO, DECODER_REVISION,
                                     DECODER_FILE, models_dir()])
            os.environ["VOICEPACK_MODELS"] = env_models
            found = {}
            _run([python_exe(), "-c", FETCH_MODELS], should_stop,
                 lambda line: line.startswith("MODEL_DIR=") and found.update(
                     dir=line.split("=", 1)[1]))
            with open(os.path.join(folder(), "models.ok"), "w", encoding="utf-8") as fh:
                fh.write(found.get("dir", ""))
        with open(os.path.join(folder(), "speak.py"), "w", encoding="utf-8") as fh:
            fh.write(SPEAK)
        with open(_marker(), "w", encoding="utf-8") as fh:
            json.dump({"version": PACK_VERSION}, fh)
        report("The voice pack is ready.", 1.0)
    except writer.WriterError as exc:
        raise PackError(str(exc).replace("The Smart writer", "The voice pack")) from exc
    except OSError as exc:
        raise PackError(f"The voice pack could not be saved: {exc}") from exc


def model_dir() -> str:
    """The downloaded KittenTTS 2 folder (written when the models finished)."""
    try:
        with open(os.path.join(folder(), "models.ok"), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


# --- speaking --------------------------------------------------------------------------------

# Runs inside the pack's own Python. Loads the model once, then one job per line on stdin:
# {"text", "out", "reference", "reference_text"} -> {"ok", "seconds"} or {"ok": false, "error"}.
SPEAK = r'''
import json, os, sys, time
os.environ["HF_HUB_OFFLINE"] = "1"
import soundfile as sf
from kittenml import KittenTTS

started = time.time()
model = KittenTTS(sys.argv[1], cache_dir=sys.argv[2], device="cuda")
print(json.dumps({"ready": True, "load_seconds": round(time.time() - started, 1)}), flush=True)
for line in sys.stdin:
    try:
        job = json.loads(line)
        wav = model.generate(job["text"], reference=job["reference"],
                             reference_text=job.get("reference_text"),
                             advanced={"seed": job.get("seed", 7)})
        sf.write(job["out"], wav, model.sample_rate)
        print(json.dumps({"ok": True, "seconds": len(wav) / model.sample_rate}), flush=True)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}), flush=True)
'''


class Speaker:
    """The pack's speaker, loaded once for a whole video: ``say`` one line after another."""

    def __init__(self, popen=subprocess.Popen):
        if not installed():
            raise PackError("The voice pack isn't installed.")
        self.process = popen(
            [python_exe(), os.path.join(folder(), "speak.py"), model_dir(), models_dir()],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            encoding="utf-8", creationflags=NO_WINDOW, env=_env())
        first = self._answer()
        if not first.get("ready"):
            self.close()
            raise PackError("The voice pack could not start: " + first.get("error", "no answer"))
        self.load_seconds = first.get("load_seconds", 0.0)

    def _answer(self) -> dict:
        line = self.process.stdout.readline()
        if not line:
            return {"ok": False, "error": "the voice pack stopped (not enough graphics memory?)"}
        try:
            return json.loads(line)
        except ValueError:
            return {"ok": False, "error": line.strip()[:200]}

    def say(self, text: str, out: str, reference: str, reference_text: str) -> float:
        """Speak ``text`` into ``out`` in the voice of ``reference``; returns its length."""
        self.process.stdin.write(json.dumps({"text": text, "out": out, "reference": reference,
                                             "reference_text": reference_text}) + "\n")
        self.process.stdin.flush()
        answer = self._answer()
        if not answer.get("ok"):
            raise PackError(answer.get("error", "the voice pack gave no answer"))
        return float(answer["seconds"])

    def close(self) -> None:
        try:
            self.process.stdin.close()
            self.process.wait(timeout=10)
        except (OSError, ValueError, subprocess.SubprocessError):
            self.process.kill()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
