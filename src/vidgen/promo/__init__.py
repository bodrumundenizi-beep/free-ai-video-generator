"""Promo videos (5.3): a 15-25 second launch video about the user's own app or website,
from its screenshots and a short form. Inspired by /brag (MIT, Shunit Haviv Hakimi).

    promo.make(Promo(...), "out.mp4")  ->  checks, then draws every frame in a headless
    Chromium (browser.py) from scene.html, and saves an MP4 without sound (sound: piece C).
"""
from __future__ import annotations

import colorsys
import json
import os
import shutil
import sys
import tempfile

from . import browser, colours, plan, sound, tones
from .browser import BrowserError
from .colours import Palette
from .plan import SIZES, Feature, Promo, Scene

__all__ = ["BrowserError", "Feature", "Palette", "Promo", "PromoError", "Scene", "SIZES",
           "build_page", "check", "make", "tones"]

FPS = 30


class PromoError(Exception):
    """The promo can't be made as it is. The message is for the user."""


def here(*parts) -> str:
    if getattr(sys, "frozen", False):   # the built app: the files sit in the bundle
        base = os.path.join(getattr(sys, "_MEIPASS", ""), "vidgen", "promo")
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, *parts)


def _extras(palette: Palette) -> dict:
    """The colours the page derives: text on the accent, the glow, card and edge tints."""
    bg = colours._rgb(palette.background)
    accent = colours._rgb(palette.accent)
    dark = colours.luminance(bg) < 0.4
    on_accent = "#FFFFFF" if colours.contrast((255, 255, 255), accent) >= 3 else "#0B0D12"
    lift = (255, 255, 255) if dark else (0, 0, 0)
    card = tuple(b * 0.9 + l * 0.1 for b, l in zip(bg, lift))
    edge = tuple(b * 0.8 + l * 0.2 for b, l in zip(bg, lift))
    h, light, s = colorsys.rgb_to_hls(*(v / 255 for v in accent))
    glow = "rgba(%d,%d,%d,%.2f)" % (*accent, 0.30 if dark else 0.18)
    return {"background": palette.background, "accent": palette.accent, "text": palette.text,
            "muted": palette.muted, "onaccent": on_accent, "glow": glow,
            "card": colours._hex(card), "edge": colours._hex(edge)}


def build_page(promo: Promo, folder: str, palette: Palette | None = None,
               logo: str = "", scenes: list[Scene] | None = None) -> tuple[str, list[Scene]]:
    """Write the page, its fonts and pictures into ``folder``. (page path, scenes)."""
    scenes = scenes or plan.storyboard(promo)
    palette = palette or (colours.from_screenshots(promo.shots) if promo.shots
                          else colours.FALLBACK)
    os.makedirs(os.path.join(folder, "fonts"), exist_ok=True)
    for name in os.listdir(here("fonts")):
        if name.endswith(".woff2"):
            shutil.copy2(here("fonts", name), os.path.join(folder, "fonts", name))
    copied = {}

    def local(path):
        if not path:
            return ""
        if path not in copied:
            name = f"pic{len(copied)}{os.path.splitext(path)[1].lower() or '.png'}"
            shutil.copy2(path, os.path.join(folder, name))
            copied[path] = name
        return copied[path]

    tone = tones.get(promo.tone)
    data = {
        "size": SIZES.get(promo.aspect, SIZES["16:9"]),
        "tone": {"name": tone.name, "display": tone.display, "body": tone.body,
                 "weight": tone.weight, "case": tone.case, "spacing": tone.spacing,
                 "transition": tone.transition, "transition_seconds": tone.transition_seconds,
                 "entrance": tone.entrance, "tilt": tone.tilt},
        "palette": _extras(palette),
        "logo": local(logo),
        # F2: every screenshot, for backdrops and card pictures, with how much to darken each
        "shots": [local(p) for p in promo.shots],
        "veils": [colours.backdrop_veil(p, palette) for p in promo.shots],
        "scenes": [{"kind": s.kind, "start": s.start, "duration": s.duration,
                    "data": {k: (local(v) if k in ("image", "after") else v)
                             for k, v in s.data.items()}} for s in scenes],
    }
    with open(here("scene.html"), encoding="utf-8") as fh:
        html = fh.read().replace("PROMO_DATA", json.dumps(data, ensure_ascii=False))
    page = os.path.join(folder, "promo.html")
    with open(page, "w", encoding="utf-8") as fh:
        fh.write(html)
    return page, scenes


def check(promo: Promo, palette: Palette | None = None, browser_path: str | None = None) -> list[str]:
    """Everything that would spoil the video, before a frame is drawn: lines too short to
    read, text that doesn't fit even when shrunk, anything off the frame. [] when fine."""
    folder = tempfile.mkdtemp(prefix="aivs-promo-")
    try:
        page, scenes = build_page(promo, folder, palette)
        issues = plan.reading_problems(scenes, promo.tone)
        size = SIZES.get(promo.aspect, SIZES["16:9"])
        with browser.Browser(*size, path=browser_path) as b:
            b.open(page)
            issues += b.evaluate("window.promoIssues") or []
        return issues
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def make(promo: Promo, out_path: str, palette: Palette | None = None, logo: str = "",
         music: str | None = None, sfx_folder: str | None = None, voice: str | None = None,
         effects_volume: float = 1.0, volume: float = 1.0, should_stop=None, progress=None, gpu: bool = False,
         browser_path: str | None = None) -> list[Scene]:
    """Check, then draw the video and lay its sound under it. ``music``: a track (cuts land
    on its beats); ``sfx_folder``: the effects (None: no effects); ``voice``: an optional
    narration (off by default, as in /brag). Raises PromoError or BrowserError."""
    if not promo.name.strip() or not promo.hook.strip():
        raise PromoError("A promo video needs at least a name and a first line.")
    folder = tempfile.mkdtemp(prefix="aivs-promo-")
    try:
        scenes = plan.storyboard(promo)
        scenes = sound.snap(scenes, sound.beats_for(music, plan.total(scenes) + 1), promo.tone)
        page, scenes = build_page(promo, folder, palette, logo, scenes)
        issues = plan.reading_problems(scenes, promo.tone)
        size = SIZES.get(promo.aspect, SIZES["16:9"])
        with browser.Browser(*size, path=browser_path) as b:
            b.open(page)
            issues += b.evaluate("window.promoIssues") or []
        if issues:
            raise PromoError("The video can't be made as it is:\n- " + "\n- ".join(issues))
        seconds = plan.total(scenes)
        silent = os.path.join(folder, "picture.mp4")
        browser.record(page, size, seconds, silent, FPS, gpu, should_stop, progress, browser_path)
        if music or sfx_folder or voice:
            wav = sound.mix(scenes, seconds, promo.tone, os.path.join(folder, "sound.wav"),
                            music, sfx_folder, voice, effects_volume, volume)
            sound.mux(silent, wav, out_path)
        else:
            shutil.move(silent, out_path)
        return scenes
    finally:
        shutil.rmtree(folder, ignore_errors=True)
