"""Tutorial videos (5.4): the user's own steps in any app, made into a sleek tutorial.

The look and motion are the house style of the hand-made reference
(tests/fixtures/tutorial_ref, HOUSE_STYLE.md). A video is a **timeline** (a dict, saved as JSON):
the screens, the camera, the cursor, the clicks, the labels and the steps, all in seconds and
in the recorded window's pixels. scene.html draws any timeline; the promo engine
(promo/browser.py) turns it into an MP4, with the WebView2 fallback when no browser is found.

    tutorial.make(timeline, images_dir, "out.mp4")
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

from ..promo import browser
from ..promo.browser import BrowserError

__all__ = ["BrowserError", "FPS", "SIZE", "STYLE", "TutorialError", "build_page", "check",
           "load", "make"]

FPS = 30
SIZE = (1920, 1080)
STYLE = {   # the reference's motion (HOUSE_STYLE.md); a timeline may override any of them
    "crossfade": 0.18,   # seconds a screen change takes
    "press": 0.12,       # seconds the cursor stays pressed at a click
    "ring": 0.6,         # seconds a click ring lasts
    "spotlight": 0.26,   # how dark the edges get while a label shows
}
LOOK = {"accent": "#E8763A", "on_accent": "#1A0D05", "font": "Segoe UI"}
NEEDED = ("length", "window", "camera", "cursor", "steps", "title", "recap")


class TutorialError(Exception):
    """The timeline can't be drawn as it is. The message is for the user."""


def here(*parts) -> str:
    if getattr(sys, "frozen", False):   # the built app: the files sit in the bundle
        base = os.path.join(getattr(sys, "_MEIPASS", ""), "vidgen", "tutorial")
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, *parts)


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check(timeline: dict, images_dir: str) -> dict:
    """The timeline with defaults filled in, or TutorialError naming what is wrong."""
    missing = [key for key in NEEDED if key not in timeline]
    if missing:
        raise TutorialError("The tutorial is missing: " + ", ".join(missing) + ".")
    if not (timeline.get("screens") or timeline.get("frames")) or not timeline["steps"]:
        raise TutorialError("A tutorial needs its recording (or pictures) and one step.")
    for name in ("camera", "cursor"):
        times = [key[0] for key in timeline[name]]
        if not times or times != sorted(times):
            raise TutorialError(f"The {name} moves must be in time order.")
    for screen in timeline.get("screens", []):
        path = os.path.join(images_dir, screen["image"])
        if os.path.basename(screen["image"]) != screen["image"] or not os.path.isfile(path):
            raise TutorialError(f"A screen picture is missing: {screen['image']}")
    filled = dict(timeline)
    filled["style"] = {**STYLE, **timeline.get("style", {})}
    filled["look"] = {**LOOK, **timeline.get("look", {})}
    filled.setdefault("stage", {"size": list(SIZE), "centre": [960, 520]})
    filled["stage"].setdefault("size", list(SIZE))
    filled.setdefault("clicks", [])
    filled.setdefault("drags", [])
    filled.setdefault("callouts", [])
    filled.setdefault("main", [timeline["title"]["until"] - 0.1, timeline["recap"]["at"]])
    return filled


def build_page(timeline: dict, images_dir: str, folder: str) -> str:
    """Write the page, its timeline and its screens into ``folder``; the page's path."""
    filled = check(timeline, images_dir)
    shutil.copy(here("scene.html"), os.path.join(folder, "scene.html"))
    for name in {screen["image"] for screen in filled.get("screens", [])}:
        shutil.copy(os.path.join(images_dir, name), os.path.join(folder, name))
    with open(os.path.join(folder, "timeline.js"), "w", encoding="utf-8") as f:
        # </ cannot end the script early: the JSON is data, never markup
        f.write("window.TIMELINE = " + json.dumps(filled).replace("</", "<\\/") + ";\n")
    return os.path.join(folder, "scene.html")


def make(timeline: dict, images_dir: str, out_path: str, should_stop=None, progress=None,
         gpu: bool = False, browser_path: str | None = None, recording: str = "",
         log: dict | None = None, voice: str | None = None, music: str | None = None,
         sfx_folder: str | None = None, say=None) -> str:
    """Draw the tutorial into an MP4. With ``recording`` and its ``log`` the recording itself is
    shown, cut to the tutorial's clock (frames.py); else the timeline's still ``screens`` from
    ``images_dir``. Sound (sound.py): ``voice`` (the app's voice to read the steps, or a
    ``say(text) -> wav`` function), ``music`` (a track) and ``sfx_folder`` (the clicks); none of
    them: a silent video. ``progress(done, total, phase)``: "prepare" (the recording's frames),
    "draw", then "sound" (0 of 1, 1 of 1). Raises TutorialError or BrowserError."""
    folder = tempfile.mkdtemp(prefix="aivs-tutorial-")
    try:
        if recording:
            from . import frames
            timeline = frames.smooth_drawing(dict(timeline), recording, folder)
            if "look" not in timeline:   # the recorded app's own colours (look.py)
                from PIL import Image

                from . import look
                from .record import frame_at
                first = frame_at(recording, 0.2, os.path.join(folder, "first.png"))
                with Image.open(first) as picture:
                    timeline["look"] = look.from_frame(picture, (log or {}).get("window"))
            timeline["frames"] = frames.cut(
                recording, log, timeline, folder, should_stop,
                (lambda done, total: progress(done, total, "prepare")) if progress else None)
        page = build_page(timeline, images_dir, folder)
        with_sound = bool(voice or say or music or sfx_folder)
        picture = os.path.join(folder, "picture.mp4") if with_sound else out_path
        size = tuple((timeline.get("stage") or {}).get("size", SIZE))
        browser.record(page, size, float(timeline["length"]), picture, FPS, gpu, should_stop,
                       (lambda done, total: progress(done, total, "draw")) if progress else None,
                       browser_path)
        if not with_sound:
            return out_path
        from ..promo.sound import mux
        from . import sound

        if progress:
            progress(0, 1, "sound")
        lines_dir = os.path.join(folder, "voice")
        os.makedirs(lines_dir, exist_ok=True)
        if say is None and voice:
            say = sound.app_voice(voice, lines_dir)
        wav = sound.mix(timeline, os.path.join(folder, "sound.wav"), say, music, sfx_folder)
        mux(picture, wav, out_path)
        if progress:
            progress(1, 1, "sound")
        return out_path
    finally:
        shutil.rmtree(folder, ignore_errors=True)
