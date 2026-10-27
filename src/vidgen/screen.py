"""Recording part of the screen with the bundled ffmpeg, and cutting it into scenes.

Only the window the tip is about is recorded (its rectangle on screen), never the whole
desktop, so the taskbar, notifications and the guide box stay out of the video. The guide box
also asks Windows to leave it out of any capture, in case it is moved over the window.
"""

from __future__ import annotations

import atexit
import ctypes
import os
import subprocess
import sys
import time

from . import mastering

FPS = 30
HOLD = 30.0   # seconds a step's last frame is held, so a short step still covers its voice


def foreground() -> int:
    """The handle of the window in front (0 if none)."""
    return int(ctypes.windll.user32.GetForegroundWindow() or 0) if sys.platform == "win32" else 0


def open_window(command, wait: float = 5.0, popen=subprocess.Popen, sleep=time.sleep) -> int:
    """Open ``command``'s window and return its handle once it is in front; 0 if it never was.

    The app lets the new window come to the front (Windows otherwise keeps a window opened by
    another program behind the one in use), then waits until the front window changes. When
    the page was already open, it may not change: then whatever is in front after the wait.
    """
    before = foreground()
    if sys.platform == "win32":
        ctypes.windll.user32.AllowSetForegroundWindow(-1)   # ASFW_ANY
    popen(command, creationflags=0x08000000 if sys.platform == "win32" else 0)
    waited = 0.0
    while waited < wait:
        sleep(0.25)
        waited += 0.25
        now = foreground()
        if now and now != before:
            sleep(1.2)   # let the page finish drawing before it is read or recorded
            return foreground()
    now = foreground()
    # Unchanged: fine if the page was already in front, not if this app's own window is.
    return now if now and _owner(now) != os.getpid() else 0


def _owner(hwnd: int) -> int:
    if sys.platform != "win32":
        return 0
    from ctypes import wintypes

    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    return pid.value


def foreground_rect():
    """(left, top, width, height) of the window in front, without its shadow; None if unknown."""
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    rect = wintypes.RECT()
    # The visible frame; GetWindowRect would include the invisible resize border.
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), 9, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
        if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            return None
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width < 200 or height < 150:
        return None
    return rect.left, rect.top, width - width % 2, height - height % 2


def hide_from_capture(hwnd: int) -> None:
    """Ask Windows to leave this window out of screen recordings (Windows 10 2004 and later)."""
    try:
        ctypes.windll.user32.SetWindowDisplayAffinity(ctypes.c_void_p(hwnd), 0x11)
    except (AttributeError, OSError):
        pass   # older Windows: the box is placed beside the recorded window anyway


def grab_args(rect, fps=FPS):
    left, top, width, height = rect
    return ["-f", "gdigrab", "-framerate", str(fps), "-offset_x", str(left), "-offset_y", str(top),
            "-video_size", f"{width}x{height}", "-draw_mouse", "1", "-i", "desktop"]


def can_record(rect) -> str:
    """"" if the screen can be recorded here, else why not (a locked-down PC may refuse)."""
    try:
        mastering.run_ffmpeg([*grab_args(rect), "-frames:v", "1", "-f", "null", "-"])
        return ""
    except Exception as exc:  # noqa: BLE001 - shown to the user as is
        return f"This PC doesn't allow recording the screen ({str(exc)[:160]})."


class Recorder:
    """ffmpeg recording ``rect`` into ``path`` until stop(); marks() are seconds into it."""

    def __init__(self, rect, path, popen=subprocess.Popen, clock=time.monotonic):
        self.rect, self.path, self.clock = rect, path, clock
        self.process = popen(
            [mastering.ffmpeg_exe(), "-hide_banner", "-y", *grab_args(rect), "-c:v", "libx264",
             "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p", path],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=0x08000000 if sys.platform == "win32" else 0)
        self.began = clock()
        self.marks = []
        # Closing the app mid-recording left ffmpeg recording the screen for good (2026-10-22).
        atexit.register(self.stop)

    def mark(self) -> None:
        self.marks.append(self.clock() - self.began)

    def stop(self) -> float:
        """Finish the file; returns how long the recording is. Safe to call twice."""
        length = self.clock() - self.began
        if self.process.poll() is not None:
            return length
        try:
            self.process.communicate(b"q", timeout=15)   # ffmpeg ends the file properly on "q"
        except (subprocess.TimeoutExpired, OSError, ValueError):
            self.process.kill()
        return length


# Seconds of a step kept: its end, where the click happens. Shorter than a spoken line, so the
# click lands while the voice is still talking, and the held last frame shows the result.
LONGEST_STEP = 2.5


def spans(marks, length, longest=LONGEST_STEP):
    """(start, end) of each step: from one mark to the next, the last one to the end.

    A long step keeps only its last ``longest`` seconds: the click that ends it is there,
    and the minutes spent looking for the button are not.
    """
    starts = [0.0] + list(marks)
    ends = list(marks) + [length]
    return [(max(a, b - longest), max(b, a + 0.1)) for a, b in zip(starts, ends)]


ZOOM_MOST = 2.5           # never closer than this: the button keeps some of the page around it
ZOOM_FROM, ZOOM_FOR = 0.3, 0.8   # seconds into a step's clip the zoom starts, and how long it takes
RING = "0xFFD400@0.95"    # the highlight round the button, in the app's yellow


def zoom_plan(window, focus):
    """(zoom, left, top) of the view that ends on ``focus`` (x, y, w, h in window pixels).

    The view is three times the button's width (four times its height) so the page around it
    still reads, at most ZOOM_MOST, never outside the window. A click with no button behind it
    has w = h = 0: then a fifth of the window round it.
    """
    win_w, win_h = window
    x, y, w, h = focus
    if w <= 0 or h <= 0:
        x, y, w, h = x - win_w / 10, y - win_h / 12, win_w / 5, win_h / 6
    zoom = max(1.0, min(ZOOM_MOST, win_w / (w * 3), win_h / (h * 4)))
    view_w, view_h = win_w / zoom, win_h / zoom
    left = min(max(x + w / 2 - view_w / 2, 0), win_w - view_w)
    top = min(max(y + h / 2 - view_h / 2, 0), win_h - view_h)
    return zoom, left, top


def _blur_chain(label, blurs, window):
    """Filters hiding each (x, y, w, h) of ``blurs`` behind big blocks; returns (filters, label).

    Blocks rather than a blur: a 16-pixel line of text can only take a tiny blur radius, which
    leaves it readable; blocks of 8 pixels or more never do.
    """
    parts = []
    win_w, win_h = window
    for i, (x, y, w, h) in enumerate(blurs):
        x, y = max(int(x) - 4, 0), max(int(y) - 4, 0)          # a little margin round it
        w, h = min(int(w) + 8, win_w - x), min(int(h) + 8, win_h - y)
        if w < 2 or h < 2:
            continue
        out = f"p{i}"
        parts.append(f"[{label}]split[{out}a][{out}b];[{out}b]crop={w}:{h}:{x}:{y},"
                     f"scale={max(1, w // 10)}:{max(1, h // 10)},scale={w}:{h}:flags=neighbor"
                     f"[{out}c];[{out}a][{out}c]overlay={x}:{y}[{out}]")
        label = out
    return parts, label


def cut(path, span, size, dest, run=mastering.run_ffmpeg, window=None, focus=None,
        blurs=()) -> str:
    """One step of the recording as a clip the size of the video.

    ``blurs`` (window pixels) are blurred first: the account's name, email and picture.
    With a ``focus`` the view then glides in onto it and a ring is drawn round it. The window
    is fitted whole into the frame over a blurred, darker copy of itself, so a wide window
    still reads in a tall Short, and the last frame is held so the clip covers the voice.
    """
    width, height = size
    start, end = span
    blurs = [b for b in blurs if window and b[2] > 2 and b[3] > 2]
    parts, label = _blur_chain("0:v", blurs, window) if blurs else ([], "0:v")
    if focus is not None and window is not None:
        win_w, win_h = window
        zoom, left, top = zoom_plan(window, focus)
        p = f"clip((it-{ZOOM_FROM})/{ZOOM_FOR},0,1)"
        ease = f"({p}*{p}*(3-2*{p}))"
        # The view's corner travels with the zoom: (0, 0) at 1x, (left, top) at full zoom.
        share = f"(zoom-1)/{max(zoom - 1, 1e-6):.4f}"
        parts.append(
            f"[{label}]zoompan=z='1+{zoom - 1:.4f}*{ease}':x='{left:.1f}*{share}':"
            f"y='{top:.1f}*{share}':d=1:s={win_w}x{win_h}:fps={FPS}[z]")
        fx, fy, fw, fh = focus
        if fw > 0 and fh > 0:   # the ring, once the view has arrived
            pad = 6
            parts.append(
                f"[z]drawbox=x={(fx - left) * zoom - pad:.0f}:y={(fy - top) * zoom - pad:.0f}:"
                f"w={fw * zoom + 2 * pad:.0f}:h={fh * zoom + 2 * pad:.0f}:color={RING}:t=6:"
                f"enable='gte(t,{ZOOM_FROM + ZOOM_FOR})'[r]")
            label = "r"
        else:
            label = "z"
    parts.append(
        f"[{label}]split[a][b];"
        f"[a]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
        f"boxblur=24:2,eq=brightness=-0.12[bg];"
        f"[b]scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,fps={FPS},tpad=stop_mode=clone:stop_duration={HOLD},"
        f"format=yuv420p[v]")
    run(["-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", path, "-filter_complex",
         ";".join(parts), "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "16", dest])
    return dest


def cut_all(path, marks, length, size, out_dir, run=mastering.run_ffmpeg, window=None,
            focuses=(), blurs=()) -> list[str]:
    """Every step's clip; ``focuses`` has one (x, y, w, h) or None per step."""
    os.makedirs(out_dir, exist_ok=True)
    focuses = list(focuses)
    return [cut(path, span, size, os.path.join(out_dir, f"step_{i + 1}.mp4"), run, window,
                focuses[i] if i < len(focuses) else None, blurs)
            for i, span in enumerate(spans(marks, length))]


# --- where the mouse clicks while recording ----------------------------------------------
def cursor():
    """The mouse position in real screen pixels (the ones the recording uses), or None."""
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    point = wintypes.POINT()
    if ctypes.windll.user32.GetPhysicalCursorPos(ctypes.byref(point)):
        return point.x, point.y
    return None


def mouse_down() -> bool:
    return sys.platform == "win32" and bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)


def last_click(clicks, span, window_rect):
    """The last click inside the window during ``span``, as (x, y, 0, 0) in window pixels."""
    left, top, width, height = window_rect
    inside = [(t, x - left, y - top) for t, x, y in clicks
              if span[0] <= t <= span[1] and 0 <= x - left < width and 0 <= y - top < height]
    return (inside[-1][1], inside[-1][2], 0, 0) if inside else None
