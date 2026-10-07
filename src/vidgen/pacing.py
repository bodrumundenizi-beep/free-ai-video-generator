"""How long a video will be, and making it a chosen length.

A video is as long as its voice lines plus the pause after each. To land on a
target the app can change two things, and tries the gentler one first:

1. the pauses between scenes, anywhere from PAD_MIN to PAD_MAX;
2. the speed of the voice, by at most 15% either way, pitch unchanged.

Beyond that a voice stops sounding like a person, so a script that is far too
long or short for the target is rendered as close as it gets and the user is
told how many words to cut or add.

Scenes with their own Duration: or Padding: line are "fixed": the script said
exactly what it wants, so they are counted but never touched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TARGETS = {"Auto": None, "15 s": 15.0, "30 s": 30.0, "60 s": 60.0}
DEFAULT_TARGET = "Auto"

PAD_MIN, PAD_MAX = 0.1, 1.0
TEMPO_MIN, TEMPO_MAX = 0.87, 1.15
CLOSE_ENOUGH = 0.3  # seconds: nobody notices, and encoders round to a frame anyway

# Measured from real voice lines (see tests): a neural voice at its normal
# rate reads about this many characters a second, pauses not included.
CHARS_PER_SECOND = 15.6
# What the voice engine inserts, before the persona's pause scale (voice.py).
PAUSE_STOP, PAUSE_COMMA = 0.26, 0.12


@dataclass(frozen=True)
class Fit:
    tempo: float  # 1.0 = unchanged; above 1 is faster
    padding: float  # pause after each flexible scene
    achieved: float  # the video length these give
    reached: bool  # whether that is the target


def fit(voice_seconds: float, flexible_scenes: int, fixed_seconds: float, target: float) -> Fit:
    """The pause and voice speed that bring the video to ``target`` seconds.

    ``voice_seconds`` is the total speech in the flexible scenes,
    ``fixed_seconds`` everything that may not change.
    """
    n = flexible_scenes
    if n <= 0 or voice_seconds <= 0:
        return Fit(1.0, 0.0, fixed_seconds, abs(fixed_seconds - target) <= CLOSE_ENOUGH)

    room = target - fixed_seconds  # what the flexible scenes have to fill
    padding = (room - voice_seconds) / n
    if PAD_MIN <= padding <= PAD_MAX:
        return Fit(1.0, padding, target, True)

    # Pauses alone can't do it: hold them at the nearer limit and move the voice.
    padding = PAD_MIN if padding < PAD_MIN else PAD_MAX
    speech_room = room - n * padding
    wanted = voice_seconds / speech_room if speech_room > 0 else float("inf")
    tempo = min(max(wanted, TEMPO_MIN), TEMPO_MAX)
    achieved = fixed_seconds + voice_seconds / tempo + n * padding
    return Fit(tempo, padding, achieved, abs(achieved - target) <= CLOSE_ENOUGH)


def settle_padding(voice_seconds: float, flexible_scenes: int, fixed_seconds: float,
                   target: float) -> float:
    """The pause that lands on ``target`` given the voice as it actually came out.

    Speeding audio up never gives exactly the length asked for, so the pauses
    absorb the last fraction of a second.
    """
    if flexible_scenes <= 0:
        return 0.0
    padding = (target - fixed_seconds - voice_seconds) / flexible_scenes
    return min(max(padding, PAD_MIN), PAD_MAX)


def words_to_change(achieved: float, target: float, words: int, voice_seconds: float) -> int:
    """About how many words to cut (positive) or add (negative) to reach ``target``."""
    if voice_seconds <= 0 or words <= 0:
        return 0
    return round((achieved - target) * words / voice_seconds)


def rate_factor(rate: str) -> float:
    """An edge-tts rate like "+8%" or "-2%" as a speed multiplier."""
    match = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s*%\s*", rate or "")
    return 1.0 + float(match.group(1)) / 100.0 if match else 1.0


def estimate_voice(text: str, rate: str = "+0%", pause_scale: float = 1.0,
                   enhanced: bool = True) -> float:
    """Roughly how long ``text`` takes to say, from the text alone."""
    text = " ".join((text or "").split())
    if not text:
        return 0.0
    seconds = len(text) / (CHARS_PER_SECOND * rate_factor(rate))
    if enhanced:  # studio voices get breath pauses; none after the last word
        inner = text[:-1]
        seconds += pause_scale * (PAUSE_STOP * len(re.findall(r"[.!?]+", inner))
                                  + PAUSE_COMMA * len(re.findall(r"[,;:]", inner)))
    return seconds


@dataclass(frozen=True)
class Estimate:
    total: float  # the video at its natural pace
    voice: float  # speech in the flexible scenes
    flexible: int  # scenes whose pause and speed may be adjusted
    fixed: float  # seconds in scenes that say exactly what they want


def estimate(scenes, persona, default_padding: float) -> Estimate:
    """Roughly how long the video will be, split the way ``fit`` needs it.

    ``scenes`` are parsed script scenes; ``persona`` has rate, pause_scale and
    enhanced (see voices.py).
    """
    voice = fixed = 0.0
    flexible = 0
    for scene in scenes:
        spoken = estimate_voice(scene.voice or "", persona.rate, persona.pause_scale,
                                persona.enhanced)
        if scene.duration is not None:
            fixed += scene.duration
        elif scene.padding is not None:
            fixed += max(spoken + scene.padding, 0.5)
        else:
            voice += spoken
            flexible += 1
    return Estimate(fixed + voice + flexible * default_padding, voice, flexible, fixed)


def describe(guess: Estimate, scenes: int, target: float | None) -> tuple[str, bool]:
    """(text for the Create page, whether it is a warning)."""
    plural = "scene" if scenes == 1 else "scenes"
    base = f"About {round(guess.total)} s · {scenes} {plural}"
    if target is None or scenes == 0:
        return base, False
    # The same rules the render applies, run on the estimate.
    result = fit(guess.voice, guess.flexible, guess.fixed, target)
    if result.reached:
        return f"{base} · will be fitted to {target:g} s", False
    problem = "too long" if result.achieved > target else "too short"
    return f"{base} · {problem} for {target:g} s", True
