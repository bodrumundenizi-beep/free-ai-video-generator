"""Before a tutorial is recorded (5.4, plan step 5a): the windows to pick from, and a check of
everything that can go wrong, said up front instead of after the user did their steps.

Show it on my PC found out mid-way that a PC refused recording; here a real one-frame grab is
tried first, both ways. Its code is not reused beyond the plain helpers in screen.py.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

from .. import screen, uiread
from . import record


@dataclass
class Window:
    hwnd: int
    title: str
    app: str        # "PowerPoint", from the title, or the program's file name


@dataclass
class Report:
    hwnd: int
    ok: bool = True                 # can be recorded
    lines: list = field(default_factory=list)   # (kind, text): kind "ok", "note" or "problem"
    method: str = ""                # the screen grab that worked: "gdi" or "dda"
    buttons: int = 0
    hidden: int = 0
    scale: float = 1.0
    admin: bool = False
    can_move: bool = False          # Windows lets the app move the mouse: Do it for me can run

    def add(self, kind: str, text: str):
        self.lines.append((kind, text))
        if kind == "problem":
            self.ok = False


# ---- the windows ----------------------------------------------------------------------------
def _exe_name(hwnd: int) -> str:
    """The program's file name without .exe ("POWERPNT"), or ""."""
    if sys.platform != "win32":
        return ""
    import ctypes
    from ctypes import wintypes

    pid = screen._owner(hwnd)
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(520)
        buffer = ctypes.create_unicode_buffer(520)
        if ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return os.path.splitext(os.path.basename(buffer.value))[0]
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)
    return ""


def app_label(title: str, exe: str) -> str:
    from .build import app_name

    return app_name(title) or exe or title


def windows(own_pid: int | None = None) -> list[Window]:
    """The windows a tutorial can be made of: visible, titled, normal-sized app windows, not
    this app's own and not hidden ("cloaked") ones such as suspended Store apps."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    own_pid = os.getpid() if own_pid is None else own_pid
    found = []

    def each(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):   # GW_OWNER: a dialog
            return True
        if user32.GetWindowLongW(hwnd, -20) & 0x80:                       # WS_EX_TOOLWINDOW
            return True
        cloaked = wintypes.DWORD()
        ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4)
        if cloaked.value:
            return True
        title = record.window_title(hwnd).strip()
        rect = record.window_rect(hwnd)
        if not title or rect is None or screen._owner(hwnd) == own_pid:
            return True
        if not user32.IsIconic(hwnd) and (rect[2] < 300 or rect[3] < 200):
            return True
        if title in ("Program Manager", "Windows Input Experience"):
            return True
        found.append(Window(int(hwnd), title, app_label(title, _exe_name(hwnd))))
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(each), 0)
    return found


def icon_of(hwnd: int, size: int = 32):
    """The window's own icon as a PIL image (RGBA), or None."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    from PIL import Image

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.SendMessageTimeoutW.restype = wintypes.LPARAM
    result = ctypes.c_size_t()
    icon = 0
    for kind in (1, 2, 0):   # ICON_BIG, ICON_SMALL2, ICON_SMALL
        if user32.SendMessageTimeoutW(hwnd, 0x007F, kind, 0, 0x0002, 300, ctypes.byref(result)):
            icon = result.value
        if icon:
            break
    if not icon:
        get = getattr(user32, "GetClassLongPtrW", user32.GetClassLongW)
        get.restype = ctypes.c_size_t
        icon = get(hwnd, -14) or get(hwnd, -34)   # GCLP_HICON, GCLP_HICONSM
    if not icon:
        return None

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                    ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), size, -size, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    dc = gdi32.CreateCompatibleDC(0)
    bitmap = gdi32.CreateDIBSection(dc, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
    old = gdi32.SelectObject(dc, bitmap)
    try:
        user32.DrawIconEx(dc, 0, 0, wintypes.HICON(icon), size, size, 0, None, 3)   # DI_NORMAL
        raw = ctypes.string_at(bits, size * size * 4)
    finally:
        gdi32.SelectObject(dc, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(dc)
    picture = Image.frombuffer("RGBA", (size, size), raw, "raw", "BGRA", 0, 1)
    return picture if picture.getextrema()[3][1] > 0 else None


# ---- the checks -----------------------------------------------------------------------------
def is_admin_window(hwnd: int) -> bool:
    """Whether the window's program runs as administrator while this app does not: Windows
    then stops this app reading its buttons and sending it clicks."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    def elevated(process) -> bool | None:
        token = wintypes.HANDLE()
        if not ctypes.windll.advapi32.OpenProcessToken(process, 0x0008, ctypes.byref(token)):
            return None
        try:
            value, size = wintypes.DWORD(), wintypes.DWORD()
            ok = ctypes.windll.advapi32.GetTokenInformation(token, 20, ctypes.byref(value), 4,
                                                            ctypes.byref(size))   # TokenElevation
            return bool(value.value) if ok else None
        finally:
            ctypes.windll.kernel32.CloseHandle(token)

    if elevated(ctypes.windll.kernel32.GetCurrentProcess()):
        return False   # this app is administrator too: nothing is blocked
    process = ctypes.windll.kernel32.OpenProcess(0x1000, False, screen._owner(hwnd))
    if not process:
        return True    # not even allowed to look at it: treat it as blocked
    try:
        return bool(elevated(process))   # unknown counts as not blocked; the grab test decides
    finally:
        ctypes.windll.kernel32.CloseHandle(process)


def window_scale(hwnd: int) -> float:
    if sys.platform != "win32":
        return 1.0
    import ctypes

    try:
        return ctypes.windll.user32.GetDpiForWindow(hwnd) / 96 or 1.0
    except (AttributeError, OSError):
        return 1.0


def analyze(hwnd: int, can_record=screen.can_record, read=uiread.read, rect_of=record.window_rect,
            iconic=None, admin=is_admin_window, scale_of=window_scale, can_move=None) -> Report:
    """Everything that decides whether, and how well, this window can become a tutorial."""
    report = Report(hwnd)
    if iconic is None:
        import ctypes
        iconic = (lambda h: bool(ctypes.windll.user32.IsIconic(h))) if sys.platform == "win32" \
            else (lambda h: False)
    rect = rect_of(hwnd)
    if rect is None:
        report.add("problem", "That window has closed. Pick it again.")
        return report
    if iconic(hwnd):
        report.add("problem", "The window is minimized. Open it on screen, then Analyze again.")
        return report
    report.admin = admin(hwnd)
    if report.admin:
        report.add("problem", "This app runs as administrator, so Windows doesn't let other "
                              "apps record its buttons or follow its clicks. Open it normally "
                              "(not \"Run as administrator\"), then Analyze again.")
        return report
    area = record.area_for(rect)
    if area[2] < rect[2] * 0.9 or area[3] < rect[3] * 0.9:
        report.add("note", "Part of the window is off the screen; only what's on screen is "
                           "recorded. Moving it fully onto one screen is better.")
    report.scale = scale_of(hwnd)
    if report.scale != 1.0:
        report.add("ok", f"Screen scaling {round(report.scale * 100)}%: handled.")
    why = can_record(area)
    if why:
        report.add("problem", why)
        return report
    report.method = screen.METHOD
    report.add("ok", "This PC can record the window"
                     + (" (with Windows' Desktop Duplication)." if report.method == "dda" else "."))
    controls = read(hwnd)
    clickable = [c for c in controls if c.kind in uiread.CLICKABLE and c.name.strip()]
    report.buttons = len(clickable)
    if clickable:
        report.add("ok", f"{len(clickable)} buttons found: their names will be used for the "
                         "labels.")
    else:
        report.add("note", "The app doesn't tell Windows its button names, so the names will "
                           "come from pictures (the Smart writer looks at each click).")
    if can_move is None:
        from .auto import can_move_mouse as can_move
    report.can_move = bool(can_move())
    if not report.can_move:
        report.add("note", "Windows doesn't let the app move the mouse here, so \"Do it for me\" "
                           "is off. Record works: you do the steps.")
    hidden = uiread.private(controls) + record.account_buttons(controls, rect)
    report.hidden = len(set(map(tuple, hidden)))
    if report.hidden:
        report.add("ok", f"Your account details ({report.hidden} place"
                         f"{'s' if report.hidden != 1 else ''}) will be hidden in the video.")
    return report
