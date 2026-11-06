"""Looking at stock clips before the video is made, so a clip that shows something else
(a snake for "python code") is swapped before anyone sees it.

Every clip a scene will show is looked at: the Smart writer (Session.clips_text_fit) first
reads what the site says the clip shows, and a "no" there drops it at once; the clips left
have their preview stills described by the photo model (photos.describe), and the Smart
writer judges that the same way. Only the picture can say yes: a site's tags list
everything ("covid-19, coronavirus, crash" passed for "stock market crash"). After TRIES
clips without a fit the scene becomes an AI picture, or, without the Picture model, the
user is asked. Nothing here touches Tk.
"""

from __future__ import annotations

from dataclasses import dataclass, field

TRIES = 4   # clips looked at per scene before giving up on stock
AI_SHARE = 10   # at most one scene in this many becomes an AI picture: each costs time
LONG_LINE = 15  # words: a line this long is about render.LONG_SCENE, so it shows two clips


@dataclass
class Scene:
    index: int
    visual: str           # the search words
    candidates: list      # footage.Candidate, best first
    wanted: int = 1       # clips the scene shows: two for a long line (render._parts)


@dataclass
class Picked:
    clips: list = field(default_factory=list)   # to try in order: the checked ones first
    ai: bool = False      # no clip fits: make an AI picture instead
    ask: bool = False     # no clip fits and no Picture model: ask the user


def clips_for(voice: str | None) -> int:
    """How many clips a scene will show, from its line before the voice exists."""
    return 2 if len((voice or "").split()) >= LONG_LINE else 1


def pick(scenes, describe, judge, ai_ready: bool, tries: int = TRIES,
         taken=(), ai_most: int | None = None, tell=None) -> dict[int, Picked]:
    """What each scene uses. ``judge(visual, texts)`` gives yes/maybe/no for each text;
    ``describe(candidates)`` gives what each clip's still shows ("" when unknown, which
    counts as fitting). ``taken``: keys of clips other scenes use; no two scenes share one.
    ``ai_most``: how many scenes may become AI pictures (None: any); past it a scene keeps
    its best free clip, as it would without the check.

    Works in rounds: each round every unfinished scene puts forward its next clips (a "no"
    from the site's words skips a clip without fetching its still), all stills are
    described in one go, then judged. Most scenes are done after the first round.
    """
    taken = set(taken)
    left = {s.index: [c for c in s.candidates if c.key not in taken][:tries * 2]
            for s in scenes}
    accepted = {s.index: [] for s in scenes}
    rejected = {s.index: [] for s in scenes}
    looked = {s.index: 0 for s in scenes}
    pending = [s for s in scenes if s.candidates]
    while pending:
        offered = {}
        for scene in pending:
            need, mine = scene.wanted - len(accepted[scene.index]), []
            while left[scene.index] and len(mine) < need \
                    and looked[scene.index] + len(mine) < tries:
                c = left[scene.index].pop(0)
                if c.key in taken:
                    continue
                if c.about and judge(scene.visual, [c.about])[0] == "no":
                    rejected[scene.index].append(c)
                    continue
                mine.append(c)
            offered[scene.index] = mine
        flat = [c for mine in offered.values() for c in mine]
        shows = dict(zip(map(id, flat), describe(flat))) if flat else {}
        for done, scene in enumerate(pending, 1):
            if tell:
                tell("Checking clips", done, len(pending))
            for c in offered[scene.index]:
                looked[scene.index] += 1
                said = shows.get(id(c), "")
                if c.key in taken or said and judge(scene.visual, [said])[0] == "no":
                    rejected[scene.index].append(c)
                else:
                    accepted[scene.index].append(c)
                    taken.add(c.key)
        pending = [s for s in pending if offered[s.index]
                   and len(accepted[s.index]) < s.wanted and looked[s.index] < tries
                   and left[s.index]]
    found = {}
    for scene in scenes:
        good, bad = accepted[scene.index], rejected[scene.index]
        rest = [c for c in scene.candidates if c not in good and c not in bad]
        if good:
            found[scene.index] = Picked(good + rest)
        elif not scene.candidates:
            found[scene.index] = Picked([])          # nothing found: the render says so
        elif ai_ready and (ai_most is None or ai_most > 0):
            ai_most = None if ai_most is None else ai_most - 1
            found[scene.index] = Picked(rest, ai=True)
        elif ai_ready:
            # ponytail: the first scenes in the video get the AI pictures, not the worst clips.
            free = [c for c in scene.candidates if c.key not in taken]
            taken.update(c.key for c in free[:1])
            found[scene.index] = Picked(free + [c for c in scene.candidates if c not in free])
        else:
            found[scene.index] = Picked(list(scene.candidates), ask=True)
    return found


def run(scenes, folder: str, ai_ready: bool, should_stop=None, gpu: bool = False,
        session=None, tell=None) -> dict[int, Picked]:
    """pick() with the real models, the clips' stills fetched into ``folder``."""
    from concurrent.futures import ThreadPoolExecutor

    from . import photos, scenes as previews, writer

    def describe(candidates):
        with ThreadPoolExecutor(8) as pool:   # a few KB each: the waiting is the cost
            paths = list(pool.map(lambda c: previews.fetch_thumb(c, folder, session),
                                  candidates))
        wanted = [p for p in paths if p]
        said = dict(zip(wanted, photos.describe(wanted, should_stop, gpu,
                                                session=eyes))) if wanted else {}
        return [said.get(p, "") if p else "" for p in paths]

    # Both models stay loaded for the whole check: each round would otherwise start the
    # photo model again (seconds each time).
    with writer.Session(should_stop, gpu=gpu) as judge_session,             photos.session_for(should_stop, gpu) as eyes:
        def judge(visual, texts):   # the site's words and the photo model's, alike
            judge_session.start()
            return judge_session.clips_text_fit(visual, texts)

        return pick(scenes, describe, judge, ai_ready,
                    ai_most=max(1, len(scenes) // AI_SHARE), tell=tell)


__all__ = ["AI_SHARE", "LONG_LINE", "TRIES", "Picked", "Scene", "clips_for", "pick", "run"]
