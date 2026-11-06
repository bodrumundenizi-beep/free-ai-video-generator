"""Stock clips checked before the video is made (clipcheck), with fake models."""

from vidgen.clipcheck import TRIES, Scene, clips_for, pick
from vidgen.footage import Candidate


def clip(name, shows="", about=""):
    c = Candidate("Pexels", name, f"https://x/{name}.mp4", 1920, 1080, about=about)
    c.shows = shows
    return c


def fake(answers):
    """describe: each clip's ``shows``; judge: the answer set for each text."""
    calls = {"described": [], "judged": []}

    def describe(candidates):
        calls["described"].append([c.id for c in candidates])
        return [c.shows for c in candidates]

    def judge(visual, texts):
        calls["judged"].extend(texts)
        return [answers[t] for t in texts]

    return describe, judge, calls


def test_a_clip_that_fits_is_kept_first_and_nothing_more_is_looked_at():
    a, b = clip("a", "code on a screen"), clip("b", "a snake")
    describe, judge, calls = fake({"code on a screen": "yes", "a snake": "no"})
    found = pick([Scene(0, "python code", [a, b])], describe, judge, ai_ready=True)
    assert found[0].clips == [a, b] and not found[0].ai and not found[0].ask
    assert calls["described"] == [["a"]] and calls["judged"] == ["code on a screen"]


def test_a_no_moves_on_and_maybe_counts_as_fitting():
    a, b, c = clip("a", "a snake"), clip("b", "a terminal"), clip("c", "code")
    describe, judge, calls = fake({"a snake": "no", "a terminal": "maybe", "code": "yes"})
    found = pick([Scene(0, "python code", [a, b, c])], describe, judge, ai_ready=True)
    assert found[0].clips == [b, c]   # the snake is never shown
    assert calls["described"] == [["a"], ["b"]]   # one round per try


def test_the_site_words_can_only_say_no():
    earth = clip("e", "the Earth lit up", about="covid-19, coronavirus, crash")
    chart = clip("c", "a falling red chart", about="stock chart")
    mango = clip("m", "a hand holding fruit", about="mango squeeze, pink")
    answers = {"covid-19, coronavirus, crash": "yes", "the Earth lit up": "no",
               "stock chart": "yes", "a falling red chart": "yes", "mango squeeze, pink": "no",
               "beans": "yes"}
    describe, judge, calls = fake(answers)
    found = pick([Scene(0, "stock market crash", [earth, chart]),
                  Scene(1, "coffee fruit pulping", [mango, clip("b", "beans")])],
                 describe, judge, ai_ready=True)
    assert found[0].clips[0] is chart   # a "yes" from the tags didn't save the Earth
    assert "m" not in sum(calls["described"], [])   # the mango's still is never fetched


def test_a_long_line_gets_two_checked_clips():
    cake, sand, glass = clip("c", "a cake"), clip("s", "a sandy hand"), clip("g", "a dessert")
    describe, judge, _ = fake({"a cake": "yes", "a sandy hand": "no", "a dessert": "yes"})
    found = pick([Scene(0, "tiramisu", [cake, sand, glass], wanted=2)], describe, judge,
                 ai_ready=True)
    assert found[0].clips[:2] == [cake, glass] and sand not in found[0].clips
    assert clips_for("short line") == 1 and clips_for("word " * 15) == 2


def test_tries_without_a_fit_make_an_ai_picture_or_ask_without_the_picture_model():
    clips = [clip(n, f"wrong {n}") for n in "abcdefg"]
    answers = {f"wrong {n}": "no" for n in "abcdefg"}
    describe, judge, calls = fake(answers)
    found = pick([Scene(0, "tiramisu", clips)], describe, judge, ai_ready=True)
    assert found[0].ai and found[0].clips == clips[TRIES:]
    assert len(calls["judged"]) == TRIES
    found = pick([Scene(0, "tiramisu", clips)], *fake(answers)[:2], ai_ready=False)
    assert found[0].ask and not found[0].ai and found[0].clips == clips


def test_ai_pictures_are_capped_then_scenes_keep_their_best_clip():
    scenes = [Scene(i, "tiramisu", [clip(f"{i}{n}", "a dog") for n in "abcd"]) for i in range(3)]
    describe, judge, _ = fake({"a dog": "no"})
    found = pick(scenes, describe, judge, ai_ready=True, ai_most=1)
    assert found[0].ai and not found[1].ai and not found[2].ai
    assert found[1].clips[0].id == "1a" and not found[1].ask


def test_two_scenes_never_get_the_same_clip():
    shared, own = clip("s", "coffee cherries"), clip("o", "coffee plant")
    describe, judge, _ = fake({"coffee cherries": "yes", "coffee plant": "yes"})
    found = pick([Scene(0, "coffee tree", [shared]), Scene(1, "coffee tree", [shared, own])],
                 describe, judge, ai_ready=True)
    assert found[0].clips[0] is shared and found[1].clips[0] is own
    found = pick([Scene(0, "coffee", [shared])], describe, judge, ai_ready=True,
                 taken={shared.key})
    assert found[0].ai   # its only clip is another scene's


def test_unknown_stills_count_as_fitting_and_a_scene_with_no_clips_is_left_alone():
    a = clip("a")
    describe, judge, calls = fake({})
    found = pick([Scene(0, "coffee", [a]), Scene(1, "x", [])], describe, judge, ai_ready=True)
    assert found[0].clips == [a] and found[1].clips == [] and not found[1].ai
    assert calls["described"] == [["a"]]   # all scenes' stills in one go
