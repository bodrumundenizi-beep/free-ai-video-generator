"""The script format.

A scene starts at a ``Visual:`` line and runs until the next one. The simple
format is just Visual/Voice pairs, and still parses exactly as it always has:

    Visual: hard drive
    Voice: Your PC is hoarding gigabytes of junk.

Any scene can add optional keys, in any order after its ``Visual:``:

    Visual: local:C:/Users/me/recordings/taskmgr.mp4
    Voice: Here is the fix. Open Task Manager.
    Duration: 4s        auto (default) or a number of seconds
    Padding: 0.5s       silence after the voice; default comes from Settings
    Zoom: in            in, out, none, or auto (default: a subtle zoom)

Keys are case-insensitive. A scene needs a ``Voice:``, or a numeric
``Duration:`` to be a silent shot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

KEYS = ("visual", "voice", "duration", "padding", "zoom")
ZOOM_MODES = ("auto", "in", "out", "none")
_ZOOM_ALIASES = {"off": "none", "no": "none", "default": "auto"}

MIN_DURATION = 0.5
MAX_DURATION = 120.0
MAX_PADDING = 10.0

LOCAL_PREFIX = "local:"

_LINE = re.compile(r"^([A-Za-z]+)\s*:(.*)$")
_SECONDS = re.compile(r"^(\d+(?:\.\d+)?|\.\d+)\s*(?:s|sec|secs|seconds?)?$", re.IGNORECASE)


class ScriptError(Exception):
    """A script the parser cannot accept. The message names the line."""


@dataclass
class Scene:
    visual: str
    voice: str | None = None
    duration: float | None = None  # None means auto: fit the voice
    padding: float | None = None  # None means the Settings default
    zoom: str = "auto"
    line: int = 0  # 1-based line of the scene's Visual:

    @property
    def is_local(self) -> bool:
        return self.visual.lower().startswith(LOCAL_PREFIX)

    @property
    def local_path(self) -> str | None:
        """The path after ``local:``, with surrounding quotes removed."""
        if not self.is_local:
            return None
        raw = self.visual[len(LOCAL_PREFIX):].strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            raw = raw[1:-1].strip()
        return raw


def _seconds(text, line_no, key, *, allow_auto, low, high, warnings):
    raw = text.strip()
    if allow_auto and raw.lower() == "auto":
        return None
    match = _SECONDS.match(raw)
    if not match:
        expected = (
            "'auto' or a number of seconds like 4.5s"
            if allow_auto
            else "a number of seconds like 0.5s"
        )
        raise ScriptError(f"Error on Line {line_no}: {key}: expects {expected}, got '{raw}'")
    value = float(match.group(1))
    clamped = min(max(value, low), high)
    if clamped != value and warnings is not None:
        warnings.append(
            f"Line {line_no}: {key}: {raw} is outside {low:g}-{high:g}s, using {clamped:g}s."
        )
    return clamped


def _zoom(text, line_no):
    value = text.strip().lower()
    value = _ZOOM_ALIASES.get(value, value)
    if value not in ZOOM_MODES:
        raise ScriptError(
            f"Error on Line {line_no}: Zoom: expects in, out, none or auto, got '{text.strip()}'"
        )
    return value


def parse_script(script_text, warnings=None):
    """Parse a script into a list of Scene objects.

    Raises ScriptError with a line number on anything malformed. If
    ``warnings`` is a list, non-fatal problems are appended to it - such as a
    ``Visual:`` with nothing to say, which older versions dropped silently.
    """
    scenes: list[Scene] = []
    current: Scene | None = None
    seen: set[str] = set()

    def finish():
        nonlocal current
        if current is None:
            return
        if current.voice is None and current.duration is None:
            if warnings is not None:
                warnings.append(
                    f"The scene at line {current.line} has no Voice: or Duration:, "
                    "so it was skipped."
                )
        else:
            scenes.append(current)
        current = None

    for line_no, raw in enumerate(script_text.split("\n"), start=1):
        line = raw.strip()
        if not line:
            continue

        match = _LINE.match(line)
        key = match.group(1).lower() if match else None
        if key not in KEYS:
            raise ScriptError(
                f"Error on Line {line_no}: Line must begin with 'Visual:' or 'Voice:' "
                "(or Duration:, Padding:, Zoom:)"
            )
        value = match.group(2).strip()
        label = key.capitalize()

        if key == "visual":
            finish()
            if not value:
                raise ScriptError(
                    f"Error on Line {line_no}: Visual: needs search words or local:<path>"
                )
            current = Scene(visual=value, line=line_no)
            seen = {"visual"}
            continue

        if current is None:
            raise ScriptError(f"Error near Line {line_no}: Missing 'Visual:' before '{label}:'")
        if key in seen:
            raise ScriptError(
                f"Error on Line {line_no}: the scene starting at line {current.line} "
                f"already has a {label}: line"
            )
        seen.add(key)

        if key == "voice":
            if not value:
                raise ScriptError(f"Error on Line {line_no}: Voice: is empty")
            current.voice = value
        elif key == "duration":
            current.duration = _seconds(
                value, line_no, "Duration", allow_auto=True,
                low=MIN_DURATION, high=MAX_DURATION, warnings=warnings,
            )
        elif key == "padding":
            current.padding = _seconds(
                value, line_no, "Padding", allow_auto=False,
                low=0.0, high=MAX_PADDING, warnings=warnings,
            )
        else:
            current.zoom = _zoom(value, line_no)

    finish()
    if not scenes:
        raise ScriptError("No valid scenes found.")
    return scenes


def count_scenes(script_text):
    """Non-raising variant used for the live 'Scenes parsed' card."""
    try:
        return len(parse_script(script_text))
    except Exception:
        return None
