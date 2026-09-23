import pytest

from vidgen.script import ScriptError, count_scenes, parse_script

DEFAULT_SCRIPT = """Visual: hard drive
Voice: Your PC could be hoarding gigabytes of junk you will never use.

Visual: computer data
Voice: Temporary files and old cache pile up silently, slowing your whole system down.

Visual: keyboard typing
Voice: Here is the fix. Press the Windows key, search for Disk Cleanup, and open it.

Visual: mouse click
Voice: Select your main drive, then click Clean up system files."""

# The advanced example from the 3.1 brief, verbatim.
ADVANCED = """Visual: hard drive
Voice: Your PC is hoarding gigabytes of junk.
Duration: auto
Zoom: in
Visual: local:C:/Users/recordings/taskmgr.mp4
Voice: Here is the fix. Open Task Manager.
Duration: 4s"""


# --- the simple format is unchanged -----------------------------------------

def test_default_script_parses_as_before():
    scenes = parse_script(DEFAULT_SCRIPT)
    assert [s.visual for s in scenes] == [
        "hard drive", "computer data", "keyboard typing", "mouse click"]
    assert scenes[0].voice.startswith("Your PC could be hoarding")
    for scene in scenes:
        assert scene.duration is None  # auto
        assert scene.padding is None  # Settings default
        assert scene.zoom == "auto"
        assert not scene.is_local


def test_keys_are_case_insensitive_and_tolerate_spacing():
    scenes = parse_script("  VISUAL :  ocean waves  \nvoice:Calm water.")
    assert scenes[0].visual == "ocean waves"
    assert scenes[0].voice == "Calm water."


def test_scene_lines_are_recorded():
    scenes = parse_script(DEFAULT_SCRIPT)
    assert [s.line for s in scenes] == [1, 4, 7, 10]


# --- the advanced format -------------------------------------------------------

def test_advanced_example_from_the_brief():
    first, second = parse_script(ADVANCED)
    assert first.visual == "hard drive"
    assert first.duration is None and first.zoom == "in"
    assert second.is_local
    assert second.local_path == "C:/Users/recordings/taskmgr.mp4"
    assert second.duration == 4.0
    assert second.zoom == "auto"  # omitted -> subtle default


def test_optional_keys_in_any_order():
    (scene,) = parse_script("Visual: city\nZoom: out\nPadding: 0.5s\nVoice: Night.\nDuration: 3")
    assert (scene.zoom, scene.padding, scene.duration, scene.voice) == ("out", 0.5, 3.0, "Night.")


@pytest.mark.parametrize("text,value", [
    ("4.5s", 4.5), ("4.5", 4.5), ("4 s", 4.0), ("4 seconds", 4.0), (".5s", 0.5),
    ("auto", None), ("AUTO", None),
])
def test_duration_values(text, value):
    (scene,) = parse_script(f"Visual: x\nVoice: y\nDuration: {text}")
    assert scene.duration == value


@pytest.mark.parametrize("text,value", [("off", "none"), ("none", "none"),
                                        ("IN", "in"), ("default", "auto")])
def test_zoom_aliases(text, value):
    (scene,) = parse_script(f"Visual: x\nVoice: y\nZoom: {text}")
    assert scene.zoom == value


def test_out_of_range_values_are_clamped_with_a_warning():
    warnings = []
    (scene,) = parse_script("Visual: x\nVoice: y\nDuration: 0.1s\nPadding: 99", warnings)
    assert scene.duration == 0.5
    assert scene.padding == 10.0
    assert len(warnings) == 2


def test_local_path_quotes_are_stripped():
    (scene,) = parse_script('Visual: local: "C:\\My Videos\\clip one.mp4"\nVoice: y')
    assert scene.local_path == "C:\\My Videos\\clip one.mp4"


def test_voiceless_scene_needs_a_duration():
    (scene,) = parse_script("Visual: local:logo.png\nDuration: 2s")
    assert scene.voice is None and scene.duration == 2.0


def test_scene_with_nothing_to_say_is_skipped_with_a_warning():
    # Older versions dropped this silently; failing now would break scripts
    # that currently render.
    warnings = []
    scenes = parse_script("Visual: forgotten\nVisual: kept\nVoice: hi", warnings)
    assert [s.visual for s in scenes] == ["kept"]
    assert "line 1" in warnings[0]


# --- errors ---------------------------------------------------------------------

def test_existing_error_messages_are_unchanged():
    with pytest.raises(ScriptError, match=r"^Error near Line 1: Missing 'Visual:' before 'Voice:'$"):
        parse_script("Voice: orphan")
    with pytest.raises(ScriptError, match=r"^No valid scenes found\.$"):
        parse_script("\n\n")
    with pytest.raises(ScriptError, match=r"^Error on Line 2: Line must begin with 'Visual:' or 'Voice:'"):
        parse_script("Visual: x\nthis is not a key line")


@pytest.mark.parametrize("script,fragment", [
    ("Visual: x\nVoice: y\nDuration: soon", "Line 3: Duration: expects 'auto'"),
    ("Visual: x\nVoice: y\nPadding: auto", "Line 3: Padding: expects a number"),
    ("Visual: x\nVoice: y\nZoom: sideways", "Line 3: Zoom: expects in, out"),
    ("Visual: x\nVoice: a\nVoice: b", "Line 3: the scene starting at line 1 already has a Voice:"),
    ("Visual:\nVoice: y", "Line 1: Visual: needs search words"),
    ("Visual: x\nVoice:", "Line 2: Voice: is empty"),
    ("Duration: 3s", "Missing 'Visual:' before 'Duration:'"),
    ("Visual: x\nSpeed: 2x", "Line 2: Line must begin with"),
])
def test_errors_name_the_line(script, fragment):
    with pytest.raises(ScriptError, match=fragment):
        parse_script(script)


def test_count_scenes_never_raises():
    assert count_scenes(DEFAULT_SCRIPT) == 4
    assert count_scenes(ADVANCED) == 2
    assert count_scenes("nonsense") is None
