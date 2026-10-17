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
    assert "--offload-to-cpu" in args and args[args.index("-W") + 1] == "480"


def test_a_clip_is_made_or_the_failure_says_why(tmp_path, monkeypatch):
    monkeypatch.setattr(animate, "installed", lambda: True)
    monkeypatch.setattr(imagegen, "program_path", lambda: "sd-cli.exe")
    from PIL import Image

    pic = str(tmp_path / "pic.png")
    Image.new("RGB", (576, 1024)).save(pic)
    out = str(tmp_path / "clip.avi")
    seconds = animate.make(pic, "a cat", "9:16", out, launch=lambda a, p: Done(a, p))
    with Image.open(out + ".first.png") as first:
        assert first.size == animate.SIZES["9:16"]
    assert os.path.isfile(out) and seconds >= 0
    lines = []
    with pytest.raises(imagegen.ImageError) as caught:
        animate.make(pic, "a cat", "16:9", out, launch=lambda a, p: Done(a, p, 1),
                     log=lines.append)
    assert "out of memory" in caught.value.detail and lines


def test_without_the_download_it_says_so(monkeypatch):
    monkeypatch.setattr(animate, "installed", lambda: False)
    with pytest.raises(imagegen.ImageError, match="not downloaded"):
        animate.make("pic.png", "a cat", "9:16", "out.avi")
