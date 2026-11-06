"""The clip check as the first step of a video (render._check_clips), with a fake check."""

import threading

import pytest

from vidgen import clipcheck, imagegen, render
from vidgen.footage import Candidate
from vidgen.script import Scene


class Ui:
    def __init__(self):
        self.logs = []

    def log(self, msg):
        self.logs.append(msg)

    def status(self, msg):
        pass


class Search:
    """Hands each query its own clips, as FootageSearch would."""

    def __init__(self, clips):
        self.clips, self.used, self.disabled = clips, set(), {}

    def find(self, query, limit):
        return list(self.clips.get(query, []))[:limit]


def clip(name):
    return Candidate("Pexels", name, f"https://x/{name}.mp4", 1920, 1080)


A, B, C, D = clip("a"), clip("b"), clip("c"), clip("d")


def setup(picked, **cfg):
    plans = [render._ScenePlan(Scene(visual="coffee plant", voice="Coffee grows.")),
             render._ScenePlan(Scene(visual="roast thermometer", voice="It's hot."))]
    search = Search({"coffee plant": [A, B], "roast thermometer": [C, D]})
    seen = {}

    def check(scenes, folder, ai_ready, should_stop=None, gpu=False, tell=None):
        seen["scenes"] = [s.index for s in scenes]
        seen["wanted"] = [s.wanted for s in scenes]
        if tell:
            tell("Checking clips", 1, len(scenes))
        return picked

    cfg = dict({"check_clips": True}, **cfg)
    return plans, search, cfg, check, seen


@pytest.fixture(autouse=True)
def picture_model_ready(monkeypatch):
    monkeypatch.setattr(imagegen, "ready", lambda *a, **k: (True, ""))


def test_the_checked_clip_goes_first_and_is_logged(tmp_path):
    plans, search, cfg, check, _ = setup({0: clipcheck.Picked([B, A]),
                                          1: clipcheck.Picked([C, D])})
    ui = Ui()
    render._check_clips(cfg, ui, plans, search, str(tmp_path), lambda: False, check)
    assert cfg["clip_choices"] == {0: [B, A], 1: [C, D]}
    assert "2 scenes" in ui.logs[-1] and "1 swapped, 0 AI pictures" in ui.logs[-1]


def test_a_scene_with_no_good_clip_becomes_an_ai_picture(tmp_path):
    plans, search, cfg, check, _ = setup({0: clipcheck.Picked([B], ai=True)}, _ai_total=0)
    render._check_clips(cfg, Ui(), plans, search, str(tmp_path), lambda: False, check)
    assert plans[0].scene.is_ai and "coffee plant" in plans[0].scene.ai_prompt
    assert cfg["_ai_total"] == 1 and 0 not in cfg["clip_choices"]


def test_the_question_answers_reach_the_render(tmp_path):
    asked = []

    def ask(items):
        asked.extend(items)
        return {1: "next"}

    plans, search, cfg, check, _ = setup({1: clipcheck.Picked([C, D], ask=True)}, ask_clips=ask)
    render._check_clips(cfg, Ui(), plans, search, str(tmp_path), lambda: False, check)
    assert [(i["index"], i["visual"], i["voice"]) for i in asked] == \
        [(1, "roast thermometer", "It's hot.")]
    assert cfg["clip_choices"][1] == [D, C]


def test_no_window_in_a_batch_and_the_clip_is_used(tmp_path):
    plans, search, cfg, check, _ = setup({1: clipcheck.Picked([C, D], ask=True)})
    render._check_clips(cfg, Ui(), plans, search, str(tmp_path), lambda: False, check)
    assert cfg["clip_choices"][1] == [C, D]


def test_storyboard_picks_are_never_checked(tmp_path):
    plans, search, cfg, check, seen = setup({1: clipcheck.Picked([D, C])},
                                            clip_choices={0: [B]})
    render._check_clips(cfg, Ui(), plans, search, str(tmp_path), lambda: False, check)
    assert seen["scenes"] == [1] and cfg["clip_choices"] == {0: [B], 1: [D, C]}


def test_cancel_during_the_check_and_during_the_question(tmp_path):
    stop = threading.Event()
    plans, search, cfg, check, _ = setup({0: clipcheck.Picked([A])})

    def cancelling_check(*args, tell=None, **kwargs):
        stop.set()
        tell("Checking clips", 1, 2)

    with pytest.raises(render.RenderCancelled):
        render._check_clips(cfg, Ui(), plans, search, str(tmp_path), stop.is_set,
                            cancelling_check)
    plans, search, cfg, check, _ = setup({1: clipcheck.Picked([C, D], ask=True)},
                                         ask_clips=lambda items: None)
    with pytest.raises(render.RenderCancelled):
        render._check_clips(cfg, Ui(), plans, search, str(tmp_path), lambda: False, check)


def test_off_or_a_failing_check_leaves_the_video_alone(tmp_path):
    plans, search, cfg, check, seen = setup({0: clipcheck.Picked([B])}, check_clips=False)
    render._check_clips(cfg, Ui(), plans, search, str(tmp_path), lambda: False, check)
    assert seen == {} and "clip_choices" not in cfg

    def broken(*args, **kwargs):
        raise OSError("model gone")

    plans, search, cfg, _, _ = setup({})
    ui = Ui()
    render._check_clips(cfg, ui, plans, search, str(tmp_path), lambda: False, broken)
    assert "clip_choices" not in cfg and "not checked" in ui.logs[-1]
