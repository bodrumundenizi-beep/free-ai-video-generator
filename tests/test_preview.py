import os

import pytest

pytest.importorskip("imageio_ffmpeg")

from vidgen import mastering, preview  # noqa: E402
from vidgen.preview import FrameReader, fit, preview_size  # noqa: E402


# --- sizes -----------------------------------------------------------------------

def test_portrait_fits_the_portrait_box():
    assert preview_size((1080, 1920)) == (270, 480)
    assert preview_size((720, 1280)) == (270, 480)


def test_landscape_fits_the_landscape_box():
    assert preview_size((1920, 1080)) == (480, 270)


def test_display_scale_enlarges_the_preview():
    assert preview_size((1080, 1920), 1.5) == (404, 720)


def test_fit_keeps_the_shape_and_gives_even_sides():
    width, height = fit((1000, 333), (480, 270))
    assert width % 2 == 0 and height % 2 == 0
    assert width <= 480 and height <= 270
    assert width / height == pytest.approx(1000 / 333, rel=0.03)


def test_fit_never_enlarges_a_small_video():
    assert fit((200, 100), (480, 270)) == (200, 100)


# --- against a real (tiny) video ----------------------------------------------------

@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    """Two seconds of 320x180 test pattern with a beep, made by ffmpeg."""
    path = str(tmp_path_factory.mktemp("preview") / "clip.mp4")
    mastering.run_ffmpeg([
        "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path,
    ])
    return path


def test_probe_reads_size_and_length(clip):
    width, height, seconds = preview.probe(clip)
    assert (width, height) == (320, 180)
    assert seconds == pytest.approx(2.0, abs=0.2)


def test_poster_is_one_frame_at_the_asked_size(clip):
    image = preview.poster(clip, (160, 90), at=0.5)
    assert image.size == (160, 90) and image.mode == "RGB"
    assert len(set(image.getdata())) > 10  # a picture, not a blank frame


def test_frame_reader_yields_every_frame_then_none(clip):
    reader = FrameReader(clip, (160, 90), fps=12)
    try:
        frames = []
        while (frame := reader.read()) is not None:
            frames.append(frame)
    finally:
        reader.close()
    assert 22 <= len(frames) <= 26  # two seconds at 12 fps
    assert all(len(frame) == 160 * 90 * 3 for frame in frames)


def test_skip_to_catches_up_and_reports_the_end(clip):
    reader = FrameReader(clip, (160, 90), fps=12)
    try:
        assert reader.skip_to(9) is not None
        assert reader.index == 10
        assert reader.skip_to(500) is None
    finally:
        reader.close()


def test_close_stops_a_reader_mid_video(clip):
    reader = FrameReader(clip, (160, 90), fps=12)
    reader.read()
    reader.close()
    assert reader.process.poll() is not None


def test_extract_audio_writes_a_wav(clip, tmp_path):
    wav = preview.extract_audio(clip, str(tmp_path / "sound.wav"))
    with open(wav, "rb") as fh:
        assert fh.read(4) == b"RIFF"
    assert os.path.getsize(wav) > 44100  # about two seconds of stereo PCM
