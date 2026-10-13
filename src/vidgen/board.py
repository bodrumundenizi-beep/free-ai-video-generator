"""The Storyboard's data: a script as a list of scenes that can be changed one at a time.

The window shows a card per scene and calls these to edit; text() is always a
script parse_script accepts, so the Storyboard and the script editor are two
views of one thing. Pure: no Tk, no network.
"""

from __future__ import annotations

import dataclasses
import re

from .footage import STOPWORDS
from .script import AI_PREFIX, Scene, parse_script, to_text

STOCK, AI, LOCAL = "stock", "ai", "local"
FIELDS = ("voice", "visual", "card", "picture")
NEW_VISUAL, NEW_VOICE = "abstract background", "Write this line."


def stock_words(description: str) -> str:
    """Search words for a stock site from a picture description: its first few real words."""
    words = [w for w in re.findall(r"[a-z]+", (description or "").lower())
             if len(w) > 2 and w not in STOPWORDS]
    return " ".join(words[:3]) or NEW_VISUAL


def _one_line(value) -> str:
    return " ".join(str(value or "").split())


class Board:
    def __init__(self, scenes: list[Scene]):
        self.scenes = list(scenes)

    @classmethod
    def from_text(cls, text: str) -> "Board":
        """Raises ScriptError for a script that does not parse."""
        return cls(parse_script(text))

    def text(self) -> str:
        return to_text(self.scenes)

    def __len__(self) -> int:
        return len(self.scenes)

    # -- one scene --------------------------------------------------------------------
    def source(self, index: int) -> str:
        scene = self.scenes[index]
        return LOCAL if scene.is_local else AI if scene.is_ai else STOCK

    def shown(self, index: int) -> str:
        """What the card's "picture" box holds: search words, a description, or a file."""
        scene = self.scenes[index]
        return scene.local_path if scene.is_local else scene.ai_prompt if scene.is_ai \
            else scene.visual

    def set(self, index: int, field: str, value) -> None:
        """Change one thing about a scene. Empty cards and pictures are removed; a scene
        keeps its line and its visual, because a script cannot be without them."""
        if field not in FIELDS:
            raise ValueError(field)
        scene, value = self.scenes[index], _one_line(value)
        if field == "voice":
            if not value and scene.duration is None:
                return   # a scene needs something to say, or a set length
            value = value or None
        elif field == "visual":
            if not value:
                return
            # The box shows the words without their prefix; put it back for AI and files.
            prefix = scene.visual[:scene.visual.index(":") + 1] + " " \
                if scene.is_ai or scene.is_local else ""
            value = prefix + (_one_line(value.replace(":", " ")) if scene.is_ai else value)
        else:
            value = value or None
        self.scenes[index] = dataclasses.replace(scene, **{field: value})

    def set_source(self, index: int, kind: str) -> None:
        """Switch a scene between stock footage and an AI picture, keeping what each needs."""
        scene = self.scenes[index]
        if kind == AI and not scene.is_ai:
            described = scene.picture or ", ".join(filter(None, [scene.visual, scene.voice]))
            self.scenes[index] = dataclasses.replace(
                scene, visual=f"{AI_PREFIX} {_one_line(described.replace(':', ' '))}", picture=None)
        elif kind == STOCK and scene.is_ai:
            self.scenes[index] = dataclasses.replace(
                scene, visual=stock_words(scene.ai_prompt), picture=scene.ai_prompt)

    # -- the order ----------------------------------------------------------------------
    def move(self, index: int, by: int) -> int:
        """Move a scene up (-1) or down (+1). Returns where it is now."""
        target = index + by
        if 0 <= target < len(self.scenes):
            self.scenes[index], self.scenes[target] = self.scenes[target], self.scenes[index]
            return target
        return index

    def delete(self, index: int) -> bool:
        """Remove a scene. The last one stays: a script has at least one."""
        if len(self.scenes) <= 1:
            return False
        del self.scenes[index]
        return True

    def insert(self, after: int) -> int:
        """A new scene after ``after``, for the user to fill in. Returns its place."""
        self.scenes.insert(after + 1, Scene(visual=NEW_VISUAL, voice=NEW_VOICE))
        return after + 1


def apply_revision(before: list[Scene], revised: list[tuple[str, str]], cards=None) -> list[Scene]:
    """The scenes after the Smart writer's rewrite (``revised``: what is seen, what is said).

    A scene whose line came back unchanged keeps everything it had (its clip
    or picture, card, timing). A line that is new takes the kind of visual
    most of the script uses, and a card if ``cards(voice)`` suggests one.
    """
    old = Board(before)
    unused = list(range(len(before)))
    mostly_ai = sum(s.is_ai for s in before) * 2 > len(before)
    out = Board([])
    for seen, said in revised:
        said, seen = _one_line(said), _one_line(seen)
        same = next((i for i in unused if before[i].voice == said), None)
        if same is not None:
            unused.remove(same)
            out.scenes.append(before[same])
            if seen and seen != old.shown(same) and not before[same].is_local:
                out.set(len(out) - 1, "visual", seen)
            continue
        visual = f"{AI_PREFIX} {seen.replace(':', ' ')}" if mostly_ai and seen \
            else stock_words(seen) if len(seen.split()) > 4 else (seen.replace(":", " ") or NEW_VISUAL)
        scene = Scene(visual=_one_line(visual), voice=said)
        if not mostly_ai and len(seen.split()) > 4:
            scene.picture = seen
        if cards is not None:
            scene.card = cards(said)
        out.scenes.append(scene)
    return out.scenes


def _identity(scene) -> tuple:
    return (scene.visual, scene.voice, scene.card, scene.picture)


def changed(before: list[Scene], after: list[Scene]) -> list[int]:
    """Places in ``after`` holding a scene that was not in ``before`` as it now stands.

    A scene that only moved is not counted: the marks are for what was rewritten.
    """
    known = {_identity(scene) for scene in before}
    return [i for i, scene in enumerate(after) if _identity(scene) not in known]
