from types import SimpleNamespace

import pytest

from vidgen import pacing
from vidgen.pacing import (
    PAD_MAX, PAD_MIN, TEMPO_MAX, TEMPO_MIN, describe, estimate, estimate_voice, fit,
    rate_factor, settle_padding, words_to_change,
)


def scene(voice, duration=None, padding=None):
    return SimpleNamespace(voice=voice, duration=duration, padding=padding)


GUY = SimpleNamespace(rate="+8%", pause_scale=0.8, enhanced=True)
DEEP = SimpleNamespace(rate="-2%", pause_scale=1.3, enhanced=True)
LINE = "Your PC could be hoarding gigabytes of junk you will never use."


# --- fitting a target ------------------------------------------------------------

def test_pauses_alone_reach_a_nearby_target():
    result = fit(voice_seconds=26.0, flexible_scenes=6, fixed_seconds=0.0, target=30.0)
    assert result.tempo == 1.0 and result.reached
    assert result.padding == pytest.approx(4.0 / 6)
    assert result.achieved == 30.0


def test_a_long_script_speeds_the_voice_up_with_short_pauses():
    result = fit(33.0, 6, 0.0, 30.0)
    assert result.padding == PAD_MIN
    assert 1.0 < result.tempo <= TEMPO_MAX
    assert result.reached and result.achieved == pytest.approx(30.0)


def test_a_short_script_slows_the_voice_down_with_long_pauses():
    result = fit(22.0, 6, 0.0, 30.0)
    assert result.padding == PAD_MAX
    assert TEMPO_MIN <= result.tempo < 1.0
    assert result.reached and result.achieved == pytest.approx(30.0)


@pytest.mark.parametrize("voice,target", [(60.0, 15.0), (3.0, 60.0), (500.0, 30.0), (0.5, 30.0)])
def test_tempo_never_leaves_its_limits(voice, target):
    result = fit(voice, 5, 0.0, target)
    assert TEMPO_MIN <= result.tempo <= TEMPO_MAX
    assert PAD_MIN <= result.padding <= PAD_MAX


def test_an_unreachable_target_reports_the_closest_length():
    result = fit(48.0, 6, 0.0, 30.0)
    assert not result.reached
    assert result.tempo == TEMPO_MAX and result.padding == PAD_MIN
    assert result.achieved == pytest.approx(48.0 / TEMPO_MAX + 6 * PAD_MIN)
    assert result.achieved > 30.0


def test_fixed_scenes_are_counted_but_never_changed():
    # 10 s of the video is fixed, so the flexible scenes only have 20 s to fill.
    result = fit(18.0, 4, 10.0, 30.0)
    assert result.reached and result.tempo == 1.0
    assert result.padding == pytest.approx(0.5)


def test_nothing_flexible_means_nothing_to_adjust():
    result = fit(0.0, 0, 12.0, 30.0)
    assert (result.tempo, result.padding, result.achieved, result.reached) == (1.0, 0.0, 12.0, False)


def test_settle_padding_absorbs_what_the_retimed_voice_left_over():
    assert settle_padding(27.3, 6, 0.0, 30.0) == pytest.approx(0.45)
    assert settle_padding(40.0, 6, 0.0, 30.0) == PAD_MIN
    assert settle_padding(5.0, 6, 0.0, 30.0) == PAD_MAX


def test_words_to_cut_or_add():
    # 40 s of speech in 100 words: 2.5 words a second.
    assert words_to_change(41.0, 30.0, 100, 40.0) == 28
    assert words_to_change(20.0, 30.0, 100, 40.0) == -25
    assert words_to_change(41.0, 30.0, 0, 0.0) == 0


# --- the estimate ----------------------------------------------------------------

def test_rate_strings():
    assert rate_factor("+8%") == pytest.approx(1.08)
    assert rate_factor("-2%") == pytest.approx(0.98)
    assert rate_factor("") == 1.0 and rate_factor("fast") == 1.0


def test_estimate_grows_with_the_text():
    assert estimate_voice(LINE + " " + LINE) > 1.9 * estimate_voice(LINE)
    assert estimate_voice("") == 0.0


def test_a_slower_voice_with_longer_pauses_takes_longer():
    quick = estimate_voice(LINE, GUY.rate, GUY.pause_scale)
    slow = estimate_voice(LINE, DEEP.rate, DEEP.pause_scale)
    assert slow > quick


def test_classic_voices_have_no_added_pauses():
    text = "One. Two, three. Four."
    assert estimate_voice(text, enhanced=False) < estimate_voice(text, enhanced=True)


def test_estimate_splits_flexible_from_fixed():
    scenes = [scene(LINE), scene(LINE, padding=1.0), scene(None, duration=2.0), scene(LINE)]
    guess = estimate(scenes, GUY, 0.25)
    one = estimate_voice(LINE, GUY.rate, GUY.pause_scale)
    assert guess.flexible == 2
    assert guess.voice == pytest.approx(2 * one)
    assert guess.fixed == pytest.approx(one + 1.0 + 2.0)
    assert guess.total == pytest.approx(guess.fixed + guess.voice + 2 * 0.25)


def test_describe_plain_fitted_and_warning():
    guess = estimate([scene(LINE)] * 6, GUY, 0.25)
    text, warn = describe(guess, 6, None)
    assert text == f"About {round(guess.total)} s · 6 scenes" and not warn
    comfortable = estimate([scene(LINE)] * 7, GUY, 0.25)   # about 29 s as written
    text, warn = describe(comfortable, 7, 30.0)
    assert "will be fitted to 30 s" in text and not warn
    text, warn = describe(guess, 6, 15.0)
    assert "too long for 15 s" in text and warn
    text, warn = describe(estimate([scene("Hi.")], GUY, 0.25), 1, 60.0)
    assert "too short for 60 s" in text and warn and "1 scene" in text


def test_describe_does_not_cry_wolf_near_the_limit():
    # An estimate can be 15% out, so a script that only just misses on paper
    # is called close, not too long.
    borderline = pacing.Estimate(total=36.5, voice=35.0, flexible=6, fixed=0.0)
    assert not fit(35.0, 6, 0.0, 30.0).reached
    text, warn = describe(borderline, 6, 30.0)
    assert "close to the limit for 30 s" in text and not warn


def test_targets_table():
    assert pacing.TARGETS[pacing.DEFAULT_TARGET] is None
    assert [v for v in pacing.TARGETS.values() if v] == [15.0, 30.0, 60.0]
