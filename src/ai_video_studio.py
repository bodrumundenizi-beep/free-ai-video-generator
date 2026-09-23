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

import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
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
from vidgen.render import UiBridge, render_worker, sweep_old_work_dirs  # noqa: E402
from vidgen.script import count_scenes  # noqa: E402


APP_NAME = "AI Video Studio"
APP_VERSION = "3.1.0"
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


def _known_folder(csidl: int, fallback: str) -> str:
    """A shell folder, honouring a OneDrive redirect, with a home-dir fallback.

    SHGetFolderPathW is superseded by SHGetKnownFolderPath, but it is still
    present on Windows 11, it follows folder redirection, and it needs no GUID
    struct - which makes it the cheapest correct option here.
    """
    if sys.platform == "win32":
        try:
            buffer = ctypes.create_unicode_buffer(260)
            # SHGFP_TYPE_CURRENT = 0
            if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buffer) == 0:
                if buffer.value and os.path.isdir(buffer.value):
                    return buffer.value
        except Exception:
            pass
    guess = os.path.join(os.path.expanduser("~"), fallback)
    return guess if os.path.isdir(guess) else os.path.expanduser("~")


def default_output_dir() -> str:
    """The user's Videos folder."""
    return _known_folder(14, "Videos")  # CSIDL_MYVIDEO


# Tracks dropped in here appear in Settings > Background music. Documents rather
# than %APPDATA%, because people need to be able to find it.
MUSIC_DIR = os.path.join(_known_folder(5, "Documents"), "AI Video Studio", "Music")
MUSIC_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")
SEARCH_CACHE_DIR = os.path.join(SETTINGS_DIR, "cache", "search")
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

VOICE_OPTIONS = [
    "Neural Male",
    "Neural Female",
    "Happy/Upbeat (Female)",
    "Deep/Narrator (Male)",
]

def default_settings() -> dict:
    return {
        "api_key": "",
        "pixabay_key": "",
        "padding": DEFAULT_PADDING,
        "music_enabled": False,
        "music_path": "",
        "output_path": os.path.join(default_output_dir(), "final_video.mp4"),
        "aspect": "9:16",
        "resolution": "1080p",
        "voice": "Neural Male",
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
    if data["music_path"] and not os.path.isfile(str(data["music_path"])):
        data["music_path"] = ""

    if data["aspect"] not in RATIO_OPTIONS:
        data["aspect"] = "9:16"
    if data["resolution"] not in RESOLUTION_OPTIONS:
        data["resolution"] = "1080p"
    if data["voice"] not in VOICE_OPTIONS:
        data["voice"] = "Neural Male"
    if data["theme"] not in ("dark", "light"):
        data["theme"] = "dark"

    # Repair a path saved by an older build, or one that pointed at a folder the
    # installed app cannot write to.
    out_dir = os.path.dirname(str(data.get("output_path") or ""))
    if not out_dir or not os.path.isdir(out_dir) or not os.access(out_dir, os.W_OK):
        data["output_path"] = os.path.join(default_output_dir(), "final_video.mp4")
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

# The segmented control shares one text colour across all its segments, so the
# selected fill has to stay readable with the theme's normal text colour.
SEGMENT_SELECTED = ("#CCE4F7", "#0F6CBD")
SEGMENT_SELECTED_HOVER = ("#BBD9F2", "#1B7CD6")

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


class Card(ctk.CTkFrame):
    """Flat rounded surface, the building block of every page."""

    def __init__(self, master, **kwargs):
        kwargs.setdefault("corner_radius", 5)
        kwargs.setdefault("fg_color", CARD_BG)
        kwargs.setdefault("border_width", 0)
        super().__init__(master, **kwargs)


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
        self.selected = value
        self.configure(fg_color=NAV_SELECTED if value else "transparent")
        self.bar.configure(fg_color=ACCENT if value else "transparent")

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

    def __init__(self, app, title, message, ok=True):
        super().__init__(app)
        self.title(title)
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)

        wrapper = Card(self)
        wrapper.pack(fill="both", expand=True, padx=14, pady=14)

        header = ctk.CTkFrame(wrapper, fg_color="transparent")
        header.pack(fill="x", padx=18, pady=(16, 4))
        ctk.CTkLabel(header, text=app.icon("status" if ok else "clear"),
                     font=app.font_icon_lg,
                     text_color=OK_COLOR if ok else ERR_COLOR).pack(side="left",
                                                                    padx=(0, 10))
        ctk.CTkLabel(header, text=title, font=app.font_title,
                     text_color=TEXT).pack(side="left")

        ctk.CTkLabel(wrapper, text=message, font=app.font_body,
                     text_color=TEXT_MUTED, justify="left", wraplength=420,
                     anchor="w").pack(fill="x", padx=18, pady=(2, 16))

        ctk.CTkButton(wrapper, text="OK", width=96, height=32, corner_radius=4,
                      font=app.font_body, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      text_color=ACCENT_TEXT,
                      command=self.destroy).pack(anchor="e", padx=18, pady=(0, 16))

        self.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_width()) // 2
        y = app.winfo_rooty() + (app.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        try:
            self.grab_set()
        except Exception:
            pass


# =============================================================================
#  SECTION 7 - APPLICATION SHELL
# =============================================================================


class App(ctk.CTk):
    def __init__(self):
        super().__init__()

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
        self.last_render = "Never"
        self.sidebar_collapsed = False
        self.effect_note = ""
        self._refresh_job = None

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
        self.var_voice = tk.StringVar(value=s["voice"])
        self.var_theme = tk.StringVar(value=s["theme"])
        self.var_translucent = tk.BooleanVar(value=bool(s["translucent"]))
        self.var_pixabay = tk.StringVar(value=s["pixabay_key"])
        padding_label = {v: k for k, v in PADDING_OPTIONS.items()}[s["padding"]]
        self.var_padding = tk.StringVar(value=padding_label)
        self.var_music_enabled = tk.BooleanVar(value=s["music_enabled"])
        self.music_path = s["music_path"]
        self.var_music_track = tk.StringVar(value="")
        self._tracks = {}

        for var in (self.var_api, self.var_output, self.var_aspect,
                    self.var_resolution, self.var_voice, self.var_pixabay,
                    self.var_padding, self.var_music_enabled):
            var.trace_add("write", self._on_setting_changed)

    def _collect_settings(self) -> dict:
        return {
            "api_key": self.var_api.get().strip(),
            "pixabay_key": self.var_pixabay.get().strip(),
            "padding": PADDING_OPTIONS.get(self.var_padding.get(), DEFAULT_PADDING),
            "music_enabled": bool(self.var_music_enabled.get()),
            "music_path": self.music_path,
            "output_path": self.var_output.get().strip(),
            "aspect": self.var_aspect.get(),
            "resolution": self.var_resolution.get(),
            "voice": self.var_voice.get(),
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
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkFrame(self, width=SIDEBAR_WIDTH, corner_radius=0,
                                    fg_color=SIDEBAR_BG)
        self.sidebar.grid(row=0, column=0, sticky="nsw")
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

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew")
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
        for name, page in self.pages.items():
            if name == key:
                page.grid(row=0, column=0, sticky="nsew",
                          padx=(EDGE, EDGE), pady=(14, 14))
            else:
                page.grid_remove()
        for name, item in self.nav_items.items():
            item.set_selected(name == key)
        if key == "home":
            self.refresh_home()
        elif key == "settings":
            self._refresh_tracks()

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

        self.tile_key = Tile(page, self, self.icon("key"), "Pexels key", "Missing")
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
        self.row_voice.set_value(self.var_voice.get())
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
        has_key = bool(self.var_api.get().strip())
        self.tile_key.set_value("Set" if has_key else "Missing",
                                OK_COLOR if has_key else ERR_COLOR)

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

        top = ctk.CTkFrame(control_card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
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
        self.render_button.grid(row=0, column=1, sticky="e")

        self.progress = ctk.CTkProgressBar(control_card, height=4, corner_radius=2,
                                           progress_color=ACCENT, fg_color=FIELD_BG)
        self.progress.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 16))
        self.progress.set(0)
        self.progress.grid_remove()
        return page

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
                      fg_color=FIELD_BG, hover_color=CARD_HOVER, text_color=TEXT,
                      command=self.clear_log).pack(side="right")

        self.log_box = ctk.CTkTextbox(
            card, font=self.font_body, corner_radius=4, fg_color=FIELD_BG,
            text_color=LOG_BODY, border_width=0, wrap="word",
        )
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        self._configure_log_tags()

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
        ).pack(anchor="w", padx=18, pady=(0, 16))

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
                      fg_color=FIELD_BG, hover_color=CARD_HOVER, text_color=TEXT,
                      command=self.open_output_folder).pack(side="left", padx=(0, 8))
        ctk.CTkButton(bar, text=f"{self.icon('settings')}  Open settings folder",
                      height=32, corner_radius=4, font=self.font_body,
                      fg_color=FIELD_BG, hover_color=CARD_HOVER, text_color=TEXT,
                      command=self.open_settings_folder).pack(side="left")
        return page

    def open_output_folder(self):
        target = os.path.dirname(self.var_output.get().strip()) or default_output_dir()
        self._open_folder(target)

    def open_settings_folder(self):
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        self._open_folder(SETTINGS_DIR)

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

        self._caption(page, "STOCK FOOTAGE", 0, pady=(0, 6))
        self.api_entry = self._key_row(
            page, 1, "Pexels API key",
            "Stored locally in settings.json - never in the code",
            self.var_api, "Paste your Pexels API key",
        )
        self.pixabay_entry = self._key_row(
            page, 2, "Pixabay API key",
            "Optional - used when Pexels finds nothing",
            self.var_pixabay, "Paste your Pixabay API key",
        )

        self._caption(page, "OUTPUT", 3)

        path_row = SettingRow(page, self, self.icon("folder"), "Save video to",
                              "Full path of the rendered .mp4")
        path_row.grid(row=4, column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkEntry(
            path_row.control, textvariable=self.var_output, width=300, height=32,
            corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
            border_color=FIELD_BORDER, border_width=1, text_color=TEXT,
        ).pack(side="left")
        ctk.CTkButton(
            path_row.control, text="Browse", width=84, height=32, corner_radius=4,
            font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            text_color=TEXT, command=self.choose_save_location,
        ).pack(side="left", padx=(6, 0))

        self._caption(page, "VIDEO", 5)

        ratio_row = SettingRow(page, self, self.icon("aspect"), "Aspect ratio",
                               "Portrait for Shorts / TikTok, landscape for YouTube")
        ratio_row.grid(row=6, column=0, sticky="ew", pady=PAD // 2)
        self._segmented(ratio_row.control, RATIO_OPTIONS, self.var_aspect, 170)

        res_row = SettingRow(page, self, self.icon("resolution"), "Resolution",
                             "1080p renders at 8000k, 720p at 5000k")
        res_row.grid(row=7, column=0, sticky="ew", pady=PAD // 2)
        self._segmented(res_row.control, RESOLUTION_OPTIONS, self.var_resolution, 170)

        padding_row = SettingRow(page, self, self.icon("timer"), "Scene padding",
                                 "Pause after each voice line. A script's Padding: overrides it")
        padding_row.grid(row=8, column=0, sticky="ew", pady=PAD // 2)
        self._segmented(padding_row.control, list(PADDING_OPTIONS), self.var_padding, 260)

        voice_row = SettingRow(page, self, self.icon("voice"), "Voice engine",
                               "Microsoft Edge neural text-to-speech")
        voice_row.grid(row=9, column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkOptionMenu(
            voice_row.control, values=VOICE_OPTIONS, variable=self.var_voice,
            width=210, height=32, corner_radius=4, font=self.font_body,
            fg_color=FIELD_BG, button_color=FIELD_BG, button_hover_color=CARD_HOVER,
            text_color=TEXT, dropdown_fg_color=CARD_BG, dropdown_text_color=TEXT,
            dropdown_hover_color=CARD_HOVER, dropdown_font=self.font_body,
        ).pack(side="left")

        self._caption(page, "AUDIO", 10)

        music_row = SettingRow(page, self, self.icon("music"), "Background music",
                               "Plays under the video and ducks while the voice speaks")
        music_row.grid(row=11, column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkSwitch(
            music_row.control, text="", width=44, variable=self.var_music_enabled,
            onvalue=True, offvalue=False, progress_color=ACCENT,
        ).pack(side="left")

        track_row = SettingRow(page, self, self.icon("folder"), "Track",
                               "Drop tracks in the Music folder, or browse for any file")
        track_row.grid(row=12, column=0, sticky="ew", pady=PAD // 2)
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
                text_color=TEXT, command=command,
            ).pack(side="left", padx=(6, 0))
        self._refresh_tracks()

        self._caption(page, "APPEARANCE", 13)

        theme_row = SettingRow(page, self, self.icon("theme"), "Dark theme",
                               "Switches the whole app between dark and light")
        theme_row.grid(row=14, column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkSwitch(
            theme_row.control, text="", width=44, variable=self.var_theme,
            onvalue="dark", offvalue="light", progress_color=ACCENT,
            command=self.apply_theme,
        ).pack(side="left")

        glass_row = SettingRow(page, self, self.icon("glass"), "Window translucency",
                               "Mica / acrylic where supported, alpha elsewhere")
        glass_row.grid(row=15, column=0, sticky="ew", pady=(PAD // 2, PAD))
        ctk.CTkSwitch(
            glass_row.control, text="", width=44, variable=self.var_translucent,
            onvalue=True, offvalue=False, progress_color=ACCENT,
            command=self.apply_effects,
        ).pack(side="left")
        return page

    def _key_row(self, page, row, title, subtitle, variable, placeholder):
        """A masked API-key entry with a reveal button. Returns the entry."""
        key_row = SettingRow(page, self, self.icon("key"), title, subtitle)
        key_row.grid(row=row, column=0, sticky="ew", pady=PAD // 2)
        entry = ctk.CTkEntry(
            key_row.control, textvariable=variable, width=320, height=32,
            corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
            border_color=FIELD_BORDER, border_width=1, text_color=TEXT,
            placeholder_text=placeholder, show="•",
        )
        entry.pack(side="left")
        ctk.CTkButton(
            key_row.control, text=self.icon("eye"), font=self.font_icon, width=34,
            height=32, corner_radius=4, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            text_color=TEXT_MUTED, command=lambda: self.toggle_key_visibility(entry),
        ).pack(side="left", padx=(6, 0))
        return entry

    def _segmented(self, parent, values, variable, width):
        ctk.CTkSegmentedButton(
            parent, values=values, variable=variable,
            font=self.font_body, width=width, height=32, corner_radius=4,
            selected_color=SEGMENT_SELECTED,
            selected_hover_color=SEGMENT_SELECTED_HOVER,
            unselected_color=FIELD_BG, unselected_hover_color=CARD_HOVER,
            fg_color=FIELD_BG, text_color=TEXT, dynamic_resizing=False,
        ).pack(side="left")

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
        os.makedirs(MUSIC_DIR, exist_ok=True)
        self._open_folder(MUSIC_DIR)

    def choose_save_location(self):
        current = self.var_output.get().strip()
        chosen = filedialog.asksaveasfilename(
            defaultextension=".mp4",
            filetypes=[("MP4 Video", "*.mp4"), ("All Files", "*.*")],
            initialdir=os.path.dirname(current) or default_output_dir(),
            initialfile=os.path.basename(current) or "final_video.mp4",
        )
        if chosen:
            self.var_output.set(chosen)

    def apply_theme(self):
        ctk.set_appearance_mode(self.var_theme.get())
        self._configure_log_tags()   # text tags hold single colours, not pairs
        self._on_setting_changed()
        self.apply_effects()

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
        self.append_log(f"{APP_NAME} {APP_VERSION} ready.")
        self.append_log(f"🪟 Window effect: {self.effect_note}")
        self.append_log(f"🎞️ ffmpeg: {FFMPEG_EXE or 'system / imageio auto-detect'}")
        if not self.var_api.get().strip() and not self.var_pixabay.get().strip():
            self.append_log("⚠️ No footage API key yet - add a Pexels or Pixabay key "
                            "in Settings.")
        self.refresh_home()
        threading.Thread(target=self._warm_moviepy, daemon=True).start()

    def _warm_moviepy(self):
        sweep_old_work_dirs()
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
                    self.progress.configure(mode="determinate")
                    self.progress.set(payload["value"])
                elif kind == "spinner":
                    if payload["on"]:
                        self.progress.configure(mode="indeterminate")
                        self.progress.start()
                    else:
                        self.progress.stop()
                        self.progress.configure(mode="determinate")
                elif kind == "busy":
                    self._set_busy(payload["on"])
                elif kind == "finished":
                    self.last_render = "Success" if payload["ok"] else "Failed"
                    self.refresh_home()
                    Dialog(self, payload["title"], payload["message"],
                           ok=payload["ok"])
        except queue.Empty:
            pass
        finally:
            self.after(60, self._drain_queue)

    def _set_busy(self, busy: bool):
        self.is_rendering = busy
        self.render_button.configure(
            state="disabled" if busy else "normal",
            text=f"{self.icon('play')}   {'Rendering...' if busy else 'Render Video'}",
        )
        self.row_render.button.configure(state="disabled" if busy else "normal")
        if busy:
            self.progress.grid()
        else:
            self.progress.grid_remove()
            self.status_label.configure(text="Ready")
        self.refresh_home()

    # -- render --------------------------------------------------------------
    def start_render(self):
        if self.is_rendering:
            return
        # Snapshot every widget value here, on the UI thread.
        settings = self._collect_settings()
        self.settings = settings
        save_settings(settings)
        cfg = dict(settings, cache_dir=SEARCH_CACHE_DIR)

        self._set_busy(True)
        self.progress.configure(mode="determinate")
        self.progress.set(0)
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
