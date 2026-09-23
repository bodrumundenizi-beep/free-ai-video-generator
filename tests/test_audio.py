import numpy as np
import pytest

from vidgen.audio import (
    ATTACK, BASE_GAIN, DUCK_GAIN, RELEASE, gain_curve, merge_intervals, speech_bounds,
)


def gain_at(times, gains, t):
    return float(np.interp(t, times, gains))


def test_merge_joins_overlaps_and_short_gaps():
    assert merge_intervals([(5, 6), (0, 2), (1.5, 3)]) == [(0, 3), (5, 6)]
    # A gap shorter than attack + release would only make the music pump.
    assert merge_intervals([(0, 2), (2.2, 4)]) == [(0, 4)]


def test_merge_keeps_real_pauses():
    assert merge_intervals([(0, 2), (3, 4)]) == [(0, 2), (3, 4)]


def test_merge_ignores_empty_spans():
    assert merge_intervals([(1, 1), (2, 1)]) == []


def test_music_sits_at_base_level_in_pauses_and_ducks_under_speech():
    times, gains = gain_curve([(2.0, 5.0)], 10.0)
    assert gain_at(times, gains, 0.5) == pytest.approx(BASE_GAIN)
    assert gain_at(times, gains, 3.5) == pytest.approx(DUCK_GAIN)
    assert gain_at(times, gains, 9.0) == pytest.approx(BASE_GAIN)


def test_ducking_starts_before_the_voice():
    times, gains = gain_curve([(2.0, 5.0)], 10.0)
    assert gain_at(times, gains, 2.0 - ATTACK - 0.01) == pytest.approx(BASE_GAIN)
    assert gain_at(times, gains, 2.0) == pytest.approx(DUCK_GAIN)
    assert DUCK_GAIN < gain_at(times, gains, 2.0 - ATTACK / 2) < BASE_GAIN


def test_release_is_gradual():
    times, gains = gain_curve([(2.0, 5.0)], 10.0)
    mid = gain_at(times, gains, 5.0 + RELEASE / 2)
    assert DUCK_GAIN < mid < BASE_GAIN
    assert gain_at(times, gains, 5.0 + RELEASE + 0.01) == pytest.approx(BASE_GAIN)


def test_gain_never_leaves_its_range():
    times, gains = gain_curve([(0.05, 1.0), (1.2, 2.0), (6.0, 9.95)], 10.0)
    assert gains.min() >= DUCK_GAIN - 1e-9
    assert gains.max() <= BASE_GAIN + 1e-9


def test_speech_bounds_ignore_padding_silence():
    fps = 1000
    samples = np.zeros(3000)
    samples[200:2100] = np.sin(np.arange(1900) * 0.3)  # speech from 0.2 s to 2.1 s
    start, end = speech_bounds(samples, fps)
    assert start == pytest.approx(0.2, abs=0.02)
    assert end == pytest.approx(2.1, abs=0.02)


def test_speech_bounds_of_silence_is_none():
    assert speech_bounds(np.zeros((500, 2)), 1000) is None


def test_no_speech_means_constant_music():
    times, gains = gain_curve([], 3.0)
    assert np.allclose(gains, BASE_GAIN)
    assert times[-1] >= 3.0
