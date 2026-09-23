import numpy as np
import pytest

pytest.importorskip("moviepy")

from moviepy import ColorClip, ImageClip, VideoClip  # noqa: E402

from vidgen.motion import (  # noqa: E402
    SLOW_MOTION, ZOOM_EXPLICIT, framed_shot, measure_motion, smoothstep, zoom_range,
)


def marker_image(w=1600, h=900, box=100):
    """Black frame with a white square in the exact centre."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[h // 2 - box // 2:h // 2 + box // 2, w // 2 - box // 2:w // 2 + box // 2] = 255
    return img


def marker_width(frame):
    row = frame[frame.shape[0] // 2, :, 0]
    return int((row > 127).sum())


def test_output_is_exactly_the_target_size():
    shot = framed_shot(ImageClip(marker_image()), 2.0, (720, 1280))
    assert shot.size == (720, 1280)
    assert shot.get_frame(0).shape == (1280, 720, 3)
    assert shot.duration == 2.0


def test_centre_crop_keeps_the_centre():
    frame = framed_shot(ImageClip(marker_image()), 1.0, (720, 1280)).get_frame(0.5)
    assert frame[640, 360].tolist() == [255, 255, 255]


def test_zoom_in_grows_the_subject_by_the_zoom_factor():
    shot = framed_shot(ImageClip(marker_image()), 2.0, (720, 1280), (1.0, ZOOM_EXPLICIT))
    first, last = marker_width(shot.get_frame(0)), marker_width(shot.get_frame(2.0))
    assert last / first == pytest.approx(ZOOM_EXPLICIT, rel=0.03)


def test_zoom_out_shrinks_it():
    shot = framed_shot(ImageClip(marker_image()), 2.0, (720, 1280), (ZOOM_EXPLICIT, 1.0))
    assert marker_width(shot.get_frame(2.0)) < marker_width(shot.get_frame(0))


def test_zoom_moves_smoothly_without_steps():
    # Sub-pixel crop boxes: the scale changes a little every frame, never
    # holding still and then jumping a whole pixel.
    shot = framed_shot(ImageClip(marker_image(box=400)), 3.0, (720, 1280), (1.0, 1.1))
    means = [shot.get_frame(t).mean() for t in np.arange(0.5, 2.5, 1 / 30)]
    steps = np.diff(means)
    assert (steps > 0).all()


def test_short_sources_loop_to_length():
    source = ColorClip((640, 360), color=(10, 20, 30), duration=0.5)
    shot = framed_shot(source, 2.0, (1280, 720))
    assert shot.duration == 2.0
    assert shot.get_frame(1.9).shape == (720, 1280, 3)


def test_smoothstep_eases_in_and_out():
    assert smoothstep(0) == 0 and smoothstep(1) == 1 and smoothstep(0.5) == 0.5
    assert smoothstep(0.1) < 0.1 and smoothstep(0.9) > 0.9


@pytest.mark.parametrize("mode,kind,motion,flip,expected", [
    ("none", "video", 0.0, False, (1.0, 1.0)),
    ("in", "video", 50.0, False, (1.0, 1.10)),
    ("out", "image", None, True, (1.10, 1.0)),
    ("auto", "image", None, False, (1.0, 1.08)),
    ("auto", "image", None, True, (1.08, 1.0)),
    ("auto", "video", 1.0, False, (1.0, 1.06)),
    ("auto", "video", 30.0, False, (1.0, 1.03)),
])
def test_zoom_range(mode, kind, motion, flip, expected):
    assert zoom_range(mode, kind, motion, flip) == pytest.approx(expected)


def test_measure_motion_tells_static_from_moving():
    still = ColorClip((320, 180), color=(90, 90, 90), duration=2)

    def moving_frame(t):
        img = np.zeros((180, 320, 3), dtype=np.uint8)
        x = int(t * 200) % 240
        img[:, x:x + 80] = 255
        return img

    moving = VideoClip(moving_frame, duration=2)
    assert measure_motion(still) < SLOW_MOTION
    assert measure_motion(moving) > SLOW_MOTION
