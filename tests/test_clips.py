"""Stock clips cut to the frame once: the same framing as before, from smaller files."""
import os

import pytest

from vidgen import cache, clips, motion


def crop_at(size, frame, focus, scale):
    """The crop framed_shot takes at ``scale``, from motion.framed_shot's own arithmetic."""
    w, h = size
    aspect = frame[0] / frame[1]
    box_w, box_h = (h * aspect, float(h)) if w / h > aspect else (float(w), w / aspect)
    cx, cy = w / 2, h / 2
    if focus is not None:
        cx += (focus[0] * w - cx) * motion.FOCUS_PULL
        cy += (focus[1] * h - cy) * motion.FOCUS_PULL
    half_w, half_h = box_w / scale / 2, box_h / scale / 2
    x = min(max(cx, half_w), w - half_w)
    y = min(max(cy, half_h), h - half_h)
    return x - half_w, y - half_h, x + half_w, y + half_h


@pytest.mark.parametrize("focus", [None, (0.5, 0.5), (0.05, 0.3), (0.97, 0.9), (0.62, 0.1)])
@pytest.mark.parametrize("scale", [1.0, 1.03, 1.08, 1.10])
def test_the_cut_clip_frames_the_same_picture_at_every_zoom(focus, scale):
    size, frame = (3840, 2160), (1080, 1920)
    (left, top, cw, ch), out, new_focus = clips.plan_cut(size, frame, focus)
    before = crop_at(size, frame, focus, scale)
    after = crop_at(out, frame, new_focus, scale)
    k = cw / out[0]   # cut-clip pixels back to the download's
    after = (left + after[0] * k, top + after[1] * k, left + after[2] * k, top + after[3] * k)
    assert after == pytest.approx(before, abs=1.5)   # under two pixels of a 4K picture


def test_4k_for_a_short_is_cut_to_the_strip_it_shows_and_never_enlarged():
    (left, top, cw, ch), out, _ = clips.plan_cut((3840, 2160), (1080, 1920))
    assert (cw, ch) == (1215, 2160) and left == round((3840 - 1215) / 2)
    assert out[0] % 2 == 0 and out[1] % 2 == 0
    assert out[0] <= cw and out[0] >= 1080           # room for the zoom, but no upscaling
    big = clips.plan_cut((3840, 2160), (1920, 1080))[1]
    assert big == (2112, 1188)                        # 1920 x the deepest zoom


def test_a_clip_that_already_is_the_frame_is_left_alone():
    assert clips.plan_cut((1080, 1920), (1080, 1920)) is None
    assert clips.plan_cut((1920, 1080), (1920, 1080)) is None


@pytest.fixture
def clip_file(tmp_path):
    from vidgen import mastering

    path = tmp_path / "source.mp4"
    mastering.run_ffmpeg(["-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=60:duration=1",
                          "-pix_fmt", "yuv420p", str(path)])
    return path


def test_ready_cuts_once_and_a_second_time_downloads_nothing(tmp_path, clip_file, monkeypatch):
    import shutil

    downloads = []

    def download(url, dest, should_stop=None):
        downloads.append(url)
        shutil.copyfile(clip_file, dest)

    monkeypatch.setattr(clips.footage, "download", download)
    store = cache.Cache(str(tmp_path / "cache"), "clips")
    work = tmp_path / "work"
    work.mkdir()
    first = clips.ready("https://x/1.mp4", (180, 320), True, store, str(work), "a")
    second = clips.ready("https://x/1.mp4", (180, 320), True, store, str(work), "b")
    assert downloads == ["https://x/1.mp4"]
    assert first == second and os.path.isfile(first["path"]) and first["path"] != first["original"]

    from moviepy import VideoFileClip

    with VideoFileClip(first["path"], audio=False) as cut:
        assert cut.size == [198, 352] or tuple(cut.size) == (198, 352)   # 180 x 1.1, even
        assert cut.fps == pytest.approx(30, abs=0.5)                    # 60 fps halved
    # Another size is another cut of the same download.
    other = clips.ready("https://x/1.mp4", (320, 180), True, store, str(work), "c")
    assert downloads == ["https://x/1.mp4"] and other["path"] != first["path"]


def test_without_a_cache_everything_stays_in_the_work_folder(tmp_path, clip_file, monkeypatch):
    import shutil

    monkeypatch.setattr(clips.footage, "download",
                        lambda url, dest, should_stop=None: shutil.copyfile(clip_file, dest))
    got = clips.ready("https://x/2.mp4", (180, 320), False, cache.Cache(None, "clips"),
                      str(tmp_path), "z")
    assert os.path.dirname(got["path"]) == str(tmp_path) and got["focus"] is None
