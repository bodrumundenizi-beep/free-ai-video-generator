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
import re
import socket
import subprocess
import sys
import threading
import time
import zlib
from dataclasses import dataclass

import requests

from . import draft, pacing, voices
from .footage import Stopped
from .script import parse_script

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


def total_memory() -> int | None:
    """Physical memory fitted, in bytes, or None where it cannot be asked."""
    return _memory("total")


def free_memory() -> int | None:
    """Physical memory not in use, in bytes, or None where it cannot be asked."""
    return _memory("available")


def _memory(field: str) -> int | None:
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
        return int(getattr(status, field))
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


# The same example with a picture for each scene, for when AI images are downloaded.
_EXAMPLE_PICTURES = (
    "a glass jar of golden honey with a wooden spoon on a kitchen table, morning light",
    "an ancient clay pot of honey inside a dim Egyptian tomb, hieroglyphs on the stone wall",
    "a close-up of bees crawling over a honeycomb full of honey",
)
_EXAMPLE_OUT_PICTURES = json.dumps({"scenes": [
    dict(scene, picture=picture)
    for scene, picture in zip(json.loads(_EXAMPLE_OUT)["scenes"], _EXAMPLE_PICTURES)]})
PICTURE_TOKENS = 45  # room in the answer for one scene's picture description


_IDEA_START = re.compile(r"^\s*(please\s+)?(write|make|create|tell|give|generate|do)\b", re.I)
_STORY = re.compile(r"\b(story|tale|horror|adventure|thriller|mystery)\b", re.I)


def is_idea(text: str) -> bool:
    """A request for a video ("write a story about being followed at night"), not a text to
    turn into one: short, and asks for something."""
    words = len(text.split())
    return words <= 40 and (bool(_IDEA_START.search(text)) or words <= 12)


def build_prompt(audience: draft.Audience, words: int, pictures: bool = False,
                 idea: str = "") -> str:
    """The instructions for writing a script of about ``words`` spoken words.

    With ``pictures`` every scene also gets a description an image model can draw. With
    ``idea`` (the user asked for a video rather than giving a text) the script is invented:
    for a story, events that happen one after another, not a mood described.
    """
    if idea and _STORY.search(idea):
        task = ("The user gives an idea for a story. Write an original short story from it, "
                "told as narration. Something happens in every scene: a character does "
                "something, hears or sees something, decides something. It builds: a calm "
                "start, rising tension, a turn, and an ending that pays off. Use concrete "
                "details (a sound, a place, an object), not feelings described in general.\n")
    elif idea:
        task = ("The user gives an idea for a video, not a text. Write an original script "
                "about it with concrete facts, steps or examples, one new point per scene.\n")
    else:
        task = ("Rewrite the user's text as a script, in your own words. Do not copy its "
                "sentences.\n")
    return (
        "You are a scriptwriter for short narrated videos made from stock footage. "
        + task +
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
        + ("- \"picture\" is one sentence describing a photo for this scene: who or what is "
           "in it, what they are doing and where. Things you can see, not ideas. No writing, "
           "logos or brand names in the picture.\n" if pictures else "")
        + ("- No greetings, hashtags or emojis.\n" if idea else
           "- Use only facts from the text. No greetings, hashtags or emojis.\n")
        + "Answer with JSON only."
    )


def script_schema(scenes: int, pictures: bool = False) -> dict:
    """Fixing the number of scenes is what makes the length land (see MODEL's test)."""
    extra = {"picture": {"type": "string"}} if pictures else {}
    return {
        "type": "object",
        "properties": {"scenes": {
            "type": "array", "minItems": scenes, "maxItems": scenes,
            "items": {"type": "object",
                      "properties": {"visual": {"type": "string"},
                                     "voice": {"type": "string"}, **extra},
                      "required": ["visual", "voice", *extra]}}},
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
    "fingers pressing keys", "office desk computer", "student laptop library",
    "woman smiling laptop", "man coffee laptop", "home office desk", "laptop kitchen table",
    "young man computer night", "woman taking notes laptop", "colleagues looking screen",
    "person working cafe", "hands tablet couch", "programmer desk monitors",
    "freelancer laptop window",
)
# Taking the words from the "picture" sentence instead was measured (2026-10-16): it
# describes the screen too ("settings menu with clipboard history toggle") and doubles
# the writing time. So: many generic shots, and each video uses different ones.


def filmable(visual: str, turn: int = 0) -> str:
    """``visual`` without the words no camera can film.

    When fewer than two words are left, the scene shows someone at a computer;
    ``turn`` picks which shot, so the scenes of a video differ.
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


# --- changing a finished script ------------------------------------------------------

_REVISE_TOKENS = 1500
_REVISE_PROMPT = (
    "You edit the script of a short narrated video. The user gives the script as scenes "
    "and one comment saying what to change. Make that change and nothing else.\n"
    "Rules:\n"
    "- A comment about how the script sounds (more exciting, more serious, funnier, simpler, "
    "for another audience) means rewriting the voice lines in that way: new wording, same "
    "facts.\n"
    "- A comment about one part (the start, one scene, one topic) means changing only that "
    "part; the other scenes come back exactly as they were.\n"
    "- \"voice\" is one full spoken sentence of 8 to 18 words.\n"
    "- \"picture\" says what is seen in that scene, in the same style as the other scenes.\n"
    "- To shorten, remove the least important scenes. To remove a topic, remove its scenes.\n"
    "- Do not add facts that are not already in the script.\n"
    "Answer with JSON only: the whole script after the change."
)
_REVISE_EXAMPLE_BEFORE = [
    {"picture": "honey jar spoon", "voice": "There is one food in your kitchen that will never go bad."},
    {"picture": "egyptian pyramid desert",
     "voice": "Honey found in Egyptian tombs was still safe to eat after 3,000 years."},
    {"picture": "bees honeycomb", "voice": "It holds so little water that bacteria simply cannot live in it."},
]
_REVISE_EXAMPLE_COMMENT = "make the first line more exciting"
_REVISE_EXAMPLE_AFTER = [
    {"picture": "honey jar spoon", "voice": "Stop throwing this away, because it is the one food that never dies."},
    *_REVISE_EXAMPLE_BEFORE[1:],
]
def _revise_schema(fewest: int, most: int) -> dict:
    return {
        "type": "object",
        "properties": {"scenes": {
            "type": "array", "minItems": fewest, "maxItems": most,
            "items": {"type": "object",
                      "properties": {"picture": {"type": "string"}, "voice": {"type": "string"}},
                      "required": ["picture", "voice"]}}},
        "required": ["scenes"],
    }


_REVISE_SCHEMA = _revise_schema(1, MAX_SCENES)
_LONGER = re.compile(r"\b(longer|lengthen|extend|expand|more detail|add more|more scenes|"
                     r"too short|not long enough)\b", re.I)
_SHORTER = re.compile(r"\b(shorter|shorten|trim|condense|briefer|cut it down|fewer scenes|"
                      r"too long)\b", re.I)
_A_LITTLE = re.compile(r"\b(little|bit|slightly|tad|somewhat|touch)\b", re.I)
_A_LOT = re.compile(r"\b(much|lot|way|far|twice|double|half|significantly|really)\b", re.I)


def length_change(comment: str, scenes: int) -> int | None:
    """How many scenes a comment about length asks for, or None if it is about something else.

    "A little" is about a fifth, "a lot" about half, and anything in between a
    third; always at least one scene's difference, and never fewer than two scenes.
    """
    longer, shorter = bool(_LONGER.search(comment)), bool(_SHORTER.search(comment))
    if longer == shorter:
        return None
    share = 0.2 if _A_LITTLE.search(comment) else 0.5 if _A_LOT.search(comment) else 0.33
    step = max(1, round(scenes * share))
    wanted = min(scenes + step, MAX_SCENES) if longer else max(scenes - step, 2)
    return wanted if wanted != scenes else None


def _revise_turn(scenes, comment: str) -> str:
    return f"Script:\n{json.dumps({'scenes': scenes})}\n\nComment: {comment}"


def parse_scenes(content: str) -> list[draft.DraftScene]:
    """The model's answer as scenes. Repeated and empty lines are dropped."""
    try:
        items = json.loads(content)["scenes"]
        if not isinstance(items, list):
            raise TypeError("scenes is not a list")
    except (ValueError, KeyError, TypeError) as exc:
        raise WriterError("The Smart writer gave an answer the app could not read.") from exc
    scenes, said = [], set()
    # Each video starts somewhere else in the generic shots, so videos don't all look alike.
    first = next((i.get("voice") for i in items if isinstance(i, dict)), "")
    generic = zlib.crc32(str(first).encode("utf-8"))
    for item in items:
        if not isinstance(item, dict):
            continue
        voice = " ".join(str(item.get("voice") or "").split())
        key = voice.lower()
        if not voice or key in said:
            continue  # small models sometimes say a line twice
        said.add(key)
        asked = str(item.get("visual") or "")
        picture = " ".join(str(item.get("picture") or "").split())
        visual = filmable(asked, generic)
        generic += visual in _COMPUTER_SHOTS
        scenes.append(draft.DraftScene(visual, voice, len(scenes), picture, visual != asked))
    if not scenes:
        raise WriterError("The Smart writer gave an empty answer.")
    return scenes


# --- text for posting the video ---------------------------------------------------

_UPLOAD_TOKENS = 300
_UPLOAD_PROMPT = (
    "You write the text for posting a short video online. The user gives what the video "
    "says.\n"
    "- \"title\": under 70 characters, written like a headline with normal capital letters, "
    "makes people curious, no clickbait lies, no emojis.\n"
    "- \"description\": 2 or 3 plain sentences about what the viewer learns, with normal "
    "capital letters.\n"
    "- \"tags\": 5 to 10 search words or short phrases, only these in lower case, no # "
    "signs.\n"
    "Use only what the video says. Answer with JSON only."
)
_UPLOAD_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "description": {"type": "string"},
                   "tags": {"type": "array", "items": {"type": "string"},
                            "minItems": 1, "maxItems": 10}},
    "required": ["title", "description", "tags"],
}


def _translate_tokens(lines) -> int:
    # Other languages take more tokens per word than English; Hindi about three times.
    return min(200 + sum(len(line) for line in lines) * 2, CONTEXT // 2)


def tidy_upload(title, description, tags) -> dict:
    """The upload text cleaned up: one-line title of at most 70 characters, unique tags."""
    title = " ".join(str(title).split()).strip("\"' ")
    if len(title) > 70:
        title = title[:70].rsplit(" ", 1)[0].rstrip(",.;:-")
    seen, clean = set(), []
    for tag in tags if isinstance(tags, list) else []:
        tag = " ".join(str(tag).lower().lstrip("#").split())
        if tag and tag not in seen:
            seen.add(tag)
            clean.append(tag)
    description = " ".join(str(description).split())
    if title == title.lower():   # the small model sometimes writes it all in lower case
        title = title[:1].upper() + title[1:]
    if description == description.lower():
        description = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m[1] + m[2].upper(), description)
    return {"title": title, "description": description, "tags": clean[:10]}


def upload_fallback(script: str) -> dict:
    """Without the Smart writer: the first line as the title, the search words as tags."""
    scenes = parse_script(script)
    lines = [s.voice for s in scenes if s.voice]
    title = lines[0] if lines else "My video"
    tags = [s.visual for s in scenes if not s.is_ai and not s.is_local]
    return tidy_upload(title, " ".join(lines[:2]), tags)


def upload_file_text(upload: dict) -> str:
    return (f"Title:\n{upload['title']}\n\nDescription:\n{upload['description']}\n\n"
            f"Tags:\n{', '.join(upload['tags'])}\n")


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


# Every Session with a running model, so the app can stop them all as it closes: a worker
# thread dies with the app, and its model would otherwise keep 2.5 GB of memory.
_LIVE: set = set()
_LIVE_LOCK = threading.Lock()


def close_all() -> None:
    with _LIVE_LOCK:
        live = list(_LIVE)
    for session in live:
        session.close()


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
        with _LIVE_LOCK:
            _LIVE.add(self)
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
        with _LIVE_LOCK:
            _LIVE.discard(self)
        if proc is not None:
            try:
                proc.kill()
                proc.wait(timeout=10)
            except (OSError, subprocess.SubprocessError):
                pass

    def _ask(self, system: str, turns: list[tuple[str, str]], schema: dict,
             max_tokens: int, temperature: float = 0.6) -> str:
        """One answer from the model, as text that fits ``schema``."""
        self.start()
        messages = [{"role": "system", "content": system}]
        messages += [{"role": role, "content": content} for role, content in turns]
        body = {
            "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
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
    def translate(self, lines: list[str], language: str) -> list[str]:
        """``lines`` in ``language``, one for one: the scenes keep their pictures."""
        if not lines:
            return []
        asked = json.dumps({"lines": lines}, ensure_ascii=False)
        _check_fits(asked, _translate_tokens(lines))
        prompt = (f"You are a professional translator and a native {language} speaker. "
                  f"Translate each line of a short narrated video into {language}, the way a "
                  f"native speaker would say it: correct grammar and spelling, everyday "
                  f"words, the usual {language} names for computer things. Translate every "
                  "word; leave no English words except names and key combinations such as "
                  "Win + V. Keep the meaning and the order, one translated line for each line. "
                  "Answer with JSON only.")
        schema = {"type": "object", "required": ["lines"], "properties": {"lines": {
            "type": "array", "items": {"type": "string"},
            "minItems": len(lines), "maxItems": len(lines)}}}
        # Low temperature: a translation should be the likeliest wording, not a creative one.
        content = self._ask(prompt, [("user", asked)], schema, _translate_tokens(lines), 0.2)
        try:
            found = [" ".join(str(line).split()) for line in json.loads(content)["lines"]]
        except (ValueError, KeyError, TypeError) as exc:
            raise WriterError("The Smart writer gave an answer the app could not read.") from exc
        if len(found) != len(lines) or not all(found):
            raise WriterError("The Smart writer left out a line. Try again.")
        return found

    def upload_text(self, script: str) -> dict:
        """A title, description and tags for posting the video. Nothing is posted."""
        spoken = " ".join(s.voice for s in parse_script(script) if s.voice)
        _check_fits(spoken, _UPLOAD_TOKENS)
        content = self._ask(_UPLOAD_PROMPT, [("user", spoken)], _UPLOAD_SCHEMA, _UPLOAD_TOKENS)
        try:
            answer = json.loads(content)
            return tidy_upload(answer["title"], answer["description"], answer["tags"])
        except (ValueError, KeyError, TypeError) as exc:
            raise WriterError("The Smart writer gave an answer the app could not read.") from exc

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
              cards: bool = True, ai: str = draft.AI_NONE) -> draft.Draft:
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

        pictures = bool(ai)
        scenes = self._write_once(text, audience, words, pictures)
        if target is not None and not _fits(scenes, target, persona):
            # Too long. Ask once more for less, then cut what still does not fit.
            spoken = sum(len(draft.words_in(s.voice)) for s in scenes)
            fewer = max(8, round(words * words / max(spoken, words)))
            self._stopping()
            scenes = self._write_once(text, audience, fewer, pictures)
            if not _fits(scenes, target, persona):
                scenes = _trim(scenes, target, persona)
        script = draft.to_script(scenes, cards, ai)
        seconds = sum(pacing.estimate_voice(s.voice, persona.rate, persona.pause_scale,
                                            persona.enhanced) + padding for s in scenes)
        return draft.Draft(script, scenes, seconds, length, persona.id, audience.aspect,
                           source="Smart writer")

    def revise(self, scenes: list[tuple[str, str]], comment: str,
               source: str = "") -> list[tuple[str, str]]:
        """The script changed the way ``comment`` asks. Scenes are (picture, voice) pairs.

        The answer keeps the scene format, so it is always a usable script; what
        the comment did not ask about should come back word for word. ``source``
        is the text the script was written from, if known: more to draw on when
        the comment asks for a longer video.
        """
        comment = " ".join((comment or "").split())
        if not comment:
            raise WriterError("Say what you would like changed first.")
        script = json.dumps({"scenes": [{"picture": p, "voice": v} for p, v in scenes]})
        _check_fits(script + comment, _REVISE_TOKENS)
        # "A little longer" is not something this model can weigh, but it does write the
        # number of scenes it is told to (see script_schema): so the length is settled here.
        wanted = length_change(comment, len(scenes))
        schema, asked = _REVISE_SCHEMA, comment
        if wanted:
            schema = _revise_schema(wanted, wanted)
            more = wanted > len(scenes)
            asked += (f"\n(The script has {len(scenes)} scenes now. Write exactly {wanted}: "
                      + ("keep the scenes there are and add new ones that say more about the "
                         "same subject." if more else
                         "keep the most important scenes as they are and drop the rest.") + ")")
            source = " ".join(source.split())[:1500]
            if more and source:
                asked += f"\nMore to draw on, from the text the video is about: {source}"
        turns = [("user", _revise_turn(_REVISE_EXAMPLE_BEFORE, _REVISE_EXAMPLE_COMMENT)),
                 ("assistant", json.dumps({"scenes": _REVISE_EXAMPLE_AFTER})),
                 ("user", _revise_turn(json.loads(script)["scenes"], asked))]
        found = []
        for attempt in range(2):
            content = self._ask(_REVISE_PROMPT, turns, schema, _REVISE_TOKENS)
            try:
                items = json.loads(content)["scenes"]
                found = [(" ".join(str(i["picture"]).split()), " ".join(str(i["voice"]).split()))
                         for i in items]
            except (ValueError, KeyError, TypeError) as exc:
                raise WriterError(
                    "The Smart writer gave an answer the app could not read.") from exc
            found = [(picture, voice) for picture, voice in found if voice]
            if found != list(scenes):
                break
            # Told to leave the rest alone, this model sometimes leaves everything alone.
            self._stopping()
            turns += [("assistant", content),
                      ("user", "That is the same script: nothing was changed. Carry out the "
                               f"comment this time: {comment}")]
        if not found:
            raise WriterError("The Smart writer gave an empty answer.")
        return found

    def _write_once(self, text, audience, words, pictures=False):
        room = _answer_tokens(words, pictures)
        _check_fits(text, room)
        example = _EXAMPLE_OUT_PICTURES if pictures else _EXAMPLE_OUT
        content = self._ask(
            build_prompt(audience, words, pictures, text if is_idea(text) else ""),
            [("user", _EXAMPLE_IN), ("assistant", example), ("user", text)],
            script_schema(scene_count(words), pictures), room)
        return parse_scenes(content)


def _answer_tokens(words: int, pictures: bool = False) -> int:
    extra = scene_count(words) * PICTURE_TOKENS if pictures else 0
    return min(round(words * 2.4) + 200 + extra, CONTEXT // 2)


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
