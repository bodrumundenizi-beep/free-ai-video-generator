"""The soundtrack: voiceovers on the timeline, and music ducked beneath them.

Ducking is side-chaining with perfect foresight. Every voice's start and end
are known before encoding, so the music can begin dipping just *before* each
line rather than reacting to it, the way a live compressor would.

The gain curve is computed once on a 100 Hz grid and interpolated per audio
sample, so the ramps are smooth - no clicks - and cost almost nothing.
"""

from __future__ import annotations

import numpy as np

BASE_GAIN = 0.25  # music level in the pauses
DUCK_GAIN = 0.09  # music level under a voice
ATTACK = 0.10  # the dip starts this long before each line
RELEASE = 0.40  # and recovers over this long after it
GRID_HZ = 100
FADE_IN = 1.0
FADE_OUT = 1.5
TRIM_FADE = 0.15  # fade on a voice cut short by a forced Duration:

# edge-tts pads every file with ~0.2 s of silence before the speech and ~0.85 s
# after it. Left in, that silence would stack on top of Padding: - so even
# "Padding: 0s" couldn't pace tighter than a second - and the music would stay
# ducked while nobody is talking. Voices are trimmed to the speech, keeping a
# little air so words don't start or stop abruptly.
SPEECH_HEAD = 0.05
SPEECH_TAIL = 0.12
SILENCE_RATIO = 0.05  # below 5% of the loudest 20 ms counts as silence


def merge_intervals(intervals, bridge: float = ATTACK + RELEASE):
    """Sorted, merged (start, end) spans of speech.

    Gaps shorter than ``bridge`` are merged too: in a gap that short the music
    would dip, rise and dip again - audible "pumping" - without ever reaching
    its pause level. Real pauses, like a 0.5 s Padding:, still breathe.
    """
    spans = sorted((s, e) for s, e in intervals if e > s)
    merged = []
    for start, end in spans:
        if merged and start - merged[-1][1] < bridge:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def gain_curve(speech, total: float):
    """(times, gains) sampled at GRID_HZ over [0, total]."""
    count = int(np.ceil(total * GRID_HZ)) + 1
    times = np.arange(count) / GRID_HZ
    gains = np.full(count, BASE_GAIN)
    for start, end in merge_intervals(speech):
        falling = np.clip((times - (start - ATTACK)) / ATTACK, 0.0, 1.0)
        rising = np.clip(((end + RELEASE) - times) / RELEASE, 0.0, 1.0)
        depth = np.minimum(falling, rising)  # 1 while speaking, ramps at the edges
        gains = np.minimum(gains, BASE_GAIN - (BASE_GAIN - DUCK_GAIN) * depth)
    return times, gains


def music_track(path: str, total: float, speech, keep):
    """The music file looped or trimmed to ``total``, ducked under ``speech``.

    ``keep`` registers the opened file so the render can close it - an unclosed
    reader holds the file open, which on Windows stops the temp folder being
    deleted (WinError 32).
    """
    from moviepy import AudioFileClip, afx

    music = keep(AudioFileClip(path))
    if music.duration < total:
        music = music.with_effects([afx.AudioLoop(duration=total)])
    else:
        music = music.subclipped(0, total)

    times, gains = gain_curve(speech, total)

    def duck(get_frame, t):
        frame = get_frame(t)
        gain = np.interp(np.asarray(t, dtype=float), times, gains)
        return frame * (gain.reshape(-1, 1) if frame.ndim == 2 else gain)

    music = music.transform(duck, keep_duration=True)
    return music.with_effects([
        afx.AudioFadeIn(min(FADE_IN, total / 4)),
        afx.AudioFadeOut(min(FADE_OUT, total / 4)),
    ])


def speech_bounds(samples: np.ndarray, fps: int):
    """(start, end) seconds of the audible part of ``samples``, or None if silent.

    ``samples`` is mono or (N, channels). Uses a 20 ms RMS envelope, so a
    single click can't pass for speech.
    """
    mono = np.abs(samples.mean(axis=1) if samples.ndim == 2 else samples)
    if mono.size == 0:
        return None
    window = max(1, int(fps * 0.02))
    rms = np.sqrt(np.convolve(mono ** 2, np.ones(window) / window, mode="same"))
    peak = rms.max()
    if peak <= 0:
        return None
    loud = np.flatnonzero(rms > peak * SILENCE_RATIO)
    return loud[0] / fps, (loud[-1] + 1) / fps


def trim_to_speech(voice, fps: int = 22050):
    """``voice`` without the silence edge-tts adds before and after it."""
    bounds = speech_bounds(voice.to_soundarray(fps=fps), fps)
    if bounds is None:
        return voice
    start = max(0.0, bounds[0] - SPEECH_HEAD)
    end = min(voice.duration, bounds[1] + SPEECH_TAIL)
    if end - start < 0.1:
        return voice
    return voice.subclipped(start, end)


def trim_voice(voice, length: float):
    """A voice cut to ``length`` with a short fade, so it doesn't end on a click."""
    from moviepy import afx

    return voice.subclipped(0, length).with_effects([afx.AudioFadeOut(TRIM_FADE)])


def mix_audio(voices, music, total: float):
    """One soundtrack from (clip, start) voices and optional music, or None."""
    from moviepy import CompositeAudioClip

    layers = [clip.with_start(start) for clip, start in voices]
    if music is not None:
        layers.append(music)
    if not layers:
        return None
    return CompositeAudioClip(layers).with_duration(total)
