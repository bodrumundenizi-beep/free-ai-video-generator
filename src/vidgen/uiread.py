"""The real buttons of a Windows window, read with Windows' own UI Automation (read only).

PowerShell loads the UI Automation library that ships with Windows and lists the window's
controls: name, kind, on/off state and position on screen. Nothing is clicked. A read takes
about 0.3 s (measured on the Night light page, 2026-10-21). Where PowerShell or UI Automation
is blocked, as on some school PCs, the list is empty and tips work without it.

Pages are kept per place, Windows build and language (cache "winui"), so the app learns each
page once. Names and emails found in the window are never logged and never kept: the cache
only holds what the private() filter leaves.
"""

from __future__ import annotations

import base64
import ctypes
import json
import locale
import platform
import re
import subprocess
import sys
from dataclasses import asdict, dataclass

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

_SCRIPT = r"""
param([Int64]$Handle)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$A = [System.Windows.Automation.AutomationElement]
$root = $A::FromHandle([IntPtr]$Handle)
$kinds = 'Button','CheckBox','ComboBox','Hyperlink','ListItem','MenuItem','RadioButton',
         'Slider','TabItem','Text','ToggleButton','TreeItem','Edit','Image','Group'
$out = New-Object System.Collections.Generic.List[object]
$all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                     [System.Windows.Automation.Condition]::TrueCondition)
foreach ($e in $all) {
    try {
        $c = $e.Current
        $kind = $c.ControlType.ProgrammaticName -replace '^ControlType\.', ''
        if ($kinds -notcontains $kind -or $c.IsOffscreen) { continue }
        $r = $c.BoundingRectangle
        if ($r.IsEmpty -or $r.Width -lt 4 -or $r.Height -lt 4) { continue }
        $state = $null
        $p = $null
        if ($e.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern, [ref]$p)) {
            $state = [string]$p.Current.ToggleState
        } elseif ($e.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$p)) {
            $state = if ($p.Current.IsSelected) { 'Selected' } else { 'NotSelected' }
        }
        $out.Add([ordered]@{ name = $c.Name; kind = $kind; state = $state
                             x = [int]$r.X; y = [int]$r.Y; w = [int]$r.Width; h = [int]$r.Height })
    } catch { }
}
ConvertTo-Json -InputObject $out.ToArray() -Compress -Depth 3
"""

# Controls a person clicks; Text, Image and Group are kept only to find private details.
CLICKABLE = {"Button", "CheckBox", "ComboBox", "Hyperlink", "ListItem", "MenuItem",
             "RadioButton", "Slider", "TabItem", "ToggleButton", "TreeItem", "Edit"}
_CHROME = re.compile(r"^(minimize|maximize|restore|close)\b", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


@dataclass(frozen=True)
class Control:
    name: str
    kind: str
    state: str | None
    x: int
    y: int
    w: int
    h: int

    @property
    def rect(self):
        return self.x, self.y, self.w, self.h


def read(hwnd: int, run=subprocess.run) -> list[Control]:
    """The window's controls; [] when Windows won't say (blocked PowerShell, no window...)."""
    wrapped = f"& {{ {_SCRIPT} }} -Handle {int(hwnd)}"
    encoded = base64.b64encode(wrapped.encode("utf-16-le")).decode("ascii")
    try:
        result = run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                      "-EncodedCommand", encoded], capture_output=True, text=True,
                     encoding="utf-8", errors="replace", creationflags=NO_WINDOW, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0 or not (result.stdout or "").strip():
        return []
    return parse(result.stdout)


def parse(text: str) -> list[Control]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if isinstance(data, dict):   # PowerShell writes a single control without the list
        data = [data]
    found = []
    for item in data if isinstance(data, list) else []:
        try:
            found.append(Control(str(item.get("name") or "").strip(), str(item["kind"]),
                                 item.get("state"), int(item["x"]), int(item["y"]),
                                 int(item["w"]), int(item["h"])))
        except (KeyError, TypeError, ValueError):
            continue
    return found


def private(controls) -> list:
    """Rectangles to blur: the account's picture, name and email wherever they show."""
    rects, names = [], set()
    for c in controls:
        email = _EMAIL.search(c.name)
        if email:
            rects.append(c.rect)
            rest = " ".join(_EMAIL.sub(" ", c.name).split())
            if len(rest) >= 3:
                names.add(rest)   # "Deniz Kartal Ser bodrum...@..." -> the display name
        elif c.kind == "Image" and re.search(r"(profile|account|user)\s*(picture|photo|image)",
                                             c.name, re.I):
            rects.append(c.rect)
    # Anywhere else the name shows in part: "Deniz Kartal - Personal" (OneDrive, on Storage
    # Sense) beside the account's "Deniz Kartal Ser" (2026-10-26). Any two words of it in a row.
    pairs = {" ".join(words[i:i + 2]).lower() for name in names
             for words in [name.split()] for i in range(max(len(words) - 1, 1))}
    # Settings' home card shows the first name alone ("Deniz") with the PC's name and "Rename"
    # under it (2026-10-27): a text that is just one word of the name, and the lines below it.
    words = {w.lower() for name in names for w in name.split() if len(w) >= 3}
    hits = [c for c in controls if not _EMAIL.search(c.name)
            and (c.name in names or any(p in c.name.lower() for p in pairs)
                 or c.name.strip().lower() in words)]
    for hit in [c for c in hits if c.name.strip().lower() in words]:
        hits += [c for c in controls if c.kind in ("Text", "Hyperlink", "Button")
                 and abs(c.x - hit.x) <= 8 and 0 < c.y - hit.y <= 3 * max(hit.h, 16)]
    rects += [c.rect for c in hits]
    return rects


_INTERNAL = re.compile(r"^[\w]+(\.[\w]+){2,}$")   # "SystemSettings.ViewModel.SettingEntry"
_NAVIGATION = re.compile(r"\b(search|find a setting|back|forward|up to)\b", re.I)


def named(controls) -> list[Control]:
    """Switches with only an internal name get the name of their row (Startup apps' list).

    Settings lists one switch per app, all called "SystemSettings.ViewModel.SettingEntry";
    the app's name is the leftmost text on the same row.
    """
    texts = [c for c in controls if c.kind == "Text" and c.name and not _EMAIL.search(c.name)]
    out = []
    for c in controls:
        if _INTERNAL.match(c.name) and c.state in ("On", "Off"):
            middle = c.y + c.h / 2
            row = [t for t in texts if t.x < c.x and abs(t.y + t.h / 2 - middle) < max(c.h, 24)]
            if row:
                c = Control(min(row, key=lambda t: t.x).name, c.kind, c.state, c.x, c.y, c.w,
                            c.h)
        out.append(c)
    return out


def targets(controls, window=None) -> list[Control]:
    """The controls a step may click: first of each name.

    Left out: window buttons, the search box and Back, internal names, the account, and with
    ``window`` (x, y, w, h) the side menu and the path along the top. A tip happens on the page
    that is already open, and the small model wandered off into those when it had them.
    """
    controls = named(controls)
    blocked = {c.name for c in controls if _EMAIL.search(c.name)}
    seen, out = set(), []
    for c in controls:
        if (c.kind not in CLICKABLE or not c.name or c.name in seen or _CHROME.match(c.name)
                or c.name in blocked or not 2 <= len(c.name) <= 80 or _INTERNAL.match(c.name)
                or (c.kind in ("Edit", "Button") and _NAVIGATION.search(c.name))):
            continue
        if window is not None:
            left, top, width, _height = window
            side_menu = c.kind in ("ListItem", "TreeItem") and c.x + c.w / 2 < left + width * 0.3
            path = c.y + c.h / 2 < top + 100 and c.kind in ("Button", "Hyperlink")
            if side_menu or path:
                continue
        seen.add(c.name)
        out.append(c)
    return out


def best_match(buttons, words) -> Control | None:
    """The button whose name shares the most of ``words`` (the tip and its map words), or None.

    Chosen by the app, not the Smart writer: in a test (2026-10-21) the writer named the
    switches it could see (HDR) for a tip about one it couldn't (GPU scheduling).
    """
    from .winmap import _terms

    wanted = set()
    for text in words:
        wanted |= _terms(text)
    scored = []
    for b in buttons:
        mine = _terms(b.name)
        shared = len(mine & wanted)
        if shared and shared / max(len(mine), 1) >= 0.5:   # most of the button's name was asked for
            switch = b.state in ("On", "Off") or b.kind == "RadioButton"
            scored.append((shared + (0.5 if switch else 0), b))
    return max(scored, key=lambda pair: pair[0])[1] if scored else None


def label(control) -> str:
    """How a button is described to the Smart writer: its name and what kind of thing it is."""
    if control.state in ("On", "Off", "Indeterminate"):
        kind = f"switch, now {control.state.lower()}"
    elif control.kind == "RadioButton":
        kind = "option, chosen" if control.state == "Selected" else "option"
    else:
        kind = {"Hyperlink": "link", "ComboBox": "drop-down", "Slider": "slider",
                "Edit": "text box"}.get(control.kind, "button")
    return f"{control.name} ({kind})"


def find(controls, name: str):
    """The clickable control called ``name`` (for its position and state), or None. A switch
    goes first: the Notifications page's title has the same name as its switch (2026-10-26)."""
    found = [c for c in named(controls) if c.name == name and c.kind in CLICKABLE]
    return next((c for c in found if c.state in ("On", "Off")), found[0] if found else None)


def states(controls) -> dict:
    """{name: state} of every on/off or selectable control, to see what a recording changed."""
    out = {}
    for c in named(controls):
        if not c.name or _EMAIL.search(c.name):
            continue
        # Switches (On/Off) and option buttons are settings; a selected page in the side
        # menu is only where the user is, not something to put back.
        if c.state in ("On", "Off", "Indeterminate") or (c.kind == "RadioButton" and c.state):
            out.setdefault(c.name, c.state)
    return out


def changed(before: dict, after: dict) -> dict:
    """{name: state before} for every control whose state is different now."""
    return {name: was for name, was in before.items()
            if name in after and after[name] != was}


def page_key(place_id: str) -> str:
    """Cache name for a page's buttons: the place, the Windows build and the language."""
    from . import cache

    try:
        language = ctypes.windll.kernel32.GetUserDefaultUILanguage()
    except (AttributeError, OSError):
        language = locale.getlocale()[0] or ""
    return cache.key("winui", place_id, platform.version(), language) + ".json"


def remember(store, place_id: str, controls) -> None:
    """Keep a page's clickable controls (no private details) for the next plan."""
    store.put_json(page_key(place_id), [asdict(c) for c in targets(controls)])


def recall(store, place_id: str) -> list[Control]:
    kept = store.get_json(page_key(place_id)) or []
    return [Control(**item) for item in kept if isinstance(item, dict)]
