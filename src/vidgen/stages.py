"""Where a render's time goes: wall time per stage, plus the app's own CPU and memory.

The Log's ⏱️ line is built from this. Stages add up to the total: whatever no stage
claimed is shown as "other", so nothing hides inside a bigger name. App CPU is this
process only (other programs on the PC don't count); ffmpeg, the voice engine and the
picture engine are separate programs, so their work shows as wall time, not CPU.
"""

from __future__ import annotations

import ctypes
import sys
import time
from contextlib import contextmanager

# The order they happen in, which is also the order the Log line uses.
ORDER = ("voices", "pacing", "stock", "AI pictures", "moving pictures", "timeline", "layers",
         "cards", "captions", "sound", "encode", "deliver", "credits", "thumbnail")


def memory_now() -> int:
    """This process's working set in bytes (0 where Windows can't say)."""
    if sys.platform != "win32":
        return 0
    try:
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                    "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        read = ctypes.windll.psapi.GetProcessMemoryInfo
        # Without argtypes the "current process" handle (-1) reaches Windows cut to 32 bits.
        read.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        if read(wintypes.HANDLE(-1), ctypes.byref(counters), counters.cb):
            return int(counters.WorkingSetSize)
    except (AttributeError, OSError):
        pass
    return 0


def cpu_busy(sample: float = 0.3) -> float | None:
    """How busy the whole PC's processor is (0-1), over ``sample`` seconds; None if unknown."""
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    def times():
        idle, kernel, user = (wintypes.FILETIME() for _ in range(3))
        if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel),
                                                     ctypes.byref(user)):
            raise OSError("GetSystemTimes failed")
        return [(t.dwHighDateTime << 32) | t.dwLowDateTime for t in (idle, kernel, user)]

    try:
        a = times()
        time.sleep(sample)
        b = times()
    except (AttributeError, OSError):
        return None
    idle, total = b[0] - a[0], (b[1] - a[1]) + (b[2] - a[2])   # kernel time includes idle
    return None if total <= 0 else max(0.0, min(1.0, 1 - idle / total))


LOW_RAM = 3e9      # free memory below this before a render: suggest closing programs
BUSY_CPU = 0.6     # the processor already this busy: the same


def load_hint(free_memory: int | None, busy: float | None) -> str:
    """A short hint before a render when the PC is already short of room; "" if it isn't."""
    reasons = []
    if free_memory is not None and free_memory < LOW_RAM:
        reasons.append(f"only {free_memory / 1e9:.1f} GB memory free")
    if busy is not None and busy > BUSY_CPU:
        reasons.append(f"the processor is already {busy * 100:.0f} % busy")
    if not reasons:
        return ""
    text = " and ".join(reasons)
    return f"💡 {text[0].upper()}{text[1:]}: close other apps for a faster render."


class Stages:
    """Collects (seconds, app CPU seconds, highest memory seen) per stage name."""

    def __init__(self, clock=time.perf_counter, cpu=time.process_time, memory=memory_now):
        self.clock, self.cpu, self.memory = clock, cpu, memory
        self.began = clock()
        self.seconds: dict[str, float] = {}
        self.cpu_seconds: dict[str, float] = {}
        self.peak: dict[str, int] = {}

    @contextmanager
    def time(self, name: str):
        start, cpu = self.clock(), self.cpu()
        try:
            yield
        finally:
            self.add(name, self.clock() - start, self.cpu() - cpu)

    def add(self, name: str, seconds: float, cpu_seconds: float = 0.0) -> None:
        self.seconds[name] = self.seconds.get(name, 0.0) + seconds
        self.cpu_seconds[name] = self.cpu_seconds.get(name, 0.0) + cpu_seconds
        self.peak[name] = max(self.peak.get(name, 0), self.memory())

    def total(self) -> float:
        return self.clock() - self.began

    def table(self, total: float | None = None) -> list[tuple[str, float, float, int]]:
        """(name, seconds, app CPU seconds, memory bytes) in pipeline order, "other" last.

        The seconds always add up to ``total``.
        """
        total = self.total() if total is None else total
        names = [n for n in ORDER if n in self.seconds] + sorted(
            n for n in self.seconds if n not in ORDER)
        rows = [(n, self.seconds[n], self.cpu_seconds[n], self.peak[n]) for n in names]
        other = total - sum(row[1] for row in rows)
        rows.append(("other", max(other, 0.0), 0.0, 0))
        return rows

    def line(self, total: float | None = None) -> str:
        """The Log's ⏱️ line: every stage that took a tenth of a second or more."""
        total = self.total() if total is None else total
        parts = [f"{name} {seconds:.1f} s" for name, seconds, _cpu, _mem in self.table(total)
                 if seconds >= 0.05]
        return "⏱️ " + " · ".join(parts) + f" · total {total:.1f} s"
