"""The windows moved into src/gui/ (2026-11-06): everything is still where callers look."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import ai_video_studio as ai  # noqa: E402
from gui import base, dialogs, photo_windows, thumb_editor, tips  # noqa: E402
from split_names import everywhere  # noqa: E402

MOVED = {
    tips: ["TipPicker", "GuideBox", "TipDialog", "TipSession"],
    dialogs: ["WelcomeDialog", "WhatsNewDialog", "TourDialog", "SlowPicturesDialog",
              "ClipQuestion", "BatchDialog", "MyVoiceDialog", "TranslateDialog", "CopyDialog",
              "ResultDialog", "ReportDialog"],
    photo_windows: ["ImageEditor", "PhotoList", "change_photo_window", "DraftDialog"],
    thumb_editor: ["ThumbEditor"],
    base: ["Dialog", "FlipDialog", "centre_on", "show_whats_new", "report_tip_failure", "mci",
           "short_path", "writer_size", "WHATS_NEW", "TOUR", "BUILD", "RECORDINGS_DIR",
           "KEY_PROVIDERS", "WRITER_MODES", "CLOSING_WAIT", "HOLD_WAIT", "load_settings",
           "save_settings", "SETTINGS_DIR", "APP_VERSION"],
}


@pytest.mark.parametrize("module", list(MOVED), ids=lambda m: m.__name__)
def test_every_moved_name_is_still_on_ai_video_studio(module):
    for name in MOVED[module]:
        assert getattr(ai, name) is getattr(module, name), name


def test_the_developer_tools_find_what_they_read():
    # vidgen/tiptest.py and vidgen/bench.py are handed the module and read these off it.
    for name in ("App", "TipDialog", "GuideBox", "FlipDialog", "ERR_COLOR", "SETTINGS_DIR",
                 "ctk", "writer", "APP_VERSION", "load_settings"):
        assert hasattr(ai, name), name


def test_assets_are_found_from_the_gui_folder():
    # base.py is one folder down; the bundle folder is still src/, and assets/ beside it.
    assert base.BUNDLE_DIR == os.path.dirname(os.path.abspath(ai.__file__))
    found = base.asset_path("icon.ico")
    assert found and os.path.isfile(found)


def test_settings_go_where_they_are_told(tmp_path, monkeypatch):
    everywhere(monkeypatch, "SETTINGS_DIR", str(tmp_path))
    everywhere(monkeypatch, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    ai.save_settings({"window": "900x600"})
    assert json.loads((tmp_path / "settings.json").read_text(encoding="utf-8")) == \
        {"window": "900x600"}
    assert ai.load_settings()["window"] == "900x600"
