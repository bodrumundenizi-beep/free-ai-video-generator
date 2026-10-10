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
from vidgen.script import ScriptError, count_scenes, parse_script  # noqa: E402
from vidgen import voices  # noqa: E402
from vidgen.render import TEMP_ROOT  # noqa: E402
from vidgen import (  # noqa: E402
    captions, diagnostics, draft, footage, pacing, paths, preview, scenes, updates, writer,
)
from vidgen import imagegen, localvoice  # noqa: E402
from vidgen.voice import generate_voiceover  # noqa: E402


APP_NAME = "AI Video Studio"
APP_VERSION = "3.9.0"
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
SEARCH_CACHE_DIR = os.path.join(SETTINGS_DIR, "cache", "search")
# Stays on this PC. Only a report the user sends themselves ever quotes from it.
LOG_DIR = os.path.join(SETTINGS_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "app.log")
PADDING_OPTIONS = {"0s": 0.0, "0.25s": 0.25, "0.5s": 0.5, "1s": 1.0}
DEFAULT_PADDING = 0.25


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
                      "minute more.",
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
AI_IMAGES_WARNING = (
    "Needs a graphics card with about 8 GB of video memory. Around 20 seconds per picture "
    "on an RTX 4060. Without a graphics card a picture can take several minutes.")


def default_settings() -> dict:
    return {
        "api_key": "",
        "pixabay_key": "",
        "padding": DEFAULT_PADDING,
        "music_enabled": False,
        "ask_save": True,
        "welcome_seen": False,
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
        "music_path": "",
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
    if data["music_path"] and not os.path.isfile(str(data["music_path"])):
        data["music_path"] = ""

    if data["aspect"] not in RATIO_OPTIONS:
        data["aspect"] = "9:16"
    if data["resolution"] not in RESOLUTION_OPTIONS:
        data["resolution"] = "1080p"
    # An ID or an old label; the four original labels are the Classic voices' IDs.
    data["voice"] = voices.persona(data["voice"]).id
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

    def __init__(self, app, title, message, ok=True, on_report=None):
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
        ctk.CTkButton(buttons, text="OK", width=96, height=32, corner_radius=4,
                      font=app.font_body, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      text_color=ACCENT_TEXT, command=self.destroy).pack(side="right")
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


class ResultDialog(ctk.CTkToplevel):
    """Render complete: the video, playable in the window, with its sound.

    Tk has no video widget, so vidgen.preview decodes small frames and this
    window shows them on a timer while winsound plays the soundtrack. Play
    and stop only - the default player is one button away for anything more.
    """

    def __init__(self, app, title, message, paths):
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
            self.version_names = [f"Version {n}" for n in range(1, len(self.paths) + 1)]
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
        button("Close", self.close).pack_configure(pady=(0, 16))

        centre_on(self, app)

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


class ScenePreviewDialog(ctk.CTkToplevel):
    """The clip chosen for each scene, before rendering, with "Try another".

    Searching and fetching stills happen on worker threads and come back
    through the app's UI queue; this window only draws what arrives.
    """

    def __init__(self, app, rows, picker, frame_size):
        super().__init__(app)
        self.app = app
        self.rows = rows
        self.picker = picker
        self.stopping = threading.Event()
        # Read on this thread: workers must not touch Tk variables.
        self.search = app.new_footage_search()
        self.local_base = os.path.dirname(app.var_output.get().strip())
        portrait = frame_size[1] >= frame_size[0]
        self.box = (72, 128) if portrait else (160, 90)
        self.widgets = {}   # row index -> {"picture", "status", "button"}
        self.images = {}    # row index -> CTkImage, kept so Tk doesn't drop it
        self.title("Preview scenes")
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.close)

        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        ctk.CTkLabel(card, text="Preview scenes", font=app.font_title, text_color=TEXT,
                     anchor="w").pack(fill="x", padx=20, pady=(18, 2))
        ctk.CTkLabel(
            card, font=app.font_body, text_color=TEXT_MUTED, justify="left",
            wraplength=600, anchor="w",
            text="These are the clips the video will use. Press Try another on any scene "
                 "you don't like.",
        ).pack(fill="x", padx=20, pady=(0, 10))

        row_height = self.box[1] + 22
        listing = ctk.CTkScrollableFrame(
            card, width=620, height=min(len(rows) * row_height, 430), fg_color="transparent",
            scrollbar_fg_color=SCROLLBAR_BG, scrollbar_button_color=SCROLLBAR_THUMB,
            scrollbar_button_hover_color=SCROLLBAR_THUMB_HOVER)
        listing.pack(padx=12)
        listing.grid_columnconfigure(1, weight=1)
        for position, row in enumerate(rows):
            picture = ctk.CTkLabel(listing, text="", width=self.box[0], height=self.box[1],
                                   fg_color=FIELD_BG, corner_radius=4)
            picture.grid(row=position, column=0, padx=(8, 12), pady=6)
            words = ctk.CTkFrame(listing, fg_color="transparent")
            words.grid(row=position, column=1, sticky="w")
            shown = os.path.basename(row.visual) if row.is_local else row.visual
            ctk.CTkLabel(words, text=f"Scene {row.index + 1} · {ellipsize(shown, 44)}",
                         font=app.font_bold, text_color=TEXT, anchor="w").pack(anchor="w")
            if row.voice:
                ctk.CTkLabel(words, text=row.voice, font=app.font_tiny, text_color=TEXT_MUTED,
                             anchor="w", justify="left", wraplength=320).pack(anchor="w")
            status = ctk.CTkLabel(words, text="Your own file" if row.is_local
                                  else "AI picture, made with the video" if row.is_ai
                                  else "Searching...",
                                  font=app.font_tiny, text_color=TEXT_DIM, anchor="w")
            status.pack(anchor="w")
            button = None
            if row.is_stock:
                button = ctk.CTkButton(
                    listing, text="Try another", width=104, height=30, corner_radius=4,
                    font=app.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                    border_width=1, border_color=FIELD_BORDER, text_color=TEXT, state="disabled",
                    command=lambda index=row.index: self.try_another(index))
                button.grid(row=position, column=2, padx=(8, 12))
            self.widgets[row.index] = {"picture": picture, "status": status, "button": button}

        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.pack(fill="x", padx=20, pady=(12, 18))
        self.render_button = ctk.CTkButton(
            buttons, text="Render with these clips", width=190, height=32, corner_radius=4,
            font=app.font_body, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color=ACCENT_TEXT, state="disabled", command=self.render)
        self.render_button.pack(side="right")
        ctk.CTkButton(
            buttons, text="Close", width=96, height=32, corner_radius=4,
            font=app.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT, command=self.close,
        ).pack(side="right", padx=(0, 8))

        centre_on(self, app)
        threading.Thread(target=self._load, daemon=True).start()

    # -- worker threads: never touch a widget ------------------------------------
    def _post(self, **payload):
        self.app._queue.put(("scene_preview", dict(payload, dialog=self)))

    def _load(self):
        """Find clips for the stock scenes, then a still for every scene."""
        try:
            for row in self.rows:
                if self.stopping.is_set():
                    return
                if row.is_stock and row.index not in self.picker.options:
                    scenes.find_options(self.search, [row], self.picker,
                                        stop=self.stopping.is_set)
                self._post(event="found", index=row.index)
                self._still(row)
        except Exception as exc:  # noqa: BLE001 - shown in the window, not raised in a thread
            self._post(event="failed", message=str(exc))
        self._post(event="done")

    def _still(self, row):
        """Fetch the still for the row's current clip and hand it to the window."""
        key, path = "", None
        try:
            if row.is_local:
                found, kind = footage.resolve_local(row.visual, self.local_base)
                if kind == "image":
                    path = found
                else:
                    width, height, _length = preview.probe(found)
                    still = preview.poster(found, preview.fit((width, height), (320, 320)), at=0.5)
                    os.makedirs(THUMB_DIR, exist_ok=True)
                    path = os.path.join(THUMB_DIR, f"local_{row.index}.jpg")
                    still.save(path)
            else:
                clip = self.picker.current(row.index)
                if clip is not None:
                    key, path = clip.key, scenes.fetch_thumb(clip, THUMB_DIR)
        except Exception:  # noqa: BLE001 - a missing still is not worth an error
            path = None
        self._post(event="still", index=row.index, key=key, path=path)

    # -- UI thread ---------------------------------------------------------------
    def on_event(self, payload):
        if not self.winfo_exists():
            return
        event, index = payload["event"], payload.get("index")
        if event == "found":
            widgets = self.widgets[index]
            if widgets["button"] is not None:
                total = self.picker.count(index)
                widgets["status"].configure(
                    text=self.picker.label(index) if total else "No clips found for these words",
                    text_color=TEXT_DIM if total else ERR_COLOR)
                widgets["button"].configure(state="normal" if total > 1 else "disabled")
        elif event == "still":
            clip = self.picker.current(index)
            # A slow still for a clip the user has already stepped past is dropped.
            if payload["key"] and (clip is None or clip.key != payload["key"]):
                return
            self._show(index, payload["path"])
        elif event == "failed":
            self.app.append_log(f"⚠️ Scene preview: {payload['message']}")
        elif event == "done":
            self.render_button.configure(state="normal")

    def _show(self, index, path):
        from PIL import Image, ImageOps

        picture = self.widgets[index]["picture"]
        try:
            with Image.open(path) as opened:
                # Twice the box, so it stays sharp on a scaled display.
                fitted = ImageOps.fit(opened.convert("RGB"), (self.box[0] * 2, self.box[1] * 2))
            self.images[index] = ctk.CTkImage(light_image=fitted, dark_image=fitted, size=self.box)
            picture.configure(image=self.images[index], text="")
        except Exception:  # noqa: BLE001 - no path, or not a picture
            self.images.pop(index, None)
            picture.configure(image=None, text="No picture", font=self.app.font_tiny,
                              text_color=TEXT_DIM)

    def try_another(self, index):
        if self.picker.next(index) is None:
            return
        self.widgets[index]["status"].configure(text=self.picker.label(index))
        row = next(r for r in self.rows if r.index == index)
        threading.Thread(target=self._still, args=(row,), daemon=True).start()

    def render(self):
        self.close()
        self.app.start_render()

    def close(self):
        """Keep the choices: Render Video on the Create page uses them too."""
        self.stopping.set()
        self.app.scene_picker = self.picker
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
                bool(self.app.var_auto_cards.get()), self.app.ai_mode())
        threading.Thread(target=self._work, args=args, daemon=True).start()

    def _post(self, **payload):
        self.app._queue.put(("draft", dict(payload, dialog=self)))

    def _work(self, job, text, audience, length, padding, gpu, cards, ai):
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
                                                     ai))
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
        return draft.Audience(self.var_content.get(), self.var_age.get(), self.var_platform.get())

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
                   "the editor, and Preview scenes shows the clips it finds.",
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
        self.scene_picker = None   # clips chosen in the scene preview
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
        self.var_music_track = tk.StringVar(value="")
        self._tracks = {}

        for var in (self.var_api, self.var_output, self.var_aspect,
                    self.var_resolution, self.var_voice, self.var_pixabay,
                    self.var_padding, self.var_music_enabled, self.var_captions,
                    self.var_ask_save, self.var_check_updates, self.var_versions,
                    self.var_writer_gpu, self.var_voice_online, self.var_auto_cards,
                    self.var_ai_for, self.var_ai_look,
                    self.var_target,
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
            ("create", "create", "Create"),
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
        if key != self.current_page:
            self._show_page(key)

        for name, item in self.nav_items.items():
            item.set_selected(name == key)
        if key == "home":
            self.refresh_home()
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

        self.row_aspect = InfoRow(page, self, self.icon("aspect"), "Aspect Ratio")
        self.row_aspect.grid(row=0, column=0, columnspan=2, sticky="ew",
                             padx=(0, PAD // 2), pady=PAD // 2)

        self.row_resolution = InfoRow(page, self, self.icon("resolution"), "Resolution")
        self.row_resolution.grid(row=0, column=2, columnspan=2, sticky="ew",
                                 padx=(PAD // 2, 0), pady=PAD // 2)

        self.row_voice = InfoRow(page, self, self.icon("voice"), "Voice")
        self.row_voice.grid(row=1, column=0, columnspan=2, sticky="ew",
                            padx=(0, PAD // 2), pady=PAD // 2)

        self.row_bitrate = InfoRow(page, self, self.icon("bitrate"), "Bitrate")
        self.row_bitrate.grid(row=1, column=2, columnspan=2, sticky="ew",
                              padx=(PAD // 2, 0), pady=PAD // 2)

        self.row_output = InfoRow(page, self, self.icon("folder"), "Output path")
        self.row_output.grid(row=2, column=0, columnspan=2, sticky="ew",
                             padx=(0, PAD // 2), pady=PAD // 2)

        self.row_render = ActionRow(page, self, self.icon("play"), "Render",
                                    "Idle", self.icon("play"), self.start_render)
        self.row_render.grid(row=2, column=2, columnspan=2, sticky="ew",
                             padx=(PAD // 2, 0), pady=PAD // 2)

        self.tile_scenes = Tile(page, self, self.icon("scenes"), "Scenes", "0")
        self.tile_scenes.grid(row=3, column=0, sticky="ew",
                              padx=(0, PAD // 2), pady=(PAD, PAD // 2))

        self.tile_last = Tile(page, self, self.icon("status"), "Last render", "Never")
        self.tile_last.grid(row=3, column=1, sticky="ew",
                            padx=PAD // 2, pady=(PAD, PAD // 2))

        self.tile_size = Tile(page, self, self.icon("resolution"), "Video",
                              "1080x1920", corner="1080p")
        self.tile_size.grid(row=3, column=2, sticky="ew",
                            padx=PAD // 2, pady=(PAD, PAD // 2))

        self.tile_key = Tile(page, self, self.icon("key"), "Footage key", "Missing")
        self.tile_key.grid(row=3, column=3, sticky="ew",
                           padx=(PAD // 2, 0), pady=(PAD, PAD // 2))
        return page

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
        ctk.CTkOptionMenu(
            voice_bar, values=VOICE_OPTIONS, variable=self.var_voice,
            width=360, height=32, corner_radius=4, font=self.font_body,
            fg_color=FIELD_BG, button_color=FIELD_BG, button_hover_color=CARD_HOVER,
            text_color=TEXT, dropdown_fg_color=CARD_BG, dropdown_text_color=TEXT,
            dropdown_hover_color=CARD_HOVER, dropdown_font=self.font_body,
            dynamic_resizing=False,
        ).pack(side="left")
        self.preview_button = ctk.CTkButton(
            voice_bar, text=f"{self.icon('play')}  Preview Voice", width=150,
            height=32, corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
            hover_color=CARD_HOVER, border_width=1, border_color=FIELD_BORDER,
            text_color=TEXT, command=self.preview_voice,
        )
        self.preview_button.pack(side="left", padx=(8, 0))

        # Captions: the same variables as the Settings section, in compact form.
        caption_bar = ctk.CTkFrame(control_card, fg_color="transparent")
        caption_bar.grid(row=1, column=0, sticky="ew", padx=16, pady=(10, 0))
        ctk.CTkLabel(caption_bar, text=self.icon("captions"), font=self.font_icon,
                     text_color=TEXT_MUTED).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(caption_bar, text="Captions", font=self.font_body,
                     text_color=TEXT).pack(side="left", padx=(0, 12))
        ctk.CTkSwitch(
            caption_bar, text="", width=44, variable=self.var_captions,
            onvalue=True, offvalue=False, progress_color=ACCENT,
        ).pack(side="left", padx=(0, 6))
        self.caption_menus = []
        for key, allowed, _default in CAPTION_SETTINGS:
            menu = ctk.CTkOptionMenu(
                caption_bar, values=list(allowed), variable=self.caption_vars[key],
                width=116, height=32, corner_radius=4, font=self.font_body,
                fg_color=FIELD_BG, button_color=FIELD_BG, button_hover_color=CARD_HOVER,
                text_color=TEXT, text_color_disabled=TEXT_DIM, dropdown_fg_color=CARD_BG,
                dropdown_text_color=TEXT, dropdown_hover_color=CARD_HOVER,
                dropdown_font=self.font_body, dynamic_resizing=False,
            )
            menu.pack(side="left", padx=(6, 0))
            self.caption_menus.append(menu)
        self.var_captions.trace_add("write", self._sync_caption_controls)
        self._sync_caption_controls()

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
            top, text=f"{self.icon('scenes')}   Preview scenes", font=self.font_body,
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
        ctk.CTkOptionMenu(
            voice_row.control, values=VOICE_OPTIONS, variable=self.var_voice,
            width=360, dynamic_resizing=False, height=32, corner_radius=4, font=self.font_body,
            fg_color=FIELD_BG, button_color=FIELD_BG, button_hover_color=CARD_HOVER,
            text_color=TEXT, dropdown_fg_color=CARD_BG, dropdown_text_color=TEXT,
            dropdown_hover_color=CARD_HOVER, dropdown_font=self.font_body,
        ).pack(side="left")

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
        tracks = music_tracks()
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
        chosen = os.path.normpath(chosen)
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

    # -- startup / queue -----------------------------------------------------
    def _post_init(self):
        self.apply_effects()
        self._install_error_hooks()
        self.append_log(f"{APP_NAME} {APP_VERSION} ready.")
        self.append_log(f"⏱️ Window ready in {time.perf_counter() - self._launched:.1f} s")
        self.append_log(f"🪟 Window effect: {self.effect_note}")
        self.append_log(f"🎞️ ffmpeg: {FFMPEG_EXE or 'system / imageio auto-detect'}")
        if not self.var_api.get().strip() and not self.var_pixabay.get().strip():
            self.append_log("⚠️ No footage API key yet - add a Pexels or Pixabay key "
                            "in Settings.")
        self.refresh_home()
        threading.Thread(target=self._warm_moviepy, daemon=True).start()
        if not self._has_footage_key() and not self.welcome_seen:
            self.after(300, lambda: WelcomeDialog(self))
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
        """Only a built copy that the installer put there can replace itself."""
        found = self.update_release
        return bool(IS_FROZEN and found and found.can_install
                    and updates.is_installed_copy(self._app_dir()))

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
                path = updates.download_installer(found, folder, progress, stop.is_set)
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
        command = updates.installer_command(path, self._app_dir())
        try:
            # Detached: the installer must outlive this process, which it is about to replace.
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
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
        shutil.rmtree(THUMB_DIR, ignore_errors=True)   # last session's preview stills
        try:
            # Importing the engine's heavy half pulls in MoviePy, NumPy and PIL.
            import vidgen.audio  # noqa: F401
            import vidgen.motion  # noqa: F401
            self.ui.log("📦 MoviePy loaded.")
        except Exception as exc:  # noqa: BLE001
            self.ui.log(f"⚠️ MoviePy not available: {exc}")

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
                    self.last_render = "Cancelled"
                    self.refresh_home()
                elif kind == "scene_preview":
                    try:
                        payload["dialog"].on_event(payload)
                    except tk.TclError:
                        pass   # the preview window was closed meanwhile
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
                elif kind == "crash":
                    self._unexpected(payload["trace"])
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
                    if payload["ok"] and made:
                        ResultDialog(self, payload["title"], payload["message"], made)
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
        try:
            rows = scenes.plan_preview(script)
        except ScriptError as exc:
            Dialog(self, "Check your script", str(exc), ok=False)
            return
        if not self._has_footage_key() and any(row.is_stock for row in rows):
            WelcomeDialog(self)
            return
        # Reopening keeps the clips already chosen, as long as nothing changed.
        picker = self.scene_picker
        if picker is None or not picker.matches(script, self.frame_label()):
            picker = scenes.ScenePicker(script, self.frame_label())
        width, height, _orientation, _bitrate = resolve_target(
            self.var_aspect.get(), self.var_resolution.get())
        ScenePreviewDialog(self, rows, picker, (width, height))

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
        self._last_draft = result.script.strip()
        self.writer_mode = mode or self.writer_mode
        self.audience = {"audience_content": audience.content, "audience_age": audience.age,
                         "audience_platform": audience.platform}
        self.script_box.delete("1.0", "end")
        self.script_box.insert("1.0", result.script)
        self.var_voice.set(voices.persona(result.voice).label)
        self.var_aspect.set(result.aspect)
        self.var_target.set(result.target)
        self.scene_picker = None
        self._commit_script()
        self.select_page("create")

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

    def _choice_hint(self, page, row, variable, hints):
        """A line under a row of choices that explains the one that is picked."""
        label = ctk.CTkLabel(page, text=hints.get(variable.get(), ""), font=self.font_tiny,
                             text_color=TEXT_MUTED, anchor="w", justify="left", wraplength=860)
        label.grid(row=row, column=0, sticky="w", padx=18, pady=(0, 6))
        variable.trace_add(
            "write", lambda *_args: label.configure(text=hints.get(variable.get(), "")))

    def ai_mode(self) -> str:
        """Which scenes New from text gives an AI picture: none unless AI images are here."""
        if not imagegen.installed():
            return draft.AI_NONE
        return {AI_IMAGES_FOR[0]: draft.AI_UNFILMABLE,
                AI_IMAGES_FOR[1]: draft.AI_ALL}.get(self.var_ai_for.get(), draft.AI_NONE)

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
            self.images_state.configure(text=f"Installed · {imagegen.SIZE / 1e9:.1f} GB",
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
        self._sync_look_bar()
        if not imagegen.installed():
            self.try_status.configure(text="Download AI images first.", text_color=TEXT_DIM)

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

        def worker():
            try:
                os.makedirs(PREVIEW_DIR, exist_ok=True)
                path = os.path.join(PREVIEW_DIR, "ai_try.png")
                seconds = imagegen.make(prompt, size, path, look)
                self._queue.put(("images", {"event": "made", "path": path, "seconds": seconds,
                                            "size": size}))
            except Exception as exc:  # noqa: BLE001 - shown in the tab
                self._queue.put(("images", {"event": "not_made", "message": str(exc)}))

        threading.Thread(target=worker, daemon=True).start()

    def _images_event(self, payload):
        """UI thread: news from the AI images download or the Try it box."""
        event = payload["event"]
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
            self.try_status.configure(
                text=f"Made in {payload['seconds']:.0f} s on this PC.", text_color=OK_COLOR)
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

    # -- render --------------------------------------------------------------
    def start_render(self):
        if self.is_rendering:
            return
        # No key yet: walk the user through getting one instead of failing.
        if not self._has_footage_key() and self._needs_stock_footage():
            WelcomeDialog(self)
            return
        if self.voice_missing():
            self.select_page("log")
            return

        # Like a browser download: ask where this one goes, unless told not to.
        overwrite = False
        if self.var_ask_save.get():
            chosen = self._ask_save_path()
            if not chosen:
                return   # cancelled: no render
            self.var_output.set(chosen)
            # Windows has already asked before replacing an existing file.
            overwrite = True

        # Snapshot every widget value here, on the UI thread.
        settings = self._collect_settings()
        self.settings = settings
        save_settings(settings)
        self._cancel = threading.Event()
        # Clips picked in the scene preview hold only for this script and frame.
        choices = {}
        if self.scene_picker is not None:
            if self.scene_picker.matches(settings["script"], self.frame_label()):
                choices = self.scene_picker.choices()
            else:
                self.scene_picker = None
        cfg = dict(settings, cache_dir=SEARCH_CACHE_DIR, overwrite=overwrite,
                   cancel=self._cancel, clip_choices=choices,
                   target_length=pacing.TARGETS.get(settings["target_length"]))

        self._set_busy(True)
        if choices:
            self.append_log("🎬 Using the clips chosen in the scene preview.")
        for bar in self._progress_bars():
            bar.configure(mode="determinate")
            bar.set(0)
        self.select_page("log")
        threading.Thread(target=render_worker, args=(cfg, self.ui), daemon=True).start()

    # -- shutdown ------------------------------------------------------------
    def _on_close(self):
        save_settings(self._collect_settings())
        self.destroy()


def main():
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                APP_MODEL_ID
            )
        except Exception:
            pass
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
