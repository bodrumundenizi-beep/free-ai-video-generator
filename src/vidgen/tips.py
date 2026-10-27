"""Tech tips recorded on the user's own PC ("Show it on my PC").

A tip knows which Windows window to open, how to read the setting it is about, how to put
that setting back exactly as it was, and the steps the user clicks through while the screen is
recorded. Nothing here clicks or changes anything by itself except ``restore``, which only ever
puts back the value read before the recording; a setting that was already on stays on.

Settings are read and restored with Windows' own documented commands, never the registry, and
never anything that needs admin rights.
"""

from __future__ import annotations

import ctypes
import re
import subprocess
import sys
from dataclasses import dataclass, field

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def run(args, run_=subprocess.run) -> str:
    """A Windows command's output (no console window); raises OSError when it fails."""
    try:
        result = run_(args, capture_output=True, text=True, creationflags=NO_WINDOW, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError(f"{args[0]} could not run ({exc})") from exc
    if result.returncode != 0:
        raise OSError(f"{args[0]} failed: {(result.stderr or result.stdout).strip()[:200]}")
    return result.stdout


# The last step when nothing is left to do: it finishes by itself (TipSession.show_step).
CLOSING = "That's it. Press Finish."


@dataclass
class Step:
    """One thing the user does on screen, and what the voice says over it."""
    guide: str                 # the guide box: what to click
    voice_off: str             # the voice when the setting was off before
    voice_on: str = ""         # ... and when it was already on ("" = the same line)
    waits_for_change: bool = False   # True: the step ends by itself once the setting is on
    target: str = ""           # the real button it clicks (uiread): zoomed onto, watched
    already: bool = False      # its switch is already the way the tip wants: said, not waited for
    nav: bool = False          # it only opens a section ("Show more..."): the app clicks it itself


@dataclass
class Tip:
    key: str
    title: str                 # in the list: "Use the High performance power plan"
    opens: list                # the command that opens the window
    read: object               # () -> state, any value restore() understands
    is_on: object              # (state) -> bool
    restore: object            # (state) -> None: put back what read() returned
    steps: list = field(default_factory=list)
    available: object = lambda: ""   # () -> "" or why this tip can't be shown on this PC
    # True: a setting with no "on" (a speed, a plan): the step ends when it differs from
    # what was read before, and there is no "already on".
    watch_change: bool = False
    # The switch the app may flip for the video itself, after the user says yes (can_flip),
    # and the way it turns it ("On" or "Off"); None when the user clicks.
    auto: object = None
    auto_to: str = ""


# --- power plan ---------------------------------------------------------------------------
# Plans by their fixed IDs, so this works in every Windows language.
HIGH_PERFORMANCE = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
_GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def active_plan(run_=run) -> str:
    found = _GUID.search(run_(["powercfg", "/getactivescheme"]))
    if not found:
        raise OSError("Windows didn't say which power plan is on.")
    return found.group(0).lower()


def plans(run_=run) -> list[str]:
    return [g.lower() for g in _GUID.findall(run_(["powercfg", "/list"]))]


def set_plan(guid: str, run_=run) -> None:
    run_(["powercfg", "/setactive", guid])


def _plan_available(run_=run) -> str:
    try:
        return "" if HIGH_PERFORMANCE in plans(run_) else (
            "This PC has no High performance power plan (many newer laptops only show "
            "Balanced), so this tip can't be shown here.")
    except OSError as exc:
        return f"Windows won't say which power plans this PC has ({exc})."


POWER_PLAN = Tip(
    key="power_plan",
    title="Use the High performance power plan",
    opens=["control", "powercfg.cpl"],
    read=active_plan,
    is_on=lambda state: state == HIGH_PERFORMANCE,
    restore=set_plan,
    available=_plan_available,
    steps=[
        Step(guide="This is Power Options. Press Next when the window is fully open.",
             voice_off="Open Control Panel and go to Power Options.",
             voice_on="Open Control Panel and go to Power Options."),
        Step(guide="Click High performance. (Click Show additional plans first if it's hidden.)",
             voice_off="Pick High performance, and Windows stops holding your processor back.",
             voice_on="On my PC High performance is already on, so you can turn it on for "
                      "yourself right here.",
             waits_for_change=True),
        Step(guide=CLOSING,
             voice_off="That's it: games and exports get the full speed of your PC.",
             voice_on="That's it: games and exports get the full speed of your PC."),
    ],
)

TIPS = {tip.key: tip for tip in (POWER_PLAN,)}
# Typed tips that a ready-made one covers better: it reads and restores the setting itself.
_READY = [(re.compile(r"\b(high|best|max(imum)?) performance\b.*\bplan\b|\bpower plan\b", re.I),
           POWER_PLAN)]


def ready_made(text: str):
    """The ready-made tip for a typed one, or None."""
    return next((tip for pattern, tip in _READY if pattern.search(text or "")), None)


# --- mouse speed (a documented Windows call, no registry) ------------------------------------
def mouse_speed(api=None) -> int:
    api = api or ctypes.windll.user32
    speed = ctypes.c_int()
    if not api.SystemParametersInfoW(0x70, 0, ctypes.byref(speed), 0):   # SPI_GETMOUSESPEED
        raise OSError("Windows didn't say the mouse speed.")
    return speed.value


def set_mouse_speed(speed: int, api=None) -> None:
    api = api or ctypes.windll.user32
    # SPI_SETMOUSESPEED, saved and announced like the Settings app does it
    if not api.SystemParametersInfoW(0x71, 0, ctypes.c_void_p(int(speed)), 3):
        raise OSError("Windows didn't take the mouse speed back.")


# Places whose setting the app can read and put back; everything else is only recorded.
WATCHED = {
    "power-options": (active_plan, set_plan),
    "mouse": (mouse_speed, set_mouse_speed),
    "mouse-classic": (mouse_speed, set_mouse_speed),
}


class TipRefused(Exception):
    """A place the app won't record (admin rights or security); the message says why."""


def _check(place) -> None:
    if place.admin:
        raise TipRefused(f"{place.name} needs admin rights or guards your PC's security, so the "
                         "app won't record it. Show it with your own screen recorder instead.")


def from_answer(text: str, answer: dict, place=None) -> Tip:
    """A Tip from the Smart writer's answer for ``place`` (None: Windows' own search)."""
    raw = [s for s in answer.get("steps") or [] if str(s.get("guide", "")).strip()][:4]
    steps = [Step(guide=" ".join(str(s["guide"]).split()),
                  voice_off=" ".join(str(s.get("voice") or s["guide"]).split()),
                  waits_for_change=bool(s.get("changes_setting")),
                  target=str(s.get("target") or "").strip())
             for s in raw]
    if place is None:
        words = " ".join(str(answer.get("search_words") or text).split())[:60]
        steps.insert(0, Step(guide=f"Type \"{words}\" in the search box at the top, then pick "
                                   "the result. Press Next when the page is open.",
                             voice_off=f"Press Windows and I to open Settings, then search "
                                       f"for {words}."))
        return Tip(key="search", title=text, opens=["explorer", "ms-settings:"],
                   read=lambda: None, is_on=lambda _s: False, restore=lambda _s: None,
                   steps=_tidy(steps))
    _check(place)
    return _tip_for(place, text, steps)

def _tidy(steps):
    """Only a watched setting can end a step by itself; the last step always waits for Finish."""
    return [Step(s.guide, s.voice_off, s.voice_on, s.waits_for_change and i < len(steps) - 1,
                 s.target, s.already, s.nav) for i, s in enumerate(steps)] or [
        Step("Press Finish when you're done.", "")]


def _tip_for(place, text, steps) -> Tip:
    watched = WATCHED.get(place.id)
    if watched is None:
        # No Windows command for it: a step ends on the change only when its button is a
        # real switch the app can watch (TipSession); otherwise the user presses Next.
        steps = [Step(s.guide, s.voice_off, s.voice_on, s.waits_for_change and bool(s.target),
                      s.target, s.already, s.nav) for s in steps]
        return Tip(key=place.id, title=text, opens=place.command(), read=lambda: None,
                   is_on=lambda _s: False, restore=lambda _s: None, steps=_tidy(steps))
    read, put_back = watched
    return Tip(key=place.id, title=text, opens=place.command(), read=read,
               is_on=lambda _s: False, restore=put_back, steps=_tidy(steps), watch_change=True)


def fallback(text: str, places) -> Tip:
    """Without the Smart writer: the best matching place and plain steps."""
    if not places:
        raise TipRefused("Nothing in the map of Windows matches that tip. Try other words, "
                         "like the name of the setting.")
    place = places[0]
    _check(place)
    steps = [Step(f"This is {place.name}.",
                  f"Open {place.name}."),
             Step("Now make the change you want to show, then press Next.",
                  f"Here is where you {text.rstrip('.?!').lower()}.",
                  waits_for_change=True),
             Step(CLOSING, "That's all it takes.")]
    return _tip_for(place, text, steps)


def script(tip: Tip, was_on: bool, clips: list[str], intro=()) -> str:
    """The video's script: one scene per recorded step, the voice saying what is on screen.

    A step that says to press keys ("Press Windows and I...") gets the keycap card for them.
    """
    from .draft import key_card

    # The opening (hooks.py): a picture, its voice and its card each, moving gently.
    blocks = [f'Visual: local: "{picture}"\nVoice: {voice}' + (f"\nCard: {card}" if card else "")
              for picture, voice, card in intro]
    for step, clip in zip(tip.steps, clips):
        voice = (step.voice_on or step.voice_off) if (was_on or step.already) else step.voice_off
        keys = key_card(voice)
        blocks.append(f'Visual: local: "{clip}"\nVoice: {voice}\nZoom: none'
                      + (f"\nCard: {keys}" if keys else ""))
    return "\n\n".join(blocks)


_OFF_WORDS = re.compile(r"\b(off|disable|disabling|stop|remove|hide|turn off|get rid)\b", re.I)


MORE = re.compile(r"\b(show more|more settings|advanced|additional)\b", re.I)


def match_words(text: str, place=None) -> list:
    """The tip and the map's words for its page that mean the same thing.

    Words that share a whole word with the tip, and a short form's long name: "hags" spells
    h(ardware) a(ccelerated) g(pu) s(cheduling).
    """
    from .winmap import _terms

    words = [text]
    if place is None:
        return words
    asked = _terms(text)
    short = {w for w in text.lower().split() if 2 < len(w) < 7}
    for w in place.words:
        initials = "".join(part[0] for part in re.split(r"[\s-]+", w) if part)
        if _terms(w) & asked or initials in short:
            words.append(w)
    return words


def _main(tip: Tip) -> int:
    """The step that changes the setting: the one marked so, else the one before the last."""
    return next((i for i, s in enumerate(tip.steps) if s.waits_for_change),
                min(len(tip.steps) - 1, max(len(tip.steps) - 2, 0)))


def can_flip(place, button) -> bool:
    """True when the app may flip ``button`` itself for a video (and back): an on/off switch
    on a Settings page that nothing can go wrong with for a moment (winmap.NEVER_AUTO)."""
    from .winmap import NEVER_AUTO, NEVER_AUTO_NAMES

    return (place is not None and not place.admin and place.opens.startswith("ms-settings:")
            and place.id not in NEVER_AUTO and button.state in ("On", "Off")
            and not NEVER_AUTO_NAMES.search(button.name))


def aim(tip: Tip, buttons, text: str, place=None, opened=None) -> str:
    """Point the tip's main step at the page's real button for it; "" or a note for the user.

    The app picks the button by matching the tip's words (and the map's words for the page)
    to button names, rather than trusting the Smart writer, which in tests named switches it
    could see (HDR) for one it couldn't (GPU scheduling). With no match, no step names a
    button at all (the video zooms where the user clicks); if the page has a "Show more
    settings" kind of section, a first step opens it, and the recording finds the switch once
    it shows. A switch that already is the way the tip wants is said in the voice ("already on
    on my PC") and not waited for. ``opened``: the "Show more" button planning clicked to see
    the switch; the tip then starts by opening it too.
    """
    from . import uiread

    if not tip.steps or tip.key == "search" or not buttons:
        return ""
    button = uiread.best_match(buttons, match_words(text, place))
    index = _main(tip)
    step = tip.steps[index]
    if button is None:
        # The Smart writer's steps named buttons it guessed (HDR for GPU scheduling): keep
        # none of them. One step to find and switch it, one to finish.
        doing = re.sub(r"^(how (do i|to|can i)|can you)\s+", "", text.strip(), flags=re.I)
        tip.steps[:] = [Step(guide=f"Find the setting for \"{text}\" on this page and click it.",
                             voice_off=f"Here's how to {doing}: it's right here on this page.",
                             waits_for_change=True),
                        Step(guide=CLOSING, voice_off="That's all it takes.")]
        more = next((b for b in buttons if MORE.search(b.name)), None)
        if more is None:
            return " No button on this page matches that tip yet: the video zooms where you click."
        tip.steps.insert(0, _opening(more))
        return f" The switch isn't visible yet: the first step opens {more.name}."
    if step.target != button.name:
        step.target = button.name
        step.guide = f"Click {button.name}."
    # After the main step the writer tended to add clicks elsewhere ("click Graphics"), which
    # would take the user off the page: the tip ends with a plain closing step instead.
    tip.steps[index + 1:] = [Step(guide=CLOSING,
                                  voice_off="That's all it takes.")]
    wants = "Off" if _OFF_WORDS.search(text) else "On"
    switch = button.state in ("On", "Off")
    step.waits_for_change = switch and button.state != wants and index < len(tip.steps) - 1
    if step.waits_for_change and can_flip(place, button):
        tip.auto, tip.auto_to = button, wants
    if switch and button.state == wants:
        step.already = True
        step.guide = f"Watch: {button.name} is already {wants.lower()} here; the app points at it."
        step.voice_on = (f"On my PC {button.name} is already {wants.lower()}; "
                         f"this is the switch to turn it {wants.lower()}.")
    if opened is not None:
        tip.steps.insert(0, _opening(opened))
    return ""


def _opening(more) -> Step:
    """The step that opens a section ("Show more graphics settings"): the app clicks it."""
    return Step(guide=f"Watch: the app opens {more.name}.", voice_off=f"First, open {more.name}.",
                target=more.name, nav=True)
