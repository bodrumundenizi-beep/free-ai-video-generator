"""My voice (beta) with KittenTTS 2: the reference cut, making the voice, speaking, falling back."""
import json
import os

import numpy as np
import pytest

from vidgen import myvoice, voice, voicepack, voices

RATE = 22050


def speech(pattern, rate=RATE):
    """Loud stretches and pauses: [(seconds, loud?)] -> samples."""
    rng = np.random.default_rng(1)
    parts = [rng.normal(0, 0.3 if loud else 0.0, int(seconds * rate)) for seconds, loud in pattern]
    return np.concatenate(parts).astype(np.float32)


# Six "sentences" of 4.5 s with 0.6 s pauses; the second has a comma breath 0.8 s before its end.
SIX = ([(0.3, False)] + [(4.5, True), (0.6, False)] * 1 + [(3.2, True), (0.4, False), (0.8, True),
       (0.6, False)] + [(4.5, True), (0.6, False)] * 4)


def test_sentence_ends_merge_a_breath_inside_a_sentence():
    ends = myvoice.sentence_ends(speech(SIX), RATE)
    assert len(ends) == 6
    assert ends[1] == pytest.approx(0.3 + 5.1 + 4.4 + 0.3, abs=0.3)   # the end, not the breath


def test_the_reference_is_whole_sentences_under_30_seconds():
    cut, count = myvoice.reference_cut(speech(SIX), RATE)
    assert count == 5 and cut <= 30
    long_ones = speech([(0.3, False)] + [(7.0, True), (0.6, False)] * 6)
    cut, count = myvoice.reference_cut(long_ones, RATE)
    assert count == 3 and cut <= 30                  # the 4th ends after 30 s: three fit


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(myvoice, "MY_FOLDER", str(tmp_path))
    monkeypatch.setattr(myvoice, "_voice", None)
    monkeypatch.setattr(myvoice, "_speaker", None)
    return tmp_path


class FakeSpeaker:
    made = []

    def __init__(self, fail=False):
        self.fail = fail

    def say(self, text, out, reference, reference_text):
        if self.fail:
            raise voicepack.PackError("CUDA out of memory")
        FakeSpeaker.made.append((text, reference, reference_text))
        myvoice.write_wav(out, speech([(0.2, False), (1.5, True), (0.2, False)], 24000), 24000)
        return 1.9

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def test_make_saves_the_reference_and_its_exact_transcript(folder):
    recording = folder / "recording.wav"
    myvoice.write_wav(str(recording), speech(SIX), RATE)
    FakeSpeaker.made = []
    made = myvoice.make(str(recording), FakeSpeaker)
    assert made["sentences"] == 5 and made["text"] == " ".join(myvoice.SENTENCES[:5])
    assert os.path.isfile(made["reference"]) and (folder / myvoice.SAMPLE).is_file()
    assert FakeSpeaker.made == [(myvoice.SAMPLE_LINE, made["reference"], made["text"])]
    assert json.loads((folder / myvoice.VOICE_FILE).read_text())["key"] == made["key"]


@pytest.mark.parametrize("pattern, message", [([(3.0, True)], "too short"),
                                              ([(8.0, False)], "silent")])
def test_a_recording_that_cant_be_used_says_why(folder, pattern, message):
    recording = folder / "recording.wav"
    myvoice.write_wav(str(recording), speech(pattern), RATE)
    with pytest.raises(ValueError, match=message):
        myvoice.make(str(recording), FakeSpeaker)


def test_a_voice_made_by_the_old_openvoice_version_must_be_made_again(folder):
    (folder / myvoice.VOICE_FILE).write_text(json.dumps({"base": "am_michael", "target": []}))
    assert myvoice.voice() is None and not myvoice.ready()


def test_a_new_recording_changes_the_voice_key(folder):
    recording = folder / "recording.wav"
    myvoice.write_wav(str(recording), speech(SIX), RATE)
    first = myvoice.make(str(recording), FakeSpeaker)["key"]
    myvoice.write_wav(str(recording), speech(SIX) * 0.5, RATE)
    assert myvoice.make(str(recording), FakeSpeaker)["key"] != first


def test_one_speaker_serves_every_line_and_a_failure_starts_a_fresh_one(folder):
    recording = folder / "recording.wav"
    myvoice.write_wav(str(recording), speech(SIX), RATE)
    myvoice.make(str(recording), FakeSpeaker)
    started = []

    def factory():
        started.append(1)
        return FakeSpeaker(fail=len(started) == 2)

    myvoice.speak("One.", str(folder / "a.wav"), factory)
    myvoice.speak("Two.", str(folder / "b.wav"), factory)
    assert len(started) == 1                          # loaded once for the whole video
    myvoice.close_speaker()
    with pytest.raises(voicepack.PackError):
        myvoice.speak("Three.", str(folder / "c.wav"), factory)
    myvoice.speak("Four.", str(folder / "d.wav"), factory)   # the dead one isn't reused
    assert len(started) == 3
    myvoice.close_speaker()


# --- the voice in a render --------------------------------------------------------------------

def test_words_are_spread_over_the_spoken_part_by_length():
    samples = speech([(0.5, False), (2.0, True), (0.5, False)], 24000)
    words = voice.spread_words("Hi wonderful world.", samples, 24000)
    assert [w[2] for w in words] == ["Hi", "wonderful", "world."]
    assert words[0][0] == pytest.approx(0.5, abs=0.05)
    assert words[-1][0] + words[-1][1] <= 2.55
    assert words[1][1] > words[0][1] * 2              # the long word takes longer


def test_my_voice_lines_come_from_the_pack(folder, monkeypatch):
    monkeypatch.setattr(voice, "myvoice_ready", lambda: True)
    monkeypatch.setattr(myvoice, "speak",
                        lambda text, out: FakeSpeaker().say(text, out, "ref", "text"))
    called = []
    monkeypatch.setattr(voice.localvoice if hasattr(voice, "localvoice") else
                        __import__("vidgen.localvoice", fromlist=["say"]), "say",
                        lambda *a: called.append(a))
    path, words = voice.generate_voiceover_timed("Hello there.", str(folder / "v.mp3"),
                                                 lambda m: None, voices.MY_VOICE)
    assert os.path.isfile(path) and [w[2] for w in words] == ["Hello", "there."]
    assert not called                                 # the normal voice wasn't needed


def test_a_failing_pack_falls_back_to_the_normal_voice(folder, monkeypatch):
    from vidgen import localvoice

    monkeypatch.setattr(voice, "myvoice_ready", lambda: True)

    def broken(text, out):
        raise voicepack.PackError("CUDA out of memory")

    monkeypatch.setattr(myvoice, "speak", broken)
    used = []

    def normal(text, speaker, rate):
        used.append(speaker)
        return speech([(0.2, False), (1.0, True)], 24000), [(0.2, 1.0, "Hello.")]

    monkeypatch.setattr(localvoice, "say", normal)
    logs = []
    voice.generate_voiceover_timed("Hello.", str(folder / "v.mp3"), logs.append, voices.MY_VOICE)
    assert used == [voices.persona(voices.MY_VOICE).local]
    assert any("normal voice" in line and "out of memory" in line for line in logs)


def test_the_card_check_is_asked_once(monkeypatch):
    asked = []
    monkeypatch.setattr(myvoice, "_available", None)
    monkeypatch.setattr(voicepack, "graphics", lambda: asked.append(1) or None)
    assert "NVIDIA" in myvoice.available() and "NVIDIA" in myvoice.available()
    assert asked == [1]
