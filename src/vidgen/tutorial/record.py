"""Recording a tutorial (5.4): the user's own steps in any window, without the cursor, and a log
of what they did, so the tutorial can draw its own cursor, zooms and labels afterwards.

    with Session(hwnd, folder) as rec:       # starts recording at once
        ...                                  # the user works; rec.stopped is set by the hotkey
    rec.log  ->  the log, also saved as folder/log.json next to folder/recording.mp4

What is recorded: the window's area plus a margin (menus and pop-ups open outside the window),
cut to the monitor it is on, in real pixels (screen scaling such as 125% is undone), with the
cursor left out. What is logged, 60 times a second: the mouse position, presses and releases
(a press that moves is a drag), shortcuts (Ctrl/Alt/Win + a key, Enter, Esc, Tab, Delete,
F-keys) and when the user was typing. **Typed letters are never kept**, only that typing
happened: a password must never end up in a log. At each press, Windows' UI Automation names
the control under the mouse (a small PowerShell helper that waits for points, so nothing
heavy runs while recording). Names with an email in them are dropped. Where Windows has no
name, crops() cuts a picture round the click afterwards, for name_crops() (Qwen3-VL).
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time

from .. import screen, uiread
from ..mastering import run_ffmpeg

POLL = 1 / 60            # seconds between two looks at the mouse and keys
MARGIN = 0.12            # share of the window's size recorded round it, for menus
DRAG = 8                 # pixels a press must move to be a drag
SETTLE = 0.12            # seconds the mouse rests before Windows is asked what is under it
NEAR = 6                 # pixels between that resting point and a press, to use its answer
STOP_KEYS = (0x11, 0x10, 0x78)   # Ctrl + Shift + F9 stops the recording
STOP_NAME = "Ctrl + Shift + F9"
APP_KEYS = {"Ctrl + Shift + Y", "Ctrl + Shift + N", "Ctrl + Shift + F9"}   # the app's own keys:
                                                   # never shown as the tutorial's shortcuts

_MODS = {0x11: "Ctrl", 0x12: "Alt", 0x10: "Shift", 0x5B: "Win", 0x5C: "Win"}
_NAMED = {0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x1B: "Esc", 0x20: "Space",
          0x2E: "Delete", 0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down",
          0x21: "Page Up", 0x22: "Page Down", 0x23: "End", 0x24: "Home"}
_NAMED.update({0x70 + i: f"F{i + 1}" for i in range(12)})
_LETTERS = {code: chr(code) for code in list(range(0x30, 0x3A)) + list(range(0x41, 0x5B))}
_SHOWN_ALONE = {"Enter", "Esc", "Tab", "Delete"} | {f"F{i}" for i in range(1, 13)}

# Waits for "x y" lines and answers each with the control at that point, as one JSON line.
# An unnamed part (an icon inside a button) takes the nearest named parent's name.
_NAMER = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$A = [System.Windows.Automation.AutomationElement]
$walk = [System.Windows.Automation.TreeWalker]::ControlViewWalker
# Classic apps (Notepad++, VLC) give the .NET reader only empty panes: then MSAA, the older
# accessibility their menus and toolbars speak, names the point (same as uiread's fallback).
$msaa = $false
try {
    Add-Type -ReferencedAssemblies Accessibility -Language CSharp -TypeDefinition @"
using System; using System.Runtime.InteropServices; using Accessibility;
public static class AivsPoint {
  [StructLayout(LayoutKind.Sequential)] public struct PT { public int X; public int Y; }
  [DllImport("oleacc.dll")] static extern int AccessibleObjectFromPoint(PT p, [MarshalAs(UnmanagedType.Interface)] out IAccessible acc, [MarshalAs(UnmanagedType.Struct)] out object child);
  [DllImport("user32.dll")] static extern IntPtr SetThreadDpiAwarenessContext(IntPtr ctx);
  public static string At(int x, int y) {
    SetThreadDpiAwarenessContext(new IntPtr(-4));
    IAccessible acc; object child; PT p; p.X = x; p.Y = y;
    if (AccessibleObjectFromPoint(p, out acc, out child) != 0 || acc == null) return "";
    try {
      int role = Convert.ToInt32(acc.get_accRole(child));
      string name = acc.get_accName(child) ?? "";
      int l, t, w, h; acc.accLocation(out l, out t, out w, out h, child);
      string parent = "";
      try { var up = acc.accParent as IAccessible; if (up != null) parent = up.get_accName(0) ?? ""; } catch { }
      return role + "	" + name.Replace("	", " ") + "	" + parent.Replace("	", " ") + "	" + l + "	" + t + "	" + w + "	" + h;
    } catch { return ""; }
  }
}
"@
    $msaa = $true
} catch { }
$roles = @{ 43 = 'Button'; 12 = 'MenuItem'; 37 = 'TabItem'; 44 = 'CheckBox'; 45 = 'RadioButton';
            46 = 'ComboBox'; 34 = 'ListItem'; 36 = 'TreeItem'; 30 = 'Hyperlink'; 62 = 'SplitButton';
            56 = 'SplitButton'; 57 = 'MenuItem'; 42 = 'Edit'; 41 = 'Text'; 40 = 'Image'; 51 = 'Slider' }
while ($null -ne ($line = [Console]::In.ReadLine())) {
    try {
        $p = $line.Split(' ')
        $e = $A::FromPoint((New-Object System.Windows.Point([double]$p[0], [double]$p[1])))
        $n = 0
        while ($e -ne $null -and [string]::IsNullOrWhiteSpace($e.Current.Name) -and $n -lt 3) {
            $e = $walk.GetParent($e); $n++
        }
        $c = $e.Current; $r = $c.BoundingRectangle
        $kind = $c.ControlType.ProgrammaticName -replace '^ControlType\.', ''
        $up = $walk.GetParent($e); $parent = ''
        if ($up -ne $null) { $parent = $up.Current.Name }
        $o = [ordered]@{ name = $c.Name; kind = $kind; parent = $parent
                         x = [int]$r.X; y = [int]$r.Y; w = [int]$r.Width; h = [int]$r.Height }
        if ($msaa -and ([string]::IsNullOrWhiteSpace($c.Name) -or $kind -eq 'Pane' -or $kind -eq 'Window')) {
            $f = [AivsPoint]::At([int]$p[0], [int]$p[1]).Split("`t")
            if ($f.Count -ge 7 -and $roles.ContainsKey([int]$f[0]) -and $f[1].Trim()) {
                $o = [ordered]@{ name = $f[1]; kind = $roles[[int]$f[0]]; parent = $f[2]
                                 x = [int]$f[3]; y = [int]$f[4]; w = [int]$f[5]; h = [int]$f[6] }
            }
        }
        [Console]::Out.WriteLine((ConvertTo-Json -InputObject $o -Compress))
    } catch { [Console]::Out.WriteLine('{}') }
    [Console]::Out.Flush()
}
"""


# Names that say nothing about the button: the part of a split button that opens its menu
# ("More Options" under Shape Fill, PowerPoint 2026-11-14), so the button's own name is used.
_GENERIC = {"more options", "more", "open", "expand", "drop down", "dropdown", "options"}
_NOT_A_BUTTON = {"Window", "Pane", "Document", "TitleBar", ""}


def account_names() -> list[str]:
    """The signed-in person's names, to hide wherever they show ("Deniz Kartal Ser" on
    PowerPoint's account button has no email in it, 2026-11-14). Never logged."""
    names = set()
    if sys.platform == "win32":
        import ctypes

        for kind in (3,):   # NameDisplay
            size = ctypes.c_ulong(256)
            buffer = ctypes.create_unicode_buffer(256)
            try:
                if ctypes.windll.secur32.GetUserNameExW(kind, buffer, ctypes.byref(size)):
                    names.add(buffer.value.strip())
            except (AttributeError, OSError):
                pass
    try:
        names.add(os.getlogin().strip())
    except OSError:
        pass
    return [n for n in names if len(n) >= 3]


_PERSON = re.compile(r"^[A-ZÀ-Ý][a-zà-ÿ'-]+( [A-ZÀ-Ý][a-zà-ÿ'-]+){1,3}$")
_TITLE_BAR = 56   # pixels from a window's top where apps put the account button


def account_buttons(controls, window) -> list:
    """Rectangles of title-bar buttons named like a person ("Deniz Kartal Ser"): the account
    button. Windows keeps that display name nowhere reliable to look it up, 2026-11-14."""
    top = window[1]
    # Just left of Minimize, where Office and Windows 11 apps put it; elsewhere in the title
    # bar "From Beginning" and "More Options" look like names too (PowerPoint, 2026-11-14).
    lows = [c.x for c in controls if c.name.lower().startswith("minimize") and c.y - top < _TITLE_BAR]
    if not lows:
        return []
    edge = min(lows)
    return [c.rect for c in controls
            if c.kind in ("MenuItem", "Button", "Image", "SplitButton") and c.y - top < _TITLE_BAR
            and edge - 240 <= c.x < edge
            and (_PERSON.match(c.name.strip()) or re.search(r"\baccount\b", c.name, re.I))]


def is_private(name: str, accounts) -> bool:
    low = name.lower()
    return bool(uiread._EMAIL.search(name)) or any(a.lower() in low for a in accounts)


def useful_name(found: dict, accounts=()) -> dict | None:
    """What a tutorial calls the control Windows found, or None (no name, the whole window,
    or something private)."""
    name = " ".join(uiread.clean_name(found.get("name")).split())
    kind = found.get("kind", "")
    if name.lower() in _GENERIC:
        name = " ".join(str(found.get("parent") or "").split())
    if not name or kind in _NOT_A_BUTTON or is_private(name, accounts):
        return None
    return {"name": name[:80], "kind": kind, "rect": [found.get(k, 0) for k in ("x", "y", "w", "h")]}


def window_title(hwnd: int) -> str:
    """The window's title ("Presentation1 - PowerPoint"); the tutorial's tag uses its last part."""
    if sys.platform != "win32":
        return ""
    import ctypes

    buffer = ctypes.create_unicode_buffer(512)
    ctypes.windll.user32.GetWindowTextW(hwnd, buffer, 512)
    return buffer.value


def area_for(window, margin=MARGIN, screens=None):
    """The rectangle to record: ``window`` (left, top, width, height) grown by ``margin`` on
    every side, cut to its monitor, even sizes."""
    left, top, width, height = window
    grow_x, grow_y = int(width * margin), int(height * margin)
    grown = (left - grow_x, top - grow_y, width + 2 * grow_x, height + 2 * grow_y)
    return screen.fit_to_screen(grown, screens)[0]


def window_rect(hwnd: int):
    """(left, top, width, height) of a window's visible frame in real pixels, or None."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    rect = wintypes.RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), 9, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
        return None
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def own_windows(hwnd: int) -> list:
    """Visible top-level windows of the same program other than ``hwnd`` itself (its menus and
    pop-ups), as (left, top, width, height) in real pixels."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    pid = screen._owner(hwnd)
    found = []

    def each(other, _):
        if other != hwnd and user32.IsWindowVisible(other) and screen._owner(other) == pid:
            rect = window_rect(other)
            if rect and rect[2] > 4 and rect[3] > 4:
                found.append(rect)
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(each), 0)
    return found


def covered(hwnd: int, rect, own_pid: int | None = None) -> bool:
    """Whether another program's window (a notification, a chat pop-up...) lies over the
    window: 5 x 4 points across it, each asked which window is on top there. The recorded
    program's own windows and this app's (left out of the recording anyway) don't count."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    mine = {screen._owner(hwnd), os.getpid() if own_pid is None else own_pid}
    left, top, width, height = rect
    for i in range(5):
        for j in range(4):
            point = wintypes.POINT(int(left + width * (i + 0.5) / 5), int(top + height * (j + 0.5) / 4))
            on_top = user32.GetAncestor(user32.WindowFromPhysicalPoint(point), 2)
            if on_top and screen._owner(on_top) not in mine:
                return True
    return False


def keys_down(codes, state=None) -> set:
    if state is None:
        import ctypes
        state = lambda code: ctypes.windll.user32.GetAsyncKeyState(code) & 0x8000  # noqa: E731
    return {code for code in codes if state(code)}


def combo(pressed: int, held: set) -> str | None:
    """What a key press shows in a tutorial: "Ctrl + S", "Enter"; None for plain typing."""
    mods = []
    for code in (0x11, 0x12, 0x10, 0x5B):
        if code in held or (code == 0x5B and 0x5C in held):
            mods.append(_MODS[code])
    name = _NAMED.get(pressed) or _LETTERS.get(pressed)
    if name is None:
        return None
    command = any(m in mods for m in ("Ctrl", "Alt", "Win"))
    if command or name in _SHOWN_ALONE:
        return " + ".join(mods + [name])
    return None   # a letter, digit or Space without Ctrl/Alt/Win: typing, never kept


class Namer:
    """The control under a point, from Windows' UI Automation, without waiting for it."""

    def __init__(self, popen=subprocess.Popen, accounts=None):
        self.accounts = account_names() if accounts is None else accounts
        encoded = base64.b64encode(_NAMER.encode("utf-16-le")).decode("ascii")
        try:
            self.process = popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-EncodedCommand", encoded], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                creationflags=uiread.NO_WINDOW, bufsize=1)
        except OSError:
            self.process = None   # PowerShell blocked (some school PCs): no names, crops instead
        self.jobs = queue.Queue()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def ask(self, x: int, y: int, into: dict) -> None:
        """Fill ``into`` with name, kind and rect of the control at (x, y), soon."""
        self.jobs.put((x, y, into))

    def _run(self):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            x, y, into = job
            if self.process is None or self.process.poll() is not None:
                continue
            try:
                self.process.stdin.write(f"{x} {y}\n")
                self.process.stdin.flush()
                found = json.loads(self.process.stdout.readline() or "{}")
            except (OSError, ValueError):
                continue
            useful = useful_name(found, self.accounts)
            if useful and not into.get("by_app"):   # the app's own name for its click wins
                into.update(useful)

    def close(self, wait: float = 3.0):
        self.jobs.put(None)
        self.thread.join(wait)   # answers still on their way are kept
        if self.process is not None:
            try:
                self.process.stdin.close()
                self.process.wait(2)
            except (OSError, subprocess.TimeoutExpired, ValueError):
                self.process.kill()


class Session:
    """One recording: the screen (no cursor) and the log. ``ignore`` is a rectangle the log
    leaves out (the Stop box). Use with ``with``; the hotkey sets ``stopped``."""

    def __init__(self, hwnd: int, folder: str, ignore=None, margin=MARGIN, namer=None,
                 recorder=screen.Recorder, read=uiread.read, clock=time.monotonic):
        self.hwnd, self.folder, self.ignore = hwnd, folder, ignore
        self.window = window_rect(hwnd)
        if self.window is None:
            raise OSError("That window can't be recorded (is it still open?).")
        self.area = area_for(self.window, margin)
        self.clock = clock
        self.stopped = threading.Event()
        self.make_recorder, self.read, self.namer = recorder, read, namer
        self.moves, self.clicks, self.keys, self.typing = [], [], [], []
        self._hovered = []   # per click: what Windows said where the mouse rested before it
        # [t, [[x, y, w, h], ...]] each time the program's own pop-ups change: only the window
        # and these may show in the tutorial; the rest of the margin is someone else's screen.
        self.popups = []
        # [[start, end], ...]: another program's window lay over the recorded one (a
        # notification...): the tutorial shows the last clean moment instead
        self.covers = []
        self.log = None

    # ---- start and stop ----
    def __enter__(self):
        os.makedirs(self.folder, exist_ok=True)
        self.controls = self.read(self.hwnd)          # before: the account's name, to hide
        self.namer = self.namer or Namer()
        self.recorder = self.make_recorder(self.area, os.path.join(self.folder, "recording.mp4"),
                                           mouse=False)
        self.thread = threading.Thread(target=self._watch, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    def stop(self) -> dict:
        if self.log is not None:
            return self.log
        self.stopped.set()
        self.thread.join(2)
        length = self.recorder.stop()
        self.namer.close()
        controls = self.controls + self.read(self.hwnd)   # after: menus may show it too
        for click, hovered in zip(self.clicks, self._hovered):
            if not click.get("name") and hovered.get("name"):
                click.update(hovered)
        accounts = self.namer.accounts
        hidden = uiread.private(controls) + account_buttons(controls, self.window) + [
            c.rect for c in controls if c.name and is_private(c.name, accounts)]
        left, top = self.area[:2]
        self.log = {
            "title": window_title(self.hwnd),
            "area": list(self.area),
            "window": [self.window[0] - left, self.window[1] - top, *self.window[2:]],
            "length": round(length, 3),
            "moves": self.moves, "clicks": self.clicks, "keys": self.keys, "typing": self.typing,
            "popups": self.popups,
            "covered": [[round(a - 0.1, 3), round(b + 0.15, 3)] for a, b in self.covers],
            "private": [[x - left, y - top, w, h] for x, y, w, h in dict.fromkeys(hidden)],
        }
        for click in self.clicks:   # Windows' rectangles in recording pixels too
            if "rect" in click:
                x, y, w, h = click["rect"]
                click["rect"] = [x - left, y - top, w, h]
        with open(os.path.join(self.folder, "log.json"), "w", encoding="utf-8") as f:
            json.dump(self.log, f)
        return self.log

    def note_click(self, x: int, y: int, name: str = "") -> None:
        """A click the app itself made (Do it for me), at screen (x, y), named the user's way.
        Polling can miss a quick click; this never does. The polled twin, if any, is merged."""
        t = self._now()
        left, top = self.area[:2]
        cx, cy = x - left, y - top
        for click in reversed(self.clicks[-4:]):
            if abs(click["t"] - t) < 0.5 and abs(click["x"] - cx) <= 6 and abs(click["y"] - cy) <= 6:
                click.update(name=name, by_app=True)
                click.pop("named_by", None)
                return
        self.clicks.append({"t": round(t - 0.08, 3), "x": cx, "y": cy, "button": "left",
                            "name": name, "by_app": True, "up": t, "up_x": cx, "up_y": cy,
                            "drag": False})
        self._hovered.append({})

    # ---- the watcher ----
    def _now(self) -> float:
        return round(self.clock() - self.recorder.began, 3)

    def _noted(self, x, y, t) -> bool:
        """The app already told this click (note_click came first)."""
        return any(c.get("by_app") and abs(c["t"] - t) < 0.5 and abs(c["x"] - x) <= 6
                   and abs(c["y"] - y) <= 6 for c in self.clicks[-4:])

    def _skip(self, x, y) -> bool:
        if self.ignore is None:
            return False
        left, top, width, height = self.ignore
        return left <= x < left + width and top <= y < top + height

    def _watch(self, cursor=screen.cursor, down=keys_down, sleep=time.sleep, popups=own_windows,
               is_covered=None):
        is_covered = is_covered or (lambda: covered(self.hwnd, self.window))
        left, top = self.area[:2]
        looked = -1.0
        watched = list(_MODS) + list(_NAMED) + list(_LETTERS) + [0x01, 0x02]
        held, pressing, last, typed = set(), None, None, None
        rest_since, hover = 0.0, None   # hover: (screen x, y, answer) where the mouse rested
        while not self.stopped.is_set():
            t = self._now()
            where = cursor()
            now = down(watched)
            if t - looked >= 0.1:   # the program's menus, ten times a second
                looked = t
                # a menu and its shadow are two windows with the same rectangle: one is enough
                seen = sorted({(x - left, y - top, w, h) for x, y, w, h in popups(self.hwnd)})
                seen = [list(r) for r in seen]
                if not self.popups or self.popups[-1][1] != seen:
                    self.popups.append([t, seen])
                if is_covered():
                    if self.covers and self.covers[-1][1] >= t - 0.25:
                        self.covers[-1][1] = t
                    else:
                        self.covers.append([t, t])
            if where:
                x, y = where[0] - left, where[1] - top
                if (x, y) != last:
                    self.moves.append([t, x, y])
                    last, rest_since = (x, y), t
                elif t - rest_since >= SETTLE and (hover is None or hover[:2] != where):
                    # Menu items close the moment they are clicked, so Windows is asked while
                    # the mouse rests on them, before the press (Rectangle, Gold; 2026-11-14).
                    hover = (*where, {})
                    self.namer.ask(*where, hover[2])
                for button, code in (("left", 0x01), ("right", 0x02)):
                    if code in now and code not in held and not self._skip(*where) \
                            and not self._noted(x, y, t):
                        click = {"t": t, "x": x, "y": y, "button": button}
                        near = (hover is not None and abs(hover[0] - where[0]) <= NEAR
                                and abs(hover[1] - where[1]) <= NEAR)
                        self._hovered.append(hover[2] if near else {})
                        self.namer.ask(*where, click)
                        self.clicks.append(click)
                        pressing = (code, click)
                    if code not in now and code in held and pressing and pressing[0] == code:
                        click = pressing[1]
                        click.update(up=t, up_x=x, up_y=y,
                                     drag=abs(x - click["x"]) + abs(y - click["y"]) > DRAG)
                        pressing = None
            if all(code in now for code in STOP_KEYS):
                self.stopped.set()
                break
            for code in now - held:
                if code in _MODS or code in (0x01, 0x02):
                    continue
                shown = combo(code, now)
                if shown in APP_KEYS:
                    continue
                if shown:
                    self.keys.append([t, shown])
                elif typed is None or t - typed[1] > 1.0:
                    typed = [t, t]
                    self.typing.append(typed)
                else:
                    typed[1] = t
            held = now
            sleep(POLL)


# ---- after recording ----------------------------------------------------------------------
def frame_at(video: str, t: float, out_png: str, run=run_ffmpeg) -> str:
    run(["-y", "-ss", f"{max(t, 0):.3f}", "-i", video, "-frames:v", "1", out_png])
    return out_png


def crops(folder: str, log: dict, size=(400, 200), run=run_ffmpeg) -> list[str]:
    """For each click Windows couldn't name: the recording just before the press, cut with the
    click at its exact centre, saved as crop_N.png. The paths, in click order ("" for named
    clicks). No ring is drawn: Qwen3-VL named the ring itself ("circle", 2026-11-14)."""
    from PIL import Image

    video = os.path.join(folder, "recording.mp4")
    out = []
    for i, click in enumerate(log["clicks"]):
        if click.get("name"):
            out.append("")
            continue
        whole = frame_at(video, click["t"] - 0.1, os.path.join(folder, f"frame_{i}.png"), run)
        with Image.open(whole) as im:
            w, h = size
            x0, y0 = click["x"] - w // 2, click["y"] - h // 2   # past the edge: black
            piece = im.convert("RGB").crop((x0, y0, x0 + w, y0 + h))
        colour = swatch_name(piece)
        if colour:   # a colour square: named from its pixels, exactly (Qwen3-VL said "cyan")
            click.update(name=colour, named_by="colour")
            os.remove(whole)
            out.append("")
            continue
        path = os.path.join(folder, f"crop_{i}.png")
        piece.save(path)
        os.remove(whole)
        out.append(path)
    return out


# Office's standard colours and a few more; a swatch takes the nearest one's name.
# Office's own names (its #FFC000 is "Orange", not Gold, 2026-11-14), so labels match the app
COLOURS = {"Dark Red": (192, 0, 0), "Red": (255, 0, 0), "Orange": (255, 192, 0),
           "Yellow": (255, 255, 0), "Light Green": (146, 208, 80),
           "Green": (0, 176, 80), "Teal": (0, 128, 128), "Light Blue": (0, 176, 240),
           "Blue": (0, 112, 192), "Dark Blue": (0, 32, 96), "Purple": (112, 48, 160),
           "Pink": (255, 105, 180), "Brown": (128, 64, 0)}


def swatch_name(piece) -> str | None:
    """The colour's name when the picture's centre is one strong, even colour (a swatch);
    None for anything else. Greys never count: they are the app's own buttons and panels."""
    import colorsys

    cx, cy = piece.width // 2, piece.height // 2
    pixels = [piece.getpixel((cx + dx, cy + dy)) for dx in range(-3, 4) for dy in range(-3, 4)]
    mean = [sum(p[i] for p in pixels) / len(pixels) for i in range(3)]
    if max(abs(p[i] - mean[i]) for p in pixels for i in range(3)) > 18:
        return None   # not even: an icon, text or an edge
    _h, light, sat = colorsys.rgb_to_hls(*(v / 255 for v in mean))
    if sat < 0.45 or not 0.12 < light < 0.9:
        return None
    return min(COLOURS, key=lambda n: sum((a - b) ** 2 for a, b in zip(COLOURS[n], mean)))


NAME_PROMPT = ("This is part of an app's window. Someone clicked the exact centre of this "
               "picture. Name the one thing at the centre, not a heading or label beside it. If it has "
               "words, give them exactly. If it is a picture or shape, name what it shows in 1 "
               "to 3 words (a shape's name, a colour's name, an icon's object). If the centre "
               "is empty space of a page or canvas, answer \"canvas\". "
               "Answer as JSON: {\"name\": \"...\"}.")
CANVAS = "canvas"   # a click or drag on empty space: no label, the step says what it does
_NAME_SCHEMA = {"type": "object", "properties": {"name": {"type": "string", "maxLength": 60}},
                "required": ["name"]}


def name_crops(log: dict, paths, session=None, should_stop=None, gpu: bool = False) -> dict:
    """Names from Qwen3-VL for the crops (photos.py's model); the log, updated."""
    from .. import photos

    todo = [(click, path) for click, path in zip(log["clicks"], paths) if path]
    if not todo:
        return log
    own = session is None
    session = session or photos.session_for(should_stop, gpu)
    with session if own else contextlib.nullcontext():
        session.start()
        for click, path in todo:
            content = session._ask("", [("user", [
                {"type": "image_url", "image_url": {"url": photos.data_url(path)}},
                {"type": "text", "text": NAME_PROMPT}])], _NAME_SCHEMA, 40, 0.1)
            try:
                name = " ".join(str(json.loads(content)["name"]).split())[:60]
            except (ValueError, KeyError, TypeError):
                continue
            if name.lower() == CANVAS:
                click.update(on_canvas=True)
            elif name and not uiread._EMAIL.search(name):
                click.update(name=name, named_by="picture")
    return log

