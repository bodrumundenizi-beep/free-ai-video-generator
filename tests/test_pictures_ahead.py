"""AI pictures drawn on a thread while the voices are made (render._start_pictures_ahead)."""
import threading
from types import SimpleNamespace

import pytest

from vidgen import imagegen, render, voices
from vidgen.script import parse_script

SCRIPT = "\n\n".join(f"Visual: ai: picture {n}\nVoice: Line {n}." for n in range(4))


def ui():
    return SimpleNamespace(log=lambda _line: None, ai_backend=lambda _b: None,
                           ai_speed=lambda *_a: None)


def plans(script=SCRIPT):
    return [render._ScenePlan(scene) for scene in parse_script(script)]


@pytest.fixture
def drawing(monkeypatch):
    """A fake imagegen.make that records which thread drew what, and can fail one prompt."""
    seen = SimpleNamespace(calls=[], fail=None, gate=threading.Event())
    seen.gate.set()

    def make(prompt, size, path, look, **kwargs):
        seen.gate.wait(5)
        if kwargs["should_stop"]():
            raise imagegen.Stopped()
        seen.calls.append((prompt, threading.current_thread().name))
        if prompt == seen.fail:
            raise imagegen.ImageError("could not be made")
        open(path, "wb").write(b"png")
        return imagegen.Made(1.0, imagegen.GPU, size)

    monkeypatch.setattr(imagegen, "make", make)
    monkeypatch.setattr(imagegen, "ready", lambda: (True, ""))
    yield seen
    imagegen.serve(False)


def run(cfg, tmp_path, stop=lambda: False, script=SCRIPT, providers=("pexels",)):
    """Start drawing ahead, then fetch every scene like render step 2 does."""
    made = plans(script)
    render._start_pictures_ahead(made, cfg, "9:16", str(tmp_path), ui(), stop)
    used = [render._ai_picture(p, i, cfg, "9:16", str(tmp_path), ui(), 1,
                               SimpleNamespace(providers=list(providers)), stop)
            for i, p in enumerate(made)]
    return made, used


def test_the_pictures_are_drawn_ahead_in_order_and_each_is_used_once(drawing, tmp_path):
    cfg = {}
    made, used = run(cfg, tmp_path)
    assert used == [True] * 4
    assert [prompt for prompt, _t in drawing.calls] == [f"picture {n}" for n in range(4)]
    assert {thread for _p, thread in drawing.calls} == {"pictures-ahead"}
    assert all(p.sources[0].path.endswith(f"ai_1_{i}.png") for i, p in enumerate(made))
    cfg.pop("_ahead").close()


@pytest.mark.parametrize("cfg", [
    {"ai_images_backend": imagegen.CPU},          # both would need the processor
    {"voice": voices.MY_VOICE},                   # My voice uses the graphics card too
    {"animate": "First AI scene"},                # the video model needs the card's memory
    {"resolution": render.DRAFT},                 # a draft makes no AI pictures at all
])
def test_no_drawing_ahead_where_it_would_get_in_the_way(drawing, tmp_path, cfg, monkeypatch):
    if cfg.get("voice"):
        monkeypatch.setattr(voices, "persona", lambda _v: SimpleNamespace(id=voices.MY_VOICE))
    render._start_pictures_ahead(plans(), cfg, "9:16", str(tmp_path), ui(), lambda: False)
    assert "_ahead" not in cfg and drawing.calls == []


def test_a_slow_pc_reusing_pictures_only_draws_the_first_ones_ahead(drawing, tmp_path):
    cfg = {"ai_reuse": 2}
    run(cfg, tmp_path)
    assert [prompt for prompt, _t in drawing.calls] == ["picture 0", "picture 1"]
    cfg.pop("_ahead").close()


def test_a_failed_picture_becomes_a_stock_scene_and_the_rest_still_come(drawing, tmp_path):
    drawing.fail = "picture 1"
    cfg = {}
    made, used = run(cfg, tmp_path)
    assert used == [True, False, True, True]
    cfg.pop("_ahead").close()


def test_cancel_while_waiting_for_a_picture_cancels_the_render(drawing, tmp_path):
    drawing.gate.clear()   # the first picture never finishes
    cancel = threading.Event()
    cfg = {}
    made = plans()
    render._start_pictures_ahead(made, cfg, "9:16", str(tmp_path), ui(), cancel.is_set)
    cancel.set()
    with pytest.raises(render.RenderCancelled):
        render._ai_picture(made[0], 0, cfg, "9:16", str(tmp_path), ui(), 1,
                           SimpleNamespace(providers=["pexels"]), cancel.is_set)
    drawing.gate.set()
    cfg.pop("_ahead").close()
    assert drawing.calls == []   # it saw the cancel and drew nothing
