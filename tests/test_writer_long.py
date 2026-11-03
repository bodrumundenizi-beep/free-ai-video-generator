"""4.9.2 piece 1: long videos are written as an outline, then a part at a time."""

import json

import pytest

from test_writer import FakeModel, session
from vidgen import draft, pacing, voices, writer
from vidgen.script import ScriptError, parse_script, to_text
from vidgen.writer import Stopped

IDEA = "Make a video about how coffee is grown, picked and roasted"
PERSONA = voices.persona(draft.Audience().voice)


@pytest.fixture(autouse=True)
def no_real_files(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(writer, "runtime_path", lambda: "llama-server.exe")


def word(i):
    """A made-up word of its own for each number, so no fake line repeats another."""
    return "zz" + "".join("abcdefghij"[int(d)] for d in str(i)) + "qq"


def parts_for(length):
    return writer.part_count(writer.target_words(pacing.TARGETS[length], PERSONA))


def outline(parts):
    return json.dumps({"parts": [{"title": f"Part title {i + 1}",
                                  "points": [f"Point {i + 1}a.", f"Point {i + 1}b."]}
                                 for i in range(parts)]})


def part(at, lines=3, title=""):
    return json.dumps({"title": title, "scenes": [
        {"visual": "coffee farm", "voice": f"Then {word(at)} {word(at * 50 + n + 1000)} here."}
        for n in range(lines)]})


def test_a_long_video_is_an_outline_then_one_answer_per_part():
    parts = parts_for("3 min")
    model = FakeModel([outline(parts)] + [part(i) for i in range(parts)])
    heard = []
    with session(model) as s:
        result = s.write(IDEA, length="3 min",
                         progress=lambda message, fraction: heard.append((message, fraction)))
    assert len(model.bodies) == parts + 1 and result.length == "3 min"
    assert "exactly" in model.bodies[0]["messages"][0]["content"]   # the outline asks for N parts
    scenes = parse_script(result.script)
    chapters = [s.chapter for s in scenes if s.chapter]
    assert chapters == [f"Part title {i + 1}" for i in range(parts)]
    assert scenes[0].chapter == "Part title 1" and scenes[3].chapter == "Part title 2"
    second = model.bodies[2]["messages"][0]["content"]
    assert "part 2 of" in second and f"Then {word(0)} {word(1002)} here." in second
    assert "hook" in model.bodies[1]["messages"][0]["content"]
    assert "No hook" in second
    assert heard[0][0] == "Writing the outline..." and heard[-1] == ("Done.", 1.0)
    assert f"Writing part {parts} of {parts}..." in [m for m, _ in heard]


def test_short_lengths_still_take_one_answer():
    model = FakeModel([json.dumps({"scenes": [{"visual": "cup", "voice": "One line here."},
                                              {"visual": "cup", "voice": "Two lines here."}]})])
    with session(model) as s:
        s.write(IDEA, length="60 s")
    assert len(model.bodies) == 1


def test_cancel_after_two_parts_keeps_them_and_cancel_before_any_stops():
    parts = parts_for("5 min")
    model = FakeModel([outline(parts)] + [part(i) for i in range(parts)])
    with session(model, should_stop=lambda: len(model.bodies) >= 3) as s:
        result = s.write(IDEA, length="5 min")
    assert len(model.bodies) == 3
    assert result.source.endswith(f"(stopped after part 2 of {parts})")
    assert [s.chapter for s in parse_script(result.script) if s.chapter] == [
        "Part title 1", "Part title 2"]
    model = FakeModel([outline(parts)] + [part(i) for i in range(parts)])
    with session(model, should_stop=lambda: len(model.bodies) >= 1) as s, \
            pytest.raises(Stopped):
        s.write(IDEA, length="5 min")


def test_a_long_text_is_written_a_piece_at_a_time_without_an_outline():
    sentences = [f"Coffee fact number {n} is about farms, beans, roasting and cups." for n in
                 range(400)]
    text = " ".join(sentences)
    parts = parts_for("10 min")
    model = FakeModel([part(i, title=f"Named {i + 1}") for i in range(parts)])
    with session(model) as s:
        result = s.write(text, length="10 min")
    assert len(model.bodies) == parts   # no outline: the text is too long for one answer
    asked = [body["messages"][-1]["content"] for body in model.bodies]
    assert all(len(a) < len(text) / 2 for a in asked)   # each part got its own piece
    assert asked[0].startswith("Coffee fact number 0") and "number 399" in asked[-1]
    chapters = [s.chapter for s in parse_script(result.script) if s.chapter]
    assert chapters == [f"Named {i + 1}" for i in range(parts)]


def test_too_long_parts_are_cut_and_every_part_left_keeps_its_chapter():
    parts = parts_for("3 min")
    long_part = lambda at: json.dumps({"title": "", "scenes": [
        {"visual": "coffee", "voice": f"{word(at)} {word(at * 50 + n + 1000)} "
                                      f"{word(at * 50 + n + 2000)} {word(at * 50 + n + 3000)} "
                                      f"{word(at * 50 + n + 4000)} {word(at * 50 + n + 5000)} "
                                      f"{word(at * 50 + n + 6000)} {word(at * 50 + n + 7000)}."}
        for n in range(12)]})
    model = FakeModel([outline(parts)] + [long_part(i) for i in range(parts)])
    with session(model) as s:
        result = s.write(IDEA, length="3 min")
    assert draft.fits([s.voice for s in result.scenes], 180.0, PERSONA)
    scenes = parse_script(result.script)
    assert scenes[0].chapter == "Part title 1"
    starts = {}
    for scene in scenes:
        starts.setdefault(scene.voice.split(" ")[0], scene)
    for first in starts.values():   # the first scene still there of each part names it
        assert first.chapter


def test_split_text_keeps_every_sentence_in_order():
    text = " ".join(f"Sentence {n} is here." for n in range(50))
    pieces = writer.split_text(text, 4)
    assert len(pieces) == 4 and " ".join(pieces) == text


def test_the_chapter_key_round_trips_and_is_named_in_errors():
    script = "Visual: farm\nVoice: Coffee grows on hills.\nChapter: Where it grows"
    scenes = parse_script(script)
    assert scenes[0].chapter == "Where it grows" and to_text(scenes) == script
    with pytest.raises(ScriptError, match="Chapter:"):
        parse_script("Visual: farm\nVoice: Hi.\nChapters: no")


def test_long_lengths_are_offered():
    assert draft.LENGTHS[3:6] == ["3 min", "5 min", "10 min"]
    assert pacing.TARGETS["10 min"] == 600.0
    assert writer.part_count(writer.target_words(600, PERSONA)) == writer.MAX_PARTS


def test_a_line_said_again_in_other_words_is_a_repeat():
    said = {writer._gist("Did you know coffee grows on trees in tropical regions?")}
    assert writer._repeats("Coffee grows on trees in tropical regions.", said)
    assert not writer._repeats("Arabica needs more shade and cooler temperatures.", said)
