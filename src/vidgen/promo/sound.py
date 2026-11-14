"""The promo video's sound: a music bed, a few quiet effects on the motion, cuts on the beat.

After /brag's audio rules: big moments may move to a strong beat within 0.15 s, small
reveals within 0.10 s, and a line never arrives sooner than the one before it can be read.
Effects sit under the music; the whole mix is brought to -14 LUFS like the app's videos.
Beats come from vidgen.audio (the same finder the normal videos use), worked out when needed.
"""
from __future__ import annotations

import os
import random
import tempfile

import numpy as np

from .. import audio, mastering
from . import plan, tones

RATE = 44100
CUT_REACH, REVEAL_REACH = 0.15, 0.10
MUSIC_GAIN, VOICE_DUCK = 0.5, 0.18
TARGET_LUFS = -25.0   # Deniz picked it by ear (2026-11-14): -14, the normal videos' level, was too loud here
# Each effect: (files, level). Soft levels: they sit under the music.
EFFECTS = {
    "key": (["keypress-001.wav", "keypress-002.wav", "keypress-003.wav", "keypress-004.wav",
             "keypress-005.wav", "keypress-006.wav"], 0.22),
    "card": (["card-slide-1.ogg"], 0.30),
    "click": (["click_003.ogg"], 0.55),
    "thud": (["impactSoft_medium_001.ogg"], 0.45),
    "bell": (["impactBell_heavy_000.ogg"], 0.32),
}
# The music that suits each tone: a prefix of the app's track names (assets/music).
MOODS = {"polished": "calm", "deadpan": "calm", "cinematic": "dramatic"}


def pick_music(tone: str, folder: str) -> str | None:
    """A track from the app's music folder that suits the tone."""
    mood = MOODS.get(tone, "upbeat")
    try:
        names = sorted(n for n in os.listdir(folder) if n.lower().endswith((".mp3", ".wav", ".ogg")))
    except OSError:
        return None
    suited = [n for n in names if n.lower().startswith(mood)] or names
    return os.path.join(folder, suited[0]) if suited else None


def snap(scenes, beat_times, tone_name: str):
    """Scenes with their cuts, and the cards' arrivals, moved onto nearby beats. A scene
    never gets shorter than reading needs; a card never arrives before the last is read."""
    if not beat_times:
        return scenes
    tone = tones.get(tone_name)
    needs = [plan._need(s.kind, s.data, tone) for s in scenes]
    durations = audio.snap_cuts([s.duration for s in scenes], needs, beat_times, CUT_REACH)
    out, at = [], 0.0
    for scene, length in zip(scenes, durations):
        data = dict(scene.data)
        if scene.kind == "cards":
            times, earliest = [], 0.0
            for i, when in enumerate(data["times"]):
                when = max(when, earliest)
                near = [b - at for b in beat_times
                        if abs(b - at - when) <= REVEAL_REACH and b - at >= earliest]
                when = min(near, key=lambda b: abs(b - when)) if near else when
                times.append(round(when, 3))
                earliest = when + plan.read_time(data["items"][i]["title"])
            data["times"] = times
        out.append(plan.Scene(scene.kind, round(at, 3), round(length, 3), data))
        at += length
    return out


def events(scenes, tone_name: str) -> list[tuple[str, float]]:
    """(effect, seconds) for the motion in scene.html: the same timings as its updaters."""
    tone = tones.get(tone_name)
    tr = tone.transition_seconds
    found, chooser = [], random.Random(7)
    first_shot = True
    for s in scenes:
        if s.kind == "hook" and tone.entrance == "type":
            text = s.data["text"]
            typing = min(1.6, 0.045 * len(text))
            for i in range(0, len(text), 2):   # every other letter: a key a letter is too busy
                if text[i] != " ":
                    found.append((f"key{chooser.randrange(6)}", s.start + tr * 0.5 + typing * i / len(text)))
        elif s.kind == "shot" and first_shot:
            found.append(("thud", s.start + tr * 0.4))
            first_shot = False
        elif s.kind == "feature" and tone.entrance == "slam":
            found.append(("thud", s.start + tr * 0.5))
        elif s.kind == "cards":
            found += [("card", s.start + t) for t in s.data["times"]]
        elif s.kind == "flow":
            found.append(("click", s.start + tr + 0.9 + 0.9))
        elif s.kind == "outro":
            found.append(("bell", s.start + tr * 0.5))
    return sorted(found, key=lambda e: e[1])


def _stereo(path: str) -> np.ndarray:
    out = mastering.run_ffmpeg(["-i", path, "-f", "f32le", "-ac", "2", "-ar", str(RATE), "-"]).stdout
    return np.frombuffer(out, dtype=np.float32).reshape(-1, 2).copy()


def _effect(name: str, folder: str) -> tuple[np.ndarray, float]:
    base = name.rstrip("0123456789")
    files, level = EFFECTS[base]
    index = int(name[len(base):] or 0) % len(files)
    return _stereo(os.path.join(folder, files[index])), level


def mix(scenes, total: float, tone_name: str, out_wav: str, music: str | None = None,
        sfx_folder: str | None = None, voice: str | None = None,
        effects_volume: float = 1.0, volume: float = 1.0) -> str:
    """The soundtrack as a stereo WAV: music (looped, faded), effects, an optional voice
    (the music dips under it), then brought to -14 LUFS with a limiter. ``effects_volume``:
    the user's slider for the effects, 0 (off) to 1.5. ``volume``: the slider for the whole
    video, 0.25 to 1.5 (1.0 is -25 LUFS)."""
    effects_volume = max(0.0, min(1.5, effects_volume))
    n = int(total * RATE)
    track = np.zeros((n, 2), dtype=np.float32)
    if music:
        bed = _stereo(music)
        if len(bed):
            reps = int(np.ceil(n / len(bed)))
            bed = np.tile(bed, (reps, 1))[:n]
            ramp = np.ones(n, dtype=np.float32)
            fade_in, fade_out = int(0.4 * RATE), int(1.6 * RATE)
            ramp[:fade_in] = np.linspace(0, 1, fade_in)
            ramp[-fade_out:] = np.minimum(ramp[-fade_out:], np.linspace(1, 0, fade_out))
            gain = np.full(n, MUSIC_GAIN, dtype=np.float32)
            if voice:
                gain[:] = VOICE_DUCK
            track += bed * (ramp * gain)[:, None]
    if voice:
        spoken = _stereo(voice)[:n]
        track[:len(spoken)] += spoken
    if sfx_folder and effects_volume > 0:
        cache = {}
        for name, at in events(scenes, tone_name):
            if name not in cache:
                cache[name] = _effect(name, sfx_folder)
            sound, level = cache[name]
            start = int(at * RATE)
            if 0 <= start < n:
                part = sound[: n - start]
                track[start:start + len(part)] += part * level * effects_volume
    raw = os.path.join(tempfile.mkdtemp(prefix="aivs-promo-"), "raw.wav")
    _write(raw, track)
    try:
        measured, _peak = mastering.loudness(raw)
        target = TARGET_LUFS + 20 * np.log10(max(0.25, min(1.5, volume)))
        gain_db = target - measured if np.isfinite(measured) else 0.0
    except (ValueError, AttributeError, RuntimeError):
        gain_db = 0.0   # silence: nothing to bring up
    mastering.run_ffmpeg(["-y", "-i", raw, "-af",
                          f"volume={gain_db:.2f}dB,alimiter=limit=0.84:attack=5:release=50:level=disabled",
                          "-ar", str(RATE), "-c:a", "pcm_s16le", out_wav])
    return out_wav


def _write(path: str, samples: np.ndarray) -> None:
    import wave

    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as fh:
        fh.setnchannels(2)
        fh.setsampwidth(2)
        fh.setframerate(RATE)
        fh.writeframes(pcm.tobytes())


def beats_for(music: str | None, total: float) -> list[float]:
    """The music's beats over the video, or [] without music."""
    if not music:
        return []
    try:
        return audio.beats(music, total)
    except RuntimeError:
        return []


def mux(video: str, sound: str, out_path: str) -> str:
    """Put the soundtrack under the video (the picture is copied, not encoded again)."""
    mastering.run_ffmpeg(["-y", "-i", video, "-i", sound, "-map", "0:v", "-map", "1:a",
                          "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
                          "-movflags", "+faststart", out_path])
    return out_path
