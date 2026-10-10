"""Looking at the clips before rendering, and choosing between them.

The window shows one row per scene: a still of the clip the app would use, and
a "Try another" button that steps through the alternatives. This module is the
part of that with no window in it: which rows there are, which clip each one
is on, and what the render should be told.

The stills come from the stock services' own preview pictures, so stepping
through clips downloads a few kilobytes, not the clips themselves.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import requests

from .script import parse_script

PER_SCENE = 6  # clips offered for each scene
EXCERPT = 70  # characters of the voice line shown beside the still
THUMB_TIMEOUT = (10, 20)


@dataclass(frozen=True)
class Row:
    index: int  # the scene's position in the script, from 0
    line: int  # where it starts in the script text
    visual: str  # the search words, or the local file's path
    voice: str  # the start of what is said over it
    is_local: bool
    is_ai: bool = False  # an AI picture: nothing to search for, made with the video

    @property
    def is_stock(self) -> bool:
        return not self.is_local and not self.is_ai


def plan_preview(script_text: str) -> list[Row]:
    """One row per scene. Raises ScriptError for a script that doesn't parse."""
    rows = []
    for index, scene in enumerate(parse_script(script_text)):
        voice = " ".join((scene.voice or "").split())
        if len(voice) > EXCERPT:
            voice = voice[:EXCERPT - 1].rstrip() + "…"
        shown = (scene.local_path if scene.is_local
                 else scene.ai_prompt if scene.is_ai else scene.visual)
        rows.append(Row(index, scene.line, shown, voice, scene.is_local, scene.is_ai))
    return rows


def _normal(text: str) -> str:
    return "\n".join(line.strip() for line in (text or "").strip().splitlines())


class ScenePicker:
    """Which clip each stock scene is on, out of the ones found for it.

    Choices belong to one script at one frame size: change either and the
    clips no longer correspond, so ``matches`` says to pick afresh.
    """

    def __init__(self, script_text: str, frame: str = ""):
        self.script = _normal(script_text)
        self.frame = frame  # e.g. "9:16 1080p"
        self.options: dict[int, list] = {}
        self.position: dict[int, int] = {}

    def set_options(self, index: int, candidates) -> None:
        self.options[index] = list(candidates)
        self.position[index] = 0

    def count(self, index: int) -> int:
        return len(self.options.get(index, ()))

    def current(self, index: int):
        clips = self.options.get(index)
        return clips[self.position[index]] if clips else None

    def next(self, index: int):
        """Step to the following clip, wrapping round; returns it (None if there are none)."""
        clips = self.options.get(index)
        if not clips:
            return None
        self.position[index] = (self.position[index] + 1) % len(clips)
        return clips[self.position[index]]

    def label(self, index: int) -> str:
        total = self.count(index)
        return f"{self.position[index] + 1} of {total}" if total else ""

    def choices(self) -> dict[int, list]:
        """Per scene, the clips to try in order: the chosen one, then those after it."""
        ordered = {}
        for index, clips in self.options.items():
            if clips:
                at = self.position[index]
                ordered[index] = clips[at:] + clips[:at]
        return ordered

    def matches(self, script_text: str, frame: str = "") -> bool:
        return _normal(script_text) == self.script and frame == self.frame


def find_options(search, rows, picker: ScenePicker, on_scene=None, stop=lambda: False) -> None:
    """Fill ``picker`` with clips for every stock row, calling ``on_scene(row)`` after each.

    The first clip of each scene is marked used, so two scenes with similar
    search words don't open on the same clip.
    """
    for row in rows:
        if stop():
            return
        if row.is_stock:
            found = search.find(row.visual, PER_SCENE)
            picker.set_options(row.index, found)
            if found:
                search.mark_used(found[0])
        if on_scene:
            on_scene(row)


def thumb_path(candidate, folder: str) -> str:
    name = re.sub(r"[^\w.-]", "_", candidate.key)
    return os.path.join(folder, f"{name}.jpg")


def fetch_thumb(candidate, folder: str, session=None) -> str | None:
    """The clip's still as a file in ``folder``; None if it has none or it can't be fetched."""
    if not getattr(candidate, "thumb", ""):
        return None
    path = thumb_path(candidate, folder)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    try:
        os.makedirs(folder, exist_ok=True)
        response = (session or requests).get(candidate.thumb, timeout=THUMB_TIMEOUT)
        if response.status_code != 200 or not response.content:
            return None
        with open(path, "wb") as fh:
            fh.write(response.content)
        return path
    except (requests.RequestException, OSError):
        return None
