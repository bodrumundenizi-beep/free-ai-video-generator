import numpy as np
import pytest
from vidgen import audio

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


def test_sound_effects_sit_before_each_cut_and_on_each_card():
    times = audio.effect_times([3.0, 6.0], [0.1, 6.1], 9.0)
    assert ("whoosh", 3.0 - audio.WHOOSH_LEAD) in times and ("pop", 6.1) in times
    assert len(times) == 4
    assert audio.effect_times([0.2], [], 9.0) == []          # too close to the start
    assert audio.effect_times([], [8.99], 9.0) == []         # past the end


def test_the_effects_are_quiet_stereo_sounds():
    for wave, gain in ((audio.whoosh(), audio.WHOOSH_GAIN), (audio.pop(), audio.POP_GAIN)):
        assert wave.ndim == 2 and wave.shape[1] == 2
        assert 0 < abs(wave).max() <= gain + 1e-6


def test_onsets_find_where_the_music_hits():
    rate = 1000
    samples = np.zeros(4 * rate, dtype=np.float32)
    for hit in (0.5, 1.5, 2.5):
        samples[int(hit * rate):int(hit * rate) + 100] = 1.0
    found = audio.onsets(samples, rate)
    assert len(found) == 3 and all(abs(f - h) < 0.03 for f, h in zip(found, (0.5, 1.5, 2.5)))


def test_cuts_move_onto_nearby_beats_but_never_cut_a_word():
    beats = [3.1, 6.4, 9.0]
    # cut 1 at 3.0 -> 3.1; cut 2 at 3.1 + 3.0 = 6.1 -> 6.4 is 0.3 away: stays
    assert audio.snap_cuts([3.0, 3.0, 3.0], [1, 1, 1], beats) == pytest.approx([3.1, 3.0, 3.0])
    # a beat earlier than the scene's words end is not used
    assert audio.snap_cuts([3.0, 3.0], [2.95, 1], [2.8]) == [3.0, 3.0]
