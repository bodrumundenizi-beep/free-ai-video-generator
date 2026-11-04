"""5.0 piece 1: reference photos, described on this PC and placed in the script."""

import json
import os

import pytest
from PIL import Image

from test_writer import FakeModel, session
from test_writer_long import outline, parts_for, word
from vidgen import draft, photos, projects, writer
from vidgen.script import parse_script


@pytest.fixture(autouse=True)
def no_real_files(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(writer, "runtime_path", lambda: "llama-server.exe")


def photo(tmp_path, name, colour="red"):
    path = str(tmp_path / name)
    Image.new("RGB", (1200, 800), colour).save(path)
    return path


# --- describing -----------------------------------------------------------------

def test_describe_sends_each_photo_small_as_a_data_url_and_reads_the_sentence(tmp_path,
                                                                              monkeypatch):
    monkeypatch.setattr(photos, "ready", lambda: (True, ""))
    model = FakeModel([json.dumps({"about": "The image shows a red barn in a field"}),
                       json.dumps({"about": "a blue lake at dawn."})])
    paths = [photo(tmp_path, "a.jpg"), photo(tmp_path, "b.png", "blue")]
    heard = []
    found = photos.describe(paths, done=lambda i, about: heard.append((i, about)),
                            launch=model.launch, http=model)
    assert found == ["A red barn in a field", "A blue lake at dawn."]
    assert heard == [(0, found[0]), (1, found[1])]
    content = model.bodies[0]["messages"][0]["content"]
    assert content[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "one sentence" in content[1]["text"]
    assert model.bodies[0]["messages"][0]["role"] == "user"   # no empty system turn
    assert model.killed == 1                                   # stopped before the writer loads


def test_describe_skips_a_file_that_is_not_a_picture(tmp_path, monkeypatch):
    monkeypatch.setattr(photos, "ready", lambda: (True, ""))
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not a picture")
    model = FakeModel([json.dumps({"about": "a cat"})])
    found = photos.describe([str(broken), photo(tmp_path, "c.jpg")], launch=model.launch,
                            http=model)
    assert found == ["", "A cat"] and len(model.bodies) == 1


def test_describe_needs_the_photo_part():
    with pytest.raises(writer.WriterError, match="photo part"):
        photos.describe(["x.jpg"], launch=FakeModel().launch, http=FakeModel())


def test_the_download_fetches_only_what_is_missing_with_one_progress(monkeypatch):
    got, heard = [], []

    def fake(progress, should_stop, session, model):
        got.append(model)
        progress(model.size, model.size)

    monkeypatch.setattr(writer, "download", fake)
    monkeypatch.setattr(writer, "installed", lambda m=None: (m or writer.MODEL) is writer.MODEL)
    writer.download_all(lambda done, total: heard.append((done, total)))
    total = writer.VISION.size + writer.VISION_PROJ.size
    assert got == [writer.VISION, writer.VISION_PROJ]
    assert heard == [(writer.VISION.size, total), (total, total)]


# --- the Smart writer -----------------------------------------------------------

PHOTOS = [draft.Photo("C:/p/barn.jpg", "A red barn in a field", True),
          draft.Photo("C:/p/notes.jpg", "Hand-written notes about harvest", False),
          draft.Photo("C:/p/lake.jpg", "A blue lake at dawn", True)]


def test_the_schema_lets_a_scene_name_only_shown_photos_or_none():
    schema = writer.script_schema(4, photos=[1, 3])
    item = schema["properties"]["scenes"]["items"]
    assert item["properties"]["photo"] == {"enum": [0, 1, 3]} and "photo" in item["required"]
    assert "photo" not in writer.script_schema(4)["properties"]["scenes"]["items"]["properties"]


def test_the_writer_is_told_about_the_photos_and_places_them_once():
    answer = json.dumps({"scenes": [
        {"visual": "barn", "voice": "The old barn stands red in the morning sun.", "photo": 1},
        {"visual": "barn door", "voice": "Its doors have opened every day for years.", "photo": 1},
        {"visual": "lake", "voice": "Down the hill the lake is still and blue.", "photo": 3},
        {"visual": "notes", "voice": "The harvest notes are kept in a small book.", "photo": 2}]})
    model = FakeModel([answer])
    with session(model) as s:
        result = s.write("A day on the farm by the lake.", length="15 s", photos=PHOTOS)
    asked = model.bodies[0]["messages"][-1]["content"]
    assert "1. A red barn in a field (show it)" in asked
    assert "2. Hand-written notes about harvest (don't show it" in asked
    assert "photo" in model.bodies[0]["messages"][0]["content"]
    shown = [s.local_path for s in parse_script(result.script)]
    assert shown == ["C:/p/barn.jpg", None, "C:/p/lake.jpg", None]


def test_a_shown_photo_the_writer_left_out_still_gets_a_scene():
    answer = json.dumps({"scenes": [
        {"visual": "barn", "voice": "The old barn stands red in the morning sun.", "photo": 0},
        {"visual": "lake", "voice": "Down the hill the lake is still and blue.", "photo": 0}]})
    with session(FakeModel([answer])) as s:
        result = s.write("A day on the farm by the lake.", length="15 s", photos=PHOTOS)
    shown = [s.local_path for s in parse_script(result.script)]
    assert shown == ["C:/p/barn.jpg", "C:/p/lake.jpg"]


def test_a_long_video_never_shows_a_photo_twice():
    parts = parts_for("3 min")

    def part_with(at):
        return json.dumps({"title": "", "scenes": [
            {"visual": "farm", "voice": f"Then {word(at)} {word(at * 50 + n + 1000)} here.",
             "photo": 1 if n == 0 else 0} for n in range(3)]})

    model = FakeModel([outline(parts)] + [part_with(i) for i in range(parts)])
    with session(model) as s:
        result = s.write("Make a video about a farm by a lake", length="3 min", photos=PHOTOS)
    shown = [p for p in (s.local_path for s in parse_script(result.script)) if p]
    assert sorted(shown) == ["C:/p/barn.jpg", "C:/p/lake.jpg"]
    second = model.bodies[2]
    enum = second["response_format"]["json_schema"]["schema"]["properties"]["scenes"][
        "items"]["properties"]["photo"]["enum"]
    assert enum == [0, 3]                                     # the barn is used already
    assert "1. A red barn" not in second["messages"][-1]["content"]


# --- Quick split ------------------------------------------------------------------

def test_quick_split_puts_a_photo_in_the_scene_about_it():
    text = ("We start the day early. The red barn opens at six. "
            "Cows walk out to the field. The lake is calm at dawn.")
    result = draft.write(text, length=draft.KEEP_ALL, photos=PHOTOS)
    shown = {s.voice: s.local_path for s in parse_script(result.script)}
    assert shown["The red barn opens at six."] == "C:/p/barn.jpg"
    assert shown["The lake is calm at dawn."] == "C:/p/lake.jpg"
    assert "C:/p/notes.jpg" not in result.script


def test_photos_with_no_words_in_common_are_spread_over_the_video():
    scenes = [draft.DraftScene("x", f"Line number {i} here.", i) for i in range(9)]
    placed = draft.place_photos(scenes, [draft.Photo("a.jpg", "zebra"),
                                         draft.Photo("b.jpg", "")])
    assert [i for i, s in enumerate(placed) if s.photo] == [3, 6]


# --- kept with the project ----------------------------------------------------------

def test_a_project_keeps_its_own_copy_of_each_photo(tmp_path):
    original = photo(tmp_path, "trip photo.jpg")
    script = f'Visual: local: "{original}"\nVoice: A day out.\n\nVisual: farm\nVoice: Then home.'
    project = str(tmp_path / "project")
    kept = projects.keep_photos(project, script)
    path = parse_script(kept)[0].local_path
    assert os.path.dirname(path) == os.path.join(os.path.realpath(project), "photos")
    assert os.path.isfile(path) and "Visual: farm" in kept
    assert projects.keep_photos(project, kept) == kept     # already inside: left alone
    os.remove(original)
    assert os.path.isfile(parse_script(kept)[0].local_path)
