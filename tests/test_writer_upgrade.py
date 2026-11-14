"""5.3's new Smart writer (Qwen3-VL) beside 5.2's (Qwen3 4B + SmolVLM): which one runs,
what gets downloaded, and what may be deleted."""
import os

import pytest

from vidgen import imagegen, photos, writer
from vidgen.writer import Model


def tiny(name):
    return Model(name, name + ".gguf", "https://example.test/" + name, 10, "0" * 64, "Apache 2.0")


@pytest.fixture
def models(tmp_path, monkeypatch):
    """Five tiny stand-ins for the real files, in an empty models folder."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    found = {n: tiny(n) for n in ("old", "vision", "proj", "new", "newproj")}
    monkeypatch.setattr(writer, "MODEL", found["old"])
    monkeypatch.setattr(writer, "VISION", found["vision"])
    monkeypatch.setattr(writer, "VISION_PROJ", found["proj"])
    monkeypatch.setattr(writer, "FILES", (found["old"], found["vision"], found["proj"]))
    monkeypatch.setattr(writer, "NEW", found["new"])
    monkeypatch.setattr(writer, "NEW_PROJ", found["newproj"])
    monkeypatch.setattr(writer, "NEW_FILES", (found["new"], found["newproj"]))
    os.makedirs(writer.models_dir())
    return found


def put(*models, size=10):
    for m in models:
        with open(writer.model_path(m), "wb") as fh:
            fh.write(b"x" * size)


def test_a_new_user_downloads_the_new_writer(models):
    assert not writer.installed() and writer.missing() == [models["new"], models["newproj"]]
    assert writer.current() is models["new"] and not writer.can_upgrade()
    put(models["new"], models["newproj"])
    assert writer.installed() and writer.sees() and writer.missing() == []


def test_5_2s_writer_keeps_working_until_the_new_one_is_complete(models):
    put(models["old"], models["vision"], models["proj"])
    assert writer.installed() and writer.sees() and writer.current() is models["old"]
    assert writer.can_upgrade() and writer.missing() == []      # the new one is offered apart
    put(models["new"])                                          # half there: not used yet
    put(models["newproj"], size=4)                              # an unfinished file
    assert writer.current() is models["old"] and writer.old_files(False) == []
    put(models["newproj"])
    assert writer.current() is models["new"] and not writer.can_upgrade()


def test_5_2s_writer_without_its_photo_part_still_gets_only_that(models):
    put(models["old"])
    assert writer.missing() == [models["vision"], models["proj"]]


def test_nothing_old_may_be_deleted_before_the_new_writer_is_all_there(models):
    put(models["old"], models["vision"], models["proj"], models["new"])
    assert writer.old_files(keep_for_pictures=False) == []


def test_ai_pictures_keep_qwen3_4b(models):
    put(models["old"], models["vision"], models["proj"], models["new"], models["newproj"])
    assert writer.old_files(keep_for_pictures=True) == [models["vision"], models["proj"]]
    assert writer.old_files(keep_for_pictures=False) == [
        models["vision"], models["proj"], models["old"]]


def test_ai_pictures_need_qwen3_4b_even_with_the_new_writer(models, monkeypatch):
    put(models["new"], models["newproj"])
    assert models["old"] in imagegen.needed_files()
    command = imagegen.command("sd.exe", "a cat", (512, 512), "out.png", 1)
    assert command[command.index("--llm") + 1] == writer.model_path(models["old"])


def test_photos_use_the_new_writer_once_it_is_there(models, monkeypatch):
    launched = []
    monkeypatch.setattr(photos.subprocess, "Popen", lambda args, **_kw: launched.append(args))
    put(models["vision"], models["proj"])
    photos._launch("llama.exe", "", 1234)
    put(models["new"], models["newproj"])
    photos._launch("llama.exe", "", 1234)
    assert launched[0][2] == writer.model_path(models["vision"])
    assert launched[1][2] == writer.model_path(models["new"])
    assert launched[1][4] == writer.model_path(models["newproj"])


def test_the_upgrade_download_fetches_only_the_new_files(models, monkeypatch):
    put(models["old"], models["vision"], models["proj"], models["new"])
    fetched = []
    monkeypatch.setattr(writer, "download", lambda *a, **_kw: fetched.append(a[3]))
    writer.download_all(upgrade=True)
    assert fetched == [models["newproj"]]


def test_the_writer_is_told_not_to_add_facts():
    from vidgen import draft
    prompt = writer.build_prompt(draft.Audience(), 60)
    assert "even a true one" in prompt and "say an earlier point again" in prompt
