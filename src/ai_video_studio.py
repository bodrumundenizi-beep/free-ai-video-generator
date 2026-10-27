# -*- coding: utf-8 -*-
"""
AI Video Studio - Fluent Edition
================================
The window: a Windows 11 / "Wintoys"-style shell around the video engine.

This file is the interface - settings, pages, window effects. The engine
(script parser, footage search, motion, music, rendering) lives in the
``vidgen`` package beside it.

  * Fluent-style shell: near-black sidebar, lighter content area, rounded cards.
  * Pages: Home / Create / Log / Feedback / Settings.
  * Settings are persisted to a small JSON file, including the API keys, which
    are never in the source.
  * Mica / acrylic translucency + dark title bar on Windows 11.
  * Thread-safe UI: the render thread never touches a widget.  It pushes
    messages onto a queue that the main thread drains from a window.after()
    loop.

Requirements
------------
    pip install -r requirements.txt

Run
---
    python src/ai_video_studio.py
"""

from __future__ import annotations

import collections
import atexit
import dataclasses
import ctypes
import itertools
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import traceback
import webbrowser
from tkinter import filedialog
from tkinter import font as tkfont


# =============================================================================
#  SECTION 0 - FROZEN-BUILD BOOTSTRAP
# =============================================================================
# Everything in this section has to run before CustomTkinter or MoviePy are
# imported.  It is a no-op when running from source.

IS_FROZEN = getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")
BUNDLE_DIR = sys._MEIPASS if IS_FROZEN else os.path.dirname(os.path.abspath(__file__))

# The engine package lives beside this file. Python adds the script's folder
# to sys.path when it is run directly, but not when it is loaded some other way
# (tests, an IDE runner), so make sure.
if not IS_FROZEN and BUNDLE_DIR not in sys.path:
    sys.path.insert(0, BUNDLE_DIR)

# A windowed (--noconsole) build has no stdout or stderr at all: they are None.
# Anything that writes to them - the import guard below, or tqdm inside MoviePy's
# progress logger - would raise AttributeError on None and kill the render.
# Give them somewhere harmless to go.
if IS_FROZEN:
    for _stream in ("stdout", "stderr"):
        if getattr(sys, _stream, None) is None:
            try:
                setattr(sys, _stream, open(os.devnull, "w", encoding="utf-8"))
            except OSError:
                pass


def asset_path(name: str):
    """Locate a bundled asset in both layouts, or None.

    Frozen: <_MEIPASS>/assets/<name>.  From source the script lives in src/, so
    the repo's assets/ folder is one level up.
    """
    for base in (BUNDLE_DIR, os.path.dirname(BUNDLE_DIR)):
        candidate = os.path.join(base, "assets", name)
        if os.path.exists(candidate):
            return candidate
    return None


def _bundled_ffmpeg():
    """The ffmpeg PyInstaller collected via hook-imageio_ffmpeg, if present."""
    binaries = os.path.join(BUNDLE_DIR, "imageio_ffmpeg", "binaries")
    try:
        names = os.listdir(binaries)
    except OSError:
        return None
    for name in names:
        lowered = name.lower()
        if lowered.startswith("ffmpeg") and lowered.endswith(".exe"):
            return os.path.join(binaries, name)
    return None


def configure_ffmpeg():
    """Point imageio-ffmpeg, and so MoviePy, at the bundled ffmpeg.

    imageio_ffmpeg.get_ffmpeg_exe() reads IMAGEIO_FFMPEG_EXE first and trusts it
    without probing.  Deliberately not MoviePy's own FFMPEG_BINARY: that path is
    validated by spawning the binary at import time.

    Returns None when running from source, where imageio auto-detects as before.
    """
    if os.environ.get("IMAGEIO_FFMPEG_EXE"):
        return os.environ["IMAGEIO_FFMPEG_EXE"]
    exe = _bundled_ffmpeg()
    if exe:
        os.environ["IMAGEIO_FFMPEG_EXE"] = exe
    return exe


FFMPEG_EXE = configure_ffmpeg()


try:
    import customtkinter as ctk
except ImportError:  # pragma: no cover - startup guard
    sys.stderr.write(
        "\nAI Video Studio needs CustomTkinter for its interface.\n"
        "Install it with:\n\n    pip install customtkinter\n\n"
    )
    raise SystemExit(1)

# The engine. Only light modules load here; MoviePy waits for the first render
# (or the warm-up thread), so the window still appears instantly.
from vidgen.formats import RATIO_OPTIONS, RESOLUTION_OPTIONS, resolve_target  # noqa: E402
from vidgen.render import (  # noqa: E402
    UiBridge, next_free_path, render_worker, sweep_old_work_dirs,
)
from vidgen.script import (  # noqa: E402
    ScriptError, count_scenes, looks_like_plain_text, parse_script,
)
from vidgen import voices  # noqa: E402
from vidgen.render import TEMP_ROOT  # noqa: E402
from vidgen import (  # noqa: E402
    cache, captions, diagnostics, draft, footage, pacing, paths, preview, projects, scenes,
    updates, writer,
)
from vidgen import board as boards  # noqa: E402
from vidgen import (  # noqa: E402
    animate, drawn, drive, hooks, imagegen, localvoice, mastering, myvoice, putback, screen,
    tiplist, tips, uiread, voicepack, winmap,
)
from vidgen.voice import generate_voiceover  # noqa: E402


APP_NAME = "AI Video Studio"
APP_VERSION = "4.7.0"
# Must match AppUserModelID in packaging/installer.iss, or a pinned taskbar
# shortcut will not group with the running window.
APP_MODEL_ID = "AIVideoStudio.Desktop.3"


# =============================================================================
#  SECTION 1 - SETTINGS (persisted between launches)
# =============================================================================

SETTINGS_DIR = os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~"), "AIVideoStudio"
)
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")


def default_output_dir() -> str:
    """The user's Videos folder."""
    return paths.known_folder(paths.CSIDL_VIDEOS, "Videos")


# Tracks dropped in here appear in Settings > Background music. Documents rather
# than %APPDATA%, because people need to be able to find it.
MUSIC_DIR = os.path.join(paths.known_folder(paths.CSIDL_DOCUMENTS, "Documents"),
                         "AI Video Studio", "Music")
MUSIC_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")
# Kept between renders (vidgen.cache): search results, clips, voices. Clear cache empties it.
CACHE_ROOT = os.path.join(SETTINGS_DIR, "cache")
SEARCH_CACHE_DIR = os.path.join(CACHE_ROOT, "search")
# Stays on this PC. Only a report the user sends themselves ever quotes from it.
LOG_DIR = os.path.join(SETTINGS_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "app.log")
PADDING_OPTIONS = {"0s": 0.0, "0.25s": 0.25, "0.5s": 0.5, "1s": 1.0}
DEFAULT_PADDING = 0.25


NO_TEMPLATE = "None"
MAX_BATCH = 20
UNDO_STEPS = 50
AUTO_TRACK = "auto"   # music_path meaning: a bundled track that fits the video
AUTO_LABEL = "Auto (fits the video)"


def bundled_tracks() -> list:
    folder = asset_path("music")
    if not folder or not os.path.isdir(folder):
        return []
    return [os.path.join(folder, n) for n in os.listdir(folder) if n.lower().endswith(MUSIC_EXTS)]


def music_tracks() -> dict:
    """{name: path} for every track in the user's Music folder and assets/music."""
    tracks = {}
    for folder in (MUSIC_DIR, asset_path("music")):
        if not folder or not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder), key=str.lower):
            if name.lower().endswith(MUSIC_EXTS):
                tracks.setdefault(name, os.path.join(folder, name))
    return tracks


DEFAULT_SCRIPT = """Visual: hard drive
Voice: Your PC could be hoarding gigabytes of junk you will never use.

Visual: computer data
Voice: Temporary files and old cache pile up silently, slowing your whole system down.

Visual: keyboard typing
Voice: Here is the fix. Press the Windows key, search for Disk Cleanup, and open it.

Visual: mouse click
Voice: Select your main drive, then click Clean up system files."""

# Dropdown labels, grouped by persona; settings.json stores the voice ID.
VOICE_OPTIONS = voices.labels()

PREVIEW_TEXT = "This is how your video will sound."
PREVIEW_DIR = os.path.join(TEMP_ROOT, "preview")
THUMB_DIR = os.path.join(PREVIEW_DIR, "thumbs")   # stills for the scene preview
VERSION_OPTIONS = ("1", "2")

# (settings key, allowed values, default) for each caption choice.
CAPTION_SETTINGS = (
    ("caption_style", captions.STYLES, captions.DEFAULT_STYLE),
    ("caption_size", tuple(captions.SIZES), captions.DEFAULT_SIZE),
    ("caption_color", tuple(captions.COLORS), captions.DEFAULT_COLOR),
    ("caption_position", tuple(captions.POSITIONS), captions.DEFAULT_POSITION),
)

# Who the last "New from text" script was written for: (setting, allowed, default).
AUDIENCE_SETTINGS = (
    ("audience_content", tuple(draft.CONTENT), draft.DEFAULT_CONTENT),
    ("audience_age", tuple(draft.AGES), draft.DEFAULT_AGE),
    ("audience_platform", tuple(draft.PLATFORMS), draft.DEFAULT_PLATFORM),
)


# Which scenes New from text gives an AI picture, once AI images are downloaded.
AI_IMAGES_FOR = ("Scenes stock can't show", "Every scene", "Only ai: lines")
# What each choice in the AI images tab means, shown under the row when it is picked.
AI_FOR_HINTS = {
    AI_IMAGES_FOR[0]: "Most scenes keep their stock clips. A scene gets an AI picture only when "
                      "it is about something on a screen (an app, a menu, a setting), which no "
                      "stock clip can show. Fastest: usually 2 or 3 pictures a video, about a "
                      "minute more. In the Storyboard you can switch any other scene to an AI "
                      "picture with one click. With no stock footage key, every scene gets one.",
    AI_IMAGES_FOR[1]: "Every scene gets an AI picture and no stock footage is used, so the whole "
                      "video has one matching look. Best for stories and tech tips. Adds about "
                      "20 seconds for each scene (around 3 minutes for a 30-second video).",
    AI_IMAGES_FOR[2]: "New from text never adds AI pictures. You get one only in the scenes "
                      "where you write it yourself, like  Visual: ai: a robot holding a clipboard",
}
AI_LOOK_HINTS = {
    "Realistic photo": "Looks like a real photograph, with natural light. Best for stories, "
                       "people, places and everyday scenes.",
    "3D render": "Looks like clean, colourful 3D animation, the style of a product advert. "
                 "Best for tech tips, gadgets and robots.",
    "Illustration": "Looks like a flat drawing with bold colours. Best for explaining ideas, "
                    "money topics and videos for children.",
}
AI_DEVICES = {"Auto": "", "Graphics card": imagegen.GPU, "Processor": imagegen.CPU}
AI_DEVICE_HINTS = {
    "Auto": "Tries the graphics card first. If it can't make the picture, the processor takes "
            "over, and the app remembers that for next time.",
    "Graphics card": "Only the graphics card. If it can't make a picture, that scene uses a "
                     "stock clip instead; the processor is never used.",
    "Processor": "Only the processor. Slow (minutes a picture on a laptop) but works on any PC. "
                 "Pictures are made at the Fast size.",
}
MOVING_HINTS = {
    "Off": "Every AI picture is a still, with a slow zoom.",
    "First AI scene": "The first AI picture of each video moves: the hook, where it matters "
                      "most. About 1.5 min more per video on an RTX 4060 (measured).",
    "Every AI scene": "Every AI picture moves. About 1.5 min more for each one on an RTX 4060.",
}
AI_THREAD_OPTIONS = ("Auto", "2", "4", "6", "8", "10", "12", "14", "16", "20", "24", "32")
SLOW_AI_SCENES = 2       # AI pictures New from text makes by default on a slow PC
SLOW_FAST_SECONDS = 40   # a Fast picture slower than this makes a PC "slow" for AI pictures
AI_SPEED_HINTS = {
    imagegen.FAST: "Smallest pictures, enlarged to the video's size: a little softer, but the "
                   "quickest by far. Best for PCs without a graphics card.",
    imagegen.BALANCED: "Middle size. Sharp enough for Shorts, about twice as fast as Best.",
    imagegen.BEST: "The full picture size. The sharpest, and the slowest. On the processor the "
                   "app still uses Fast: Best would take many minutes a picture.",
}
AI_IMAGES_WARNING = (
    "Needs a graphics card with about 8 GB of video memory. Around 20 seconds per picture "
    "on an RTX 4060. If the graphics card can't do it, the app uses the processor instead, "
    "with a smaller picture that can take several minutes.")


def default_settings() -> dict:
    return {
        "api_key": "",
        "pixabay_key": "",
        "padding": DEFAULT_PADDING,
        "music_enabled": True,     # new users: the bundled music, picked to fit
        "ask_save": True,
        "welcome_seen": False,
        "tour_seen": False,
        "whats_new_seen": "",      # the last version whose What's new was shown
        "whats_new_off": False,    # "Don't show again" was ticked
        "check_updates": True,
        "versions": 1,
        "target_length": pacing.DEFAULT_TARGET,
        "audience_content": draft.DEFAULT_CONTENT,
        "audience_age": draft.DEFAULT_AGE,
        "audience_platform": draft.DEFAULT_PLATFORM,
        "writer_mode": "Smart writer",
        "writer_gpu": False,
        "voice_online": False,
        "auto_cards": True,
        "ai_images_for": AI_IMAGES_FOR[0],
        "ai_images_look": imagegen.DEFAULT_LOOK,
        "ai_images_backend": "",   # "cpu" once the graphics card has failed on this PC
        "ai_device": "Auto",       # where AI pictures are made: Auto, Graphics card, Processor
        "project": "",             # the project the script belongs to (vidgen.projects)
        "intro_on": False,         # a channel-name card before the video
        "intro_text": "",
        "outro_on": False,         # an end card after it
        "outro_text": "Subscribe for more",
        "sound_effects": True,     # a whoosh on each cut, a pop as a card appears
        "smart_motion": True,      # cuts on the music's beats, framing toward the detail
        "animate": "Off",          # which AI pictures become moving clips (vidgen.animate)
        "web_facts": True,         # fact ideas are looked up on Wikipedia first (webfacts)
        "style": "Normal",         # Normal footage, or drawn on screen (vidgen.drawn)
        "quick_sync": False,       # encode on an Intel graphics chip when it works (render)
        "ai_images_speed": "",     # Fast / Balanced / Best; "" = chosen for this PC
        "ai_threads": "Auto",      # processor threads for AI pictures, or a number
        "ai_threads_best": "",     # "threads:affinity" the Test speed button found fastest
        "ai_fast_seconds": 0.0,    # a measured picture's time, scaled to the Fast setting
        "ai_gpu": "",              # "dedicated", "integrated" or "none", once detected
        "key_tip": True,           # after a keyless video: mention that a free key exists
        "music_path": AUTO_TRACK,
        "output_path": os.path.join(default_output_dir(), "final_video.mp4"),
        "aspect": "9:16",
        "resolution": "1080p",
        "voice": voices.DEFAULT_ID,
        "captions": True,
        "caption_style": captions.DEFAULT_STYLE,
        "caption_size": captions.DEFAULT_SIZE,
        "caption_color": captions.DEFAULT_COLOR,
        "caption_position": captions.DEFAULT_POSITION,
        "theme": "dark",
        "translucent": True,
        "script": DEFAULT_SCRIPT,
    }


def load_settings() -> dict:
    """Read settings.json, falling back to defaults for anything missing."""
    data = default_settings()
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
            stored = json.load(fh)
        if isinstance(stored, dict):
            for key in data:
                if key in stored and stored[key] is not None:
                    data[key] = stored[key]
            # Settings from before the tour existed: this is an update, not a first run.
            if "tour_seen" not in stored:
                data["tour_seen"] = True
    except (OSError, ValueError):
        pass

    # Never hardcode the key: allow an environment variable as a second source.
    if not str(data.get("api_key", "")).strip():
        data["api_key"] = os.environ.get("PEXELS_API_KEY", "")
    if not str(data.get("pixabay_key", "")).strip():
        data["pixabay_key"] = os.environ.get("PIXABAY_API_KEY", "")

    try:
        padding = float(data["padding"])
    except (TypeError, ValueError):
        padding = DEFAULT_PADDING
    data["padding"] = padding if padding in PADDING_OPTIONS.values() else DEFAULT_PADDING
    data["music_enabled"] = bool(data["music_enabled"])
    data["ask_save"] = bool(data["ask_save"])
    data["welcome_seen"] = bool(data["welcome_seen"])
    data["tour_seen"] = bool(data["tour_seen"])
    data["check_updates"] = bool(data["check_updates"])
    data["versions"] = 2 if str(data["versions"]) == "2" else 1
    if data["target_length"] not in pacing.TARGETS:
        data["target_length"] = pacing.DEFAULT_TARGET
    for key, allowed, fallback in AUDIENCE_SETTINGS:
        if data[key] not in allowed:
            data[key] = fallback
    if data["writer_mode"] not in ("Smart writer", "Quick split"):
        data["writer_mode"] = "Smart writer"
    data["writer_gpu"] = bool(data["writer_gpu"])
    data["voice_online"] = bool(data["voice_online"])
    data["auto_cards"] = bool(data["auto_cards"])
    if data["ai_images_for"] not in AI_IMAGES_FOR:
        data["ai_images_for"] = AI_IMAGES_FOR[0]
    if data["ai_images_look"] not in imagegen.LOOKS:
        data["ai_images_look"] = imagegen.DEFAULT_LOOK
    if data["ai_images_backend"] != imagegen.CPU:
        data["ai_images_backend"] = ""
    if data["ai_device"] not in AI_DEVICES:
        data["ai_device"] = "Auto"
    data["project"] = str(data["project"])
    if data["animate"] not in animate.CHOICES:
        data["animate"] = animate.CHOICES[0]
    data["whats_new_seen"] = str(data["whats_new_seen"])
    if data["style"] not in drawn.STYLES:
        data["style"] = drawn.NORMAL
    for key in ("intro_on", "outro_on", "sound_effects", "smart_motion", "web_facts",
                "whats_new_off", "quick_sync"):
        data[key] = bool(data[key])
    for key in ("intro_text", "outro_text"):
        data[key] = " ".join(str(data[key]).split())[:60]
    data["key_tip"] = bool(data["key_tip"])
    if data["ai_images_speed"] not in imagegen.SPEEDS:
        data["ai_images_speed"] = ""
    if str(data["ai_threads"]) not in AI_THREAD_OPTIONS:
        data["ai_threads"] = "Auto"
    try:
        data["ai_fast_seconds"] = max(float(data["ai_fast_seconds"]), 0.0)
    except (TypeError, ValueError):
        data["ai_fast_seconds"] = 0.0
    if data["music_path"] not in ("", AUTO_TRACK) and not os.path.isfile(str(data["music_path"])):
        data["music_path"] = ""

    if data["aspect"] not in RATIO_OPTIONS:
        data["aspect"] = "9:16"
    if data["resolution"] not in RESOLUTION_OPTIONS:
        data["resolution"] = "1080p"
    # An ID or an old label; the four original labels are the Classic voices' IDs.
    data["voice"] = voices.persona(data["voice"]).id
    if data["voice"] == voices.MY_VOICE and not myvoice.ready():
        data["voice"] = voices.DEFAULT_ID   # My voice is hidden (or not made): the usual voice
    data["captions"] = bool(data["captions"])
    for key, allowed, default in CAPTION_SETTINGS:
        if data[key] not in allowed:
            data[key] = default
    if data["theme"] not in ("dark", "light"):
        data["theme"] = "dark"

    # A saved folder that is gone - another PC's profile, a removed drive - is
    # replaced now. Whether a folder that exists also takes files is tested
    # when rendering (see vidgen.paths), not here on every launch.
    data["output_path"] = paths.repair_output_path(data.get("output_path"),
                                                   default_output_dir())
    return data


def save_settings(data: dict) -> None:
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError:
        pass


# =============================================================================
#  SECTION 4 - WINDOW EFFECTS (Mica / acrylic / dark title bar)
# =============================================================================

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMWA_MICA_EFFECT = 1029  # Windows 11 21H2 fallback


def _hwnd_of(window) -> int:
    try:
        window.update_idletasks()
        handle = ctypes.windll.user32.GetParent(window.winfo_id())
        return handle or window.winfo_id()
    except Exception:
        return 0


def _colorref(hex_color: str) -> int:
    """#RRGGBB -> Windows COLORREF (0x00BBGGRR)."""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    return (b << 16) | (g << 8) | r


def apply_window_effects(window, dark: bool = True, translucent: bool = True) -> str:
    """Dark/light title bar + Mica backdrop, with an alpha fallback.

    Returns a short human-readable description of what was actually applied.
    The order matters: the backdrop is set before the caption colour, because a
    Mica backdrop otherwise paints over the caption we asked for.
    """
    caption = "#202020" if dark else "#F3F3F3"
    title_text = "#FFFFFF" if dark else "#101010"
    notes = []
    backdrop_ok = False

    if sys.platform != "win32":
        if translucent:
            try:
                window.attributes("-alpha", 0.95)
                return "Non-Windows: window alpha 0.95 (no Mica available)"
            except Exception:
                pass
        return "Non-Windows: no translucency applied"

    hwnd = _hwnd_of(window)
    dwm = None
    try:
        dwm = ctypes.windll.dwmapi
    except Exception:
        pass

    def set_attr(attribute, value):
        if not (dwm and hwnd):
            return False
        try:
            boxed = ctypes.c_int(value)
            return dwm.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(boxed), ctypes.sizeof(boxed)
            ) == 0
        except Exception:
            return False

    # 1. Immersive dark/light mode for the native caption.
    if not set_attr(DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0):
        set_attr(DWMWA_USE_IMMERSIVE_DARK_MODE_OLD, 1 if dark else 0)
    notes.append("dark title bar" if dark else "light title bar")

    # 2. Backdrop.  Mica only in dark mode - in light mode it would tint the
    #    caption back to dark and fight the theme.
    if dark:
        try:
            import pywinstyles  # type: ignore

            pywinstyles.apply_style(window, "mica")
            backdrop_ok = True
            notes.append("pywinstyles Mica")
        except Exception:
            if set_attr(DWMWA_SYSTEMBACKDROP_TYPE, 2):        # 2 = Mica
                backdrop_ok = True
                notes.append("Mica backdrop")
            elif set_attr(DWMWA_MICA_EFFECT, 1):              # 21H2 fallback
                backdrop_ok = True
                notes.append("Mica (21H2 attribute)")
    else:
        set_attr(DWMWA_SYSTEMBACKDROP_TYPE, 1)                # 1 = auto
        set_attr(DWMWA_MICA_EFFECT, 0)

    # 3. Caption colours last, so they survive the backdrop change.
    set_attr(DWMWA_CAPTION_COLOR, _colorref(caption))
    set_attr(DWMWA_TEXT_COLOR, _colorref(title_text))

    # 4. Translucency.  Mica does not tint Tk's opaque client area, so a light
    #    alpha is what actually makes the window read as translucent.
    try:
        if translucent:
            window.attributes("-alpha", 0.97 if backdrop_ok else 0.95)
            notes.append("alpha 0.97" if backdrop_ok else "alpha 0.95 fallback")
        else:
            window.attributes("-alpha", 1.0)
    except Exception:
        pass

    return ", ".join(notes) if notes else "no effects available on this system"


# =============================================================================
#  SECTION 5 - DESIGN TOKENS  (every colour is a (light, dark) pair)
# =============================================================================

SIDEBAR_BG = ("#F0F0F0", "#202020")
MAIN_BG = ("#F7F7F7", "#272727")
CARD_BG = ("#FFFFFF", "#2F2F2F")
CARD_HOVER = ("#F4F4F4", "#353535")
NAV_SELECTED = ("#E4E4E4", "#2D2D2D")
NAV_HOVER = ("#EAEAEA", "#292929")
TEXT = ("#1A1A1A", "#FFFFFF")
TEXT_MUTED = ("#5E5E5E", "#9D9D9D")
TEXT_DIM = ("#767676", "#7A7A7A")
ACCENT = ("#005FB8", "#60CDFF")
ACCENT_HOVER = ("#00509E", "#7FD7FF")
ACCENT_TEXT = ("#FFFFFF", "#0B0B0B")
FIELD_BG = ("#FDFDFD", "#373737")
FIELD_BORDER = ("#D8D8D8", "#4A4A4A")
DIVIDER = ("#E5E5E5", "#3A3A3A")
OK_COLOR = ("#0F7B0F", "#6CCB5F")
ERR_COLOR = ("#C42B1C", "#FF99A4")
WARN_COLOR = ("#9D5D00", "#FCE100")

# The log is a normal Fluent surface, not a terminal: same well as the script
# editor, UI font, and severity carried by colour instead of phosphor green.
LOG_BODY = ("#3C3C3C", "#C8C8C8")
LOG_STEP = ("#1A1A1A", "#FFFFFF")
LOG_TAGS = {
    "body": LOG_BODY,
    "step": LOG_STEP,
    "success": OK_COLOR,
    "error": ERR_COLOR,
    "warn": WARN_COLOR,
    "dim": ("#767676", "#7A7A7A"),
}

# Segmented control: a sunken track with a raised pill on the chosen option,
# like the Windows 11 Settings app.
SEG_TRACK = ("#F3F3F3", "#2B2B2B")
SEG_HOVER = ("#EAEAEA", "#333333")
SEG_PILL = ("#FFFFFF", "#454545")
SEG_PILL_BORDER = ("#E0E0E0", "#505050")

# Motion, in milliseconds.  Fluent's "fast" and "normal" durations.
ANIM_FAST = 160
ANIM_PAGE = 220
PAGE_RISE = 28   # px the incoming page slides up from

SCROLLBAR_BG = "transparent"
SCROLLBAR_THUMB = ("#D2D2D2", "#3F3F3F")
SCROLLBAR_THUMB_HOVER = ("#BDBDBD", "#4E4E4E")

PAD = 8          # gap between cards
EDGE = 18        # page margin
SIDEBAR_WIDTH = 175
SIDEBAR_COLLAPSED = 48

# Segoe Fluent Icons glyphs, with a plain-unicode fallback set.
GLYPHS = {
    "menu": ("", "≡"),
    "home": ("", "⌂"),
    "create": ("", "▶"),
    "log": ("", "≡"),
    "images": ("", "▣"),
    "settings": ("", "⚙"),
    "feedback": ("", "?"),
    "aspect": ("", "▣"),
    "resolution": ("", "▭"),
    "voice": ("", "♪"),
    "bitrate": ("", "⚡"),
    "folder": ("", "▤"),
    "scenes": ("", "≣"),
    "status": ("", "✓"),
    "key": ("", "⚿"),
    "play": ("", "▶"),
    "clear": ("", "✗"),
    "theme": ("", "◑"),
    "glass": ("", "▨"),
    "info": ("", "ℹ"),
    "script": ("", "✎"),
    "music": ("\ue8d6", "\u266b"),
    "timer": ("\ue916", "\u23f1"),
    "stop": ("\ue71a", "\u25a0"),
    "warning": ("\ue7ba", "\u26a0"),
    "captions": ("\ue7f0", "CC"),
    "font_size": ("\ue8e9", "A"),
    "color": ("\ue790", "\u25cf"),
    "position": ("\ue8cb", "\u2195"),
    "projects": ("\ue8f1", "▦"),
    "eye": ("", "◉"),
}


# =============================================================================
#  SECTION 6 - REUSABLE UI PIECES
# =============================================================================


def theme_color(pair):
    """Resolve a (light, dark) token to the colour for the current mode.

    Needed because Tk text tags take a single colour, unlike CTk widgets.
    """
    if isinstance(pair, (tuple, list)):
        return pair[1] if ctk.get_appearance_mode().lower() == "dark" else pair[0]
    return pair


def log_severity(message: str) -> str:
    """Pick a log tag from the message the pipeline emitted.

    The pipeline's log strings are left untouched, so the UI classifies them by
    their leading marker rather than by a level argument.
    """
    text = message.lstrip()
    if text.startswith("❌") or text.startswith("ERROR"):
        return "error"
    if text.startswith("✅"):
        return "success"
    if text.startswith("⚠️"):
        return "warn"
    if text.startswith(("🚀", "🎬", "✂️", "🧹", "📦", "🪟", "🎵")):
        return "step"
    if text.startswith("🎞️"):
        return "dim"
    return "body"


def ellipsize(text: str, limit: int = 58) -> str:
    """Middle-ellipsis for long paths."""
    if len(text) <= limit:
        return text
    head = limit // 2 - 2
    tail = limit - head - 3
    return f"{text[:head]}...{text[-tail:]}"


def ease_out(t: float) -> float:
    """Cubic ease-out: quick start, gentle landing - Fluent's decelerate curve."""
    return 1 - (1 - t) ** 3


class Animation:
    """Calls ``step(eased)`` from 0 to 1 over ``duration`` ms on the Tk loop.

    Frames are timed by the clock, not counted, so a slow frame shortens the
    animation instead of stretching it.
    """

    FRAME_MS = 10

    def __init__(self, widget, duration, step, done=None):
        self.widget, self.duration, self.step, self.done = widget, duration, step, done
        self.start = time.perf_counter()
        self.job = None
        self._tick()

    def _tick(self):
        t = min((time.perf_counter() - self.start) * 1000 / self.duration, 1.0)
        self.step(ease_out(t))
        if t < 1.0:
            self.job = self.widget.after(self.FRAME_MS, self._tick)
        else:
            self.job = None
            if self.done:
                self.done()

    def cancel(self):
        if self.job:
            try:
                self.widget.after_cancel(self.job)
            except Exception:
                pass
            self.job = None


def round_rect(canvas, x0, y0, x1, y1, r, **kwargs):
    """A rounded rectangle as one smoothed polygon (Tk has no native one)."""
    r = max(0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    points = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
              x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def focus_accent(entry):
    """Give an entry Fluent's accent-coloured border while it has focus."""
    entry.bind("<FocusIn>", lambda _e: entry.configure(border_color=ACCENT), add="+")
    entry.bind("<FocusOut>", lambda _e: entry.configure(border_color=FIELD_BORDER), add="+")
    return entry


class Card(ctk.CTkFrame):
    """Flat rounded surface, the building block of every page."""

    def __init__(self, master, **kwargs):
        kwargs.setdefault("corner_radius", 5)
        kwargs.setdefault("fg_color", CARD_BG)
        kwargs.setdefault("border_width", 0)
        super().__init__(master, **kwargs)


class FluentSegmented(ctk.CTkFrame):
    """Windows 11 segmented control bound to a StringVar.

    A sunken track, a raised pill under the chosen option and a short accent
    bar beneath its label; pill and bar slide when the choice changes.  It is
    drawn on one Tk canvas because the pill has to pass *under* the labels,
    which separate CTk widgets can't do.
    """

    INSET = 3       # gap between the track edge and the pill
    RADIUS = 6
    PILL_RADIUS = 4
    BAR_WIDTH = 16

    def __init__(self, master, app, values, variable, width, height=32):
        super().__init__(master, fg_color="transparent", width=width, height=height)
        self.values = list(values)
        self.variable = variable
        self.family = app.ui_family
        self.pos = float(self._index(variable.get()))   # animated pill position
        self.hover = None
        self.anim = None

        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0, cursor="hand2")
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.canvas.bind("<Configure>", lambda _e: self.draw())
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self._trace = variable.trace_add("write", self._on_variable)
        self.bind("<Destroy>", self._on_destroy, add="+")

    # -- model -----------------------------------------------------------------
    def _index(self, value):
        return self.values.index(value) if value in self.values else 0

    def _segment_at(self, x):
        width = self.canvas.winfo_width()
        inner = max(width - 2 * self.INSET, 1)
        index = int((x - self.INSET) / inner * len(self.values))
        return max(0, min(index, len(self.values) - 1))

    # -- events ----------------------------------------------------------------
    def _on_click(self, event):
        value = self.values[self._segment_at(event.x)]
        if value != self.variable.get():
            self.variable.set(value)   # the trace animates the pill

    def _on_motion(self, event):
        index = self._segment_at(event.x)
        if index != self.hover:
            self.hover = index
            self.draw()

    def _on_leave(self, _event):
        self.hover = None
        self.draw()

    def _on_variable(self, *_args):
        target = float(self._index(self.variable.get()))
        if self.anim:
            self.anim.cancel()
        start = self.pos
        if not self.winfo_ismapped() or start == target:
            self.pos = target
            self.draw()
            return

        def step(t):
            self.pos = start + (target - start) * t
            self.draw()

        self.anim = Animation(self, ANIM_FAST, step)

    def _on_destroy(self, event):
        if event.widget is self:
            if self.anim:
                self.anim.cancel()
            try:
                self.variable.trace_remove("write", self._trace)
            except Exception:
                pass

    # -- CustomTkinter hooks -----------------------------------------------------
    def _set_appearance_mode(self, mode_string):
        super()._set_appearance_mode(mode_string)
        self.draw()

    def _set_scaling(self, *args, **kwargs):
        super()._set_scaling(*args, **kwargs)
        self.draw()

    # -- drawing -----------------------------------------------------------------
    def draw(self):
        c = self.canvas
        w, h = c.winfo_width(), c.winfo_height()
        if w < 4 or h < 4:
            return
        s = self._get_widget_scaling()
        col = self._apply_appearance_mode
        c.delete("all")
        c.configure(bg=col(self._bg_color))

        n = len(self.values)
        inset = self.INSET * s
        seg = (w - 2 * inset) / n
        round_rect(c, 0, 0, w - 1, h - 1, self.RADIUS * s,
                   fill=col(SEG_TRACK), outline=col(FIELD_BORDER))

        selected = self._index(self.variable.get())
        if self.hover is not None and self.hover != selected:
            x0 = inset + self.hover * seg
            round_rect(c, x0 + 1, inset, x0 + seg - 1, h - inset,
                       self.PILL_RADIUS * s, fill=col(SEG_HOVER), outline="")

        x0 = inset + self.pos * seg
        round_rect(c, x0, inset, x0 + seg, h - inset, self.PILL_RADIUS * s,
                   fill=col(SEG_PILL), outline=col(SEG_PILL_BORDER))
        mid = x0 + seg / 2
        bar_y = h - inset - 3 * s
        c.create_line(mid - self.BAR_WIDTH * s / 2, bar_y, mid + self.BAR_WIDTH * s / 2,
                      bar_y, fill=col(ACCENT), width=max(3 * s, 2), capstyle="round")

        font = (self.family, -round(13 * s))
        for index, value in enumerate(self.values):
            # The label nearest the pill reads as selected throughout the slide.
            active = abs(index - self.pos) < 0.5
            c.create_text(inset + (index + 0.5) * seg, h / 2 - 1, text=value, font=font,
                          fill=col(TEXT if active else TEXT_MUTED))


class NavItem(ctk.CTkFrame):
    """Sidebar entry: accent bar + icon + label, with hover and selected states."""

    def __init__(self, master, app, key, glyph, text, command):
        super().__init__(master, fg_color="transparent", corner_radius=5,
                         width=SIDEBAR_WIDTH - 12, height=36)
        self.pack_propagate(False)
        self.grid_propagate(False)
        self.app = app
        self.key = key
        self.command = command
        self.selected = False

        self.bar = ctk.CTkFrame(self, width=3, height=16, corner_radius=2,
                                fg_color="transparent")
        self.bar.place(x=3, rely=0.5, anchor="w")

        self.icon = ctk.CTkLabel(self, text=glyph, font=app.font_icon,
                                 text_color=TEXT, width=20)
        self.icon.place(x=14, rely=0.5, anchor="w")

        self.label = ctk.CTkLabel(self, text=text, font=app.font_body,
                                  text_color=TEXT, anchor="w")
        self.label.place(x=44, rely=0.5, anchor="w")

        for widget in (self, self.icon, self.label, self.bar):
            widget.bind("<Button-1>", self._on_click)
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)
            try:
                widget.configure(cursor="hand2")
            except Exception:
                pass

    # -- interaction ---------------------------------------------------------
    def _on_click(self, _event=None):
        self.command(self.key)

    def _on_enter(self, _event=None):
        if not self.selected:
            self.configure(fg_color=NAV_HOVER)

    def _on_leave(self, _event=None):
        if not self.selected:
            self.configure(fg_color="transparent")

    def set_selected(self, value: bool):
        was = self.selected
        self.selected = value
        self.configure(fg_color=NAV_SELECTED if value else "transparent")
        self.bar.configure(fg_color=ACCENT if value else "transparent")
        if value and not was:
            # NavigationView's indicator grows in from the middle.
            Animation(self, ANIM_FAST, lambda t: self.bar.configure(height=4 + 12 * t))

    def set_collapsed(self, collapsed: bool):
        if collapsed:
            self.label.place_forget()
            self.icon.place_configure(relx=0.5, x=0, anchor="center")
        else:
            self.icon.place_configure(relx=0, x=14, anchor="w")
            self.label.place(x=44, rely=0.5, anchor="w")


class InfoRow(Card):
    """Wide card: icon, label, value - the top half of the reference layout."""

    def __init__(self, master, app, glyph, label, value=""):
        super().__init__(master, height=56)
        self.grid_propagate(False)
        self.grid_columnconfigure(2, weight=1)
        self.grid_rowconfigure(0, weight=1)

        ctk.CTkLabel(self, text=glyph, font=app.font_icon, text_color=TEXT_MUTED,
                     width=20).grid(row=0, column=0, padx=(16, 12))
        ctk.CTkLabel(self, text=label, font=app.font_body, text_color=TEXT,
                     anchor="w", width=104).grid(row=0, column=1, sticky="w")
        self.value_label = ctk.CTkLabel(self, text=value, font=app.font_body,
                                        text_color=TEXT_MUTED, anchor="w")
        self.value_label.grid(row=0, column=2, sticky="w", padx=(8, 16))

    def set_value(self, value, color=None):
        self.value_label.configure(text=value, text_color=color or TEXT_MUTED)


class ActionRow(Card):
    """Info row with a round accent button on the right (the 'flame' card)."""

    def __init__(self, master, app, glyph, label, value, button_glyph, command):
        super().__init__(master, height=56)
        self.grid_propagate(False)
        self.grid_columnconfigure(2, weight=1)
        self.grid_rowconfigure(0, weight=1)

        ctk.CTkLabel(self, text=glyph, font=app.font_icon, text_color=TEXT_MUTED,
                     width=20).grid(row=0, column=0, padx=(16, 12))
        ctk.CTkLabel(self, text=label, font=app.font_body, text_color=TEXT,
                     anchor="w", width=104).grid(row=0, column=1, sticky="w")
        self.value_label = ctk.CTkLabel(self, text=value, font=app.font_body,
                                        text_color=TEXT_MUTED, anchor="w")
        self.value_label.grid(row=0, column=2, sticky="w", padx=(8, 8))

        self.button = ctk.CTkButton(
            self, text=button_glyph, font=app.font_icon, width=34, height=28,
            corner_radius=4, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER,
            text_color=ACCENT, command=command,
        )
        self.button.grid(row=0, column=3, padx=(0, 12))

    def set_value(self, value, color=None):
        self.value_label.configure(text=value, text_color=color or TEXT_MUTED)


class Tile(Card):
    """Square-ish card: icon top-left, label bottom-left, value bottom-right."""

    def __init__(self, master, app, glyph, label, value="", corner=""):
        super().__init__(master, height=112)
        self.grid_propagate(False)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(self, text=glyph, font=app.font_icon_lg,
                     text_color=TEXT_MUTED).grid(row=0, column=0, sticky="w",
                                                 padx=(16, 0), pady=(14, 0))
        self.corner_label = ctk.CTkLabel(self, text=corner, font=app.font_tiny,
                                         text_color=TEXT_DIM)
        self.corner_label.grid(row=0, column=1, sticky="e", padx=(0, 16), pady=(14, 0))

        ctk.CTkLabel(self, text=label, font=app.font_body, text_color=TEXT,
                     anchor="w").grid(row=2, column=0, sticky="w",
                                      padx=(16, 0), pady=(0, 14))
        self.value_label = ctk.CTkLabel(self, text=value, font=app.font_value,
                                        text_color=TEXT, anchor="e")
        self.value_label.grid(row=2, column=1, sticky="e", padx=(0, 16), pady=(0, 14))

    def set_value(self, value, color=None):
        self.value_label.configure(text=value, text_color=color or TEXT)

    def set_corner(self, text):
        self.corner_label.configure(text=text)


class SettingRow(Card):
    """Windows-11-Settings style row: icon, title/subtitle, control on the right."""

    def __init__(self, master, app, glyph, title, subtitle=""):
        super().__init__(master)
        self.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self, text=glyph, font=app.font_icon, text_color=TEXT_MUTED,
                     width=20).grid(row=0, column=0, padx=(16, 12), pady=14)

        text_frame = ctk.CTkFrame(self, fg_color="transparent")
        text_frame.grid(row=0, column=1, sticky="w", pady=12)
        ctk.CTkLabel(text_frame, text=title, font=app.font_body, text_color=TEXT,
                     anchor="w").pack(anchor="w")
        if subtitle:
            ctk.CTkLabel(text_frame, text=subtitle, font=app.font_tiny,
                         text_color=TEXT_DIM, anchor="w").pack(anchor="w")

        self.control = ctk.CTkFrame(self, fg_color="transparent")
        self.control.grid(row=0, column=2, sticky="e", padx=(12, 14), pady=10)


class Dialog(ctk.CTkToplevel):
    """Small themed modal replacing tkinter.messagebox."""

    def __init__(self, app, title, message, ok=True, on_report=None, confirm=None,
                 confirm_text="OK", cancel=None, cancel_text="Cancel"):
        super().__init__(app)
        self.title(title)
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)

        wrapper = Card(self)
        wrapper.pack(fill="both", expand=True, padx=14, pady=14)

        header = ctk.CTkFrame(wrapper, fg_color="transparent")
        header.pack(fill="x", padx=18, pady=(16, 4))
        ctk.CTkLabel(header, text=app.icon("status" if ok else "warning"),
                     font=app.font_icon_lg,
                     text_color=OK_COLOR if ok else ERR_COLOR).pack(side="left",
                                                                    padx=(0, 10))
        ctk.CTkLabel(header, text=title, font=app.font_title,
                     text_color=TEXT).pack(side="left")

        ctk.CTkLabel(wrapper, text=message, font=app.font_body,
                     text_color=TEXT_MUTED, justify="left", wraplength=420,
                     anchor="w").pack(fill="x", padx=18, pady=(2, 16))

        buttons = ctk.CTkFrame(wrapper, fg_color="transparent")
        buttons.pack(fill="x", padx=18, pady=(0, 16))
        def confirmed():
            self.destroy()
            confirm()

        def cancelled():
            self.destroy()
            cancel()

        self.confirm_button = ctk.CTkButton(
            buttons, text=confirm_text if confirm else "OK", width=96, height=32,
            corner_radius=4, font=app.font_body, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color=ACCENT_TEXT, command=confirmed if confirm else self.destroy)
        self.confirm_button.pack(side="right")
        if confirm:
            ctk.CTkButton(buttons, text=cancel_text, width=96, height=32, corner_radius=4,
                          font=app.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                          border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                          command=cancelled if cancel else self.destroy,
                          ).pack(side="right", padx=(0, 8))
            if cancel:
                self.protocol("WM_DELETE_WINDOW", cancelled)
        if on_report:
            def report():
                self.destroy()
                on_report()

            ctk.CTkButton(buttons, text="Report this", width=110, height=32, corner_radius=4,
                          font=app.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                          border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                          command=report).pack(side="right", padx=(0, 8))

        centre_on(self, app)


# The two footage services, for the Welcome window. ``setting`` is the App
# variable the key is stored in.
KEY_PROVIDERS = {
    "Pixabay": {
        "page": "https://pixabay.com/api/docs/",
        "button": "Open pixabay.com/api/docs",
        "hint": "Sign up or log in on the Pixabay page. Your key is then shown on that "
                "page, next to \"key (required)\".",
        "setting": "var_pixabay",
    },
    "Pexels": {
        "page": "https://www.pexels.com/api/",
        "button": "Open pexels.com/api",
        "hint": "Sign up on the Pexels page, then copy the key shown under "
                "\"Your API key\".",
        "setting": "var_api",
    },
}


class WelcomeDialog(ctk.CTkToplevel):
    """First-run setup: the one thing a new user has to fetch is a footage key.

    Either service will do, so the window offers both and starts on Pixabay.
    """

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title(f"Welcome to {APP_NAME}")
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.skip)
        # One box per service, so switching doesn't lose what was typed.
        self.keys = {name: tk.StringVar(value=getattr(app, info["setting"]).get())
                     for name, info in KEY_PROVIDERS.items()}
        only_pexels = self.keys["Pexels"].get().strip() and not self.keys["Pixabay"].get().strip()
        self.var_provider = tk.StringVar(value="Pexels" if only_pexels else "Pixabay")

        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)

        ctk.CTkLabel(card, text=f"Welcome to {APP_NAME}", font=app.font_title,
                     text_color=TEXT, anchor="w").pack(fill="x", padx=20, pady=(18, 4))
        ctk.CTkLabel(
            card, font=app.font_body, text_color=TEXT_MUTED, justify="left",
            wraplength=440, anchor="w",
            text="The app finds stock footage for your script on Pixabay or Pexels. "
                 "Each gives every user a free key for that, with no payment details. "
                 "One key is enough - pick either.",
        ).pack(fill="x", padx=20, pady=(0, 12))
        FluentSegmented(card, app, list(KEY_PROVIDERS), self.var_provider, 220).pack(
            anchor="w", padx=20, pady=(0, 16))

        self._step(card, "1", "Get your free key")
        self.hint = ctk.CTkLabel(card, font=app.font_tiny, text_color=TEXT_DIM,
                                 justify="left", wraplength=410, anchor="w", height=30)
        self.hint.pack(fill="x", padx=(50, 20))
        self.open_button = ctk.CTkButton(
            card, width=210, height=32, corner_radius=4,
            font=app.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
            command=lambda: webbrowser.open(KEY_PROVIDERS[self.var_provider.get()]["page"]),
        )
        self.open_button.pack(anchor="w", padx=(50, 20), pady=(8, 16))

        self._step(card, "2", "Paste it here")
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=(50, 20), pady=(6, 0))
        self.entry = focus_accent(ctk.CTkEntry(
            row, width=300, height=32, corner_radius=4,
            font=app.font_body, fg_color=FIELD_BG, border_color=FIELD_BORDER,
            border_width=1, text_color=TEXT,
        ))
        self.entry.pack(side="left")
        self.test_button = ctk.CTkButton(
            row, text="Test key", width=92, height=32, corner_radius=4,
            font=app.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT, command=self.test,
        )
        self.test_button.pack(side="left", padx=(6, 0))
        self.result = ctk.CTkLabel(card, text="", font=app.font_tiny, text_color=TEXT_DIM,
                                   anchor="w", justify="left", wraplength=410)
        self.result.pack(fill="x", padx=(50, 20), pady=(6, 14))

        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.pack(fill="x", padx=20, pady=(0, 18))
        ctk.CTkButton(
            buttons, text="Save and continue", width=150, height=32, corner_radius=4,
            font=app.font_body, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color=ACCENT_TEXT, command=self.save,
        ).pack(side="right")
        ctk.CTkButton(
            buttons, text="Skip for now", width=110, height=32, corner_radius=4,
            font=app.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT, command=self.skip,
        ).pack(side="right", padx=(0, 8))

        self._trace = self.var_provider.trace_add("write", self._show_provider)
        self._show_provider()
        centre_on(self, app)

    def _show_provider(self, *_args):
        """Point step 1 and the key box at the chosen service."""
        if not self.winfo_exists():
            return
        name = self.var_provider.get()
        info = KEY_PROVIDERS[name]
        self.hint.configure(text=info["hint"])
        self.open_button.configure(text=info["button"])
        self.entry.configure(textvariable=self.keys[name])
        self.result.configure(text="")

    def _step(self, card, number, title):
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=20)
        ctk.CTkLabel(row, text=number, width=22, height=22, corner_radius=11,
                     font=self.app.font_caption, fg_color=ACCENT,
                     text_color=ACCENT_TEXT).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(row, text=title, font=self.app.font_bold, text_color=TEXT,
                     anchor="w").pack(side="left")

    def show_result(self, ok, message):
        if self.winfo_exists():
            self.test_button.configure(state="normal", text="Test key")
            self.result.configure(text=message, text_color=OK_COLOR if ok else ERR_COLOR)

    def test(self):
        self.test_button.configure(state="disabled", text="Testing...")
        self.result.configure(text="")
        name = self.var_provider.get()
        self.app.test_key(name, self.keys[name].get(), self.show_result)

    def save(self):
        name = self.var_provider.get()
        key = self.keys[name].get().strip()
        if not key:
            self.show_result(False, "Paste a key first, or choose Skip for now.")
            return
        self.app.welcome_seen = True
        # Saves the settings through the variable's trace.
        getattr(self.app, KEY_PROVIDERS[name]["setting"]).set(key)
        self.app.select_page("create")
        self.destroy()

    def skip(self):
        self.app.welcome_seen = True
        self.app._on_setting_changed()
        self.destroy()


TOUR = (
    ("home", "Paste your text",
     "Put any text in the box on Home: an article, a tip, your notes. Press Make my video and "
     "the app writes the script, finds the pictures, reads it out and adds captions."),
    ("scenes", "Look before it is made",
     "The Storyboard shows every scene with its picture and its line. Change a line, swap a "
     "clip, or move scenes around. When it looks right, press Make video."),
    ("script", "Say what to change",
     "When the video is done, type what you would like different, such as \"make it shorter\" "
     "or \"more exciting start\". The app rewrites it and shows you what changed."),
    ("key", "One free key",
     "To download video clips the app needs one free key from Pixabay or Pexels. It takes about "
     "a minute, and the next window walks you through it."),
)


# Shown once after updating to this version (WhatsNewDialog). Short: the release notes have
# the whole story.
WHATS_NEW = (
    "Show it on my PC (beta): type a Windows tip and the app finds the page itself (Settings "
    "search, the Run box or the taskbar), records it, zooms onto the button and blurs your "
    "name. A simple switch it can flip for you after you say yes, and it puts it back.",
    "Tip videos start with a hook (\"Here's how to make your PC faster in 15 seconds\") and "
    "shortcut keycaps like Win + I; 192 ready-made tips cover every page the app knows.",
    "My voice (beta): read six sentences, and scripts are spoken in a voice like yours. "
    "Needs an NVIDIA graphics card with 6 GB or more and a one-time 7 GB voice pack.",
    "Fixes: a scene with no stock clip left no longer stops the render, videos always save as "
    ".mp4, and built-in graphics skip moving pictures instead of crashing after a minute.",
)


RECORDINGS_DIR = os.path.join(os.path.dirname(MUSIC_DIR), "Recordings")
# When this copy was built (the exe's date), or "from source": shown where a test build matters.
BUILD = (time.strftime("build %d %b %H:%M", time.localtime(os.path.getmtime(sys.executable)))
         if getattr(sys, "frozen", False) else "from source")


class FlipDialog(Dialog):
    """Show it on my PC: may the app flip the tip's switch itself, and back? Asked every time;
    ``answer(True/False)`` gets the reply (No, or closing it: the user clicks, as before)."""

    def __init__(self, app, tip, answer):
        was, to = tip.auto.state.lower(), tip.auto_to.lower()
        super().__init__(
            app, "OK to change this?",
            f"For the video the app will turn {tip.auto.name} {to} itself, then back {was} "
            "right after, and check it. Nothing else is changed.\n\n"
            "No: you click it yourself, as before.",
            confirm=lambda: answer(True), confirm_text="Yes",
            cancel=lambda: answer(False), cancel_text="No")


class TipPicker(ctk.CTkToplevel):
    """The ready-made tips (tiplist: one or more for every page of the map), with a search box.
    A click puts the tip in the dialog's box; Record then plans it like a typed one."""

    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        app = dialog.app
        self.title("Ready-made tips")
        self.geometry("460x560")
        self.configure(fg_color=MAIN_BG)
        self.transient(dialog)
        self.find = ctk.CTkEntry(self, placeholder_text="Search, for example: night",
                                 font=app.font_body)
        self.find.pack(fill="x", padx=14, pady=(14, 8))
        self.find.bind("<KeyRelease>", lambda _e: self.show())
        self.list = ctk.CTkScrollableFrame(self, fg_color=CARD_BG)
        self.list.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.rows = []   # (heading or None, widget, tip text)
        for name, texts in tiplist.groups():
            heading = ctk.CTkLabel(self.list, text=name, font=app.font_body, text_color=TEXT,
                                   anchor="w")
            self.rows.append((name, heading, ""))
            for text in texts:
                button = ctk.CTkButton(self.list, text=text, anchor="w", height=26,
                                       font=app.font_tiny, fg_color="transparent",
                                       hover_color=CARD_HOVER, text_color=TEXT_MUTED,
                                       command=lambda t=text: self.choose(t))
                self.rows.append((None, button, text))
        self.show()
        self.protocol("WM_DELETE_WINDOW", self.close)
        centre_on(self, dialog)
        self.find.focus_set()

    def show(self):
        """Only the tips that have every searched word, under their headings."""
        words = self.find.get().lower().split()
        for _heading, widget, _text in self.rows:
            widget.pack_forget()
        heading = None
        for name, widget, text in self.rows:
            if name is not None:
                heading = widget
                continue
            if all(w in text.lower() for w in words):
                if heading is not None:
                    heading.pack(fill="x", padx=6, pady=(10, 2))
                    heading = None
                widget.pack(fill="x", padx=6)

    def choose(self, text):
        self.dialog.ask.delete(0, "end")
        self.dialog.ask.insert(0, text)
        self.dialog._forget_plan()
        self.close()

    def close(self):
        self.destroy()
        if self.dialog.winfo_exists():
            self.dialog.grab_set()   # the dialog is modal again


class GuideBox(ctk.CTkToplevel):
    """The small box that says what to click while the screen is recorded.

    It sits beside the recorded window and asks Windows to leave it out of recordings, so it
    never shows in the video.
    """

    def __init__(self, app, rect, on_next, on_cancel):
        super().__init__(app)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color=CARD_BG)
        self.text = ctk.CTkLabel(self, text="", font=app.font_body, text_color=TEXT,
                                 wraplength=260, justify="left")
        self.text.pack(fill="x", padx=14, pady=(12, 8))
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(0, 12))
        self.next = ctk.CTkButton(row, text="Next", width=90, height=30, command=on_next,
                                  fg_color=ACCENT)
        self.next.pack(side="left")
        ctk.CTkButton(row, text="Cancel", width=80, height=30, command=on_cancel,
                      fg_color=FIELD_BG, hover_color=CARD_HOVER, text_color=TEXT,
                      ).pack(side="left", padx=(8, 0))
        self.bind("<Escape>", lambda _e: on_cancel())
        self.update_idletasks()
        left, top, width, height = rect
        box_w, box_h = 300, self.winfo_reqheight()
        screen_w = self.winfo_screenwidth()
        # Real pixels: Tk places windows in the same pixels the recording uses.
        x = left + width + 12 if left + width + 12 + box_w <= screen_w else max(left - box_w - 12, 0)
        self.geometry(f"{box_w}x{box_h}+{x}+{max(top, 0)}")
        self.update_idletasks()
        screen.hide_from_capture(ctypes.windll.user32.GetParent(self.winfo_id())
                                 if sys.platform == "win32" else 0)

    def show(self, text, button="Next", waiting=False):
        self.text.configure(text=text)
        # Never greyed out: a switch that is already the right way never changes, and the
        # user must always be able to go on. Waiting only means the step can also end by itself.
        self.next.configure(text=button, state="normal")


class TipDialog(ctk.CTkToplevel):
    """Show it on my PC: record a Windows tip on this PC, then make it a video.

    The setting is read first and put back afterwards, whatever happens; one that was
    already on is left on.
    """

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title(f"Show it on my PC · {BUILD}")   # which copy is being tested
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)
        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        ctk.CTkLabel(card, text="Show it on my PC", font=app.font_title, text_color=TEXT,
                     ).pack(anchor="w", padx=18, pady=(16, 4))
        ctk.CTkLabel(card, text="The app finds the right window itself and records it. A small "
                                "box tells you what to click. A simple switch the app can flip "
                                "itself, only after you say yes, and it puts it back right "
                                "after. Anything you change is put back too.",
                     font=app.font_body, text_color=TEXT_MUTED, wraplength=440, justify="left",
                     ).pack(anchor="w", padx=18)
        ctk.CTkLabel(card, text="What tip? For example: how to turn on night light",
                     font=app.font_body, text_color=TEXT, anchor="w",
                     ).pack(anchor="w", padx=18, pady=(12, 2))
        self.ask = ctk.CTkEntry(card, width=440, font=app.font_body)
        self.ask.pack(anchor="w", padx=18)
        self.ask.bind("<Return>", lambda _e: self.record())
        # A changed tip needs a new plan; Record then plans it by itself.
        self.ask.bind("<KeyRelease>", lambda e: e.keysym != "Return" and self._forget_plan())
        ctk.CTkButton(card, text="...or pick a ready-made tip", width=200, height=28,
                      font=app.font_tiny, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                      text_color=TEXT, command=lambda: TipPicker(self),
                      ).pack(anchor="w", padx=18, pady=(8, 8))
        self.var_hook = tk.BooleanVar(self, True)
        ctk.CTkCheckBox(card, text="Start with a hook (\"Here's how to make your PC faster...\")",
                        variable=self.var_hook, font=app.font_tiny, text_color=TEXT_DIM,
                        checkbox_width=16, checkbox_height=16, border_width=1,
                        ).pack(anchor="w", padx=18, pady=(0, 8))
        self.preview = ctk.CTkLabel(card, text="", font=app.font_body, text_color=TEXT_MUTED,
                                    wraplength=440, justify="left", anchor="w")
        self.preview.pack(anchor="w", padx=18)
        self.planned = None
        self.page_hwnd, self.opened_more, self.page_states = 0, None, {}
        self.status = ctk.CTkLabel(card, text="", font=app.font_tiny, text_color=TEXT_DIM,
                                   wraplength=440, justify="left")
        self.status.pack(anchor="w", padx=18)
        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.pack(fill="x", padx=18, pady=(8, 16))
        self.record_button = ctk.CTkButton(buttons, text="Record", width=120,
                                           command=self.record, fg_color=ACCENT)
        self.record_button.pack(side="right")   # it plans first; no separate Plan it button
        self.session = None
        centre_on(self, app)

    def say(self, text, colour=TEXT_DIM):
        self.status.configure(text=text, text_color=colour)

    def plan(self, then=None):
        """Find the place in the map of Windows and let the Smart writer write the steps.

        ``then`` runs after a plan that worked (Record: plan, then record).
        """
        text = " ".join(self.ask.get().split())
        if not text:
            self.say("Type a tip first.", ERR_COLOR)
            return
        places = winmap.search(text)
        self.say("Finding where that is in Windows...")
        self.record_button.configure(state="disabled")

        def work():
            note, buttons = "", []   # buttons: also when reading the page fails
            try:
                place = places[0] if places else None   # the map decides the page
                if place is not None:
                    tips.fallback(text, places)          # an admin place: refused before asking
                buttons = self.page_buttons(
                    place, tips.match_words(text, place) if place is not None else ())
                if place is not None and not buttons:
                    # Windows hides this page's buttons (Task Manager runs with admin rights):
                    # the Smart writer would only guess their names ("click the File icon").
                    tip = tips.fallback(text, places)
                    note = (" (Plain steps: Windows doesn't show this page's buttons to the app, "
                            "so it doesn't name them.)")
                elif writer.installed():
                    with writer.Session() as session:
                        tip = tips.from_answer(text, session.tip(text, place, buttons), place)
                else:   # no Smart writer: plain steps, or Windows' search when nothing matched
                    tip = tips.fallback(text, places) if places else tips.from_answer(text, {})
                    if not writer.installed():
                        note = " (Plain steps: the Smart writer isn't downloaded.)"
            except tips.TipRefused as exc:
                message = str(exc)
                self.app.call_soon(lambda: self.show_plan(None, message))
                return
            except Exception as exc:  # noqa: BLE001 - the plain steps still work
                try:
                    tip = tips.fallback(text, places)
                    note = f" (Plain steps: the Smart writer failed: {exc})"
                except tips.TipRefused as refused:
                    message = str(refused)
                    self.app.call_soon(lambda: self.show_plan(None, message))
                    return
            if place is not None:
                note += tips.aim(tip, buttons, text, place,   # the app picks the real button
                                 self.opened_more)
            self.app.call_soon(lambda: self.show_plan(tip, note, then))

        threading.Thread(target=work, daemon=True).start()

    def page_buttons(self, place, words=()):
        """Worker thread: open the page, read its real buttons (kept for next time), or [].

        When none matches the tip's ``words`` but a "Show more..." button is there, the app
        opens it (only navigation) and reads again, so the switch behind it is known while
        planning (``opened_more``). ``page_states``: how its switches are now, for put-back.
        """
        self.opened_more, self.page_states = None, {}
        if place is None:
            return []
        store = cache.Cache(CACHE_ROOT, "winui")
        if drive.route(place) == "taskbar":
            # Task Manager runs with admin rights: it can't be read, nor closed by the app, and
            # left in front it blocks the app's mouse when it finds the page itself.
            return uiread.recall(store, place.id)
        try:
            hwnd = screen.open_window(place.command())
            self.page_hwnd = hwnd   # closed again before the app finds it through the Run box
            controls = drive.settled(hwnd) if hwnd else []
            window = screen.foreground_rect() if hwnd else None
        except OSError:
            controls, window = [], None
        if not controls:
            return uiread.recall(store, place.id)   # read here before, when Windows allowed it
        buttons = uiread.targets(controls, window)
        uiread.remember(store, place.id, buttons)   # the page as it opens: to recognise it
        self.page_states = uiread.states(controls)
        if words and uiread.best_match(buttons, words) is None and drive.find_more(controls):
            try:
                more, after = drive.open_more(hwnd, window)
            except drive.Interrupted:
                more = None
            if more is not None and after:
                self.opened_more = more
                self.page_states = {**uiread.states(after), **self.page_states}
                buttons = uiread.targets(after, window)
        return buttons

    def _forget_plan(self):
        if self.planned is not None:
            self.planned = None
            self.preview.configure(text="")

    def show_plan(self, tip, note, then=None):
        if not self.winfo_exists():
            return
        self.record_button.configure(state="normal")
        if tip is None:
            self.planned = None
            self.preview.configure(text="")
            self.say(note, ERR_COLOR)
            return
        self.planned = tip
        place = winmap.BY_ID.get(tip.key)
        where = "Windows search" if tip.key == "search" else place.name if place else tip.title
        lines = [f"Opens: {where}"] + [f"{i}. {s.guide}" for i, s in enumerate(tip.steps, 1)]
        self.preview.configure(text="\n".join(lines))
        self.say("Planned." + note,
                 WARN_COLOR if note.strip() else TEXT_DIM)
        if then is not None:
            then()

    def record(self):
        tip = self.planned
        typed = " ".join(self.ask.get().split())
        if tip is None and tips.ready_made(typed):
            tip = tips.ready_made(typed)   # it reads and restores the setting by itself
        if tip is None and typed:
            self.plan(then=self.record)   # plan first, then record
            return
        if tip is None:
            self.say("Type a tip first, or pick a ready-made one.", ERR_COLOR)
            return
        why = tip.available()
        if why:
            self.say(why, ERR_COLOR)
            return
        try:
            state = tip.read()
        except OSError as exc:
            self.say(f"Couldn't read the setting first ({exc}), so nothing was recorded.",
                     ERR_COLOR)
            return

        def go(auto):
            if self.winfo_exists():
                self.session = TipSession(self, tip, state, typed, auto)
                self.session.open()

        if tip.auto is not None:
            FlipDialog(self.app, tip, go)   # the app clicks a switch only after a yes
        else:
            go(False)


CLOSING_WAIT = 3000   # ms the closing step is recorded before it finishes by itself
HOLD_WAIT = 2.5       # s a step the app shows by itself stays on screen before the next


class TipSession:
    """One recording: open the window, record the steps, put the setting back, cut the clips.

    The page's real buttons are read again and again while recording (uiread), in the
    background: a switch that only appears after "Show more settings" is still found, zoomed
    onto and watched; flipping a step's switch ends that step by itself; Next always works. The
    account's name, email and picture are hidden. Afterwards the user is walked through
    switching back whatever they changed.

    First the app finds the page itself, slowly, so the video shows the way there (drive):
    Settings' search box, the taskbar's menu or the Run box. It only clicks its way there,
    never a setting; when a person takes over or nothing is found, it opens the page directly.
    Those steps (``nav``) go before the tip's own; the taskbar and Run box are recorded on
    their own (``intro``), since they are outside the page's window.

    Worker threads never touch the window: everything they report goes through app.call_soon.
    """

    def __init__(self, dialog, tip, state, text="", auto=False):
        self.dialog, self.app, self.tip, self.state = dialog, dialog.app, tip, state
        # The switch the app flips itself (the user said yes), and whether it did yet.
        self.auto = tip.auto if auto else None
        self.flipped = self.flipping = False
        hook = getattr(dialog, "var_hook", None)
        self.hook = bool(hook is not None and hook.get())   # start with a hook (hooks.py)
        self.text = text or tip.title
        self.was_on = False if tip.watch_change else tip.is_on(state)
        self.place = winmap.BY_ID.get(tip.key)
        self.route = drive.route(self.place)
        self.nav, self.nav_blurs = [], []   # (voice, control or None) per step found by the app
        self.intro = self.intro_recorder = None   # {"path", "rect", "marks", "length"}
        self.recorder = self.box = None
        self.step = 0
        self.restored = False
        self.phase = "record"
        self.hwnd, self.rect, self.controls, self.before = 0, None, [], {}
        self.clicks, self.pressed, self.found = [], False, {}
        self.marks, self.length, self.path, self.left = [], 0.0, "", {}
        self.folder = os.path.join(RECORDINGS_DIR, time.strftime("%Y-%m-%d_%H%M%S"))
        atexit.register(self.restore)   # even if the app is closed mid-recording

    def restore(self):
        """Put the setting back exactly as it was read. Safe to call more than once."""
        if self.restored:
            return
        self.restored = True
        try:
            if self.tip.read() != self.state:
                self.tip.restore(self.state)
                self.app.call_soon(lambda: self.app.append_log(
                    "↩️ The setting is back as it was before the recording."))
        except OSError as exc:
            message = (f"⚠️ Couldn't put the setting back ({exc}). Change it back by hand in "
                       "the window the tip opened.")
            self.app.call_soon(lambda: self.app.append_log(message))

    # -- recording -----------------------------------------------------------------------
    def open(self):
        # The dialog is modal (centre_on grabs all clicks): hidden, it kept the grab, and every
        # click on the guide box was thrown away (2026-10-22). Let go of it first.
        self.dialog.grab_release()
        self.dialog.withdraw()
        self.app.iconify()   # out of the way of the window being recorded

        def work():
            try:
                hwnd = 0
                if self.route == "settings":
                    # Opened again, Settings stays on the page planning left it on: closed
                    # first, it opens fresh on its home page (2026-10-26).
                    drive.close(self.dialog.page_hwnd)
                    time.sleep(1.0)
                    hwnd = screen.open_window(["explorer", "ms-settings:"])
                elif self.route in ("run", "taskbar"):
                    hwnd = self.find_outside()
                if not hwnd:
                    self.route = "direct" if self.route else ""
                    hwnd = screen.open_window(self.tip.opens)
            except OSError as exc:
                message = f"Couldn't open the window ({exc})."
                self.app.call_soon(lambda: self.fail(message))
                return
            self.app.call_soon(lambda: self.start(hwnd))

        threading.Thread(target=work, daemon=True).start()

    def stopped(self):
        return self.phase == "done"

    def flip_on(self, index):
        """Worker: the app flips the tip's switch itself (the user said yes). follow() sees the
        change and ends the step. Written down first, so a crash still gets it put back."""
        time.sleep(1.5)   # the viewer sees the page before the click
        if self.step != index or self.phase != "record":
            return
        was = "Off" if self.tip.auto_to == "On" else "On"

        def clicking():
            # Written down only when the app really clicks: a switch it never found (hidden
            # in a closed section) left a false "not put back" (2026-10-27).
            putback.write(SETTINGS_DIR, self.tip.key, self.auto.name, was)
            self.flipped = True

        if not drive.flip(self.hwnd, self.auto.name, self.tip.auto_to,
                          lambda: self.phase != "record", clicking=clicking):
            name = self.auto.name
            self.app.call_soon(lambda: self.app.append_log(
                f"ℹ️ The app couldn't turn {name} itself: click it, then press Next."))

    def auto_step(self, index, step, action):
        """Worker: a step the app does itself. "open" clicks a "Show more..." button, "show"
        points at a switch that is already right, "hold" waits: then on to the next step.
        "point" only shows the user where to click. A person taking over gets the step back."""
        def alive():
            return self.step == index and self.phase == "record"

        def go_on():
            self.app.call_soon(lambda: alive() and self.next())

        time.sleep(HOLD_WAIT if action == "hold" else 1.5)
        if not alive():
            return
        if action == "hold":
            go_on()
            return
        try:
            if action == "open":
                more, _after = drive.open_more(self.hwnd, self.rect, name=step.target,
                                               stop=lambda: not alive(), clicks_only=True)
                if more is None:
                    raise drive.Interrupted(f"{step.target} isn't on the page")
                go_on()
                return
            control = uiread.find(uiread.read(self.hwnd), step.target)
            if control is not None:
                drive.Driver(self.hwnd, lambda: not alive(), clicks_only=True).point(control)
            if action == "show":
                time.sleep(HOLD_WAIT)
                go_on()
        except drive.Interrupted:
            if action == "open":
                name = step.target
                self.app.call_soon(lambda: alive() and self.box is not None and self.box.show(
                    f"Click {name}, then press Next.", "Next"))

    def put_back_switch(self):
        """Worker: switch back what the app flipped, and check it. Safe to call twice."""
        if not self.flipped:
            return
        self.flipped = False
        entry = putback.read(SETTINGS_DIR) or {}
        name, was = self.auto.name, entry.get("state", "Off" if self.tip.auto_to == "On" else "On")
        if drive.flip(self.hwnd, name, was):
            putback.clear(SETTINGS_DIR)
            self.app.call_soon(lambda: self.app.append_log(
                f"↩️ {name} is back {was.lower()}, as it was before the recording."))
        else:   # the journal stays: the next start tries again
            self.app.call_soon(lambda: self.app.append_log(
                f"⚠️ Couldn't switch {name} back to {was.lower()} by itself. Switch it back in "
                "the window the tip opened."))

    def did(self, voice, control):
        """Worker: the app finished one step of finding the page."""
        recorder = self.intro_recorder or self.recorder
        if recorder is not None:   # None: cancelled meanwhile
            recorder.mark()
            self.nav.append((voice, control))

    def find_outside(self):
        """Worker: the taskbar's menu or the Run box, recorded on their own; the page or 0."""
        drive.close(self.dialog.page_hwnd)   # opened while planning: let the app open it
        time.sleep(0.5)
        os.makedirs(self.folder, exist_ok=True)
        intro = {"path": os.path.join(self.folder, "intro.mp4")}

        def record(rect):
            if screen.can_record(rect):
                return False
            intro["rect"] = rect
            self.intro_recorder = screen.Recorder(rect, intro["path"])
            return True

        find = drive.taskbar_menu if self.route == "taskbar" else drive.run_box
        try:
            hwnd = find(self.place, record, self.did, self.stopped)
        except drive.Interrupted:
            hwnd = 0
        recorder, self.intro_recorder = self.intro_recorder, None
        if recorder is not None:
            intro["length"] = recorder.stop()
            intro["marks"] = recorder.marks[:len(self.nav) - 1]   # the last step ends the file
        if hwnd and self.nav and recorder is not None:
            self.intro = intro
        else:
            self.nav = []
            if recorder is not None:
                try:
                    os.remove(intro["path"])
                except OSError:
                    pass
        return hwnd

    def find_in_settings(self):
        """Worker: search Settings for the page and click it, or open it directly."""
        home = uiread.read(self.hwnd)
        self.nav_blurs = uiread.private(home)   # the account card on Settings' home page
        try:
            found = drive.settings_search(self.hwnd, self.place, self.did, self.stopped)
        except drive.Interrupted:
            found = False
        if self.phase != "auto":
            return
        planned = uiread.recall(cache.Cache(CACHE_ROOT, "winui"), self.place.id)
        if found and not drive.same_page(drive.settled(self.hwnd), planned):
            found = False   # the result led elsewhere: the planned page is opened after it
        if not found:
            self.route = "direct"
            subprocess.Popen(self.place.command(), creationflags=uiread.NO_WINDOW)
            time.sleep(2.0)
            self.did(f"Open {self.place.name}.", None)
        controls = drive.settled(self.hwnd)   # read too early, switches show the wrong way
        self.app.call_soon(lambda: self.after_nav(controls))

    def after_nav(self, controls):
        if self.phase != "auto":
            return
        self.phase = "record"
        if controls:
            self.controls, self.before = controls, self.states_before(controls)
        self.begin_steps()

    def states_before(self, controls):
        """How the page's switches were before recording: as read while planning first (the
        page had settled then), else as read now."""
        return {**uiread.states(controls), **self.dialog.page_states}

    def begin_steps(self):
        """The tip's own steps, after the ones the app did to find the page."""
        if self.nav and self.step == 0:
            steps = [tips.Step(guide="", voice_off=voice) for voice, _c in self.nav]
            # A copy: the plan in the dialog stays as it was for another recording.
            self.tip = dataclasses.replace(self.tip, steps=steps + list(self.tip.steps))
            self.step = len(steps)
        self.show_step()

    def start(self, hwnd):
        self.hwnd, rect = hwnd, screen.foreground_rect() if hwnd else None
        if rect is None:
            self.fail("The window opened behind other windows, so there is nothing to record. "
                      "Close other windows and press Record again.")
            return
        why = screen.can_record(rect)
        if why:
            self.fail(why)
            return
        self.rect = rect
        self.controls = uiread.read(self.hwnd)
        if not self.controls:
            self.app.append_log("ℹ️ Windows didn't list this page's buttons (UI Automation is "
                                "blocked here), so the video won't zoom onto them.")
        self.before = self.states_before(self.controls)
        os.makedirs(self.folder, exist_ok=True)
        self.recorder = screen.Recorder(rect, os.path.join(self.folder, "recording.mp4"))
        self.box = GuideBox(self.app, rect, self.next, self.cancel)
        if self.route == "settings":
            self.phase = "auto"
            self.track_clicks()
            self.box.show("Watch: the app is finding the page. Don't touch the mouse.", "Next")
            threading.Thread(target=self.find_in_settings, daemon=True).start()
            return
        self.track_clicks()
        self.begin_steps()

    def track_clicks(self):
        """Where the mouse is pressed while recording, 30 times a second (window's thread)."""
        if self.recorder is None or self.phase not in ("record", "auto"):
            return
        down = screen.mouse_down()
        if down and not self.pressed:
            where = screen.cursor()
            if where:
                self.clicks.append((self.recorder.clock() - self.recorder.began, *where))
                # A click on the step's own button ends the step, like a flipped switch does:
                # a button ("Run Storage Sense now") has no state to watch (2026-10-26).
                button, index = self.found.get(self.step), self.step
                if self.phase == "record" and button is not None and \
                        button.x <= where[0] < button.x + button.w and \
                        button.y <= where[1] < button.y + button.h:
                    self.app.after(800, lambda: self.step == index and self.next())
        self.pressed = down
        self.app.after(33, self.track_clicks)

    def show_step(self):
        step = self.tip.steps[self.step]
        last = self.step == len(self.tip.steps) - 1
        guide = step.guide
        if step.waits_for_change and self.was_on:
            guide = "It's already on here: point at it with the mouse, then press Next."
        mine = self.auto is not None and step.target == self.auto.name and not self.flipping
        # What the app does itself in this step; only what it may not do is left to the user.
        action = ""
        if mine:
            guide = (f"Watch: the app turns {self.auto.name} {self.tip.auto_to.lower()}. "
                     "Don't touch the mouse.")
        elif step.nav:
            action = "open"                 # "Show more...": only navigation
        elif step.target and (step.already or (step.waits_for_change and self.was_on)):
            action = "show"                 # already the way the tip wants: pointed at
        elif step.target and not last:
            action = "point"                # the user clicks it: the app shows where
            guide = f"{guide} (The mouse shows where; click it yourself.)"
        elif not step.target and not step.waits_for_change and not last:
            action = "hold"                 # nothing to click: long enough for its voice
        self.box.show(guide, "Finish" if last else "Next")
        if mine:
            self.flipping = True
            threading.Thread(target=self.flip_on, args=(self.step,), daemon=True).start()
        elif action:
            threading.Thread(target=self.auto_step, args=(self.step, step, action),
                             daemon=True).start()
        if last and step.guide == tips.CLOSING:
            index = self.step   # nothing left to do: long enough for its voice, then finish
            self.app.after(CLOSING_WAIT, lambda: self.step == index and self.phase == "record"
                           and self.recorder is not None and self.finish())
        if self.tip.watch_change or self.tip.key in tips.TIPS:
            if step.waits_for_change and not self.was_on:
                self.watch()   # a setting with a Windows command: read it directly
        else:
            self.follow(self.step)

    def watch(self):
        """Poll the setting until the user has turned it on; that ends the step."""
        if self.recorder is None or self.phase != "record":
            return
        step = self.step
        try:
            now = self.tip.read()
            on = now != self.state if self.tip.watch_change else self.tip.is_on(now)
        except OSError:
            on = False
        if on:
            self.app.after(600, lambda: self.step == step and self.next())
        else:
            self.app.after(300, self.watch)

    def follow(self, index):
        """Background: re-read the page during step ``index``; find its button, watch its switch.

        The button is found by name, or for the main step by the tip's words (a switch that
        only appeared after the user opened a section). A switch that flips ends the step.
        """
        step = self.tip.steps[index]
        main = step.waits_for_change or (not step.target and index == len(self.tip.steps) - 2)
        words = tips.match_words(self.text, winmap.BY_ID.get(self.tip.key))

        def work():
            first = None
            while self.recorder is not None and self.phase == "record" and self.step == index:
                controls = uiread.read(self.hwnd)
                if controls:
                    self.controls = controls
                    for name, state in uiread.states(controls).items():
                        self.before.setdefault(name, state)   # first seen: what to put back
                    button = uiread.find(controls, step.target) if step.target else None
                    if button is None and main and not step.already:
                        button = uiread.best_match(uiread.targets(controls, self.rect), words)
                    if button is not None:
                        self.found[index] = button
                        if step.waits_for_change and button.state in ("On", "Off"):
                            if first is None:
                                first = button.state
                            elif button.state != first:
                                self.app.call_soon(lambda: self.step == index and self.next(),
                                                   600)
                                return
                time.sleep(0.3)

        threading.Thread(target=work, daemon=True).start()

    def next(self):
        if self.phase == "putback":
            self.skip_putback()
            return
        if self.recorder is None or self.phase != "record":
            return
        if self.step == len(self.tip.steps) - 1:
            self.finish()
            return
        self.recorder.mark()
        self.step += 1
        self.show_step()

    def _close(self):
        """Stop recording and close the box (cancel and fail only)."""
        recorder, self.recorder = self.recorder, None
        if recorder is not None:
            threading.Thread(target=recorder.stop, daemon=True).start()
        if self.box is not None:
            self.box.destroy()
            self.box = None
        self.app.deiconify()

        def work():
            self.restore()
            self.put_back_switch()

        threading.Thread(target=work, daemon=True).start()

    def cancel(self):
        if self.phase == "putback":
            self.skip_putback()
            return
        if self.phase not in ("record", "auto"):
            return   # saving or done: the recording is kept and cut
        self.phase = "done"   # also stops the app finding the page (drive checks stopped())
        self._close()
        shutil.rmtree(self.folder, ignore_errors=True)
        self.dialog.deiconify()
        self.dialog.say("Cancelled. Nothing was kept, and the setting is as it was.")

    def fail(self, message):
        self.phase = "done"
        self._close()
        self.dialog.deiconify()
        self.dialog.say(message, ERR_COLOR)

    # -- after recording ------------------------------------------------------------------
    def finish(self):
        """Stop recording (in the background), then walk the user through putting things back."""
        recorder, self.recorder = self.recorder, None
        self.phase = "saving"
        self.marks = list(recorder.marks)
        self.box.show("Saving the recording...", "Skip")

        def work():
            self.length, self.path = recorder.stop(), recorder.path
            self.restore()   # settings with a Windows command go back by themselves
            self.put_back_switch()   # ...and a switch the app flipped, by the app
            self.app.call_soon(self.start_putback)

        threading.Thread(target=work, daemon=True).start()

    def start_putback(self):
        self.phase = "putback"
        self.check_putback()

    def check_putback(self):
        def work():
            controls = uiread.read(self.hwnd)
            left = uiread.changed(self.before, uiread.states(controls)) if controls else {}
            self.app.call_soon(lambda: self.show_putback(left))

        threading.Thread(target=work, daemon=True).start()

    def show_putback(self, left):
        if self.phase != "putback":
            return
        if not left:
            self.done_putback()
            return
        self.left = left
        lines = [f"Switch {name} back to {was}" for name, was in left.items()]
        if self.box is not None:
            self.box.show("Recording stopped. Now put back what you changed:\n"
                          + "\n".join(lines), "Skip")
        self.app.after(800, self.check_putback)

    def skip_putback(self):
        if self.left:
            self.app.append_log("⚠️ Still changed after the recording: "
                                + "; ".join(f"{n} (was {w})" for n, w in self.left.items())
                                + ". Switch them back in the window the tip opened.")
        self.done_putback()

    def done_putback(self):
        if self.phase == "done":
            return
        self.phase = "done"
        if self.box is not None:
            self.box.destroy()
            self.box = None
        self.app.deiconify()
        self.cut()

    @staticmethod
    def _inside(control, rect):
        """``control``'s rectangle in the recorded ``rect``'s pixels, or None."""
        return None if control is None else (control.x - rect[0], control.y - rect[1],
                                             control.w, control.h)

    def focuses(self):
        """Per step of the page's recording: its button's rectangle in the window, or where
        the user last clicked. The steps of an intro recording come before these."""
        left, top = self.rect[0], self.rect[1]
        offset = len(self.nav) if self.intro else 0
        out = []
        for index, span in enumerate(screen.spans(self.marks, self.length)):
            index += offset
            if index < len(self.nav):
                out.append(self._inside(self.nav[index][1], self.rect))
                continue
            step = self.tip.steps[index] if index < len(self.tip.steps) else None
            button = self.found.get(index) or (
                uiread.find(self.controls, step.target) if step and step.target else None)
            if button is not None:
                out.append((button.x - left, button.y - top, button.w, button.h))
            else:
                out.append(screen.last_click(self.clicks, span, self.rect))
        return out

    def cut(self):
        w, h, _o, _b = resolve_target(self.app.var_aspect.get(), self.app.var_resolution.get())
        if self.dialog.winfo_exists():
            self.dialog.destroy()
        self.app.append_log("✂️ Cutting the recording into scenes...")
        left, top = self.rect[0], self.rect[1]
        blurs = [(x - left, y - top, bw, bh) for x, y, bw, bh in uiread.private(self.controls)]
        # Settings' home page, searched in the page's own recording, shows the account card.
        home = blurs + [(x - left, y - top, bw, bh) for x, y, bw, bh in self.nav_blurs]
        searched = 0 if self.intro else len(self.nav)
        focuses = self.focuses()
        intro = self.intro

        def work():
            try:
                clips = []
                if intro is not None:
                    rect = intro["rect"]
                    clips += screen.cut_all(
                        intro["path"], intro["marks"], intro["length"], (w, h),
                        os.path.join(self.folder, "intro"), window=rect[2:],
                        focuses=[self._inside(c, rect) for _v, c in self.nav])
                for i, span in enumerate(screen.spans(self.marks, self.length)):
                    clips.append(screen.cut(
                        self.path, span, (w, h), os.path.join(self.folder, f"step_{i + 1}.mp4"),
                        window=self.rect[2:], focus=focuses[i],
                        blurs=home if i < searched else blurs))
                text = tips.script(self.tip, self.was_on, clips,
                                   self.opening(clips) if self.hook else ())
            except Exception as exc:  # noqa: BLE001
                message = f"⚠️ Couldn't cut the recording ({exc})."
                self.app.call_soon(lambda: self.app.append_log(message))
                return
            self.app.call_soon(lambda: self.use(text))

        threading.Thread(target=work, daemon=True).start()

    def opening(self, clips):
        """Worker: the hook and the "Right now we're going to..." scenes (hooks.py) over the
        result frame (the switch zoomed in, ringed), blurred: [(picture, voice, card)]."""
        main = next((i for i, s in enumerate(self.tip.steps)
                     if self.auto is not None and s.target == self.auto.name), None)
        if main is None:
            main = next((i for i, s in enumerate(self.tip.steps)
                         if s.target and (s.waits_for_change or s.already)), len(clips) - 1)
        clip = clips[min(main, len(clips) - 1)]
        pictures = []
        # Softly: Settings is dark already, and a strong blur left a black frame (2026-10-26).
        looks = (("intro_hook.png", "boxblur=6:1,eq=brightness=0.04:saturation=1.3"),
                 ("intro_explain.png", "boxblur=2:1"))
        for name, look in looks:
            picture = os.path.join(self.folder, name)
            # The cut clip ends on its held last frame: the zoom and ring have arrived there.
            mastering.run_ffmpeg(["-y", "-sseof", "-0.2", "-i", clip, "-frames:v", "1", "-vf",
                                  look, picture])
            pictures.append(picture)
        template, voice, card = hooks.pick(hooks.kind_of(self.place),
                                           hooks.seconds(len(self.tip.steps)),
                                           hooks.remembered(SETTINGS_DIR))
        if self.place is None and writer.installed():   # Windows search: no lines fit it
            try:
                with writer.Session() as session:
                    written = " ".join(session.hook(self.text).split())
                if hooks.check(written):
                    template, voice, card = written, written, "QUICK WINDOWS TIP"
            except Exception:  # noqa: BLE001 - the ready-made line stays
                pass
        hooks.remember(SETTINGS_DIR, template)
        return [(pictures[0], voice, card),
                (pictures[1], hooks.explain(self.tip, self.text, self.place), "")]

    def use(self, text):
        self.app.script_box.delete("1.0", "end")
        self.app.script_box.insert("1.0", text)
        self.app._commit_script()
        self.app.append_log("🎬 The recording is in the Storyboard: check it, then Make video.")
        self.app.select_page("board")

class WhatsNewDialog(ctk.CTkToplevel):
    """After an update, once: what is new in this version, with "Don't show again"."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("What's new")
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)
        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        ctk.CTkLabel(card, text=f"What's new in {APP_VERSION}", font=app.font_title,
                     text_color=TEXT).pack(anchor="w", padx=18, pady=(16, 8))
        for line in WHATS_NEW:
            ctk.CTkLabel(card, text=f"•  {line}", font=app.font_body, text_color=TEXT_MUTED,
                         wraplength=440, justify="left", anchor="w").pack(fill="x", padx=18,
                                                                          pady=2)
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(12, 16))
        self.var_never = tk.BooleanVar(self, False)
        ctk.CTkCheckBox(row, text="Don't show again", variable=self.var_never,
                        font=app.font_tiny, text_color=TEXT_DIM, checkbox_width=16,
                        checkbox_height=16, border_width=1).pack(side="left")
        ctk.CTkButton(row, text="OK", width=96, height=32, corner_radius=4, font=app.font_body,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ACCENT_TEXT,
                      command=self.close).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self.close)
        centre_on(self, app)

    def close(self):
        self.app.whats_new_seen = APP_VERSION
        if self.var_never.get():
            self.app.whats_new_off = True
        save_settings(self.app._collect_settings())
        self.destroy()


def show_whats_new(seen: str, off: bool, first_run: bool) -> bool:
    """Once per version, after an update only, unless switched off."""
    return not (off or first_run) and seen != APP_VERSION


class TourDialog(ctk.CTkToplevel):
    """First run only: four short screens on how the app is used.

    Never shown after an update (load_settings marks it seen when a settings
    file already exists). Feedback > Show the tour brings it back.
    """

    def __init__(self, app):
        super().__init__(app)
        self.app, self.step = app, 0
        self.title("Welcome to AI Video Studio")
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.finish)
        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        self.glyph = ctk.CTkLabel(card, text="", font=app.font_icon_lg, text_color=ACCENT)
        self.glyph.pack(pady=(22, 6))
        self.heading = ctk.CTkLabel(card, text="", font=app.font_title, text_color=TEXT)
        self.heading.pack()
        self.body = ctk.CTkLabel(card, text="", font=app.font_body, text_color=TEXT_MUTED,
                                 wraplength=400, justify="center", width=440, height=96)
        self.body.pack(padx=20, pady=(8, 4))
        self.count = ctk.CTkLabel(card, text="", font=app.font_tiny, text_color=TEXT_DIM)
        self.count.pack()
        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.pack(pady=(10, 18))
        ctk.CTkButton(buttons, text="Skip", width=96, height=34, corner_radius=4,
                      font=app.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                      border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                      command=self.finish).pack(side="left", padx=(0, 8))
        self.next_button = ctk.CTkButton(
            buttons, text="Next", width=130, height=34, corner_radius=4, font=app.font_bold,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ACCENT_TEXT, command=self.next)
        self.next_button.pack(side="left")
        self.show()
        centre_on(self, app)

    def show(self):
        glyph, heading, body = TOUR[self.step]
        self.glyph.configure(text=self.app.icon(glyph))
        self.heading.configure(text=heading)
        self.body.configure(text=body)
        self.count.configure(text=f"{self.step + 1} of {len(TOUR)}")
        self.next_button.configure(text="Got it" if self.step == len(TOUR) - 1 else "Next")

    def next(self):
        if self.step == len(TOUR) - 1:
            self.finish()
            return
        self.step += 1
        self.show()

    def finish(self):
        """Seen once is seen: remember it, then go on to the key setup if that is still due."""
        app = self.app
        app.tour_seen = True
        save_settings(app._collect_settings())
        self.destroy()
        if not app._has_footage_key() and not app.welcome_seen and not imagegen.installed():
            WelcomeDialog(app)


class SlowPicturesDialog(ctk.CTkToplevel):
    """Before a long render on a slow PC: how long, and quicker ways to do it."""

    def __init__(self, app, count):
        super().__init__(app)
        self.app = app
        self.title("This will take a while")
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)
        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        each = app.seconds_per_picture()
        measured = "measured on this PC" if app.ai_fast_seconds else "a guess until the first one"

        def minutes(seconds):
            return f"about {max(1, round(seconds / 60))} min"

        ctk.CTkLabel(card, text=f"{count} AI pictures: {minutes(each * count)}",
                     font=app.font_title, text_color=TEXT).pack(anchor="w", padx=18, pady=(16, 4))
        ctk.CTkLabel(card, text=f"This PC makes an AI picture in {minutes(each)} ({measured}). "
                                "The rest of the video takes a few minutes more. Closing other "
                                "busy programs (a game, many browser tabs) helps: in a test, a "
                                "busy PC took half as long again.",
                     font=app.font_body, text_color=TEXT_MUTED, wraplength=420, justify="left",
                     ).pack(anchor="w", padx=18, pady=(0, 12))

        def option(text, overrides):
            ctk.CTkButton(card, text=text, height=34, corner_radius=4, font=app.font_body,
                          fg_color=FIELD_BG, hover_color=CARD_HOVER, border_width=1,
                          border_color=FIELD_BORDER, text_color=TEXT, anchor="w",
                          command=lambda: self.choose(overrides)).pack(fill="x", padx=18, pady=3)

        option(f"Make all {count}  ({minutes(each * count)})", {})
        if count > SLOW_AI_SCENES:
            option(f"Make {SLOW_AI_SCENES} pictures and use them again for the other scenes  "
                   f"({minutes(each * SLOW_AI_SCENES)})", {"ai_reuse": SLOW_AI_SCENES})
        if app.ai_speed_now() != imagegen.FAST:
            fast = app.seconds_per_picture(imagegen.FAST)
            option(f"Use the Fast size  ({minutes(fast * count)})",
                   {"ai_images_speed": imagegen.FAST})
        ctk.CTkButton(card, text="Cancel", height=34, corner_radius=4, font=app.font_body,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ACCENT_TEXT,
                      command=self.destroy).pack(fill="x", padx=18, pady=(10, 16))
        centre_on(self, app)

    def choose(self, overrides):
        self.destroy()
        self.app.start_after_slow_choice(overrides)


class BatchDialog(ctk.CTkToplevel):
    """Make several: one text per block, each becomes its own video and project."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Make several videos")
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)
        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        template = app.var_template.get()
        ctk.CTkLabel(card, text="Make several videos", font=app.font_title, text_color=TEXT,
                     ).pack(anchor="w", padx=18, pady=(16, 2))
        ctk.CTkLabel(card, text="Paste one text or topic for each video, with an empty line "
                                "between them. They are made one after another, each as its "
                                "own project, with the " + (f"{template} template."
                                                           if template != NO_TEMPLATE else
                                                           "answers New from text last used."),
                     font=app.font_tiny, text_color=TEXT_DIM, wraplength=560, justify="left",
                     ).pack(anchor="w", padx=18, pady=(0, 8))
        self.box = ctk.CTkTextbox(card, width=560, height=260, font=app.font_body,
                                  corner_radius=4, fg_color=FIELD_BG, text_color=TEXT,
                                  border_width=0, wrap="word")
        self.box.pack(padx=18)
        self.box.bind("<KeyRelease>", self._count)
        self.status = ctk.CTkLabel(card, text="", font=app.font_tiny, text_color=TEXT_DIM,
                                   anchor="w")
        self.status.pack(fill="x", padx=18, pady=(6, 8))
        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.pack(fill="x", padx=18, pady=(0, 16))
        ctk.CTkButton(buttons, text="Start", width=110, height=34, corner_radius=4,
                      font=app.font_bold, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      text_color=ACCENT_TEXT, command=self.start).pack(side="right")
        ctk.CTkButton(buttons, text="Cancel", width=96, height=34, corner_radius=4,
                      font=app.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                      border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                      command=self.destroy).pack(side="right", padx=(0, 8))
        centre_on(self, app)
        self.box.focus_set()

    def texts(self):
        return draft.batch_texts(self.box.get("1.0", "end"))

    def _count(self, _event=None):
        n = len(self.texts())
        self.status.configure(text=f"{n} video{'s' if n != 1 else ''}" + (
            f" (only the first {MAX_BATCH} are made)" if n > MAX_BATCH else ""),
            text_color=TEXT_DIM)

    def start(self):
        texts = self.texts()
        if not texts:
            self.status.configure(text="Paste some text first.", text_color=ERR_COLOR)
            return
        self.destroy()
        self.app.start_batch(texts[:MAX_BATCH])


def mci(command: str) -> str:
    """Windows' own sound recorder (winmm): no library needed. Raises with its message."""
    buffer = ctypes.create_unicode_buffer(256)
    error = ctypes.windll.winmm.mciSendStringW(command, buffer, 256, None)
    if error:
        text = ctypes.create_unicode_buffer(256)
        ctypes.windll.winmm.mciGetErrorStringW(error, text, 256)
        raise OSError(text.value or f"recording error {error}")
    return buffer.value


def short_path(folder: str) -> str:
    """The folder's old 8.3 name: the recorder refuses long or unusual paths when saving."""
    buffer = ctypes.create_unicode_buffer(512)
    return buffer.value if ctypes.windll.kernel32.GetShortPathNameW(folder, buffer, 512)         else folder


class MyVoiceDialog(ctk.CTkToplevel):
    """Make my voice (beta): record six sentences; KittenTTS 2 in the voice pack then speaks
    scripts in a voice like yours (vidgen.myvoice, vidgen.voicepack)."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Make my voice (beta)")
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.recording = False
        self.stopped = False   # closing the window stops the pack install (what is done is kept)
        self.path = os.path.join(myvoice.MY_FOLDER, myvoice.RECORDING)
        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        ctk.CTkLabel(card, text="Make my voice (beta)", font=app.font_title, text_color=TEXT,
                     ).pack(anchor="w", padx=18, pady=(16, 2))
        ctk.CTkLabel(card, text="Press Start, read these six sentences out loud at your normal "
                                "speed, then press Stop. A quiet room and a headset microphone "
                                "work best. The recording and the voice made from it stay on "
                                "this PC.", font=app.font_tiny, text_color=TEXT_DIM,
                     wraplength=520, justify="left").pack(anchor="w", padx=18, pady=(0, 8))
        for n, line in enumerate(myvoice.SENTENCES, 1):
            ctk.CTkLabel(card, text=f"{n}.  {line}", font=app.font_body, text_color=TEXT,
                         wraplength=520, justify="left", anchor="w").pack(fill="x", padx=24,
                                                                         pady=1)
        self.status = ctk.CTkLabel(card, text="" if myvoice.installed() else
                                   f"The first time, the voice pack is installed (about "
                                   f"{myvoice.SIZE / 1e9:.0f} GB: PyTorch for your graphics card "
                                   "and the voice model). It can be stopped and carried on "
                                   "later.", font=app.font_tiny,
                                   text_color=TEXT_DIM, wraplength=520, justify="left",
                                   anchor="w")
        self.status.pack(fill="x", padx=18, pady=(10, 6))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(0, 16))

        def button(text, command, accent=False):
            widget = ctk.CTkButton(row, text=text, height=34, corner_radius=4, font=app.font_body,
                                   fg_color=ACCENT if accent else FIELD_BG,
                                   hover_color=ACCENT_HOVER if accent else CARD_HOVER,
                                   text_color=ACCENT_TEXT if accent else TEXT,
                                   text_color_disabled=ACCENT_TEXT if accent else TEXT_DIM,
                                   border_width=0 if accent else 1, border_color=FIELD_BORDER,
                                   command=command)
            widget.pack(side="left", padx=(0, 6))
            return widget

        self.record_button = button("Start recording", self.toggle)
        self.play_button = button("Play it back", self.play)
        self.make_button = button("Make my voice", self.make, accent=True)
        if myvoice.installed():
            button("Remove voice pack", self.remove_pack)
        if not os.path.isfile(self.path):
            self.play_button.configure(state="disabled")
            self.make_button.configure(state="disabled")
        centre_on(self, app)

    def toggle(self):
        try:
            if not self.recording:
                os.makedirs(myvoice.MY_FOLDER, exist_ok=True)
                mci("open new type waveaudio alias myvoice")
                mci("set myvoice bitspersample 16 channels 1 samplespersec 22050 "
                    "bytespersec 44100 alignment 2")
                mci("record myvoice")
                self.recording = True
                self.record_button.configure(text="Stop recording")
                for b in (self.play_button, self.make_button):
                    b.configure(state="disabled")
                self.status.configure(text="Recording... read the six sentences.",
                                      text_color=ERR_COLOR)
            else:
                mci("stop myvoice")
                saved = os.path.join(short_path(myvoice.MY_FOLDER), "rec.wav")
                mci(f'save myvoice "{saved}"')
                mci("close myvoice")
                os.replace(saved, self.path)
                self.recording = False
                self.record_button.configure(text="Record again")
                for b in (self.play_button, self.make_button):
                    b.configure(state="normal")
                self.status.configure(text="Recorded. Play it back if you like, then press "
                                           "Make my voice.", text_color=TEXT_DIM)
        except OSError as exc:
            self.recording = False
            try:
                mci("close myvoice")
            except OSError:
                pass
            self.status.configure(text=f"The microphone could not be used: {exc}",
                                  text_color=ERR_COLOR)

    def play(self):
        import winsound

        winsound.PlaySound(self.path, winsound.SND_FILENAME | winsound.SND_ASYNC)

    def make(self):
        self.make_button.configure(state="disabled", text="Making it...")
        self.record_button.configure(state="disabled")
        app = self.app

        def worker():
            try:
                if not myvoice.installed():
                    def progress(text, fraction):
                        app._queue.put(("myvoice", {"dialog": self,
                                                    "progress": (text, fraction)}))
                    myvoice.download(progress, should_stop=lambda: self.stopped)
                app._queue.put(("myvoice", {"dialog": self, "progress": (
                    "Learning your voice and saying a test line (about a minute)...", 1.0)}))
                myvoice.make(self.path)
                app._queue.put(("myvoice", {"dialog": self, "done": True}))
            except Exception as exc:  # noqa: BLE001 - shown in the window
                app._queue.put(("myvoice", {"dialog": self, "error": str(exc)}))

        threading.Thread(target=worker, daemon=True).start()

    def on_event(self, payload):
        if not self.winfo_exists():
            return
        if "progress" in payload:
            text, fraction = payload["progress"]
            self.status.configure(text=f"{text} ({fraction * 100:.0f}%)" if fraction < 1 else text,
                                  text_color=TEXT_DIM)
            return
        self.make_button.configure(state="normal", text="Make my voice")
        self.record_button.configure(state="normal")
        if payload.get("error"):
            self.status.configure(text=payload["error"], text_color=ERR_COLOR)
            return
        self.app.my_voice_made()
        self.status.configure(text="Done: this is how it sounds. \"My voice (beta)\" is now in "
                                   "the voice list and chosen.", text_color=OK_COLOR)
        sample = os.path.join(myvoice.MY_FOLDER, myvoice.SAMPLE)
        if os.path.isfile(sample):
            import winsound

            winsound.PlaySound(sample, winsound.SND_FILENAME | winsound.SND_ASYNC)

    def remove_pack(self):
        """Delete the voice pack (about 7 GB); My voice falls back to the normal voice."""
        myvoice.close_speaker()
        voicepack.remove()
        self.app.my_voice_made()   # refreshes the voice lists: My voice drops out
        if self.app.var_voice.get() == voices.persona(voices.MY_VOICE).label:
            self.app.var_voice.set(voices.persona(voices.DEFAULT_ID).label)
        self.status.configure(text="The voice pack was removed. Your recording is kept.",
                              text_color=TEXT_DIM)

    def close(self):
        self.stopped = True
        if self.recording:
            try:
                mci("close myvoice")
            except OSError:
                pass
        self.destroy()


class TranslateDialog(ctk.CTkToplevel):
    """The same video in another language: the Smart writer translates the lines into a copy."""

    def __init__(self, app, project):
        super().__init__(app)
        self.app, self.project = app, project
        self.title("Make it in another language")
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)
        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        ctk.CTkLabel(card, text=f"\"{project.get('title')}\" in another language",
                     font=app.font_title, text_color=TEXT, wraplength=420, justify="left",
                     ).pack(anchor="w", padx=18, pady=(16, 8))
        self.var_language = tk.StringVar(self, voices.LANGUAGES[0])
        ctk.CTkOptionMenu(
            card, values=list(voices.LANGUAGES), variable=self.var_language, width=260,
            height=32, corner_radius=4, font=app.font_body, fg_color=FIELD_BG,
            button_color=FIELD_BG, button_hover_color=CARD_HOVER, text_color=TEXT,
            dropdown_fg_color=CARD_BG, dropdown_text_color=TEXT,
            dropdown_hover_color=CARD_HOVER, dropdown_font=app.font_body,
            dynamic_resizing=False).pack(anchor="w", padx=18)
        ctk.CTkLabel(card, text="The lines are translated by the Smart writer, a small AI on "
                                "this PC. Its translations are understandable but have "
                                "mistakes, so the copy opens in the Storyboard first: check the "
                                "lines there (or have someone who speaks the language read "
                                "them) before you make the video. Pictures, clips and cards "
                                "stay the same; the voice and captions change.",
                     font=app.font_tiny, text_color=TEXT_DIM, wraplength=420, justify="left",
                     ).pack(anchor="w", padx=18, pady=(10, 8))
        self.status = ctk.CTkLabel(card, text="", font=app.font_tiny, text_color=TEXT_DIM,
                                   wraplength=420, justify="left", anchor="w")
        self.status.pack(fill="x", padx=18)
        self.go_button = ctk.CTkButton(
            card, text="Translate and open in the Storyboard", height=34, corner_radius=4,
            font=app.font_body, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color=ACCENT_TEXT, command=self.go)
        self.go_button.pack(fill="x", padx=18, pady=(8, 3))
        ctk.CTkButton(card, text="Cancel", height=34, corner_radius=4, font=app.font_body,
                      fg_color=FIELD_BG, hover_color=CARD_HOVER, border_width=1,
                      border_color=FIELD_BORDER, text_color=TEXT,
                      command=self.destroy).pack(fill="x", padx=18, pady=(3, 16))
        centre_on(self, app)

    def go(self):
        ok, why = writer.ready()
        if not ok:
            self.status.configure(text=f"Translating needs the Smart writer. {why}",
                                  text_color=ERR_COLOR)
            return
        try:
            lines = [scene.voice for scene in parse_script(self.project["script"]) if scene.voice]
        except ScriptError as exc:
            self.status.configure(text=str(exc), text_color=ERR_COLOR)
            return
        language, gpu = self.var_language.get(), bool(self.app.var_writer_gpu.get())
        self.go_button.configure(state="disabled", text="Translating...")
        self.status.configure(text=f"The Smart writer is translating {len(lines)} lines into "
                                   f"{language}...", text_color=TEXT_DIM)

        def worker():
            try:
                with writer.Session(gpu=gpu) as session:
                    found = session.translate(lines, language)
                self.app._queue.put(("translated", {"dialog": self, "lines": found,
                                                    "language": language}))
            except Exception as exc:  # noqa: BLE001 - shown in the window
                self.app._queue.put(("translated", {"dialog": self, "error": str(exc),
                                                    "language": language}))

        threading.Thread(target=worker, daemon=True).start()

    def done(self, payload):
        """UI thread: the translation came back."""
        if not self.winfo_exists():
            return
        if payload.get("error"):
            self.go_button.configure(state="normal", text="Translate and open in the Storyboard")
            self.status.configure(text=payload["error"], text_color=ERR_COLOR)
            return
        language, app, project = payload["language"], self.app, self.project
        script = boards.with_lines(project["script"], payload["lines"])
        new = projects.copy(project["id"])
        data = projects.load(new)
        projects.save(new, dict(data, script=script, language=language,
                                title=f"{project.get('title') or 'Untitled video'} ({language})"))
        was = voices.persona((project.get("settings") or {}).get("voice") or app.var_voice.get())
        self.destroy()
        if app.open_project(new, {"voice": voices.for_language(language, was).id}):
            app.board_status.configure(
                text=f"Translated into {language} by the Smart writer. Read the lines and fix "
                     "any mistakes, then press Make video.", text_color=WARN_COLOR)


class CopyDialog(ctk.CTkToplevel):
    """Make a copy of a project in the other shape or with another voice."""

    def __init__(self, app, project):
        super().__init__(app)
        self.app, self.project = app, project
        self.title("Make a copy")
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)
        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        settings = project.get("settings") or {}
        self.shape = settings.get("aspect") if settings.get("aspect") in RATIO_OPTIONS \
            else RATIO_OPTIONS[0]
        try:
            self.pictures = sum(s.is_ai for s in parse_script(project["script"]))
        except ScriptError:
            self.pictures = 0

        ctk.CTkLabel(card, text=f"Copy of \"{project.get('title')}\"", font=app.font_title,
                     text_color=TEXT, wraplength=420, justify="left",
                     ).pack(anchor="w", padx=18, pady=(16, 10))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=4)
        ctk.CTkLabel(row, text="Shape", font=app.font_body, text_color=TEXT, width=60,
                     anchor="w").pack(side="left")
        self.var_shape = tk.StringVar(self, self.shape)
        FluentSegmented(row, app, list(RATIO_OPTIONS), self.var_shape, 200).pack(side="left")
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=4)
        ctk.CTkLabel(row, text="Voice", font=app.font_body, text_color=TEXT, width=60,
                     anchor="w").pack(side="left")
        self.var_voice = tk.StringVar(
            self, voices.persona(settings.get("voice") or app.var_voice.get()).label)
        ctk.CTkOptionMenu(
            row, values=VOICE_OPTIONS, variable=self.var_voice, width=340, height=32,
            corner_radius=4, font=app.font_body, fg_color=FIELD_BG, button_color=FIELD_BG,
            button_hover_color=CARD_HOVER, text_color=TEXT, dropdown_fg_color=CARD_BG,
            dropdown_text_color=TEXT, dropdown_hover_color=CARD_HOVER,
            dropdown_font=app.font_body, dynamic_resizing=False).pack(side="left")
        self.note = ctk.CTkLabel(card, text="", font=app.font_tiny, text_color=TEXT_DIM,
                                 wraplength=420, justify="left", anchor="w")
        self.note.pack(fill="x", padx=18, pady=(8, 10))
        self.var_shape.trace_add("write", self._note)
        self._note()

        def button(text, command, accent=False):
            ctk.CTkButton(card, text=text, height=34, corner_radius=4, font=app.font_body,
                          fg_color=ACCENT if accent else FIELD_BG,
                          hover_color=ACCENT_HOVER if accent else CARD_HOVER,
                          text_color=ACCENT_TEXT if accent else TEXT,
                          border_width=0 if accent else 1, border_color=FIELD_BORDER,
                          command=command).pack(fill="x", padx=18, pady=3)

        button("Make the video", lambda: self.go(render=True), accent=True)
        button("Open the copy in the Storyboard", lambda: self.go(render=False))
        button("Cancel", self.destroy)
        ctk.CTkFrame(card, fg_color="transparent", height=10).pack()
        centre_on(self, app)

    def _note(self, *_args):
        if self.pictures and self.var_shape.get() != self.shape:
            each = self.app.seconds_per_picture()
            self.note.configure(text=f"A new shape needs new AI pictures: {self.pictures} to "
                                     f"make, about {max(1, round(each * self.pictures / 60))} "
                                     "min. Stock clips are searched again.")
        else:
            self.note.configure(text="The original is kept as it is." + (
                " Its AI pictures are reused, so the copy is quick to make."
                if self.pictures and self.var_shape.get() == self.shape else ""))

    def go(self, render):
        new = projects.copy(self.project["id"])
        changes = {"aspect": self.var_shape.get(),
                   "voice": voices.persona(self.var_voice.get()).id}
        self.destroy()
        if self.app.open_project(new, changes) and render:
            self.app.start_render()


class ResultDialog(ctk.CTkToplevel):
    """Render complete: the video, playable in the window, with its sound.

    Tk has no video widget, so vidgen.preview decodes small frames and this
    window shows them on a timer while winsound plays the soundtrack. Play
    and stop only - the default player is one button away for anything more.
    """

    def __init__(self, app, title, message, paths, names=None):
        super().__init__(app)
        self.app = app
        self.paths = list(paths)
        self.path = self.paths[0]
        self.title(title)
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.reader = None
        self.job = None
        self.wav = None
        self.playing = False
        self.poster_photo = None
        self.photo = None

        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)

        size = self._load_poster()
        portrait = size is None or size[1] >= size[0]
        info = ctk.CTkFrame(card, fg_color="transparent")
        if size is not None:
            # A plain Tk label: frames are raw pixels, already scaled for the display.
            self.screen = tk.Label(card, image=self.poster_photo, bd=0, bg="#000000",
                                   width=size[0], height=size[1])
            if portrait:
                self.screen.pack(side="left", padx=(16, 0), pady=16)
                info.pack(side="left", fill="both", expand=True)
            else:
                self.screen.pack(padx=16, pady=(16, 0))
                info.pack(fill="both", expand=True)
        else:
            info.pack(fill="both", expand=True)

        header = ctk.CTkFrame(info, fg_color="transparent")
        header.pack(fill="x", padx=18, pady=(16, 4))
        ctk.CTkLabel(header, text=app.icon("status"), font=app.font_icon_lg,
                     text_color=OK_COLOR).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(header, text=title, font=app.font_title,
                     text_color=TEXT).pack(side="left")
        if len(self.paths) > 1:
            # Both versions, side by side in time: switch, play, compare.
            self.version_names = (list(names) if names and len(names) == len(self.paths) else
                                  [f"Version {n}" for n in range(1, len(self.paths) + 1)])
            self.var_version = tk.StringVar(value=self.version_names[0])
            FluentSegmented(info, app, self.version_names, self.var_version,
                            110 * len(self.paths)).pack(anchor="w", padx=18, pady=(6, 6))
            self.var_version.trace_add("write", self._select_version)
        ctk.CTkLabel(info, text=message, font=app.font_body, text_color=TEXT_MUTED,
                     justify="left", wraplength=300 if portrait else 440,
                     anchor="w").pack(fill="x", padx=18, pady=(2, 14))

        def button(text, command, accent=False):
            widget = ctk.CTkButton(
                info, text=text, height=34, corner_radius=4, font=app.font_body,
                fg_color=ACCENT if accent else FIELD_BG,
                hover_color=ACCENT_HOVER if accent else CARD_HOVER,
                text_color=ACCENT_TEXT if accent else TEXT,
                border_width=0 if accent else 1, border_color=FIELD_BORDER, command=command,
            )
            widget.pack(fill="x", padx=18, pady=(0, 6))
            return widget

        self.play_button = None
        if size is not None:
            self.play_button = button(self._play_label(), self.toggle, accent=True)
        button("Open in player", self.open_in_player)
        button("Show in folder", self.show_in_folder)
        if os.path.isfile(self._thumb()):
            button("Open thumbnail", lambda: os.startfile(self._thumb()))
        if app.project and projects.load(app.project):
            button("Make it in another language",
                   lambda: (self.close(), TranslateDialog(app, projects.load(app.project))))

        self.upload_box = ctk.CTkFrame(info, fg_color="transparent")
        self.upload_box.pack(fill="x", padx=18, pady=(4, 2))
        ctk.CTkLabel(self.upload_box, text="Title, description and tags for posting it",
                     font=app.font_tiny, text_color=TEXT_MUTED, anchor="w").pack(fill="x")
        self.upload_note = ctk.CTkLabel(self.upload_box, text="Writing them...",
                                        font=app.font_tiny, text_color=TEXT_DIM, anchor="w")
        self.upload_note.pack(fill="x")
        self.upload_fields = {}
        for key, height in (("title", 1), ("description", 3), ("tags", 2)):
            row = ctk.CTkFrame(self.upload_box, fg_color="transparent")
            box = ctk.CTkTextbox(row, height=24 + 18 * (height - 1), font=app.font_tiny,
                                 corner_radius=4, fg_color=FIELD_BG, text_color=TEXT,
                                 border_width=0, wrap="word")
            box.pack(side="left", fill="x", expand=True)
            ctk.CTkButton(row, text="Copy", width=54, height=24, corner_radius=4,
                          font=app.font_tiny, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                          border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                          command=lambda b=box: self._copy(b)).pack(side="left", padx=(6, 0))
            self.upload_fields[key] = (row, box)
        if app.last_upload:
            self.set_upload(app.last_upload)

        ctk.CTkLabel(info, text="Want something different? Say what to change.",
                     font=app.font_tiny, text_color=TEXT_MUTED, anchor="w",
                     ).pack(fill="x", padx=18, pady=(6, 2))
        self.comment = focus_accent(ctk.CTkEntry(
            info, height=32, corner_radius=4, font=app.font_body, fg_color=FIELD_BG,
            border_color=FIELD_BORDER, text_color=TEXT,
            placeholder_text="e.g. make it shorter, or: more exciting start"))
        self.comment.pack(fill="x", padx=18, pady=(0, 6))
        self.comment.bind("<Return>", lambda _event: self.change_it())
        button("Change it", self.change_it)

        if app.key_tip and not app._has_footage_key():
            # A reminder, not a wall: the video above is complete either way.
            tip = ctk.CTkFrame(info, fg_color="transparent")
            tip.pack(fill="x", padx=18, pady=(4, 6))
            ctk.CTkLabel(tip, text="Tip: with a free Pexels or Pixabay key the app can also "
                                   "use real video clips.", font=app.font_tiny,
                         text_color=TEXT_DIM, anchor="w", justify="left",
                         wraplength=300 if portrait else 440).pack(anchor="w")
            links = ctk.CTkFrame(tip, fg_color="transparent")
            links.pack(anchor="w", pady=(2, 0))
            ctk.CTkButton(links, text="Get a free key", width=110, height=26, corner_radius=4,
                          font=app.font_tiny, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                          border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                          command=lambda: (self.close(), WelcomeDialog(app))).pack(side="left")
            self.var_no_tip = tk.BooleanVar(self, False)
            ctk.CTkCheckBox(links, text="Don't show again", variable=self.var_no_tip,
                            font=app.font_tiny, text_color=TEXT_DIM, checkbox_width=16,
                            checkbox_height=16, border_width=1,
                            command=lambda: app.set_key_tip(not self.var_no_tip.get()),
                            ).pack(side="left", padx=(12, 0))

        button("Close", self.close).pack_configure(pady=(0, 16))

        centre_on(self, app)

    def set_upload(self, upload):
        """Show the title, description and tags (written after the render)."""
        if not self.winfo_exists():
            return
        self.upload_note.pack_forget()
        for key, (row, box) in self.upload_fields.items():
            value = upload[key] if key != "tags" else ", ".join(upload[key])
            box.delete("1.0", "end")
            box.insert("1.0", value)
            row.pack(fill="x", pady=2)

    def _copy(self, box):
        self.clipboard_clear()
        self.clipboard_append(box.get("1.0", "end").strip())

    def _thumb(self):
        return os.path.splitext(self.path)[0] + ".thumbnail.jpg"

    def change_it(self):
        """Hand the comment to the Storyboard, which shows what changed before any render."""
        comment = self.comment.get().strip()
        if comment:
            self.close()
            self.app.select_page("board")
            self.app.revise_script(comment)

    # -- picture ---------------------------------------------------------------
    def _load_poster(self):
        """Read the video's size and a still frame. None if it can't be previewed."""
        try:
            from PIL import ImageTk

            width, height, self.length = preview.probe(self.path)
            # Real pixels: follow the display scaling, but never outgrow the screen.
            scale = min(ctk.ScalingTracker.get_widget_scaling(self.app),
                        self.winfo_screenheight() * 0.6 / preview.PORTRAIT_BOX[1])
            self.size = preview.preview_size((width, height), max(scale, 0.5))
            image = preview.poster(self.path, self.size, at=min(1.0, self.length / 2))
            self.poster_photo = ImageTk.PhotoImage(image, master=self)
            return self.size
        except Exception as exc:  # noqa: BLE001 - the video is saved either way
            self.app.append_log(f"⚠️ Couldn't show the preview ({exc}).")
            return None

    def _select_version(self, *_args):
        """Point the preview and the buttons at the chosen version's file."""
        if not self.winfo_exists():
            return
        self.stop()
        self.path = self.paths[self.version_names.index(self.var_version.get())]
        self._forget_sound()
        if self.play_button is not None and self._load_poster() is not None:
            self.screen.configure(image=self.poster_photo)

    def _forget_sound(self):
        if self.wav:
            try:
                os.remove(self.wav)
            except OSError:
                pass
            self.wav = None

    def _play_label(self):
        glyph = self.app.icon("stop" if self.playing else "play")
        return f"{glyph}   {'Stop' if self.playing else 'Play'}"

    # -- playback --------------------------------------------------------------
    def toggle(self):
        if self.playing:
            self.stop()
        else:
            self.play()

    def play(self):
        self.stop()
        try:
            if self.wav is None and sys.platform == "win32":
                os.makedirs(PREVIEW_DIR, exist_ok=True)
                handle, wav = tempfile.mkstemp(suffix=".wav", prefix="result_", dir=PREVIEW_DIR)
                os.close(handle)
                try:
                    self.wav = preview.extract_audio(self.path, wav)
                except Exception:  # noqa: BLE001 - a silent video still plays
                    os.remove(wav)
            self.reader = preview.FrameReader(self.path, self.size)
            first = self.reader.read()   # waits until ffmpeg is really decoding
        except Exception as exc:  # noqa: BLE001
            self.app.append_log(f"⚠️ Couldn't play the preview ({exc}).")
            self.stop()
            return
        if first is None:
            self.stop()
            return
        self.playing = True
        self.play_button.configure(text=self._play_label())
        self._show(first)
        # Sound and clock start together, after the first frame is on screen.
        if self.wav and sys.platform == "win32":
            import winsound

            winsound.PlaySound(self.wav, winsound.SND_FILENAME | winsound.SND_ASYNC
                               | winsound.SND_NODEFAULT)
        self.started = time.perf_counter()
        self.job = self.after(10, self._tick)

    def _tick(self):
        self.job = None
        if not self.playing:
            return
        wanted = int((time.perf_counter() - self.started) * preview.FPS)
        if wanted >= self.reader.index:
            # Skips frames when the window is behind, so picture follows sound.
            frame = self.reader.skip_to(wanted)
            if frame is None:
                self.stop(silence=False)   # let the last moment of sound finish
                return
            self._show(frame)
        self.job = self.after(10, self._tick)

    def _show(self, frame):
        from PIL import Image, ImageTk

        self.photo = ImageTk.PhotoImage(Image.frombytes("RGB", self.size, frame), master=self)
        self.screen.configure(image=self.photo)

    def stop(self, silence=True):
        self.playing = False
        if self.job:
            try:
                self.after_cancel(self.job)
            except Exception:
                pass
            self.job = None
        if self.reader:
            self.reader.close()
            self.reader = None
        if silence and sys.platform == "win32":
            import winsound

            winsound.PlaySound(None, winsound.SND_PURGE)
        if self.winfo_exists() and self.play_button is not None:
            self.play_button.configure(text=self._play_label())
            self.screen.configure(image=self.poster_photo)

    # -- buttons ---------------------------------------------------------------
    def open_in_player(self):
        self.stop()
        try:
            if sys.platform == "win32":
                os.startfile(self.path)  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", self.path])
        except OSError as exc:
            self.app.append_log(f"⚠️ Couldn't open the video ({exc}).")

    def show_in_folder(self):
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self.path)])
        else:
            self.app._open_folder(os.path.dirname(self.path))

    def close(self):
        self.stop()
        self._forget_sound()
        self.destroy()


WRITER_MODES = ("Smart writer", "Quick split")


class DraftDialog(ctk.CTkToplevel):
    """New from text: paste plain text, say who it is for, get a script.

    Three pages in one window: the text, the questions with answers picked
    from it, then the script to review. The Smart writer (a model on this PC)
    runs on a worker thread and reports through the app's UI queue; Quick
    split is instant and is also what every Smart writer failure falls back to.
    """

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.result = None
        self.keep = set()        # dropped sentences the user put back
        self.style = ""          # a Home template's extra instruction for the writer
        self.confirming = False  # "Use this script" was pressed over the user's own script
        self.busy = False        # a worker is running, or the model is downloading
        self.stop = threading.Event()
        self.session = None      # the loaded model, kept from Analyse to Write script
        self.note = ""           # why Quick split was used instead of the Smart writer
        self._job = None
        self._idle = None
        self._after_download = None
        self._restore = self.show_text
        self.title("New from text")
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.close)

        saved = app.audience
        self.var_content = tk.StringVar(self, saved["audience_content"])
        self.var_age = tk.StringVar(self, saved["audience_age"])
        self.var_platform = tk.StringVar(self, saved["audience_platform"])
        self.var_length = tk.StringVar(self, self.audience().length)
        self.var_aspect = tk.StringVar(self, self.audience().aspect)
        self.var_mode = tk.StringVar(self, app.writer_mode)
        self.var_platform.trace_add("write", self._on_platform)
        self.var_mode.trace_add("write", lambda *_: self._on_mode())

        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        ctk.CTkLabel(card, text="New from text", font=app.font_title, text_color=TEXT,
                     anchor="w").pack(fill="x", padx=20, pady=(18, 2))
        self.intro = ctk.CTkLabel(card, font=app.font_body, text_color=TEXT_MUTED,
                                  justify="left", wraplength=600, anchor="w")
        self.intro.pack(fill="x", padx=20, pady=(0, 10))

        self.text_page = ctk.CTkFrame(card, fg_color="transparent")
        self.ask_page = ctk.CTkFrame(card, fg_color="transparent")
        self.review_page = ctk.CTkFrame(card, fg_color="transparent")
        self._build_text(self.text_page)
        self._build_ask(self.ask_page)
        self._build_review(self.review_page)

        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.pack(side="bottom", fill="x", padx=20, pady=(12, 18))
        self.main_button = ctk.CTkButton(
            buttons, width=170, height=32, corner_radius=4, font=app.font_body,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ACCENT_TEXT,
            text_color_disabled=ACCENT_TEXT)
        self.main_button.pack(side="right")
        self.back_button = self._plain(buttons, "Back", self.show_text)
        self._plain(buttons, "Close", self.close).pack(side="left")

        # What the Smart writer is doing, shown only while it is doing it.
        self.working = ctk.CTkFrame(card, fg_color="transparent")
        self.working_label = ctk.CTkLabel(self.working, text="", font=app.font_tiny,
                                          text_color=TEXT_MUTED, anchor="w")
        self.working_label.pack(fill="x")
        self.working_bar = ctk.CTkProgressBar(self.working, height=4, corner_radius=2,
                                              progress_color=ACCENT, fg_color=FIELD_BG)
        self.working_bar.pack(fill="x", pady=(4, 0))

        app.writer_listeners.append(self._on_download)
        self.show_text()
        centre_on(self, app)
        self.text_box.focus_set()

    def _plain(self, master, text, command, width=96):
        return ctk.CTkButton(
            master, text=text, width=width, height=32, corner_radius=4, font=self.app.font_body,
            fg_color=FIELD_BG, hover_color=CARD_HOVER, border_width=1, border_color=FIELD_BORDER,
            text_color=TEXT, command=command)

    def _menu(self, master, values, variable):
        return ctk.CTkOptionMenu(
            master, values=list(values), variable=variable, width=230, height=32,
            corner_radius=4, font=self.app.font_body, fg_color=FIELD_BG, button_color=FIELD_BG,
            button_hover_color=CARD_HOVER, text_color=TEXT, dropdown_fg_color=CARD_BG,
            dropdown_text_color=TEXT, dropdown_hover_color=CARD_HOVER,
            dropdown_font=self.app.font_body, dynamic_resizing=False)

    def _show(self, page, intro, button, command, back=None):
        """Bring one of the three pages forward."""
        self.confirming = False
        for other in (self.text_page, self.ask_page, self.review_page):
            other.pack_forget()
        page.pack(fill="both", expand=True)
        self.intro.configure(text=intro)
        self.main_button.configure(text=button, command=command, state="normal")
        self.back_button.pack_forget()
        if back:
            self.back_button.configure(text="Back", command=back)
            self.back_button.pack(side="right", padx=(0, 8))

    def smart(self) -> bool:
        return self.var_mode.get() == WRITER_MODES[0]

    # -- working: the Smart writer or its download is busy -------------------------
    def _working(self, message, fraction=None):
        """Show what is happening and turn Back into Cancel."""
        self.busy = True
        self.main_button.configure(state="disabled")
        self.back_button.configure(text="Cancel", command=self.cancel)
        self.back_button.pack(side="right", padx=(0, 8))
        self.working_label.configure(text=message)
        if not self.working.winfo_ismapped():
            self.working.pack(side="bottom", fill="x", padx=20, pady=(8, 0))
        if fraction is None:
            if self.working_bar.cget("mode") != "indeterminate":
                self.working_bar.configure(mode="indeterminate")
                self.working_bar.start()
        else:
            self.working_bar.stop()
            self.working_bar.configure(mode="determinate")
            self.working_bar.set(fraction)

    def _rest(self):
        """Back to the page that was showing before the work began."""
        self.busy = False
        self.working_bar.stop()
        self.working_bar.configure(mode="determinate")
        self.working.pack_forget()
        self._restore()

    def cancel(self):
        self.working_label.configure(text="Stopping...")
        self.stop.set()
        if self.app.writer_downloading:
            self.app.cancel_writer_download()

    def _unload(self):
        """Give the model's memory back when the user has stopped to think."""
        self._idle = None
        if not self.busy and self.session is not None:
            self.session.close()

    def _start(self, job, message):
        """Run ``job`` ("suggest" or "write") on a worker thread."""
        if self._idle:
            self.after_cancel(self._idle)
            self._idle = None
        self.stop.clear()
        self._working(message)
        # Everything the worker needs is read here: it must not touch Tk variables.
        args = (job, self.text(), self.audience(), self.var_length.get(),
                self.app.settings["padding"], bool(self.app.var_writer_gpu.get()),
                bool(self.app.var_auto_cards.get()), self.app.ai_mode(),
                bool(self.app.var_web_facts.get()))
        threading.Thread(target=self._work, args=args, daemon=True).start()

    def _post(self, **payload):
        self.app._queue.put(("draft", dict(payload, dialog=self)))

    def _work(self, job, text, audience, length, padding, gpu, cards, ai, web=False):
        """Worker thread: never touches a widget."""
        keep_loaded = False
        try:
            if self.session is None:
                self.session = writer.Session(self.stop.is_set, gpu=gpu)
            if job == "suggest":
                found = self.session.suggest(text, audience, padding)
                keep_loaded = True  # Write script comes next; don't load it twice
                self._post(event="suggested", found=found)
            else:
                self._post(event="written",
                           result=self.session.write(text, audience, length, padding, cards,
                                                     ai, web))
        except writer.Stopped:
            self._post(event="stopped")
        except (writer.WriterError, draft.DraftError, ScriptError) as exc:
            self._post(event="failed", job=job, message=str(exc))
        except Exception as exc:  # noqa: BLE001 - a thread must not die silently
            self._post(event="failed", job=job, trace=traceback.format_exc(),
                       message=f"The Smart writer hit an unexpected problem ({type(exc).__name__}).")
        finally:
            if not keep_loaded and self.session is not None:
                self.session.close()

    def on_event(self, payload):
        """UI thread: a worker finished."""
        if not self.winfo_exists():
            return
        event = payload["event"]
        if payload.get("trace"):
            self.app.session_log.write(payload["trace"])
        if self.session is not None and self.session.gpu_failed:
            self.session.gpu_failed = False  # say it once
            self.app.append_log("⚠️ The graphics card could not be used for the Smart writer, "
                                "so it ran on the processor.")
        self.busy = False
        if event == "stopped":
            self._rest()
        elif event == "suggested":
            self._rest()
            self._apply(payload["found"])
            # The model stays loaded for Write script, but not for ever.
            self._idle = self.after(120_000, self._unload)
        elif event == "written":
            self._rest()
            self.note, self.result = "", payload["result"]
            self.show_review()
        elif payload["job"] == "suggest":
            self._rest()
            self._apply(self._rule_suggestion())
        else:
            self._rest()
            self._quick_split(payload["message"])

    # -- page 1: the text ----------------------------------------------------------
    def _build_text(self, page):
        app = self.app
        self.text_box = ctk.CTkTextbox(
            page, width=620, height=260, font=app.font_body, corner_radius=4,
            fg_color=FIELD_BG, text_color=TEXT, border_width=0, wrap="word")
        self.text_box.pack(padx=14)
        for event in ("<KeyRelease>", "<<Paste>>", "<<Cut>>"):
            self.text_box.bind(event, self._on_text)
        self.found = ctk.CTkLabel(page, text="Paste or type your text above.", font=app.font_tiny,
                                  text_color=TEXT_DIM, anchor="w", justify="left", wraplength=600)
        self.found.pack(fill="x", padx=20, pady=(6, 8))

    def show_text(self):
        self._restore = self.show_text
        self._show(self.text_page, "Paste any text and press Analyse. The app reads it and "
                   "suggests who the video is for and how long it should be.",
                   "Analyse", self.analyse)

    def prefill(self, text):
        """Start with ``text`` already in the box: plain text the user tried to render."""
        self.text_box.insert("1.0", text)
        self._show(self.text_page, "This is plain text, not a script yet, so it is here in "
                   "New from text. Press Analyse and the app turns it into a script.",
                   "Analyse", self.analyse)
        self._on_text()

    def _rule_suggestion(self):
        return draft.suggest(self.text(), self.audience(), self.app.settings["padding"])

    def analyse(self):
        """Read the text and pick answers to the questions; the user can change them all."""
        if self.busy:
            return
        if not self.text():
            self.found.configure(text="Paste some text first.", text_color=ERR_COLOR)
            return
        self.keep = set()
        if self.smart() and writer.ready()[0]:
            self._start("suggest", "The Smart writer is reading your text...")
        else:
            self._apply(self._rule_suggestion())

    def _apply(self, found):
        self.var_content.set(found.audience.content)
        self.var_age.set(found.audience.age)
        self.var_length.set(found.length)  # after the platform: it presets the length
        self.reason.configure(text=found.reason)
        self.show_ask()

    # -- page 2: the questions -----------------------------------------------------
    def _build_ask(self, page):
        app = self.app
        self.reason = ctk.CTkLabel(page, text="", font=app.font_body, text_color=TEXT,
                                   anchor="w", justify="left", wraplength=600)
        self.reason.pack(fill="x", padx=20, pady=(0, 4))
        self.ask_error = ctk.CTkLabel(page, text="", font=app.font_tiny, text_color=TEXT_DIM,
                                      anchor="w", justify="left", wraplength=600)
        self.ask_error.pack(fill="x", padx=20, pady=(0, 8))
        questions = ctk.CTkFrame(page, fg_color="transparent")
        questions.pack(fill="x", padx=20)
        rows = (
            ("Who is it for?", lambda m: self._menu(m, draft.CONTENT, self.var_content)),
            ("Age", lambda m: FluentSegmented(m, app, list(draft.AGES), self.var_age, 380)),
            ("Where will it go?", lambda m: self._menu(m, draft.PLATFORMS, self.var_platform)),
            ("How long?", lambda m: FluentSegmented(m, app, draft.LENGTHS, self.var_length, 420)),
            ("Aspect ratio", lambda m: FluentSegmented(m, app, RATIO_OPTIONS, self.var_aspect, 170)),
            ("Written by", lambda m: FluentSegmented(m, app, list(WRITER_MODES), self.var_mode, 260)),
        )
        for position, (label, make) in enumerate(rows):
            ctk.CTkLabel(questions, text=label, font=app.font_body, text_color=TEXT, width=140,
                         anchor="w").grid(row=position, column=0, sticky="w", pady=5)
            make(questions).grid(row=position, column=1, sticky="w", pady=5)
        self.mode_hint = ctk.CTkLabel(page, text="", font=app.font_tiny, text_color=TEXT_DIM,
                                      anchor="w", justify="left", wraplength=600)
        self.mode_hint.pack(fill="x", padx=20, pady=(6, 0))

    def audience(self) -> draft.Audience:
        return draft.Audience(self.var_content.get(), self.var_age.get(), self.var_platform.get(),
                              self.style)

    def _on_platform(self, *_args):
        """A platform suggests a length and a shape; both can still be changed."""
        self.var_length.set(self.audience().length)
        self.var_aspect.set(self.audience().aspect)

    def _on_text(self, _event=None):
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(250, self._analyse)

    def text(self) -> str:
        return self.text_box.get("1.0", "end").strip()

    def _analyse(self):
        self._job = None
        found = draft.analyse(self.text(), padding=self.app.settings["padding"])
        self.found.configure(text_color=TEXT_DIM)
        if not found.words:
            self.found.configure(text="Paste or type your text above.")
            return
        parts = [f"{found.words} words", f"{found.sentences} sentences",
                 f"about {round(found.seconds)} s read in full"]
        if found.topics:
            parts.append("about: " + ", ".join(found.topics[:4]))
        self.found.configure(text=" · ".join(parts))

    def needs_download(self) -> bool:
        return self.smart() and not writer.installed() and writer.runtime_path() is not None

    def _on_mode(self):
        """Say what the chosen writer does, and what the button will do."""
        if self._restore != self.show_ask or self.busy:
            return
        model = writer.MODEL
        if self.needs_download():
            hint = (f"The Smart writer is a one-time download of {model.gigabytes} "
                    f"({model.name}, {model.licence} licence). After that it runs on this PC: "
                    "your text is never sent anywhere.")
            button = f"Download ({model.gigabytes})"
        elif self.smart():
            hint = ("The Smart writer rewrites your text to fit the length and picks the search "
                    "words. It runs on this PC and takes 10 to 60 seconds.")
            button = "Write script"
        else:
            hint = ("Quick split keeps your words exactly as written and splits them into "
                    "scenes. It is instant.")
            button = "Write script"
        self.mode_hint.configure(text=hint)
        self.main_button.configure(text=button, command=self.write)

    def show_ask(self):
        self._restore = self.show_ask
        self.ask_error.configure(text="These answers were picked from your text. Change "
                                      "anything that isn't right.", text_color=TEXT_DIM)
        self._show(self.ask_page, "Who is the video for, and how long should it be?",
                   "Write script", self.write, back=self.show_text)
        self._on_mode()

    # -- writing -------------------------------------------------------------------
    def write(self):
        if self.busy:
            return
        if not self.smart():
            self._quick_split()
        elif self.needs_download():
            self._after_download = self.write
            self.app.start_writer_download()
            self._working(f"Downloading the Smart writer ({writer.MODEL.gigabytes})...", 0)
        else:
            ok, why = writer.ready()
            if ok:
                self._start("write", "The Smart writer is writing your script. This takes "
                                     "10 to 60 seconds...")
            else:
                self._quick_split(why)

    def _quick_split(self, because=""):
        """Write with the user's own words. ``because`` is why the Smart writer wasn't used."""
        try:
            self.result = draft.write(self.text(), self.audience(), self.var_length.get(),
                                      keep=self.keep, padding=self.app.settings["padding"],
                                      cards=bool(self.app.var_auto_cards.get()),
                                      ai=self.app.ai_mode())
        except (draft.DraftError, ScriptError) as exc:
            self.ask_error.configure(text=str(exc), text_color=ERR_COLOR)
            return
        self.note = because
        self.show_review()

    def _on_download(self, payload):
        """UI thread: the model download moved on (it may have been started in Settings)."""
        if not self.winfo_exists() or not self.busy or self._after_download is None:
            return
        event = payload["event"]
        if event == "progress":
            done, total = payload["done"], payload["total"]
            self._working(f"Downloading the Smart writer: {done / 1e9:.2f} of "
                          f"{total / 1e9:.2f} GB. You can cancel and carry on later.",
                          done / total if total else 0)
            return
        after, self._after_download = self._after_download, None
        self._rest()
        if event == "done":
            after()
        elif event == "failed":
            self.ask_error.configure(text=payload["message"], text_color=ERR_COLOR)
        else:
            self.ask_error.configure(text="Download paused. It carries on from here next time.",
                                     text_color=TEXT_DIM)

    # -- page 3: the script --------------------------------------------------------
    def _build_review(self, page):
        app = self.app
        self.script_view = ctk.CTkTextbox(
            page, width=620, height=230, font=app.font_mono, corner_radius=4,
            fg_color=FIELD_BG, text_color=TEXT, border_width=0, wrap="word")
        self.script_view.pack(padx=14)
        self.summary = ctk.CTkLabel(page, text="", font=app.font_tiny, text_color=TEXT_MUTED,
                                    anchor="w", justify="left", wraplength=600)
        self.summary.pack(fill="x", padx=20, pady=(6, 0))
        self.dropped_box = ctk.CTkFrame(page, fg_color="transparent")
        self.dropped_box.pack(fill="x", padx=14, pady=(6, 0))

    def show_review(self):
        app, result = self.app, self.result
        self._restore = self.show_review
        self._show(self.review_page, "Here is the script. You can still edit it after it is in "
                   "the editor, and the Storyboard shows the clips it finds.",
                   "Use this script", self.use, back=self.show_ask)
        self.script_view.configure(state="normal")
        self.script_view.delete("1.0", "end")
        self.script_view.insert("1.0", result.script)
        self.script_view.configure(state="disabled")
        plural = "scene" if len(result.scenes) == 1 else "scenes"
        self.summary.configure(
            text=f"{result.source} · about {round(result.seconds)} s · {len(result.scenes)} "
                 f"{plural} · voice: {voices.persona(result.voice).short} · {result.aspect}",
            text_color=TEXT_MUTED)

        for child in self.dropped_box.winfo_children():
            child.destroy()
        if self.note:
            ctk.CTkLabel(self.dropped_box, font=app.font_tiny, text_color=ERR_COLOR, anchor="w",
                         justify="left", wraplength=590,
                         text=f"{self.note} This script was made with Quick split instead."
                         ).pack(fill="x", padx=6, pady=(0, 4))
        if result.source == "Smart writer":
            again = ctk.CTkFrame(self.dropped_box, fg_color="transparent")
            again.pack(fill="x", padx=6)
            ctk.CTkLabel(again, font=app.font_tiny, text_color=TEXT_DIM, anchor="w",
                         text="Not quite right? Each try comes out a little different."
                         ).pack(side="left")
            self._plain(again, "Write again", self.write, width=110).pack(side="right")
        if not result.dropped:
            return
        count = len(result.dropped)
        header = ctk.CTkFrame(self.dropped_box, fg_color="transparent")
        header.pack(fill="x", padx=6)
        ctk.CTkLabel(header, font=app.font_body, text_color=TEXT, anchor="w",
                     text=f"Left out to fit {result.length}: {count} "
                          f"sentence{'' if count == 1 else 's'}").pack(side="left")
        longer = result.longer
        label = "Keep everything" if longer == draft.KEEP_ALL else f"Use {longer} and keep everything"
        self._plain(header, label, lambda: self.use_length(longer), width=230).pack(side="right")
        listing = ctk.CTkScrollableFrame(
            self.dropped_box, width=600, height=min(count, 3) * 36, fg_color="transparent",
            scrollbar_fg_color=SCROLLBAR_BG, scrollbar_button_color=SCROLLBAR_THUMB,
            scrollbar_button_hover_color=SCROLLBAR_THUMB_HOVER)
        listing.pack(fill="x")
        listing.grid_columnconfigure(0, weight=1)
        for position, (index, sentence) in enumerate(result.dropped):
            ctk.CTkLabel(listing, text=ellipsize(sentence, 74), font=app.font_tiny,
                         text_color=TEXT_MUTED, anchor="w").grid(row=position, column=0,
                                                                 sticky="w", pady=3)
            self._plain(listing, "Put back", lambda i=index: self.put_back(i),
                        width=84).grid(row=position, column=1, padx=(8, 4), pady=3)

    def put_back(self, index):
        self.keep.add(index)
        self._quick_split(self.note)

    def use_length(self, length):
        self.var_length.set(length)
        self._quick_split(self.note)

    def use(self):
        if self.busy:
            return
        # Never silently replace a script the user wrote: ask once, in place.
        if self.app.script_is_the_users_own() and not self.confirming:
            self.confirming = True
            self.summary.configure(text="This replaces the script in the editor. Press again "
                                        "to replace it.", text_color=ERR_COLOR)
            self.main_button.configure(text="Replace my script")
            return
        self.app.source_text = self.text()   # what "make it longer" can draw on later
        self.app.use_draft(self.result, self.audience(), self.var_mode.get())
        self.close()

    def close(self):
        """Stop the model (a download carries on; Settings shows it) and close."""
        self.stop.set()
        if self.session is not None:
            self.session.close()
        if self._on_download in self.app.writer_listeners:
            self.app.writer_listeners.remove(self._on_download)
        self.destroy()


class ReportDialog(ctk.CTkToplevel):
    """A problem report the user reads, edits and sends themselves.

    The app sends nothing: the buttons open a prefilled GitHub issue in the
    browser, or copy the text.
    """

    def __init__(self, app, report, error=""):
        super().__init__(app)
        self.app = app
        self.error = error
        self.title("Report a problem")
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)

        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        ctk.CTkLabel(card, text="Report a problem", font=app.font_title,
                     text_color=TEXT, anchor="w").pack(fill="x", padx=20, pady=(18, 4))
        ctk.CTkLabel(
            card, font=app.font_body, text_color=TEXT_MUTED, justify="left",
            wraplength=580, anchor="w",
            text="This is exactly what will be shared. Nothing is sent until you press a "
                 "button. API keys, your Windows user name and the spoken lines of your "
                 "script have been removed; search words are kept. You can edit the text.",
        ).pack(fill="x", padx=20, pady=(0, 10))

        self.box = ctk.CTkTextbox(card, width=600, height=320, font=app.font_mono_sm,
                                  corner_radius=4, fg_color=FIELD_BG, text_color=TEXT,
                                  border_width=0, wrap="word")
        self.box.pack(padx=20)
        self.box.insert("1.0", report)

        self.status = ctk.CTkLabel(card, text="", font=app.font_tiny, text_color=TEXT_DIM,
                                   anchor="w", justify="left", wraplength=580)
        self.status.pack(fill="x", padx=20, pady=(8, 8))

        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.pack(fill="x", padx=20, pady=(0, 18))
        ctk.CTkButton(
            buttons, text="Open GitHub issue", width=150, height=32, corner_radius=4,
            font=app.font_body, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color=ACCENT_TEXT, command=self.open_issue,
        ).pack(side="right")
        for text, command in (("Copy to clipboard", self.copy), ("Close", self.destroy)):
            ctk.CTkButton(
                buttons, text=text, width=130, height=32, corner_radius=4,
                font=app.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                border_width=1, border_color=FIELD_BORDER, text_color=TEXT, command=command,
            ).pack(side="right", padx=(0, 8))

        centre_on(self, app)

    def text(self) -> str:
        return self.box.get("1.0", "end").strip()

    def copy(self, quiet=False):
        self.app.clipboard_clear()
        self.app.clipboard_append(self.text())
        if not quiet:
            self.status.configure(text="Copied. Paste it into an email or a message.")

    def open_issue(self):
        self.copy(quiet=True)   # the fallback if the link has to leave the log out
        first = (self.error or "").strip().splitlines()[0:1]
        title = f"Render failed: {first[0][:80]}" if first else "Problem report"
        webbrowser.open(diagnostics.issue_url(self.app.scrub(title), self.text()))
        self.status.configure(
            text="GitHub opened in your browser. Press \"Submit new issue\" there to send "
                 "it (you need a GitHub account). The report is also on your clipboard.")


def centre_on(window, app):
    """Place a dialog over the main window and make it modal."""
    window.update_idletasks()

    def grab():
        try:
            window.grab_set()
        except Exception:
            pass

    if app.state() == "iconic":
        # The main window is minimized (a render finished in the background, say).
        # A dialog that takes the grab now would swallow the taskbar's "restore" and
        # the app could never be brought back, so bring the app back first.
        app.deiconify()
        app.update_idletasks()
        window.lift()
    x = app.winfo_rootx() + (app.winfo_width() - window.winfo_width()) // 2
    y = app.winfo_rooty() + (app.winfo_height() - window.winfo_height()) // 3
    window.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    grab()


# =============================================================================
#  SECTION 7 - APPLICATION SHELL
# =============================================================================


class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self._launched = time.perf_counter()
        self.session_log = diagnostics.SessionLog(LOG_FILE)
        self.recent_lines = collections.deque(maxlen=300)   # what a report quotes from
        self.settings = load_settings()
        ctk.set_appearance_mode(self.settings["theme"])
        ctk.set_default_color_theme("blue")

        self.title(f"{APP_NAME}")
        icon_path = asset_path("icon.ico")
        if icon_path:
            try:
                self.iconbitmap(icon_path)
            except Exception:
                pass
        self.geometry("1180x780")
        self.minsize(980, 640)
        self.configure(fg_color=MAIN_BG)

        self._queue: queue.Queue = queue.Queue()
        self.ui = UiBridge(self._queue)
        self.is_rendering = False
        self._previewing = False
        self.last_render = "Never"
        self.sidebar_collapsed = False
        self.effect_note = ""
        self._refresh_job = None
        self.current_page = None
        self._page_anim = None
        self._key_tests = {}   # token -> callback(ok, message)
        self._cancel = None    # threading.Event of the render in progress
        self.board = None          # the Storyboard's scenes (vidgen.board.Board)
        self.board_text = None     # the script the Storyboard was last built from
        self.board_clips = {}      # search words -> stock clips found for them
        self.board_pos = {}        # search words -> which of those clips is chosen
        self.board_pictures = {}   # AI picture key -> the file made for it
        self.board_marked = set()  # scenes the last "Change it" rewrote
        self.board_history, self.board_future = [], []   # Storyboard Undo / Redo
        self.board_cards, self.board_images = [], {}
        self.board_gen = 0         # goes up on every rebuild; late worker news is dropped
        self._revising = False
        self.source_text = ""      # the text the script in the editor was written from
        self._after_draft = "board"   # or "render": what New from text does when used
        self._slow_ok = None       # the script a slow processor render was agreed to for
        self._last_draft = None    # the last script "New from text" put in the editor
        self.writer_listeners = []     # callables told how the model download is going
        self.writer_downloading = False
        self.voice_downloading = False
        self.images_downloading = False
        self._images_stop = threading.Event()
        self._images_confirm = False
        self._making_picture = False
        self._try_image = None   # kept so Tk doesn't drop the picture
        self._writer_stop = threading.Event()

        self._build_fonts()
        self._build_vars()
        self._build_shell()
        self._build_pages()
        self.select_page("home")

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(120, self._post_init)
        self.after(60, self._drain_queue)

    # -- fonts & icons -------------------------------------------------------
    def _build_fonts(self):
        families = set(tkfont.families(self))
        self.ui_family = "Segoe UI" if "Segoe UI" in families else "Helvetica"
        if "Segoe Fluent Icons" in families:
            self.icon_family, self._icon_index = "Segoe Fluent Icons", 0
        elif "Segoe MDL2 Assets" in families:
            self.icon_family, self._icon_index = "Segoe MDL2 Assets", 0
        else:
            self.icon_family, self._icon_index = self.ui_family, 1

        self.font_body = ctk.CTkFont(family=self.ui_family, size=13)
        self.font_bold = ctk.CTkFont(family=self.ui_family, size=13, weight="bold")
        self.font_tiny = ctk.CTkFont(family=self.ui_family, size=11)
        self.font_caption = ctk.CTkFont(family=self.ui_family, size=12, weight="bold")
        self.font_title = ctk.CTkFont(family=self.ui_family, size=16, weight="bold")
        self.font_value = ctk.CTkFont(family=self.ui_family, size=15)
        self.font_icon = ctk.CTkFont(family=self.icon_family, size=15)
        self.font_icon_lg = ctk.CTkFont(family=self.icon_family, size=18)
        self.font_mono = ctk.CTkFont(family="Consolas", size=12)
        self.font_mono_sm = ctk.CTkFont(family="Consolas", size=11)

    def icon(self, name: str) -> str:
        return GLYPHS.get(name, ("", "?"))[self._icon_index]

    # -- state ---------------------------------------------------------------
    def _build_vars(self):
        s = self.settings
        self.var_api = tk.StringVar(value=s["api_key"])
        self.var_output = tk.StringVar(value=s["output_path"])
        self.var_aspect = tk.StringVar(value=s["aspect"])
        self.var_resolution = tk.StringVar(value=s["resolution"])
        self.var_voice = tk.StringVar(value=voices.persona(s["voice"]).label)
        self.var_theme = tk.StringVar(value=s["theme"])
        self.var_translucent = tk.BooleanVar(value=bool(s["translucent"]))
        self.var_pixabay = tk.StringVar(value=s["pixabay_key"])
        padding_label = {v: k for k, v in PADDING_OPTIONS.items()}[s["padding"]]
        self.var_padding = tk.StringVar(value=padding_label)
        self.var_music_enabled = tk.BooleanVar(value=s["music_enabled"])
        self.var_ask_save = tk.BooleanVar(value=s["ask_save"])
        self.welcome_seen = s["welcome_seen"]
        self.tour_seen = s["tour_seen"]
        self.var_check_updates = tk.BooleanVar(value=s["check_updates"])
        self.var_versions = tk.StringVar(value=str(s["versions"]))
        self.var_target = tk.StringVar(value=s["target_length"])
        self.var_captions = tk.BooleanVar(value=s["captions"])
        self.caption_vars = {key: tk.StringVar(value=s[key])
                             for key, _allowed, _default in CAPTION_SETTINGS}
        self.music_path = s["music_path"]
        self.audience = {key: s[key] for key, _allowed, _default in AUDIENCE_SETTINGS}
        self.writer_mode = s["writer_mode"]
        self.var_writer_gpu = tk.BooleanVar(value=s["writer_gpu"])
        self.var_voice_online = tk.BooleanVar(value=s["voice_online"])
        self.var_auto_cards = tk.BooleanVar(value=s["auto_cards"])
        self.var_ai_for = tk.StringVar(value=s["ai_images_for"])
        self.var_ai_look = tk.StringVar(value=s["ai_images_look"])
        self.ai_backend = s["ai_images_backend"]
        self.var_ai_device = tk.StringVar(value=s["ai_device"])
        self.project = s["project"] or None   # vidgen.projects id of what the script is
        self.var_intro_on = tk.BooleanVar(value=s["intro_on"])
        self.var_intro_text = tk.StringVar(value=s["intro_text"])
        self.var_outro_on = tk.BooleanVar(value=s["outro_on"])
        self.var_outro_text = tk.StringVar(value=s["outro_text"])
        self.var_sfx = tk.BooleanVar(value=s["sound_effects"])
        self.var_smart_motion = tk.BooleanVar(value=s["smart_motion"])
        self.var_animate = tk.StringVar(value=s["animate"])
        self.var_web_facts = tk.BooleanVar(value=s["web_facts"])
        self.var_style = tk.StringVar(value=s["style"])
        self.var_quick_sync = tk.BooleanVar(value=s["quick_sync"])
        self.var_style.trace_add("write", lambda *_args: getattr(self, "board_cards", None)
                                 and self.refresh_board(force=True))   # cards show the style
        self.whats_new_seen, self.whats_new_off = s["whats_new_seen"], s["whats_new_off"]
        self.key_tip = s["key_tip"]
        self.ai_gpu, self.ai_fast_seconds = s["ai_gpu"], s["ai_fast_seconds"]
        self.ai_threads_best = s["ai_threads_best"]
        self.ai_speed_chosen = bool(s["ai_images_speed"])
        self.var_ai_speed = tk.StringVar(value=s["ai_images_speed"] or self.default_ai_speed())
        self.var_ai_threads = tk.StringVar(value=str(s["ai_threads"]))
        self.var_ai_speed.trace_add("write", self._ai_speed_picked)
        self._render_overrides = {}   # one render's answer to the slow-PC question
        self._both = None   # "Make both shapes": {shape, other, first: the first videos}
        self._batch = None  # "Make several": the texts, how far it is, what was made
        self.last_upload = None   # title, description, tags of the last video made
        self.voice_menus = []     # the voice dropdowns, to add My voice to once it is made
        self.var_music_track = tk.StringVar(value="")
        self._tracks = {}

        for var in (self.var_api, self.var_output, self.var_aspect,
                    self.var_resolution, self.var_voice, self.var_pixabay,
                    self.var_padding, self.var_music_enabled, self.var_captions,
                    self.var_ask_save, self.var_check_updates, self.var_versions,
                    self.var_writer_gpu, self.var_voice_online, self.var_auto_cards,
                    self.var_ai_for, self.var_ai_look,
                    self.var_target, self.var_intro_on, self.var_intro_text,
                    self.var_outro_on, self.var_outro_text, self.var_sfx,
                    self.var_smart_motion, self.var_animate, self.var_web_facts,
                    self.var_style, self.var_quick_sync,
                    *self.caption_vars.values()):
            var.trace_add("write", self._on_setting_changed)

    def _collect_settings(self) -> dict:
        return {
            "api_key": self.var_api.get().strip(),
            "pixabay_key": self.var_pixabay.get().strip(),
            "padding": PADDING_OPTIONS.get(self.var_padding.get(), DEFAULT_PADDING),
            "music_enabled": bool(self.var_music_enabled.get()),
            "ask_save": bool(self.var_ask_save.get()),
            "welcome_seen": self.welcome_seen,
            "tour_seen": self.tour_seen,
            "check_updates": bool(self.var_check_updates.get()),
            "versions": 2 if self.var_versions.get() == "2" else 1,
            "target_length": self.var_target.get(),
            **self.audience,
            "writer_mode": self.writer_mode,
            "writer_gpu": bool(self.var_writer_gpu.get()),
            "voice_online": bool(self.var_voice_online.get()),
            "auto_cards": bool(self.var_auto_cards.get()),
            "ai_images_for": self.var_ai_for.get(),
            "ai_images_look": self.var_ai_look.get(),
            "ai_images_backend": self.ai_backend,
            "ai_device": self.var_ai_device.get(),
            "project": self.project or "",
            "intro_on": bool(self.var_intro_on.get()),
            "intro_text": self.var_intro_text.get().strip(),
            "outro_on": bool(self.var_outro_on.get()),
            "outro_text": self.var_outro_text.get().strip(),
            "sound_effects": bool(self.var_sfx.get()),
            "smart_motion": bool(self.var_smart_motion.get()),
            "animate": self.var_animate.get(),
            "web_facts": bool(self.var_web_facts.get()),
            "style": self.var_style.get(),
            "quick_sync": bool(self.var_quick_sync.get()),
            "whats_new_seen": self.whats_new_seen,
            "whats_new_off": self.whats_new_off,
            "key_tip": self.key_tip,
            "ai_images_speed": self.var_ai_speed.get() if self.ai_speed_chosen else "",
            "ai_threads": self.var_ai_threads.get(),
            "ai_threads_best": self.ai_threads_best,
            "ai_fast_seconds": self.ai_fast_seconds,
            "ai_gpu": self.ai_gpu,
            "music_path": self.music_path,
            "output_path": self.var_output.get().strip(),
            "aspect": self.var_aspect.get(),
            "resolution": self.var_resolution.get(),
            "voice": voices.persona(self.var_voice.get()).id,
            "captions": bool(self.var_captions.get()),
            **{key: var.get() for key, var in self.caption_vars.items()},
            "theme": self.var_theme.get(),
            "translucent": bool(self.var_translucent.get()),
            "script": self.script_box.get("1.0", "end").strip()
            if hasattr(self, "script_box") else self.settings["script"],
        }

    def _on_setting_changed(self, *_args):
        self.settings = self._collect_settings()
        save_settings(self.settings)
        self.refresh_home()

    # -- shell ---------------------------------------------------------------
    def _build_shell(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)   # row 0 is the update bar, when shown

        self.sidebar = ctk.CTkFrame(self, width=SIDEBAR_WIDTH, corner_radius=0,
                                    fg_color=SIDEBAR_BG)
        self.sidebar.grid(row=0, column=0, rowspan=2, sticky="nsw")
        # Children here are packed, so pack_propagate() is what stops them from
        # dictating the sidebar's width (grid_propagate would be a no-op).
        self.sidebar.pack_propagate(False)
        self.sidebar.grid_propagate(False)

        self.hamburger = ctk.CTkButton(
            self.sidebar, text=self.icon("menu"), font=self.font_icon,
            width=36, height=34, corner_radius=5, fg_color="transparent",
            hover_color=NAV_HOVER, text_color=TEXT, command=self.toggle_sidebar,
        )
        self.hamburger.pack(anchor="w", padx=6, pady=(10, 8))

        self.nav_items = {}
        for key, glyph, label in (
            ("home", "home", "Home"),
            ("projects", "projects", "Projects"),
            ("board", "scenes", "Storyboard"),
            ("create", "create", "Script"),
            ("images", "images", "AI images"),
            ("log", "log", "Log"),
        ):
            item = NavItem(self.sidebar, self, key, self.icon(glyph), label,
                           self.select_page)
            item.pack(fill="x", padx=6, pady=1)
            self.nav_items[key] = item

        bottom = ctk.CTkFrame(self.sidebar, fg_color="transparent",
                              width=SIDEBAR_WIDTH, height=84)
        bottom.pack(side="bottom", fill="x", pady=(0, 10))
        bottom.pack_propagate(False)
        for key, glyph, label in (
            ("feedback", "feedback", "Feedback"),
            ("settings", "settings", "Settings"),
        ):
            item = NavItem(bottom, self, key, self.icon(glyph), label,
                           self.select_page)
            item.pack(fill="x", padx=6, pady=1)
            self.nav_items[key] = item

        # Shown only when a newer release exists; see check_for_updates().
        self.update_url = updates.RELEASES_PAGE
        self.update_bar = Card(self)
        self.update_bar.grid(row=0, column=1, sticky="ew", padx=EDGE, pady=(14, 0))
        self.update_bar.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self.update_bar, text=self.icon("info"), font=self.font_icon,
                     text_color=ACCENT).grid(row=0, column=0, padx=(16, 10), pady=10)
        self.update_bar_label = ctk.CTkLabel(self.update_bar, text="", font=self.font_body,
                                             text_color=TEXT, anchor="w")
        self.update_bar_label.grid(row=0, column=1, sticky="w")
        # "Update now" for an installed copy, "Download" (the release page) otherwise.
        self.update_action = ctk.CTkButton(
            self.update_bar, text="Download", width=110, height=30, corner_radius=4,
            font=self.font_body, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color=ACCENT_TEXT, text_color_disabled=ACCENT_TEXT,
            command=self.open_update_page,
        )
        self.update_action.grid(row=0, column=2, padx=(8, 0))
        self.update_later = ctk.CTkButton(
            self.update_bar, text="Later", width=72, height=30, corner_radius=4,
            font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
            command=self.update_bar.grid_remove,
        )
        self.update_later.grid(row=0, column=3, padx=(6, 12))
        self.update_bar.grid_remove()
        self.update_release = None     # the newer release, once one is found
        self._updating = False
        self._update_stop = threading.Event()

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=1, column=1, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

    def toggle_sidebar(self):
        self.sidebar_collapsed = not self.sidebar_collapsed
        width = SIDEBAR_COLLAPSED if self.sidebar_collapsed else SIDEBAR_WIDTH
        self.sidebar.configure(width=width)
        for item in self.nav_items.values():
            item.configure(width=width - 12)
            item.set_collapsed(self.sidebar_collapsed)
        # Keep the grid column in step with the frame so the content reflows.
        self.grid_columnconfigure(0, minsize=width)
        self.update_idletasks()

    def select_page(self, key: str):
        if self.current_page == "board" and key != "board":
            self.save_project()   # a Storyboard left without a video is kept as a draft
        if key != self.current_page:
            self._show_page(key)

        for name, item in self.nav_items.items():
            item.set_selected(name == key)
        if key == "home":
            self.refresh_home()
        elif key == "board":
            self.refresh_board()
        elif key == "projects":
            self.refresh_projects()
        elif key == "settings":
            self._refresh_tracks()

    def _show_page(self, key):
        if self._page_anim:
            self._page_anim.cancel()
            self._page_anim = None
        for name, page in self.pages.items():
            if name != key:
                page.grid_remove()
                tk.Frame.place_forget(self._frame_of(page))

        page = self.pages[key]
        if self.current_page is None:   # first page at startup: no entrance
            self._grid_page(page)
        else:
            self._slide_in(page)
        self.current_page = key

    @staticmethod
    def _frame_of(page):
        """The Tk frame that is actually laid out (a scrollable page wraps one)."""
        return getattr(page, "_parent_frame", page)

    @staticmethod
    def _grid_page(page):
        page.grid(row=0, column=0, sticky="nsew", padx=(EDGE, EDGE), pady=(14, 14))

    def _slide_in(self, page):
        """Fluent page entrance: rise into place, decelerating.

        While moving, the page is placed at exactly the size the grid gives it,
        so nothing inside re-lays out; the grid takes over again at the end.
        CTk's own place() refuses width/height, hence the plain Tk call.
        """
        frame = self._frame_of(page)
        page.grid_remove()
        s = ctk.ScalingTracker.get_widget_scaling(self)

        def step(t):
            tk.Frame.place(frame, x=EDGE * s, y=(14 + PAGE_RISE * (1 - t)) * s,
                           relwidth=1, relheight=1, width=-2 * EDGE * s, height=-28 * s)

        def done():
            self._page_anim = None
            tk.Frame.place_forget(frame)
            self._grid_page(page)

        self._page_anim = Animation(self, ANIM_PAGE, step, done)

    # -- page scaffolding ----------------------------------------------------
    def _scroll_page(self):
        return ctk.CTkScrollableFrame(
            self.content, fg_color="transparent",
            scrollbar_fg_color=SCROLLBAR_BG,
            scrollbar_button_color=SCROLLBAR_THUMB,
            scrollbar_button_hover_color=SCROLLBAR_THUMB_HOVER,
        )

    def _caption(self, parent, text, row, pady=(10, 4)):
        label = ctk.CTkLabel(parent, text=text, font=self.font_caption,
                             text_color=TEXT_MUTED, anchor="w")
        label.grid(row=row, column=0, columnspan=4, sticky="w",
                   padx=2, pady=pady)
        return label

    def _build_pages(self):
        self.pages = {
            "home": self._build_home(),
            "projects": self._build_projects(),
            "board": self._build_board(),
            "create": self._build_create(),
            "images": self._build_images(),
            "log": self._build_log(),
            "feedback": self._build_feedback(),
            "settings": self._build_settings(),
        }

    # ---------------------------------------------------------------- HOME --
    def _build_home(self):
        page = self._scroll_page()
        page.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="cards")

        # The front door: most people arrive with text, not with a script (issues #2, #3).
        door = Card(page)
        door.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, PAD))
        door.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(door, text="Paste your text or describe your video", font=self.font_title,
                     text_color=TEXT, anchor="w").grid(row=0, column=0, columnspan=3, sticky="w",
                                                       padx=16, pady=(14, 2))
        ctk.CTkLabel(door, text="An article, a tip, a story, notes. The app writes the script, "
                                "finds the pictures, reads it out and adds captions.",
                     font=self.font_tiny, text_color=TEXT_DIM, anchor="w",
                     ).grid(row=1, column=0, columnspan=3, sticky="w", padx=16)
        self.door_box = ctk.CTkTextbox(door, height=130, font=self.font_body, corner_radius=4,
                                       fg_color=FIELD_BG, text_color=TEXT, border_width=0,
                                       wrap="word")
        template_bar = ctk.CTkFrame(door, fg_color="transparent")
        template_bar.grid(row=2, column=0, columnspan=3, sticky="w", padx=16, pady=(10, 0))
        ctk.CTkLabel(template_bar, text="Template", font=self.font_body, text_color=TEXT_MUTED,
                     ).pack(side="left", padx=(0, 10))
        self.var_template = tk.StringVar(value=NO_TEMPLATE)
        FluentSegmented(template_bar, self, [NO_TEMPLATE, *draft.TEMPLATES], self.var_template,
                        540).pack(side="left")
        ctk.CTkLabel(template_bar, text="Style", font=self.font_body, text_color=TEXT_MUTED,
                     ).pack(side="left", padx=(16, 8))
        ctk.CTkOptionMenu(
            template_bar, values=list(drawn.STYLES), variable=self.var_style, width=120,
            height=30, corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
            button_color=FIELD_BG, button_hover_color=CARD_HOVER, text_color=TEXT,
            dropdown_fg_color=CARD_BG, dropdown_text_color=TEXT, dropdown_hover_color=CARD_HOVER,
            dropdown_font=self.font_body, dynamic_resizing=False).pack(side="left")
        ctk.CTkButton(template_bar, text="Make several...", width=120, height=30,
                      corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
                      hover_color=CARD_HOVER, border_width=1, border_color=FIELD_BORDER,
                      text_color=TEXT, command=lambda: BatchDialog(self),
                      ).pack(side="left", padx=(12, 0))
        ctk.CTkButton(template_bar, text="Show it on my PC...", width=150, height=30,
                      corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
                      hover_color=CARD_HOVER, border_width=1, border_color=FIELD_BORDER,
                      text_color=TEXT, command=lambda: TipDialog(self),
                      ).pack(side="left", padx=(8, 0))
        self.door_box.grid(row=3, column=0, columnspan=3, sticky="ew", padx=14, pady=(8, 8))
        self.door_status = ctk.CTkLabel(door, text="", font=self.font_tiny, text_color=TEXT_DIM,
                                        anchor="w")
        self._look_bar(door).grid(row=4, column=0, columnspan=3, sticky="w", padx=16,
                                  pady=(0, 10))
        self.door_status.grid(row=5, column=0, sticky="w", padx=16, pady=(0, 14))
        self.var_straight = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(door, text="Make it straight away", variable=self.var_straight,
                        font=self.font_body, text_color=TEXT_MUTED, checkbox_width=18,
                        checkbox_height=18, border_width=1,
                        ).grid(row=5, column=1, sticky="e", padx=(0, 14), pady=(0, 14))
        ctk.CTkButton(door, text=f"{self.icon('play')}   Make my video", width=170, height=36,
                      corner_radius=4, font=self.font_bold, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, text_color=ACCENT_TEXT,
                      command=self.make_from_text).grid(row=5, column=2, sticky="e",
                                                        padx=(0, 14), pady=(0, 14))

        self.row_aspect = InfoRow(page, self, self.icon("aspect"), "Aspect Ratio")
        self.row_aspect.grid(row=1, column=0, columnspan=2, sticky="ew",
                             padx=(0, PAD // 2), pady=PAD // 2)

        self.row_resolution = InfoRow(page, self, self.icon("resolution"), "Resolution")
        self.row_resolution.grid(row=1, column=2, columnspan=2, sticky="ew",
                                 padx=(PAD // 2, 0), pady=PAD // 2)

        self.row_voice = InfoRow(page, self, self.icon("voice"), "Voice")
        self.row_voice.grid(row=2, column=0, columnspan=2, sticky="ew",
                            padx=(0, PAD // 2), pady=PAD // 2)

        self.row_bitrate = InfoRow(page, self, self.icon("bitrate"), "Bitrate")
        self.row_bitrate.grid(row=2, column=2, columnspan=2, sticky="ew",
                              padx=(PAD // 2, 0), pady=PAD // 2)

        self.row_output = InfoRow(page, self, self.icon("folder"), "Output path")
        self.row_output.grid(row=3, column=0, columnspan=2, sticky="ew",
                             padx=(0, PAD // 2), pady=PAD // 2)

        self.row_render = ActionRow(page, self, self.icon("play"), "Render",
                                    "Idle", self.icon("play"), self.start_render)
        self.row_render.grid(row=3, column=2, columnspan=2, sticky="ew",
                             padx=(PAD // 2, 0), pady=PAD // 2)

        self.tile_scenes = Tile(page, self, self.icon("scenes"), "Scenes", "0")
        self.tile_scenes.grid(row=4, column=0, sticky="ew",
                              padx=(0, PAD // 2), pady=(PAD, PAD // 2))

        self.tile_last = Tile(page, self, self.icon("status"), "Last render", "Never")
        self.tile_last.grid(row=4, column=1, sticky="ew",
                            padx=PAD // 2, pady=(PAD, PAD // 2))

        self.tile_size = Tile(page, self, self.icon("resolution"), "Video",
                              "1080x1920", corner="1080p")
        self.tile_size.grid(row=4, column=2, sticky="ew",
                            padx=PAD // 2, pady=(PAD, PAD // 2))

        self.tile_key = Tile(page, self, self.icon("key"), "Footage key", "Missing")
        self.tile_key.grid(row=4, column=3, sticky="ew",
                           padx=(PAD // 2, 0), pady=(PAD, PAD // 2))
        return page

    def make_from_text(self):
        """The front door's button: write a script from the pasted text, then show it."""
        text = self.door_box.get("1.0", "end").strip()
        if self.is_rendering:
            return
        if not text:
            self.door_status.configure(text="Paste or type some text first.",
                                       text_color=ERR_COLOR)
            return
        if not self._has_footage_key() and not imagegen.installed():
            WelcomeDialog(self)   # nothing to show the scenes with yet: the free key comes first
            return
        self.door_status.configure(text="", text_color=TEXT_DIM)
        self._after_draft = "render" if self.var_straight.get() else "board"
        dialog = DraftDialog(self)
        dialog.prefill(text)
        name = self.var_template.get()
        template = draft.TEMPLATES.get(name)
        if template is None:
            dialog.analyse()   # straight to the questions: the text is already here
            return
        # A template answers the questions itself; they are still shown, to change.
        self.var_auto_cards.set(template.cards)
        self.caption_vars["caption_style"].set(template.caption_style)
        self.var_style.set(template.look)
        dialog.style = template.audience.style
        dialog.var_platform.set(template.audience.platform)   # before the length it presets
        dialog._apply(draft.Suggestion(template.audience, template.length,
                                       f"The {name} template. Change any answer if you like."))

    def refresh_home(self):
        if not hasattr(self, "row_aspect"):
            return
        aspect = self.var_aspect.get()
        quality = self.var_resolution.get()
        w, h, _orientation, bitrate = resolve_target(aspect, quality)

        label = "9:16 (Shorts / TikTok)" if aspect == "9:16" else "16:9 (Landscape)"
        self.row_aspect.set_value(label)
        self.row_resolution.set_value(f"{w}x{h}  ·  {quality}")
        self.row_voice.set_value(voices.persona(self.var_voice.get()).short)
        self.row_bitrate.set_value(bitrate)
        self.row_output.set_value(ellipsize(self.var_output.get() or "Not set"))
        self.row_render.set_value("Rendering..." if self.is_rendering else "Idle")

        scenes = count_scenes(
            self.script_box.get("1.0", "end") if hasattr(self, "script_box") else ""
        )
        if scenes is None:
            self.tile_scenes.set_value("!", ERR_COLOR)
        else:
            self.tile_scenes.set_value(str(scenes))

        color = None
        if self.last_render.startswith("Success"):
            color = OK_COLOR
        elif self.last_render.startswith("Failed"):
            color = ERR_COLOR
        self.tile_last.set_value(self.last_render, color)

        self.tile_size.set_value(f"{w}x{h}")
        self.tile_size.set_corner(quality)
        has_key = self._has_footage_key()   # Pexels or Pixabay: either is enough
        self.tile_key.set_value("Set" if has_key else "Missing",
                                OK_COLOR if has_key else ERR_COLOR)
        self.refresh_estimate()

    def refresh_estimate(self):
        """About how long the script is, and whether a chosen length is in reach."""
        if not hasattr(self, "estimate_label"):
            return
        try:
            parsed = parse_script(self.script_box.get("1.0", "end"))
        except ScriptError:
            self.estimate_label.configure(text="")
            return
        guess = pacing.estimate(
            parsed, voices.persona(self.var_voice.get()),
            PADDING_OPTIONS.get(self.var_padding.get(), DEFAULT_PADDING))
        text, warning = pacing.describe(guess, len(parsed),
                                        pacing.TARGETS.get(self.var_target.get()))
        pictures = sum(scene.is_ai for scene in parsed)
        if pictures and imagegen.installed():
            wait = pictures * 20 * (2 if self.var_versions.get() == "2" else 1)
            text += (f" · {pictures} AI picture{'s' if pictures != 1 else ''}, about "
                     + (f"{wait} s" if wait < 90 else f"{round(wait / 60)} min") + " more")
        self.estimate_label.configure(text=text,
                                      text_color=WARN_COLOR if warning else TEXT_MUTED)

    # -------------------------------------------------------------- CREATE --
    def _build_create(self):
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        self._caption(page, "SCRIPT", 0, pady=(0, 6))

        editor_card = Card(page)
        editor_card.grid(row=1, column=0, sticky="nsew", pady=(0, PAD))
        editor_card.grid_columnconfigure(0, weight=1)
        editor_card.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(editor_card, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        ctk.CTkLabel(header, text=self.icon("script"), font=self.font_icon,
                     text_color=TEXT_MUTED).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(header, text="Your script", font=self.font_body,
                     text_color=TEXT).pack(side="left")
        ctk.CTkLabel(header, text="Visual: and Voice: per scene · optional Duration:, "
                                  "Padding:, Zoom:",
                     font=self.font_tiny, text_color=TEXT_DIM).pack(side="right")

        self.script_box = ctk.CTkTextbox(
            editor_card, font=self.font_mono, corner_radius=4,
            fg_color=FIELD_BG, text_color=TEXT, border_width=0, wrap="word",
        )
        self.script_box.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        self.script_box.insert("1.0", self.settings["script"])
        self.script_box.bind("<KeyRelease>", self._on_script_changed)

        control_card = Card(page)
        control_card.grid(row=2, column=0, sticky="ew")
        control_card.grid_columnconfigure(0, weight=1)

        # Same variable as the Settings dropdown, so the two stay in step.
        voice_bar = ctk.CTkFrame(control_card, fg_color="transparent")
        voice_bar.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 0))
        ctk.CTkLabel(voice_bar, text=self.icon("voice"), font=self.font_icon,
                     text_color=TEXT_MUTED).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(voice_bar, text="Voice", font=self.font_body,
                     text_color=TEXT).pack(side="left", padx=(0, 12))
        self.voice_menus.append(ctk.CTkOptionMenu(
            voice_bar, values=VOICE_OPTIONS, variable=self.var_voice,
            width=360, height=32, corner_radius=4, font=self.font_body,
            fg_color=FIELD_BG, button_color=FIELD_BG, button_hover_color=CARD_HOVER,
            text_color=TEXT, dropdown_fg_color=CARD_BG, dropdown_text_color=TEXT,
            dropdown_hover_color=CARD_HOVER, dropdown_font=self.font_body,
            dynamic_resizing=False,
        ))
        self.voice_menus[-1].pack(side="left")
        self.preview_button = ctk.CTkButton(
            voice_bar, text=f"{self.icon('play')}  Preview Voice", width=150,
            height=32, corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
            hover_color=CARD_HOVER, border_width=1, border_color=FIELD_BORDER,
            text_color=TEXT, command=self.preview_voice,
        )
        self.preview_button.pack(side="left", padx=(8, 0))

        self._look_bar(control_card, shape=False).grid(row=1, column=0, sticky="ew", padx=16,
                                                       pady=(10, 0))

        # Length and versions, with a running estimate of how long the script is.
        pace_bar = ctk.CTkFrame(control_card, fg_color="transparent")
        pace_bar.grid(row=2, column=0, sticky="ew", padx=16, pady=(10, 0))
        ctk.CTkLabel(pace_bar, text=self.icon("timer"), font=self.font_icon,
                     text_color=TEXT_MUTED).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(pace_bar, text="Length", font=self.font_body,
                     text_color=TEXT).pack(side="left", padx=(0, 12))
        FluentSegmented(pace_bar, self, list(pacing.TARGETS), self.var_target, 232).pack(side="left")
        ctk.CTkLabel(pace_bar, text="Versions", font=self.font_body,
                     text_color=TEXT).pack(side="left", padx=(18, 12))
        FluentSegmented(pace_bar, self, list(VERSION_OPTIONS), self.var_versions, 84).pack(side="left")
        self.estimate_label = ctk.CTkLabel(pace_bar, text="", font=self.font_tiny,
                                           text_color=TEXT_MUTED, anchor="e")
        self.estimate_label.pack(side="right")

        # The look of AI pictures: only there once AI images are downloaded.
        self.look_bar = ctk.CTkFrame(control_card, fg_color="transparent")
        ctk.CTkLabel(self.look_bar, text=self.icon("images"), font=self.font_icon,
                     text_color=TEXT_MUTED).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(self.look_bar, text="AI pictures", font=self.font_body,
                     text_color=TEXT).pack(side="left", padx=(0, 12))
        FluentSegmented(self.look_bar, self, list(imagegen.LOOKS), self.var_ai_look,
                        340).pack(side="left")
        look_hint = ctk.CTkLabel(self.look_bar, text=AI_LOOK_HINTS.get(self.var_ai_look.get(), ""),
                                 font=self.font_tiny, text_color=TEXT_MUTED, anchor="w",
                                 justify="left", wraplength=420)
        look_hint.pack(side="left", padx=(14, 0))
        self.var_ai_look.trace_add("write", lambda *_args: look_hint.configure(
            text=AI_LOOK_HINTS.get(self.var_ai_look.get(), "")))
        self._sync_look_bar()

        top = ctk.CTkFrame(control_card, fg_color="transparent")
        top.grid(row=4, column=0, sticky="ew", padx=16, pady=(12, 8))
        top.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(top, text="Ready", font=self.font_body,
                                         text_color=TEXT_MUTED, anchor="w")
        self.status_label.grid(row=0, column=0, sticky="w")
        # Pexels and Pixabay both ask apps to show where footage comes from.
        ctk.CTkLabel(top, text="Stock footage from Pexels and Pixabay",
                     font=self.font_tiny, text_color=TEXT_DIM,
                     anchor="w").grid(row=1, column=0, sticky="w")

        self.render_button = ctk.CTkButton(
            top, text=f"{self.icon('play')}   Render Video", font=self.font_bold,
            width=190, height=38, corner_radius=4, fg_color=ACCENT,
            hover_color=ACCENT_HOVER, text_color=ACCENT_TEXT,
            command=self.start_render,
        )
        self.render_button.grid(row=0, column=3, sticky="e")
        self.scenes_button = ctk.CTkButton(
            top, text=f"{self.icon('scenes')}   Storyboard", font=self.font_body,
            width=150, height=38, corner_radius=4, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
            command=self.open_scene_preview,
        )
        self.scenes_button.grid(row=0, column=2, sticky="e", padx=(0, 8))
        self.draft_button = ctk.CTkButton(
            top, text=f"{self.icon('script')}   New from text", font=self.font_body,
            width=150, height=38, corner_radius=4, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
            command=self.open_draft,
        )
        self.draft_button.grid(row=0, column=1, sticky="e", padx=(0, 8))

        self.progress = ctk.CTkProgressBar(control_card, height=4, corner_radius=2,
                                           progress_color=ACCENT, fg_color=FIELD_BG)
        self.progress.grid(row=5, column=0, sticky="ew", padx=16, pady=(0, 16))
        self.progress.set(0)
        self.progress.grid_remove()
        return page

    def _look_bar(self, parent, shape=True):
        """One row for how the video looks: its shape and its captions.

        The same variables as Settings, so every copy of this row (Home, Storyboard,
        Script) shows and changes the same choices.
        """
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        if shape:
            ctk.CTkLabel(bar, text=self.icon("aspect"), font=self.font_icon,
                         text_color=TEXT_MUTED).pack(side="left", padx=(0, 10))
            ctk.CTkLabel(bar, text="Shape", font=self.font_body,
                         text_color=TEXT).pack(side="left", padx=(0, 10))
            FluentSegmented(bar, self, RATIO_OPTIONS, self.var_aspect, 150).pack(
                side="left", padx=(0, 18))
        ctk.CTkLabel(bar, text=self.icon("captions"), font=self.font_icon,
                     text_color=TEXT_MUTED).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(bar, text="Captions", font=self.font_body,
                     text_color=TEXT).pack(side="left", padx=(0, 12))
        ctk.CTkSwitch(
            bar, text="", width=44, variable=self.var_captions,
            onvalue=True, offvalue=False, progress_color=ACCENT,
        ).pack(side="left", padx=(0, 6))
        first = not hasattr(self, "caption_menus")
        if first:
            self.caption_menus = []
        for key, allowed, _default in CAPTION_SETTINGS:
            menu = ctk.CTkOptionMenu(
                bar, values=list(allowed), variable=self.caption_vars[key],
                width=116 if not shape else 106, height=32, corner_radius=4,
                font=self.font_body,
                fg_color=FIELD_BG, button_color=FIELD_BG, button_hover_color=CARD_HOVER,
                text_color=TEXT, text_color_disabled=TEXT_DIM, dropdown_fg_color=CARD_BG,
                dropdown_text_color=TEXT, dropdown_hover_color=CARD_HOVER,
                dropdown_font=self.font_body, dynamic_resizing=False,
            )
            menu.pack(side="left", padx=(6, 0))
            self.caption_menus.append(menu)
        if first:
            self.var_captions.trace_add("write", self._sync_caption_controls)
            self.var_aspect.trace_add("write", self._shape_changed)
        self._sync_caption_controls()
        return bar

    def _shape_changed(self, *_args):
        """Another shape means other clips and other picture sizes: the Storyboard starts over."""
        self.board_clips, self.board_pos, self.board_text = {}, {}, None
        if self.current_page == "board":
            self.refresh_board(force=True)

    def _sync_look_bar(self):
        """Show the AI pictures row on Create only while AI images are installed."""
        if not hasattr(self, "look_bar"):
            return
        if imagegen.installed():
            self.look_bar.grid(row=3, column=0, sticky="ew", padx=16, pady=(10, 0))
        else:
            self.look_bar.grid_remove()

    def _sync_caption_controls(self, *_args):
        """The caption choices only matter while captions are on."""
        state = "normal" if self.var_captions.get() else "disabled"
        for menu in self.caption_menus:
            menu.configure(state=state)

    def _on_script_changed(self, _event=None):
        if self._refresh_job:
            try:
                self.after_cancel(self._refresh_job)
            except Exception:
                pass
        self._refresh_job = self.after(350, self._commit_script)

    def _commit_script(self):
        self._refresh_job = None
        self.settings = self._collect_settings()
        save_settings(self.settings)
        self.refresh_home()

    # ----------------------------------------------------------------- LOG --
    def _build_log(self):
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        self._caption(page, "EVENT LOG", 0, pady=(0, 6))

        card = Card(page)
        card.grid(row=1, column=0, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        ctk.CTkLabel(header, text=self.icon("log"), font=self.font_icon,
                     text_color=TEXT_MUTED).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(header, text="Output", font=self.font_body,
                     text_color=TEXT).pack(side="left")
        ctk.CTkButton(header, text=f"{self.icon('clear')}  Clear", width=88,
                      height=28, corner_radius=4, font=self.font_tiny,
                      fg_color=FIELD_BG, hover_color=CARD_HOVER,
                      border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                      command=self.clear_log).pack(side="right")
        # Packed only while a render is running (see _set_busy).
        self.log_cancel = ctk.CTkButton(
            header, text=f"{self.icon('stop')}  Cancel", width=88, height=28, corner_radius=4,
            font=self.font_tiny, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
            command=self.cancel_render)

        self.log_box = ctk.CTkTextbox(
            card, font=self.font_body, corner_radius=4, fg_color=FIELD_BG,
            text_color=LOG_BODY, border_width=0, wrap="word",
        )
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        self._configure_log_tags()

        # Render jumps to this page, so progress has to be visible here too.
        self.log_progress = ctk.CTkProgressBar(card, height=4, corner_radius=2,
                                               progress_color=ACCENT, fg_color=FIELD_BG)
        self.log_progress.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 14))
        self.log_progress.set(0)
        self.log_progress.grid_remove()

        self.log_empty = ctk.CTkLabel(
            card, text="Nothing logged yet.", font=self.font_body,
            text_color=TEXT_DIM, fg_color=FIELD_BG,   # blend into the well
        )
        self.log_empty.place(relx=0.5, rely=0.55, anchor="center")

        self.log_box.configure(state="disabled")
        return page

    def _configure_log_tags(self):
        """(Re)apply severity colours and spacing - rerun on a theme change.

        Margins live on the tags rather than the widget: CustomTkinter's
        configure() rejects lmargin/rmargin, but Tk text tags accept them.
        """
        for name, color in LOG_TAGS.items():
            self.log_box.tag_config(
                name, foreground=theme_color(color),
                spacing1=3, spacing3=3, lmargin1=10, lmargin2=28, rmargin=10,
            )
        # Defined last so it outranks the severity tags on lmargin1: an embedded
        # newline (the "saved to:" message) is a real line, not a wrapped one,
        # so it needs the hanging indent applied explicitly.
        self.log_box.tag_config("cont", lmargin1=28, spacing1=0)

    def append_log(self, message: str):
        """Main-thread only.  Worker threads go through UiBridge.log()."""
        self.session_log.write(message)
        self.recent_lines.extend(message.split("\n"))
        severity = log_severity(message)
        self.log_box.configure(state="normal")
        for index, line in enumerate(message.split("\n")):
            tags = (severity,) if index == 0 else (severity, "cont")
            self.log_box.insert("end", line + "\n", tags)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        self.log_empty.place_forget()
        last_line = message.strip().split("\n")[0]
        if hasattr(self, "status_label") and last_line:
            self.status_label.configure(text=ellipsize(last_line, 80))

    def clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")
        self.log_empty.place(relx=0.5, rely=0.55, anchor="center")

    # ------------------------------------------------------------ FEEDBACK --
    def _build_feedback(self):
        page = self._scroll_page()
        page.grid_columnconfigure(0, weight=1)

        self._caption(page, "ABOUT", 0, pady=(0, 6))

        card = Card(page)
        card.grid(row=1, column=0, sticky="ew", pady=(0, PAD))
        ctk.CTkLabel(card, text=f"{APP_NAME}  {APP_VERSION}", font=self.font_title,
                     text_color=TEXT, anchor="w").pack(anchor="w", padx=18, pady=(16, 2))
        ctk.CTkLabel(
            card,
            text="Pexels & Pixabay stock footage · edge-tts neural voices · MoviePy render",
            font=self.font_body, text_color=TEXT_MUTED, anchor="w",
        ).pack(anchor="w", padx=18, pady=(0, 10))
        update_row = ctk.CTkFrame(card, fg_color="transparent")
        update_row.pack(fill="x", padx=18, pady=(0, 16))
        self.update_button = ctk.CTkButton(
            update_row, text="Check for updates", width=140, height=30, corner_radius=4,
            font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
            command=lambda: self.check_for_updates(manual=True),
        )
        self.update_button.pack(side="left")
        self.update_status = ctk.CTkLabel(update_row, text="", font=self.font_body,
                                          text_color=TEXT_MUTED, anchor="w")
        self.update_status.pack(side="left", padx=(12, 0))

        self._caption(page, "SYSTEM", 2)

        self.row_effect = InfoRow(page, self, self.icon("glass"), "Transparency", "-")
        self.row_effect.grid(row=3, column=0, sticky="ew", pady=PAD // 2)

        row_py = InfoRow(page, self, self.icon("info"), "Python",
                         sys.version.split()[0])
        row_py.grid(row=4, column=0, sticky="ew", pady=PAD // 2)

        row_ctk = InfoRow(page, self, self.icon("info"), "CustomTkinter",
                          getattr(ctk, "__version__", "?"))
        row_ctk.grid(row=5, column=0, sticky="ew", pady=PAD // 2)

        row_cfg = InfoRow(page, self, self.icon("folder"), "Settings",
                          ellipsize(SETTINGS_FILE, 54))
        row_cfg.grid(row=6, column=0, sticky="ew", pady=PAD // 2)

        self._caption(page, "SHORTCUTS", 7)

        actions = Card(page)
        actions.grid(row=8, column=0, sticky="ew", pady=PAD // 2)
        bar = ctk.CTkFrame(actions, fg_color="transparent")
        bar.pack(fill="x", padx=14, pady=14)
        ctk.CTkButton(bar, text=f"{self.icon('folder')}  Open output folder",
                      height=32, corner_radius=4, font=self.font_body,
                      fg_color=FIELD_BG, hover_color=CARD_HOVER,
                      border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                      command=self.open_output_folder).pack(side="left", padx=(0, 8))
        ctk.CTkButton(bar, text=f"{self.icon('settings')}  Open settings folder",
                      height=32, corner_radius=4, font=self.font_body,
                      fg_color=FIELD_BG, hover_color=CARD_HOVER,
                      border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                      command=self.open_settings_folder).pack(side="left", padx=(0, 8))
        ctk.CTkButton(bar, text=f"{self.icon('key')}  Setup guide",
                      height=32, corner_radius=4, font=self.font_body,
                      fg_color=FIELD_BG, hover_color=CARD_HOVER,
                      border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                      command=lambda: WelcomeDialog(self)).pack(side="left")
        ctk.CTkButton(bar, text=f"{self.icon('info')}  Show the tour",
                      height=32, corner_radius=4, font=self.font_body,
                      fg_color=FIELD_BG, hover_color=CARD_HOVER,
                      border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                      command=lambda: TourDialog(self)).pack(side="left", padx=(8, 0))
        help_bar = ctk.CTkFrame(actions, fg_color="transparent")
        help_bar.pack(fill="x", padx=14, pady=(0, 14))
        ctk.CTkButton(help_bar, text=f"{self.icon('feedback')}  Report a problem",
                      height=32, corner_radius=4, font=self.font_body,
                      fg_color=FIELD_BG, hover_color=CARD_HOVER,
                      border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                      command=self.open_report).pack(side="left", padx=(0, 8))
        ctk.CTkButton(help_bar, text=f"{self.icon('log')}  Open log folder",
                      height=32, corner_radius=4, font=self.font_body,
                      fg_color=FIELD_BG, hover_color=CARD_HOVER,
                      border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                      command=lambda: self._open_made_folder(LOG_DIR)).pack(side="left")
        return page

    def open_output_folder(self):
        target = os.path.dirname(self.var_output.get().strip()) or default_output_dir()
        self._open_folder(target)

    # -- problem reports ------------------------------------------------------
    def scrub(self, text: str) -> str:
        """``text`` without keys or the names this PC's user goes by."""
        text = diagnostics.redact(text, [self.var_api.get(), self.var_pixabay.get()],
                                  os.environ.get("USERNAME", ""))
        # The profile folder can be named differently from the account.
        return diagnostics.redact(text, user_name=os.path.basename(os.path.expanduser("~")))

    def open_report(self, error=""):
        """Show the report window. Nothing leaves the PC unless the user sends it."""
        report = diagnostics.build_report(
            APP_VERSION, IS_FROZEN, self._collect_settings(), list(self.recent_lines),
            error, os.environ.get("USERNAME", ""))
        ReportDialog(self, self.scrub(report), error)

    def report_callback_exception(self, exc, val, tb):
        """Tk calls this for an error in a button or timer: keep it, don't lose it."""
        trace = "".join(traceback.format_exception(exc, val, tb))
        self.session_log.write(trace)
        self._unexpected(trace)

    def _unexpected(self, trace: str):
        """Main thread. Put an already-logged trace on the Log page and into reports."""
        try:
            self.recent_lines.extend(trace.rstrip().split("\n"))
            last = trace.strip().splitlines()[-1] if trace.strip() else "unknown"
            self.append_log(f"❌ Unexpected error: {last}")
        except Exception:
            pass   # reporting an error must never raise another

    def _install_error_hooks(self):
        """Keep uncaught errors from any thread.

        The trace is written to the log file at once, in case the app is going
        down, then handed to the window through the UI queue. The normal
        handlers still run, so a console shows the error as before.
        """
        usual_thread_hook, usual_hook = threading.excepthook, sys.excepthook

        def keep(trace):
            self.session_log.write(trace)
            self._queue.put(("crash", {"trace": trace}))

        def from_thread(args):
            keep("".join(traceback.format_exception(
                args.exc_type, args.exc_value, args.exc_traceback)))
            usual_thread_hook(args)

        def from_main(exc, val, tb):
            keep("".join(traceback.format_exception(exc, val, tb)))
            usual_hook(exc, val, tb)

        threading.excepthook = from_thread
        sys.excepthook = from_main

    def open_settings_folder(self):
        self._open_made_folder(SETTINGS_DIR)

    def _open_made_folder(self, folder):
        """Open ``folder`` in Explorer, creating it first; say so if it can't be."""
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            self.append_log(f"⚠️ Couldn't create {folder} ({exc.strerror or exc}).")
            return
        self._open_folder(folder)

    @staticmethod
    def _open_folder(path):
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass

    # ------------------------------------------------------------ SETTINGS --
    def _build_settings(self):
        page = self._scroll_page()
        page.grid_columnconfigure(0, weight=1)
        rows = itertools.count()   # sections and cards, top to bottom

        self._caption(page, "STOCK FOOTAGE", next(rows), pady=(0, 6))
        self.api_entry = self._key_row(
            page, next(rows), "Pexels", "Pexels API key",
            "One footage key is enough. Stored locally in settings.json",
            self.var_api, "Paste your Pexels API key",
        )
        self.pixabay_entry = self._key_row(
            page, next(rows), "Pixabay", "Pixabay API key",
            "Works on its own, or as a backup when Pexels finds nothing",
            self.var_pixabay, "Paste your Pixabay API key",
        )

        self._caption(page, "OUTPUT", next(rows))

        path_row = SettingRow(page, self, self.icon("folder"), "Save video to",
                              "Default folder and name for new videos")
        path_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        focus_accent(ctk.CTkEntry(
            path_row.control, textvariable=self.var_output, width=300, height=32,
            corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
            border_color=FIELD_BORDER, border_width=1, text_color=TEXT,
        )).pack(side="left")
        ctk.CTkButton(
            path_row.control, text="Browse", width=84, height=32, corner_radius=4,
            font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER,
            text_color=TEXT, command=self.choose_save_location,
        ).pack(side="left", padx=(6, 0))

        ask_row = SettingRow(page, self, self.icon("folder"), "Ask where to save each video",
                             "Opens a Save window when you render. Off: saves to the path "
                             "above, numbering new videos so none is overwritten")
        ask_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkSwitch(
            ask_row.control, text="", width=44, variable=self.var_ask_save,
            onvalue=True, offvalue=False, progress_color=ACCENT,
        ).pack(side="left")

        self._caption(page, "VIDEO", next(rows))

        ratio_row = SettingRow(page, self, self.icon("aspect"), "Aspect ratio",
                               "Portrait for Shorts / TikTok, landscape for YouTube")
        ratio_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self._segmented(ratio_row.control, RATIO_OPTIONS, self.var_aspect, 170)

        res_row = SettingRow(page, self, self.icon("resolution"), "Resolution",
                             "1080p renders at 8000k, 720p at 5000k")
        res_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self._segmented(res_row.control, RESOLUTION_OPTIONS, self.var_resolution, 170)

        padding_row = SettingRow(page, self, self.icon("timer"), "Scene padding",
                                 "Pause after each voice line. A script's Padding: overrides it")
        padding_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self._segmented(padding_row.control, list(PADDING_OPTIONS), self.var_padding, 260)

        voice_row = SettingRow(page, self, self.icon("voice"), "Voice engine",
                               "Microsoft Edge neural text-to-speech")
        voice_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self.voice_menus.append(ctk.CTkOptionMenu(
            voice_row.control, values=VOICE_OPTIONS, variable=self.var_voice,
            width=360, dynamic_resizing=False, height=32, corner_radius=4, font=self.font_body,
            fg_color=FIELD_BG, button_color=FIELD_BG, button_hover_color=CARD_HOVER,
            text_color=TEXT, dropdown_fg_color=CARD_BG, dropdown_text_color=TEXT,
            dropdown_hover_color=CARD_HOVER, dropdown_font=self.font_body,
        ))
        self.voice_menus[-1].pack(side="left")
        why_not = myvoice.available()   # an NVIDIA card with 6 GB or more (checked once)
        mine_row = SettingRow(page, self, self.icon("voice"), "My voice (beta)",
                              why_not or "Record six sentences, and scripts are read in a voice "
                                         "like yours. Needs the voice pack (about 7 GB)")
        mine_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkButton(mine_row.control, text="Make my voice", width=130, height=32,
                      corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
                      hover_color=CARD_HOVER, border_width=1, border_color=FIELD_BORDER,
                      text_color=TEXT, command=lambda: MyVoiceDialog(self),
                      state="disabled" if why_not else "normal").pack(side="left")

        self._caption(page, "CAPTIONS", next(rows))

        captions_row = SettingRow(page, self, self.icon("captions"), "Captions",
                                  "Burns the spoken words into the video and saves an .srt file")
        captions_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkSwitch(
            captions_row.control, text="", width=44, variable=self.var_captions,
            onvalue=True, offvalue=False, progress_color=ACCENT,
        ).pack(side="left")

        for key, glyph, title, subtitle, width in (
            ("caption_style", "script", "Style",
             "Highlight the spoken word, show one word at a time, or plain text", 300),
            ("caption_size", "font_size", "Size", "How big the caption text is", 260),
            ("caption_color", "color", "Highlight colour",
             "How the word being spoken stands out", 400),
            ("caption_position", "position", "Position",
             "Where the captions sit on the video", 260),
        ):
            allowed = next(a for k, a, _d in CAPTION_SETTINGS if k == key)
            option_row = SettingRow(page, self, self.icon(glyph), title, subtitle)
            option_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
            self._segmented(option_row.control, list(allowed), self.caption_vars[key], width)

        self._caption(page, "START AND END CARDS", next(rows))
        for var_on, var_text, title, subtitle, hint in (
                (self.var_intro_on, self.var_intro_text, "Channel name card",
                 "A 2-second card before every video", "Your channel name"),
                (self.var_outro_on, self.var_outro_text, "End card",
                 "A 2-second card after every video", "Subscribe for more")):
            bookend_row = SettingRow(page, self, self.icon("captions"), title, subtitle)
            bookend_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
            ctk.CTkSwitch(bookend_row.control, text="", width=44, variable=var_on,
                          onvalue=True, offvalue=False, progress_color=ACCENT,
                          ).pack(side="left", padx=(0, 10))
            focus_accent(ctk.CTkEntry(
                bookend_row.control, textvariable=var_text, width=240, height=32,
                corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
                border_color=FIELD_BORDER, text_color=TEXT, placeholder_text=hint,
            )).pack(side="left")

        self._caption(page, "AUDIO", next(rows))

        music_row = SettingRow(page, self, self.icon("music"), "Background music",
                               "Plays under the video and ducks while the voice speaks")
        music_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkSwitch(
            music_row.control, text="", width=44, variable=self.var_music_enabled,
            onvalue=True, offvalue=False, progress_color=ACCENT,
        ).pack(side="left")

        track_row = SettingRow(page, self, self.icon("folder"), "Track",
                               "Drop tracks in the Music folder, or browse for any file")
        track_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self.music_menu = ctk.CTkOptionMenu(
            track_row.control, values=["No tracks yet"], variable=self.var_music_track,
            command=self._pick_track, width=220, height=32, corner_radius=4,
            font=self.font_body, fg_color=FIELD_BG, button_color=FIELD_BG,
            button_hover_color=CARD_HOVER, text_color=TEXT, dropdown_fg_color=CARD_BG,
            dropdown_text_color=TEXT, dropdown_hover_color=CARD_HOVER,
            dropdown_font=self.font_body, dynamic_resizing=False,
        )
        self.music_menu.pack(side="left")
        for label, command in (("Browse", self.browse_music),
                               ("Open folder", self.open_music_folder)):
            ctk.CTkButton(
                track_row.control, text=label, width=96, height=32, corner_radius=4,
                font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                border_width=1, border_color=FIELD_BORDER,
                text_color=TEXT, command=command,
            ).pack(side="left", padx=(6, 0))
        self._refresh_tracks()

        sfx_row = SettingRow(page, self, self.icon("music"), "Sound effects",
                             "A soft whoosh on each cut and a pop as a card appears")
        sfx_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkSwitch(sfx_row.control, text="", width=44, variable=self.var_sfx, onvalue=True,
                      offvalue=False, progress_color=ACCENT).pack(side="left")
        motion_row = SettingRow(page, self, self.icon("scenes"), "Cuts on the beat, smart framing",
                                "Scene changes land on the music's beats; the picture moves "
                                "toward where the detail is")
        motion_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        style_row = SettingRow(page, self, self.icon("script"), "Style",
                               "Normal footage, or every scene drawn on screen: chalk on a "
                               "blackboard, or a flat-colour drawing. Fast on any PC")
        style_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self._segmented(style_row.control, list(drawn.STYLES), self.var_style, 330)
        ctk.CTkSwitch(motion_row.control, text="", width=44, variable=self.var_smart_motion,
                      onvalue=True, offvalue=False, progress_color=ACCENT).pack(side="left")
        qsv_row = SettingRow(page, self, self.icon("scenes"), "Encode on the Intel graphics chip",
                             "Quick Sync: faster on Intel laptops. Falls back to the normal "
                             "encoder by itself where it isn't there or fails")
        qsv_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkSwitch(qsv_row.control, text="", width=44, variable=self.var_quick_sync,
                      onvalue=True, offvalue=False, progress_color=ACCENT).pack(side="left")
        cache_row = SettingRow(page, self, self.icon("folder"), "Kept clips and voices",
                               "Re-renders reuse downloaded clips and spoken lines. "
                               f"Limit {cache.LIMIT_BYTES // 1024 ** 3} GB; oldest go first")
        cache_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self.cache_label = ctk.CTkLabel(cache_row.control, text="", font=self.font_body,
                                        text_color=TEXT_MUTED)
        ctk.CTkButton(
            cache_row.control, text="Clear cache", width=110, height=32, corner_radius=4,
            font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER, border_width=1,
            border_color=FIELD_BORDER, text_color=TEXT, command=self.clear_cache,
        ).pack(side="left")
        self.cache_label.pack(side="left", padx=(10, 0))
        self._show_cache_size()

        self._caption(page, "APPEARANCE", next(rows))

        theme_row = SettingRow(page, self, self.icon("theme"), "Dark theme",
                               "Switches the whole app between dark and light")
        theme_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkSwitch(
            theme_row.control, text="", width=44, variable=self.var_theme,
            onvalue="dark", offvalue="light", progress_color=ACCENT,
            command=self.apply_theme,
        ).pack(side="left")

        glass_row = SettingRow(page, self, self.icon("glass"), "Window translucency",
                               "Mica / acrylic where supported, alpha elsewhere")
        glass_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkSwitch(
            glass_row.control, text="", width=44, variable=self.var_translucent,
            onvalue=True, offvalue=False, progress_color=ACCENT,
            command=self.apply_effects,
        ).pack(side="left")

        self._caption(page, "SCRIPT WRITER", next(rows))
        web_row = SettingRow(page, self, self.icon("info"), "Look facts up on Wikipedia",
                             "For an idea that asks for facts (top 5, latest, fastest...), the "
                             "Smart writer reads Wikipedia first. Only the search words are sent")
        web_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkSwitch(web_row.control, text="", width=44, variable=self.var_web_facts,
                      onvalue=True, offvalue=False, progress_color=ACCENT).pack(side="left")

        writer_row = SettingRow(
            page, self, self.icon("script"), "Smart writer",
            f"{writer.MODEL.name}, an open-source AI that writes scripts on this PC. "
            "Nothing you paste is sent anywhere")
        writer_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self.writer_state = ctk.CTkLabel(writer_row.control, text="", font=self.font_body,
                                         text_color=TEXT_MUTED)
        self.writer_state.pack(side="left", padx=(0, 12))
        self.writer_button = ctk.CTkButton(
            writer_row.control, text="", width=96, height=32, corner_radius=4,
            font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT)
        self.writer_button.pack(side="left")
        self.writer_listeners.append(self._writer_row_changed)
        self._writer_row_changed()

        threads_row = SettingRow(
            page, self, self.icon("timer"), "AI picture threads",
            "How many processor threads make an AI picture when there is no graphics card. "
            "Auto uses the result of Test speed in the AI images tab")
        threads_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkOptionMenu(
            threads_row.control, values=list(AI_THREAD_OPTIONS), variable=self.var_ai_threads,
            width=110, height=32, corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
            button_color=FIELD_BG, button_hover_color=CARD_HOVER, text_color=TEXT,
            dropdown_fg_color=CARD_BG, dropdown_text_color=TEXT, dropdown_hover_color=CARD_HOVER,
            dropdown_font=self.font_body, dynamic_resizing=False,
            command=lambda _value: save_settings(self._collect_settings()),
        ).pack(side="left")

        gpu_row = SettingRow(
            page, self, self.icon("resolution"), "Use the graphics card",
            "Faster on PCs with a dedicated graphics card. Turn it off if the Smart "
            "writer fails to start")
        gpu_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkSwitch(
            gpu_row.control, text="", width=44, variable=self.var_writer_gpu,
            onvalue=True, offvalue=False, progress_color=ACCENT,
        ).pack(side="left")

        cards_row = SettingRow(
            page, self, self.icon("script"), "Add cards automatically",
            "New from text puts key combos like Win + V and big numbers on screen as "
            "animated cards. You can edit or delete the Card: lines in the script")
        cards_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkSwitch(
            cards_row.control, text="", width=44, variable=self.var_auto_cards,
            onvalue=True, offvalue=False, progress_color=ACCENT,
        ).pack(side="left")

        self._caption(page, "VOICE", next(rows))

        voice_row = SettingRow(
            page, self, self.icon("voice"), "Offline voice",
            f"{localvoice.MODEL.name}, an open-source voice that speaks on this PC. "
            "No internet needed once it is downloaded")
        voice_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self.voice_state = ctk.CTkLabel(voice_row.control, text="", font=self.font_body,
                                        text_color=TEXT_MUTED)
        self.voice_state.pack(side="left", padx=(0, 12))
        self.voice_button = ctk.CTkButton(
            voice_row.control, text="Download", width=96, height=32, corner_radius=4,
            font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
            command=self.start_voice_download)
        self.voice_button.pack(side="left")
        self._voice_row_changed()

        online_row = SettingRow(
            page, self, self.icon("info"), "Use Microsoft's online voices",
            "The voices the app used before 3.8. Microsoft's service stopped answering in "
            "October 2026, so leave this off unless it works again")
        online_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkSwitch(
            online_row.control, text="", width=44, variable=self.var_voice_online,
            onvalue=True, offvalue=False, progress_color=ACCENT,
        ).pack(side="left")

        self._caption(page, "UPDATES", next(rows))

        updates_row = SettingRow(page, self, self.icon("info"), "Check for updates on startup",
                                 "Asks GitHub for the latest version number. Nothing is "
                                 "installed automatically")
        updates_row.grid(row=next(rows), column=0, sticky="ew", pady=(PAD // 2, PAD))
        ctk.CTkSwitch(
            updates_row.control, text="", width=44, variable=self.var_check_updates,
            onvalue=True, offvalue=False, progress_color=ACCENT,
        ).pack(side="left")
        return page

    def _key_row(self, page, row, provider, title, subtitle, variable, placeholder):
        """A masked API-key entry with reveal and test buttons. Returns the entry."""
        key_row = SettingRow(page, self, self.icon("key"), title, subtitle)
        key_row.grid(row=row, column=0, sticky="ew", pady=PAD // 2)
        entry = ctk.CTkEntry(
            key_row.control, textvariable=variable, width=260, height=32,
            corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
            border_color=FIELD_BORDER, border_width=1, text_color=TEXT,
            placeholder_text=placeholder, show="•",
        )
        focus_accent(entry).pack(side="left")
        ctk.CTkButton(
            key_row.control, text=self.icon("eye"), font=self.font_icon, width=34,
            height=32, corner_radius=4, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER,
            text_color=TEXT_MUTED, command=lambda: self.toggle_key_visibility(entry),
        ).pack(side="left", padx=(6, 0))
        test = ctk.CTkButton(
            key_row.control, text="Test", width=64, height=32, corner_radius=4,
            font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
        )
        test.pack(side="left", padx=(6, 0))

        def done(ok, message):
            test.configure(state="normal", text="Works" if ok else "Failed",
                           text_color=OK_COLOR if ok else ERR_COLOR)
            self.append_log(f"{'✅' if ok else '⚠️'} {provider} key: {message}")

        def run():
            test.configure(state="disabled", text="...", text_color=TEXT)
            self.test_key(provider, variable.get(), done)

        test.configure(command=run)
        return entry

    def test_key(self, provider, key, callback):
        """Check a footage key off the UI thread; ``callback(ok, message)`` on it."""
        token = object()
        self._key_tests[token] = callback

        def worker():
            try:
                ok, message = footage.check_key(provider, key)
            except Exception as exc:  # noqa: BLE001 - reported in the window
                ok, message = False, f"The test failed ({exc})."
            self._queue.put(("keytest", {"token": token, "ok": ok, "message": message}))

        threading.Thread(target=worker, daemon=True).start()

    def _segmented(self, parent, values, variable, width):
        FluentSegmented(parent, self, values, variable, width).pack(side="left")

    def toggle_key_visibility(self, entry=None):
        entry = entry or self.api_entry
        showing = entry.cget("show") == ""
        entry.configure(show="•" if showing else "")

    # -- background music ----------------------------------------------------
    def _refresh_tracks(self):
        """Re-read the Music folder; a browsed file outside it stays listed."""
        if not hasattr(self, "music_menu"):
            return
        tracks = {AUTO_LABEL: AUTO_TRACK, **music_tracks()} if bundled_tracks()             else music_tracks()
        if self.music_path and os.path.isfile(self.music_path):
            known = {os.path.normcase(p) for p in tracks.values()}
            if os.path.normcase(self.music_path) not in known:
                tracks[f"{os.path.basename(self.music_path)} (browsed)"] = self.music_path
        self._tracks = tracks
        self.music_menu.configure(values=list(tracks) or ["No tracks yet"])
        current = next(
            (name for name, path in tracks.items()
             if os.path.normcase(path) == os.path.normcase(self.music_path or "")),
            None,
        )
        self.var_music_track.set(current or ("Choose a track" if tracks else "No tracks yet"))

    def _pick_track(self, name):
        path = self._tracks.get(name)
        if not path:
            return
        self.music_path = path
        # Choosing a track means wanting music; don't make them flip the switch too.
        self.var_music_enabled.set(True)
        self._on_setting_changed()

    def browse_music(self):
        start = MUSIC_DIR if os.path.isdir(MUSIC_DIR) else os.path.expanduser("~")
        chosen = filedialog.askopenfilename(
            title="Choose background music",
            initialdir=start,
            filetypes=[("Audio", " ".join(f"*{ext}" for ext in MUSIC_EXTS)),
                       ("All Files", "*.*")],
        )
        if chosen:
            self.music_path = os.path.normpath(chosen)
            self.var_music_enabled.set(True)
            self._on_setting_changed()
            self._refresh_tracks()

    def open_music_folder(self):
        self._open_made_folder(MUSIC_DIR)

    def _save_dialog(self, suggested, title):
        """The Windows Save window, opened on ``suggested``. '' if cancelled.

        It opens in a folder that exists and takes files - Windows' own Save
        window answers "file not found" in a protected one - and it never
        hands back a path into a folder that isn't there.
        """
        suggested = suggested or os.path.join(default_output_dir(), "final_video.mp4")
        folder = os.path.dirname(os.path.abspath(suggested))
        name = os.path.basename(suggested) or "final_video.mp4"
        try:
            usable, moved = paths.usable_output(os.path.join(folder, name), paths.safe_folders())
        except paths.SaveError as exc:
            Dialog(self, "Can't save here", str(exc), ok=False)
            return ""
        if moved:
            self.append_log(f"⚠️ Couldn't save to {folder} - using "
                            f"{os.path.dirname(usable)} instead.")
            usable = next_free_path(usable)
        try:
            chosen = filedialog.asksaveasfilename(
                parent=self, title=title, defaultextension=".mp4",
                filetypes=[("MP4 Video", "*.mp4"), ("All Files", "*.*")],
                initialdir=paths.existing_dir(os.path.dirname(usable), default_output_dir()),
                initialfile=os.path.basename(usable),
            )
        except tk.TclError as exc:
            self.append_log(f"⚠️ The Save window couldn't open ({exc}).")
            return ""
        if not chosen:
            return ""
        chosen = paths.as_video(os.path.normpath(chosen))
        if not paths.is_writable(os.path.dirname(chosen)):
            Dialog(self, "Can't save here",
                   paths.cannot_save_message(os.path.dirname(chosen)), ok=False)
            return ""
        return chosen

    def choose_save_location(self):
        chosen = self._save_dialog(self.var_output.get().strip(), "Default save location")
        if chosen:
            self.var_output.set(chosen)

    def _ask_save_path(self):
        """Where this render goes. Suggests a name that isn't taken yet."""
        current = self.var_output.get().strip() or os.path.join(
            default_output_dir(), "final_video.mp4")
        return self._save_dialog(next_free_path(os.path.abspath(current)), "Save video as")

    def apply_theme(self):
        ctk.set_appearance_mode(self.var_theme.get())
        self._configure_log_tags()   # text tags hold single colours, not pairs
        self.apply_effects()         # also saves the settings

    def apply_effects(self):
        self.effect_note = apply_window_effects(
            self,
            dark=self.var_theme.get() == "dark",
            translucent=bool(self.var_translucent.get()),
        )
        if hasattr(self, "row_effect"):
            self.row_effect.set_value(self.effect_note)
        self._on_setting_changed()

    def _put_back_left(self, entry):
        """Worker: open the page and switch back what a recording left flipped, then check."""
        place = winmap.BY_ID.get(entry["place"])
        name, was = entry["switch"], entry["state"]
        hwnd = 0
        if place is not None:
            try:
                hwnd = screen.open_window(place.command())
            except OSError:
                hwnd = 0
        if hwnd and drive.flip(hwnd, name, was):
            putback.clear(SETTINGS_DIR)
            self.call_soon(lambda: self.append_log(
                f"↩️ {name} is back {was.lower()}: the app had changed it for a video and "
                "was closed before it could switch it back."))
        elif place is None:
            putback.clear(SETTINGS_DIR)
        else:
            where = place.name
            self.call_soon(lambda: self.append_log(
                f"⚠️ A video recording left {name} changed. Switch it back to {was.lower()} in "
                f"{where}; the app will try again next time it starts."))

    # -- startup / queue -----------------------------------------------------
    def _post_init(self):
        self.apply_effects()
        self._install_error_hooks()
        self.append_log(f"{APP_NAME} {APP_VERSION} ready.")
        self.append_log(f"⏱️ Window ready in {time.perf_counter() - self._launched:.1f} s")
        self.append_log(f"🪟 Window effect: {self.effect_note}")
        self.append_log(f"🎞️ ffmpeg: {FFMPEG_EXE or 'system / imageio auto-detect'}")
        if not self.var_api.get().strip() and not self.var_pixabay.get().strip():
            self.append_log(
                "🎨 No stock footage key: every scene will get an AI picture."
                if imagegen.installed() else
                "⚠️ No footage API key yet - add a Pexels or Pixabay key in Settings.")
        self.refresh_home()
        threading.Thread(target=self._warm_moviepy, daemon=True).start()
        left = putback.read(SETTINGS_DIR)
        if left:   # the app closed while a switch it flipped for a video was still flipped
            threading.Thread(target=self._put_back_left, args=(left,), daemon=True).start()
        if not self.tour_seen:
            self.whats_new_seen = APP_VERSION   # a first install: nothing is "new" yet
            self.after(300, lambda: TourDialog(self))   # it leads into the key setup itself
        elif show_whats_new(self.whats_new_seen, self.whats_new_off, False):
            self.after(300, lambda: WhatsNewDialog(self))
        elif not self._has_footage_key() and not self.welcome_seen:
            self.after(300, lambda: WelcomeDialog(self))
        self.detect_graphics()
        if self.var_check_updates.get():
            self.check_for_updates()

    # -- updates -------------------------------------------------------------
    def check_for_updates(self, manual=False):
        """Ask GitHub for the latest release, off the UI thread.

        ``manual`` is the Check button: only then is a failed check mentioned.
        """
        if manual:
            self.update_button.configure(state="disabled")
            self.update_status.configure(text="Checking...", text_color=TEXT_MUTED)

        def worker():
            try:
                found = updates.latest(APP_VERSION)
            except Exception:  # noqa: BLE001 - an update check never interrupts
                found = None
            self._queue.put(("update", {"found": found, "manual": manual}))

        threading.Thread(target=worker, daemon=True).start()

    def _update_checked(self, found, manual):
        self.update_button.configure(state="normal")
        if found is None:
            if manual:
                self.update_status.configure(
                    text="Couldn't check right now - try again later.", text_color=TEXT_MUTED)
            return
        tag, page = found.tag, found.page
        if updates.is_newer(tag, APP_VERSION):
            version = found.version
            self.update_url = page
            if not self._updating:
                self.update_release = found
                self._offer_update()
            self.update_status.configure(text=f"Version {version} is available.",
                                         text_color=ACCENT)
        else:
            self.update_status.configure(text="You have the latest version.",
                                         text_color=OK_COLOR)

    def open_update_page(self):
        webbrowser.open(self.update_url)

    # -- updating from inside the app ----------------------------------------
    def _app_dir(self) -> str:
        """The folder this copy of the app runs from."""
        return os.path.dirname(os.path.abspath(sys.executable))

    def _can_self_update(self) -> bool:
        """A built copy: installed ones run the new installer, portable ones the new ZIP."""
        found = self.update_release
        if not (IS_FROZEN and found):
            return False
        if updates.is_installed_copy(self._app_dir()):
            return found.can_install
        return found.can_unpack and updates.can_write(self._app_dir())

    def _offer_update(self, message=None, allow_install=True):
        """Show the bar: Update now where the app can do it, the release page otherwise."""
        found = self.update_release
        self.update_bar_label.configure(
            text=message or f"Version {found.version} is available. You have {APP_VERSION}.")
        if allow_install and self._can_self_update():
            self.update_action.configure(text="Update now", command=self.start_update,
                                         state="normal")
        else:
            self.update_action.configure(text="Download", command=self.open_update_page,
                                         state="normal")
        self.update_later.configure(text="Later", command=self.update_bar.grid_remove)
        self.update_bar.grid()

    def start_update(self):
        """Download the new installer (checked against the release's checksum), then run it."""
        if self._updating or not self._can_self_update():
            return
        if self.is_rendering:
            self._offer_update("Finish or cancel the render first, then press Update now.")
            return
        found = self.update_release
        self._updating = True
        self._update_stop = stop = threading.Event()
        self.update_bar_label.configure(text=f"Downloading version {found.version}...")
        self.update_action.configure(text="Updating...", state="disabled")
        self.update_later.configure(text="Cancel", command=stop.set)
        self.append_log(f"⬇️ Downloading version {found.version}...")
        folder = os.path.join(TEMP_ROOT, "update")
        installed = updates.is_installed_copy(self._app_dir())

        def worker():
            last = 0.0

            def progress(done, total):
                nonlocal last
                now = time.monotonic()
                if now - last >= 0.25 or done >= total:
                    last = now
                    self._queue.put(("updater", {"event": "progress", "done": done,
                                                 "total": total}))

            try:
                if installed:
                    path = updates.download_installer(found, folder, progress, stop.is_set)
                else:
                    archive = updates.download_portable(found, folder, progress, stop.is_set)
                    path = updates.unpack_portable(archive, os.path.join(folder, "portable"))
                    os.remove(archive)   # unpacked: the 120 MB ZIP isn't needed again
                self._queue.put(("updater", {"event": "ready", "path": path}))
            except footage.Stopped:
                self._queue.put(("updater", {"event": "stopped"}))
            except updates.UpdateError as exc:
                self._queue.put(("updater", {"event": "failed", "message": str(exc)}))
            except Exception as exc:  # noqa: BLE001 - a thread must not die silently
                self._queue.put(("updater", {
                    "event": "failed", "trace": traceback.format_exc(),
                    "message": f"The update hit an unexpected problem ({type(exc).__name__})."}))

        threading.Thread(target=worker, daemon=True).start()

    def _updater_event(self, payload):
        """UI thread: the update download moved on."""
        event, found = payload["event"], self.update_release
        if event == "progress":
            self.update_bar_label.configure(
                text=f"Downloading version {found.version}: {payload['done'] / 1e6:.0f} of "
                     f"{payload['total'] / 1e6:.0f} MB")
            return
        self._updating = False
        if payload.get("trace"):
            self.session_log.write(payload["trace"])
        if event == "ready":
            self._install_update(payload["path"])
        elif event == "stopped":
            self.append_log("⏹️ Update cancelled.")
            self._offer_update()
        else:
            self.append_log(f"⚠️ {payload['message']}")
            # Whatever went wrong, the release page still works.
            self._offer_update(f"{payload['message']} You can still get it from the "
                               "release page.", allow_install=False)

    def _install_update(self, path):
        """Hand over to the installer and close, so it can replace this copy's files."""
        if self.is_rendering:   # a render was started while the update downloaded
            self._offer_update("The update is ready. Finish or cancel the render first, "
                               "then press Update now.")
            return
        try:
            if updates.is_installed_copy(self._app_dir()):
                command = updates.installer_command(path, self._app_dir())
                # Detached: the installer must outlive this process, which it replaces.
                flags = getattr(subprocess, "DETACHED_PROCESS", 0)
            else:
                # Portable: a script copies the unpacked app over this one once it has closed.
                script = os.path.join(TEMP_ROOT, "update", "update.cmd")
                with open(script, "w", encoding="utf-8") as fh:
                    fh.write(updates.portable_script(path, self._app_dir(), os.getpid()))
                command = ["cmd", "/c", script]
                # A hidden console, shared by robocopy, instead of windows popping up.
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            subprocess.Popen(command, creationflags=flags, close_fds=True)
        except OSError as exc:
            self.append_log(f"⚠️ The installer could not be started: {exc}")
            self._offer_update("The update could not be started. You can still get it from "
                               "the release page.", allow_install=False)
            return
        self.append_log("🔄 Installing the update; the app will reopen.")
        # Nothing of ours may still be running when the installer replaces the files.
        self._writer_stop.set()
        for window in list(self.winfo_children()):
            if isinstance(window, DraftDialog):
                window.close()
        self._on_close()

    def _has_footage_key(self) -> bool:
        return bool(self.var_api.get().strip() or self.var_pixabay.get().strip())

    def _needs_stock_footage(self) -> bool:
        """Whether the script has a scene that isn't the user's own file."""
        try:
            scenes = parse_script(self.script_box.get("1.0", "end"))
        except ScriptError:
            return False   # let the render report the script problem itself
        return any(not scene.is_local and not scene.is_ai for scene in scenes)

    def _warm_moviepy(self):
        sweep_old_work_dirs()
        cache.trim(CACHE_ROOT)   # back under the size limit, oldest files first
        shutil.rmtree(THUMB_DIR, ignore_errors=True)   # last session's preview stills
        try:
            # Importing the engine's heavy half pulls in MoviePy, NumPy and PIL.
            import vidgen.audio  # noqa: F401
            import vidgen.motion  # noqa: F401
            self.ui.log("📦 MoviePy loaded.")
        except Exception as exc:  # noqa: BLE001
            self.ui.log(f"⚠️ MoviePy not available: {exc}")

    def call_soon(self, fn, delay_ms: int = 0):
        """Run ``fn`` on the window's thread: the only safe way for a worker thread to touch it.

        Tkinter calls (after, configure...) from another thread can be lost without a word,
        which left "Show it on my PC" stuck at Finish (2026-10-21).
        """
        self._queue.put(("call", {"fn": (lambda: self.after(delay_ms, fn)) if delay_ms else fn}))

    def _drain_queue(self):
        """The single point where background work touches the interface."""
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    self.append_log(payload["msg"])
                elif kind == "status":
                    self.status_label.configure(text=payload["msg"])
                elif kind == "progress":
                    for bar in self._progress_bars():
                        bar.configure(mode="determinate")
                        bar.set(payload["value"])
                elif kind == "spinner":
                    for bar in self._progress_bars():
                        if payload["on"]:
                            bar.configure(mode="indeterminate")
                            bar.start()
                        else:
                            bar.stop()
                            bar.configure(mode="determinate")
                elif kind == "busy":
                    self._set_busy(payload["on"])
                elif kind == "preview":
                    self._preview_done(payload["path"], payload["error"])
                elif kind == "cancelled":
                    if self._batch:
                        self.append_log("📚 The rest of the videos were not made.")
                        self._batch = None
                    if self._both:
                        self._shape_back(self._both)
                        self._both = None
                    self.last_render = "Cancelled"
                    self.refresh_home()
                elif kind == "board":
                    self._board_event(payload)
                elif kind == "line":
                    self._line_ready(payload)
                elif kind == "draft":
                    try:
                        payload["dialog"].on_event(payload)
                    except tk.TclError:
                        pass   # the window was closed meanwhile
                elif kind == "writer":
                    self._writer_event(payload)
                elif kind == "updater":
                    self._updater_event(payload)
                elif kind == "voice":
                    self._voice_event(payload)
                elif kind == "images":
                    self._images_event(payload)
                elif kind == "project":
                    self._project_thumb_done(payload)
                elif kind == "batch":
                    self._batch_event(payload)
                elif kind == "moving":
                    self._moving_row_changed(payload)
                elif kind == "myvoice":
                    payload["dialog"].on_event(payload)
                elif kind == "translated":
                    payload["dialog"].done(payload)
                elif kind == "upload":
                    self._upload_done(payload)
                elif kind == "ai_speed":
                    self.note_picture_time(payload["seconds"], payload["speed"])
                elif kind == "ai_gpu":
                    self._graphics_found(payload["found"])
                elif kind == "ai_backend":
                    self.remember_ai_backend(payload["backend"])
                elif kind == "crash":
                    self._unexpected(payload["trace"])
                elif kind == "call":
                    try:
                        payload["fn"]()
                    except tk.TclError:
                        pass   # its window was closed meanwhile
                elif kind == "update":
                    self._update_checked(payload["found"], payload["manual"])
                elif kind == "keytest":
                    callback = self._key_tests.pop(payload["token"], None)
                    if callback:
                        try:
                            callback(payload["ok"], payload["message"])
                        except tk.TclError:
                            pass   # its window was closed meanwhile
                elif kind == "finished":
                    self.last_render = "Success" if payload["ok"] else "Failed"
                    self.refresh_home()
                    made = [p for p in (payload.get("paths") or [payload.get("path")])
                            if p and os.path.isfile(p)]
                    if self._batch:
                        b = self._batch
                        self.save_project(videos=made)
                        if payload["ok"] and made:
                            b["made"].extend(made)
                            if b.get("upload"):
                                self._upload_done({"upload": b.pop("upload"), "videos": made,
                                                   "project": self.project})
                        else:
                            b["failed"].append((b["at"] + 1, payload["message"]))
                        b["at"] += 1
                        self.after(500, self._batch_next)
                        continue
                    both, self._both = self._both, None
                    if both and payload["ok"] and made and not both.get("first"):
                        # The first shape is done: the other one next, in the same project.
                        self.save_project(videos=made)
                        self._both = dict(both, first=made)
                        self.after(300, self._second_shape)
                        continue
                    if both:
                        made = both.get("first", []) + made
                        self._shape_back(both)
                    self.save_project(videos=made)
                    self.last_upload = None
                    if payload["ok"] and made:
                        self.make_upload_text(made)
                        ResultDialog(self, payload["title"], payload["message"], made,
                                     names=both and [both["shape"], both["other"]])
                    else:
                        message = payload["message"]
                        Dialog(self, payload["title"], message, ok=payload["ok"],
                               on_report=None if payload["ok"] else
                               (lambda: self.open_report(message)))
        except queue.Empty:
            pass
        finally:
            self.after(60, self._drain_queue)

    def _set_busy(self, busy: bool):
        self.is_rendering = busy
        # While rendering, the Render button is the way to stop.
        if busy:
            self.render_button.configure(state="normal", text=f"{self.icon('stop')}   Cancel",
                                         command=self.cancel_render)
            self.log_cancel.configure(state="normal", text=f"{self.icon('stop')}  Cancel")
            self.log_cancel.pack(side="right", padx=(0, 8))
        else:
            self.render_button.configure(state="normal", command=self.start_render,
                                         text=f"{self.icon('play')}   Render Video")
            self.log_cancel.pack_forget()
        self.scenes_button.configure(state="disabled" if busy else "normal")
        self.draft_button.configure(state="disabled" if busy else "normal")
        self.row_render.button.configure(state="disabled" if busy else "normal")
        for bar in self._progress_bars():
            if busy:
                bar.grid()
            else:
                bar.grid_remove()
        if not busy:
            self.status_label.configure(text="Ready")
        self.refresh_home()

    def _progress_bars(self):
        return (self.progress, self.log_progress)

    def cancel_render(self):
        """Ask the render to stop. It does so at its next safe point."""
        if not self.is_rendering or self._cancel is None or self._cancel.is_set():
            return
        self._cancel.set()
        self.render_button.configure(state="disabled", text="Cancelling...")
        self.log_cancel.configure(state="disabled", text="Cancelling...")
        self.status_label.configure(text="Cancelling...")

    # -- scene preview -------------------------------------------------------
    def frame_label(self) -> str:
        return f"{self.var_aspect.get()} {self.var_resolution.get()}"

    def new_footage_search(self):
        """A stock search for the current keys and frame, quiet (no Log lines)."""
        width, height, orientation, _bitrate = resolve_target(
            self.var_aspect.get(), self.var_resolution.get())
        return footage.FootageSearch(
            self.var_api.get(), self.var_pixabay.get(), width, height, orientation,
            self.var_resolution.get(), SEARCH_CACHE_DIR, lambda _message: None)

    def open_scene_preview(self):
        if self.is_rendering:
            return
        script = self.script_box.get("1.0", "end")
        if looks_like_plain_text(script):   # as Render Video does: it wants New from text
            DraftDialog(self).prefill(script.strip())
            return
        self.select_page("board")

    # -- new from text -------------------------------------------------------
    def open_draft(self):
        if not self.is_rendering:
            DraftDialog(self)

    def script_is_the_users_own(self) -> bool:
        """Whether replacing the editor's script would lose something they wrote."""
        current = self.script_box.get("1.0", "end").strip()
        return current not in ("", DEFAULT_SCRIPT.strip(), self._last_draft)

    def use_draft(self, result, audience, mode=None):
        """Put a written script in the editor, with the settings it was written for."""
        script = result.script
        self.save_project()
        self.project = None   # a new video: its own project from the first save
        self.board_history, self.board_future = [], []
        if self.ai_slow() and self._has_footage_key():
            # Each picture takes minutes here: keep the first few, stock for the rest.
            capped = boards.Board(boards.limit_ai(parse_script(script), SLOW_AI_SCENES)).text()
            if capped != script:
                self.append_log(f"🎨 This PC makes AI pictures slowly, so only the first "
                                f"{SLOW_AI_SCENES} scenes get one. The Storyboard can switch "
                                "any other scene in one click.")
            script = capped
        self._last_draft = script.strip()
        self.writer_mode = mode or self.writer_mode
        self.audience = {"audience_content": audience.content, "audience_age": audience.age,
                         "audience_platform": audience.platform}
        self.script_box.delete("1.0", "end")
        self.script_box.insert("1.0", script)
        self.var_voice.set(voices.persona(result.voice).label)
        self.var_aspect.set(result.aspect)
        self.var_target.set(result.target)
        self._commit_script()
        # Look before it is made, unless the user asked for the video straight away.
        after, self._after_draft = self._after_draft, "board"
        self.select_page("board")
        if after == "render":
            self.start_render()

    # -- the Smart writer's one-time download --------------------------------
    def start_writer_download(self):
        """Fetch the model on a worker thread. Listeners hear how it goes."""
        if self.writer_downloading:
            return
        self.writer_downloading = True
        self._writer_stop = stop = threading.Event()
        self.append_log(f"⬇️ Downloading the Smart writer ({writer.MODEL.gigabytes})...")

        def worker():
            last = 0.0

            def progress(done, total):
                nonlocal last
                now = time.monotonic()
                if now - last >= 0.25 or done >= total:  # a few times a second is plenty
                    last = now
                    self._queue.put(("writer", {"event": "progress", "done": done, "total": total}))

            try:
                writer.download(progress, stop.is_set)
                self._queue.put(("writer", {"event": "done"}))
            except writer.Stopped:
                self._queue.put(("writer", {"event": "stopped"}))
            except writer.WriterError as exc:
                self._queue.put(("writer", {"event": "failed", "message": str(exc)}))
            except Exception as exc:  # noqa: BLE001 - a thread must not die silently
                self._queue.put(("writer", {
                    "event": "failed", "trace": traceback.format_exc(),
                    "message": f"The download hit an unexpected problem ({type(exc).__name__})."}))

        threading.Thread(target=worker, daemon=True).start()
        # Tell the listeners at once, so their buttons read Cancel before the first byte.
        self._writer_event({"event": "progress", "done": 0, "total": writer.MODEL.size})

    def cancel_writer_download(self):
        self._writer_stop.set()

    def repair_writer(self):
        """Throw the model away and fetch it again."""
        if not self.writer_downloading:
            writer.remove()
            self.start_writer_download()

    def _writer_event(self, payload):
        """UI thread: pass the download's news to whoever is showing it."""
        event = payload["event"]
        if event != "progress":
            self.writer_downloading = False
            if payload.get("trace"):
                self.session_log.write(payload["trace"])
            self.append_log({"done": "✅ The Smart writer is ready.",
                             "stopped": "⏸️ Smart writer download paused.",
                             "failed": f"⚠️ {payload.get('message', '')}"}[event])
        for listener in list(self.writer_listeners):
            try:
                listener(payload)
            except tk.TclError:
                self.writer_listeners.remove(listener)   # its window is gone

    def _writer_row_changed(self, payload=None):
        """The Settings row: what state the Smart writer is in, and what the button does."""
        self._writer_confirm = False
        model = writer.MODEL
        if payload and payload["event"] == "progress":
            done, total = payload["done"], payload["total"]
            self.writer_state.configure(
                text=f"Downloading {done / 1e9:.2f} of {total / 1e9:.2f} GB", text_color=TEXT_MUTED)
            self.writer_button.configure(text="Cancel", command=self.cancel_writer_download)
            return
        if payload and payload["event"] == "failed":
            self.writer_state.configure(text=ellipsize(payload["message"], 60), text_color=ERR_COLOR)
        elif writer.runtime_path() is None:
            self.writer_state.configure(text="Not included in this copy", text_color=TEXT_MUTED)
        elif writer.installed():
            self.writer_state.configure(text=f"Installed · {model.gigabytes}", text_color=OK_COLOR)
        else:
            self.writer_state.configure(text=f"Not downloaded · {model.gigabytes}",
                                        text_color=TEXT_MUTED)
        if writer.installed():
            self.writer_button.configure(text="Repair", command=self._confirm_repair)
        else:
            self.writer_button.configure(text="Download", command=self.start_writer_download)

    def _confirm_repair(self):
        """Repair downloads 2.5 GB again, so it takes a second press."""
        if not self._writer_confirm:
            self._writer_confirm = True
            self.writer_state.configure(
                text=f"Press again to download {writer.MODEL.gigabytes} again",
                text_color=ERR_COLOR)
            return
        self.repair_writer()

    # ------------------------------------------------------------ PROJECTS --
    # Every video made (and every Storyboard left unfinished), from vidgen.projects.
    def _build_projects(self):
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=1)
        self._caption(page, "PROJECTS", 0, pady=(0, 6))
        top = ctk.CTkFrame(page, fg_color="transparent")
        top.grid(row=1, column=0, sticky="ew", pady=(0, PAD // 2))
        top.grid_columnconfigure(0, weight=1)
        self.project_search = focus_accent(ctk.CTkEntry(
            top, height=34, corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
            border_color=FIELD_BORDER, text_color=TEXT,
            placeholder_text="Search titles and scripts"))
        self.project_search.grid(row=0, column=0, sticky="ew")
        self.project_search.bind("<KeyRelease>", lambda _event: self.refresh_projects())
        self.projects_count = ctk.CTkLabel(top, text="", font=self.font_tiny,
                                           text_color=TEXT_DIM)
        self.projects_count.grid(row=0, column=1, padx=(12, 4))
        self.projects_list = ctk.CTkScrollableFrame(
            page, fg_color="transparent", scrollbar_fg_color=SCROLLBAR_BG,
            scrollbar_button_color=SCROLLBAR_THUMB,
            scrollbar_button_hover_color=SCROLLBAR_THUMB_HOVER)
        self.projects_list.grid(row=2, column=0, sticky="nsew")
        self.projects_list.grid_columnconfigure((0, 1, 2), weight=1, uniform="projects")
        self.project_images = []   # kept so Tk doesn't drop the thumbnails
        return page

    def refresh_projects(self):
        for child in self.projects_list.winfo_children():
            child.destroy()
        self.project_images = []
        every = projects.all_projects()
        shown = [p for p in every if projects.matches(p, self.project_search.get())]
        count = f"{len(every)} project{'s' if len(every) != 1 else ''}"
        self.projects_count.configure(
            text=count if len(shown) == len(every) else f"{len(shown)} of {count}")
        if not shown:
            hint = ("Nothing matches that search." if every else
                    "Videos you make appear here, so you can open them again in the Storyboard "
                    "and change them. A Storyboard you leave without making the video is kept "
                    "here too, as a draft.")
            ctk.CTkLabel(self.projects_list, text=hint, font=self.font_body,
                         text_color=TEXT_MUTED, justify="left", anchor="w", wraplength=640,
                         ).grid(row=0, column=0, columnspan=3, sticky="w", padx=8, pady=20)
        for index, project in enumerate(shown):
            self._project_card(project, index)

    def _project_card(self, project, index):
        from PIL import Image

        pid, settings = project["id"], project.get("settings") or {}
        videos = [v for v in project.get("videos") or [] if os.path.isfile(v)]
        card = Card(self.projects_list)
        card.grid(row=index // 3, column=index % 3, sticky="nsew", padx=4, pady=4)
        card.grid_columnconfigure((0, 1), weight=1)

        box = (101, 180) if settings.get("aspect", "9:16") == "9:16" else (240, 135)
        picture = ctk.CTkLabel(card, text=self.icon("projects"), font=self.font_icon_lg,
                               text_color=TEXT_DIM, width=box[0], height=box[1],
                               fg_color=FIELD_BG, corner_radius=4)
        picture.grid(row=0, column=0, columnspan=2, pady=(12, 6))
        thumb = os.path.join(projects.folder(pid), projects.THUMB)
        if os.path.isfile(thumb):
            try:
                with Image.open(thumb) as image:
                    image = image.convert("RGB")
                image.thumbnail(box)
                photo = ctk.CTkImage(light_image=image, dark_image=image, size=image.size)
                self.project_images.append(photo)
                picture.configure(image=photo, text="")
            except OSError:
                pass   # no picture: the icon stays

        title = ctk.CTkLabel(card, text=project.get("title") or "Untitled video",
                             font=self.font_bold, text_color=TEXT, anchor="w", justify="left",
                             wraplength=240)
        title.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12)
        when = time.strftime("%d %b %Y", time.localtime(project.get("updated") or 0))
        if projects.is_draft(project):
            info, color = f"Draft · {when}", WARN_COLOR
        elif not videos:
            info, color = f"{when} · video file not found", WARN_COLOR
        else:
            length = project.get("length") or 0
            info = f"{when} · {int(length // 60)}:{int(length % 60):02d} · " \
                   f"{settings.get('aspect', '9:16')}"
            color = TEXT_DIM
        ctk.CTkLabel(card, text=info, font=self.font_tiny, text_color=color, anchor="w",
                     ).grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 6))

        def rename():
            title.grid_remove()
            entry = focus_accent(ctk.CTkEntry(
                card, height=30, corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
                border_color=FIELD_BORDER, text_color=TEXT))
            entry.insert(0, project.get("title") or "")
            entry.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12)
            entry.focus_set()
            entry.select_range(0, "end")

            def done(_event=None):
                name, data = entry.get().strip(), projects.load(pid)
                if name and data is not None:
                    projects.save(pid, dict(data, title=name), touch=False)
                self.refresh_projects()

            entry.bind("<Return>", done)
            entry.bind("<Escape>", lambda _event: self.refresh_projects())

        def delete():
            def really():
                projects.delete(pid)
                if self.project == pid:
                    self.project = None
                self.refresh_projects()

            Dialog(self, "Delete this project?",
                   f"\"{project.get('title')}\" is removed from Projects, with its saved "
                   "AI pictures. Your video file is kept.", ok=False,
                   confirm=really, confirm_text="Delete")

        def change_it():
            if self.open_project(pid):
                self.board_comment.focus_set()
                self.board_status.configure(
                    text="Say what to change in the box below, then press Change it.",
                    text_color=TEXT_DIM)

        buttons = (("Open", lambda: self.open_project(pid), True),
                   ("Change it", change_it, True),
                   ("Play", lambda: os.startfile(videos[0]), bool(videos)),
                   ("Show in folder", lambda: subprocess.Popen(
                       ["explorer", "/select,", os.path.normpath(videos[0])]), bool(videos)),
                   ("Make a copy", lambda: CopyDialog(self, project), True),
                   ("Translate", lambda: TranslateDialog(self, project), True),
                   ("Rename", rename, True),
                   ("Delete", delete, True))
        for at, (text, command, usable) in enumerate(buttons):
            ctk.CTkButton(
                card, text=text, height=28, corner_radius=4, font=self.font_tiny,
                fg_color=ACCENT if at == 0 else FIELD_BG,
                hover_color=ACCENT_HOVER if at == 0 else CARD_HOVER,
                text_color=ACCENT_TEXT if at == 0 else TEXT, border_width=0 if at == 0 else 1,
                border_color=FIELD_BORDER, state="normal" if usable else "disabled",
                command=command,
            ).grid(row=3 + at // 2, column=at % 2, sticky="ew",
                   padx=(12 if at % 2 == 0 else 3, 3 if at % 2 == 0 else 12), pady=3)
        ctk.CTkFrame(card, fg_color="transparent", height=8).grid(row=8, column=0)

    def save_project(self, videos=None):
        """Keep the script, its settings and AI pictures as a project.

        ``videos``: the render just finished (an empty list when it failed). Without
        it this saves a draft, and only when the script changed since the last save.
        """
        if not hasattr(self, "script_box"):
            return
        script = self.script_box.get("1.0", "end").strip()
        try:
            scenes_now = parse_script(script)
        except ScriptError:
            return
        old = projects.load(self.project) if self.project else None
        if videos is None and (script == DEFAULT_SCRIPT.strip()
                               or (old is not None and old["script"] == script)):
            return
        self.project = self.project if old is not None or videos is not None \
            else projects.new_id()
        folder = projects.folder(self.project)
        first = None
        for scene in scenes_now:
            if not scene.is_ai:
                continue
            key = self._picture_key(scene)
            keep, made = projects.picture_file(folder, key), self.board_pictures.get(key)
            try:
                if made and os.path.isfile(made) and not os.path.isfile(keep):
                    os.makedirs(os.path.dirname(keep), exist_ok=True)
                    shutil.copyfile(made, keep)
            except OSError:
                pass   # the picture is made again when needed
            if first is None and os.path.isfile(keep):
                first = keep
        s = self._collect_settings()
        data = dict(old or {}, script=script, settings={k: s[k] for k in projects.KEYS},
                    clip_pos=dict(self.board_pos))
        if videos:
            data["videos"] = list(videos)
        projects.save(self.project, data)
        save_settings(s)
        if videos or first:
            threading.Thread(target=self._project_thumb,
                             args=(self.project, (videos or [None])[0], first),
                             daemon=True).start()

    def _project_thumb(self, pid, video, picture):
        """Worker: the card's picture (a frame of the video, else the first AI picture)."""
        from PIL import Image

        dest, length = os.path.join(projects.folder(pid), projects.THUMB), None
        try:
            made = os.path.splitext(video or "")[0] + ".thumbnail.jpg"
            if video:
                width, height, length = preview.probe(video)
                if os.path.isfile(made):   # the thumbnail the render made, with its title
                    with Image.open(made) as image:
                        image = image.convert("RGB")
                    image.thumbnail((360, 360))
                else:
                    image = preview.poster(video, preview.fit((width, height), (360, 360)),
                                           at=min(1.0, length / 2))
            else:
                with Image.open(picture) as image:
                    image = image.convert("RGB")
                image.thumbnail((360, 360))
            image.save(dest)
        except Exception:  # noqa: BLE001 - no thumbnail: the card shows an icon
            return
        self._queue.put(("project", {"id": pid, "length": length}))

    def _project_thumb_done(self, payload):
        data = projects.load(payload["id"])
        if data is not None and payload["length"] is not None:
            projects.save(payload["id"], dict(data, length=payload["length"]), touch=False)
        if self.current_page == "projects":
            self.refresh_projects()

    def open_project(self, pid, changes=None) -> bool:
        """Put a project back in the editor and the Storyboard. ``changes``: settings to
        use instead of the saved ones (Make a copy)."""
        if self.is_rendering:
            Dialog(self, "A video is being made",
                   "Wait until it is done, or cancel it on the Log page, then open the project.",
                   ok=False)
            return False
        data = projects.load(pid)
        if data is None:
            self.refresh_projects()
            return False
        self.save_project()   # what is open now is kept first
        snap = dict(data.get("settings") or {}, **(changes or {}))
        if snap.get("aspect") in RATIO_OPTIONS:
            self.var_aspect.set(snap["aspect"])
        if snap.get("resolution") in RESOLUTION_OPTIONS:
            self.var_resolution.set(snap["resolution"])
        if snap.get("voice"):
            self.var_voice.set(voices.persona(snap["voice"]).label)
        if "captions" in snap:
            self.var_captions.set(bool(snap["captions"]))
        for key, allowed, _default in CAPTION_SETTINGS:
            if snap.get(key) in allowed:
                self.caption_vars[key].set(snap[key])
        if snap.get("style") in drawn.STYLES:
            self.var_style.set(snap["style"])
        if snap.get("ai_images_look") in imagegen.LOOKS:
            self.var_ai_look.set(snap["ai_images_look"])
        if snap.get("target_length") in pacing.TARGETS:
            self.var_target.set(snap["target_length"])
        if "music_enabled" in snap:
            self.var_music_enabled.set(bool(snap["music_enabled"]))
        music = snap.get("music_path") or ""
        self.music_path = music if music == AUTO_TRACK or os.path.isfile(music) else ""

        if snap.get("aspect") != (data.get("settings") or {}).get("aspect"):
            data["clip_pos"] = {}   # clip picks belong to the other shape's search
        self.project = pid
        if changes:
            projects.save(pid, dict(data, settings=snap))
        self.script_box.delete("1.0", "end")
        self.script_box.insert("1.0", data["script"])
        # After the shape: a new shape clears the Storyboard's choices.
        self.board_clips, self.board_pos = {}, dict(data.get("clip_pos") or {})
        folder = projects.folder(pid)
        try:
            for scene in parse_script(data["script"]):
                if scene.is_ai:
                    key = self._picture_key(scene)
                    if os.path.isfile(projects.picture_file(folder, key)):
                        self.board_pictures[key] = projects.picture_file(folder, key)
        except ScriptError:
            pass
        self.board_text = None
        self.board_history, self.board_future = [], []
        self._commit_script()
        self.select_page("board")
        return True

    # ---------------------------------------------------------- STORYBOARD --
    # The script as a card per scene. vidgen.board holds the scenes and every edit
    # is written straight back to the script editor, so the two never disagree.
    def _build_board(self):
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        self._caption(page, "STORYBOARD", 0, pady=(0, 6))
        self.board_list = ctk.CTkScrollableFrame(
            page, fg_color="transparent", scrollbar_fg_color=SCROLLBAR_BG,
            scrollbar_button_color=SCROLLBAR_THUMB,
            scrollbar_button_hover_color=SCROLLBAR_THUMB_HOVER)
        self.board_list.grid(row=1, column=0, sticky="nsew")
        self.board_list.grid_columnconfigure(0, weight=1)

        strip = ctk.CTkFrame(page, fg_color="transparent")
        strip.grid(row=2, column=0, sticky="ew", pady=(PAD // 2, 0))
        strip.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(strip, text="Timeline: drag the right edge of a scene to change how long it "
                                 "lasts, double-click it to go back to automatic, click it to "
                                 "find its card.", font=self.font_tiny, text_color=TEXT_DIM,
                     anchor="w").grid(row=0, column=0, sticky="w", padx=4)
        self.timeline = tk.Canvas(strip, height=46, highlightthickness=0, bd=0,
                                  bg=theme_color(MAIN_BG))
        self.timeline.grid(row=1, column=0, sticky="ew")
        self.timeline.bind("<Configure>", lambda _e: self._draw_timeline())
        self.timeline.bind("<ButtonPress-1>", self._timeline_press)
        self.timeline.bind("<B1-Motion>", self._timeline_drag)
        self.timeline.bind("<ButtonRelease-1>", self._timeline_release)
        self.timeline.bind("<Double-Button-1>", self._timeline_auto)
        self._timeline_hold = None

        bar = Card(page)
        bar.grid(row=3, column=0, sticky="ew", pady=(PAD // 2, 0))
        bar.grid_columnconfigure(0, weight=1)
        steps = ctk.CTkFrame(bar, fg_color="transparent")
        steps.grid(row=2, column=3, sticky="e", padx=14, pady=(0, 10))
        for text, redo in (("Undo", False), ("Redo", True)):
            ctk.CTkButton(steps, text=text, width=64, height=28, corner_radius=4,
                          font=self.font_tiny, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                          border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                          command=lambda r=redo: self.board_undo(r)).pack(side="left", padx=(4, 0))
        self.bind_all("<Control-z>", lambda _e: self._undo_key(False), add="+")
        self.bind_all("<Control-y>", lambda _e: self._undo_key(True), add="+")
        self._look_bar(bar).grid(row=0, column=0, columnspan=4, sticky="w", padx=14,
                                 pady=(12, 0))
        self.board_comment = focus_accent(ctk.CTkEntry(
            bar, height=34, corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
            border_color=FIELD_BORDER, text_color=TEXT,
            placeholder_text="Say what to change: make it shorter, more exciting start, "
                             "remove the part about..."))
        self.board_comment.grid(row=1, column=0, sticky="ew", padx=(14, 8), pady=(10, 4))
        self.board_comment.bind(
            "<Return>", lambda _event: self.revise_script(self.board_comment.get()))
        self.board_change = ctk.CTkButton(
            bar, text="Change it", width=110, height=34, corner_radius=4, font=self.font_body,
            fg_color=FIELD_BG, hover_color=CARD_HOVER, border_width=1,
            border_color=FIELD_BORDER, text_color=TEXT,
            command=lambda: self.revise_script(self.board_comment.get()))
        self.board_change.grid(row=1, column=1, padx=(0, 8), pady=(10, 4))
        ctk.CTkButton(
            bar, text="Make both shapes", width=140, height=34, corner_radius=4,
            font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER, border_width=1,
            border_color=FIELD_BORDER, text_color=TEXT, command=self.make_both_shapes,
        ).grid(row=1, column=2, padx=(0, 8), pady=(10, 4))
        ctk.CTkButton(
            bar, text=f"{self.icon('play')}   Make video", width=150, height=34,
            corner_radius=4, font=self.font_bold, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color=ACCENT_TEXT, command=self.start_render,
        ).grid(row=1, column=3, padx=(0, 14), pady=(10, 4))
        self.board_status = ctk.CTkLabel(bar, text="", font=self.font_tiny, text_color=TEXT_DIM,
                                         anchor="w", justify="left", wraplength=820)
        self.board_status.grid(row=2, column=0, columnspan=3, sticky="w", padx=16, pady=(0, 10))
        return page

    def board_box(self):
        """The size of a card's picture: the shape of the video, small."""
        return (81, 144) if self.var_aspect.get() == "9:16" else (176, 99)

    def refresh_board(self, force=False):
        """Rebuild the cards if the script changed since they were built."""
        text = self.script_box.get("1.0", "end").strip()
        if not force and text == self.board_text and self.board_cards:
            return
        for child in self.board_list.winfo_children():
            child.destroy()
        self.board_cards, self.board_images = [], {}
        self.board_gen += 1
        try:
            self.board = boards.Board.from_text(text)
        except ScriptError as exc:
            self.board, self.board_text = None, None
            hint = ("There is plain text in the script box. Paste it on Home instead and press "
                    "Make my video." if looks_like_plain_text(text) else
                    f"The script has a mistake, so there is nothing to show yet.\n{exc}\n"
                    "Fix it on the Script page.")
            ctk.CTkLabel(self.board_list, text=hint, font=self.font_body, text_color=TEXT_MUTED,
                         justify="left", anchor="w").grid(row=0, column=0, sticky="w",
                                                          padx=8, pady=20)
            self._draw_timeline()
            return
        self.board_text = text
        self._draw_timeline()
        for index in range(len(self.board)):
            self._board_card(index)
        if not self._revising:
            self.board_status.configure(
                text=f"{len(self.board)} scenes. Change anything here; it is the same video as "
                     "on the Script page.", text_color=TEXT_DIM)
        threading.Thread(target=self._board_load, daemon=True,
                         args=(self.board_gen, list(self.board.scenes), self._board_ctx())).start()

    def _board_card(self, index):
        scene, kind = self.board.scenes[index], self.board.source(index)
        box = self.board_box()
        card = Card(self.board_list)
        card.grid(row=index, column=0, sticky="ew", pady=(0, 6))
        card.grid_columnconfigure(1, weight=1)
        picture = ctk.CTkLabel(card, text="...", width=box[0], height=box[1], fg_color=FIELD_BG,
                               corner_radius=4, font=self.font_tiny, text_color=TEXT_DIM)
        picture.grid(row=0, column=0, rowspan=4, padx=(10, 12), pady=10)

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=1, sticky="ew", pady=(8, 0))
        ctk.CTkLabel(head, text=f"Scene {index + 1}", font=self.font_bold,
                     text_color=TEXT).pack(side="left")
        if index in self.board_marked:
            ctk.CTkLabel(head, text="  changed", font=self.font_tiny,
                         text_color=WARN_COLOR).pack(side="left")
        status = ctk.CTkLabel(head, text="", font=self.font_tiny, text_color=TEXT_DIM)
        status.pack(side="left", padx=(10, 0))

        def field(row, name, value, hint):
            entry = focus_accent(ctk.CTkEntry(
                card, height=30, corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
                border_color=FIELD_BORDER, text_color=TEXT, placeholder_text=hint))
            if value:
                entry.insert(0, value)
            entry.grid(row=row, column=1, sticky="ew", pady=2)
            for event in ("<FocusOut>", "<Return>"):
                entry.bind(event, lambda _e, i=index, n=name, w=entry: self._board_edit(i, n, w.get()))
            return entry

        field(1, "voice", scene.voice, "What is said in this scene")
        field(2, "visual", self.board.shown(index),
              {boards.STOCK: "Search words for stock footage",
               boards.AI: "What the AI picture shows", boards.LOCAL: "Your file"}[kind])
        field(3, "card", scene.card, "Card (optional): Win + V   or   $120,000 | after 30 years")

        def button(parent, text, command, width=118):
            return ctk.CTkButton(parent, text=text, width=width, height=28, corner_radius=4,
                                 font=self.font_tiny, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                                 border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                                 command=command)

        side = ctk.CTkFrame(card, fg_color="transparent")
        side.grid(row=0, column=2, rowspan=4, padx=(10, 4), pady=8, sticky="n")
        action = None
        if kind == boards.STOCK:
            action = button(side, "Another clip", lambda i=index: self._board_another(i))
        elif kind == boards.AI:
            action = button(side, "Make picture", lambda i=index: self._board_make(i))
        if action is not None:
            action.pack(pady=(0, 4))
        if kind == boards.AI:
            button(side, "Use a stock clip",
                   lambda i=index: self._board_do(lambda: self.board.set_source(i, boards.STOCK)),
                   ).pack(pady=(0, 4))
        elif kind == boards.STOCK and imagegen.installed():
            button(side, "Use an AI picture",
                   lambda i=index: self._board_do(lambda: self.board.set_source(i, boards.AI)),
                   ).pack(pady=(0, 4))
        if scene.voice:
            button(side, "Hear it", lambda i=index: self.hear_line(self.board.scenes[i].voice),
                   ).pack(pady=(0, 4))

        order = ctk.CTkFrame(card, fg_color="transparent")
        order.grid(row=0, column=3, rowspan=4, padx=(0, 10), pady=8, sticky="n")
        for text, act in (("Up", lambda i=index: self.board.move(i, -1)),
                          ("Down", lambda i=index: self.board.move(i, 1)),
                          ("Add", lambda i=index: self.board.insert(i)),
                          ("Delete", lambda i=index: self.board.delete(i))):
            button(order, text, lambda a=act: self._board_do(a), width=64).pack(pady=(0, 4))
        self.board_cards.append({"picture": picture, "status": status, "action": action})

    # -- edits: every one is written back to the script -------------------------------
    # -- the timeline under the cards ---------------------------------------------------
    def _draw_timeline(self, lengths=None):
        canvas = self.timeline
        canvas.delete("all")
        canvas.configure(bg=theme_color(MAIN_BG))
        if self.board is None or not len(self.board):
            self._timeline_edges = []
            return
        persona = voices.persona(self.var_voice.get())
        padding = PADDING_OPTIONS.get(self.var_padding.get(), DEFAULT_PADDING)
        lengths = lengths or self.board.lengths(persona, padding)
        width = max(canvas.winfo_width(), 200) - 8
        self._timeline_scale = width / max(sum(lengths), 0.1)
        x, self._timeline_edges = 4.0, []
        for i, (scene, length) in enumerate(zip(self.board.scenes, lengths)):
            w = length * self._timeline_scale
            spoken = pacing.estimate_voice(scene.voice or "", persona.rate, persona.pause_scale,
                                           persona.enhanced)
            fixed = scene.duration is not None
            cut = fixed and spoken > scene.duration + 0.05   # the line won't fit: shown
            canvas.create_rectangle(x, 4, x + w - 3, 42, outline="", fill=theme_color(
                WARN_COLOR if cut else CARD_HOVER if fixed else CARD_BG))
            canvas.create_rectangle(x + w - 8, 4, x + w - 3, 42, outline="",
                                    fill=theme_color(ACCENT if fixed else FIELD_BORDER))
            if w > 34:
                label = f"{i + 1}  {length:.1f} s" if w > 70 else str(i + 1)
                canvas.create_text(x + 7, 23, anchor="w", text=label, font=self.font_tiny,
                                   fill=theme_color(TEXT))
            x += w
            self._timeline_edges.append(x)

    def _timeline_at(self, x):
        """(scene index, whether x is on its right edge) for a point on the strip."""
        start = 4.0
        for i, edge in enumerate(self._timeline_edges):
            if x <= edge:
                return i, edge - x <= 9
            start = edge
        return None, False

    def _timeline_press(self, event):
        if self.board is None or self.is_rendering:
            return
        index, on_edge = self._timeline_at(event.x)
        if index is None:
            return
        start = self._timeline_edges[index - 1] if index else 4.0
        self._timeline_hold = {"index": index, "start": start, "drag": on_edge, "moved": False}

    def _timeline_drag(self, event):
        hold = self._timeline_hold
        if not hold or not hold["drag"]:
            return
        hold["moved"] = True
        self.board.set_length(hold["index"], (event.x - hold["start"]) / self._timeline_scale)
        self._draw_timeline()

    def _timeline_release(self, _event):
        hold, self._timeline_hold = self._timeline_hold, None
        if not hold:
            return
        if hold["moved"]:
            self._board_write()
        elif self.board_cards:   # a click: bring its card into view
            self.board_list._parent_canvas.yview_moveto(hold["index"] / len(self.board_cards))

    def _timeline_auto(self, event):
        index, _edge = self._timeline_at(event.x)
        if index is not None and self.board is not None and not self.is_rendering:
            self.board.set_length(index, None)
            self._board_write()

    def _board_write(self):
        text = self.board.text()
        if self.board_text and self.board_text != text:   # the step Undo goes back to
            self.board_history = (self.board_history + [self.board_text])[-UNDO_STEPS:]
            self.board_future = []
        self.board_text = text
        self.script_box.delete("1.0", "end")
        self.script_box.insert("1.0", text)
        self._commit_script()
        self.refresh_estimate()
        self._draw_timeline()

    def _board_edit(self, index, name, value):
        if self.board is None or index >= len(self.board):
            return
        before = self.board.text()
        self.board.set(index, name, value)
        if self.board.text() == before:
            return
        self._board_write()
        if name == "visual":   # other words, another clip or picture: load this card again
            scene = self.board.scenes[index]
            threading.Thread(target=self._board_load_one, daemon=True,
                             args=(self.board_gen, index, scene, self._board_ctx())).start()

    def board_undo(self, redo=False):
        """Back (or forward again) one change made in the Storyboard."""
        steps, other = ((self.board_future, self.board_history) if redo else
                        (self.board_history, self.board_future))
        if not steps or self.is_rendering or self._revising:
            return
        other.append(self.script_box.get("1.0", "end").strip())
        text = steps.pop()
        self.script_box.delete("1.0", "end")
        self.script_box.insert("1.0", text)
        self.board_marked = set()
        self._commit_script()
        self.refresh_board(force=True)
        self.refresh_estimate()

    def _undo_key(self, redo):
        """Ctrl+Z / Ctrl+Y on the Storyboard; a text box keeps its own undo."""
        if self.current_page == "board" and not isinstance(self.focus_get(), (tk.Entry, tk.Text)):
            self.board_undo(redo)
            return "break"
        return None

    def _board_do(self, change):
        """A change to the order or the kind of a scene: every card is built again."""
        if self.board is None or self.is_rendering:
            return
        before = self.board.text()
        change()
        if self.board.text() != before:
            self.board_marked = set()
            self._board_write()
            self.refresh_board(force=True)

    # -- pictures: found on worker threads, shown through the queue ---------------------
    def _picture_key(self, scene, ctx=None) -> str:
        ctx = ctx or self._board_ctx(search=False)
        return f"{scene.ai_prompt}|{ctx['look']}|{ctx['aspect']}"

    def _board_ctx(self, search=True) -> dict:
        """UI thread: what the picture workers need, read here because they must not touch Tk."""
        return {"look": self.var_ai_look.get(), "aspect": self.var_aspect.get(),
                "style": self.var_style.get(),
                "has_key": self._has_footage_key(), "ai": imagegen.installed(),
                "base": os.path.dirname(os.path.abspath(self.var_output.get() or ".")),
                "search": self.new_footage_search() if search else None}

    def _board_post(self, gen, **payload):
        self._queue.put(("board", dict(payload, gen=gen)))

    def _board_load(self, gen, scenes_now, ctx):
        for index, scene in enumerate(scenes_now):
            if gen != self.board_gen:
                return
            self._board_load_one(gen, index, scene, ctx)

    def _board_load_one(self, gen, index, scene, ctx):
        """Worker thread: the still for one card, and what to say beside it."""
        path, note = None, ""
        try:
            if scene.is_local:
                found, kind = footage.resolve_local(scene.local_path, ctx["base"])
                note = "Your own file"
                if kind == "image":
                    path = found
                else:
                    width, height, _length = preview.probe(found)
                    still = preview.poster(found, preview.fit((width, height), (320, 320)), at=0.5)
                    os.makedirs(THUMB_DIR, exist_ok=True)
                    path = os.path.join(THUMB_DIR, f"board_local_{index}.jpg")
                    still.save(path)
            elif scene.is_ai:
                path = self.board_pictures.get(self._picture_key(scene, ctx))
                note = "AI picture" if path else (
                    "AI picture: made with the video, or press Make picture to see it now"
                    if ctx["ai"] else "AI picture: download AI images to use it")
            elif not ctx["has_key"]:
                note = "Stock clip: add a free key in Settings to see it"
            else:
                query = scene.visual
                if query not in self.board_clips:
                    # Ranked as the render ranks them, so the card shows the clip it uses.
                    self.board_clips[query] = footage.rank(ctx["search"].find(query, 6), query,
                                                           scene.voice or "")
                clips = self.board_clips[query]
                if clips:
                    at = self.board_pos.get(query, 0) % len(clips)
                    path = scenes.fetch_thumb(clips[at], THUMB_DIR)
                    told = clips[at].about[:48]
                    note = f"Stock clip {at + 1} of {len(clips)}" + (f": {told}" if told else "")
                else:
                    note = "No stock clip found for these words"
            style = ctx.get("style")
            if path and style in drawn.STYLES and style != drawn.NORMAL:
                # The render draws every scene from its still: show the card the same way.
                from PIL import Image

                with Image.open(path) as image:
                    finished = drawn.prepare(image, preview.fit(image.size, (320, 320)), style)[0]
                os.makedirs(THUMB_DIR, exist_ok=True)
                path = os.path.join(THUMB_DIR, f"board_drawn_{index}.jpg")
                Image.fromarray(finished).save(path)
                note += f" · drawn as {style}"
        except Exception as exc:  # noqa: BLE001 - a missing still is not worth an error
            note = note or f"Could not load a picture ({type(exc).__name__})"
        self._board_post(gen, event="still", index=index, path=path, note=note)

    def _board_another(self, index):
        scene = self.board.scenes[index]
        clips = self.board_clips.get(scene.visual) or []
        if len(clips) > 1:
            self.board_pos[scene.visual] = (self.board_pos.get(scene.visual, 0) + 1) % len(clips)
            threading.Thread(target=self._board_load_one, daemon=True,
                             args=(self.board_gen, index, scene, self._board_ctx())).start()

    def _board_make(self, index):
        """Make (or make again) the AI picture for a scene; the video will use this one."""
        scene = self.board.scenes[index]
        if self._making_picture or self.is_rendering:
            self.board_status.configure(text="One picture at a time: wait for the last one.",
                                        text_color=WARN_COLOR)
            return
        if not imagegen.installed():
            self.select_page("images")
            return
        self._making_picture = True
        gen, key = self.board_gen, self._picture_key(scene)
        size, look, backend = imagegen.size_for(self.var_aspect.get()), self.var_ai_look.get(), \
            self.ai_backend_now()
        speed, threads = self.ai_speed_now(), self.threads_pair()
        self.board_cards[index]["status"].configure(text="Making the picture...")

        def worker():
            import hashlib

            try:
                os.makedirs(PREVIEW_DIR, exist_ok=True)
                name = hashlib.sha1(f"{key}|{time.time()}".encode()).hexdigest()[:16]
                path = os.path.join(PREVIEW_DIR, f"board_{name}.png")
                made = imagegen.make(scene.ai_prompt, size, path, look, backend=backend,
                                     log=self.ui.log, speed=speed, threads=threads)
                self.ui.ai_speed(made.seconds, speed)
                self._board_post(gen, event="made", index=index, key=key, path=path,
                                 seconds=made.seconds, backend=made.backend)
            except Exception as exc:  # noqa: BLE001 - shown on the card
                self._board_post(gen, event="not_made", index=index, message=str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _board_event(self, payload):
        """UI thread: news from the Storyboard's workers."""
        event, index = payload["event"], payload.get("index")
        if event in ("made", "not_made"):
            self._making_picture = False
            if event == "made":
                self.board_pictures[payload["key"]] = payload["path"]
                if payload["backend"] == imagegen.CPU:
                    self.remember_ai_backend(imagegen.CPU)
        if event == "revised":
            self._revised(payload)
            return
        if payload["gen"] != self.board_gen or index is None or index >= len(self.board_cards):
            return   # the cards were rebuilt meanwhile
        widgets = self.board_cards[index]
        if event == "not_made":
            widgets["status"].configure(text=ellipsize(payload["message"], 70),
                                        text_color=ERR_COLOR)
            return
        if event == "made":
            payload = dict(payload, note=f"AI picture, made in {payload['seconds']:.0f} s")
        widgets["status"].configure(text=payload["note"], text_color=TEXT_DIM)
        if widgets["action"] is not None and self.board.source(index) == boards.AI:
            widgets["action"].configure(text="Make another" if payload["path"] else "Make picture")
        self._board_show(index, payload["path"])

    def _board_show(self, index, path):
        from PIL import Image, ImageOps

        picture, box = self.board_cards[index]["picture"], self.board_box()
        try:
            with Image.open(path) as opened:
                fitted = ImageOps.fit(opened.convert("RGB"), (box[0] * 2, box[1] * 2))
            self.board_images[index] = ctk.CTkImage(light_image=fitted, dark_image=fitted, size=box)
            picture.configure(image=self.board_images[index], text="")
        except Exception:  # noqa: BLE001 - no path, or not a picture
            self.board_images.pop(index, None)
            picture.configure(image=None, text="No picture\nyet")

    def board_choices(self, script: str):
        """(stock clips, AI pictures) chosen in the Storyboard, by scene, for ``script``.

        Empty when the Storyboard shows another script: a choice belongs to the
        scene it was made for.
        """
        if self.board is None or (self.board_text or "").strip() != (script or "").strip():
            return {}, {}
        clips, pictures = {}, {}
        for index, scene in enumerate(self.board.scenes):
            if scene.is_ai:
                path = self.board_pictures.get(self._picture_key(scene))
                if path and os.path.isfile(path):
                    pictures[index] = path
            elif not scene.is_local:
                found = self.board_clips.get(scene.visual) or []
                at = self.board_pos.get(scene.visual, 0)
                if found and at:   # the first clip is what the search gives anyway
                    at %= len(found)
                    clips[index] = found[at:] + found[:at]
        return clips, pictures

    # -- "Change it": the Smart writer rewrites the script from a comment ----------------
    def revise_script(self, comment):
        comment = (comment or "").strip()
        if self._revising or self.is_rendering or not comment:
            return
        self.refresh_board()
        if self.board is None:
            return
        ok, why = writer.ready()
        if not ok:
            self.board_status.configure(text=f"Change it needs the Smart writer. {why}",
                                        text_color=WARN_COLOR)
            return
        if any(not scene.voice for scene in self.board.scenes):
            self.board_status.configure(
                text="Change it can't rewrite a script with silent scenes yet. Edit the cards "
                     "instead.", text_color=WARN_COLOR)
            return
        self._revising = True
        self.board_change.configure(state="disabled", text="Thinking...")
        self.board_status.configure(text=f"The Smart writer is changing the script: {comment}",
                                    text_color=TEXT_DIM)
        before = list(self.board.scenes)
        shown = [(self.board.shown(i), scene.voice) for i, scene in enumerate(before)]
        gpu, source = bool(self.var_writer_gpu.get()), self.source_text

        def worker():
            try:
                with writer.Session(lambda: False, gpu=gpu) as session:
                    answer = session.revise(shown, comment, source)
                self._queue.put(("board", {"event": "revised", "before": before,
                                           "answer": answer, "gen": 0}))
            except Exception as exc:  # noqa: BLE001 - shown under the box
                self._queue.put(("board", {"event": "revised", "before": before,
                                           "error": str(exc), "gen": 0}))

        threading.Thread(target=worker, daemon=True).start()

    def _revised(self, payload):
        self._revising = False
        self.board_change.configure(state="normal", text="Change it")
        if payload.get("error"):
            self.board_status.configure(text=payload["error"], text_color=ERR_COLOR)
            return
        before = payload["before"]
        if self.board is None or self.board.scenes != before:
            self.board_status.configure(text="The script changed meanwhile, so the rewrite was "
                                             "not used.", text_color=WARN_COLOR)
            return
        cards = draft.suggest_card if self.var_auto_cards.get() else None
        after = boards.apply_revision(before, payload["answer"], cards)
        marked = set(boards.changed(before, after))
        if not marked and len(after) == len(before) and after == before:
            self.board_status.configure(text="Nothing was changed. Try saying it another way.",
                                        text_color=WARN_COLOR)
            return
        self.board = boards.Board(after)
        self.board_marked = marked
        self._board_write()
        self.refresh_board(force=True)
        self.board_comment.delete(0, "end")
        gone = len(before) - len(after)
        parts = [f"{len(marked)} scene{'s' if len(marked) != 1 else ''} changed"] if marked else []
        if gone:
            parts.append(f"{abs(gone)} {'removed' if gone > 0 else 'added'}")
        if not marked and not gone:
            parts = ["the order changed"]
        self.board_status.configure(
            text=", ".join(parts).capitalize() + ". Look them over, then press Make video. "
                 "The Script page has the old lines if you undo there (Ctrl+Z).",
            text_color=OK_COLOR)
        self.save_project()

    # -- hearing one line -----------------------------------------------------------------
    def hear_line(self, text):
        """Speak one line in the chosen voice, without blocking the window."""
        if not text or self.voice_missing():
            return
        persona, online = voices.persona(self.var_voice.get()), bool(self.var_voice_online.get())

        def worker():
            import hashlib

            try:
                os.makedirs(PREVIEW_DIR, exist_ok=True)
                name = hashlib.sha1(f"{persona.id}|{online}|{text}".encode()).hexdigest()[:16]
                stem = os.path.join(PREVIEW_DIR, f"line_{name}")
                playable = stem + ".play.wav"
                if not os.path.exists(playable):
                    from vidgen import mastering

                    made = generate_voiceover(text, stem + ".mp3", lambda _line: None,
                                              persona.id, online)
                    partial = stem + ".tmp.wav"
                    mastering.run_ffmpeg(["-y", "-i", made, "-ar", "44100", "-ac", "1",
                                          "-c:a", "pcm_s16le", partial])
                    os.replace(partial, playable)
                self._queue.put(("line", {"path": playable, "error": None}))
            except Exception as exc:  # noqa: BLE001 - reported in the window
                self._queue.put(("line", {"path": None, "error": str(exc)}))

        threading.Thread(target=worker, daemon=True).start()

    def _line_ready(self, payload):
        if payload["error"]:
            self.append_log(f"⚠️ Could not read the line out: {payload['error']}")
            self.board_status.configure(text="Could not read the line out - see the Log",
                                        text_color=ERR_COLOR)
        elif sys.platform == "win32":
            import winsound

            winsound.PlaySound(payload["path"], winsound.SND_FILENAME | winsound.SND_ASYNC)

    def set_key_tip(self, on):
        """The free-key reminder after a keyless video: "Don't show again" turns it off."""
        self.key_tip = bool(on)
        save_settings(self._collect_settings())

    # ----------------------------------------------------------- AI IMAGES --
    def _build_images(self):
        page = self._scroll_page()
        page.grid_columnconfigure(0, weight=1)
        rows = itertools.count()

        self._caption(page, "AI IMAGES (OPTIONAL)", next(rows), pady=(0, 6))
        pack_row = SettingRow(
            page, self, self.icon("images"), "AI pictures, made on this PC",
            f"{imagegen.MODEL.name}, an open-source model, draws a picture for scenes stock "
            "footage has nothing for")
        pack_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self.images_state = ctk.CTkLabel(pack_row.control, text="", font=self.font_body,
                                         text_color=TEXT_MUTED)
        self.images_state.pack(side="left", padx=(0, 12))
        self.images_button = ctk.CTkButton(
            pack_row.control, text="", width=96, height=32, corner_radius=4,
            font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT)
        self.images_button.pack(side="left")
        # Only shown once the graphics card has failed here and the processor took over.
        self.images_retry = ctk.CTkButton(
            pack_row.control, text="Try the graphics card again", width=190, height=32,
            corner_radius=4, font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
            command=lambda: self.remember_ai_backend(""))

        warning = Card(page)
        warning.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkLabel(warning, text=f"{self.icon('info')}  {AI_IMAGES_WARNING}",
                     font=self.font_body, text_color=WARN_COLOR, anchor="w", justify="left",
                     wraplength=820).pack(fill="x", padx=16, pady=12)

        self._caption(page, "IN YOUR VIDEOS", next(rows))
        for_row = SettingRow(
            page, self, self.icon("script"), "Use AI pictures for",
            "Which scenes New from text gives an AI picture")
        for_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self._segmented(for_row.control, list(AI_IMAGES_FOR), self.var_ai_for, 470)
        self._choice_hint(page, next(rows), self.var_ai_for, AI_FOR_HINTS)
        look_row = SettingRow(page, self, self.icon("resolution"), "Look",
                              "One look for the whole video, so its pictures match")
        look_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self._segmented(look_row.control, list(imagegen.LOOKS), self.var_ai_look, 380)
        self._choice_hint(page, next(rows), self.var_ai_look, AI_LOOK_HINTS)

        device_row = SettingRow(page, self, self.icon("resolution"), "Make pictures on",
                                "Where the AI pictures are made")
        device_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self._segmented(device_row.control, list(AI_DEVICES), self.var_ai_device, 380)
        self._choice_hint(page, next(rows), self.var_ai_device, AI_DEVICE_HINTS)
        self.var_ai_device.trace_add("write", self._ai_device_picked)

        speed_row = SettingRow(page, self, self.icon("timer"), "Speed",
                               "Smaller pictures are made much faster, then enlarged")
        speed_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self._segmented(speed_row.control, list(imagegen.SPEEDS), self.var_ai_speed, 380)
        self._choice_hint(page, next(rows), self.var_ai_speed, AI_SPEED_HINTS)

        test_row = SettingRow(page, self, self.icon("timer"), "Test the processor's speed",
                              "For pictures made on the processor: makes one tiny picture a "
                              "few ways (a few minutes on a laptop) and keeps the fastest")
        test_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self.speed_test_button = ctk.CTkButton(
            test_row.control, text="Test speed", width=110, height=32, corner_radius=4,
            font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER, border_width=1,
            border_color=FIELD_BORDER, text_color=TEXT, command=self.test_ai_speed)
        self.speed_test_button.pack(side="left")
        ctk.CTkButton(test_row.control, text="Copy results", width=110, height=32,
                      corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
                      hover_color=CARD_HOVER, border_width=1, border_color=FIELD_BORDER,
                      text_color=TEXT, command=self.copy_speed_results,
                      ).pack(side="left", padx=(8, 0))
        self.speed_results = ctk.CTkLabel(page, text="", font=self.font_tiny,
                                          text_color=TEXT_MUTED, anchor="w", justify="left")
        self.speed_results.grid(row=next(rows), column=0, sticky="w", padx=18, pady=(0, 6))

        self._caption(page, "MOVING PICTURES (BETA, GRAPHICS CARD ONLY)", next(rows))
        moving_row = SettingRow(
            page, self, self.icon("play"), "Make AI pictures move",
            f"{animate.VIDEO_MODEL.name} turns a picture into a short moving clip. A separate "
            f"download ({animate.SIZE / 1e9:.1f} GB)")
        moving_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self.moving_state = ctk.CTkLabel(moving_row.control, text="", font=self.font_body,
                                         text_color=TEXT_MUTED)
        self.moving_state.pack(side="left", padx=(0, 12))
        self.moving_button = ctk.CTkButton(
            moving_row.control, text="", width=96, height=32, corner_radius=4,
            font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT)
        self.moving_button.pack(side="left")
        which_row = SettingRow(page, self, self.icon("scenes"), "Which pictures move",
                               "Each clip takes minutes, even on a good graphics card")
        which_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self._segmented(which_row.control, list(animate.CHOICES), self.var_animate, 380)
        self._choice_hint(page, next(rows), self.var_animate, MOVING_HINTS)
        self._moving_row_changed()

        self._caption(page, "TRY IT", next(rows))
        try_card = Card(page)
        try_card.grid(row=next(rows), column=0, sticky="ew", pady=(PAD // 2, PAD))
        try_card.grid_columnconfigure(0, weight=1)
        self.try_entry = focus_accent(ctk.CTkEntry(
            try_card, height=34, corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
            border_color=FIELD_BORDER, text_color=TEXT,
            placeholder_text="Describe a picture, for example: a robot holding a clipboard"))
        self.try_entry.grid(row=0, column=0, sticky="ew", padx=(16, 8), pady=(14, 6))
        self.try_entry.bind("<Return>", lambda _event: self.try_picture())
        self.try_button = ctk.CTkButton(
            try_card, text="Make picture", width=120, height=34, corner_radius=4,
            font=self.font_body, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color=ACCENT_TEXT, command=self.try_picture)
        self.try_button.grid(row=0, column=1, padx=(0, 16), pady=(14, 6))
        self.try_status = ctk.CTkLabel(try_card, text="", font=self.font_tiny,
                                       text_color=TEXT_DIM, anchor="w")
        self.try_status.grid(row=1, column=0, columnspan=2, sticky="w", padx=16)
        self.try_picture_label = ctk.CTkLabel(try_card, text="", width=10, height=10)
        self.try_picture_label.grid(row=2, column=0, columnspan=2, pady=(6, 14))

        self._images_row_changed()
        return page

    # -- moving pictures -------------------------------------------------------
    def _moving_row_changed(self, payload=None):
        if payload and payload.get("event") == "progress":
            self.moving_state.configure(text=f"Downloading: {payload['done'] / 1e9:.1f} of "
                                             f"{payload['total'] / 1e9:.1f} GB")
            self.moving_button.configure(text="Cancel", command=self._moving_stop.set)
            return
        if payload and payload.get("event") == "failed":
            self.append_log(f"⚠️ {payload['message']}")
        if animate.installed():
            self.moving_state.configure(text="Downloaded", text_color=OK_COLOR)
            self.moving_button.configure(text="Remove", command=self.remove_moving)
        else:
            self.moving_state.configure(
                text="Needs AI images first" if not imagegen.installed() else
                f"Not downloaded ({animate.SIZE / 1e9:.1f} GB)", text_color=TEXT_MUTED)
            self.moving_button.configure(text="Download", command=self.download_moving,
                                         state="normal" if imagegen.installed() else "disabled")

    def download_moving(self):
        self._moving_stop = stop = threading.Event()

        def worker():
            def progress(done, total):
                self._queue.put(("moving", {"event": "progress", "done": done, "total": total}))
            try:
                animate.download(progress, stop.is_set)
                self._queue.put(("moving", {"event": "done"}))
            except Exception as exc:  # noqa: BLE001 - shown in the Log
                self._queue.put(("moving", {"event": "failed", "message": str(exc)}))

        self._moving_row_changed({"event": "progress", "done": 0, "total": animate.SIZE})
        threading.Thread(target=worker, daemon=True).start()

    def remove_moving(self):
        animate.remove()
        self.var_animate.set(animate.CHOICES[0])
        self._moving_row_changed()

    def _choice_hint(self, page, row, variable, hints):
        """A line under a row of choices that explains the one that is picked."""
        label = ctk.CTkLabel(page, text=hints.get(variable.get(), ""), font=self.font_tiny,
                             text_color=TEXT_MUTED, anchor="w", justify="left", wraplength=860)
        label.grid(row=row, column=0, sticky="w", padx=18, pady=(0, 6))
        variable.trace_add(
            "write", lambda *_args: label.configure(text=hints.get(variable.get(), "")))

    def default_ai_speed(self) -> str:
        """Fast where pictures are made without a dedicated graphics card, else Balanced."""
        return imagegen.FAST if self.ai_slow() else imagegen.BALANCED

    def ai_backend_now(self) -> str:
        """Where the next picture is made: the user's choice, or in Auto what was learnt."""
        chosen = AI_DEVICES.get(self.var_ai_device.get(), "")
        return chosen or self.ai_backend

    def ai_slow(self) -> bool:
        """Whether AI pictures take minutes here: no usable graphics card, or measured so."""
        if self.ai_backend_now() == imagegen.CPU or (
                self.ai_backend_now() != imagegen.GPU and self.ai_gpu in ("integrated", "none")):
            return True
        return self.ai_fast_seconds > SLOW_FAST_SECONDS

    def _ai_device_picked(self, *_args):
        save_settings(self._collect_settings())
        if not self.ai_speed_chosen:
            self.var_ai_speed.set(self.default_ai_speed())
            self.ai_speed_chosen = False
        if hasattr(self, "images_state"):
            self._images_row_changed()

    def _ai_speed_picked(self, *_args):
        self.ai_speed_chosen = True

    def ai_speed_now(self) -> str:
        """The speed a picture is made at now: the processor never goes above Fast."""
        speed = self.var_ai_speed.get()
        return imagegen.FAST if self.ai_backend_now() == imagegen.CPU else speed

    def seconds_per_picture(self, speed=None) -> float:
        """Measured where possible, else a guess from what was measured on similar PCs."""
        fast = self.ai_fast_seconds or (270.0 if self.ai_slow() else 8.0)
        return fast * imagegen.cost(speed or self.ai_speed_now())

    def threads_pair(self) -> tuple[int, int]:
        """(threads, affinity) for processor pictures, from Settings or the speed test."""
        chosen = self.var_ai_threads.get()
        if chosen != "Auto":
            return int(chosen), 0
        try:
            threads, affinity = (int(part) for part in self.ai_threads_best.split(":"))
            return threads, affinity
        except ValueError:
            return imagegen.auto_threads(imagegen.cpu_cores())

    def note_picture_time(self, seconds, speed):
        """A picture just finished: keep its time, as if it were Fast, for the estimates."""
        self.ai_fast_seconds = round(seconds / imagegen.cost(speed), 1)
        if not self.ai_speed_chosen:
            self.var_ai_speed.set(self.default_ai_speed())
            self.ai_speed_chosen = False
        save_settings(self._collect_settings())

    def detect_graphics(self):
        """Once, in the background: does the picture program see a dedicated graphics card?"""
        if self.ai_gpu or not imagegen.installed():
            return

        def worker():
            self._queue.put(("ai_gpu", {"found": imagegen.graphics()}))

        threading.Thread(target=worker, daemon=True).start()

    def _graphics_found(self, found):
        if not found:
            return
        self.ai_gpu = found
        if not self.ai_speed_chosen:
            self.var_ai_speed.set(self.default_ai_speed())
            self.ai_speed_chosen = False
        save_settings(self._collect_settings())

    def pictures_to_make(self) -> int:
        """How many AI pictures the script in the editor still needs made."""
        try:
            parsed = parse_script(self.script_box.get("1.0", "end"))
        except ScriptError:
            return 0
        if not imagegen.installed():
            return 0
        keyless = not self._has_footage_key()
        ready = set(self.board_choices(self.script_box.get("1.0", "end").strip())[1])
        return sum(1 for i, scene in enumerate(parsed)
                   if i not in ready and (scene.is_ai or (keyless and not scene.is_local)))

    def _slow_pictures_need_a_yes(self) -> bool:
        """On a slow PC, before a long render: say how long, and offer quicker ways."""
        count = self.pictures_to_make()
        script = self.script_box.get("1.0", "end").strip()
        if not count or self._slow_ok == script or not self.ai_slow():
            return False
        if self.seconds_per_picture() * count < 180:
            return False
        SlowPicturesDialog(self, count)
        return True

    def start_after_slow_choice(self, overrides):
        """The slow-PC window's answer: go ahead with these settings, for this video only."""
        self._slow_ok = self.script_box.get("1.0", "end").strip()
        self._render_overrides = dict(overrides)
        self.start_render()

    def ai_mode(self) -> str:
        """Which scenes New from text gives an AI picture: none unless AI images are here."""
        if not imagegen.installed():
            return draft.AI_NONE
        if not self._has_footage_key():
            return draft.AI_ALL   # nothing else to show a scene with
        return {AI_IMAGES_FOR[0]: draft.AI_UNFILMABLE,
                AI_IMAGES_FOR[1]: draft.AI_ALL}.get(self.var_ai_for.get(), draft.AI_NONE)

    def test_ai_speed(self):
        """Time a tiny picture a few ways on the processor; Auto then uses the fastest."""
        if self._making_picture or self.is_rendering or not imagegen.installed():
            return
        self._making_picture = True
        self.speed_test_button.configure(state="disabled", text="Testing...")
        self.speed_results.configure(text="Testing: each try makes one tiny picture...")
        choices = imagegen.thread_choices(imagegen.cpu_cores())

        def worker():
            try:
                found = imagegen.benchmark(choices, os.path.join(PREVIEW_DIR, "speed"),
                                           log=self.ui.log)
                self._queue.put(("images", {"event": "bench", "results": found}))
            except Exception as exc:  # noqa: BLE001 - shown in the tab
                self._queue.put(("images", {"event": "bench", "results": [], "error": str(exc)}))

        threading.Thread(target=worker, daemon=True).start()

    def _speed_tested(self, payload):
        self._making_picture = False
        self.speed_test_button.configure(state="normal", text="Test speed")
        timed = [r for r in payload["results"] if r[3] is not None]
        if not timed:
            self.speed_results.configure(text=payload.get("error") or "The test failed; see the Log.",
                                         text_color=ERR_COLOR)
            return
        best = min(timed, key=lambda r: r[3])
        self.ai_threads_best = f"{best[1]}:{best[2]}"
        cores = imagegen.cpu_cores()
        lines = [f"{label}: {seconds:.0f} s" if seconds is not None else f"{label}: failed"
                 for label, _t, _a, seconds in payload["results"]]
        self.speed_report = (f"AI Video Studio {APP_VERSION} speed test · "
                             f"{len(cores)} cores, kinds {sorted({e for e, _m, _s in cores})} · "
                             f"{os.cpu_count()} logical\n" + "\n".join(lines)
                             + f"\nFastest: {best[0]}")
        self.speed_results.configure(text="\n".join(lines) + f"\nAuto now uses: {best[0]}",
                                     text_color=TEXT_MUTED)
        save_settings(self._collect_settings())

    def copy_speed_results(self):
        report = getattr(self, "speed_report", "")
        if report:
            self.clipboard_clear()
            self.clipboard_append(report)
            self.speed_results.configure(text=self.speed_results.cget("text") + "\nCopied.")

    def _images_row_changed(self, payload=None):
        """The tab's first row: what state AI images are in, and what the button does."""
        self._images_confirm = False
        gigabytes = sum(m.size for m in imagegen.needed_files()) / 1e9
        if payload and payload["event"] == "progress":
            self.images_state.configure(
                text=f"Downloading {payload['done'] / 1e9:.2f} of {payload['total'] / 1e9:.2f} GB",
                text_color=TEXT_MUTED)
            self.images_button.configure(text="Cancel", command=self._images_stop.set)
            return
        if payload and payload["event"] == "failed":
            self.images_state.configure(text=ellipsize(payload["message"], 60),
                                        text_color=ERR_COLOR)
        elif imagegen.installed():
            slow = " · using the processor (slower)" if self.ai_backend_now() == imagegen.CPU \
                else ""
            self.images_state.configure(text=f"Installed · {imagegen.SIZE / 1e9:.1f} GB{slow}",
                                        text_color=OK_COLOR)
        else:
            self.images_state.configure(text=f"Not downloaded · {gigabytes:.1f} GB",
                                        text_color=TEXT_MUTED)
        if imagegen.installed():
            self.images_button.configure(text="Remove", command=self._confirm_remove_images)
        else:
            self.images_button.configure(text="Download", command=self.start_images_download)
        self.try_button.configure(
            state="normal" if imagegen.installed() and not self._making_picture else "disabled")
        if imagegen.installed() and self.ai_backend == imagegen.CPU and \
                self.var_ai_device.get() == "Auto":
            self.images_retry.pack(side="left", padx=(8, 0))
        else:
            self.images_retry.pack_forget()
        self._sync_look_bar()
        if not imagegen.installed():
            self.try_status.configure(text="Download AI images first.", text_color=TEXT_DIM)

    def remember_ai_backend(self, backend):
        """Keep what AI pictures need on this PC: "cpu" after the graphics card failed."""
        if backend == self.ai_backend:
            return
        self.ai_backend = backend
        save_settings(self._collect_settings())
        self.append_log(
            "🎨 The graphics card could not make an AI picture, so the processor is used from "
            "now on. The AI images tab can switch back." if backend == imagegen.CPU
            else "🎨 AI pictures will try the graphics card again.")
        if hasattr(self, "images_state"):
            self._images_row_changed()

    def _confirm_remove_images(self):
        """Remove deletes 4.2 GB, so it takes a second press."""
        if not self._images_confirm:
            self._images_confirm = True
            self.images_state.configure(text="Press again to remove AI images",
                                        text_color=ERR_COLOR)
            return
        imagegen.remove()
        self.append_log("🗑️ AI images removed.")
        self._images_row_changed()
        self.refresh_estimate()

    def start_images_download(self):
        """Fetch AI images on a worker thread; the tab's row shows how it goes."""
        if self.images_downloading:
            return
        self.images_downloading = True
        self._images_stop = stop = threading.Event()
        total = sum(m.size for m in imagegen.needed_files())
        self.append_log(f"⬇️ Downloading AI images ({total / 1e9:.1f} GB)...")

        def worker():
            last = 0.0

            def progress(done, total):
                nonlocal last
                now = time.monotonic()
                if now - last >= 0.25 or done >= total:
                    last = now
                    self._queue.put(("images", {"event": "progress", "done": done,
                                                "total": total}))

            try:
                imagegen.download(progress, stop.is_set)
                self._queue.put(("images", {"event": "done"}))
            except imagegen.Stopped:
                self._queue.put(("images", {"event": "stopped"}))
            except Exception as exc:  # noqa: BLE001 - a thread must not die silently
                self._queue.put(("images", {"event": "failed", "message": str(exc)}))

        threading.Thread(target=worker, daemon=True).start()
        self._images_row_changed({"event": "progress", "done": 0, "total": total})

    def try_picture(self):
        """Make one picture from the Try it box, without blocking the window."""
        prompt = self.try_entry.get().strip()
        if self._making_picture or not prompt or not imagegen.installed():
            return
        if self.is_rendering:
            self.try_status.configure(text="Wait for the video to finish first.",
                                      text_color=WARN_COLOR)
            return
        self._making_picture = True
        self.try_button.configure(state="disabled", text="Making...")
        self.try_status.configure(text="Making the picture. The first one takes longest.",
                                  text_color=TEXT_DIM)
        size, look = imagegen.size_for(self.var_aspect.get()), self.var_ai_look.get()
        backend, speed, threads = self.ai_backend_now(), self.ai_speed_now(), self.threads_pair()

        def worker():
            try:
                os.makedirs(PREVIEW_DIR, exist_ok=True)
                path = os.path.join(PREVIEW_DIR, "ai_try.png")
                made = imagegen.make(prompt, size, path, look, backend=backend,
                                     log=self.ui.log, speed=speed, threads=threads)
                self.ui.ai_speed(made.seconds, speed)
                self._queue.put(("images", {"event": "made", "path": path,
                                            "seconds": made.seconds, "size": made.size,
                                            "backend": made.backend}))
            except Exception as exc:  # noqa: BLE001 - shown in the tab
                self._queue.put(("images", {"event": "not_made", "message": str(exc)}))

        threading.Thread(target=worker, daemon=True).start()

    def _images_event(self, payload):
        """UI thread: news from the AI images download or the Try it box."""
        event = payload["event"]
        if event == "bench":
            self._speed_tested(payload)
            return
        if event in ("made", "not_made"):
            self._making_picture = False
            self.try_button.configure(state="normal", text="Make picture")
            if event == "not_made":
                self.try_status.configure(text=payload["message"], text_color=ERR_COLOR)
                return
            from PIL import Image

            width, height = payload["size"]
            shown = (round(360 * width / height), 360) if height >= width else (480, 270)
            with Image.open(payload["path"]) as opened:
                picture = opened.convert("RGB")
            self._try_image = ctk.CTkImage(light_image=picture, dark_image=picture, size=shown)
            self.try_picture_label.configure(image=self._try_image)
            where = "the processor" if payload["backend"] == imagegen.CPU else "the graphics card"
            self.try_status.configure(
                text=f"Made in {payload['seconds']:.0f} s on {where}.", text_color=OK_COLOR)
            if payload["backend"] == imagegen.CPU:
                self.remember_ai_backend(imagegen.CPU)
            return
        if event != "progress":
            self.images_downloading = False
            self.append_log({"done": "✅ AI images are ready.",
                             "stopped": "⏸️ AI images download paused.",
                             "failed": f"⚠️ {payload.get('message', '')}"}[event])
            self.refresh_estimate()
        self._images_row_changed(payload)

    # -- the offline voice's one-time download -------------------------------
    def voice_missing(self) -> bool:
        """Whether the voice still has to be fetched. Starts fetching it if so."""
        if self.var_voice_online.get() or localvoice.installed():
            return False
        self.start_voice_download()
        self.status_label.configure(text="Downloading the voice - try again when it is ready")
        return True

    def start_voice_download(self):
        """Fetch the voice's two files on a worker thread."""
        if self.voice_downloading or localvoice.installed():
            return
        self.voice_downloading = True
        self.append_log(f"⬇️ Downloading the voice, once ({localvoice.SIZE / 1e6:.0f} MB)...")

        def worker():
            last = 0.0

            def progress(done, total):
                nonlocal last
                now = time.monotonic()
                if now - last >= 0.5 or done >= total:
                    last = now
                    self._queue.put(("voice", {"event": "progress", "done": done, "total": total}))

            try:
                localvoice.download(progress)
                self._queue.put(("voice", {"event": "done"}))
            except Exception as exc:  # noqa: BLE001 - a thread must not die silently
                self._queue.put(("voice", {"event": "failed", "message": str(exc)}))

        threading.Thread(target=worker, daemon=True).start()
        self._voice_row_changed({"event": "progress", "done": 0, "total": localvoice.SIZE})

    def _voice_event(self, payload):
        """UI thread: the voice download's news, to the Log and the Settings row."""
        if payload["event"] == "done":
            self.voice_downloading = False
            self.append_log("✅ The voice is ready.")
        elif payload["event"] == "failed":
            self.voice_downloading = False
            self.append_log(f"⚠️ The voice could not be downloaded: {payload['message']}")
        self._voice_row_changed(payload)

    def _voice_row_changed(self, payload=None):
        if not hasattr(self, "voice_state"):
            return   # Settings has not been built yet
        if self.voice_downloading and payload and payload["event"] == "progress":
            self.voice_state.configure(
                text=f"Downloading {payload['done'] * 100 // max(payload['total'], 1)}%")
            self.voice_button.configure(state="disabled")
        elif localvoice.installed():
            self.voice_state.configure(text="Ready")
            self.voice_button.pack_forget()
        else:
            self.voice_state.configure(text="Not downloaded")
            self.voice_button.configure(state="normal")

    # -- voice preview -------------------------------------------------------
    def preview_voice(self):
        """Speak a short sample in the chosen voice, without blocking the window."""
        if self._previewing or self.voice_missing():
            return
        persona = voices.persona(self.var_voice.get())
        self._previewing = True
        self.preview_button.configure(state="disabled", text="Generating...")
        threading.Thread(target=self._preview_worker,
                         args=(persona.id, bool(self.var_voice_online.get())),
                         daemon=True).start()

    def _preview_worker(self, voice_id, online):
        """Worker thread: the full voice chain once, cached per voice, engine and version."""
        try:
            os.makedirs(PREVIEW_DIR, exist_ok=True)
            engine = "online" if online else "offline"
            stem = os.path.join(PREVIEW_DIR, re.sub(r"[^\w-]", "_",
                                                    f"{voice_id}_{engine}_{APP_VERSION}"))
            playable = stem + ".play.wav"
            if not os.path.exists(playable):
                made = generate_voiceover(PREVIEW_TEXT, stem + ".mp3", self.ui.log, voice_id,
                                          online)
                # winsound plays WAV only; classic voices arrive as MP3.
                from vidgen import mastering
                # Convert beside it, then rename: a failed run must not leave a
                # broken file that every later preview would reuse.
                partial = stem + ".tmp.wav"
                mastering.run_ffmpeg(["-y", "-i", made, "-ar", "44100", "-ac", "1",
                                      "-c:a", "pcm_s16le", partial])
                os.replace(partial, playable)
            self._queue.put(("preview", {"path": playable, "error": None}))
        except Exception as exc:  # noqa: BLE001 - reported in the window
            self._queue.put(("preview", {"path": None, "error": str(exc)}))

    def _preview_done(self, path, error):
        self._previewing = False
        self.preview_button.configure(state="normal",
                                      text=f"{self.icon('play')}  Preview Voice")
        if error:
            self.append_log(f"⚠️ Voice preview failed: {error}")
            self.status_label.configure(text="Voice preview failed - see the Log")
            return
        # The status line mirrors the latest log line, which is the preview's
        # "Generating..." - don't leave that showing once it's done.
        if not self.is_rendering:
            short = voices.persona(self.var_voice.get()).short
            self.status_label.configure(text=f"Previewing {short}")
        if sys.platform == "win32":
            import winsound

            # SND_ASYNC returns at once; the sound plays on its own.
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)

    def my_voice_made(self):
        """My voice was just made: list it in every voice menu and choose it."""
        values = voices.labels()
        for menu in self.voice_menus:
            menu.configure(values=values)
        self.var_voice.set(voices.persona(voices.MY_VOICE).label)

    # -- title, description and tags for posting ----------------------------
    def make_upload_text(self, videos):
        """After a render: the Smart writer writes them (or the first line does)."""
        script, pid = self.script_box.get("1.0", "end").strip(), self.project
        smart = self.writer_mode == "Smart writer" and writer.ready()[0]
        gpu = bool(self.var_writer_gpu.get())

        def worker():
            upload = None
            if smart:
                try:
                    with writer.Session(gpu=gpu) as session:
                        upload = session.upload_text(script)
                except Exception:  # noqa: BLE001 - the plain version is still useful
                    upload = None
            try:
                upload = upload or writer.upload_fallback(script)
            except ScriptError:
                return
            self._queue.put(("upload", {"upload": upload, "videos": videos, "project": pid}))

        threading.Thread(target=worker, daemon=True).start()

    def _upload_done(self, payload):
        upload = payload["upload"]
        self.last_upload = upload
        for video in payload["videos"]:
            try:
                with open(os.path.splitext(video)[0] + ".upload.txt", "w",
                          encoding="utf-8") as fh:
                    fh.write(writer.upload_file_text(upload))
            except OSError:
                pass
        data = projects.load(payload["project"]) if payload["project"] else None
        if data is not None:
            projects.save(payload["project"], dict(data, upload=upload), touch=False)
        for window in self.winfo_children():
            if isinstance(window, ResultDialog):
                try:
                    window.set_upload(upload)
                except tk.TclError:
                    pass   # closed before the text arrived; it is saved beside the video

    # -- make several -------------------------------------------------------
    def start_batch(self, texts):
        """Write and make each text in turn; the Log says how far it is."""
        if self.is_rendering or self._batch:
            return
        if not self._has_footage_key() and not imagegen.installed():
            WelcomeDialog(self)
            return
        template = draft.TEMPLATES.get(self.var_template.get())
        if template:
            self.var_auto_cards.set(template.cards)
            self.caption_vars["caption_style"].set(template.caption_style)
            audience, length = template.audience, template.length
        else:
            audience = draft.Audience(self.audience["audience_content"],
                                      self.audience["audience_age"],
                                      self.audience["audience_platform"])
            length = audience.length
        self._batch = {"texts": texts, "at": 0, "made": [], "failed": [],
                       "audience": audience, "length": length,
                       "folder": os.path.dirname(os.path.abspath(
                           self.var_output.get() or default_output_dir()))}
        self.append_log(f"📚 Making {len(texts)} videos, one after another.")
        self.select_page("log")
        self._batch_next()

    def _batch_next(self):
        b = self._batch
        if b is None:
            return
        if self.is_rendering:   # the last render is still clearing up
            self.after(300, self._batch_next)
            return
        if b["at"] >= len(b["texts"]):
            self._batch = None
            failed = "".join(f"\nVideo {n}: {why}" for n, why in b["failed"])
            self.append_log(f"📚 Done: {len(b['made'])} of {len(b['texts'])} videos made.")
            Dialog(self, "Videos made", f"{len(b['made'])} of {len(b['texts'])} videos were "
                   f"made. They are on the Projects page and in {b['folder']}." + failed,
                   ok=not b["failed"])
            if self.current_page == "projects":
                self.refresh_projects()
            return
        self.append_log(f"📝 Video {b['at'] + 1} of {len(b['texts'])}: writing the script...")
        args = (b["texts"][b["at"]], b["audience"], b["length"], self.settings["padding"],
                bool(self.var_auto_cards.get()), self.ai_mode(), bool(self.var_writer_gpu.get()))
        threading.Thread(target=self._batch_write, args=args, daemon=True).start()

    def _batch_write(self, text, audience, length, padding, cards, ai, gpu):
        """Worker: one script, by the Smart writer if it is there, else Quick split."""
        try:
            if self.writer_mode == "Smart writer" and writer.ready()[0]:
                with writer.Session(gpu=gpu) as session:
                    result = session.write(text, audience, length, padding, cards, ai,
                                           bool(self.var_web_facts.get()))
                    try:   # while the model is loaded anyway
                        upload = session.upload_text(result.script)
                    except writer.WriterError:
                        upload = writer.upload_fallback(result.script)
            else:
                result = draft.write(text, audience, length, padding=padding, cards=cards, ai=ai)
                upload = writer.upload_fallback(result.script)
            self._queue.put(("batch", {"result": result, "upload": upload}))
        except Exception as exc:  # noqa: BLE001 - this video is skipped, the rest go on
            self._queue.put(("batch", {"error": str(exc) or type(exc).__name__}))

    def _batch_event(self, payload):
        b = self._batch
        if b is None:
            return
        if payload.get("error"):
            b["failed"].append((b["at"] + 1, payload["error"]))
            b["at"] += 1
            self._batch_next()
            return
        result = payload["result"]
        b["upload"] = payload.get("upload")
        self.project = None   # each video is its own project
        audience = b["audience"]   # as use_draft keeps it: the music's mood follows it
        self.audience = {"audience_content": audience.content, "audience_age": audience.age,
                         "audience_platform": audience.platform}
        self.script_box.delete("1.0", "end")
        self.script_box.insert("1.0", result.script)
        self.var_voice.set(voices.persona(result.voice).label)
        self.var_aspect.set(result.aspect)
        self.var_target.set(result.target)
        name = re.sub(r'[<>:"/\\|?*]+', "", projects.default_title(result.script)).strip(" .")
        self.var_output.set(os.path.join(b["folder"], (name or "video")[:60] + ".mp4"))
        self._commit_script()
        self._slow_ok = result.script.strip()   # nobody is there to answer the slow-PC window
        if self.ai_slow():
            self._render_overrides = {"ai_reuse": SLOW_AI_SCENES}
        self.append_log(f"🎬 Video {b['at'] + 1} of {len(b['texts'])}: making it...")
        self.start_render()
        if not self.is_rendering:
            b["failed"].append((b["at"] + 1, "the video could not be started (see the Log)"))
            b["at"] += 1
            self.after(300, self._batch_next)

    # -- both shapes ---------------------------------------------------------
    def make_both_shapes(self):
        """This shape, then the other one, saved side by side in the same project."""
        if self.is_rendering:
            return
        shape = self.var_aspect.get()
        self._both = {"shape": shape, "other": "16:9" if shape == "9:16" else "9:16",
                      "output": self.var_output.get()}
        self.start_render()
        if not self.is_rendering:   # it asked something first, or was cancelled
            self._both = None
        else:
            self._both["output"] = self.var_output.get()   # where the Save window put it

    def _second_shape(self):
        if self.is_rendering:   # the first render is still clearing up its temp files
            self.after(300, self._second_shape)
            return
        both = self._both
        base, ext = os.path.splitext(both["first"][0])
        self.var_aspect.set(both["other"])
        self.var_output.set(f"{base} ({both['other'].replace(':', 'x')}){ext or '.mp4'}")
        self.append_log(f"🎬 Now the {both['other']} version.")
        self.start_render()
        if not self.is_rendering:
            self._shape_back(both)
            self._both = None

    def _shape_back(self, both):
        self.var_aspect.set(both["shape"])
        self.var_output.set(both["output"])

    # -- render --------------------------------------------------------------
    def start_render(self):
        if self.is_rendering:
            return
        # Plain text in the script box: it wants New from text, not an error on line 1.
        pasted = self.script_box.get("1.0", "end").strip()
        if looks_like_plain_text(pasted):
            DraftDialog(self).prefill(pasted)
            return
        # No key yet: walk the user through getting one instead of failing. With AI images
        # downloaded there is nothing to walk through: every scene gets a picture.
        if not self._has_footage_key() and self._needs_stock_footage() \
                and not imagegen.installed():
            WelcomeDialog(self)
            return
        if self._slow_pictures_need_a_yes():
            return
        if self.voice_missing():
            self.select_page("log")
            return

        # Like a browser download: ask where this one goes, unless told not to.
        overwrite = False
        if self.var_ask_save.get() and not self._batch \
                and not (self._both and self._both.get("first")):
            chosen = self._ask_save_path()
            if not chosen:
                return   # cancelled: no render
            self.var_output.set(chosen)
            # Windows has already asked before replacing an existing file.
            overwrite = True

        self.project = self.project or projects.new_id()
        # Snapshot every widget value here, on the UI thread.
        settings = self._collect_settings()
        self.settings = settings
        save_settings(settings)
        self._cancel = threading.Event()
        # What was chosen in the Storyboard holds only for the script it shows.
        choices, pictures = self.board_choices(settings["script"])
        cfg = dict(settings, cache_dir=SEARCH_CACHE_DIR, cache_root=CACHE_ROOT,
                   app_version=APP_VERSION, overwrite=overwrite,
                   cancel=self._cancel, clip_choices=choices, ai_pictures=pictures,
                   ai_images_speed=self.ai_speed_now(), ai_threads=self.threads_pair(),
                   ai_images_backend=self.ai_backend_now(),
                   project_dir=projects.folder(self.project),
                   target_length=pacing.TARGETS.get(settings["target_length"]))

        if cfg["music_path"] == AUTO_TRACK:
            cfg["music_path"] = draft.pick_track(bundled_tracks(), self.audience[
                "audience_content"], settings["script"]) or ""
        cfg.update(self._render_overrides)   # "make 2 and reuse", "Fast": this video only
        self._render_overrides = {}
        self._slow_ok = None
        self._set_busy(True)
        if choices:
            self.append_log("🎬 Using the clips chosen in the Storyboard.")
        for bar in self._progress_bars():
            bar.configure(mode="determinate")
            bar.set(0)
        self.select_page("log")
        threading.Thread(target=render_worker, args=(cfg, self.ui), daemon=True).start()

    # -- cache -----------------------------------------------------------------
    def _show_cache_size(self):
        size = cache.size(CACHE_ROOT)
        self.cache_label.configure(text=f"{size / 1e6:.0f} MB kept" if size else "Empty")

    def clear_cache(self):
        if self.is_rendering:
            self.cache_label.configure(text="Wait for the render to finish first.")
            return
        freed = cache.clear(CACHE_ROOT)
        self.append_log(f"🧹 Cache cleared: {freed / 1e6:.0f} MB freed.")
        self._show_cache_size()

    # -- shutdown ------------------------------------------------------------
    def _on_close(self):
        self.save_project()
        save_settings(self._collect_settings())
        writer.close_all()   # a title or translation still being written: its model stops too
        self.destroy()


def main():
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                APP_MODEL_ID
            )
        except Exception:
            pass
    if "--tiptest" in sys.argv:   # developer tool: every place on the map of Windows, in the app
        from vidgen import tiptest

        tiptest.main(sys.argv[1:], sys.modules[__name__])
        return
    if "--bench" in sys.argv:   # developer tool: timed sample renders, report on the Desktop
        from vidgen import bench

        report = os.path.join(paths.known_folder(paths.CSIDL_DESKTOP, "Desktop"),
                              f"AIVideoStudio-bench-{time.strftime('%Y%m%d-%H%M')}.txt")
        bench.main(sys.argv[1:], APP_VERSION, load_settings(), report)
        os.startfile(report)
        return
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
