"""4.0: descriptions kept beside stock scenes, the keyless path, rewriting from a comment."""
import json

import pytest

from vidgen import board, draft, imagegen, render, writer
from vidgen.script import parse_script
from vidgen.writer import WriterError


# --- a stock scene keeps the Smart writer's description -------------------------------------

def test_to_script_keeps_the_description_beside_a_stock_scene():
    scenes = [draft.DraftScene("person typing laptop", "Press it.", 0, "a laptop screen: a list",
                               True),
              draft.DraftScene("river bank", "He left.", 1, "a muddy river bank"),
              draft.DraftScene("old plane", "It flew on.", 2)]
    mixed = parse_script(draft.to_script(scenes, False, draft.AI_UNFILMABLE))
    assert mixed[0].is_ai and mixed[0].picture is None          # already an AI picture
    assert not mixed[1].is_ai and mixed[1].picture == "a muddy river bank"
    assert mixed[2].picture is None                             # the writer gave none
    assert "Picture:" not in draft.to_script(scenes, False, draft.AI_NONE)
    assert "Picture:" not in draft.to_script(scenes, False, draft.AI_ALL)


# --- no stock key, AI images downloaded ------------------------------------------------------

def test_a_stock_scene_becomes_an_ai_scene_from_its_description_or_its_words():
    described, plain = parse_script(
        "Visual: river bank\nVoice: He left: quietly.\nPicture: a muddy river bank\nCard: 1980\n\n"
        "Visual: old plane\nVoice: It flew on.")
    first, second = render._ai_instead(described), render._ai_instead(plain)
    assert first.ai_prompt == "a muddy river bank" and first.card == "1980"
    assert second.is_ai and second.ai_prompt == "old plane, It flew on."
    assert parse_script(f"Visual: {second.visual}\nVoice: x")[0].is_ai   # still one valid line


def test_a_picture_made_in_the_storyboard_is_used_and_not_made_again(monkeypatch, tmp_path):
    from types import SimpleNamespace

    ready = tmp_path / "seen.png"
    ready.write_bytes(b"png")
    monkeypatch.setattr(imagegen, "make", lambda *a, **k: pytest.fail("made it again"))
    ui = SimpleNamespace(log=lambda _line: None, ai_backend=lambda _b: None,
                         ai_speed=lambda *_a: None)
    plan = render._ScenePlan(parse_script("Visual: ai: a cat\nVoice: Hi.")[0])
    assert render._ai_picture(plan, 0, {"ai_pictures": {0: str(ready)}}, "9:16", str(tmp_path),
                              ui, 1, SimpleNamespace(providers=[]), lambda: False)
    assert plan.sources[0].path == str(ready) and plan.sources[0].kind == "image"
    # the second version of a video gets its own picture
    made = []
    monkeypatch.setattr(imagegen, "make", lambda *a, **k: made.append(1) or imagegen.Made(
        1.0, imagegen.GPU, (576, 1024)))
    assert render._ai_picture(plan, 0, {"ai_pictures": {0: str(ready)}}, "9:16", str(tmp_path),
                              ui, 2, SimpleNamespace(providers=[]), lambda: False)
    assert made == [1]


# --- "Change it" ------------------------------------------------------------------------------

BEFORE = """Visual: person typing laptop
Voice: Your computer remembers everything you copy.
Card: 25

Visual: ai: a hand pressing two keys
Voice: Press Windows and V.
Card: Win + V

Visual: local:C:/clips/demo.mp4
Voice: Here it is.
Zoom: in"""


def test_unchanged_lines_keep_everything_and_new_ones_fit_the_script():
    before = parse_script(BEFORE)
    answer = [("C:/clips/demo.mp4", "Here it is."),                       # moved to the front
              ("a cat pressing two keys", "Press Windows and V."),        # new picture
              ("office desk computer", "Press Control and C to copy it first.")]   # new line
    after = board.apply_revision(before, answer, draft.suggest_card)
    assert after[0] == before[2]                                          # file, zoom and all
    assert after[1].ai_prompt == "a cat pressing two keys" and after[1].card == "Win + V"
    assert after[2].visual == "office desk computer" and after[2].card == "Ctrl + C"
    assert board.changed(before, after) == [1, 2]
    parse_script(board.Board(after).text())                               # still a valid script


def test_a_new_line_in_a_script_of_ai_pictures_is_an_ai_picture():
    before = parse_script("Visual: ai: a cat\nVoice: One.\n\nVisual: ai: a dog\nVoice: Two.")
    after = board.apply_revision(before, [("a cat", "One."), ("a bird on a branch", "Three.")])
    assert after[1].is_ai and after[1].ai_prompt == "a bird on a branch" and after[1].card is None


def test_a_long_description_for_a_stock_scene_becomes_search_words_and_a_picture_line():
    before = parse_script("Visual: desk\nVoice: One.\n\nVisual: sky\nVoice: Two.")
    after = board.apply_revision(before, [("desk", "One."),
                                          ("a boy finding old banknotes on a river bank", "New.")])
    assert after[1].visual == "boy finding old" and not after[1].is_ai
    assert after[1].picture == "a boy finding old banknotes on a river bank"


class FakeSession(writer.Session):
    """A Session whose model answers from a list, with no program behind it."""

    def __init__(self, answers):
        self.answers, self.asked = list(answers), []
        self.should_stop = lambda: False

    def _ask(self, system, turns, schema, max_tokens):
        self.asked.append(turns)
        return self.answers.pop(0)

    def _stopping(self):
        pass


SCENES = [("desk", "One line here."), ("sky", "Another line here.")]


def answer(*scenes):
    return json.dumps({"scenes": [{"picture": p, "voice": v} for p, v in scenes]})


def test_revise_returns_the_models_scenes():
    session = FakeSession([answer(("desk", "One line here."), ("sky", "A  better\nline."))])
    assert session.revise(SCENES, " make the end better ") == [("desk", "One line here."),
                                                               ("sky", "A better line.")]
    turns = session.asked[0]
    assert "Comment: make the end better" in turns[-1][1] and "One line here." in turns[-1][1]
    assert turns[0][0] == "user" and turns[1][0] == "assistant"      # the worked example


def test_revise_asks_once_more_when_nothing_was_changed():
    session = FakeSession([answer(*SCENES), answer(("desk", "A new line."), SCENES[1])])
    assert session.revise(SCENES, "more exciting")[0] == ("desk", "A new line.")
    assert len(session.asked) == 2 and "nothing was changed" in session.asked[1][-1][1]


@pytest.mark.parametrize("reply,message", [
    ("not json", "could not read"), (json.dumps({"scenes": [{"voice": "x"}]}), "could not read"),
    (answer(("desk", "")), "empty answer"),
])
def test_an_unusable_answer_is_a_writer_error(reply, message):
    with pytest.raises(WriterError, match=message):
        FakeSession([reply]).revise(SCENES, "shorter")


def test_revise_needs_a_comment():
    with pytest.raises(WriterError, match="what you would like changed"):
        FakeSession([]).revise(SCENES, "   ")


@pytest.mark.parametrize("comment,scenes,wanted", [
    ("just make it a little longer", 7, 8), ("longer", 7, 9), ("make it way longer", 7, 11),
    ("can you make it a bit shorter", 7, 6), ("its too long", 7, 5), ("add more detail", 6, 8),
    ("a tad shorter", 2, None),                       # never fewer than two scenes
    ("make it better", 7, None), ("a longer intro but shorter overall", 7, None),
    ("the picture should be a long river", 7, None),
])
def test_length_change_reads_how_much_longer_or_shorter(comment, scenes, wanted):
    assert writer.length_change(comment, scenes) == wanted


def test_a_length_comment_fixes_the_number_of_scenes_and_offers_the_source_text():
    class Recording(FakeSession):
        def _ask(self, system, turns, schema, max_tokens):
            self.schema = schema
            return super()._ask(system, turns, schema, max_tokens)

    longer = answer(*SCENES, ("sea", "A third line here."))
    session = Recording([longer])
    assert len(session.revise(SCENES, "a little longer", source="The sea is wide.")) == 3
    scenes = session.schema["properties"]["scenes"]
    assert (scenes["minItems"], scenes["maxItems"]) == (3, 3)
    asked = session.asked[0][-1][1]
    assert "Write exactly 3" in asked and "The sea is wide." in asked
    session = Recording([answer(("desk", "One line here, but new."), SCENES[1])])
    session.revise(SCENES, "more exciting")
    assert session.schema["properties"]["scenes"]["minItems"] == 1     # any length
