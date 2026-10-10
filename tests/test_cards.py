import pytest

from vidgen import cards, draft
from vidgen.script import ScriptError, parse_script

FRAMES = [(1080, 1920), (720, 1280), (1920, 1080), (1280, 720)]


# --- what a Card: line means ----------------------------------------------------------

@pytest.mark.parametrize("text,kind,parts", [
    ("Win + V", "keys", ["Win", "V"]),
    ("Win+Shift+S", "keys", ["Win", "Shift", "S"]),
    ("$120,000 | after 30 years", "big", ["$120,000", "after 30 years"]),
    ("3 seconds", "big", ["3 seconds"]),
    ("Paste; Pick a length; Done", "list", ["Paste", "Pick a length", "Done"]),
    ("Faster + cheaper than anything else you tried", "big",
     ["Faster + cheaper than anything else you tried"]),
])
def test_kind_and_parts(text, kind, parts):
    assert cards.kind(text) == kind and cards.parts(text) == parts


@pytest.mark.parametrize("frame", FRAMES)
@pytest.mark.parametrize("text", [
    "Win + V", "Ctrl + Shift + Esc + Backspace", "$120,000 | after 30 years",
    "One; Two; Three; Four; Five; Six",
    "A very long title that would never fit on one line of a phone screen at full size",
])
def test_a_card_always_fits_the_frame(frame, text):
    picture = cards.draw(text, frame)
    assert picture.width <= frame[0] * cards.MAX_WIDTH + 1 and picture.height < frame[1] / 2
    assert picture.getextrema()[3][1] > 0  # something was drawn


def test_a_list_keeps_its_size_while_it_fills():
    text = "Paste; Pick a length; Done"
    empty, full = cards.draw(text, FRAMES[0], shown=0), cards.draw(text, FRAMES[0])
    assert empty.size == full.size and empty.tobytes() != full.tobytes()
    assert cards.steps(text) == 3 and cards.steps("Win + V") == 1


def test_shown_only_in_the_middle_of_its_scene():
    assert cards.visible(0.0, 4.0) == 0 and cards.visible(4.0, 4.0) == 0
    assert cards.visible(2.0, 4.0) == 1
    assert 0 < cards.visible(cards.MARGIN + cards.POP / 2, 4.0) < 1
    assert cards.visible(0.1, 0.15) == 0  # a scene too short for a card shows none


def test_the_card_keeps_clear_of_the_captions():
    assert cards.centre("Lower") < 0.5 and cards.centre("Center") < 0.4
    assert cards.centre("Top") > 0.5


def test_the_layer_plays_for_the_scene_and_starts_with_it():
    layer = cards.layer("Paste; Pick; Done", (720, 1280), start=3.0, length=4.0)
    assert layer.start == 3.0 and layer.duration == 4.0
    assert layer.mask.get_frame(0.0).max() == 0
    assert layer.mask.get_frame(2.0).max() > 0.5
    assert layer.get_frame(2.0).shape[2] == 3


# --- in a script ----------------------------------------------------------------------

def test_card_line_parses_and_old_scripts_are_unchanged():
    scenes = parse_script("Visual: keyboard\nVoice: Press it.\nCard: Win + V\n\n"
                          "Visual: desk\nVoice: Done.")
    assert [s.card for s in scenes] == ["Win + V", None]
    with pytest.raises(ScriptError, match="Card: is empty"):
        parse_script("Visual: keyboard\nVoice: Press it.\nCard:")


# --- chosen by rules ------------------------------------------------------------------

@pytest.mark.parametrize("voice,card", [
    ("Press the Windows key and V to open your clipboard history.", "Win + V"),
    ("Press Windows, Shift and S together.", "Win + Shift + S"),
    ("Hold the Windows key and press the left arrow.", "Win + Left"),
    ("Just hit Control and C.", "Ctrl + C"),
    ("Press Alt and F4 to close it.", "Alt + F4"),
    ("After thirty years you have about $120,000.", "$120,000"),
    ("It grows at 7% a year.", "7%"),
    ("That is 7 percent a year.", "7%"),
    ("More than 2,500,000 people use it.", "2,500,000"),
    ("It cost 3.5 million to build.", "3.5 million"),
])
def test_suggest_card(voice, card):
    assert draft.suggest_card(voice) == card


@pytest.mark.parametrize("voice", [
    "Windows has a built-in tool for this.",
    "She worked the night shift and came home late.",
    "Press the Windows key, search for Disk Cleanup, and open it.",
    "In 1971 a man jumped out of a plane.",
    "It takes about 30 seconds and 3 steps.",
    "Press a button and wait.",
])
def test_lines_that_get_no_card(voice):
    assert draft.suggest_card(voice) is None


def test_at_most_half_the_scenes_get_a_card_and_keys_come_first():
    voices = ["It costs $100.", "It grows 7% a year.", "Press Windows and V.", "The end."]
    assert draft.add_cards(voices) == ["$100", None, "Win + V", None]
    assert draft.add_cards(["It costs $100."]) == ["$100"]


def test_new_from_text_writes_card_lines_unless_told_not_to():
    text = "Press the Windows key and V to open the clipboard history. It saves you time."
    with_cards = draft.write(text, length=draft.KEEP_ALL)
    assert "Card: Win + V" in with_cards.script
    assert "Card:" not in draft.write(text, length=draft.KEEP_ALL, cards=False).script
