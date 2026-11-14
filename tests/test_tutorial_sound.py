"""A tutorial's sound (5.4, plan E): voice lines, clicks, music that dips, -25 LUFS."""
import json
import os

import numpy as np
import pytest

from vidgen import mastering
from vidgen.promo.sound import MUSIC_GAIN, RATE, VOICE_DUCK, _write
from vidgen.tutorial import sound

REF = os.path.join(os.path.dirname(__file__), "fixtures", "tutorial_ref")
SFX = os.path.join(os.path.dirname(__file__), "..", "assets", "sfx")


def reference():
    with open(os.path.join(REF, "timeline.json"), encoding="utf-8") as f:
        return json.load(f)


def tone_say(folder, seconds=1.0):
    """A pretend voice: a tone as long as ``seconds`` for every line."""
    said = []

    def say(text):
        said.append(text)
        t = np.arange(int(seconds * RATE)) / RATE
        wave = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        path = os.path.join(folder, f"line_{len(said)}.wav")
        _write(path, np.stack([wave, wave], axis=1))
        return path
    say.said = said
    return say


def test_the_title_and_each_step_are_read():
    got = sound.lines(reference())
    assert got[0] == (sound.TITLE_AT, "Add a coloured rectangle.")
    assert [text for _t, text in got[1:]] == [
        "Open Insert, then Shapes.", "Drag on the slide to draw it.",
        "Shape Format, then Shape Fill.", "Pick a colour."]
    assert got[1][0] == pytest.approx(1.3 + sound.VOICE_AFTER)


def test_lines_never_overlap(tmp_path):
    timeline = reference()
    say = tone_say(str(tmp_path), seconds=4.5)     # longer than a step: the next one waits
    _track, spoken = sound.voice_track(timeline, say, timeline["length"])
    for (a0, a1), (b0, _b1) in zip(spoken, spoken[1:]):
        assert b0 >= a1 + sound.GAP - 1e-6


def test_the_music_dips_only_while_the_voice_speaks():
    n = 10 * RATE
    gain = sound.duck(n, [(2.0, 3.0)])
    assert gain[int(1.0 * RATE)] == pytest.approx(MUSIC_GAIN)
    assert gain[int(2.5 * RATE)] == pytest.approx(VOICE_DUCK)
    assert gain[int(5.0 * RATE)] == pytest.approx(MUSIC_GAIN)
    assert VOICE_DUCK < gain[int(1.9 * RATE)] < MUSIC_GAIN      # eased, not a jump


def test_the_soundtrack_is_at_minus_25_lufs_with_clicks(tmp_path):
    timeline = reference()
    out = sound.mix(timeline, str(tmp_path / "sound.wav"), tone_say(str(tmp_path)),
                    music=None, sfx_folder=SFX)
    measured, _peak = mastering.loudness(out)
    assert measured == pytest.approx(-25.0, abs=1.5)
    clicks_only = sound.mix(timeline, str(tmp_path / "clicks.wav"), None, None, SFX)
    samples = mastering.decode(clicks_only, RATE)
    first = int(timeline["clicks"][0][0] * RATE)
    assert np.abs(samples[first:first + RATE // 10]).max() > 0.05      # a click is there
    assert np.abs(samples[:int(1.0 * RATE)]).max() < 0.01              # silence before it
