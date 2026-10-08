from collections import Counter
from itertools import product

import pytest

from vidgen import draft, pacing, voices
from vidgen.draft import Audience, DraftError, DraftScene
from vidgen.formats import RATIO_OPTIONS
from vidgen.script import parse_script

TEXT = (
    "Your PC could be hoarding gigabytes of junk you will never use. "
    "Windows keeps old update files, temporary downloads and thumbnails long after they are needed. "
    "Open Settings, go to System, then Storage, and turn on Storage Sense. "
    "It deletes temporary files automatically every week. "
    "On my laptop this freed 14 GB in under a minute. "
    "You can also open the temp folder and delete everything in it. "
    "Try it today and see how much space you get back!"
)


# --- sentences -------------------------------------------------------------------

def test_sentences_split_at_full_stops_questions_and_exclamations():
    assert draft.split_sentences("It works. Does it? Yes! Good") == [
        "It works.", "Does it?", "Yes!", "Good"]


def test_abbreviations_initials_and_numbers_do_not_end_a_sentence():
    text = "Dr. Smith paid 3.5 dollars, e.g. for tea. J. K. Rowling was there at 5 p.m. sharp."
    assert draft.split_sentences(text) == [
        "Dr. Smith paid 3.5 dollars, e.g. for tea.", "J. K. Rowling was there at 5 p.m. sharp."]


def test_a_quote_closes_with_its_sentence():
    assert draft.split_sentences('She said "stop." Then she left.') == [
        'She said "stop."', "Then she left."]


def test_lines_and_bullets_are_sentences_without_their_markers():
    text = "- Wake up early\n* Drink water\n1. Write a list\n\n2) Start"
    assert draft.split_sentences(text) == ["Wake up early", "Drink water", "Write a list", "Start"]


def test_empty_text_has_no_sentences():
    assert draft.split_sentences("  \n\n ") == [] and draft.split_sentences(None) == []


# --- search words ----------------------------------------------------------------

def test_search_words_are_the_sentences_own_telling_words_in_order():
    counts = Counter({"files": 3, "windows": 2, "thumbnails": 1})
    words = draft.search_words("Windows keeps old update files and thumbnails.", counts, [])
    assert words == "windows files"


def test_a_sentence_with_nothing_to_show_uses_the_topic():
    assert draft.search_words("And that is it.", Counter(), ["storage", "files", "disk"]) == "storage files"
    assert draft.search_words("And that is it.", Counter(), []) == "abstract background"


def test_topic_words_are_the_most_used_first():
    topics = draft.topic_words("Cats sleep. Cats eat fish. Dogs eat too, but cats eat more fish.")
    assert topics[:3] == ["cats", "eat", "fish"]


# --- long sentences --------------------------------------------------------------

def test_a_short_sentence_stays_whole():
    assert draft.split_long("Open Settings and turn it on.", 10) == ["Open Settings and turn it on."]


def test_a_long_sentence_is_cut_where_it_pauses():
    sentence = ("Windows keeps old update files, temporary downloads you forgot about, "
                "and thumbnails long after they are needed.")
    pieces = draft.split_long(sentence, 8)
    assert len(pieces) > 1
    assert " ".join(draft.words_in(" ".join(pieces))) == " ".join(draft.words_in(sentence))
    assert all(len(draft.words_in(p)) >= draft.MIN_PIECE_WORDS for p in pieces)
    assert not any(p.endswith((",", ";", ":")) for p in pieces[:-1]) and pieces[-1].endswith(".")


def test_a_long_sentence_with_no_pause_is_left_alone():
    sentence = "This sentence simply keeps going without a single place where a reader might stop"
    assert draft.split_long(sentence, 6) == [sentence]


# --- fitting a length ------------------------------------------------------------

def test_everything_is_kept_when_no_length_is_set():
    result = draft.write(TEXT, length=draft.KEEP_ALL)
    assert result.dropped == [] and result.longer is None
    assert result.kept == list(range(7)) and result.target == pacing.DEFAULT_TARGET


def test_a_short_length_keeps_the_hook_and_drops_the_rest_in_order():
    result = draft.write(TEXT, length="15 s")
    persona = voices.persona(result.voice)
    assert result.kept[0] == 0 and result.kept == sorted(result.kept)
    assert result.dropped and {i for i, _ in result.dropped}.isdisjoint(result.kept)
    assert len(result.kept) + len(result.dropped) == 7
    assert draft.fits([s.voice for s in result.scenes], 15.0, persona)
    assert result.target == "15 s"


def test_a_longer_length_is_offered_when_sentences_were_dropped():
    assert draft.write(TEXT, length="15 s").longer in ("30 s", "60 s")
    assert draft.write(TEXT * 6, length="60 s").longer == draft.KEEP_ALL  # nothing fixed fits it


def test_putting_a_sentence_back_keeps_it():
    first = draft.write(TEXT, length="15 s")
    index = first.dropped[0][0]
    again = draft.write(TEXT, length="15 s", keep=[index])
    assert index in again.kept and again.kept[0] == 0


def test_importance_favours_what_the_text_is_about_and_numbers():
    sentences = ["Files fill the disk.", "Anyway, hello there.", "Old files take 14 GB of disk."]
    scores = draft.importance(sentences, Counter(draft._content_words(" ".join(sentences))))
    assert scores[2] > scores[0] > scores[1]


# --- the script ------------------------------------------------------------------

def test_the_script_parses_and_keeps_the_users_words():
    result = draft.write(TEXT, length=draft.KEEP_ALL)
    scenes = parse_script(result.script)
    assert len(scenes) == len(result.scenes) >= 7
    assert draft.words_in(" ".join(s.voice for s in scenes)) == draft.words_in(TEXT)
    assert all(s.visual and not s.is_local for s in scenes)


def test_search_words_can_never_be_read_as_a_key_or_a_local_file():
    script = draft.to_script([DraftScene("local: C:/x.mp4", "Line one\nstill line one.", 0),
                              DraftScene("", "Second.", 1)])
    first, second = parse_script(script)
    assert not first.is_local and first.voice == "Line one still line one."
    assert second.visual == "abstract background"


def test_text_with_nothing_to_say_is_refused():
    with pytest.raises(DraftError):
        draft.write("   \n ")
    with pytest.raises(DraftError):
        draft.to_script([DraftScene("x", "  ", 0)])


def test_the_estimate_grows_with_the_text():
    short, long = draft.analyse("One short line."), draft.analyse(TEXT)
    assert (short.sentences, long.sentences) == (1, 7)
    assert long.seconds > short.seconds > 0 and long.words > short.words
    assert "files" in long.topics


# --- suggestions -----------------------------------------------------------------

@pytest.mark.parametrize("text,content", [
    (TEXT, "Tech and how-to"),
    ("In the 18th century scientists discovered an ancient storm on a distant planet.",
     "Story and facts"),
    ("Your dream needs discipline. Build one habit, face the fear, and never quit.",
     "Motivation"),
    ("Invest part of your salary every month. A budget turns income into savings.",
     "Business and money"),
    ("The puppy and the kitten shared their toys before bedtime.", "Kids and family"),
])
def test_the_kind_of_video_is_recognised_from_its_words(text, content):
    found = draft.suggest(text)
    assert found.audience.content == content and content.lower() in found.reason


def test_an_unclear_text_keeps_the_last_choices():
    last = Audience("Motivation", "Teens", "TikTok")
    found = draft.suggest("Hello there. This is just a line about nothing much.", last)
    assert found.audience == last and "last choices" in found.reason


def test_the_platform_is_never_guessed_and_children_get_kids():
    found = draft.suggest("The kids found their toys at bedtime, and the puppy helped.",
                          Audience("Motivation", "Adults", "Instagram Reels"))
    assert found.audience.platform == "Instagram Reels" and found.audience.age == "Kids"


def test_a_short_text_is_not_stretched_to_the_platform_length():
    assert draft.suggest("Open the settings app. Click update. Done.").length == "15 s"
    assert draft.suggest(TEXT * 4).length == "30 s"  # too long: the platform's default, trimmed
    long_form = Audience(platform="YouTube long-form")
    assert draft.suggest(TEXT, long_form).length == draft.KEEP_ALL


def test_a_suggestion_can_always_be_written():
    found = draft.suggest(TEXT)
    assert parse_script(draft.write(TEXT, found.audience, found.length).script)
    assert draft.suggest("").reason  # nothing pasted is not an error


# --- audiences -------------------------------------------------------------------

@pytest.mark.parametrize("content,age,platform",
                         list(product(draft.CONTENT, draft.AGES, draft.PLATFORMS)))
def test_every_audience_maps_to_real_settings(content, age, platform):
    audience = Audience(content, age, platform)
    assert voices.find(audience.voice) is not None
    assert audience.aspect in RATIO_OPTIONS and audience.length in draft.LENGTHS
    assert audience.scene_words >= 8 and audience.brief
    result = draft.write(TEXT, audience)
    assert parse_script(result.script) and result.length == audience.length


def test_children_get_the_friendly_voice_and_shorter_scenes():
    kids, adults = Audience("Tech and how-to", "Kids", "TikTok"), Audience("Tech and how-to", "Adults")
    assert kids.voice == draft.CONTENT["Kids and family"].voice != adults.voice
    assert len(draft.write(TEXT, kids, draft.KEEP_ALL).scenes) > len(draft.write(TEXT, adults, draft.KEEP_ALL).scenes)


def test_unknown_audience_names_fall_back_to_the_defaults():
    audience = Audience("Nope", "Nope", "Nope")
    assert audience.voice == Audience().voice and audience.aspect == Audience().aspect
    assert draft.write(TEXT, audience, "not a length").length == Audience().length
