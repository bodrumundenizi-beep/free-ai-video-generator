"""The recording cut to the tutorial's clock (5.4, plan step 4)."""
import os

import pytest

from PIL import Image

from vidgen.mastering import run_ffmpeg
from vidgen.tutorial import frames
from vidgen.tutorial import build as B


def test_only_the_window_and_its_menus_show():
    mask = frames.mask_for((200, 100), [20, 10, 120, 60], [[150, 20, 30, 40]], corner=6)
    assert mask.getpixel((80, 40)) == 255          # the window
    assert mask.getpixel((5, 5)) == 0              # someone else's screen in the margin
    assert mask.getpixel((20, 10)) == 0            # a rounded corner
    assert mask.getpixel((160, 30)) == 255         # the program's own menu


def test_private_areas_become_blocks():
    picture = Image.new("RGB", (100, 60), "white")
    for x in range(30, 70, 2):                      # fine "text"
        picture.paste((0, 0, 0), (x, 20, x + 1, 30))
    frames.pixelate(picture, [[30, 20, 40, 10]], block=10)
    row = [picture.getpixel((x, 25)) for x in range(30, 70)]
    assert len(set(row)) <= 6                      # the stripes are gone


def test_frames_follow_the_anchors_and_never_go_back():
    pairs = [[0, 0], [1.0, 5.0], [1.3, 5.0], [2.0, 6.0]]   # skip 4 s, hold 0.3 s, then real speed
    wanted = frames.sources(pairs, 2.0, fps=10)
    assert wanted[0] == 0 and wanted == sorted(wanted)
    assert wanted[10] == pytest.approx(50) and wanted[12] == pytest.approx(50)   # the hold
    # the speed eases: no jump from one frame step to the next bigger than needed
    steps = [b - a for a, b in zip(wanted, wanted[1:10])]
    assert max(abs(b - a) for a, b in zip(steps, steps[1:])) < 4


def test_cut_writes_one_see_through_picture_per_frame(tmp_path):
    video = str(tmp_path / "recording.mp4")
    run_ffmpeg(["-y", "-f", "lavfi", "-i", "testsrc=size=200x100:rate=30:duration=3",
                "-pix_fmt", "yuv420p", video])
    log = {"window": [20, 10, 120, 60], "area": [0, 0, 200, 100], "length": 3.0,
           "popups": [[0, []], [1.0, [[150, 20, 30, 40]]], [2.0, []]], "private": [[30, 20, 40, 10]]}
    timeline = {"length": 2.0, "anchors": [[0, 0], [2.0, 3.0]]}   # 1.5x faster
    out = str(tmp_path / "frames")
    entry = frames.cut(video, log, timeline, out)
    assert entry == {"pattern": "frame_%05d.webp", "count": 60, "fps": 30,
                     "back": "back.jpg", "window": [20, 10, 120, 60]}
    assert len([n for n in os.listdir(out) if n.endswith(".webp")]) == 60
    assert os.path.isfile(os.path.join(out, "back.jpg"))   # blurred once, not every frame
    early = Image.open(os.path.join(out, "frame_00003.webp"))
    menu_open = Image.open(os.path.join(out, "frame_00030.webp"))   # recording 1.5 s
    assert early.mode == "RGBA"
    assert early.getpixel((5, 5))[3] == 0 and early.getpixel((80, 40))[3] == 255
    assert early.getpixel((160, 30))[3] == 0 and menu_open.getpixel((160, 30))[3] == 255


def test_picks_hold_their_menu_and_clicks_play_at_real_speed():
    import json
    with open(os.path.join(os.path.dirname(__file__), "fixtures", "tutorial_ref",
                           "golden_log.json"), encoding="utf-8") as f:
        log = json.load(f)
    built = B.build(log)
    pairs = built["anchors"]
    gold = built["clicks"][6][0]
    # the Gold pick: a still frame from just before its press, for SCREEN_AFTER_PICK
    assert B.to_recording(pairs, gold) == B.to_recording(pairs, gold + B.SCREEN_AFTER_PICK - 0.01)
    assert B.to_recording(pairs, gold) < 12.5
    # Insert (not a pick): real speed round the click
    insert = built["clicks"][0][0]
    rate = (B.to_recording(pairs, insert + 0.3) - B.to_recording(pairs, insert)) / 0.3
    assert abs(rate - 1) < 0.01


def _two_colour_video(path, second):
    run_ffmpeg(["-y", "-f", "lavfi", "-i", "color=white:s=200x100:d=1:r=30", "-f", "lavfi", "-i",
                f"color={second}:s=200x100:d=2:r=30", "-filter_complex", "[0:v][1:v]concat=n=2[v]",
                "-map", "[v]", "-pix_fmt", "yuv420p", path])


def test_a_drag_that_draws_a_shape_grows_smoothly_in_its_colour(tmp_path):
    video = str(tmp_path / "recording.mp4")
    _two_colour_video(video, "0x165f82")          # empty, then a blue shape after the drag
    timeline = {"anchors": [[0, 0], [2.0, 0.6], [3.0, 1.2], [4.0, 2.5]],
                "cursor": [[0, 20, 20], [2.0, 20, 20], [3.0, 180, 90]],
                "drags": [{"from": 2.0, "until": 3.1, "press_until": 3.0, "x": 20, "y": 20}]}
    out = frames.smooth_drawing(timeline, video, str(tmp_path))
    drag = out["drags"][0]
    assert drag["fill"].startswith("#1") and drag["edge"]
    # the recording holds before the press while the smooth shape grows
    assert B.to_recording(out["anchors"], 2.0) == pytest.approx(B.to_recording(out["anchors"], 3.0))


def test_a_drag_that_moves_something_keeps_the_real_video(tmp_path):
    video = str(tmp_path / "recording.mp4")
    _two_colour_video(video, "white")             # nothing new appears
    timeline = {"anchors": [[0, 0], [2.0, 0.6], [3.0, 1.2], [4.0, 2.5]],
                "cursor": [[0, 20, 20], [3.0, 180, 90]],
                "drags": [{"from": 2.0, "until": 3.1, "press_until": 3.0, "x": 20, "y": 20}]}
    out = frames.smooth_drawing(timeline, video, str(tmp_path))
    assert "fill" not in out["drags"][0]
    assert out["anchors"] == [[0, 0], [2.0, 0.6], [3.0, 1.2], [4.0, 2.5]]
