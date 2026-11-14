"""The Tutorial window (5.4, plan step 5a): Analyze becomes Record, the steps are editable."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import ai_video_studio as ai  # noqa: E402
from gui import tutorial_window as tw  # noqa: E402
from split_names import everywhere  # noqa: E402
from vidgen.tutorial import analyze  # noqa: E402

REF = os.path.join(os.path.dirname(__file__), "fixtures", "tutorial_ref")


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    folder = tmp_path_factory.mktemp("settings")
    path = folder / "settings.json"
    path.write_text(json.dumps({"welcome_seen": True, "tour_seen": True, "whats_new_off": True,
                                "check_updates": False, "window": "1000x700"}), encoding="utf-8")
    patch = pytest.MonkeyPatch()
    everywhere(patch, "SETTINGS_DIR", str(folder))
    everywhere(patch, "SETTINGS_FILE", str(path))
    patch.setattr(ai.projects, "ROOT", str(folder / "projects"))
    try:
        window = ai.App()
    except Exception as exc:  # noqa: BLE001
        patch.undo()
        pytest.skip(f"no window: {exc}")
    window.withdraw()
    yield window
    window.destroy()
    patch.undo()


@pytest.fixture
def dialog(app, monkeypatch):
    monkeypatch.setattr(analyze, "windows", lambda: [
        analyze.Window(11, "Presentation1 - PowerPoint", "PowerPoint"),
        analyze.Window(22, "Untitled - Paint", "Paint")])
    d = tw.TutorialDialog(app)
    yield d
    d.close()


def golden_log():
    with open(os.path.join(REF, "golden_log.json"), encoding="utf-8") as f:
        return json.load(f)


def test_home_and_new_offer_the_tutorial(app):
    assert any(f[1] == "Tutorial" for f in app._features())


def test_analyze_becomes_record_only_when_everything_is_fine(dialog):
    window = dialog.chosen()
    assert window.app == "PowerPoint" and dialog.go_button.cget("text") == "Analyze"
    bad = analyze.Report(window.hwnd)
    bad.add("problem", "This app runs as administrator...")
    dialog._analyzed(window, bad)
    assert dialog.go_button.cget("text") == "Analyze" and "✗" in dialog.check_text.cget("text")
    good = analyze.Report(window.hwnd)
    good.add("ok", "This PC can record the window.")
    dialog._analyzed(window, good)
    assert dialog.go_button.cget("text") == "Record"
    dialog.var_window.set(tw.window_label(dialog.listed[1]))   # another window: check again
    dialog._picked()
    assert dialog.go_button.cget("text") == "Analyze"


def test_the_steps_can_be_reworded_and_deleted(dialog, tmp_path):
    dialog.folder = str(tmp_path)          # no recording here: steps show without pictures
    dialog._recorded(golden_log())
    assert len(dialog.timeline["steps"]) == 4
    assert dialog.make_button.cget("state") == "normal"
    first_click = golden_log()["clicks"][0]["t"]
    dialog._reworded(first_click, "Open Insert and pick Shapes")
    assert dialog.timeline["steps"][0]["text"] == "Open **Insert** and pick **Shapes**"
    assert dialog.timeline["recap"]["rows"][0] == "Open Insert and pick Shapes"
    gold = golden_log()["clicks"][6]["t"]
    dialog._delete([gold])                 # the Gold step goes
    assert len(dialog.timeline["steps"]) == 3
    assert dialog.timeline["steps"][0]["text"] == "Open **Insert** and pick **Shapes**"   # kept


def test_nothing_clicked_says_so(dialog, tmp_path):
    dialog.folder = str(tmp_path)
    log = golden_log()
    log["clicks"] = []
    dialog._recorded(log)
    assert "Nothing was clicked" in dialog.check_text.cget("text")
    assert dialog.timeline is None


def test_bold_names():
    assert tw.bold_names("Click shape fill, then gold", ["Shape Fill", "Gold"]) == \
        "Click **shape fill**, then **gold**"
    assert tw.bold_names("Already **Insert**", ["Insert"]) == "Already **Insert**"


def test_analyze_says_what_is_wrong_up_front():
    def fails(rect):
        return "This PC doesn't allow recording the screen."
    gone = analyze.analyze(1, rect_of=lambda h: None)
    assert not gone.ok and "closed" in gone.lines[0][1]
    small = analyze.analyze(1, rect_of=lambda h: (0, 0, 800, 600), iconic=lambda h: True)
    assert not small.ok and "minimized" in small.lines[0][1]
    admin = analyze.analyze(1, rect_of=lambda h: (0, 0, 800, 600), iconic=lambda h: False,
                            admin=lambda h: True)
    assert not admin.ok and "administrator" in admin.lines[0][1]
    refused = analyze.analyze(1, rect_of=lambda h: (0, 0, 800, 600), iconic=lambda h: False,
                              admin=lambda h: False, can_record=fails, scale_of=lambda h: 1.0)
    assert not refused.ok and "doesn't allow" in refused.lines[-1][1]
    fine = analyze.analyze(1, rect_of=lambda h: (0, 0, 800, 600), iconic=lambda h: False,
                           admin=lambda h: False, can_record=lambda r: "", scale_of=lambda h: 1.25,
                           read=lambda h: [], can_move=lambda: False)
    assert fine.ok and any("125%" in t for _k, t in fine.lines)
    assert any("from pictures" in t for _k, t in fine.lines)
    assert not fine.can_move and any("Do it for me" in t for _k, t in fine.lines)


def test_recording_lets_go_of_the_modal_grab(dialog, monkeypatch, tmp_path):
    """A hidden dialog that kept its grab swallowed the Stop box's clicks (2026-11-14)."""
    class FakeSession:
        def __init__(self, hwnd, folder, ignore=None):
            self.folder, self.stopped = folder, __import__("threading").Event()
            self.recorder = type("R", (), {"stop": lambda _self: events.append("recorder stopped")})()

        def __enter__(self):
            return self

        def stop(self):
            return golden_log()

    events = []
    monkeypatch.setattr(tw.record, "Session", FakeSession)
    monkeypatch.setattr(tw.record, "name_crops", lambda log, paths: log)
    monkeypatch.setattr(tw.record, "crops", lambda folder, log: [])
    dialog.grab_set()
    dialog.report = analyze.Report(11)
    dialog.folder = str(tmp_path)
    dialog._countdown(0, dialog.chosen())
    assert dialog.grab_current() is None          # the Stop box can be clicked
    assert dialog.box is not None and dialog.box.winfo_exists()
    shown = dialog.deiconify
    monkeypatch.setattr(dialog, "deiconify", lambda: (events.append("window shown"), shown()))
    dialog.box.stop()                              # the Stop button
    assert dialog.grab_current() is dialog        # modal again once it is back
    # the recording stops before the window comes back over the recorded app
    assert events[:2] == ["recorder stopped", "window shown"]


def test_names_read_from_pictures_are_marked_to_check(dialog, tmp_path):
    dialog.folder = str(tmp_path)
    dialog._recorded(golden_log())      # Rectangle was named by Qwen3-VL ("picture")
    labels = [w.cget("text") for row in dialog.steps_list.winfo_children()
              for frame in row.winfo_children() if isinstance(frame, tw.ctk.CTkFrame)
              for w in frame.winfo_children() if isinstance(w, tw.ctk.CTkLabel)]
    assert any("check the name" in t for t in labels if t.startswith("Step 1"))
    assert not any("check the name" in t for t in labels if t.startswith("Step 2"))


def test_the_shape_choice_makes_a_9_16_tutorial(dialog, tmp_path):
    dialog.folder = str(tmp_path)
    dialog._recorded(golden_log())
    assert dialog.timeline["stage"]["size"] == [1920, 1080]
    dialog.var_shape.set("9:16")
    dialog._rebuild()
    assert dialog.timeline["stage"]["size"] == [1080, 1920]


def test_the_chosen_apps_icon_shows(dialog, monkeypatch):
    from PIL import Image
    monkeypatch.setattr(analyze, "icon_of", lambda hwnd: Image.new("RGBA", (32, 32), (255, 0, 0, 255)))
    dialog._picked()
    assert dialog._icon is not None
    monkeypatch.setattr(analyze, "icon_of", lambda hwnd: None)
    dialog._picked()
    assert dialog._icon is None


def test_the_stop_box_guides_and_ticks_steps_off(app):
    box = tw.StopBox(app, lambda: None, ["Search", "Find...", "Find Next"])
    assert "→ Search" in box.steps_text.cget("text")
    box.clicked("Edit")                 # not the next step: nothing ticks
    box.clicked("Search")
    box.clicked("Find...")
    text = box.steps_text.cget("text")
    assert "✓ Search" in text and "✓ Find..." in text and "→ Find Next" in text
    box.destroy()
    assert tw.StopBox(app, lambda: None).steps_text is None   # no steps: no checklist


def test_the_steps_box_starts_empty_and_guides_with_what_is_written(dialog):
    assert dialog.auto_steps.get("1.0", "end").strip() == ""
    assert dialog._guide_steps() == []
    dialog.auto_steps.delete("1.0", "end")
    dialog.auto_steps.insert("1.0", "Search → Find..., Find Next")
    assert dialog._guide_steps() == ["Search", "Find...", "Find Next"]
