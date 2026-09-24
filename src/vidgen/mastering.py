"""Broadcast mastering for voiceovers, with the bundled ffmpeg.

Raw TTS sounds thin and uneven. Each voice line goes through:

    highpass 70 Hz      remove rumble the ear can't use
    lowshelf +2.5 dB    150 Hz warmth - "microphone presence"
    acompressor 3:1     evens out loud and quiet words
    loudnorm -14 LUFS   the level YouTube Shorts, Reels and TikTok target

Two passes: loudnorm measures the shaped voice, then the exact gain to -14
LUFS is applied and a limiter holds peaks under -1.5 dB. One-pass loudnorm on
a two-second line is inaccurate (-15.3 LUFS on a test line meant to be -14),
and loudnorm's own "linear" mode quietly falls back to dynamic when peaks
get in the way (see filter_chain).

Also here: decoding audio to numpy and writing WAV, so voice.py doesn't need
MoviePy just to insert a few pauses.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import wave

import numpy as np

TARGET_LUFS = -14.0
TRUE_PEAK = -1.5
LRA = 11.0
OUTPUT_RATE = 44100

# The limiter only sees samples, not the peaks that form *between* them, so it
# is set this far below the true-peak target to leave room for those.
LIMITER_HEADROOM_DB = 1.0

VOICE_CHAIN = (
    # Resample first: resampling after the limiter recreates the peaks it removed.
    f"aresample={OUTPUT_RATE},"
    "highpass=f=70,"
    "lowshelf=f=150:g=2.5,"
    "acompressor=threshold=-18dB:ratio=3:attack=5:release=60:makeup=2"
)


def ffmpeg_exe() -> str:
    # Honours IMAGEIO_FFMPEG_EXE, which the frozen build points at its bundled copy.
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(args, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    """ffmpeg with no console window (the installed app has no console to borrow)."""
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    result = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-nostdin", *args] if stdin is None
        else [ffmpeg_exe(), "-hide_banner", *args],
        input=stdin, capture_output=True, creationflags=flags,
    )
    if result.returncode != 0:
        tail = result.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
        raise RuntimeError("ffmpeg failed: " + " | ".join(tail))
    return result


def decode(path: str, rate: int) -> np.ndarray:
    """Mono float32 samples in [-1, 1] at ``rate``."""
    out = run_ffmpeg(["-i", path, "-f", "s16le", "-ac", "1", "-ar", str(rate), "-"]).stdout
    return np.frombuffer(out, dtype=np.int16).astype(np.float32) / 32768.0


def write_wav(path: str, samples: np.ndarray, rate: int) -> str:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(path, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(pcm.tobytes())
    return path


def filter_chain(gain_db: float | None = None) -> str:
    """The tone chain, then (on the second pass) the exact gain and a limiter.

    loudnorm's own linear mode silently switches to dynamic whenever the gain
    it needs would push peaks past its ceiling - which left some voices a full
    dB short of -14 and others peaking at -0.7 dBTP. So loudnorm only
    *measures*; the gain is applied here, and alimiter catches the few peaks
    that gain pushes too high.
    """
    if gain_db is None:
        return f"{VOICE_CHAIN},loudnorm=I={TARGET_LUFS:g}:TP={TRUE_PEAK:g}:LRA={LRA:g}:print_format=json"
    ceiling = 10 ** ((TRUE_PEAK - LIMITER_HEADROOM_DB) / 20)
    return (f"{VOICE_CHAIN},volume={gain_db:.2f}dB,"
            f"alimiter=limit={ceiling:.4f}:attack=5:release=50:level=disabled")


def _measure(src: str) -> dict:
    err = run_ffmpeg(["-i", src, "-af", filter_chain(), "-f", "null", "-"]).stderr
    text = err.decode("utf-8", "replace")
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", text, re.S)
    if not match:
        raise RuntimeError("loudnorm printed no measurement")
    return json.loads(match.group(0))


def master(src: str, dest: str) -> str:
    """``src`` -> mastered 16-bit WAV at ``dest`` (mono, 44.1 kHz)."""
    measured = _measure(src)
    try:
        chain = filter_chain(TARGET_LUFS - float(measured["input_i"]))
    except (KeyError, ValueError):
        # Silent input ("-inf"): nothing to normalise, just shape the tone.
        chain = VOICE_CHAIN
    run_ffmpeg(["-y", "-i", src, "-af", chain, "-ar", str(OUTPUT_RATE), "-ac", "1",
                "-c:a", "pcm_s16le", dest])
    if not os.path.exists(dest) or os.path.getsize(dest) < 100:
        raise RuntimeError("mastering produced no audio")
    return dest


def loudness(path: str) -> tuple[float, float]:
    """(integrated LUFS, true peak dBTP) of a file - used to check the result."""
    err = run_ffmpeg(["-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"]).stderr
    text = err.decode("utf-8", "replace")
    summary = text[text.rfind("Summary:"):]
    integrated = float(re.search(r"I:\s*(-?[\d.]+|-inf) LUFS", summary).group(1))
    peak = float(re.search(r"Peak:\s*(-?[\d.]+|-inf) dBFS", summary).group(1))
    return integrated, peak
