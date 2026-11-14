"""The seven tones of a promo video, after /brag's tones.md (MIT, Shunit Haviv Hakimi).

A tone sets the type (font, weight, case, spacing), the pacing (how many scenes, how long
each holds) and the transitions. Fonts: Bricolage Grotesque and Atkinson Hyperlegible ship
with the app (OFL); Segoe UI, Georgia and Consolas are on every Windows PC.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tone:
    name: str
    label: str            # what the app shows
    about: str            # one line for the picker
    display: str          # CSS font-family for headlines
    body: str             # CSS font-family for small text
    weight: int           # headline weight
    case: str             # "none", "upper" or "sentence"
    spacing: str          # headline letter-spacing, CSS
    scenes: tuple[int, int]        # how many scenes (fewest, most)
    hold: tuple[float, float]      # seconds a scene holds (shortest, longest)
    transition: str       # "fade", "slide", "cut", "flash", "wipe", "zoom"
    transition_seconds: float
    entrance: str         # how a line arrives: "rise", "slam", "type", "fade"
    tilt: bool = False    # chaotic: some words tilted
    mono: str = "Consolas, 'Cascadia Mono', monospace"   # for numbers in yc-parody


DISPLAY = "'Bricolage', 'Segoe UI', sans-serif"
BODY = "'Atkinson', 'Segoe UI', sans-serif"

TONES = {t.name: t for t in (
    Tone("default", "Playful", "Clean and friendly, the good-vibes default",
         DISPLAY, BODY, 750, "none", "-0.02em", (4, 5), (3.0, 5.0), "slide", 0.4, "rise"),
    Tone("polished", "Polished", "Serious and elegant: fewer scenes, longer holds",
         DISPLAY, BODY, 400, "none", "0.01em", (3, 4), (4.0, 6.0), "fade", 0.7, "fade"),
    Tone("yc-parody", "Startup launch", "Deadpan startup energy, played completely straight",
         DISPLAY, BODY, 600, "sentence", "-0.01em", (4, 5), (3.0, 4.5), "cut", 0.15, "type"),
    Tone("chaotic", "Chaotic", "FAST, LOUD, ALL CAPS: a hype reel",
         DISPLAY, BODY, 800, "upper", "-0.03em", (6, 8), (1.6, 3.2), "flash", 0.12, "slam",
         tilt=True),
    Tone("deadpan", "Deadpan", "Calm and dry, lots of empty space, one thought at a time",
         BODY, BODY, 400, "none", "0em", (3, 4), (4.0, 6.5), "fade", 0.9, "fade"),
    Tone("cinematic", "Cinematic", "Trailer-scale: big type, dramatic reveals",
         "Georgia, 'Times New Roman', serif", BODY, 700, "upper", "0.08em", (4, 5), (3.0, 5.0),
         "zoom", 0.6, "fade"),
    Tone("app-store", "App Store", "Clean feature cards, smooth slides",
         DISPLAY, BODY, 600, "none", "-0.01em", (4, 6), (3.0, 4.5), "slide", 0.4, "rise"),
)}
DEFAULT = "default"


def get(name: str) -> Tone:
    return TONES.get(name, TONES[DEFAULT])
