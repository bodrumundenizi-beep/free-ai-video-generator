"""Where every shot sits on the finished video's timeline.

Scene i starts at B_i = D_0 + ... + D_(i-1) and its voice starts exactly there,
so voices never overlap and the video is Sum(D) long - the same length it would
be with hard cuts.

Crossfades are centred on each cut. The fade between scenes i-1 and i lasts
X_i, and each side's picture runs X_i / 2 past the cut to cover it, so neither
scene loses time and the dissolve reads as one soft cut. X_i is capped at half
of either neighbouring scene so a short scene can't be swallowed by its fades.

Pure: no MoviePy, so it can be tested on its own.
"""

from __future__ import annotations

from dataclasses import dataclass

CROSSFADE = 0.3


@dataclass(frozen=True)
class Shot:
    scene: int  # index into the scene list
    part: int  # 0, or 1 for the second clip of a split scene
    start: float  # timeline position where the shot begins
    end: float
    fade_in: float  # crossfade over the previous shot; 0 for a hard cut

    @property
    def length(self) -> float:
        return self.end - self.start


def boundaries(durations) -> list[float]:
    """B_0 ... B_n: where each scene starts, plus the end of the video."""
    points = [0.0]
    for duration in durations:
        points.append(points[-1] + duration)
    return points


def crossfades(durations, max_fade: float = CROSSFADE) -> list[float]:
    """X_0 ... X_n: the fade at each boundary. The outer two are always 0."""
    count = len(durations)
    fades = [0.0] * (count + 1)
    for i in range(1, count):
        fades[i] = min(max_fade, durations[i - 1] / 2, durations[i] / 2)
    return fades


def shot_spans(durations, parts, max_fade: float = CROSSFADE) -> list[Shot]:
    """Lay every shot on the timeline.

    ``parts[i]`` is 1, or 2 when a long scene cuts between two clips. The
    second clip starts at the scene's midpoint with a hard cut - the brief asks
    for pace there, and a dissolve inside one scene would read as a new one.
    """
    if len(parts) != len(durations):
        raise ValueError("parts and durations must be the same length")
    points = boundaries(durations)
    fades = crossfades(durations, max_fade)
    shots = []
    for i, duration in enumerate(durations):
        left = points[i] - fades[i] / 2
        right = points[i + 1] + fades[i + 1] / 2
        if parts[i] == 2:
            middle = points[i] + duration / 2
            shots.append(Shot(i, 0, left, middle, fades[i]))
            shots.append(Shot(i, 1, middle, right, 0.0))
        else:
            shots.append(Shot(i, 0, left, right, fades[i]))
    return shots
