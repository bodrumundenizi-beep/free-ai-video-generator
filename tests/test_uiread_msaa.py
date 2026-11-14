"""Classic apps' names (5.4 step 5c): shortcuts added to names are taken off."""
from vidgen import uiread
from vidgen.tutorial import record


def test_shortcuts_come_off_names():
    assert uiread.clean_name("Media Alt+M") == "Media"
    assert uiread.clean_name("Open...	Ctrl+O") == "Open..."
    assert uiread.clean_name("Save As Ctrl+Alt+S") == "Save As"
    assert uiread.clean_name("Shape Fill") == "Shape Fill"
    assert uiread.clean_name("C++ Alternatives") == "C++ Alternatives"   # not a shortcut


def test_parse_and_the_recorder_use_the_clean_names():
    found = uiread.parse('[{"name": "Playback Alt+l", "kind": "MenuItem", "state": null, '
                         '"x": 1, "y": 2, "w": 30, "h": 20}]')
    assert found[0].name == "Playback"
    assert record.useful_name({"name": "Audio Alt+A", "kind": "MenuItem"})["name"] == "Audio"


def test_the_reader_falls_back_to_msaa_for_classic_apps():
    assert "AccessibleObjectFromWindow" in uiread._SCRIPT and "$named -lt 5" in uiread._SCRIPT


def test_the_recorders_namer_falls_back_to_msaa_too():
    assert "AccessibleObjectFromPoint" in record._NAMER and "$msaa" in record._NAMER
