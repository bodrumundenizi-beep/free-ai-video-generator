"""What every window shares: setup, constants, settings, theme and small widgets. (Split out of ai_video_studio.py; that file still exports every name.)"""

# ---- moved from ai_video_studio.py, unchanged below ----

from __future__ import annotations

import collections
import atexit
import dataclasses
import datetime
import ctypes
import concurrent.futures
import itertools
import json
import math
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
BUNDLE_DIR = sys._MEIPASS if IS_FROZEN else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
    ScriptError, looks_like_plain_text, parse_script,
)
from vidgen import voices  # noqa: E402
from vidgen.render import TEMP_ROOT  # noqa: E402
from vidgen import (  # noqa: E402
    cache, captions, diagnostics, draft, footage, pacing, paths, photos, preview, projects,
    keyvault, scenes, updates, writer,
)
from vidgen import board as boards  # noqa: E402
from vidgen import (  # noqa: E402
    animate, drawn, drive, hooks, imagegen, localvoice, mastering, myvoice, putback, screen,
    started, tiplist, tips, uiread, voicepack, winmap,
)
from vidgen.voice import generate_voiceover  # noqa: E402


APP_NAME = "AI Video Studio"
APP_VERSION = "5.4.0"
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
# Thumbnails made on the Thumbnails page that belong to no video, one folder each.
FREE_THUMBS_DIR = os.path.join(SETTINGS_DIR, "thumbnails")
LOG_FILE = os.path.join(LOG_DIR, "app.log")
# Page keys that are Settings tabs since 4.8: key -> (tab, Advanced view or None).
SETTINGS_PAGES = {"images": "images", "feedback": "help", "create": "advanced"}
BOARD_VIEWS = ("Setup", "Log", "Thumbnail")
# The line at the top of each Settings tab: what it is for.
TAB_INTROS = {
    "footage": "A free Pexels or Pixabay key lets the app download stock video clips. "
               "One is enough.",
    "video": "Where videos are saved, their quality, and the cards before and after each video.",
    "voice": "The voices that read your scripts, and the music and sounds under them.",
    "writer": "The Smart writer turns your text into a script, on this PC.",
    "images": "AI pictures for the scenes stock footage can't show, made on this PC.",
    "app": "How the app looks, and how it stays up to date.",
    "help": "The version, shortcuts, and a way to report a problem.",
    "advanced": "The script behind the Storyboard as plain text, for editing by hand.",
}
SETTINGS_WIDTH = 900   # wide windows: rows stop here instead of stretching   # the Storyboard's tabs; a render shows the Log
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
# What each choice in Settings > AI pictures means, shown under the row when it is picked.
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
        "started": {},             # Home's Get started list (vidgen.started)
        "whats_new_seen": "",      # the last version whose What's new was shown
        "whats_new_off": False,    # "Don't show again" was ticked
        "old_writer_asked": False,  # asked once whether to delete 5.2's Smart writer
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
        "sound_effects": True,     # a pop as a card appears
        "smart_motion": True,      # cuts on the music's beats, framing toward the detail
        "animate": "Off",          # which AI pictures become moving clips (vidgen.animate)
        "web_facts": True,         # fact ideas are looked up on Wikipedia first (webfacts)
        "check_clips": True,       # stock clips looked at before the video (vidgen.clipcheck)
        "style": "Normal",         # Normal footage, or drawn on screen (vidgen.drawn)
        "quick_sync": False,       # encode on an Intel graphics chip when it works (render)
        "ai_images_speed": "",     # Fast / Balanced / Best; "" = chosen for this PC
        "ai_threads": "Auto",      # processor threads for AI pictures, or a number
        "ai_threads_best": "",     # "threads:affinity" the Test speed button found fastest
        "ai_fast_seconds": 0.0,    # a measured picture's time, scaled to the Fast setting
        "ai_gpu": "",              # "dedicated", "integrated" or "none", once detected
        "gpu_looked": False,       # graphics for the Smart writer looked for (once per PC)
        "key_tip": True,           # after a keyless video: mention that a free key exists
        "music_path": AUTO_TRACK,
        "output_path": os.path.join(default_output_dir(), "final_video.mp4"),
        "aspect": "9:16",
        "resolution": "1080p",
        "voice": voices.DEFAULT_ID,
        "voice_speed": voices.DEFAULT_SPEED,
        "footage_order": "Both",   # Pexels and Pixabay searched together (footage.ORDERS)
        "captions": True,
        "caption_style": captions.DEFAULT_STYLE,
        "caption_size": captions.DEFAULT_SIZE,
        "caption_color": captions.DEFAULT_COLOR,
        "caption_position": captions.DEFAULT_POSITION,
        "theme": "dark",
        "translucent": True,
        "window": "zoomed",        # maximized, or the size it was closed at ("1180x780")
        "features_hidden": False,  # Home's "What this app can do" folded to one line
        "version_since": {},       # {"version", "at"}: when this version first started
        "script": DEFAULT_SCRIPT,
    }


# The API keys: kept in Windows Credential Manager, never written to settings.json.
SECRET_SETTINGS = ("api_key", "pixabay_key")


def load_settings() -> dict:
    """Read settings.json, falling back to defaults for anything missing. The API keys come
    from Windows Credential Manager; a key still in settings.json (5.2 and before) is moved
    there, and the file is written again without it."""
    data = default_settings()
    moved = False
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
            # Settings from before 5.1: an update keeps its own graphics choice (only a new
            # install looks for graphics), and checks clips only where that choice is fast.
            if "gpu_looked" not in stored:
                data["gpu_looked"] = True
                data["check_clips"] = bool(data["writer_gpu"])
    except (OSError, ValueError):
        pass
    for name in SECRET_SETTINGS:
        plain = str(data.get(name) or "").strip()
        stored = keyvault.get(name)
        if plain and keyvault.put(name, plain):
            moved = True          # now in Credential Manager: settings.json loses it below
        elif stored:
            data[name] = stored

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
    data["started"] = started.state(data["started"])
    if not re.fullmatch(r"zoomed|\d{3,5}x\d{3,5}", str(data["window"])):
        data["window"] = "zoomed"
    data["features_hidden"] = bool(data["features_hidden"])
    if not isinstance(data["version_since"], dict):
        data["version_since"] = {}
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
                "whats_new_off", "quick_sync", "check_clips", "gpu_looked", "old_writer_asked"):
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
    if data["footage_order"] not in footage.ORDERS:
        data["footage_order"] = "Both"
    if data["voice_speed"] not in voices.SPEEDS:
        data["voice_speed"] = voices.DEFAULT_SPEED
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
    if moved:
        save_settings(data)
    return data


def save_settings(data: dict) -> None:
    """Write settings.json. The API keys go to Windows Credential Manager instead; only if
    it can't take one does that key stay in the file, so a key is never lost."""
    data = dict(data)
    for name in SECRET_SETTINGS:
        if name in data and keyvault.put(name, str(data[name] or "").strip()):
            data[name] = ""
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
TEXT_MUTED = ("#5A5A5A", "#B4B4B4")
TEXT_DIM = ("#6B6B6B", "#A3A3A3")   # 4.7:1 or more
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
WIDE = 1400      # window width (at 100% scaling) from which the side panels show
SIDE = 380       # their width
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
    "add": ("\ue710", "+"),
    "edit": ("\ue70f", "\u270e"),
    "chart": ("\ue9d2", "\u25a5"),
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


TOUR = (
    ("home", "Paste your text",
     "On Home, press Make new video and put any text in the box: an article, a tip, your notes. "
     "Press Make video and the app writes the script, finds the pictures, reads it out and adds "
     "captions."),
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
    "Tutorials: show how you do something in any app, and it becomes a sleek tutorial with "
    "zooms, a smooth cursor, labels, steps, a voice and music. New > Tutorial.",
    "Do it for me: write the steps (or let the Smart writer suggest them) and the app clicks "
    "them itself, asking first. It only clicks what it is sure of; the rest is yours.",
    "Your steps guide you while you record, ticking off as you click. 16:9 or 9:16 for Shorts.",
)


RECORDINGS_DIR = os.path.join(os.path.dirname(MUSIC_DIR), "Recordings")
# When this copy was built (the exe's date), or "from source": shown where a test build matters.
BUILD = (time.strftime("build %d %b %H:%M", time.localtime(os.path.getmtime(sys.executable)))
         if getattr(sys, "frozen", False) else "from source")


def report_tip_failure(app, message):
    """The error window a failed render shows, with Report this, for a tip that couldn't record.

    The report gets ffmpeg's full reasons too (screen.LAST_FAILURE): issue #7 came in as a
    screenshot without them (2026-10-28).
    """
    detail = f"{message}\n\nScreen recording: {screen.LAST_FAILURE}" if screen.LAST_FAILURE \
        else message
    Dialog(app, "Show it on my PC couldn't record", message, ok=False,
           on_report=lambda: app.open_report(detail))


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


CLOSING_WAIT = 3000   # ms the closing step is recorded before it finishes by itself
HOLD_WAIT = 2.5       # s a step the app shows by itself stays on screen before the next


def show_whats_new(seen: str, off: bool, first_run: bool) -> bool:
    """Once per version, after an update only, unless switched off."""
    return not (off or first_run) and seen != APP_VERSION


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


WRITER_MODES = ("Smart writer", "Quick split")


def writer_size() -> str:
    """What the Smart writer's download still has to fetch, for the buttons."""
    size = sum(m.size for m in writer.missing()) or writer.current().size
    return f"{size / 1e9:.1f} GB"


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
