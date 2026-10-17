import pytest

from vidgen import board, draft
from vidgen.board import Board
from vidgen.script import parse_script, to_text

SCRIPT = """Visual: person typing laptop
Voice: Your computer remembers everything you copy.
Picture: a laptop screen showing a list of copied text

Visual: ai: a hand pressing two keys on a keyboard
Voice: Press the Windows key and V.
Card: Win + V

Visual: local:C:/clips/demo.mp4
Voice: Here it is.
Duration: 4s
Padding: 0.5s
Zoom: in"""


def scenes_of(text):
    return [(s.visual, s.voice, s.duration, s.padding, s.zoom, s.card, s.picture)
            for s in parse_script(text)]


def test_a_script_survives_the_round_trip_unchanged():
    assert scenes_of(Board.from_text(SCRIPT).text()) == scenes_of(SCRIPT)
    assert Board.from_text(SCRIPT).text() == SCRIPT
    # and so does one New from text writes, cards included
    written = draft.write("Press the Windows key and V. It costs $100 and saves time. "
                          "Then you are done.", length=draft.KEEP_ALL).script
    assert Board.from_text(written).text() == written
    silent = "Visual: sunset\nDuration: 3s"
    assert scenes_of(to_text(parse_script(silent))) == scenes_of(silent)


def test_picture_line_parses_and_an_empty_one_is_nothing():
    scenes = parse_script("Visual: desk\nVoice: Hi.\nPicture: a tidy desk\n\n"
                          "Visual: sky\nVoice: Up.\nPicture:")
    assert [s.picture for s in scenes] == ["a tidy desk", None]


def test_sources_and_what_each_card_shows():
    b = Board.from_text(SCRIPT)
    assert [b.source(i) for i in range(3)] == [board.STOCK, board.AI, board.LOCAL]
    assert [b.shown(i) for i in range(3)] == ["person typing laptop",
                                              "a hand pressing two keys on a keyboard",
                                              "C:/clips/demo.mp4"]


def test_editing_fields():
    b = Board.from_text(SCRIPT)
    b.set(0, "voice", "  A new\nline.  ")
    b.set(0, "card", "25 | things")
    b.set(1, "card", "")
    b.set(1, "visual", "a cat: on a laptop")       # the ai: prefix is kept, the colon is not
    b.set(0, "picture", "")
    scenes = parse_script(b.text())
    assert scenes[0].voice == "A new line." and scenes[0].card == "25 | things"
    assert scenes[0].picture is None and scenes[1].card is None
    assert scenes[1].is_ai and scenes[1].ai_prompt == "a cat on a laptop"
    with pytest.raises(ValueError):
        b.set(0, "zoom", "in")


def test_a_scene_cannot_be_emptied_into_an_invalid_script():
    b = Board.from_text(SCRIPT)
    b.set(0, "voice", "")        # no line and no set length: refused
    b.set(0, "visual", "   ")
    b.set(2, "voice", "")        # has a Duration, so it may fall silent
    scenes = parse_script(b.text())
    assert scenes[0].voice and scenes[0].visual == "person typing laptop"
    assert scenes[2].voice is None and scenes[2].duration == 4


def test_switching_between_stock_and_an_ai_picture_keeps_both_descriptions():
    b = Board.from_text(SCRIPT)
    b.set_source(0, board.AI)
    scene = parse_script(b.text())[0]
    assert scene.ai_prompt == "a laptop screen showing a list of copied text"
    assert scene.picture is None
    b.set_source(0, board.STOCK)
    scene = parse_script(b.text())[0]
    assert scene.visual == "laptop screen showing" and not scene.is_ai
    assert scene.picture == "a laptop screen showing a list of copied text"
    # a stock scene with no description gets one from its words and its line
    plain = Board.from_text("Visual: river bank\nVoice: He walked: slowly.")
    plain.set_source(0, board.AI)
    assert parse_script(plain.text())[0].ai_prompt == "river bank, He walked slowly."


def test_order_delete_and_insert():
    b = Board.from_text(SCRIPT)
    assert b.move(0, 1) == 1 and b.shown(1) == "person typing laptop"
    assert b.move(0, -1) == 0 and b.move(2, 1) == 2          # the ends stay put
    assert b.insert(0) == 1 and len(b) == 4
    assert parse_script(b.text())[1].voice == board.NEW_VOICE
    assert b.delete(1) and len(b) == 3
    single = Board.from_text("Visual: desk\nVoice: Hi.")
    assert not single.delete(0) and len(single) == 1


def test_changed_marks_rewritten_scenes_not_moved_ones():
    before = parse_script(SCRIPT)
    after = Board.from_text(SCRIPT)
    after.move(0, 1)
    assert board.changed(before, after.scenes) == []
    after.set(2, "voice", "Something new.")
    after.insert(2)
    assert board.changed(before, after.scenes) == [2, 3]


def test_stock_words_come_from_the_description():
    assert board.stock_words("a robot holding an office clipboard") == "robot holding office"
    assert board.stock_words("") == board.NEW_VISUAL


def test_a_scene_length_is_set_to_the_half_second_and_can_go_back_to_automatic():
    from vidgen import voices

    b = board.Board.from_text("Visual: a\nVoice: One two three four five six.\n\nVisual: b\n"
                              "Voice: Seven.")
    b.set_length(0, 3.26)
    assert b.scenes[0].duration == 3.5 and "Duration: 3.5s" in b.text()
    b.set_length(1, 99)
    assert b.scenes[1].duration == board.MAX_LENGTH
    lengths = b.lengths(voices.persona(""), 0.3)
    assert lengths[0] == 3.5 and lengths[1] == board.MAX_LENGTH
    b.set_length(0, None)
    assert b.scenes[0].duration is None
    assert b.lengths(voices.persona(""), 0.3)[0] > 1.0


def test_a_translation_replaces_only_the_spoken_lines():
    script = ("Visual: keyboard\nVoice: Press Windows and V.\nCard: Win + V\n\n"
              "Visual: local: intro.png\nDuration: 2s\n\nVisual: ai: a desk\nVoice: Pin it.")
    out = board.with_lines(script, ["Presiona Windows y V.", "Fíjalo."])
    scenes = board.parse_script(out)
    assert [s.voice for s in scenes] == ["Presiona Windows y V.", None, "Fíjalo."]
    assert scenes[0].card == "Win + V" and scenes[1].duration == 2 and scenes[2].is_ai
