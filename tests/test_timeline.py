import pytest

from vidgen.timeline import CROSSFADE, boundaries, crossfades, shot_spans


def test_boundaries_accumulate():
    assert boundaries([2.0, 3.0, 1.5]) == [0.0, 2.0, 5.0, 6.5]


def test_outer_boundaries_never_fade():
    fades = crossfades([4.0, 4.0, 4.0])
    assert fades[0] == 0.0 and fades[-1] == 0.0
    assert fades[1] == fades[2] == CROSSFADE


def test_short_scenes_cap_their_fades():
    # A 0.4 s scene can't give away more than half of itself to each side.
    assert crossfades([4.0, 0.4, 4.0]) == [0.0, 0.2, 0.2, 0.0]


def test_total_length_is_the_sum_of_scenes():
    durations = [3.2, 5.0, 1.1, 7.4]
    shots = shot_spans(durations, [1, 1, 1, 1])
    assert shots[0].start == 0.0
    assert shots[-1].end == pytest.approx(sum(durations))


def test_crossfade_is_centred_on_the_cut():
    shots = shot_spans([4.0, 4.0], [1, 1])
    first, second = shots
    cut = 4.0
    assert second.fade_in == CROSSFADE
    assert second.start == pytest.approx(cut - CROSSFADE / 2)
    assert first.end == pytest.approx(cut + CROSSFADE / 2)
    # The overlap is exactly the fade, so the fade finishes as the old shot ends.
    assert first.end - second.start == pytest.approx(second.fade_in)


def test_first_shot_has_no_fade():
    assert shot_spans([3.0, 3.0], [1, 1])[0].fade_in == 0.0


def test_split_scene_cuts_hard_at_its_midpoint():
    shots = shot_spans([4.0, 8.0, 4.0], [1, 2, 1])
    assert [(s.scene, s.part) for s in shots] == [(0, 0), (1, 0), (1, 1), (2, 0)]
    a, b = shots[1], shots[2]
    assert a.end == b.start == pytest.approx(4.0 + 8.0 / 2)
    assert a.fade_in == CROSSFADE and b.fade_in == 0.0


def test_every_shot_has_positive_length():
    shots = shot_spans([0.5, 0.5, 9.0, 0.5], [1, 1, 2, 1])
    assert all(s.length > 0 for s in shots)


def test_single_scene():
    (shot,) = shot_spans([5.0], [1])
    assert (shot.start, shot.end, shot.fade_in) == (0.0, 5.0, 0.0)


def test_mismatched_lengths_are_rejected():
    with pytest.raises(ValueError):
        shot_spans([1.0, 2.0], [1])
