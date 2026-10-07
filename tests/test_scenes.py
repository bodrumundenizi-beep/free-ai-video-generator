import pytest
import requests

from vidgen import scenes
from vidgen.footage import Candidate
from vidgen.scenes import ScenePicker, fetch_thumb, find_options, plan_preview
from vidgen.script import ScriptError

SCRIPT = """Visual: hard drive
Voice: Your PC could be hoarding gigabytes of junk you will never use, and it gets worse every single week.

Visual: local:C:/clips/demo.mp4
Voice: Here is the fix.

Visual: local:logo.png
Duration: 2s"""


def clip(n, thumb="https://img/x.jpg"):
    return Candidate("Pexels", str(n), f"https://v/{n}.mp4", 1080, 1920, thumb=thumb)


# --- rows ------------------------------------------------------------------------

def test_rows_for_stock_local_and_silent_scenes():
    stock, local, silent = plan_preview(SCRIPT)
    assert (stock.index, stock.line, stock.visual, stock.is_local) == (0, 1, "hard drive", False)
    assert stock.voice.endswith("…") and len(stock.voice) <= scenes.EXCERPT
    assert (local.visual, local.is_local, local.voice) == ("C:/clips/demo.mp4", True, "Here is the fix.")
    assert (silent.index, silent.is_local, silent.voice) == (2, True, "")


def test_a_broken_script_is_reported_not_previewed():
    with pytest.raises(ScriptError):
        plan_preview("Voice: nothing to show")


# --- the picker ------------------------------------------------------------------

def test_picker_steps_through_the_clips_and_wraps():
    picker = ScenePicker(SCRIPT)
    picker.set_options(0, [clip(1), clip(2), clip(3)])
    assert picker.current(0).id == "1" and picker.label(0) == "1 of 3"
    assert picker.next(0).id == "2" and picker.label(0) == "2 of 3"
    assert picker.next(0).id == "3"
    assert picker.next(0).id == "1"  # round again


def test_chosen_clip_comes_first_then_the_ones_after_it():
    picker = ScenePicker(SCRIPT)
    picker.set_options(0, [clip(1), clip(2), clip(3), clip(4)])
    picker.next(0)
    picker.next(0)
    assert [c.id for c in picker.choices()[0]] == ["3", "4", "1", "2"]


def test_scenes_with_no_clips_are_handled():
    picker = ScenePicker(SCRIPT)
    picker.set_options(0, [])
    assert picker.current(0) is None and picker.next(0) is None
    assert picker.label(0) == "" and picker.choices() == {}
    assert picker.current(5) is None and picker.count(5) == 0


def test_choices_only_hold_for_the_same_script_and_frame():
    picker = ScenePicker(SCRIPT, "9:16 1080p")
    assert picker.matches(SCRIPT, "9:16 1080p")
    assert picker.matches("  " + SCRIPT.replace("\n", "  \n") + "\n\n", "9:16 1080p")  # spacing only
    assert not picker.matches(SCRIPT.replace("hard drive", "ssd"), "9:16 1080p")
    assert not picker.matches(SCRIPT, "16:9 1080p")


# --- finding clips ---------------------------------------------------------------

class FakeSearch:
    def __init__(self):
        self.used, self.asked = [], []

    def find(self, query, limit):
        self.asked.append((query, limit))
        return [clip(f"{query}-{i}") for i in range(3)]

    def mark_used(self, candidate):
        self.used.append(candidate.id)


def test_find_options_searches_stock_scenes_only_and_reports_each_row():
    rows, picker, search, seen = plan_preview(SCRIPT), ScenePicker(SCRIPT), FakeSearch(), []
    find_options(search, rows, picker, seen.append)
    assert search.asked == [("hard drive", scenes.PER_SCENE)]
    assert picker.count(0) == 3 and picker.count(1) == 0
    assert search.used == ["hard drive-0"]  # the opening clip won't open another scene
    assert [row.index for row in seen] == [0, 1, 2]


def test_find_options_stops_when_asked():
    rows, picker, search = plan_preview(SCRIPT), ScenePicker(SCRIPT), FakeSearch()
    find_options(search, rows, picker, stop=lambda: True)
    assert search.asked == []


# --- stills ----------------------------------------------------------------------

class Picture:
    def __init__(self, status=200, content=b"jpeg-bytes", error=None):
        self.status_code, self.content, self.error, self.calls = status, content, error, 0

    def get(self, url, timeout=None):
        assert timeout
        self.calls += 1
        if self.error:
            raise self.error
        return self


def test_still_is_saved_once_and_reused(tmp_path):
    session = Picture()
    path = fetch_thumb(clip(9), str(tmp_path / "thumbs"), session)
    assert open(path, "rb").read() == b"jpeg-bytes" and path.endswith("Pexels_9.jpg")
    assert fetch_thumb(clip(9), str(tmp_path / "thumbs"), session) == path
    assert session.calls == 1


@pytest.mark.parametrize("session", [
    Picture(status=404), Picture(content=b""), Picture(error=requests.ConnectionError())])
def test_a_still_that_cannot_be_fetched_is_none(session, tmp_path):
    assert fetch_thumb(clip(9), str(tmp_path), session) is None


def test_a_clip_without_a_still_makes_no_request(tmp_path):
    session = Picture()
    assert fetch_thumb(clip(9, thumb=""), str(tmp_path), session) is None
    assert session.calls == 0
