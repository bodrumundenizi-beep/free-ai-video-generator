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


def speech_window(voice, fps: int = 22050):
    """(start, end) seconds of ``voice`` to keep: the speech plus a little air."""
    bounds = speech_bounds(voice.to_soundarray(fps=fps), fps)
    if bounds is None:
        return 0.0, voice.duration
    start = max(0.0, bounds[0] - SPEECH_HEAD)
    end = min(voice.duration, bounds[1] + SPEECH_TAIL)
    if end - start < 0.1:
        return 0.0, voice.duration
    return start, end


def trim_to_speech(voice, fps: int = 22050):
    """``voice`` without the silence edge-tts adds before and after it."""
    start, end = speech_window(voice, fps)
    if (start, end) == (0.0, voice.duration):
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


# --- sound effects: made here, so there is nothing to download or license ----------------

SFX_RATE = 44100
WHOOSH_LENGTH, WHOOSH_GAIN, WHOOSH_LEAD = 0.45, 0.10, 0.32  # it peaks just before the cut
POP_LENGTH, POP_GAIN = 0.09, 0.22


def whoosh() -> np.ndarray:
    """Soft filtered noise that swells and fades: stereo samples at SFX_RATE."""
    n = int(WHOOSH_LENGTH * SFX_RATE)
    rng = np.random.default_rng(7)   # the same whoosh every time
    noise = rng.standard_normal(n)
    smooth = np.convolve(noise, np.ones(24) / 24, mode="same")   # a dull, airy hiss
    t = np.linspace(0, 1, n)
    envelope = np.sin(np.pi * np.clip(t / 0.7, 0, 1) / 2) ** 2 * np.clip((1 - t) / 0.3, 0, 1)
    wave = smooth * envelope
    wave *= WHOOSH_GAIN / max(np.abs(wave).max(), 1e-9)
    pan = np.linspace(-0.4, 0.4, n)   # it moves across, left to right
    return np.stack([wave * (1 - pan), wave * (1 + pan)], axis=1) / 1.4


def pop() -> np.ndarray:
    """A short soft blip, falling in pitch: stereo samples at SFX_RATE."""
    n = int(POP_LENGTH * SFX_RATE)
    t = np.arange(n) / SFX_RATE
    pitch = np.linspace(900, 320, n)
    wave = np.sin(2 * np.pi * np.cumsum(pitch) / SFX_RATE) * np.exp(-t * 45)
    wave *= POP_GAIN / max(np.abs(wave).max(), 1e-9)
    return np.stack([wave, wave], axis=1)


def effect_times(cuts, card_starts, total: float):
    """[(kind, start)]: a whoosh at each cut between scenes, a pop as each card appears."""
    times = [("whoosh", cut - WHOOSH_LEAD) for cut in cuts if cut > WHOOSH_LEAD]
    times += [("pop", start) for start in card_starts]
    return [(kind, start) for kind, start in times if 0 <= start < total - 0.05]


def sound_effects(cuts, card_starts, total: float):
    """The effects as (clip, start) pairs, ready for mix_audio."""
    from moviepy import AudioArrayClip

    made = {"whoosh": whoosh(), "pop": pop()}
    return [(AudioArrayClip(made[kind], fps=SFX_RATE), start)
            for kind, start in effect_times(cuts, card_starts, total)]


# --- beats: so scene changes can land on the music -----------------------------------------

BEAT_REACH = 0.25   # a cut moves at most this far to meet a beat
BEAT_GAP = 0.3      # beats closer together than this count as one


def onsets(samples: np.ndarray, rate: int) -> list[float]:
    """Where the music hits (a drum, a chord): sudden rises in loudness, in seconds."""
    hop = max(rate // 100, 1)
    frames = len(samples) // hop
    if frames < 3:
        return []
    energy = np.sqrt((samples[:frames * hop].reshape(frames, hop) ** 2).mean(axis=1))
    rise = np.maximum(np.diff(energy, prepend=energy[0]), 0)
    level = rise.mean() + 1.5 * rise.std()
    found, last = [], -1.0
    for i in range(1, frames - 1):
        t = i * hop / rate
        if rise[i] > level and rise[i] >= rise[i - 1] and rise[i] >= rise[i + 1] \
                and t - last >= BEAT_GAP:
            found.append(t)
            last = t
    return found


def beats(path: str, total: float) -> list[float]:
    """The music's beats over ``total`` seconds; a short track loops, as music_track does."""
    from . import mastering

    rate = 11025
    out = mastering.run_ffmpeg(["-i", path, "-t", f"{total + 1:.1f}", "-ac", "1", "-ar",
                                str(rate), "-f", "f32le", "-"]).stdout
    samples = np.frombuffer(out, dtype=np.float32)
    found, length = onsets(samples, rate), len(samples) / rate
    if not found or length <= 0:
        return []
    times, loop = [], 0.0
    while loop < total:
        times += [t + loop for t in found if t + loop < total]
        loop += length
    return times


def snap_cuts(durations, shortest, beat_times, reach: float = BEAT_REACH):
    """Scene lengths with each cut moved to the nearest beat within ``reach``.

    A scene never gets shorter than ``shortest`` (its words); the last scene keeps its
    length, so only the cuts between scenes move.
    """
    out, at = list(durations), 0.0
    for i in range(len(out) - 1):
        cut = at + out[i]
        near = [b for b in beat_times if abs(b - cut) <= reach and b - at >= shortest[i]]
        if near:
            out[i] = min(near, key=lambda b: abs(b - cut)) - at
        at += out[i]
    return out
