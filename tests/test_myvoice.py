import wave

import numpy as np
import pytest

from vidgen import myvoice


class FakeExtract:
    def run(self, _out, feeds):
        frames = feeds["input"].shape[1]
        return [np.full((1, 256), frames % 7 + 1.0, np.float32)]


class FakeConvert:
    def __init__(self):
        self.asked = []

    def run(self, _out, feeds):
        self.asked.append(feeds)
        length = int(feeds["audio_length"][0]) * myvoice.HOP
        return [np.full((1, 1, length), 0.1, np.float32)]


def fake():
    return myvoice.Converter(FakeExtract(), FakeConvert())


def test_resampling_keeps_the_length_in_proportion_and_a_tone_its_pitch():
    t = np.arange(24000) / 24000
    tone = np.sin(2 * np.pi * 200 * t).astype(np.float32)
    out = myvoice.resample(tone, 24000, 22050)
    assert len(out) == 22050
    assert abs(myvoice.pitch(out, 22050) - 200) < 10


def test_the_spectrogram_has_the_converters_shape():
    spec = myvoice.spectrogram(np.zeros(22050, np.float32))
    pad = (myvoice.NFFT - myvoice.HOP) // 2
    assert spec.shape == (1 + (22050 + 2 * pad - myvoice.NFFT) // myvoice.HOP, 513)


def _recording(path, seconds, hz=120, level=0.5):
    t = np.arange(int(seconds * 16000)) / 16000
    data = (np.sin(2 * np.pi * hz * t) * level * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(data.tobytes())


def test_a_voice_is_made_from_a_recording_and_converts_at_the_same_length(tmp_path, monkeypatch):
    monkeypatch.setattr(myvoice, "MY_FOLDER", str(tmp_path / "mine"))
    monkeypatch.setattr(myvoice, "_voice", None)
    _recording(tmp_path / "rec.wav", 8, hz=220)
    said = []

    def say(text, speaker):
        said.append(speaker)
        # a different length for each speaker: FakeExtract's tone depends on it
        return np.zeros(24000 * 3 + 256 * len(said), np.float32), []

    made = myvoice.make(str(tmp_path / "rec.wav"), say, fake())
    assert said == list(myvoice.BASES)   # every built-in voice is compared
    assert made["base"] in myvoice.BASES and -1 <= made["likeness"] <= 1
    assert len(made["target"]) == 256 and myvoice.base_speaker() == made["base"]
    speech = np.zeros(24000 * 2, np.float32)
    out = myvoice.convert(speech, fake())
    assert len(out) == len(speech) and out.dtype == np.float32


def test_a_short_or_silent_recording_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(myvoice, "MY_FOLDER", str(tmp_path / "mine"))
    _recording(tmp_path / "short.wav", 2)
    with pytest.raises(ValueError, match="short"):
        myvoice.make(str(tmp_path / "short.wav"), None, fake())
    _recording(tmp_path / "quiet.wav", 8, level=0.0)
    with pytest.raises(ValueError, match="silent"):
        myvoice.make(str(tmp_path / "quiet.wav"), None, fake())


def test_without_a_voice_the_speech_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(myvoice, "MY_FOLDER", str(tmp_path / "none"))
    monkeypatch.setattr(myvoice, "_voice", None)
    speech = np.ones(100, np.float32)
    assert myvoice.convert(speech, fake()) is speech
