"""The Smart writer: an open-source language model, on this PC, that writes the script.

No key and no service: the model file is downloaded once and run locally by
llama.cpp, started as a separate program for as long as a script is being
written and stopped straight after, so its memory is free again before a
render begins. Nothing the user pastes leaves the computer.

The model rewrites and condenses the text and picks the search words. It can
fail in ordinary ways - not downloaded, too little memory, a slow PC, an odd
answer - and every one of them raises WriterError with a sentence for the
user, so the window can fall back to draft.write() and say why.

Pure of Tk. The network and the model process can both be replaced in tests.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import random
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

import requests

from . import draft, pacing, voices
from .footage import Stopped

__all__ = ["MODEL", "Session", "Stopped", "WriterError", "download", "installed", "ready",
           "remove", "runtime_path", "suggest", "write"]


class WriterError(Exception):
    """The Smart writer could not be used. The message is for the user."""


@dataclass(frozen=True)
class Model:
    name: str
    file: str
    url: str
    size: int  # bytes
    sha256: str
    licence: str

    @property
    def gigabytes(self) -> str:
        return f"{self.size / 1e9:.1f} GB"


# Chosen on 2026-10-08 from a test of three sizes: the smallest one that really
# rewrites the text and lands on the length asked for.
MODEL = Model(
    name="Qwen3 4B",
    file="Qwen3-4B-Q4_K_M.gguf",
    url="https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf",
    size=2_497_280_256,
    sha256="7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5",
    licence="Apache 2.0",
)

SERVER_EXE = "llama-server.exe"
GPU_LAYERS = "99"  # more layers than the model has: all of it on the graphics card
CONTEXT = 4096  # tokens the model can hold: instructions, the text and its answer
START_TIMEOUT = 90.0  # seconds for the model to load, on a slow disk
WRITE_TIMEOUT = 180.0  # seconds for one answer before the PC is judged too slow
# The model needs about this much free memory to load and run.
NEEDED_MEMORY = 4_500_000_000
WORDS_PER_SCENE = 10  # what the model writes per scene when asked for 8 to 18
MAX_SCENES = 40
CHARS_PER_TOKEN = 3.5  # rough, for English; only used to refuse text that cannot fit
CHUNK = 1024 * 1024
DOWNLOAD_TIMEOUT = (10, 60)


# --- where things live -------------------------------------------------------------

def models_dir() -> str:
    """Per-user, outside the install folder, so an app update keeps the model."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "AIVideoStudio", "models")


def model_path(model: Model | None = None) -> str:
    return os.path.join(models_dir(), (model or MODEL).file)


def _part_path(model: Model | None = None) -> str:
    return model_path(model) + ".part"


def installed(model: Model | None = None) -> bool:
    """Whether the whole model file is there. Its contents were checked when it arrived."""
    model = model or MODEL
    try:
        return os.path.getsize(model_path(model)) == model.size
    except OSError:
        return False


def remove(model: Model | None = None) -> None:
    """Delete the model and any unfinished download of it."""
    model = model or MODEL
    for path in (model_path(model), _part_path(model)):
        try:
            os.remove(path)
        except OSError:
            pass


def bundled(folder: str, exe: str) -> str | None:
    """A helper program: bundled in the built app, under vendor/ when run from source."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        base = os.path.join(repo, "vendor")
    candidate = os.path.join(base, folder, exe)
    return candidate if os.path.isfile(candidate) else None


def runtime_path() -> str | None:
    """llama.cpp's server program."""
    return bundled("llama", SERVER_EXE)


def free_memory() -> int | None:
    """Physical memory not in use, in bytes, or None where it cannot be asked."""
    class Status(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                    ("total", ctypes.c_ulonglong), ("available", ctypes.c_ulonglong),
                    ("page_total", ctypes.c_ulonglong), ("page_available", ctypes.c_ulonglong),
                    ("virtual_total", ctypes.c_ulonglong),
                    ("virtual_available", ctypes.c_ulonglong),
                    ("extended", ctypes.c_ulonglong)]

    try:
        status = Status()
        status.length = ctypes.sizeof(Status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return int(status.available)
    except (AttributeError, OSError):
        return None


def ready(memory=free_memory) -> tuple[bool, str]:
    """(whether the writer can run now, and if not, why - in words for the user)."""
    if runtime_path() is None:
        return False, "The Smart writer's program is missing from this copy of the app."
    if not installed():
        return False, "The Smart writer has not been downloaded yet."
    free = memory()
    if free is not None and free < NEEDED_MEMORY:
        return False, ("This PC doesn't have enough free memory for the Smart writer right now "
                       f"(it needs about {NEEDED_MEMORY / 1e9:.1f} GB). Closing other programs "
                       "may help.")
    return True, ""


# --- the one-time download ---------------------------------------------------------

def _hash_file(path: str, should_stop=None):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            if should_stop is not None and should_stop():
                raise Stopped()
            block = fh.read(CHUNK)
            if not block:
                return digest
            digest.update(block)


def download(progress=None, should_stop=None, session=None, model: Model | None = None) -> str:
    """Fetch the model, carrying on from an unfinished download if there is one.

    ``progress(done_bytes, total_bytes)`` is called as it goes. The file is
    written under a .part name and only gets its real name once its SHA-256
    matches, so a half-written or tampered file is never mistaken for the
    model. Raises Stopped when ``should_stop`` says so (the part is kept for
    next time) and WriterError for anything else.
    """
    model = model or MODEL
    if installed(model):
        return model_path(model)
    http = session or requests
    part = _part_path(model)
    try:
        os.makedirs(models_dir(), exist_ok=True)
        have = os.path.getsize(part) if os.path.isfile(part) else 0
        if have > model.size:  # not ours, or a different file: start again
            os.remove(part)
            have = 0
        if have < model.size:
            headers = {"Range": f"bytes={have}-"} if have else {}
            with http.get(model.url, stream=True, timeout=DOWNLOAD_TIMEOUT,
                          headers=headers) as response:
                response.raise_for_status()
                if have and response.status_code != 206:
                    have = 0  # the server sent the whole file again; so start the file again
                with open(part, "ab" if have else "wb") as fh:
                    done = have
                    for chunk in response.iter_content(chunk_size=CHUNK):
                        if should_stop is not None and should_stop():
                            raise Stopped()
                        if chunk:
                            fh.write(chunk)
                            done += len(chunk)
                            if progress is not None:
                                progress(min(done, model.size), model.size)
        if os.path.getsize(part) != model.size or \
                _hash_file(part, should_stop).hexdigest() != model.sha256:
            os.remove(part)
            raise WriterError("The Smart writer download did not arrive intact. "
                              "Please try the download again.")
        os.replace(part, model_path(model))
    except requests.RequestException as exc:
        raise WriterError("The Smart writer could not be downloaded. Check your internet "
                          f"connection and try again. ({type(exc).__name__})") from exc
    except OSError as exc:
        raise WriterError(f"The Smart writer could not be saved: {exc}") from exc
    return model_path(model)


# --- what the model is asked -------------------------------------------------------

# One worked example, given as a past exchange. It teaches the shape of the
# answer far better than more rules do, and stops the model copying sentences.
_EXAMPLE_IN = ("Honey never spoils. Archaeologists have found pots of honey in Egyptian tombs "
               "that are over 3,000 years old and still safe to eat. Honey has very little "
               "water and is acidic, so bacteria cannot grow in it.")
_EXAMPLE_OUT = json.dumps({"scenes": [
    {"visual": "honey jar spoon",
     "voice": "There is one food in your kitchen that will never go bad."},
    {"visual": "egyptian pyramid desert",
     "voice": "Honey found in Egyptian tombs was still safe to eat after 3,000 years."},
    {"visual": "bees honeycomb",
     "voice": "It holds so little water that bacteria simply cannot live in it."},
]})


def target_words(seconds: float, persona, scenes: int | None = None) -> int:
    """About how many spoken words fill ``seconds`` in this voice."""
    if scenes is None:  # words and scenes depend on each other; settle them together
        scenes = scene_count(target_words(seconds, persona, 0))
    speech = max(seconds - scenes * 0.3, 1.0)
    return max(8, round(speech * pacing.CHARS_PER_SECOND * pacing.rate_factor(persona.rate) / 6.0))


def scene_count(words: int) -> int:
    return min(max(2, round(words / WORDS_PER_SCENE)), MAX_SCENES)


def build_prompt(audience: draft.Audience, words: int) -> str:
    """The instructions for writing a script of about ``words`` spoken words."""
    return (
        "You are a scriptwriter for short narrated videos made from stock footage. "
        "Rewrite the user's text as a script, in your own words. Do not copy its sentences.\n"
        f"Audience and style: {audience.brief}\n"
        f"Length: all the spoken lines together must be about {words} words "
        f"({round(words * 0.85)} to {round(words * 1.1)}). Keep the most important points "
        "and leave the rest out.\n"
        f"Write exactly {scene_count(words)} scenes.\n"
        "Rules:\n"
        "- The first scene is a hook: a surprising or curious line that makes people keep "
        "watching.\n"
        "- \"voice\" is one full spoken sentence of 8 to 18 words.\n"
        "- \"visual\" is 2 or 3 words for a stock video search: real things a camera can film "
        "that match the line. Every scene gets different words. No brand names, no single "
        "words.\n"
        "- Use only facts from the text. No greetings, hashtags or emojis.\n"
        "Answer with JSON only."
    )


def script_schema(scenes: int) -> dict:
    """Fixing the number of scenes is what makes the length land (see MODEL's test)."""
    return {
        "type": "object",
        "properties": {"scenes": {
            "type": "array", "minItems": scenes, "maxItems": scenes,
            "items": {"type": "object",
                      "properties": {"visual": {"type": "string"}, "voice": {"type": "string"}},
                      "required": ["visual", "voice"]}}},
        "required": ["scenes"],
    }


_SUGGEST_PROMPT = (
    "You sort texts for a video maker. Read the user's text and choose what it is about "
    "and who it is written for.\n"
    "\"content\" is the subject of the text:\n"
    "- Tech and how-to: computers, phones, apps, gadgets, step-by-step instructions for them\n"
    "- Story and facts: history, science, nature, surprising facts, true stories\n"
    "- Motivation: self-improvement, habits, mindset, encouragement\n"
    "- Business and money: saving, spending, investing, prices, jobs, companies\n"
    "- Kids and family: written for children, or about family life\n"
    "\"age\" is who the wording suits. Choose Adults unless the text is clearly written "
    "for someone younger.\n"
    "Answer with JSON only."
)
_SUGGEST_SCHEMA = {
    "type": "object",
    "properties": {"content": {"type": "string", "enum": list(draft.CONTENT)},
                   "age": {"type": "string", "enum": list(draft.AGES)}},
    "required": ["content", "age"],
}


# Stock sites have no screenshots, so these words only bring back wrong clips ("clipboard"
# is an office clipboard). Four prompt versions did not stop the model describing the
# screen (measured 2026-10-10), so the words are taken out here instead.
# ponytail: "windows" of a house are caught too; if that bites, look at the pasted text.
_UNFILMABLE = frozenset(
    # things that only exist on a screen
    "ai app apps browser clipboard cursor feature features icon icons interface logo menu "
    "notification notifications screenshot screenshots setting settings shortcut shortcuts "
    "snipping software website windows popup toolbar taskbar dialog dropdown checkbox widget "
    "plugin extension url hotkey ctrl alt toggle mode option options version ui "
    "item items pinned selected highlighted caption captions subtitle subtitles voiceover "
    "footage clips watermark subscription account algorithm pixel pixels resolution "
    # things done on a screen
    "click clicks clicking clicked paste pasted pasting scroll scrolling download downloads "
    "downloading upload uploading install installing sync syncing "
    # brands: stock sites have none of their screens or logos
    "microsoft google youtube tiktok instagram facebook chrome android".split())
_COMPUTER_SHOTS = (
    "person typing laptop", "hands keyboard close up", "woman working computer",
    "man using laptop", "hand computer mouse", "person looking monitor",
    "fingers pressing keys", "office desk computer",
)


def filmable(visual: str, turn: int = 0) -> str:
    """``visual`` without the words no camera can film.

    When fewer than two words are left, the scene shows someone at a computer;
    ``turn`` picks which shot, so neighbouring scenes differ.
    """
    words = visual.split()
    kept = [w for w in words if w.lower() not in _UNFILMABLE]
    if len(kept) < len(words):  # "windows key v": the letter means nothing without the rest
        kept = [w for w in kept if len(w) > 1]
    if len(kept) == len(words):
        return visual
    if len(kept) >= 2:
        return " ".join(kept)
    return _COMPUTER_SHOTS[turn % len(_COMPUTER_SHOTS)]


def parse_scenes(content: str) -> list[draft.DraftScene]:
    """The model's answer as scenes. Repeated and empty lines are dropped."""
    try:
        items = json.loads(content)["scenes"]
        if not isinstance(items, list):
            raise TypeError("scenes is not a list")
    except (ValueError, KeyError, TypeError) as exc:
        raise WriterError("The Smart writer gave an answer the app could not read.") from exc
    scenes, said = [], set()
    for item in items:
        if not isinstance(item, dict):
            continue
        voice = " ".join(str(item.get("voice") or "").split())
        key = voice.lower()
        if not voice or key in said:
            continue  # small models sometimes say a line twice
        said.add(key)
        visual = filmable(str(item.get("visual") or ""), len(scenes))
        scenes.append(draft.DraftScene(visual, voice, len(scenes)))
    if not scenes:
        raise WriterError("The Smart writer gave an empty answer.")
    return scenes


# --- running the model -------------------------------------------------------------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _launch(exe: str, model_file: str, port: int, gpu: bool = False):
    """Start llama.cpp, reachable from this PC only, with no window of its own."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        [exe, "-m", model_file, "--host", "127.0.0.1", "--port", str(port),
         "-c", str(CONTEXT), "-np", "1", "--no-webui",
         # Always said out loud: left alone, llama.cpp may pick the graphics card itself.
         "-ngl", GPU_LAYERS if gpu else "0"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        creationflags=flags)


class Session:
    """The model, loaded for as long as this is open. Use as ``with Session() as s:``.

    Loading takes a couple of seconds, so the window keeps one session from
    "Analyse" through "Write script" and closes it when it is done.
    ``should_stop`` is asked while waiting; when it says yes the model is
    stopped at once and Stopped is raised.
    """

    def __init__(self, should_stop=None, launch=_launch, http=requests, check=ready,
                 gpu: bool = False):
        self.should_stop = should_stop or (lambda: False)
        self.gpu = gpu
        self.gpu_failed = False  # the graphics card was asked for and could not be used
        self._launch, self._http, self._check = launch, http, check
        self._proc = None
        self._url = ""
        self._lock = threading.Lock()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def _stopping(self):
        if self.should_stop():
            self.close()
            raise Stopped()

    def start(self):
        """Load the model if it is not loaded yet."""
        if self._proc is not None and self._proc.poll() is None:
            return
        ok, why = self._check()
        if not ok:
            raise WriterError(why)
        port = _free_port()
        self._url = f"http://127.0.0.1:{port}"
        try:
            self._proc = self._launch(runtime_path(), model_path(), port, self.gpu)
        except OSError as exc:
            raise WriterError(f"The Smart writer could not be started: {exc}") from exc
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            self._stopping()
            if self._proc.poll() is not None:
                self._proc = None
                if self.gpu:
                    # Too little video memory or a driver problem: the processor still works.
                    self.gpu, self.gpu_failed = False, True
                    return self.start()
                raise WriterError("The Smart writer stopped while loading. This PC may not "
                                  "have enough memory for it.")
            try:
                if self._http.get(self._url + "/health", timeout=1).status_code == 200:
                    return
            except requests.RequestException:
                pass
            time.sleep(0.25)
        self.close()
        raise WriterError("The Smart writer took too long to load on this PC.")

    def close(self):
        """Stop the model and give its memory back. Safe to call twice, from any thread."""
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.kill()
                proc.wait(timeout=10)
            except (OSError, subprocess.SubprocessError):
                pass

    def _ask(self, system: str, turns: list[tuple[str, str]], schema: dict,
             max_tokens: int) -> str:
        """One answer from the model, as text that fits ``schema``."""
        self.start()
        messages = [{"role": "system", "content": system}]
        messages += [{"role": role, "content": content} for role, content in turns]
        body = {
            "messages": messages, "temperature": 0.6, "max_tokens": max_tokens,
            "seed": random.randrange(1, 2 ** 31),
            # Qwen3 would otherwise think out loud first, which costs time and fits no schema.
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "answer", "schema": schema}},
        }
        # The request blocks, so a watcher stops the model when the user cancels
        # or the PC proves too slow; the request then fails and we say which.
        finished, timed_out = threading.Event(), threading.Event()

        def watch():
            deadline = time.monotonic() + WRITE_TIMEOUT
            while not finished.wait(0.2):
                if self.should_stop():
                    self.close()
                    return
                if time.monotonic() > deadline:
                    timed_out.set()
                    self.close()
                    return

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            reply = self._http.post(self._url + "/v1/chat/completions", json=body,
                                    timeout=WRITE_TIMEOUT + 30)
            reply.raise_for_status()
            return reply.json()["choices"][0]["message"]["content"]
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
            if self.should_stop():
                raise Stopped() from exc
            if timed_out.is_set():
                raise WriterError("The Smart writer was too slow on this PC.") from exc
            raise WriterError("The Smart writer stopped before it finished.") from exc
        finally:
            finished.set()
            watcher.join(timeout=2)

    # -- what it is used for ---------------------------------------------------------
    def suggest(self, text: str, last: draft.Audience | None = None,
                padding: float = 0.3) -> draft.Suggestion:
        """Who the text is for, judged by the model. The length rule stays draft's."""
        _check_fits(text, 60)
        rules = draft.suggest(text, last, padding)
        content = self._ask(_SUGGEST_PROMPT, [("user", text)], _SUGGEST_SCHEMA, 60)
        try:
            picked = json.loads(content)
            kind, age = picked["content"], picked["age"]
            if kind not in draft.CONTENT or age not in draft.AGES:
                raise KeyError(kind)
        except (ValueError, KeyError, TypeError) as exc:
            raise WriterError("The Smart writer gave an answer the app could not read.") from exc
        audience = draft.Audience(kind, age, rules.audience.platform)
        # Re-run the length rule for the audience the model chose.
        length = draft.suggest_length(text, audience)
        seconds = round(draft.analyse(text, voices.persona(audience.voice), padding).seconds)
        reason = (f"This reads like {kind.lower()} for {age.lower()}, "
                  f"about {seconds} s when read in full.")
        return draft.Suggestion(audience, length, reason)

    def write(self, text: str, audience: draft.Audience | None = None,
              length: str | None = None, padding: float = 0.3,
              cards: bool = True) -> draft.Draft:
        """A script for ``text``, rewritten for the audience and the length."""
        audience = audience or draft.Audience()
        length = length if length in draft.LENGTHS else audience.length
        persona = voices.persona(audience.voice)
        text = " ".join((text or "").split())
        if not text:
            raise draft.DraftError("Paste some text first.")
        target = pacing.TARGETS.get(length)
        if target is None:  # keep everything: as many words as the text has
            words = max(8, len(draft.words_in(text)))
        else:
            words = target_words(target, persona)

        scenes = self._write_once(text, audience, words)
        if target is not None and not _fits(scenes, target, persona):
            # Too long. Ask once more for less, then cut what still does not fit.
            spoken = sum(len(draft.words_in(s.voice)) for s in scenes)
            fewer = max(8, round(words * words / max(spoken, words)))
            self._stopping()
            scenes = self._write_once(text, audience, fewer)
            if not _fits(scenes, target, persona):
                scenes = _trim(scenes, target, persona)
        script = draft.to_script(scenes, cards)
        seconds = sum(pacing.estimate_voice(s.voice, persona.rate, persona.pause_scale,
                                            persona.enhanced) + padding for s in scenes)
        return draft.Draft(script, scenes, seconds, length, persona.id, audience.aspect,
                           source="Smart writer")

    def _write_once(self, text, audience, words):
        _check_fits(text, _answer_tokens(words))
        content = self._ask(
            build_prompt(audience, words),
            [("user", _EXAMPLE_IN), ("assistant", _EXAMPLE_OUT), ("user", text)],
            script_schema(scene_count(words)), _answer_tokens(words))
        return parse_scenes(content)


def _answer_tokens(words: int) -> int:
    return min(round(words * 2.4) + 200, CONTEXT // 2)


def _check_fits(text: str, answer_tokens: int) -> None:
    """Refuse text the model cannot hold, instead of letting it be cut off silently."""
    room = CONTEXT - answer_tokens - 700  # 700: the instructions and the example
    if len(text) / CHARS_PER_TOKEN > room:
        limit = round(room * CHARS_PER_TOKEN / 6 / 50) * 50
        raise WriterError(f"This text is too long for the Smart writer (about {limit} words "
                          "is its limit).")


def _fits(scenes, target: float, persona) -> bool:
    return draft.fits([s.voice for s in scenes], target, persona)


def _trim(scenes, target: float, persona):
    """Keep the hook and the most important lines that fit ``target``."""
    lines = [s.voice for s in scenes]
    kept = draft.select_for_length(lines, lambda i: [lines[i]], target, persona)
    return [scenes[i] for i in kept]


def write(text, audience=None, length=None, should_stop=None, padding: float = 0.3,
          gpu: bool = False):
    """Load the model, write one script, stop the model."""
    with Session(should_stop, gpu=gpu) as session:
        return session.write(text, audience, length, padding)


def suggest(text, last=None, should_stop=None, padding: float = 0.3, gpu: bool = False):
    """Load the model, ask who the text is for, stop the model."""
    with Session(should_stop, gpu=gpu) as session:
        return session.suggest(text, last, padding)
