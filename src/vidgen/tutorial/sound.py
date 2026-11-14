"""A tutorial's sound (5.4, plan E): the steps read aloud, a click at every click, quiet music
that dips while the voice speaks, at -25 LUFS like promos (Deniz chose it by ear).

The voice is the app's own (voice.generate_voiceover_timed, offline), each step's line placed
just after the step starts; a line that would run into the next one waits for it to finish.
"""
from __future__ import annotations

import os
import shutil
import tempfile

import numpy as np

from .. import mastering
from ..promo.sound import MUSIC_GAIN, RATE, TARGET_LUFS, VOICE_DUCK, _stereo, _write

CLICK = "click_003.ogg"   # the reference's click (HOUSE_STYLE.md)
CLICK_LEVEL = 0.7
VOICE_AFTER = 0.35        # seconds after a step starts that its line begins
TITLE_AT = 0.25           # the title is read over the title card
GAP = 0.25                # at least this between two lines
DUCK_RAMP = 0.3           # seconds the music takes to dip and come back


def plain(text: str) -> str:
    return " ".join(str(text).replace("**", "").split())


def lines(timeline: dict) -> list[tuple[float, str]]:
    """(time, words): the title over its card, then each step's line."""
    out = []
    title = plain(timeline.get("title", {}).get("title", ""))
    if title:
        out.append((TITLE_AT, title.rstrip(".") + "."))
    for step in timeline.get("steps", []):
        out.append((step["at"] + VOICE_AFTER, plain(step["text"]).rstrip(".") + "."))
    return out


def voice_track(timeline: dict, say, length: float) -> tuple[np.ndarray, list]:
    """The spoken lines laid on the tutorial's clock: (stereo samples, [(start, end)] spoken).
    ``say(text)`` returns the path of a WAV of it."""
    n = int(length * RATE)
    track = np.zeros((n, 2), dtype=np.float32)
    spoken, free_at = [], 0.0
    for at, text in lines(timeline):
        sound = _stereo(say(text))
        start = max(at, free_at)          # never on top of the line before
        begin = int(start * RATE)
        if begin >= n:
            break
        part = sound[: n - begin]
        track[begin:begin + len(part)] += part
        end = start + len(part) / RATE
        spoken.append((start, end))
        free_at = end + GAP
    return track, spoken


def duck(n: int, spoken) -> np.ndarray:
    """The music's level: MUSIC_GAIN, dipping smoothly to VOICE_DUCK while the voice speaks."""
    gain = np.full(n, MUSIC_GAIN, dtype=np.float32)
    ramp = int(DUCK_RAMP * RATE)
    for start, end in spoken:
        a, b = int(start * RATE), int(end * RATE)
        gain[max(a, 0):min(b, n)] = VOICE_DUCK
        for i in range(ramp):   # down before, up after
            k = i / ramp
            level = MUSIC_GAIN + (VOICE_DUCK - MUSIC_GAIN) * k
            if 0 <= a - ramp + i < n:
                gain[a - ramp + i] = min(gain[a - ramp + i], level)
            if 0 <= b + ramp - 1 - i < n:
                gain[b + ramp - 1 - i] = min(gain[b + ramp - 1 - i], level)
    return gain


def mix(timeline: dict, out_wav: str, say=None, music: str | None = None,
        sfx_folder: str | None = None) -> str:
    """The soundtrack as a stereo WAV, brought to -25 LUFS with a limiter."""
    length = float(timeline["length"])
    n = int(length * RATE)
    track = np.zeros((n, 2), dtype=np.float32)
    spoken = []
    if say is not None:
        voice, spoken = voice_track(timeline, say, length)
        track += voice
    if music:
        bed = _stereo(music)
        if len(bed):
            bed = np.tile(bed, (int(np.ceil(n / len(bed))), 1))[:n]
            fade = np.ones(n, dtype=np.float32)
            fade_in, fade_out = int(0.4 * RATE), int(1.6 * RATE)
            fade[:fade_in] = np.linspace(0, 1, fade_in)
            fade[-fade_out:] = np.minimum(fade[-fade_out:], np.linspace(1, 0, fade_out))
            track += bed * (fade * duck(n, spoken))[:, None]
    click_path = os.path.join(sfx_folder, CLICK) if sfx_folder else ""
    if click_path and os.path.isfile(click_path):
        click = _stereo(click_path) * CLICK_LEVEL
        for t, *_ in timeline.get("clicks", []):
            start = int(t * RATE)
            if 0 <= start < n:
                part = click[: n - start]
                track[start:start + len(part)] += part
    folder = tempfile.mkdtemp(prefix="aivs-tutorial-sound-")
    try:
        raw = os.path.join(folder, "raw.wav")
        _write(raw, track)
        try:
            measured, _peak = mastering.loudness(raw)
            gain_db = TARGET_LUFS - measured if np.isfinite(measured) else 0.0
        except (ValueError, AttributeError, RuntimeError):
            gain_db = 0.0   # silence: nothing to bring up
        mastering.run_ffmpeg(["-y", "-i", raw, "-af",
                              f"volume={gain_db:.2f}dB,alimiter=limit=0.84:attack=5:release=50:level=disabled",
                              "-ar", str(RATE), "-c:a", "pcm_s16le", out_wav])
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return out_wav


def app_voice(voice_choice: str, folder: str, log=lambda _m: None):
    """``say(text) -> wav path`` with the app's chosen voice, offline."""
    from .. import voice

    count = [0]

    def say(text: str) -> str:
        count[0] += 1
        path, _words = voice.generate_voiceover_timed(
            text, os.path.join(folder, f"line_{count[0]}.wav"), log, voice_choice)
        return path

    return say
