import os

import pytest

from vidgen import animate, imagegen


class Done:
    """A finished program that wrote its clip."""

    def __init__(self, args, log_path, code=0):
        out = args[args.index("-o") + 1]
        if code == 0:
            open(out, "wb").write(b"avi")
        open(log_path, "w").write("ggml_vulkan: 0 = Card | x\nfailed: out of memory\n")
        self.returncode = code

    def poll(self):
        return self.returncode


def test_only_the_first_ai_scene_moves_unless_every_one_is_asked_for():
    assert animate.wanted(animate.FIRST, 3, 0) and not animate.wanted(animate.FIRST, 5, 1)
    assert animate.wanted(animate.ALL, 5, 4)
    assert not animate.wanted("Off", 0, 0) and not animate.wanted("", 0, 0)


def test_the_command_uses_the_video_mode_and_the_picture_as_first_frame():
    args = animate.command("sd-cli.exe", "pic.png", "a cat", (480, 832), "out.avi", 7)
    assert args[args.index("-M") + 1] == "vid_gen" and args[args.index("-i") + 1] == "pic.png"
    assert args[args.index("--video-frames") + 1] == str(animate.FRAMES)
    assert (animate.FRAMES - 1) % 4 == 0   # what the model needs
    assert args[args.index("-W") + 1] == "480"
    # one model at a time (4.4.1): never both the text encoder and the video model held at once
    assert args[args.index("--params-backend") + 1] == "disk" and "--offload-to-cpu" not in args


def test_a_clip_is_made_or_the_failure_says_why(tmp_path, monkeypatch):
    monkeypatch.setattr(animate, "installed", lambda: True)
    monkeypatch.setattr(imagegen, "program_path", lambda: "sd-cli.exe")
    from PIL import Image

    pic = str(tmp_path / "pic.png")
    Image.new("RGB", (576, 1024)).save(pic)
    out = str(tmp_path / "clip.avi")
    moved = animate.make(pic, "a cat", "9:16", out, launch=lambda a, p: Done(a, p),
                         memory=lambda: 16e9)
    with Image.open(out + ".first.png") as first:
        assert first.size == animate.SIZES["9:16"]
    assert os.path.isfile(out) and moved.seconds >= 0 and moved.frames == animate.FRAMES
    lines = []
    with pytest.raises(imagegen.ImageError) as caught:
        animate.make(pic, "a cat", "16:9", out, launch=lambda a, p: Done(a, p, 1),
                     log=lines.append, memory=lambda: 16e9)
    assert "out of memory" in caught.value.detail and lines


def test_without_the_download_it_says_so(monkeypatch):
    monkeypatch.setattr(animate, "installed", lambda: False)
    with pytest.raises(imagegen.ImageError, match="not downloaded"):
        animate.make("pic.png", "a cat", "9:16", "out.avi")


def test_a_crash_exit_code_is_named_and_counts_as_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(animate, "installed", lambda: True)
    monkeypatch.setattr(imagegen, "program_path", lambda: "sd-cli.exe")
    from PIL import Image

    pic = str(tmp_path / "pic.png")
    Image.new("RGB", (576, 1024)).save(pic)
    runs = []

    def crash(args, log_path):
        runs.append(args)
        return Done(args, log_path, 3221226505)   # 0xC0000409, as on the Intel laptop

    with pytest.raises(imagegen.ImageError) as caught:
        animate.make(pic, "a cat", "9:16", str(tmp_path / "c.avi"), launch=crash,
                     memory=lambda: 16e9)
    assert "0xC0000409" in caught.value.detail and "still picture" in str(caught.value)
    assert len(runs) == 1   # never run again with the same settings
    assert animate.exit_name(1) == "exit code 1" and animate.exit_name(None) == "no exit code"


def test_low_memory_makes_it_smaller_or_keeps_the_still():
    full = (animate.SIZES["9:16"], animate.FRAMES)
    small = (animate.SMALL_SIZES["9:16"], animate.SMALL_FRAMES)
    assert animate.fit("9:16", 9.0e9, shared=True) == full      # the lab laptop's 9 GB
    assert animate.fit("9:16", 6.8e9, shared=True) == small
    assert animate.fit("9:16", 5.0e9, shared=True) is None      # straight to the still
    assert animate.fit("9:16", 3.5e9, shared=False) == full     # its own graphics memory
    assert animate.fit("9:16", 2.0e9, shared=False) is None
    assert animate.fit("9:16", None, shared=True) == full       # unknown: try


def test_too_little_memory_never_starts_the_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(animate, "installed", lambda: True)
    started = []
    with pytest.raises(imagegen.ImageError, match="Not enough free memory"):
        animate.make("pic.png", "a cat", "9:16", str(tmp_path / "c.avi"), shared=True,
                     launch=lambda a, p: started.append(a), memory=lambda: 4e9)
    assert started == []


def test_in_a_render_a_crash_keeps_the_still_and_is_not_tried_again(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from vidgen import render
    from vidgen.script import parse_script

    calls = []

    def crash(*a, **k):
        calls.append(1)
        raise imagegen.ImageError("The picture could not be made to move; the still picture "
                                  "is used.", "exit code 3221226505 (0xC0000409: the engine crashed)")

    monkeypatch.setattr(animate, "installed", lambda: True)
    monkeypatch.setattr(animate, "make", crash)
    lines = []
    ui = SimpleNamespace(log=lines.append)
    cfg = {"animate": animate.ALL, "ai_gpu": "dedicated"}
    plans = []
    for n in range(2):
        plan = render._ScenePlan(parse_script(f"Visual: ai: a cat {n}\nVoice: Hi {n}.")[0])
        plan.sources, plan.kind = [render._Source(f"pic{n}.png", "image")], "still AI picture"
        render._moving_picture(plan, n, cfg, "9:16", str(tmp_path), ui, 1, n, lambda: False)
        plans.append(plan)
    assert calls == [1]                                    # the second scene doesn't retry
    assert all(p.sources[0].kind == "image" for p in plans)   # both keep their still
    assert render.scene_lines(plans) == [
        "🧩 Scene 1: still AI picture (moving picture failed, the still is used)",
        "🧩 Scene 2: still AI picture (moving picture skipped: it failed earlier in this video)"]


def test_one_log_line_per_scene_names_what_it_shows():
    from vidgen import render
    from vidgen.script import parse_script

    def plan(kind, note=""):
        p = render._ScenePlan(parse_script("Visual: x\nVoice: y.")[0])
        p.kind, p.note = kind, note
        return p

    lines = render.scene_lines([plan("moving AI picture"), plan("stock clip (Pixabay)"),
                                plan("stock clip (Pexels)", "AI picture failed, a stock clip is used"),
                                plan("your file")])
    assert lines == ["🧩 Scene 1: moving AI picture", "🧩 Scene 2: stock clip (Pixabay)",
                     "🧩 Scene 3: stock clip (Pexels) (AI picture failed, a stock clip is used)",
                     "🧩 Scene 4: your file"]


def test_built_in_graphics_keep_the_still_without_trying(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from vidgen import render
    from vidgen.script import parse_script

    monkeypatch.setattr(animate, "installed", lambda: True)
    monkeypatch.setattr(animate, "make", lambda *a, **k: pytest.fail("tried on built-in graphics"))
    lines = []
    plan = render._ScenePlan(parse_script("Visual: ai: a cat\nVoice: Hi.")[0])
    plan.sources = [render._Source("pic.png", "image")]
    render._moving_picture(plan, 0, {"animate": animate.ALL, "ai_gpu": "integrated"}, "9:16",
                           str(tmp_path), SimpleNamespace(log=lines.append), 1, 0, lambda: False)
    assert plan.sources[0].kind == "image" and "separate graphics card" in lines[0]


def test_a_scene_with_no_stock_clip_left_reuses_an_earlier_one(monkeypatch):
    from types import SimpleNamespace

    from vidgen import imagegen as ig, render
    from vidgen.script import parse_script

    monkeypatch.setattr(ig, "ready", lambda: (False, "not installed"))
    earlier = render._ScenePlan(parse_script("Visual: coffee\nVoice: Hi.")[0])
    earlier.sources = [render._Source("coffee.mp4", "video")]
    plan = render._ScenePlan(parse_script("Visual: tiramisu\nVoice: Yum.")[0])
    lines = []
    render._stand_in(plan, 1, [earlier], {}, "16:9", "", SimpleNamespace(log=lines.append), 1,
                     None, lambda: False, render.NoFootage("No usable stock footage"))
    assert plan.sources[0].path == "coffee.mp4" and "earlier scene" in lines[-1]
