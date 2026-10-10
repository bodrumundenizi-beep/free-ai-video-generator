import os

import numpy as np
import pytest

from vidgen import localvoice, voice, voices, writer
from vidgen.localvoice import Engine, VoiceError, _align, _pieces, _spread, speakable

FRAME = 600  # samples per frame in the fake model: 40 frames a second


class FakeSession:
    """Every token lasts two frames, the pads one; the audio is that many samples."""

    def __init__(self):
        self.inputs = []

    def run(self, _names, inputs):
        self.inputs.append(inputs)
        count = inputs["input_ids"].shape[1]
        duration = np.array([1] + [2] * (count - 2) + [1])
        return [np.ones(int(duration.sum()) * FRAME, np.float32), duration]


VOCAB = {c: i + 1 for i, c in enumerate("abcdefghijklmnopqrstuvwxyz .,!?—…")}


def fake_phonemes(text, british=False):
    """Each letter is its own sound; digits are dropped the way unknown sounds are."""
    return text.lower()


def engine(phonemes=fake_phonemes, session=None):
    speakers = {"am_one": np.full((510, 1, 256), 1.0, np.float32),
                "am_two": np.full((510, 1, 256), 3.0, np.float32),
                "bm_brit": np.zeros((510, 1, 256), np.float32)}
    return Engine(session or FakeSession(), speakers, VOCAB, phonemes)


# --- words the model can say --------------------------------------------------------

@pytest.mark.parametrize("written,spoken", [
    ("1971", "nineteen seventy one"), ("1905", "nineteen oh five"), ("1900", "nineteen hundred"),
    ("2000", "two thousand"), ("2005", "two thousand five"), ("2024", "twenty twenty four"),
    ("$100", "100 dollars"), ("$200,000", "200,000 dollars"), ("$5k", "5k dollars"),
    ("don’t", "don't"), ("300", "300"), ("12345", "12345"), ("video", "video"),
])
def test_speakable(written, spoken):
    assert speakable(written) == spoken


def test_pieces_keep_the_word_as_written_and_the_mark_after_it():
    pieces = _pieces('Press "Windows", Shift and S - then wait... OK?')
    assert [p.word for p in pieces] == ["Press", "Windows", "Shift", "and", "S", "then", "wait",
                                        "OK"]
    assert [p.mark for p in pieces] == ["", ",", "", "", "—", "", "…", "?"]


def test_spread_gives_every_word_at_least_one_sound():
    assert _spread([1, 2, 3, 4, 5, 6], [2, 1]) == [[1, 2, 3, 4], [5, 6]]
    assert _spread([1, 2], [9, 1]) == [[1], [2]]


def test_align_finds_words_run_together_and_words_split():
    # "jumped out of a plane": espeak-ng says "out of" as one sound-word
    assert _align([5, 5, 1, 5], [6, 3, 2, 1, 5]) == [(0, 1, 0, 1), (1, 2, 1, 1), (3, 1, 2, 1),
                                                    (4, 1, 3, 1)]
    # "with 200,000 dollars": the number is two sound-words
    assert _align([3, 10, 7, 6], [4, 24, 7]) == [(0, 1, 0, 1), (1, 1, 1, 2), (2, 1, 3, 1)]


# --- the model ----------------------------------------------------------------------

def test_say_times_every_word_in_order_and_trims_the_silence():
    samples, words = engine().say("Hello there, world.", "am_one")
    assert [w for _s, _d, w in words] == ["Hello", "there", "world"]
    starts = [s for s, _d, _w in words]
    assert starts == sorted(starts) and starts[0] == pytest.approx(localvoice.LEAD, abs=0.01)
    # hello(5) space there(5) , space world(5) . = 19 tokens of 2 frames; the fake model's
    # silent tail is one frame, shorter than what would be kept of a real one
    spoken = 19 * 2 * FRAME / localvoice.RATE
    assert len(samples) / localvoice.RATE == pytest.approx(
        spoken + localvoice.LEAD + FRAME / localvoice.RATE, abs=0.001)
    start, length, _word = words[1]  # "there": after hello and a space
    assert start - starts[0] == pytest.approx(6 * 2 * FRAME / localvoice.RATE, abs=0.001)
    assert length == pytest.approx(5 * 2 * FRAME / localvoice.RATE, abs=0.001)


def test_a_year_is_one_caption_word_found_in_the_text():
    text = "In 1971, he left."
    _samples, words = engine().say(text, "am_one")
    assert [w for _s, _d, w in words] == ["In", "1971", "he", "left"]
    assert all(w in text for _s, _d, w in words)
    assert words[1][1] > words[0][1] * 5  # "nineteen seventy one" takes far longer than "in"


def test_words_run_together_still_get_their_own_times():
    def merged(text, british=False):
        return text.lower().replace("out of", "outof")

    _samples, words = engine(merged).say("jumped out of a plane", "am_one")
    assert [w for _s, _d, w in words] == ["jumped", "out", "of", "a", "plane"]
    assert words[2][0] > words[1][0] and words[3][0] > words[2][0]


def test_two_speakers_blend_evenly_and_british_ones_use_british_sounds():
    session, asked = FakeSession(), []

    def phonemes(text, british=False):
        asked.append(british)
        return text.lower()

    voice_engine = engine(phonemes, session)
    voice_engine.say("hi", "am_one+am_two", speed=1.15)
    assert session.inputs[0]["style"].mean() == pytest.approx(2.0)
    assert session.inputs[0]["speed"][0] == pytest.approx(1.15)
    voice_engine.say("hi", "bm_brit")
    assert asked == [False, True]


def test_a_long_line_is_read_in_batches_and_the_times_carry_on():
    session = FakeSession()
    text = " ".join(["abcdefghij klmnopqrst, uvwxyz."] * 30)
    samples, words = engine(session=session).say(text, "am_one")
    assert len(session.inputs) > 1
    assert all(i["input_ids"].shape[1] <= localvoice.MAX_TOKENS + 2 for i in session.inputs)
    assert len(words) == 90
    assert words[-1][0] + words[-1][1] <= len(samples) / localvoice.RATE


def test_nothing_to_say_and_unknown_speaker_are_voice_errors():
    with pytest.raises(VoiceError):
        engine().say("... !!!", "am_one")
    with pytest.raises(VoiceError):
        engine().say("hello", "am_nobody")


# --- the files ----------------------------------------------------------------------

def test_download_fetches_both_files_and_reports_one_progress(monkeypatch):
    seen, reports = [], []

    def fake_download(progress, should_stop, session, model):
        seen.append(model.file)
        progress(model.size, model.size)

    monkeypatch.setattr(writer, "download", fake_download)
    localvoice.download(lambda done, total: reports.append((done, total)))
    assert seen == [m.file for m in localvoice.FILES]
    assert reports[-1] == (localvoice.SIZE, localvoice.SIZE) and reports[0][0] < reports[1][0]


def test_download_failure_speaks_of_the_voice(monkeypatch):
    def failing(*_args):
        raise writer.WriterError("The Smart writer could not be downloaded.")

    monkeypatch.setattr(writer, "download", failing)
    with pytest.raises(VoiceError, match="The voice could not be downloaded"):
        localvoice.download()


def test_not_ready_until_the_files_are_there(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(localvoice, "espeak_path", lambda: "espeak-ng.exe")
    assert localvoice.ready() == (False, "The voice has not been downloaded yet.")
    with pytest.raises(VoiceError):
        localvoice.say("hello", "am_one")


# --- through voice.py ---------------------------------------------------------------

def test_the_offline_voice_goes_through_the_studio_chain(monkeypatch, tmp_path):
    from vidgen import mastering

    calls = []

    def fake_say(text, speaker, speed):
        calls.append((speaker, speed))
        return (np.zeros(localvoice.RATE, np.float32),
                [(0.1, 0.2, "Hello"), (0.5, 0.3, "world")])

    monkeypatch.setattr(localvoice, "say", fake_say)
    monkeypatch.setattr(mastering, "master", lambda src, dest: os.replace(src, dest) or dest)
    path, words = voice.generate_voiceover_timed(
        "Hello, world.", str(tmp_path / "voice_0.mp3"), lambda _line: None, "viral-max")
    persona = voices.persona("viral-max")
    assert calls == [(persona.local, pytest.approx(1.15))]
    assert path.endswith("voice_0.wav") and os.path.getsize(path) > localvoice.RATE
    # the comma's breath pause pushed "world" later
    assert words[0][0] == pytest.approx(0.1) and words[1][0] > 0.5


def test_a_classic_voice_is_written_plain(monkeypatch, tmp_path):
    monkeypatch.setattr(localvoice, "say", lambda *_a: (np.zeros(2400, np.float32),
                                                        [(0.0, 0.1, "Hi")]))
    path, words = voice.generate_voiceover_timed(
        "Hi, there.", str(tmp_path / "v.mp3"), lambda _line: None, "Neural Male")
    assert path.endswith("v.wav") and words == [(0.0, 0.1, "Hi")]


def test_a_voice_error_reaches_the_user_as_a_failed_voice(monkeypatch, tmp_path):
    def missing(*_a):
        raise VoiceError("The voice has not been downloaded yet.")

    monkeypatch.setattr(localvoice, "say", missing)
    with pytest.raises(Exception, match="Voice generation failed! The voice has not"):
        voice.generate_voiceover_timed("Hi.", str(tmp_path / "v.mp3"), lambda _l: None, None)
