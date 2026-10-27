"""The opening of a tip video: a hook, then one line saying what is about to be done.

    "Here's how to make your PC faster in 20 seconds."
    "Right now we're going to turn on Hardware-accelerated GPU scheduling, so games run smoother."

Ready-made lines, picked by what the tip's page is about (KINDS), with the real seconds filled
in and the last few used left out, so videos in a row sound different. The Smart writer only
writes a hook for a tip that matched no page of the map, and check() refuses anything that
over-promises; then a "handy" line is used.
"""

from __future__ import annotations

import json
import math
import os
import random
import re

# (voice, big card text). {s} is the seconds the steps take, rounded up to 5.
LINES = {
    "speed": [
        ("Here's how to make your PC faster in {s} seconds.", "MAKE YOUR PC FASTER"),
        ("Your PC has a speed boost switched off. Here's where it is.", "HIDDEN SPEED BOOST"),
        ("Unlock your PC's full potential in {s} seconds.", "UNLOCK YOUR PC"),
        ("Stop Windows from slowing your PC down.", "STOP THE SLOWDOWN"),
        ("Is your PC slow? Try this first.", "SLOW PC? TRY THIS"),
        ("One setting that makes Windows feel snappier.", "A SNAPPIER PC"),
    ],
    "gaming": [
        ("Get smoother games with one switch.", "SMOOTHER GAMES"),
        ("Gamers, turn this on right now.", "GAMERS, DO THIS"),
        ("Squeeze more performance out of your games in {s} seconds.", "MORE GAME PERFORMANCE"),
        ("Your games could run better. Here's how.", "BETTER GAMING"),
        ("The setting every PC gamer should check.", "CHECK THIS SETTING"),
    ],
    "battery": [
        ("Make your laptop battery last longer in {s} seconds.", "LONGER BATTERY"),
        ("Your battery is draining faster than it should.", "STOP BATTERY DRAIN"),
        ("Get more hours out of every charge.", "MORE HOURS PER CHARGE"),
        ("Laptop dying too fast? Do this.", "LAPTOP DYING FAST?"),
        ("A quick battery fix most people never try.", "QUICK BATTERY FIX"),
    ],
    "storage": [
        ("Free up space on your PC in {s} seconds.", "FREE UP SPACE"),
        ("Your PC is full of files it doesn't need.", "CLEAR THE CLUTTER"),
        ("Running out of storage? Start here.", "OUT OF STORAGE?"),
        ("Let Windows clean up after itself.", "AUTO CLEAN-UP"),
        ("Get gigabytes back without deleting your stuff.", "GET SPACE BACK"),
    ],
    "privacy": [
        ("Windows shares more than you think. Here's how to stop it.", "STOP THE SHARING"),
        ("Take back your privacy in {s} seconds.", "TAKE BACK YOUR PRIVACY"),
        ("Check this privacy setting today.", "CHECK YOUR PRIVACY"),
        ("Your PC might be telling Microsoft too much.", "TOO MUCH DATA?"),
        ("A privacy switch most people never find.", "HIDDEN PRIVACY SWITCH"),
    ],
    "eyes": [
        ("Your eyes will thank you for this one.", "EASY ON THE EYES"),
        ("Using your PC at night? Do this.", "PC AT NIGHT?"),
        ("Make your screen easier to look at in {s} seconds.", "EASIER ON THE EYES"),
        ("Tired eyes after a day at the screen? Try this.", "TIRED EYES?"),
        ("A small change your eyes will notice.", "YOUR EYES WILL NOTICE"),
    ],
    "looks": [
        ("Give your PC a fresh look in {s} seconds.", "A FRESH LOOK"),
        ("Make Windows look the way you want.", "YOUR WINDOWS, YOUR WAY"),
        ("Bored of how your PC looks? Change this.", "BORED OF YOUR PC?"),
        ("A quick way to make Windows look cleaner.", "A CLEANER WINDOWS"),
        ("Make your desktop feel new again.", "LIKE NEW AGAIN"),
    ],
    "sound": [
        ("Make your PC sound better in {s} seconds.", "BETTER SOUND"),
        ("Your sound settings are hiding something.", "HIDDEN SOUND SETTING"),
        ("Sound not right? Check this first.", "FIX YOUR SOUND"),
        ("Get your audio sorted in {s} seconds.", "SORT YOUR AUDIO"),
        ("The sound setting most people miss.", "THE ONE PEOPLE MISS"),
    ],
    "handy": [
        ("Here's a Windows trick most people don't know.", "A HIDDEN WINDOWS TRICK"),
        ("This setting saves you time every day.", "SAVE TIME DAILY"),
        ("Windows can do this, and almost nobody uses it.", "NOBODY USES THIS"),
        ("Here's how to do this in {s} seconds.", "QUICK WINDOWS TIP"),
        ("A quick Windows tip you'll actually use.", "QUICK WINDOWS TIP"),
        ("Stop doing this the slow way.", "THE FAST WAY"),
    ],
}

REASON = {
    "speed": "so your PC feels quicker", "gaming": "so games run smoother",
    "battery": "so your battery lasts longer", "storage": "so you get space back",
    "privacy": "so Windows keeps more to itself", "eyes": "so the screen is easier on your eyes",
    "looks": "so Windows looks the way you like", "sound": "so it sounds the way it should",
    "handy": "",
}

# What each page of the map is about; every other page is "handy".
KINDS = {
    **dict.fromkeys(["power", "power-options", "startup-apps", "task-manager", "visual-effects",
                     "system-properties", "resource-monitor", "defragment", "startup-folder",
                     "delivery-optimization", "system-information", "multitasking"], "speed"),
    **dict.fromkeys(["game-mode", "game-bar", "captures", "display-graphics", "game-controllers",
                     "dxdiag"], "gaming"),
    **dict.fromkeys(["battery-saver", "battery-use", "energy-recommendations",
                     "mobility-center"], "battery"),
    **dict.fromkeys(["storage", "storage-sense", "storage-recommendations", "disks",
                     "save-locations", "disk-cleanup", "recycle-bin", "downloads-folder",
                     "storage-spaces"], "storage"),
    **dict.fromkeys(["privacy", "privacy-general", "privacy-location", "privacy-camera",
                     "privacy-microphone", "privacy-notifications", "privacy-diagnostics",
                     "activity-history", "privacy-speech", "privacy-inking", "privacy-files",
                     "privacy-contacts", "privacy-calendar", "privacy-screenshots",
                     "app-diagnostics", "search-permissions", "find-my-device"], "privacy"),
    **dict.fromkeys(["nightlight", "text-size", "contrast-themes", "color-filters", "magnifier",
                     "mouse-pointer", "text-cursor", "display-calibration"], "eyes"),
    **dict.fromkeys(["personalization", "background", "colors", "themes", "lock-screen", "start",
                     "start-folders", "taskbar", "fonts", "fonts-folder", "dynamic-lighting",
                     "color-management", "display"], "looks"),
    **dict.fromkeys(["sound", "sound-devices", "volume-mixer", "default-mic", "default-speaker",
                     "sound-classic", "volume-mixer-classic", "audio-accessibility",
                     "hearing-devices"], "sound"),
}

RECENT = 10   # hooks remembered so videos in a row don't open the same way
_OFF = re.compile(r"\b(off|disable|disabling|stop|remove|hide|turn off|get rid)\b", re.I)
_TOO_MUCH = re.compile(r"\d|%|double|boost fps|\bfps\b|instantly|guarantee", re.I)


def kind_of(place) -> str:
    return KINDS.get(place.id, "handy") if place is not None else "handy"


def seconds(steps: int) -> int:
    """About how long the steps take, rounded up to 5 (at least 10)."""
    return max(10, math.ceil(steps * 3.5 / 5) * 5)


def pick(kind: str, secs: int, recent=(), rng=random):
    """(line as written, voice, card) for the hook: one of ``kind``'s lines not used lately.
    Lines with the seconds are left out over a minute: "in 75 seconds" sells nothing."""
    lines = [line for line in LINES.get(kind, LINES["handy"]) if secs <= 60 or "{s}" not in line[0]]
    fresh = [line for line in lines if line[0] not in recent] or lines
    voice, card = rng.choice(fresh)
    small = f"in {secs} seconds" if "{s}" in voice else ""
    return voice, voice.format(s=secs), card + (f" | {small}" if small else "")


def check(line: str) -> bool:
    """A hook the Smart writer wrote is used only when it is short and promises nothing big."""
    line = " ".join(line.split())
    return bool(line) and len(line.split()) <= 14 and not _TOO_MUCH.search(line)


def explain(tip, text: str, place) -> str:
    """"Right now we're going to turn on <the real switch>, so ...": from the plan, so it never
    names a switch that isn't on the page."""
    step = next((s for s in tip.steps if s.waits_for_change or s.already), None)
    setting = (tip.auto.name if tip.auto is not None else
               step.target if step is not None and step.target else
               place.name if place is not None else text)
    if tip.auto is not None:
        action = f"turn {tip.auto_to.lower()}"
    elif step is not None and step.target:
        action = "turn off" if _OFF.search(text) else "turn on"
    else:
        action = "change"
    reason = REASON.get(kind_of(place), "")
    return f"Right now we're going to {action} {setting}" + (f", {reason}." if reason else ".")


def remembered(folder: str) -> list:
    try:
        with open(os.path.join(folder, "recent_hooks.json"), encoding="utf-8") as fh:
            kept = json.load(fh)
        return kept if isinstance(kept, list) else []
    except (OSError, ValueError):
        return []


def remember(folder: str, template: str) -> None:
    kept = ([template] + [t for t in remembered(folder) if t != template])[:RECENT]
    try:
        with open(os.path.join(folder, "recent_hooks.json"), "w", encoding="utf-8") as fh:
            json.dump(kept, fh)
    except OSError:
        pass   # only variety is lost
