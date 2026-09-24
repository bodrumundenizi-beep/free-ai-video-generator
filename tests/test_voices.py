import numpy as np
import pytest

from vidgen import voices
from vidgen.voice import PAUSE_COMMA, PAUSE_DASH, PAUSE_STOP, insert_pauses, plan_pauses

# --- catalog -------------------------------------------------------------------

def test_sixteen_voices_twelve_studio_four_classic():
    assert len(voices.CATALOG) == 16
    assert sum(p.enhanced for p in voices.CATALOG) == 12
    classic = [p for p in voices.CATALOG if not p.enhanced]
    assert all("(low quality)" in p.label for p in classic)


def test_ids_and_labels_are_unique():
    assert len({p.id for p in voices.CATALOG}) == 16
    assert len(set(voices.labels())) == 16


def test_classic_voices_listed_last():
    enhanced = [p.enhanced for p in voices.CATALOG]
    assert enhanced == sorted(enhanced, reverse=True)


@pytest.mark.parametrize("old,speaker", [
    ("Neural Male", "en-US-GuyNeural"),
    ("Neural Female", "en-US-AriaNeural"),
    ("Happy/Upbeat (Female)", "en-US-JennyNeural"),
    ("Deep/Narrator (Male)", "en-US-ChristopherNeural"),
])
def test_old_settings_keep_their_classic_voice(old, speaker):
    p = voices.find(old)
    assert p is not None and not p.enhanced and p.voice == speaker
    assert (p.rate, p.pitch) == ("+10%", "+5Hz")  # exactly as 3.0 read them


def test_lookup_by_label_and_fallback():
    guy = voices.persona("en-US-GuyNeural")
    assert voices.persona(guy.label) is guy
    assert voices.persona("nonsense").id == voices.DEFAULT_ID
    assert voices.persona(None).id == voices.DEFAULT_ID


def test_category_profiles():
    assert voices.persona("en-US-GuyNeural").rate == "+8%"
    deep = voices.persona("en-US-ChristopherNeural")
    assert (deep.rate, deep.pitch) == ("-2%", "-2Hz")


# --- breath pauses -------------------------------------------------------------

WORDS = [(0.1, 0.4, "Hello"), (0.76, 0.43, "world"), (1.98, 0.23, "This"),
         (2.22, 0.12, "is"), (2.36, 0.29, "fine"), (2.74, 0.35, "right")]
TEXT = "Hello, world. This is fine — right?"


def test_pauses_follow_punctuation():
    pauses = plan_pauses(WORDS, TEXT)
    lengths = [s for _, s in pauses]
    assert lengths == [PAUSE_COMMA, PAUSE_STOP, PAUSE_DASH]


def test_no_pause_after_the_last_word():
    pauses = plan_pauses(WORDS, TEXT)
    assert all(t < WORDS[-1][0] for t, _ in pauses)


def test_pause_sits_between_the_words():
    (t, _), *_ = plan_pauses(WORDS, TEXT)
    assert 0.1 + 0.4 <= t <= 0.76


def test_persona_scale():
    assert plan_pauses(WORDS, TEXT, scale=2.0)[0][1] == pytest.approx(PAUSE_COMMA * 2)


def test_plain_sentence_gets_no_pauses():
    assert plan_pauses([(0, 0.2, "just"), (0.3, 0.2, "words")], "just words") == []


def test_insert_pauses_lengthens_by_the_silence():
    rate = 1000
    samples = np.ones(2000, dtype=np.float32)
    out = insert_pauses(samples, rate, [(0.5, 0.25), (1.5, 0.1)])
    assert len(out) == 2000 + 250 + 100
    assert (out[500:750] == 0).all() and out[499] == 1 and out[750] == 1


# --- mastering chain -----------------------------------------------------------

def test_measure_pass_only_measures():
    from vidgen.mastering import filter_chain
    chain = filter_chain()
    assert "print_format=json" in chain and "volume=" not in chain


def test_apply_pass_uses_exact_gain_and_a_limiter_below_the_peak_target():
    from vidgen.mastering import LIMITER_HEADROOM_DB, TRUE_PEAK, filter_chain
    chain = filter_chain(5.5)
    assert "volume=5.50dB" in chain and "loudnorm" not in chain
    limit = float(chain.split("alimiter=limit=")[1].split(":")[0])
    assert limit == pytest.approx(10 ** ((TRUE_PEAK - LIMITER_HEADROOM_DB) / 20), rel=1e-3)
    assert "level=disabled" in chain  # alimiter's auto-level would undo the gain


def test_resampling_happens_before_the_limiter():
    from vidgen.mastering import filter_chain
    chain = filter_chain(0.0)
    assert chain.index("aresample") < chain.index("alimiter")
