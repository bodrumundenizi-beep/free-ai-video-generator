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
import dataclasses
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

from . import draft, pacing, voices, webfacts
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

# Chosen on 2026-11-03: the smallest model that says what is in a photo (photos.py), run by
# the same llama.cpp. It comes with the Smart writer's download, not as one of its own.
_SMOL = "https://huggingface.co/ggml-org/SmolVLM-500M-Instruct-GGUF/resolve/main/"
VISION = Model(
    name="SmolVLM 500M",
    file="SmolVLM-500M-Instruct-Q8_0.gguf",
    url=_SMOL + "SmolVLM-500M-Instruct-Q8_0.gguf",
    size=436_806_912,
    sha256="9d4612de6a42214499e301494a3ecc2be0abdd9de44e663bda63f1152fad1bf4",
    licence="Apache 2.0",
)
VISION_PROJ = Model(   # the part that turns a picture into something the model can read
    name="SmolVLM 500M image reader",
    file="mmproj-SmolVLM-500M-Instruct-Q8_0.gguf",
    url=_SMOL + "mmproj-SmolVLM-500M-Instruct-Q8_0.gguf",
    size=108_783_360,
    sha256="d1eb8b6b23979205fdf63703ed10f788131a3f812c7b1f72e0119d5d81295150",
    licence="Apache 2.0",
)
FILES = (MODEL, VISION, VISION_PROJ)

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


def sees() -> bool:
    """Whether the photo model is there too (photos.py)."""
    return installed(VISION) and installed(VISION_PROJ)


def missing() -> list[Model]:
    """The Smart writer's files still to download: all three, or only the photo model for a
    copy downloaded before 5.0."""
    return [m for m in FILES if not installed(m)]


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


def download_all(progress=None, should_stop=None, session=None) -> None:
    """Every file in ``missing()``, with one ``progress(done, total)`` across them."""
    files = missing()
    total, before = sum(m.size for m in files), 0
    for model in files:
        def report(done, _total, base=before):
            if progress is not None:
                progress(base + done, total)
        download(report, should_stop, session, model)
        before += model.size


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


def photo_list(photos, shown=None) -> str:
    """The user's photos as the model reads them, numbered from 1. ``shown``: the numbers
    still free to show (a long video's later parts leave out the ones used)."""
    lines = []
    for number, photo in enumerate(photos, 1):
        if photo.show and shown is not None and number not in shown:
            continue
        lines.append(f"{number}. {photo.about or 'a photo'} "
                     + ("(show it)" if photo.show else "(don't show it, only write about it)"))
    return "\n\nPhotos the user added:\n" + "\n".join(lines) if lines else ""


_PHOTO_RULE = ("- The user added photos, listed after the text: write about what they show. "
               "\"photo\" is the number of a photo marked (show it) when this scene's line is "
               "about what that photo shows, else 0. Each of those photos goes in exactly one "
               "scene. Never put a photo marked (don't show it) in a scene.\n")


def build_prompt(audience: draft.Audience, words: int, pictures: bool = False,
                 idea: str = "", facts: bool = False, part: str = "",
                 photos: bool = False) -> str:
    """The instructions for writing a script of about ``words`` spoken words.

    With ``pictures`` every scene also gets a description an image model can draw. With
    ``idea`` (the user asked for a video rather than giving a text) the script is invented:
    for a story, events that happen one after another, not a mood described. ``part``: this
    is one part of a long video (``part_note``), which replaces the rule about the hook.
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
        + part +
        "Rules:\n"
        + ("" if part else
           "- The first scene is a hook: a surprising or curious line that makes people keep "
           "watching.\n")
        + "- \"voice\" is one full spoken sentence of 8 to 18 words.\n"
        "- \"visual\" is 2 or 3 words for a stock video search: real things a camera can film "
        "that match the line. Name the video's subject when the shot shows the subject "
        "itself (\"coffee drying patio\", not \"water and sun\"), but not for other things "
        "in the line (\"food truck\", not \"coffee truck\"), and keep what the line shows, "
        "like rain, umbrellas or night. Never idea words like recap, summary, question or ending. Every "
        "scene gets different words. No brand names, no single words.\n"
        + ("- \"picture\" is one sentence describing a photo for this scene: who or what is "
           "in it, what they are doing and where. Things you can see, not ideas. No writing, "
           "logos or brand names in the picture.\n" if pictures else "")
        + (_PHOTO_RULE if photos else "")
        + ("- Use only the facts given after the request, with names and numbers exactly "
           "as written there. Leave out anything they don't say. No greetings, hashtags or "
           "emojis.\n" if facts else
           "- No greetings, hashtags or emojis.\n" if idea else
           "- Use only facts from the text. No greetings, hashtags or emojis.\n")
        + "Answer with JSON only."
    )


def script_schema(scenes: int, pictures: bool = False, photos=()) -> dict:
    """Fixing the number of scenes is what makes the length land (see MODEL's test).
    ``photos``: the numbers a scene may show (0, no photo, is always allowed)."""
    # The photo before the picture: last in a scene, a number left the model writing the
    # JSON's own brackets into the picture's words.
    extra = {"photo": {"enum": [0, *photos]}} if photos else {}
    if pictures:
        extra["picture"] = {"type": "string"}
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
# Ideas, not things: a search for "recap" finds anything at all (a night out, in a coffee
# video). They are dropped like the screen words above; the scene shows the subject instead.
_IDEAS = frozenset(
    "recap summary summarize question questions intro introduction outro ending conclusion "
    "overview hook lesson takeaway takeaways fact facts reason reasons step steps idea ideas "
    "tip tips thing things way ways part parts surprise video videos minute minutes scene "
    "scenes topic make".split())
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


def filmable(visual: str, turn: int = 0, topic: str = "") -> str:
    """``visual`` without the words no camera can film.

    When fewer than two words are left, the scene shows the video's ``topic`` ("coffee
    beans"); a tech video has none, and shows someone at a computer, ``turn`` picking
    which shot so the scenes of a video differ.
    """
    words = visual.split()
    kept = [w for w in words if w.lower() not in _UNFILMABLE and w.lower() not in _IDEAS]
    if len(kept) < len(words):  # "windows key v": the letter means nothing without the rest
        kept = [w for w in kept if len(w) > 1]
    if len(kept) == len(words):
        return visual
    if len(kept) >= 2:
        return " ".join(kept)
    if topic:
        return topic
    return _COMPUTER_SHOTS[turn % len(_COMPUTER_SHOTS)]


def topic_of(text: str) -> str:
    """The two words a text keeps coming back to that a camera can film, or "" (a tech
    video's words are mostly screen words: it keeps the computer shots)."""
    found = [w for w in draft.topic_words(text, 8)
             if w not in _UNFILMABLE and w not in _IDEAS]
    said = draft.words_in(text.lower())
    if not found or sum(w.rstrip("s") == found[0].rstrip("s") for w in said) < 3:
        return ""   # nothing the text keeps coming back to
    return " ".join(found[:2])


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


# --- long videos (4.9.2): an outline, then each part on its own -----------------------------
# One answer holds about 770 words (CONTEXT // 2 tokens), a 10 minute video about 1500: so a
# long video is planned as parts first, then written a part at a time.
LONG = 60.0          # seconds: longer targets are written in parts
PART_WORDS = 130     # spoken words in one part: about 13 scenes, well inside one answer
MAX_PARTS = 10


def part_count(words: int) -> int:
    return min(max(2, round(words / PART_WORDS)), MAX_PARTS)


def outline_schema(parts: int) -> dict:
    return {"type": "object", "required": ["parts"], "properties": {"parts": {
        "type": "array", "minItems": parts, "maxItems": parts,
        "items": {"type": "object", "required": ["title", "points"], "properties": {
            "title": {"type": "string", "maxLength": 40},
            "points": {"type": "array", "minItems": 2, "maxItems": 3,
                       "items": {"type": "string"}}}}}}}


def outline_prompt(audience: draft.Audience, parts: int, story: bool, facts: bool) -> str:
    order = ("a beginning, rising tension, a turn and an ending, like a story" if story else
             "a clear order, from what people need first to the details, ending with a "
             "conclusion")
    return (f"You plan narrated videos made from stock footage. Plan a video about the user's "
            f"text or idea in exactly {parts} parts that follow {order}. Give each part a "
            "short title of 2 to 5 words that names what it is about (never Hook, Intro or "
            "Part 1) and 2 or 3 points, one sentence each. No part repeats what another "
            "part says.\n"
            f"Audience and style: {audience.brief}\n"
            + ("Use only the facts given after the request.\n" if facts else "")
            + "Answer with JSON only.")


def part_note(at: int, parts: int, titles: list[str], points: list[str], before: str) -> str:
    """What one part of a long video is about, for build_prompt."""
    plan = "; ".join(f"{i + 1}. {title}" for i, title in enumerate(titles)) if titles else ""
    others = [t for i, t in enumerate(titles) if i != at]
    about = (f"Write only part {at + 1} of {parts}"
             + (f", \"{titles[at]}\", about: {' '.join(points)}" if titles else
                ", from the text you are given, and give it a short title of 2 to 5 words")
             + ".\n"
             + (f"- Stay on this part's points only: the other parts ({'; '.join(others)}) "
                "cover the rest.\n" if others else "")
             + "- No lines about the video itself, like what comes next or which step this "
               "is.\n")
    first = ("- The first scene is a hook: a surprising or curious line that makes people "
             "keep watching.\n" if at == 0 else
             f"- Carry on from the part before, which ended: \"{before}\" No hook, no "
             "greeting, and don't repeat earlier parts.\n")
    last = ("- The last scene ends the whole video with a strong closing line.\n"
            if at == parts - 1 else "- Don't end the video: more parts follow.\n")
    return (f"This is one part of a longer video{': ' + plan if plan else ''}.\n"
            + about + first + last)


def _gist(voice: str) -> frozenset:
    """The words that carry a line's meaning, to spot one said again in other words."""
    return frozenset(w for w in re.findall(r"[a-z']+", voice.lower()) if len(w) > 3)


def _repeats(voice: str, said) -> bool:
    """Whether ``voice`` says (almost) only what a line in ``said`` (gists) already said.
    Small models restate the hook or an earlier part when a part is written on its own."""
    gist = _gist(voice)
    return any(gist and len(gist & before) >= 0.75 * len(gist) for before in said)


def part_schema(scenes: int, pictures: bool = False, photos=()) -> dict:
    """script_schema with the part's title, which a part written from a text names itself."""
    schema = script_schema(scenes, pictures, photos)
    schema["properties"]["title"] = {"type": "string", "maxLength": 40}
    schema["required"] = ["title", "scenes"]
    return schema


def split_text(text: str, parts: int) -> list[str]:
    """``text`` cut into ``parts`` pieces of about the same length, between sentences."""
    sentences = draft.split_sentences(text) or [text]
    size = sum(len(s) for s in sentences) / parts
    pieces, current, done = [], [], 0
    for sentence in sentences:
        current.append(sentence)
        done += len(sentence)
        if done >= size * (len(pieces) + 1) and len(pieces) < parts - 1:
            pieces.append(" ".join(current))
            current = []
    pieces.append(" ".join(current))
    return [p for p in pieces if p.strip()]


def parse_scenes(content: str, topic: str = "", photos=()) -> list[draft.DraftScene]:
    """The model's answer as scenes. Repeated and empty lines are dropped. A scene's
    ``photo`` number becomes that shown photo's path, once per photo."""
    try:
        items = json.loads(content)["scenes"]
        if not isinstance(items, list):
            raise TypeError("scenes is not a list")
    except (ValueError, KeyError, TypeError) as exc:
        raise WriterError("The Smart writer gave an answer the app could not read.") from exc
    scenes, said, placed = [], set(), set()
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
        visual = filmable(asked, generic, topic)
        generic += visual in _COMPUTER_SHOTS
        number = item.get("photo")
        photo = ""
        if isinstance(number, int) and 1 <= number <= len(photos) and photos[number - 1].show \
                and number not in placed:
            photo = photos[number - 1].path
            placed.add(number)
        scenes.append(draft.DraftScene(visual, voice, len(scenes), picture, visual != asked,
                                       photo=photo))
    if not scenes:
        raise WriterError("The Smart writer gave an empty answer.")
    return scenes


# --- text for posting the video ---------------------------------------------------

_TEXT_FITS_ITEM = {"type": "object", "properties": {
    "main_subject": {"type": "string", "maxLength": 50},   # said first: it makes the answer think
    "fits": {"enum": ["yes", "maybe", "no"]}}, "required": ["main_subject", "fits"]}
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


def with_chapters(upload: dict, lines: list[str]) -> dict:
    """The upload text with a long video's chapter times under the description (YouTube
    makes chapters from lines like "0:00 Title")."""
    if not lines:
        return upload
    return dict(upload, description=upload["description"] + "\n\n" + "\n".join(lines))


def upload_file_text(upload: dict) -> str:
    return (f"Title:\n{upload['title']}\n\nDescription:\n{upload['description']}\n\n"
            f"Tags:\n{', '.join(upload['tags'])}\n")


# --- running the model -------------------------------------------------------------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def has_graphics(run=subprocess.run) -> bool | None:
    """Whether llama.cpp sees graphics it can run on: a dedicated card or the chip built into
    the processor (Intel, AMD), through Vulkan. None when it could not be asked."""
    exe = runtime_path()
    if exe is None:
        return None
    try:
        listed = run([exe, "--list-devices"], capture_output=True, timeout=60,
                     cwd=os.path.dirname(exe),
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    text = (listed.stdout or b"").decode("utf-8", "replace") + \
        (listed.stderr or b"").decode("utf-8", "replace")
    return bool(re.search(r"^\s*Vulkan\d+:", text, re.M))


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


_HOOK_PROMPT = (
    "You write the first line of a short Windows tech-tip video: one sentence that makes "
    "people keep watching, at most 12 words. Say what the viewer gets, plainly. No numbers, "
    "no percentages, no promises like 'double your FPS', no emojis. "
    "Answer as JSON: {\"hook\": \"...\"}."
)

_TIP_PROMPT = (
    "You plan a short Windows tech-tip video recorded on the viewer's own PC. You get the tip "
    "and the Windows page where it is done. The app opens that page itself, so it is already "
    "on screen: never tell them to click its name or go to it. If the page is the Settings "
    "search box, also give the few words to type in \"search_words\". Write 2 to 4 steps the "
    "person recording "
    "will do on that page: \"guide\" tells them what to click (short), \"voice\" is what the "
    "narrator says to viewers (one friendly sentence, at most 20 words). Read the tip "
    "literally: \"apps that start with Windows\" means apps that launch at startup. Only name "
    "buttons or options you are sure exist on that page; if unsure, say what to look for "
    "(\"find the switch for ...\") instead of inventing a name. Never use a search box: the "
    "page is already open. Usually one or two steps are enough: the click that changes the "
    "setting (a switch or an option), then a short closing line. When the page's buttons are "
    "listed, set each step's \"target\" to the button it clicks, exactly as listed, and use "
    "that name in the guide. Set \"changes_setting\" true only on the step where the setting "
    "itself is switched.")


def tip_schema(buttons=()) -> dict:
    """The answer's shape. With the page's real buttons, each step names one of them (or none)."""
    properties = {"guide": {"type": "string"}, "voice": {"type": "string"},
                  "changes_setting": {"type": "boolean"}}
    required = ["guide", "voice", "changes_setting"]
    if buttons:
        properties["target"] = {"enum": [*dict.fromkeys(buttons), ""]}
        required.append("target")
    step = {"type": "object", "properties": properties, "required": required}
    return {"type": "object", "properties": {
        "search_words": {"type": "string"},
        "steps": {"type": "array", "items": step, "minItems": 2, "maxItems": 4}},
        "required": ["search_words", "steps"]}


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

    def tip(self, text: str, place=None, buttons=()) -> dict:
        """The steps and voice lines for a Windows tip shown on ``place`` (None: Windows search).

        The place comes from the map's own search, which picked better than the model in
        tests (2026-10-21): the model only writes what happens on that page. ``buttons`` are
        the page's real controls (uiread); each step's target must be one of them.
        """
        where = (f"{place.name} (also known as: {', '.join(place.words[1:8])})" if place
                 else "the Settings search box, then the page Windows finds")
        from . import uiread

        buttons = list(buttons)[:60]
        names = [b.name for b in buttons]
        listing = ("\nButtons on the page: " + "; ".join(uiread.label(b) for b in buttons)
                   if buttons else "")
        content = self._ask(_TIP_PROMPT, [("user", f"Tip: {text}\nPage: {where}{listing}")],
                            tip_schema(names), 600, 0.3)
        try:
            return json.loads(content)
        except ValueError as exc:
            raise WriterError("The Smart writer's answer could not be read.") from exc

    def hook(self, text: str) -> str:
        """One short opening line for a tip video about ``text``, for a tip the map's own
        lines don't cover (hooks.py checks it before it is used)."""
        schema = {"type": "object", "properties": {"hook": {"type": "string", "maxLength": 90}},
                  "required": ["hook"]}
        content = self._ask(_HOOK_PROMPT, [("user", f"Tip: {text}")], schema, 80, 0.8)
        try:
            return str(json.loads(content).get("hook") or "")
        except ValueError as exc:
            raise WriterError("The Smart writer's answer could not be read.") from exc

    def _ask(self, system: str, turns: list[tuple[str, str]], schema: dict,
             max_tokens: int, temperature: float = 0.6) -> str:
        """One answer from the model, as text that fits ``schema``."""
        self.start()
        messages = [{"role": "system", "content": system}] if system else []
        messages +=[{"role": role, "content": content} for role, content in turns]
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

    def changed_picture(self, about: str, change: str) -> str:
        """One description of a photo as it looks after ``change`` (imagegen.redraw)."""
        schema = {"type": "object", "properties": {"picture": {"type": "string",
                                                               "maxLength": 600}},
                  "required": ["picture"]}
        content = self._ask(
            "You describe pictures for an image model. You get what a photo shows and a change "
            "the user wants. Answer with the description of the photo after the change: "
            "keep every detail the change doesn't touch word for word (the place, the sky, the "
            "light, the colours, the angle) and describe the changed part in concrete words. "
            "Answer as JSON.",
            [("user", f"Photo: {about}\nChange: {change}")], schema, 220, 0.3)
        try:
            return " ".join(str(json.loads(content)["picture"]).split())
        except (ValueError, KeyError, TypeError) as exc:
            raise WriterError("The Smart writer gave an answer the app could not read.") from exc

    def clips_text_fit(self, visual: str, texts: list[str]) -> list[str]:
        """"yes", "maybe" or "no" for each clip described by ``texts`` (the stock site's title
        or tags, or what the photo model saw) (clipcheck). One question a clip: asked about
        six at once the model mixes them up, and it is no faster (the answers are the cost)."""
        found = []
        for text in texts:
            content = self._ask(
                "You check stock video clips for a video. You get the scene's search words and "
                "a clip's description: the stock site's title or tags, or what a picture model "
                "saw. Tags list everything in the clip, so first decide what the clip's MAIN "
                "subject is, then compare it with the MAIN subject of the search words. A cup "
                "of coffee is not a coffee plant; a pie is not coffee fruit; roast pork is not "
                "a thermometer. Answer \"no\" when the main subjects differ, \"maybe\" when they "
                "are related, \"yes\" when the clip clearly shows the search words. Answer as "
                "JSON.",
                [("user", f"Search words: {visual}\nClip: {' '.join(text.split())[:200]}")],
                _TEXT_FITS_ITEM, 60, 0.0)
            try:
                found.append(str(json.loads(content)["fits"]))
            except (ValueError, KeyError, TypeError) as exc:
                raise WriterError("The Smart writer gave an answer the app could not read.") \
                    from exc
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
              cards: bool = True, ai: str = draft.AI_NONE, web: bool = False,
              progress=None, photos=()) -> draft.Draft:
        """A script for ``text``, rewritten for the audience and the length. With ``web``, an
        idea that asks for facts is first looked up on Wikipedia (vidgen.webfacts).
        ``progress(message, fraction)`` hears how a long video's parts are going.
        ``photos`` (draft.Photo): the user's reference photos; the shown ones are put in
        the scenes about them."""
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
        facts, sources, source = "", [], "Smart writer"
        if web and is_idea(text) and webfacts.wants_facts(text):
            facts, sources = webfacts.facts(text)
            source = (f"Smart writer, with facts from Wikipedia ({', '.join(sources)})" if facts
                      else "Smart writer (Wikipedia could not be reached, so from what it "
                           "knows: check the facts)")
        if target is not None and target > LONG:
            scenes, note = self._write_long(text, audience, words, target, persona, pictures,
                                            facts, progress or (lambda *_a: None), photos)
            scenes = draft.place_photos(scenes, photos)
            script = draft.to_script(scenes, cards, ai)
            seconds = sum(pacing.estimate_voice(s.voice, persona.rate, persona.pause_scale,
                                                persona.enhanced) + padding for s in scenes)
            return draft.Draft(script, scenes, seconds, length, persona.id, audience.aspect,
                               source=source + note)
        scenes = self._write_once(text, audience, words, pictures, facts, photos)
        if target is not None and not _fits(scenes, target, persona):
            # Too long. Ask once more for less, then cut what still does not fit.
            spoken = sum(len(draft.words_in(s.voice)) for s in scenes)
            fewer = max(8, round(words * words / max(spoken, words)))
            self._stopping()
            scenes = self._write_once(text, audience, fewer, pictures, facts, photos)
            if not _fits(scenes, target, persona):
                scenes = _trim(scenes, target, persona)
        scenes = draft.place_photos(scenes, photos)
        script = draft.to_script(scenes, cards, ai)
        seconds = sum(pacing.estimate_voice(s.voice, persona.rate, persona.pause_scale,
                                            persona.enhanced) + padding for s in scenes)
        return draft.Draft(script, scenes, seconds, length, persona.id, audience.aspect,
                           source=source)

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

    def _write_once(self, text, audience, words, pictures=False, facts="", photos=()):
        room = _answer_tokens(words, pictures)
        idea = text if is_idea(text) else ""
        topic = topic_of(text)
        if facts:   # the request, then what to take its facts from
            text = f"{text}\n\nFacts to use:\n{facts}"
        text += photo_list(photos)
        _check_fits(text, room)
        example = _EXAMPLE_OUT_PICTURES if pictures else _EXAMPLE_OUT
        shown = [n for n, p in enumerate(photos, 1) if p.show]
        content = self._ask(
            build_prompt(audience, words, pictures, idea, bool(facts), photos=bool(photos)),
            [("user", _EXAMPLE_IN), ("assistant", example), ("user", text)],
            script_schema(scene_count(words), pictures, shown), room)
        return parse_scenes(content, topic, photos)

    def _write_long(self, text, audience, words, target, persona, pictures, facts, progress,
                    photos=()):
        """A long video: an outline of parts, then each part in its own answer. Returns
        (scenes, a note for the source). Cancel keeps the parts already written."""
        parts = part_count(words)
        each = max(40, round(words / parts))
        idea = text if is_idea(text) else ""
        asked = (f"{text}\n\nFacts to use:\n{facts}" if facts else text) + photo_list(photos)
        topic = topic_of(text)
        free = [n for n, p in enumerate(photos, 1) if p.show]   # shown photos not used yet
        outline_room = parts * 70 + 120
        titles, points, pieces = [], [[] for _ in range(parts)], None
        if len(asked) / CHARS_PER_TOKEN <= CONTEXT - outline_room - 700:
            progress("Writing the outline...", 0.0)
            content = self._ask(outline_prompt(audience, parts, bool(_STORY.search(idea)),
                                               bool(facts)),
                                [("user", asked)], outline_schema(parts), outline_room, 0.7)
            try:
                plan = json.loads(content)["parts"]
                titles = [" ".join(str(p["title"]).split()) for p in plan]
                points = [[" ".join(str(x).split()) for x in p["points"]] for p in plan]
            except (ValueError, KeyError, TypeError) as exc:
                raise WriterError("The Smart writer gave an answer the app could not read.") \
                    from exc
        room = _answer_tokens(each, pictures) + 30
        if len(asked) / CHARS_PER_TOKEN > CONTEXT - room - 900:
            # Too long to give every part: each part is written from its own piece of it.
            pieces = split_text(asked, parts)
            parts = len(pieces)
            if len(titles) != parts:
                titles, points = [], [[] for _ in range(parts)]
        example = _EXAMPLE_OUT_PICTURES if pictures else _EXAMPLE_OUT
        scenes, part_of, said, done = [], [], set(), 0
        for at in range(parts):
            if self.should_stop():   # Cancel between parts: keep what is written
                if not done:
                    self._stopping()
                break
            progress(f"Writing part {at + 1} of {parts}...", (at + 1) / (parts + 1))
            given = pieces[at] if pieces else asked
            if photos:   # every part hears about the photos, but only the free ones to show
                given = given.split("\n\nPhotos the user added:")[0] + photo_list(photos, free)
            try:
                _check_fits(given, room)
                content = self._ask(
                    build_prompt(audience, each, pictures, idea, bool(facts),
                                 part_note(at, parts, titles, points[at],
                                           scenes[-1].voice if scenes else ""),
                                 photos=bool(photos)),
                    [("user", _EXAMPLE_IN), ("assistant", example), ("user", given)],
                    part_schema(scene_count(each), pictures, free), room)
            except Stopped:
                if not done:
                    raise
                break
            used = {photos[n - 1].path for n, p in enumerate(photos, 1) if p.show} - \
                {photos[n - 1].path for n in free}
            found = [dataclasses.replace(s, photo="") if s.photo in used else s
                     for s in parse_scenes(content, topic, photos) if not _repeats(s.voice, said)]
            free = [n for n in free if photos[n - 1].path not in {s.photo for s in found}]
            if not found:
                continue
            try:
                title = titles[at] if titles else " ".join(str(json.loads(content)["title"]).split())
            except (ValueError, KeyError, TypeError):
                title = ""
            found[0] = dataclasses.replace(found[0], chapter=title or f"Part {at + 1}")
            said.update(_gist(s.voice) for s in found)
            scenes += found
            part_of += [at] * len(found)
            done += 1
        if not scenes:
            raise WriterError("The Smart writer gave an empty answer.")
        if not _fits(scenes, target, persona):
            kept = _trim(scenes, target, persona)
            # A part whose first scene was cut starts at its next scene still there.
            chapters = {part_of[i]: s.chapter for i, s in enumerate(scenes) if s.chapter}
            where = {id(scene): part_of[i] for i, scene in enumerate(scenes)}
            seen, marked = set(), []
            for scene in kept:
                at = where[id(scene)]
                marked.append(dataclasses.replace(
                    scene, chapter=chapters.get(at, "") if at not in seen else ""))
                seen.add(at)
            scenes = marked
        progress("Done.", 1.0)
        note = "" if done == parts else f" (stopped after part {done} of {parts})"
        return scenes, note


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


def changed_picture(about: str, change: str, should_stop=None, gpu: bool = False) -> str:
    """Load the model, describe the changed photo, stop the model (before the picture
    program needs the memory)."""
    with Session(should_stop, gpu=gpu) as session:
        return session.changed_picture(about, change)


def suggest(text, last=None, should_stop=None, padding: float = 0.3, gpu: bool = False):
    """Load the model, ask who the text is for, stop the model."""
    with Session(should_stop, gpu=gpu) as session:
        return session.suggest(text, last, padding)
