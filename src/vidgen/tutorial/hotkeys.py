"""Answer keys for Do it for me (5.4, plan step 5b): Ctrl + Shift + Y and Ctrl + Shift + N.

Clicking the app's own question box closes the menu the recorded app has open (any click
outside an Office menu does, 2026-11-14). Keys registered with Windows (RegisterHotKey) go to
this app only: the recorded app never sees them, so its menus stay open. The recorder leaves
them out of the tutorial's shortcuts (record.APP_KEYS).
"""
from __future__ import annotations

import sys
import threading

YES_NAME, NO_NAME = "Ctrl + Shift + Y", "Ctrl + Shift + N"
_KEYS = {1: (0x59, "yes"), 2: (0x4E, "no")}     # Y, N
_MODS = 0x0002 | 0x0004 | 0x4000                 # CONTROL | SHIFT | NOREPEAT
_WM_HOTKEY, _WM_QUIT = 0x0312, 0x0012


class Hotkeys:
    """Calls ``on_key("yes")`` or ``on_key("no")`` (from its own thread) until stop().
    ``ok`` says whether Windows gave the keys (another app may already use them)."""

    def __init__(self, on_key):
        self.on_key, self.ok, self.thread_id = on_key, False, 0
        ready = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(ready,), daemon=True)
        if sys.platform == "win32":
            self.thread.start()
            ready.wait(2)

    def _run(self, ready):
        import ctypes
        from ctypes import wintypes

        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        self.thread_id = kernel32.GetCurrentThreadId()
        got = [ident for ident, (vk, _name) in _KEYS.items()
               if user32.RegisterHotKey(None, ident, _MODS, vk)]
        self.ok = len(got) == len(_KEYS)
        ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == _WM_HOTKEY and msg.wParam in _KEYS:
                    self.on_key(_KEYS[msg.wParam][1])
        finally:
            for ident in got:
                user32.UnregisterHotKey(None, ident)

    def stop(self):
        if self.thread_id:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, _WM_QUIT, 0, 0)
            self.thread.join(2)
