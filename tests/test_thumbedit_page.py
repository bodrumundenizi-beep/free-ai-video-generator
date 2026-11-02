"""The Thumbnails page's helpers: pictures of a video that is not open, saving over a made
video's thumbnail, Start over and free thumbnails."""

import os

import pytest
from PIL import Image

pytest.importorskip("imageio_ffmpeg")

from vidgen import mastering, thumbedit, thumbnail  # noqa: E402


def _video(path, size):
    mastering.run_ffmpeg([
        "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=30:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)])
    return str(path)


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    folder = tmp_path_factory.mktemp("made")
    return {"wide": _video(folder / "wide.mp4", "320x180"),
            "tall": _video(folder / "tall.mp4", "180x320")}


def _project(tmp_path):
    folder = tmp_path / "project"
    (folder / "pictures").mkdir(parents=True)
    Image.new("RGB", (64, 36), (200, 0, 0)).save(folder / "pictures" / "a.png")
    Image.new("RGB", (64, 36), (0, 0, 200)).save(folder / "thumb.png")
    return str(folder)


def test_video_backgrounds_are_frames_then_ai_pictures_then_the_first(tmp_path, made):
    folder = _project(tmp_path)
    found = thumbedit.video_backgrounds(folder, [str(tmp_path / "gone.mp4"), made["wide"]], 3)
    assert [label for label, _ in found] == ["Frame 1", "Frame 2", "Frame 3", "AI picture 1",
                                             "First picture"]
    assert all(os.path.isfile(path) for _, path in found)
    with Image.open(found[0][1]) as frame:
        assert frame.size == (320, 180)
    again = thumbedit.video_backgrounds(folder, [made["wide"]], 3)   # the frames are kept
    assert again[:3] == found[:3]


def test_a_missing_video_gives_only_the_pictures(tmp_path):
    folder = _project(tmp_path)
    assert [label for label, _ in thumbedit.video_backgrounds(folder, [])] == [
        "AI picture 1", "First picture"]
    assert thumbedit.video_backgrounds(str(tmp_path / "nothing"), None) == []


def test_saving_replaces_only_same_shape_video_thumbnails(tmp_path, made):
    folder = _project(tmp_path)
    picture = thumbedit.save(folder, thumbedit.blank(thumbnail.LANDSCAPE), thumbnail.LANDSCAPE)
    tall_thumb = os.path.splitext(made["tall"])[0] + ".thumbnail.jpg"
    Image.new("RGB", (10, 10)).save(tall_thumb)
    done = thumbedit.replace_video_thumbnails(
        picture, [made["wide"], made["tall"], str(tmp_path / "gone.mp4")], portrait=False)
    assert done == [os.path.splitext(made["wide"])[0] + ".thumbnail.jpg"]
    with Image.open(done[0]) as image:
        assert image.size == (1280, 720)
    with Image.open(tall_thumb) as image:
        assert image.size == (10, 10)   # the other shape is left alone


def test_export_writes_jpg_or_png_of_the_same_size(tmp_path):
    folder = str(tmp_path / "free")
    picture = thumbedit.save(folder, thumbedit.blank(thumbnail.PORTRAIT), thumbnail.PORTRAIT)
    for name in ("out.jpg", "out.png"):
        with Image.open(thumbedit.export(picture, str(tmp_path / name))) as image:
            assert image.size == (720, 1280) and image.format == ("JPEG" if name.endswith(
                "jpg") else "PNG")


def test_start_over_renders_at_both_sizes_and_round_trips(tmp_path):
    for size in (thumbnail.LANDSCAPE, thumbnail.PORTRAIT):
        layers = thumbedit.blank(size)
        assert [layer["kind"] for layer in layers] == ["background", "text"]
        assert thumbedit.render(layers, size).size == size
        folder = str(tmp_path / f"free-{size[0]}")
        thumbedit.save(folder, layers, size)
        assert thumbedit.load(folder) == (layers, size)
