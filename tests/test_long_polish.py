"""4.9.2 piece 3: chapters on the Storyboard and in the video, chapter times, clip variety."""

import queue
from types import SimpleNamespace

import pytest
from PIL import Image

from test_settings_tabs import app  # noqa: F401 - the shared app window fixture
from vidgen import board, footage, render, writer
from vidgen.script import parse_script

CHAPTERED = "\n\n".join(
    f"Visual: farm\nVoice: Line number {n} is about coffee."
    + (f"\nChapter: Part {n // 5 + 1}" if n % 5 == 0 else "") for n in range(15))


# --- chapters --------------------------------------------------------------------------------

def test_chapters_group_the_scenes_and_a_script_without_them_is_one_group():
    scenes = parse_script(CHAPTERED)
    assert board.chapters(scenes) == [(0, 5, "Part 1"), (5, 10, "Part 2"), (10, 15, "Part 3")]
    assert board.chapters(parse_script("Visual: a\nVoice: Hi.")) == [(0, 1, None)]
    late = parse_script("Visual: a\nVoice: Hi.\n\nVisual: b\nVoice: Two.\nChapter: Later")
    assert board.chapters(late) == [(0, 1, None), (1, 2, "Later")]


def test_youtube_chapters_start_at_zero_skip_short_ones_and_need_three():
    marks = [(2.0, "Start"), (40.0, "Middle"), (45.0, "Too soon"), (95.5, "End")]
    assert render.youtube_chapters(marks) == ["0:00 Start", "0:40 Middle", "1:35 End"]
    assert render.youtube_chapters(marks[:2]) == []   # YouTube needs three or more


def test_chapter_times_go_under_the_description():
    upload = {"title": "T", "description": "About coffee.", "tags": []}
    assert writer.with_chapters(upload, ["0:00 A", "1:00 B", "2:00 C"])["description"] == (
        "About coffee.\n\n0:00 A\n1:00 B\n2:00 C")
    assert writer.with_chapters(upload, []) is upload


def test_chapter_titles_are_drawn_as_cards_in_the_video(tmp_path):
    for n in range(3):
        Image.new("RGB", (320, 180), (60 * n, 80, 120)).save(tmp_path / f"{n}.png")
    script = "\n\n".join(f"Visual: local:{tmp_path / f'{n}.png'}\nDuration: 0.6s\n"
                         f"Chapter: Part {n + 1}" for n in range(3))
    events = queue.Queue()
    render.render_worker({"script": script, "output_path": str(tmp_path / "v.mp4"),
                          "aspect": "16:9", "resolution": "720p", "captions": False},
                         render.UiBridge(events))
    logs = []
    while not events.empty():
        kind, payload = events.get_nowait()
        if kind == "log":
            logs.append(payload["msg"])
    assert "🃏 Cards: 3" in logs


# --- clip variety ----------------------------------------------------------------------------

def test_pixabay_drawn_and_computer_made_clips_are_left_out():
    hit = {"id": 1, "pageURL": "p", "user": "u", "tags": "coffee, cup",
           "videos": {"large": {"url": "https://x/1.mp4", "width": 1920, "height": 1080,
                                "thumbnail": "t"}}}
    drawn = dict(hit, id=2, tags="coffee, 3d, render")
    animation = dict(hit, id=3, type="animation")
    found = footage.parse_pixabay({"hits": [hit, drawn, animation]}, 1920, 1080)
    assert [c.id for c in found] == ["1"]


def test_a_stand_in_clip_is_never_the_one_just_before(monkeypatch):
    from vidgen import imagegen

    monkeypatch.setattr(imagegen, "ready", lambda: (False, "not installed"))
    source = lambda name: render._Source(name, "video")
    before = [SimpleNamespace(sources=[source("a")]), SimpleNamespace(sources=[source("b")])]
    for index in range(4):   # whatever the scene's number, it never repeats "b"
        plan = render._ScenePlan(SimpleNamespace(visual="x"))
        render._stand_in(plan, index, before, {}, "16:9", ".", SimpleNamespace(log=print), 1,
                         None, lambda: False, "nothing left")
        assert plan.sources[0].path == "a"


def test_the_prompt_keeps_the_subject_word_for_the_subject_only():
    from vidgen import draft

    prompt = writer.build_prompt(draft.Audience(), 100)
    assert "food truck" in prompt and "coffee truck" in prompt


# --- the Storyboard folds chapters -------------------------------------------------------

def test_only_the_open_chapter_has_cards_and_a_click_opens_another(app):  # noqa: F811
    text = app.script_box.get("1.0", "end")
    try:
        app.script_box.delete("1.0", "end")
        app.script_box.insert("1.0", CHAPTERED)
        app.select_page("board")
        app.refresh_board(force=True)
        built = [i for i, card in enumerate(app.board_cards) if card is not None]
        assert built == list(range(5)) and len(app.chapter_heads) == 3
        app._toggle_chapter(10, 15)
        assert all(app.board_cards[i] is not None for i in range(10, 15))
        app._toggle_chapter(0, 5)   # shut again
        assert all(app.board_cards[i] is None for i in range(5))
        app._board_select(7)   # a scene in a folded chapter opens it
        assert app.board_cards[7] is not None and app.board_selected == 7
    finally:
        app.script_box.delete("1.0", "end")
        app.script_box.insert("1.0", text)
