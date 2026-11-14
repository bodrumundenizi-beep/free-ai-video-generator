"""Tutorial videos (5.4): the timeline engine, and twin check #1.

Twin check #1: the hand-made reference (tests/fixtures/tutorial_ref/tut.html, every number typed
by hand) and the engine fed the same numbers as a timeline (timeline.json) must draw the same
frames at every step moment.
"""
import io
import json
import os
import shutil

import numpy as np
import pytest
from PIL import Image

from vidgen import tutorial
from vidgen.promo import browser

REF = os.path.join(os.path.dirname(__file__), "fixtures", "tutorial_ref")
needs_browser = pytest.mark.skipif(browser.find() is None, reason="no browser or WebView2 here")


def reference():
    return tutorial.load(os.path.join(REF, "timeline.json"))


def moments(timeline) -> list[float]:
    """Every moment that shows something: the title, each step, click, ring, label, screen
    change, the drag and the Done card."""
    times = [0.7, timeline["title"]["until"] - 0.15]
    times += [step["at"] + d for step in timeline["steps"] for d in (0.15, 0.5)]
    times += [click[0] + d for click in timeline["clicks"] for d in (0.05, 0.3)]
    times += [(a + b) / 2 for a, b, *_ in timeline["callouts"]]
    times += [screen["at"] + 0.09 for screen in timeline["screens"][1:]]
    times += [(drag["from"] + drag["until"]) / 2 for drag in timeline["drags"]]
    times += [timeline["recap"]["at"] + d for d in (0.2, 0.8, 1.6)] + [timeline["length"] - 0.05]
    return sorted(set(round(t, 3) for t in times))


def frames(page: str, times) -> list[np.ndarray]:
    with browser.Browser(*tutorial.SIZE) as b:
        b.open(page)
        shots = []
        for t in times:
            b.evaluate(f"seek({t})")
            shots.append(np.asarray(Image.open(io.BytesIO(b.frame())).convert("RGB"), dtype=np.int16))
        return shots


def test_reference_timeline_passes_the_check():
    filled = tutorial.check(reference(), REF)
    assert filled["style"]["crossfade"] == 0.18 and filled["style"]["ring"] == 0.6
    assert filled["look"]["accent"] == "#E8763A"
    assert len(filled["steps"]) == 4 and len(filled["clicks"]) == 7


def test_check_names_what_is_wrong(tmp_path):
    with pytest.raises(tutorial.TutorialError, match="missing: camera"):
        tutorial.check({k: v for k, v in reference().items() if k != "camera"}, REF)
    late = reference()
    late["cursor"] = [[2, 0, 0], [1, 0, 0]]
    with pytest.raises(tutorial.TutorialError, match="time order"):
        tutorial.check(late, REF)
    with pytest.raises(tutorial.TutorialError, match="picture is missing"):
        tutorial.check(reference(), str(tmp_path))
    sneaky = reference()
    sneaky["screens"][0]["image"] = "../s1.png"   # only files in the images folder
    with pytest.raises(tutorial.TutorialError, match="picture is missing"):
        tutorial.check(sneaky, REF)


def test_page_text_is_data_never_markup(tmp_path):
    timeline = reference()
    timeline["title"]["title"] = "</script><b>hi</b>"
    page = tutorial.build_page(timeline, REF, str(tmp_path))
    written = (tmp_path / "timeline.js").read_text(encoding="utf-8")
    assert "</script>" not in written and "<\\/script>" in written
    assert os.path.isfile(page) and (tmp_path / "s1b.png").is_file()


@needs_browser
def test_twin_check_1_engine_draws_the_reference(tmp_path):
    """The engine and the hand-made page draw the same frames at every moment that matters."""
    timeline = reference()
    times = moments(timeline)
    hand = str(tmp_path / "hand")
    shutil.copytree(REF, hand, ignore=shutil.ignore_patterns("*.mp4"))
    engine = str(tmp_path / "engine")
    os.makedirs(engine)
    page = tutorial.build_page(timeline, REF, engine)
    want = frames(os.path.join(hand, "tut.html"), times)
    got = frames(page, times)
    worst = []
    for t, a, b in zip(times, want, got):
        diff = np.abs(a - b)
        changed = float((diff.max(axis=2) > 40).mean())   # share of clearly different pixels
        worst.append((changed, float(diff.mean()), t))
    worst.sort(reverse=True)
    print("worst moments (changed share, mean diff, t):", worst[:3])
    assert all(changed < 0.002 and mean < 1.0 for changed, mean, _ in worst), worst[:3]
