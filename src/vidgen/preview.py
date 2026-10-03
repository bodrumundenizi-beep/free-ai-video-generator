"""Watching a finished render inside the app.

The window has no video widget, so playback is done by hand: ffmpeg decodes
the file to small raw frames on a pipe, the window shows them on a timer, and
the soundtrack - extracted to a WAV - plays alongside. Nothing here touches
Tk; the window owns the timer and the label.

Frames are kept small (a few hundred pixels) on purpose: at preview size a
frame is under half a megabyte, so reading the pipe never holds up the window.
"""

from __future__ import annotations

import subprocess
import sys

from . import mastering

FPS = 24
PORTRAIT_BOX = (270, 480)
LANDSCAPE_BOX = (480, 270)
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW


def fit(size, box) -> tuple[int, int]:
    """``size`` scaled to fit inside ``box``, keeping its shape.

    Both sides come out even, which raw video scaling requires, and never
    larger than the source.
    """
    width, height = size
    scale = min(box[0] / width, box[1] / height, 1.0)
    return (max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2))


def preview_size(size, scale: float = 1.0) -> tuple[int, int]:
    """The preview dimensions for a video of ``size``, at a display ``scale``."""
    box = PORTRAIT_BOX if size[1] >= size[0] else LANDSCAPE_BOX
    return fit(size, (round(box[0] * scale), round(box[1] * scale)))


def probe(path: str) -> tuple[int, int, float]:
    """(width, height, seconds) of the video at ``path``."""
    import re

    result = subprocess.run(
        [mastering.ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", path],
        capture_output=True, creationflags=_NO_WINDOW,
    )
    info = result.stderr.decode("utf-8", "replace")
    size = re.search(r"Video:.*?\b(\d{2,5})x(\d{2,5})\b", info)
    length = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", info)
    if not size or not length:
        raise RuntimeError("ffmpeg could not read the video")
    hours, minutes, seconds = length.groups()
    return (int(size.group(1)), int(size.group(2)),
            int(hours) * 3600 + int(minutes) * 60 + float(seconds))


def poster(path: str, size, at: float = 1.0):
    """One frame of the video as a Pillow image, ``size`` big."""
    from PIL import Image

    width, height = size
    result = mastering.run_ffmpeg([
        "-ss", f"{max(at, 0):.2f}", "-i", path, "-frames:v", "1",
        "-vf", f"scale={width}:{height}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ])
    if len(result.stdout) < width * height * 3:
        raise RuntimeError("ffmpeg returned no frame")
    return Image.frombytes("RGB", (width, height), result.stdout[:width * height * 3])


def extract_audio(path: str, wav_path: str) -> str:
    """The video's soundtrack as a WAV at ``wav_path`` (what winsound can play)."""
    mastering.run_ffmpeg(["-y", "-i", path, "-vn", "-ar", "44100", "-ac", "2",
                          "-c:a", "pcm_s16le", wav_path])
    return wav_path


class FrameReader:
    """The video's frames, in order, as raw RGB bytes of ``size``."""

    def __init__(self, path: str, size, fps: int = FPS):
        self.size = size
        self.frame_bytes = size[0] * size[1] * 3
        self.index = 0  # frames handed out so far
        self.process = subprocess.Popen(
            [mastering.ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error",
             "-i", path, "-an", "-vf", f"fps={fps},scale={size[0]}:{size[1]}",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW,
        )

    def read(self):
        """The next frame's bytes, or None when the video is over."""
        data = self.process.stdout.read(self.frame_bytes)
        if len(data) < self.frame_bytes:
            return None
        self.index += 1
        return data

    def skip_to(self, index: int):
        """Read on until frame number ``index`` and return it (None at the end).

        Used to catch up when the window falls behind the sound.
        """
        frame = None
        while self.index <= index:
            frame = self.read()
            if frame is None:
                return None
        return frame

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        try:
            self.process.stdout.close()
        except OSError:
            pass
        self.process.wait()
