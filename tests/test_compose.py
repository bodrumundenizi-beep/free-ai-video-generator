"""compose.layered gives the frames MoviePy's CompositeVideoClip gives, faster."""
import numpy as np
import pytest

pytest.importorskip("moviepy")

from moviepy import ColorClip, CompositeVideoClip, VideoClip, vfx  # noqa: E402

from vidgen import compose  # noqa: E402

SIZE = (64, 96)


def noise(seed):
    rng = np.random.default_rng(seed)
    pictures = rng.integers(0, 255, (5, SIZE[1], SIZE[0], 3), dtype=np.uint8)
    return VideoClip(lambda t: pictures[int(t * 10) % 5], duration=1.0)


def strip(seed, width, height):
    rng = np.random.default_rng(seed)
    picture = rng.integers(0, 255, (height, width, 3), dtype=np.uint8)
    alpha = rng.random((height, width))
    mask = VideoClip(lambda t: alpha * min(1.0, t * 2), is_mask=True, duration=1.0)
    return VideoClip(lambda t: picture, duration=1.0).with_mask(mask)


def layers():
    first = noise(1).with_start(0)
    second = noise(2).with_effects([vfx.CrossFadeIn(0.4)]).with_start(0.7)   # overlaps first
    caption = strip(3, SIZE[0], 20).with_position((0, 60)).with_start(0).with_duration(1.7)
    card = strip(4, 50, 30).with_position((30, -10)).with_start(0.5)          # partly off-frame
    return [first, second, card, caption]


@pytest.mark.parametrize("t", [0.0, 0.3, 0.55, 0.75, 0.9, 1.2, 1.65])
def test_frames_match_moviepy(t):
    ours = compose.layered(layers(), SIZE, 1.7).get_frame(t)
    theirs = CompositeVideoClip(layers(), size=SIZE, bg_color=(0, 0, 0)).with_duration(1.7) \
        .get_frame(t)
    assert ours.shape == theirs.shape and ours.dtype == np.uint8
    assert np.abs(ours.astype(int) - theirs.astype(int)).max() <= 1   # rounding only


def test_nothing_playing_is_black():
    gap = noise(5).with_start(1.0)
    frame = compose.layered([gap], SIZE, 2.0).get_frame(0.5)
    assert frame.shape == (SIZE[1], SIZE[0], 3) and not frame.any()


def test_a_layer_over_a_colour_background_is_pasted():
    base = ColorClip(SIZE, color=(10, 20, 30), duration=1.0)
    frame = compose.layered([base], SIZE, 1.0).get_frame(0.2)
    assert (frame == (10, 20, 30)).all()
