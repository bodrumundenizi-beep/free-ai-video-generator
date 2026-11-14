"""Tutorial timeline builder (5.4, plan step 3) and the golden test.

Golden test: the builder, fed the reference's clicks (tests/fixtures/tutorial_ref/golden_log.json:
positions, times, names, menus, as the recorder logs them), must give the hand-made reference's
timeline (timeline.json) within small tolerances. That is what makes the app's tutorials and the
hand-made video twins.
"""
import json
import os

import numpy as np
import pytest

from vidgen import tutorial
from vidgen.tutorial import build as B

REF = os.path.join(os.path.dirname(__file__), "fixtures", "tutorial_ref")
SCREENS = ["s1.png", "s1b.png", "s2.png", "s1b.png", "s3.png", "s4.png", "s5.png", "s6.png"]


def golden_log():
    with open(os.path.join(REF, "golden_log.json"), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def pair():
    built = B.build(golden_log(), title="Add a coloured rectangle", app="PowerPoint",
                    screens=SCREENS, images_dir=REF)
    return built, tutorial.load(os.path.join(REF, "timeline.json"))


def test_golden_steps_clicks_and_ending(pair):
    built, ref = pair
    assert [s["at"] for s in built["steps"]] == pytest.approx([s["at"] for s in ref["steps"]],
                                                               abs=0.05)
    assert [c[1:] for c in built["clicks"]] == [c[1:] for c in ref["clicks"]]
    assert [c[0] for c in built["clicks"]] == pytest.approx([c[0] for c in ref["clicks"]], abs=0.1)
    assert built["recap"]["at"] == pytest.approx(ref["recap"]["at"], abs=0.1)
    assert built["length"] == pytest.approx(ref["length"], abs=0.2)
    assert built["main"] == pytest.approx(ref["main"], abs=0.1)
    assert len(built["recap"]["rows"]) == len(ref["recap"]["rows"])
    assert built["title"]["tag"] == ref["title"]["tag"] and built["title"]["sub"] == ref["title"]["sub"]


def test_golden_labels_and_screens(pair):
    built, ref = pair
    assert [c[4] for c in built["callouts"]] == [c[4] for c in ref["callouts"]]
    assert [c[0] for c in built["callouts"]] == pytest.approx([c[0] for c in ref["callouts"]], abs=0.1)
    assert [c[1] for c in built["callouts"]] == pytest.approx([c[1] for c in ref["callouts"]], abs=0.25)
    assert [s["image"] for s in built["screens"]] == [s["image"] for s in ref["screens"]]
    assert [s["at"] for s in built["screens"]] == pytest.approx([s["at"] for s in ref["screens"]],
                                                                abs=0.2)
    assert [(d["x"], d["y"]) for d in built["drags"]] == [(d["x"], d["y"]) for d in ref["drags"]]
    assert built["drags"][0]["press_until"] == pytest.approx(ref["drags"][0]["press_until"], abs=0.1)
    # the shape grows while it is dragged, in its own colour (the reference's, within a shade)
    got, want = built["drags"][0]["fill"], ref["drags"][0]["fill"]
    assert all(abs(int(got[i:i + 2], 16) - int(want[i:i + 2], 16)) <= 12 for i in (1, 3, 5))


def test_golden_camera_and_cursor_follow_the_reference(pair):
    built, ref = pair
    ts = np.arange(0, ref["length"], 0.05)
    centre = [np.hypot(*np.subtract(B.sample(built["camera"], t)[:2], B.sample(ref["camera"], t)[:2]))
              for t in ts]
    zoom = [abs(B.sample(built["camera"], t)[2] - B.sample(ref["camera"], t)[2]) for t in ts]
    cursor = [np.hypot(*np.subtract(B.sample(built["cursor"], t), B.sample(ref["cursor"], t)))
              for t in ts]
    # measured 2026-11-14: centre mean 37 / max 175 px (the reference drifts slowly through
    # step 3 by hand), zoom mean 0.01 / max 0.26, cursor mean 2.8 / max 50 px
    assert np.mean(centre) <= 45 and max(centre) <= 190
    assert np.mean(zoom) <= 0.03 and max(zoom) <= 0.3
    assert np.mean(cursor) <= 5 and max(cursor) <= 60
    # the same shots: the same zooms, in the same order
    def shots(keys):
        out = []
        for key in keys:
            if not out or out[-1] != key[3]:
                out.append(key[3])
        return out
    assert shots(built["camera"]) == shots(ref["camera"])


def test_golden_timeline_is_drawable(pair):
    built, _ref = pair
    filled = tutorial.check(built, REF)
    assert filled["anchors"][0][0] == 0


def test_steps_start_at_tabs_drags_and_colours():
    evs = B.events(golden_log())
    steps = B.group(evs)
    assert [[e["name"] or "drag" for e in s] for s in steps] == [
        ["Insert", "Shapes", "rectangle"], ["drag"], ["Shape Format", "Shape Fill"], ["Gold"]]
    assert evs[2]["picks"] and evs[6]["picks"]          # picked in the menu just opened
    assert evs[1]["opens"] == [374, 159, 309, 407]      # Shapes opened its menu


def test_many_clicks_become_at_most_six_steps():
    clicks = [{"t": i, "x": 10 * i, "y": 10, "name": f"Tab {i}", "kind": "TabItem"} for i in range(9)]
    steps = B.group(B.events({"clicks": clicks}))
    assert len(steps) == B.STEPS_MOST and sum(map(len, steps)) == 9


def test_captions():
    tab = {"name": "Insert", "kind": "TabItem", "drag": False, "colour": False}
    item = {"name": "shapes", "kind": "MenuItem", "drag": False, "colour": False}
    assert B.caption([tab, item]) == "Open **Insert**, then **Shapes**"
    assert B.caption([tab]) == "Click **Insert**"
    assert B.caption([{"name": "", "drag": True, "colour": False}], "PowerPoint") == \
        "**Drag** on the slide to draw it"
    assert B.caption([{"name": "", "drag": True, "colour": False}], "Notepad") == "**Drag** to draw it"
    assert B.caption([{"name": "Gold", "drag": False, "colour": True}]) == "Pick **Gold**"
    assert B.app_name("Presentation1 - PowerPoint") == "PowerPoint"


def test_anchors_pair_tutorial_and_recording_times():
    log = golden_log()
    def later(t):   # a slow recording: everything 3x later, and a long pause after 5 s
        return t * 3 + (20 if t > 5 else 0)
    for c in log["clicks"]:
        c["t"], c["up"] = later(c["t"]), later(c["up"])
    log["popups"] = [[later(t), rects] for t, rects in log["popups"]]
    built = B.build(log)
    pairs = built["anchors"]
    assert [p[0] for p in pairs] == sorted(p[0] for p in pairs)
    assert [p[1] for p in pairs] == sorted(p[1] for p in pairs)
    # the long pause is gone: the tutorial still follows the house rhythm
    assert built["length"] == pytest.approx(17.0, abs=0.2)
    assert B.to_recording(pairs, built["clicks"][4][0]) == pytest.approx(log["clicks"][4]["t"])
    assert B.to_tutorial(pairs, log["clicks"][4]["t"]) == pytest.approx(built["clicks"][4][0])


def test_real_path_cursor_follows_the_recording_but_lands_on_clicks():
    log = golden_log()
    # a shaky path from the first click to the second
    log["moves"] = [[2.3 + i * 0.016, 211 + i * 3 + (4 if i % 2 else -4), 64 + i] for i in range(50)]
    built = B.build(log, cursor="real")
    keys = built["cursor"]
    for t, x, y in built["clicks"]:
        assert [x, y] in [k[1:] for k in keys if abs(k[0] - t) < 1e-6]
    middle = [k for k in keys if built["clicks"][0][0] < k[0] < built["clicks"][1][0]]
    jumps = [abs(a[1] - b[1]) for a, b in zip(middle, middle[1:])]
    assert middle and max(jumps) < 40          # smoothed: no 8-pixel zigzag left


def test_no_clicks_is_a_clear_error():
    with pytest.raises(B.BuildError, match="Nothing was clicked"):
        B.build({"clicks": [], "window": [0, 0, 100, 100]})


def test_a_last_click_on_empty_space_is_left_out():
    log = golden_log()
    log["clicks"].append({"t": 14.0, "x": 1300, "y": 800, "button": "left", "on_canvas": True})
    assert len(B.build(log)["clicks"]) == 7


def test_classic_menu_bar_menus_start_their_own_steps():
    def click(t, x, y, name, menu_opens=None):
        return {"t": t, "x": x, "y": y, "name": name, "kind": "MenuItem", "button": "left"}
    log = {"window": [0, 300, 1200, 800],
           "clicks": [click(1, 380, 320, "Search"), click(2, 500, 380, "Find..."),
                      click(4, 430, 320, "View"), click(5, 520, 420, "Always on Top")],
           "popups": [[0, []], [1.1, [[356, 340, 350, 600]]], [2.1, []],
                      [4.1, [[405, 340, 350, 600]]], [5.1, []]]}
    steps = B.group(B.events(log))
    assert [[e["name"] for e in s] for s in steps] == [["Search", "Find..."], ["View", "Always on Top"]]


def test_9_16_is_a_tall_stage_that_starts_with_the_whole_window():
    tall = B.build(golden_log(), shape="9:16")
    assert tall["stage"]["size"] == [1080, 1920] and tall["stage"]["tall"]
    first_zoom = tall["camera"][0][3]
    assert 1586 * first_zoom <= 1080                 # the whole window fits across
    wide = B.build(golden_log())
    assert wide["stage"]["size"] == [1920, 1080] and wide["camera"][0][3] == 1
    # the same steps and clicks in both shapes: only the camera differs
    assert [c[:3] for c in tall["clicks"]] == [c[:3] for c in wide["clicks"]]


def test_a_colour_named_by_windows_is_its_own_step_too():
    log = golden_log()
    log["clicks"][6].pop("named_by")                # Windows named it, not the pixels
    log["clicks"][6]["name"], log["clicks"][6]["kind"] = "Orange", "ListItem"
    steps = B.group(B.events(log))
    assert len(steps) == 4 and B.caption(steps[-1]) == "Pick **Orange**"
    assert B.is_colour("Orange, Accent 2") and B.is_colour("Black, Text 1, Lighter 50%")
    assert not B.is_colour("Shape Fill") and not B.is_colour("Rectangle")
