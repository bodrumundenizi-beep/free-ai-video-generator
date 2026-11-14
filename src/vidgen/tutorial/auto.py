"""Do it for me (5.4, plan step 5b): the app does the user's steps itself, while recording.

New code, deliberately not Show it on my PC's (drive.py). Its lessons:
- Never find out halfway that Windows refuses: Analyze tests recording and mouse first.
- Never wait forever: finding a button, the click's effect, everything has a time limit.
- Never silently fall back: a step that can't be done is handed to the user, with the reason,
  and the recording carries on.
- The user's hands win: if the mouse moves while the app moves it, the app pauses and asks.

Each step: find the button live (Windows' button list of the window and its open menus; a
picture search by Qwen3-VL only when Windows has no such name) -> glide the cursor onto it so
the user sees where -> ask "Click it?" (Yes / I'll do it / Stop) -> click with real mouse input
-> check the screen changed within CHANGE_WAIT seconds (and that a menu opened when the next
click is in one).

The app clicks only when it is sure (Deniz, 2026-11-14: "if not 100 percent right, hand it to
the user"): Windows names the button exactly as the user wrote it, and only one button (or one
in the menu that just opened). Anything less (a similar name, several such buttons, a button
found only in the picture) is shown with the cursor and handed to the user.

The app only clicks. It never types, and never works in windows run as administrator or in
Windows' security and sign-in windows.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

FIND_WAIT = 2.0       # seconds to find a button (a menu may still be opening)
CHANGE_WAIT = 3.0     # seconds for a click to change the screen
GLIDE = 0.6           # seconds the cursor takes to a button
MOVED = 12            # pixels the cursor may be off where the app put it (else: the user moved)
CHANGED = 1.5         # mean grey-level difference that counts as "the screen changed"
RIBBON = 170          # pixels under a tab where its ribbon (its buttons) is drawn

SECURITY = re.compile(r"windows security|user account control|credential|password|passcode|"
                      r"sign[ -]?in|log[ -]?in|bitlocker|firewall|defender|\bpin\b", re.I)
CHROME = re.compile(r"^(minimize|maximize|restore|close)\b", re.I)
# the arrow half of a split button (Shape Fill's "More Options"): it opens the menu, while the
# main half applies the button straight away (it painted the shape orange, 2026-11-14)
ARROWS = {"more options", "more", "open", "expand", "drop down", "dropdown", "options"}


class Stopped(Exception):
    """The user pressed Stop."""


@dataclass
class Action:
    kind: str     # "click" or "hand" (a step only the user can do, such as a drag)
    name: str     # the button's name ("Shapes"), or the step in the user's words
    text: str     # as the user wrote it
    chain: int = 0   # clicks written with arrows ("Shapes → Rectangle") form one chain: one
                     # question for all of them, so the app never clicks its own box between a
                     # menu opening and the pick in it (that click closed the menu, 2026-11-14)


@dataclass
class Target:
    x: int
    y: int
    name: str
    source: str   # "windows" or "picture"
    open_tab: bool = False   # a tab that is already the open one: nothing to click
    kind: str = ""
    doubt: str = ""          # why the app isn't sure it's the right one ("" = sure)
    rect: tuple = ()         # the button's (x, y, w, h), for checking what its click changed


@dataclass
class Result:
    action: Action
    done_by: str  # "app", "user" or "skipped"
    why: str = ""


@dataclass
class Report:
    results: list = field(default_factory=list)

    @property
    def by_app(self) -> int:
        return sum(r.done_by == "app" for r in self.results)

    def summary(self) -> str:
        user = [r for r in self.results if r.done_by == "user"]
        line = f"The app did {self.by_app} of {len(self.results)} steps."
        if user:
            line += " You did: " + "; ".join(
                f"{r.action.name}" + (f" ({r.why})" if r.why else "") for r in user) + "."
        return line


# ---- the steps ------------------------------------------------------------------------------
_GROUPS = re.compile(r"\s*(?:,|;|\n)\s*")
_ARROWS = re.compile(r"\s*(?:→|->|>|\bthen\b)\s*", re.I)
_HAND = re.compile(r"^(drag|draw|type|write|scroll|select text|hold)\b", re.I)


def parse(text: str) -> list[Action]:
    """ "Insert → Shapes → Rectangle, drag, Shape Fill → Gold" -> the actions, in order.
    "click X" and quotes are tidied; drags and typing are left to the user."""
    actions, chain = [], 0
    for group in _GROUPS.split(text or ""):
        parts = [re.sub(r"(?<!\.)\.$", "", p.strip().strip("\"'“”‘’")).strip()
                 for p in _ARROWS.split(group)]   # "Find..." keeps its dots
        parts = [p for p in parts if p]
        if not parts:
            continue
        chain += 1
        for part in parts:
            if _HAND.match(part):
                actions.append(Action("hand", part, part, chain))
                continue
            name = re.sub(r"^(click|press|open|choose|pick|select|tap)\s+(on\s+|the\s+)?", "",
                          part, flags=re.I).strip()
            name = re.sub(r"\s+(button|tab|menu|option)$", "", name, flags=re.I).strip()
            actions.append(Action("click", name or part, part, chain))
    return actions


def chains(actions) -> list[list[Action]]:
    out = []
    for action in actions:
        if out and out[-1][0].chain == action.chain:
            out[-1].append(action)
        else:
            out.append([action])
    return out


def chain_text(chain) -> str:
    return " → ".join(a.name for a in chain)


SUGGEST = ("You list the buttons to click, in order, to do a task in a Windows app. Give only "
           "the names exactly as the app shows them (tab names, button names, menu items), one "
           "per step, at most 12. A step that needs dragging or typing is written as \"drag ...\" "
           "or \"type ...\". If you are not sure of the app's buttons, give fewer steps. Answer "
           "with JSON only.")
SUGGEST_SCHEMA = {"type": "object", "required": ["steps"], "properties": {
    "steps": {"type": "array", "maxItems": 12, "items": {"type": "string", "maxLength": 60}}}}


def suggest(task: str, app: str, ask) -> str:
    """The Smart writer's guess at the steps, as text for the user to check and change.
    ``ask(system, user, schema)`` returns the model's JSON text."""
    answer = json.loads(ask(SUGGEST, f"App: {app}\nTask: {task}", SUGGEST_SCHEMA))
    steps = [" ".join(str(s).split()) for s in answer.get("steps", []) if str(s).strip()]
    return " → ".join(steps)


def refused(title: str) -> str:
    """Why the app won't work in this window by itself, or ""."""
    if SECURITY.search(title or ""):
        return ("This looks like a security or sign-in window. The app never clicks in those: "
                "use Record and do the steps yourself.")
    return ""


# ---- finding a button -----------------------------------------------------------------------
def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def find(controls, name: str, popups=(), near=None):
    """The control the user means by ``name``: an exact name first, then one that starts with it
    or contains it. Menus that are open win (that's where the next step usually is), then the one
    nearest ``near`` (the last click). Window buttons (Close...) only when asked for exactly."""
    from ..uiread import CLICKABLE

    want = _norm(name)
    if not want:
        return None

    def inside(c, rect):
        return rect[0] <= c.x + c.w / 2 < rect[0] + rect[2] and rect[1] <= c.y + c.h / 2 < rect[1] + rect[3]

    tiers = []
    for c in controls:
        have = _norm(c.name)
        if c.kind not in CLICKABLE and c.kind not in ("SplitButton", "MenuBar", "Custom", "DataItem"):
            continue
        if not have or c.w < 4 or c.h < 4 or (CHROME.match(c.name) and have != want):
            continue
        if have == want:
            tier = 0
        elif have.startswith(want) or want.startswith(have) and len(have) >= 3:
            tier = 1
        elif want in have:
            tier = 2
        else:
            continue
        in_menu = any(inside(c, r) for r in popups)
        distance = 0 if near is None else abs(c.x + c.w / 2 - near[0]) + abs(c.y + c.h / 2 - near[1])
        tiers.append(((tier, not in_menu, distance), c))
    return min(tiers, key=lambda pair: pair[0])[1] if tiers else None


def certain(controls, name: str, popups=(), near=None):
    """(control, doubt): the button the user means and "" when the app is sure; else its best
    guess (or None) and why it isn't sure. Sure = named exactly so by Windows (or, the way Office
    does, the name and then the button's current value: "Shape Fill Orange, Accent 2"), and only
    one such button (several in the one menu that's open are the same thing twice, like
    Rectangle in Recently Used and Basic Shapes)."""
    from ..uiread import CLICKABLE

    want = _norm(name)
    if not want:
        return None, "no name"

    def inside(c, rect):
        return rect[0] <= c.x + c.w / 2 < rect[0] + rect[2] and rect[1] <= c.y + c.h / 2 < rect[1] + rect[3]

    def menu_of(c):
        return next((tuple(r) for r in popups if inside(c, r)), None)

    usable = [c for c in controls
              if (c.kind in CLICKABLE or c.kind in ("SplitButton", "MenuBar", "Custom", "DataItem"))
              and _norm(c.name) and c.w >= 4 and c.h >= 4
              and not (CHROME.match(c.name) and _norm(c.name) != want)]
    exact = [c for c in usable if _norm(c.name) == want]
    # Office's "name, then the current value": "Shape Fill Orange, Accent 2" (a comma in the
    # value); "Shape Fill" is not "Shape" with a value
    valued = [c for c in usable if _norm(c.name).startswith(want + " ")
              and "," in c.name[len(name):]]
    for pool in (exact, valued):
        if not pool:
            continue
        in_menus = [c for c in pool if menu_of(c)]
        pool = in_menus or pool
        menus = {menu_of(c) for c in pool}
        if len(pool) == 1 or (in_menus and len(menus) == 1):
            best = pool[0] if near is None else min(
                pool, key=lambda c: abs(c.x + c.w / 2 - near[0]) + abs(c.y + c.h / 2 - near[1]))
            return best, ""
        return pool[0], f"{len(pool)} buttons are called \"{pool[0].name}\""
    similar = find(controls, name, popups, near)
    if similar is not None:
        return similar, f"it only found something similar: \"{similar.name}\""
    return None, f"couldn't find \"{name}\" on screen"


def arrow_of(button, controls):
    """The arrow half of a split button: a "More Options" right beside it, or inside it."""
    for c in controls:
        if _norm(c.name) not in ARROWS or c is button:
            continue
        beside = abs(c.y - button.y) <= 4 and -2 <= c.x - (button.x + button.w) <= 8
        inside = (button.x <= c.x and c.x + c.w <= button.x + button.w + 2
                  and button.y <= c.y and c.y + c.h <= button.y + button.h + 2)
        if beside or inside:
            return c
    return None


# ---- running -------------------------------------------------------------------------------
class Runner:
    """Does the actions, asking the user before each click. ``io`` is the screen (RealScreen,
    or a fake in tests); ``ask(kind, action, target, why)`` shows the question and returns the
    answer: kind "approve" -> "yes" / "self" / "stop"; "hand" -> "done" / "stop" (the user did
    the step); "moved" -> "continue" / "stop"."""

    def __init__(self, hwnd: int, actions, ask, io, should_stop=lambda: False, on_click=None):
        self.hwnd, self.actions, self.ask, self.io = hwnd, list(actions), ask, io
        self.should_stop = should_stop
        self.on_click = on_click   # (x, y, name): tells the recorder what the app clicked
        self.report = Report()
        self.last = None   # where the app last clicked

    def run(self) -> Report:
        why = refused(self.io.title(self.hwnd))
        if why:
            raise PermissionError(why)
        try:
            for chain in chains(self.actions):
                self._check_stop()
                self.report.results += self._chain(chain)
        except Stopped:
            pass
        return self.report

    def _check_stop(self):
        if self.should_stop():
            raise Stopped()

    def _hand(self, action, why) -> Result:
        """The user does this step; the recording carries on."""
        if self.ask("hand", action, None, why) == "stop":
            raise Stopped()
        return Result(action, "user", why)

    def _chain(self, chain) -> list[Result]:
        """Ask once for the whole chain (the cursor shows its first button), then click it
        through; a step that can't be done is handed over and the chain goes on."""
        first = chain[0]
        whole = Action(first.kind, chain_text(chain), " → ".join(a.text for a in chain),
                       first.chain)
        if first.kind == "hand" and len(chain) == 1:
            return [self._hand(first, "")]
        target = self._locate(first.name) if first.kind == "click" else None
        if target is not None and not target.open_tab and not self._glide(target):
            if self.ask("moved", whole, target, "You moved the mouse.") == "stop":
                raise Stopped()
            target = self._locate(first.name)
            if target is not None and not self._glide(target):
                target = None
        answer = self.ask("approve", whole, target, "" if target or first.kind == "hand"
                          else f"couldn't find \"{first.name}\" yet")
        if answer == "stop":
            raise Stopped()
        if answer == "self":
            self._hand(whole, "")
            return [Result(a, "user") for a in chain]
        results = []
        for i, action in enumerate(chain):
            self._check_stop()
            into_menu = i + 1 < len(chain) and chain[i + 1].kind == "click"
            results.append(self._hand(action, "") if action.kind == "hand"
                           else self._click(action, into_menu))
        return results

    def _click(self, action, into_menu: bool = False) -> Result:
        target = self._locate(action.name, into_menu)
        if target is None:
            return self._hand(action, f"couldn't find \"{action.name}\" on screen")
        if target.doubt:   # not 100 % sure: show the guess, the user clicks
            self._glide(target)
            return self._hand(action, f"isn't sure: {target.doubt}. The cursor shows its guess")
        if target.open_tab:   # clicking it would change nothing (and look like a failure)
            return Result(action, "app", "already open")
        if not self.io.to_front(self.hwnd):
            return self._hand(action, "Windows didn't let the app bring the window to the front")
        if not self._glide(target):
            if self.ask("moved", action, target, "You moved the mouse.") == "stop":
                raise Stopped()
            target = self._locate(action.name)
            if target is None or not self._glide(target):
                return self._hand(action, "the mouse kept moving")
        before = self.io.signature()
        # only buttons that open menus must open one (a tab doesn't)
        expect_menu = into_menu and target.kind in ("MenuItem", "SplitButton", "ComboBox", "Button")
        menus_before = self.io.popups(self.hwnd)
        pick_in = next((m for m in menus_before if m[0] <= target.x < m[0] + m[2]
                        and m[1] <= target.y < m[1] + m[3]), None)   # a pick in an open menu
        self.io.click(target.x, target.y)
        self.last = (target.x, target.y)
        if self.on_click is not None:
            self.on_click(target.x, target.y, action.name)
        waited = 0.0
        while waited < CHANGE_WAIT:
            self.io.sleep(0.1)
            waited += 0.1
            after = self.io.signature()
            if self.io.difference(before, after) >= CHANGED:
                # "the screen changed" isn't "the right thing happened": check it per kind
                if expect_menu and not self._menu_opened(menus_before):
                    # Shape Fill's main half painted the shape instead of opening its colours
                    return self._hand(action, f"clicking \"{action.name}\" didn't open a menu")
                if pick_in is not None and not self._menu_closed(pick_in):
                    return self._hand(action, f"\"{action.name}\" was clicked but its menu "
                                              "stayed open")
                if target.kind == "TabItem" and target.rect and not pick_in:
                    x, y, w, h = target.rect
                    band = (self.io.area[0], y + h, self.io.area[2], RIBBON)
                    if self.io.difference(before, after, band) < CHANGED:
                        return self._hand(action, f"clicking the \"{action.name}\" tab changed "
                                                  "something, but not the buttons under it")
                return Result(action, "app")
        if target.kind == "TabItem":
            # a tab that changes nothing was already open (Office doesn't tell Windows which
            # tab is open, 2026-11-14): done, not a failure
            return Result(action, "app", "already open")
        return self._hand(action, f"clicking \"{action.name}\" didn't change anything; Windows "
                                  "may have blocked the click, or it's a different button")

    def _locate(self, name, into_menu: bool = False):
        waited = 0.0
        while True:
            self._check_stop()
            controls, popups = self.io.controls(self.hwnd)
            found, doubt = certain(controls, name, popups, self.last)
            if found is not None and not doubt and into_menu:
                found = arrow_of(found, controls) or found
            if found is not None and (not doubt or waited >= FIND_WAIT):
                return Target(round(found.x + found.w / 2), round(found.y + found.h / 2),
                              found.name, "windows",
                              found.kind == "TabItem" and found.state == "Selected", found.kind,
                              doubt, tuple(found.rect))
            if waited >= FIND_WAIT:
                break
            self.io.sleep(0.25)
            waited += 0.25
        point = self.io.by_picture(name)
        if point:   # a guess to show with the cursor, never to click
            return Target(*point, name, "picture",
                          doubt="it found it only by looking at the picture")
        return None

    def _menu_opened(self, before) -> bool:
        waited = 0.0
        while waited <= 1.0:
            if [m for m in self.io.popups(self.hwnd) if m not in before]:
                return True
            self.io.sleep(0.1)
            waited += 0.1
        return False

    def _menu_closed(self, menu) -> bool:
        waited = 0.0
        while waited <= 1.0:
            if list(menu) not in [list(m) for m in self.io.popups(self.hwnd)]:
                return True
            self.io.sleep(0.1)
            waited += 0.1
        return False

    def _glide(self, target) -> bool:
        """Move the cursor onto the target; False if the user's hand moved it meanwhile."""
        return self.io.glide(target.x, target.y, GLIDE)


# ---- the real screen ---------------------------------------------------------------------
class RealScreen:
    """The Windows side of Runner: buttons, the mouse, a picture of the window."""

    def __init__(self, hwnd: int, area, picture_finder=None):
        self.hwnd, self.area, self.picture_finder = hwnd, area, picture_finder
        self.width = 480   # the signature's width in pixels (a small grey picture)

    @staticmethod
    def sleep(seconds):
        time.sleep(seconds)

    @staticmethod
    def title(hwnd):
        from .record import window_title
        return window_title(hwnd)

    def controls(self, hwnd):
        from .. import uiread
        from .record import own_windows

        controls = list(uiread.read(hwnd))
        popups = own_windows(hwnd)
        for handle in own_window_handles(hwnd):
            controls += uiread.read(handle)
        return controls, popups

    @staticmethod
    def popups(hwnd):
        from .record import own_windows
        return [list(r) for r in own_windows(hwnd)]

    def signature(self):
        import numpy as np
        from PIL import ImageGrab

        left, top, width, height = self.area
        picture = ImageGrab.grab((left, top, left + width, top + height), all_screens=True)
        return np.asarray(picture.convert("L").resize((self.width, max(1, round(self.width * height / width)))),
                          dtype=np.int16)

    def difference(self, a, b, part=None) -> float:
        """Mean grey change, over the whole area or over ``part`` (screen x, y, w, h)."""
        import numpy as np

        if a.shape != b.shape:
            return 255.0
        if part is not None:
            left, top, width, _height = self.area
            k = a.shape[1] / width
            x, y, w, h = part
            x0, y0 = max(int((x - left) * k), 0), max(int((y - top) * k), 0)
            x1, y1 = min(int((x - left + w) * k) + 1, a.shape[1]), min(int((y - top + h) * k) + 1, a.shape[0])
            if x1 <= x0 or y1 <= y0:
                return 0.0
            a, b = a[y0:y1, x0:x1], b[y0:y1, x0:x1]
        diff = np.abs(a - b)
        # the share of clearly changed spots counts, not only the average: a white menu opening
        # over a white page in a big window averaged 0.46 (Notepad++, 2026-11-14)
        return float(max(diff.mean(), (diff > 12).mean() * 500))

    def by_picture(self, name):
        return self.picture_finder(name) if self.picture_finder else None

    @staticmethod
    def to_front(hwnd) -> bool:
        import ctypes

        from .. import screen

        user32 = ctypes.windll.user32
        front = user32.GetForegroundWindow()
        if front == hwnd or screen._owner(front) == screen._owner(hwnd):
            return True
        user32.AllowSetForegroundWindow(-1)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.2)
        front = user32.GetForegroundWindow()
        return front == hwnd or screen._owner(front) == screen._owner(hwnd)

    @staticmethod
    def glide(x, y, seconds) -> bool:
        """Real mouse movement (input events, like a hand), eased; False if the cursor ended
        up somewhere else than where the app put it (the user's hand)."""
        import ctypes

        from .. import screen

        user32 = ctypes.windll.user32
        width, height = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)   # all screens
        left, top = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
        start = screen.cursor() or (x, y)
        steps = max(1, int(seconds * 60))
        for i in range(1, steps + 1):
            k = i / steps
            k = k * k * (3 - 2 * k)
            px, py = round(start[0] + (x - start[0]) * k), round(start[1] + (y - start[1]) * k)
            # MOVE | ABSOLUTE | VIRTUALDESK: 0..65535 across all screens
            user32.mouse_event(0x8001 | 0x4000, (px - left) * 65535 // max(width - 1, 1),
                               (py - top) * 65535 // max(height - 1, 1), 0, 0)
            time.sleep(seconds / steps)
            now = screen.cursor()
            if now and (abs(now[0] - px) > MOVED or abs(now[1] - py) > MOVED):
                return False
        return True

    @staticmethod
    def click(x, y):
        import ctypes

        user32 = ctypes.windll.user32
        user32.mouse_event(0x0002, 0, 0, 0, 0)   # left down
        time.sleep(0.08)
        user32.mouse_event(0x0004, 0, 0, 0, 0)   # left up


def own_window_handles(hwnd: int) -> list[int]:
    """Visible top-level windows of the same program other than ``hwnd`` (its open menus)."""
    import ctypes
    from ctypes import wintypes

    from .. import screen

    user32 = ctypes.windll.user32
    pid, found = screen._owner(hwnd), []

    def each(other, _):
        if other != hwnd and user32.IsWindowVisible(other) and screen._owner(other) == pid:
            found.append(int(other))
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(each), 0)
    return found


def can_move_mouse() -> bool:
    """Whether Windows lets this app move the mouse: one pixel and back."""
    import ctypes

    from .. import screen

    start = screen.cursor()
    if start is None:
        return False
    user32 = ctypes.windll.user32
    user32.SetPhysicalCursorPos(start[0] + 1, start[1])
    moved = screen.cursor()
    user32.SetPhysicalCursorPos(*start)
    return moved is not None and moved[0] == start[0] + 1


FIND_PROMPT = ("This is an app's window. Where is the button or item called \"{name}\"? Look "
               "for that exact label, or the icon that means it. Answer as JSON: {{\"found\": "
               "true or false, \"x\": 0-1000, \"y\": 0-1000}}, x and y being its centre as "
               "thousandths of the picture's width and height.")
FIND_SCHEMA = {"type": "object", "required": ["found", "x", "y"], "properties": {
    "found": {"type": "boolean"}, "x": {"type": "integer", "minimum": 0, "maximum": 1000},
    "y": {"type": "integer", "minimum": 0, "maximum": 1000}}}


class PictureFinder:
    """Qwen3-VL finds a button by looking, when Windows has no such name. The model is loaded
    once, at the first need, and closed with close()."""

    def __init__(self, area, folder: str, should_stop=None, gpu: bool = False):
        self.area, self.folder, self.should_stop, self.gpu = area, folder, should_stop, gpu
        self.session = None

    def __call__(self, name: str):
        import os

        from PIL import ImageGrab

        from .. import photos

        left, top, width, height = self.area
        path = os.path.join(self.folder, "find.png")
        ImageGrab.grab((left, top, left + width, top + height), all_screens=True).save(path)
        if self.session is None:
            self.session = photos.session_for(self.should_stop, self.gpu)
            self.session.__enter__()
            self.session.start()
        content = self.session._ask("", [("user", [
            {"type": "image_url", "image_url": {"url": photos.data_url(path, side=1280)}},
            {"type": "text", "text": FIND_PROMPT.format(name=name)}])], FIND_SCHEMA, 40, 0.1)
        try:
            answer = json.loads(content)
        except ValueError:
            return None
        if not answer.get("found"):
            return None
        return (round(left + width * answer["x"] / 1000), round(top + height * answer["y"] / 1000))

    def close(self):
        if self.session is not None:
            self.session.__exit__(None, None, None)
            self.session = None

