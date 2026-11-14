"""The fallback that draws promo videos on a PC with no Edge, Chrome or Brave.

Windows' WebView2 runtime (Edge's engine, part of Windows 11, kept even when Edge itself is
removed) won't run on its own, only inside an app's window. This helper is that app: started
as ``AIVideoStudio.exe --promo-host <port> <parent pid>``, it opens one frameless 1920x1080
window far off-screen (a hidden window never paints) and turns on WebView2's DevTools port,
so browser.py drives it exactly like headless Chrome. It runs in its own process because
pywebview needs a main thread, and the app's main thread is its own window. It closes itself
when the app that started it goes away. Tested 2026-11-14: 600 frames at 1080p in 27 s.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading

ARG = "--promo-host"


def available() -> bool:
    """Whether the WebView2 runtime is installed (per machine, or for this user)."""
    roots = [os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
             os.environ.get("LOCALAPPDATA")]
    for root in filter(None, roots):
        folder = os.path.join(root, "Microsoft", "EdgeWebView", "Application")
        try:
            if any(os.path.isfile(os.path.join(folder, v, "msedgewebview2.exe"))
                   for v in os.listdir(folder)):
                return True
        except OSError:
            continue
    return False


def command(port: int) -> list[str]:
    """How to start this helper from the app (frozen) or from the source tree."""
    if getattr(sys, "frozen", False):
        return [sys.executable, ARG, str(port), str(os.getpid())]
    src = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return [sys.executable, "-c",
            f"import sys; sys.path.insert(0, {src!r}); from vidgen.promo import webhost; "
            f"webhost.main(sys.argv)", ARG, str(port), str(os.getpid())]


def _watch_parent(pid: int, window) -> None:
    """Close the window (and so this helper) when the app that started it ends."""
    kernel = ctypes.windll.kernel32
    handle = kernel.OpenProcess(0x00100000, False, pid)   # SYNCHRONIZE
    if handle:
        kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
        kernel.CloseHandle(handle)
    try:
        window.destroy()
    except Exception:  # noqa: BLE001 - already closing
        pass
    os._exit(0)


def _off_taskbar(window) -> None:
    """A tool window: not on the taskbar, not in Alt+Tab."""
    try:
        handle = window.native.Handle.ToInt64()
        user = ctypes.windll.user32
        style = user.GetWindowLongW(handle, -20)            # GWL_EXSTYLE
        user.SetWindowLongW(handle, -20, (style | 0x80) & ~0x40000)   # TOOLWINDOW, not APPWINDOW
    except Exception:  # noqa: BLE001 - only cosmetic
        pass


def main(argv) -> None:
    at = argv.index(ARG)
    port, parent = int(argv[at + 1]), int(argv[at + 2])
    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
        f"--remote-debugging-port={port} --remote-allow-origins=http://127.0.0.1:{port} "
        "--allow-file-access-from-files --mute-audio")
    import webview

    window = webview.create_window("AI Video Studio promo", "about:blank", width=1920,
                                   height=1080, x=-30000, y=-30000, focus=False, frameless=True)

    def ready():
        _off_taskbar(window)
        threading.Thread(target=_watch_parent, args=(parent, window), daemon=True).start()

    webview.start(ready, gui="edgechromium", private_mode=True)


if __name__ == "__main__":
    main(sys.argv)
