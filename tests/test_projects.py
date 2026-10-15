import os

import pytest

from vidgen import projects

SCRIPT = "Visual: city\nVoice: Here is a very long first line that keeps going on and on.\n"


@pytest.fixture(autouse=True)
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "ROOT", str(tmp_path / "projects"))
    return tmp_path / "projects"


def test_save_load_round_trip():
    saved = projects.save("a", {"script": SCRIPT, "settings": {"aspect": "9:16"}})
    loaded = projects.load("a")
    assert loaded["script"] == SCRIPT and loaded["settings"] == {"aspect": "9:16"}
    assert loaded["created"] == saved["created"] and loaded["videos"] == []
    assert projects.is_draft(loaded)


def test_newest_first_and_broken_skipped(root, monkeypatch):
    clock = iter([1.0, 2.0])
    monkeypatch.setattr(projects.time, "time", lambda: next(clock))
    projects.save("old", {"script": SCRIPT})
    projects.save("new", {"script": SCRIPT})
    os.makedirs(root / "broken")
    (root / "broken" / projects.DATA).write_text("{not json", encoding="utf-8")
    assert [p["id"] for p in projects.all_projects()] == ["new", "old"]


def test_missing_root_is_empty():
    assert projects.all_projects() == []


def test_copy_is_a_draft_with_its_pictures():
    projects.save("a", {"script": SCRIPT, "title": "Cats", "videos": ["C:/v.mp4"]})
    picture = projects.picture_file(projects.folder("a"), "cat|Photo|9:16")
    os.makedirs(os.path.dirname(picture))
    open(picture, "wb").write(b"png")
    new = projects.copy("a")
    copied = projects.load(new)
    assert new != "a" and copied["title"] == "Cats (copy)" and projects.is_draft(copied)
    assert os.path.isfile(projects.picture_file(projects.folder(new), "cat|Photo|9:16"))
    assert projects.load("a")["videos"] == ["C:/v.mp4"]


def test_delete_stays_inside_root(root):
    projects.save("a", {"script": SCRIPT})
    projects.delete("a")
    assert projects.load("a") is None
    with pytest.raises(ValueError):
        projects.delete("..")


def test_search_title_and_script():
    project = {"title": "Cats at night", "script": "Voice: owls hunt"}
    assert projects.matches(project, "CATS") and projects.matches(project, "owls")
    assert projects.matches(project, "") and not projects.matches(project, "dogs")


def test_default_title_cut_on_a_word():
    assert projects.default_title(SCRIPT) == "Here is a very long first line that..."
    assert projects.default_title("Voice: Short.") == "Short."
    assert projects.default_title("") == "Untitled video"


def test_a_render_keeps_its_ai_pictures_in_the_project(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from vidgen import imagegen, render
    from vidgen.script import parse_script

    def make(prompt, size, path, *a, **k):
        open(path, "wb").write(b"png")
        return imagegen.Made(1.0, imagegen.GPU, (576, 1024))

    monkeypatch.setattr(imagegen, "make", make)
    ui = SimpleNamespace(log=lambda _line: None, ai_backend=lambda _b: None,
                         ai_speed=lambda *_a: None)
    plan = render._ScenePlan(parse_script("Visual: ai: a cat\nVoice: Hi.")[0])
    work, project = tmp_path / "work", tmp_path / "project"
    work.mkdir()
    cfg = {"project_dir": str(project), "ai_images_look": "Photo"}
    assert render._ai_picture(plan, 0, cfg, "9:16", str(work), ui, 1,
                              SimpleNamespace(providers=[]), lambda: False)
    assert os.path.isfile(projects.picture_file(str(project), "a cat|Photo|9:16"))


def test_picture_file_depends_on_key():
    assert projects.picture_file("p", "a|b|9:16") != projects.picture_file("p", "a|b|16:9")
