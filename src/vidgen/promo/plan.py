"""From a filled-in promo form to a timed storyboard.

The shape is /brag's: Hook -> Reveal -> 2-3 highlights -> Outro, 15 to 25 seconds. Reading
comes first: a short label stays at least 0.8 s, a sentence 0.3 s a word (at least 1.2 s),
counted once it is fully in. Pace comes from motion and cuts, never from pulling text early.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import tones as tone_list

SHORTEST, LONGEST, TARGET = 15.0, 25.0, 20.0
SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}


@dataclass
class Feature:
    title: str
    detail: str = ""


@dataclass
class Promo:
    """The form: what the user (or the Smart writer) filled in."""
    name: str
    hook: str
    what: str = ""
    link: str = ""
    features: list[Feature] = field(default_factory=list)
    shots: list[str] = field(default_factory=list)   # screenshot paths, best first
    result: str = ""          # what the user gets, for the "entry -> action -> result" scene
    claim: str = ""           # one true stat or claim, if there is one
    tone: str = tone_list.DEFAULT
    aspect: str = "16:9"
    seconds: float = TARGET


@dataclass
class Scene:
    kind: str                 # hook, shot, cards, flow, claim, outro, feature
    start: float
    duration: float
    data: dict


def words(text: str) -> int:
    return len(text.split())


def read_time(text: str) -> float:
    """Seconds a line must stay fully visible to be read."""
    count = words(text)
    if count == 0:
        return 0.0
    return max(0.8, 0.3 * count) if count <= 3 else max(1.2, 0.3 * count)


def sentence_case(text: str) -> str:
    return text[:1].upper() + text[1:].lower() if text else text


def _need(kind: str, data: dict, tone) -> float:
    """The shortest this scene can be and still be read: entrance + reading + a beat."""
    enter = tone.transition_seconds + 0.3
    if kind == "hook":
        typing = min(1.6, 0.045 * len(data["text"])) if tone.entrance == "type" else 0.4
        return enter + typing + read_time(data["text"]) + 0.3
    if kind in ("claim", "feature"):
        return enter + read_time(data["text"]) + read_time(data.get("small", "")) * 0.6 + 0.3
    if kind == "shot":
        return enter + max(2.2, read_time(data.get("caption", ""))) + 0.3
    if kind == "cards":
        # Each card arrives only once the one before has been readable for its floor.
        arrivals = sum(read_time(item["title"]) for item in data["items"][:-1])
        last = data["items"][-1]
        return enter + arrivals + read_time(last["title"]) + read_time(last.get("detail", "")) * 0.5 + 0.8
    if kind == "flow":
        return enter + 1.0 + 0.9 + 0.3 + 0.5 + max(1.0, read_time(data.get("result", "")))
    if kind == "outro":
        return enter + read_time(data["name"]) + read_time(data.get("tagline", "")) + 1.2
    raise ValueError(kind)


def _cards_timing(items, tone) -> list[float]:
    """When each card arrives, seconds into the cards scene."""
    at, times = tone.transition_seconds + 0.3, []
    for item in items:
        times.append(round(at, 3))
        at += read_time(item["title"])
    return times


def storyboard(promo: Promo) -> list[Scene]:
    """The scenes, timed, for ``promo.tone``. Never shorter than reading allows."""
    tone = tone_list.get(promo.tone)
    case = sentence_case if tone.case == "sentence" else (lambda t: t)
    shots = list(promo.shots)
    features = [f for f in promo.features if f.title.strip()][:3]
    picked: list[tuple[str, dict, bool]] = []   # (kind, data, can be dropped)

    picked.append(("hook", {"text": case(promo.hook.strip())}, False))
    if shots:
        picked.append(("shot", {"image": shots[0], "caption": case(promo.what.strip()),
                                "device": "laptop" if promo.aspect != "9:16" else "phone"}, False))
    elif promo.what:
        picked.append(("claim", {"text": case(promo.what.strip())}, False))
    if tone.name in ("chaotic", "deadpan"):
        # One idea per scene: each feature gets its own (chaotic slams, deadpan breathes).
        for f in features:
            picked.append(("feature", {"text": case(f.title), "small": f.detail}, True))
    elif features:
        picked.append(("cards", {"items": [{"title": case(f.title), "detail": f.detail}
                                           for f in features]}, False))
    if len(shots) >= 2 and tone.name not in ("polished", "deadpan"):
        picked.append(("flow", {"image": shots[1], "after": shots[2] if len(shots) > 2 else "",
                                "result": case(promo.result.strip())}, True))
    if promo.claim and tone.name in ("yc-parody", "cinematic", "chaotic", "default"):
        picked.append(("claim", {"text": case(promo.claim.strip())}, True))
    picked.append(("outro", {"name": promo.name.strip(), "tagline": case(promo.what.strip()),
                             "link": promo.link.strip()}, False))

    # Too many scenes for the tone: drop the optional ones, from the end.
    most = tone.scenes[1]
    while len(picked) > most and any(drop for *_x, drop in picked):
        index = max(i for i, (*_x, drop) in enumerate(picked) if drop)
        picked.pop(index)

    target = min(LONGEST, max(SHORTEST, promo.seconds))
    lengths = []
    for kind, data, _drop in picked:
        need = _need(kind, data, tone)
        lengths.append(max(need, min(tone.hold[0], need + 1.5)))
    # Too long: drop optional scenes, then trim each scene towards what reading needs.
    while sum(lengths) > LONGEST and any(drop for *_x, drop in picked):
        index = max(i for i, (*_x, drop) in enumerate(picked) if drop)
        picked.pop(index)
        lengths.pop(index)
    needs = [_need(kind, data, tone) for kind, data, _d in picked]
    if sum(lengths) > target:
        spare = sum(lengths) - target
        slack = [length - need for length, need in zip(lengths, needs)]
        total_slack = sum(slack) or 1
        lengths = [length - spare * s / total_slack if spare < total_slack else need
                   for length, need, s in zip(lengths, needs, slack)]
    # Too short: let every scene hold longer, up to the tone's longest hold (then past it).
    for _ in range(3):
        short = target - sum(lengths)
        if short <= 0.01:
            break
        room = [max(0.0, tone.hold[1] * (1.4 if _ else 1.0) - length) for length in lengths]
        if not sum(room):
            room = [1.0] * len(lengths)
        lengths = [length + short * r / sum(room) for length, r in zip(lengths, room)]

    scenes, at = [], 0.0
    for (kind, data, _drop), length in zip(picked, lengths):
        data = dict(data)
        if kind == "cards":
            data["times"] = _cards_timing(data["items"], tone)
        scenes.append(Scene(kind, round(at, 3), round(length, 3), data))
        at += length
    return scenes


def total(scenes) -> float:
    return round(sum(s.duration for s in scenes), 3)


def reading_problems(scenes, tone_name: str) -> list[str]:
    """Scenes too short for their words: the check that runs before rendering."""
    tone = tone_list.get(tone_name)
    return [f"Scene {i + 1} ({s.kind}) is {s.duration:.1f}s but needs {_need(s.kind, s.data, tone):.1f}s "
            "to be read" for i, s in enumerate(scenes) if s.duration + 0.01 < _need(s.kind, s.data, tone)]
