"""Show it on my PC: the app finds the page itself, slowly, so the video shows the way there.

The app's own mouse and keys (Windows SendInput), slow enough for a viewer to follow: the
mouse glides, the name is typed a letter at a time. Only navigation is clicked here (a search
box, a search result, a menu item), never a setting.

Before every action the window in front must be the one the app is working in, and the mouse
must still be where the app left it. Otherwise ``Interrupted``: a person took over, or another
window came up, and the app stops typing at once (never into the wrong window) and opens the
page directly, as before.
"""

from __future__ import annotations

import ctypes
import os
import re
import sys
import time

from . import screen, uiread, winmap

GLIDE = 0.6          # seconds the mouse takes to reach a button
PER_KEY = 0.12       # seconds per typed letter, about a person typing
SETTLE = 1.5         # seconds for a page or menu to show
FIND_FOR = 5.0       # seconds to look for the result before opening the page directly
MOVED = 12           # pixels: the mouse is further than this from where the app left it
PAD = 60             # pixels recorded round the taskbar's menu

# Start's search results can't be read (Windows shows them in a web view that UI Automation
# doesn't list, checked 2026-10-26), so the app couldn't know what Enter would open there.
# The Run box opens exactly the command typed, and Settings' own search lists its results.


class Interrupted(Exception):
    """A person took over or another window came up: open the page directly instead."""


def route(place) -> str:
    """How the app finds ``place``: "settings" (Settings' search box), "taskbar" (right-click
    the taskbar), "run" (the Run box) or "" (open it directly, as before)."""
    if place is None or place.admin:
        return ""
    kind = place.opens.partition(":")[0]
    if kind == "ms-settings":
        return "settings"
    if place.id == "task-manager":
        return "taskbar"
    return "run" if kind in ("control", "cpl", "run", "shell") else ""


def run_text(place) -> str:
    """What is typed in the Run box: the command as a person would type it."""
    kind, _, what = place.opens.partition(":")
    if kind == "control":
        return f"control /name {what}"
    return f"shell:{what}" if kind == "shell" else what


def search_box(controls):
    """Settings' search box ("Search box, Find a setting"), or None."""
    return next((c for c in controls if c.kind == "Edit"
                 and re.search(r"search|find", c.name, re.I)), None)


def menu_rect(items, screen_size):
    """The part of the screen recorded round the taskbar's menu: the menu and PAD round it."""
    left = min(c.x for c in items) - PAD
    top = min(c.y for c in items) - PAD
    right = max(c.x + c.w for c in items) + PAD
    bottom = max(c.y + c.h for c in items) + PAD
    left, top = max(left, 0), max(top, 0)
    right, bottom = min(right, screen_size[0]), min(bottom, screen_size[1])
    width, height = right - left, bottom - top
    return left, top, width - width % 2, height - height % 2


def taskbar_gap(buttons):
    """(x, y) in the middle of the widest empty stretch of a bottom taskbar, or None."""
    row = sorted((b for b in buttons if b.kind == "Button"), key=lambda b: b.x)
    if len(row) < 2 or len({b.y for b in row}) > 2:
        return None   # a taskbar on the side, or nothing read
    gaps = [(b.x - (a.x + a.w), a.x + a.w, b) for a, b in zip(row, row[1:])]
    width, start, after = max(gaps, key=lambda g: g[0])
    if width < 40:
        return None
    return start + width // 2, after.y + after.h // 2


def pick(controls, place):
    """The search result for ``place`` among ``controls``, or None.

    A result is named like "Task Manager, App" or "Night light, System > Display": only the
    part before the first comma is compared with the place's name and words.
    """
    results = [c for c in controls if c.kind in ("ListItem", "Button", "Hyperlink", "MenuItem")
               and c.name]
    short = [uiread.Control(c.name.split(",")[0].strip(), c.kind, c.state, c.x, c.y, c.w, c.h)
             for c in results]
    best = uiread.best_match(short, [place.name, *place.words])
    if best is None:
        return None
    exact = [s for s in short if s.name.lower() == place.name.lower()]
    best = min(exact, key=lambda s: s.y) if exact else best   # the name itself, highest first
    return results[short.index(best)]


def same_page(controls, planned) -> bool:
    """True when ``controls`` look like the page read while planning (``planned``): most of
    its buttons are there. "Turn on Storage Sense" opened Storage, not Storage Sense (2026-10-26)."""
    # Names with numbers change while a page loads ("Installed apps 120 GB"): left out.
    wanted = {c.name for c in planned if not re.search(r"\d", c.name)}
    if not wanted:
        return True   # nothing to compare with
    names = {c.name for c in uiread.targets(controls)}
    return len(names & wanted) >= len(wanted) / 3


def settled(hwnd, read=None, sleep=time.sleep, wait: float = 5.0):
    """The page's controls once it has finished loading: two reads in a row with the same
    switches. Read at once, Storage showed Storage Sense as Off while it was on (2026-10-26)."""
    read = read or uiread.read   # looked up now: tests replace it
    last = read(hwnd)
    waited = 0.0
    while waited < wait:
        sleep(0.7)
        waited += 0.7
        now = read(hwnd)
        if now and uiread.states(now) == uiread.states(last):
            return now
        last = now
    return last


def find_more(controls):
    """The button that opens more of the page ("Show more graphics settings"), or None."""
    from .tips import MORE

    return next((c for c in uiread.targets(controls) if MORE.search(c.name)), None)


def open_more(hwnd, rect, read=None, stop=lambda: False, sleep=time.sleep,
              clicks_only=False, name=""):
    """Click the page's "Show more" button (or the one called ``name``), scrolling down to it
    (up to 3 times) when it is below the window: (the button or None, the controls after)."""
    read = read or uiread.read   # looked up now: tests replace it
    driver = Driver(hwnd, stop, sleep, clicks_only)
    controls = read(hwnd)
    for scrolls in range(4):
        more = uiread.find(controls, name) if name else find_more(controls)
        if more is not None:
            driver.click(more)
            sleep(SETTLE)
            return more, settled(hwnd, read, sleep)
        if scrolls < 3 and rect is not None:
            driver.scroll(rect[0] + rect[2] * 2 // 3, rect[1] + rect[3] // 2)
            controls = read(hwnd)
    return None, controls


def new_controls(before, after):
    """Controls in ``after`` that weren't in ``before``: what typing brought up (the results)."""
    seen = {(c.name, c.kind) for c in before}
    return [c for c in after if (c.name, c.kind) not in seen]


def exe(hwnd: int) -> str:
    """The program file name of a window, lower case ("" when Windows won't say)."""
    pid = screen._owner(hwnd)
    if not pid or sys.platform != "win32":
        return ""
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        size = ctypes.c_ulong(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


# --- SendInput ------------------------------------------------------------------------------
if sys.platform == "win32":
    from ctypes import wintypes

    class _MOUSE(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("data", wintypes.DWORD),
                    ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("extra", ctypes.c_size_t)]

    class _KEY(ctypes.Structure):
        _fields_ = [("vk", wintypes.WORD), ("scan", wintypes.WORD), ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]

    class _UNION(ctypes.Union):
        _fields_ = [("mi", _MOUSE), ("ki", _KEY), ("pad", ctypes.c_byte * 32)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]


KEY_UP, UNICODE = 0x0002, 0x0004
VK_RETURN, VK_ESCAPE, VK_LWIN, VK_CONTROL = 0x0D, 0x1B, 0x5B, 0x11


def key_events(text: str) -> list:
    """(vk, scan, flags) of the key presses that type ``text``: each letter down, then up."""
    out = []
    for ch in text:
        out += [(0, ord(ch), UNICODE), (0, ord(ch), UNICODE | KEY_UP)]
    return out


def _send(events) -> None:
    """Key events (vk, scan, flags) through SendInput."""
    items = (_INPUT * len(events))()
    for item, (vk, scan, flags) in zip(items, events):
        item.type = 1   # INPUT_KEYBOARD
        item.u.ki = _KEY(vk, scan, flags, 0, 0)
    ctypes.windll.user32.SendInput(len(events), items, ctypes.sizeof(_INPUT))


def _button(down: int, up: int) -> None:
    user32 = ctypes.windll.user32
    user32.mouse_event(down, 0, 0, 0, 0)
    time.sleep(0.05)
    user32.mouse_event(up, 0, 0, 0, 0)


class Driver:
    """The app's mouse and keys while it finds a page; ``hwnd`` is the window it works in
    (None: any, for the right-click on the taskbar). ``stop()`` True ends it (Cancel)."""

    def __init__(self, hwnd, stop=lambda: False, sleep=time.sleep, clicks_only=False):
        self.hwnd, self.stop, self.sleep, self.clicks_only = hwnd, stop, sleep, clicks_only
        self.at = None   # where the app last put the mouse

    def check(self) -> None:
        """Raise Interrupted unless the app's window is in front and the mouse is untouched."""
        if self.stop():
            raise Interrupted("cancelled")
        front = screen.foreground()
        # Clicking a switch, this app's own guide box may be in front (its Next was pressed):
        # the click lands by position, so the window under the mouse is what counts then.
        own = self.clicks_only and screen._owner(front) == os.getpid()
        if self.hwnd is not None and front != self.hwnd and not own:
            raise Interrupted("another window came to the front")
        if elevated(front):
            raise Interrupted("a window with admin rights is in front")
        where = screen.cursor()
        if self.at and where and max(abs(where[0] - self.at[0]),
                                     abs(where[1] - self.at[1])) > MOVED:
            raise Interrupted("the mouse was moved")

    def glide(self, x: int, y: int) -> None:
        """Move the mouse to (x, y) over GLIDE seconds, so a viewer sees where it goes."""
        self.check()
        start = screen.cursor() or (x, y)
        steps = 20
        for i in range(1, steps + 1):
            t = i / steps
            t = t * t * (3 - 2 * t)   # ease in and out
            pos = (round(start[0] + (x - start[0]) * t), round(start[1] + (y - start[1]) * t))
            ctypes.windll.user32.SetPhysicalCursorPos(*pos)
            self.at = pos
            self.sleep(GLIDE / steps)

    def click(self, control, right: bool = False) -> None:
        """Glide to the middle of ``control`` and click it."""
        x, y = control.x + control.w // 2, control.y + control.h // 2
        self.glide(x, y)
        self.sleep(0.25)
        self.check()
        if self.clicks_only and under(x, y) != self.hwnd:   # a switch: only on its page
            raise Interrupted("something else is over the button")
        _button(0x0008, 0x0010) if right else _button(0x0002, 0x0004)

    def scroll(self, x: int, y: int, ticks: int = 5) -> None:
        """Glide to (x, y) on the page and turn the mouse wheel down ``ticks`` notches."""
        self.glide(x, y)
        self.check()
        ctypes.windll.user32.mouse_event(0x0800, 0, 0, ctypes.c_ulong(-120 * ticks).value, 0)
        self.sleep(0.6)

    def point(self, control) -> None:
        """Glide onto ``control`` to show it, without clicking."""
        self.glide(control.x + control.w // 2, control.y + control.h // 2)

    def type(self, text: str) -> None:
        for down, up in zip(*[iter(key_events(text))] * 2):
            self.check()
            _send([down, up])
            self.sleep(PER_KEY)

    def key(self, vk: int) -> None:
        self.check()
        _send([(vk, 0, 0), (vk, 0, KEY_UP)])


def under(x: int, y: int) -> int:
    """The top window at screen point (x, y), or 0."""
    if sys.platform != "win32":
        return 0
    user32 = ctypes.windll.user32
    user32.WindowFromPhysicalPoint.restype = wintypes.HWND
    user32.GetAncestor.restype = wintypes.HWND
    child = user32.WindowFromPhysicalPoint(wintypes.POINT(x, y))
    return int(user32.GetAncestor(child, 2) or 0) if child else 0   # GA_ROOT


def elevated(hwnd: int) -> bool:
    """True when ``hwnd`` runs with admin rights (Task Manager does). Windows then drops the
    app's mouse and keys while it is in front (UIPI; the tip test got stuck on it, 2026-10-26)."""
    pid = screen._owner(hwnd)
    if not pid or sys.platform != "win32" or pid == os.getpid():
        return False
    kernel32, advapi32 = ctypes.windll.kernel32, ctypes.windll.advapi32
    process = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not process:
        return False
    token = wintypes.HANDLE()
    try:
        if not advapi32.OpenProcessToken(process, 0x0008, ctypes.byref(token)):   # TOKEN_QUERY
            return True   # refused: it runs above this app
        value, size = wintypes.DWORD(), wintypes.DWORD()
        try:
            ok = advapi32.GetTokenInformation(token, 20, ctypes.byref(value), 4,   # elevation
                                              ctypes.byref(size))
            return bool(ok and value.value)
        finally:
            kernel32.CloseHandle(token)
    finally:
        kernel32.CloseHandle(process)


def window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def close(hwnd: int) -> None:
    """Close a page the app opened (only that window, and never one of this app's)."""
    if hwnd and sys.platform == "win32" and screen._owner(hwnd) != os.getpid():
        ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE


def wait_front(skip, wait: float = 8.0, sleep=time.sleep) -> int:
    """The new window in front once it settles (not one of ``skip``, not this app's), or 0."""
    waited = 0.0
    while waited < wait:
        sleep(0.25)
        waited += 0.25
        now = screen.foreground()
        if now and now not in skip and screen._owner(now) != os.getpid():
            sleep(1.2)   # let the page finish drawing; a window that flashed by is skipped
            if screen.foreground() == now:
                return now
    return 0


# --- the routes ---------------------------------------------------------------------------
# Each calls did(voice, control) after every action: that is one step of the video, with the
# control (or None) to zoom onto. A route returns False when it can't go on; the caller then
# opens the page directly.

def settings_search(hwnd, place, did, stop=lambda: False, read=None) -> bool:
    """In the Settings window ``hwnd`` (on its home page): search for the place, click it."""
    read = read or uiread.read   # looked up now: tests replace it
    driver = Driver(hwnd, stop)
    before = read(hwnd)
    box = search_box(before)
    if box is None:
        return False
    driver.click(box)
    driver.type(place.name)
    did(f"Press Windows and I to open Settings, then search for {place.name}.", box)
    waited, result = 0.0, None
    while result is None and waited < FIND_FOR:
        driver.sleep(0.5)
        waited += 0.5
        result = pick(new_controls(before, read(hwnd)), place)
    if result is None:
        driver.key(VK_ESCAPE)
        return False
    driver.click(result)
    driver.sleep(SETTLE)
    did(f"Pick {place.name}.", result)
    return True


def run_box(place, record, did, stop=lambda: False, sleep=time.sleep) -> int:
    """Windows + R, type the place's command, Enter; the page's window, or 0.

    ``record(rect)`` starts the recording of the Run box once it is up.
    """
    prior = screen.foreground()
    _send([(VK_LWIN, 0, 0), (ord("R"), 0, 0), (ord("R"), 0, KEY_UP), (VK_LWIN, 0, KEY_UP)])
    box = wait_front({prior}, wait=3.0, sleep=sleep)
    # Only the real Run box is typed into: a dialog of Windows' own Explorer.
    if not box or window_class(box) != "#32770" or exe(box) != "explorer.exe":
        return 0
    rect = screen.foreground_rect()
    if rect is None or not record(rect):
        close(box)
        return 0
    driver = Driver(box, stop, sleep)
    sleep(0.8)   # the recording has started
    text = run_text(place)
    try:
        driver.type(text)
        sleep(0.6)
        did(f"Press Windows and R, type {text}, and press Enter.", None)
        driver.key(VK_RETURN)
    except Interrupted:
        close(box)
        raise
    return wait_front({box, prior}, sleep=sleep)


def taskbar_menu(place, record, did, stop=lambda: False, sleep=time.sleep,
                 read=None) -> int:
    """Right-click the taskbar, pick the place in its menu; the page's window, or 0."""
    read = read or uiread.read   # looked up now: tests replace it
    user32 = ctypes.windll.user32
    tray = user32.FindWindowW("Shell_TrayWnd", None)
    spot = taskbar_gap(read(tray)) if tray else None
    if spot is None:
        return 0
    prior = screen.foreground()
    driver = Driver(None, stop, sleep)
    driver.click(uiread.Control("", "Pane", None, spot[0] - 1, spot[1] - 1, 2, 2), right=True)
    sleep(SETTLE)
    items = [c for c in read(tray) if c.kind == "MenuItem"]
    item = pick(items, place)
    if item is None:
        driver.key(VK_ESCAPE)
        return 0
    if not record(menu_rect(items, (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)))):
        driver.key(VK_ESCAPE)
        return 0
    sleep(0.8)
    driver.click(item)
    did(f"Right-click an empty spot on the taskbar, then pick {item.name}.", item)
    # Not skipping ``prior``: Task Manager runs with admin rights, so the copy opened while
    # planning can't be closed by the app, and the menu brings that same window back (2026-10-26).
    return wait_front({tray}, sleep=sleep)


def flip(hwnd, name: str, to: str, stop=lambda: False, read=None, tries: int = 2,
         sleep=time.sleep, clicking=lambda: None) -> bool:
    """Set the switch called ``name`` on page ``hwnd`` to ``to`` ("On"/"Off") with the app's own
    click, then read the page again to check. True once it is so; False when it couldn't
    (not found, a person took over, or it didn't change)."""
    read = read or uiread.read   # looked up now: tests replace it
    def find(controls):
        # The switch itself: on the Notifications page the title comes first with the same
        # name, and the app "flipped" that (2026-10-26).
        return next((c for c in uiread.named(controls)
                     if c.name == name and c.state in ("On", "Off")), None)

    for _ in range(tries):
        switch = find(read(hwnd))
        if switch is None or switch.state not in ("On", "Off"):
            return False
        if switch.state == to:
            return True
        try:
            clicking()   # just before the click: write it down (putback)
            Driver(hwnd, stop, sleep, clicks_only=True).click(switch)
        except Interrupted:
            return False
        sleep(1.2)   # the page shows the new state
    switch = find(read(hwnd))
    return switch is not None and switch.state == to
