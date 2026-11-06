"""The one question before a video when no stock clip looked right (ClipQuestion)."""

import threading

from PIL import Image

import ai_video_studio as ai
from split_names import everywhere  # noqa: E402
from test_settings_tabs import app  # noqa: F401  (the window, shared)


def test_answers_come_back_once_per_scene(app, tmp_path):  # noqa: F811
    still = str(tmp_path / "s.jpg")
    Image.new("RGB", (320, 180), "brown").save(still)
    got = []
    items = [{"index": 3, "visual": "roast thermometer", "voice": "It's hot.", "still": still},
             {"index": 7, "visual": "bean bag filler", "voice": "", "still": None}]
    window = ai.ClipQuestion(app, items, got.append, threading.Event())
    window.picks[7].set(ai.ClipQuestion.NEXT)
    window.done()
    window.stop()   # a second close says nothing more
    assert got == [{3: "use", 7: "next"}]


def test_closing_or_cancel_in_the_main_window_cancels(app):  # noqa: F811
    got, cancel = [], threading.Event()
    window = ai.ClipQuestion(app, [{"index": 0, "visual": "x", "voice": "", "still": None}],
                             got.append, cancel)
    cancel.set()
    window._watch()
    assert got == [None]


def test_the_setting_shows_only_with_both_models(app, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ai.writer, "installed", lambda *a: True)
    monkeypatch.setattr(ai.writer, "sees", lambda: False)
    app._check_clips_row_shown()
    assert not app.check_clips_row.winfo_manager()
    monkeypatch.setattr(ai.writer, "sees", lambda: True)
    app._check_clips_row_shown()
    assert app.check_clips_row.winfo_manager() == "grid"
    app.var_check_clips.set(True)
    assert app._collect_settings()["check_clips"] is True


def test_graphics_switch_on_the_card_and_the_check_once(app, monkeypatch):  # noqa: F811
    saved = []
    everywhere(monkeypatch, "save_settings", saved.append)
    app.gpu_looked = False
    app.var_writer_gpu.set(False)
    saved.clear()                       # setting a switch saves by itself
    app._graphics_looked(None)          # couldn't ask: nothing changes, asked again later
    assert not app.gpu_looked and not saved
    app._graphics_looked(True)
    assert app.gpu_looked and app.var_writer_gpu.get() and app.var_check_clips.get()
    assert saved[-1]["gpu_looked"] is True
    app.gpu_looked = False
    app._graphics_looked(False)         # no graphics: the check would take minutes
    assert not app.var_writer_gpu.get() and not app.var_check_clips.get()
    app.var_check_clips.set(True)


def test_llama_lists_its_graphics(monkeypatch):
    class Listed:
        def __init__(self, out):
            self.stdout, self.stderr = out, b""

    monkeypatch.setattr(ai.writer, "runtime_path", lambda: r"C:\x\llama-server.exe")
    card = b"Available devices:\n  Vulkan0: Intel(R) UHD Graphics (8000 MiB, 7000 MiB free)\n"
    assert ai.writer.has_graphics(lambda *a, **k: Listed(card)) is True
    assert ai.writer.has_graphics(lambda *a, **k: Listed(b"Available devices:\n")) is False

    def broken(*a, **k):
        raise OSError("gone")

    assert ai.writer.has_graphics(broken) is None


def test_an_update_keeps_its_graphics_choice_and_a_new_install_looks(tmp_path, monkeypatch):
    import json

    path = tmp_path / "settings.json"
    everywhere(monkeypatch, "SETTINGS_FILE", str(path))
    for gpu in (False, True):   # settings from 5.0: no gpu_looked yet
        path.write_text(json.dumps({"writer_gpu": gpu, "tour_seen": True}), encoding="utf-8")
        loaded = ai.load_settings()
        assert loaded["gpu_looked"] is True and loaded["writer_gpu"] is gpu
        assert loaded["check_clips"] is gpu   # on the processor the check takes minutes
    path.unlink()               # a new install
    assert ai.load_settings()["gpu_looked"] is False
