"""The render worker end to end, on silent scenes made from local pictures.

No voice and no stock footage, so nothing here needs the network; what is
exercised is the flow itself - versions, cancelling, progress, delivery.
"""
import os
import queue
import threading

import pytest

pytest.importorskip("moviepy")

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from vidgen import render  # noqa: E402
from vidgen.render import UiBridge, render_worker  # noqa: E402


@pytest.fixture
def job(tmp_path):
    """A two-scene silent script and the settings to render it, small and quick."""
    rng = np.random.default_rng(3)
    for name in ("a.png", "b.png"):
        Image.fromarray(rng.integers(0, 255, (180, 320, 3), dtype=np.uint8)).save(tmp_path / name)
    script = (f"Visual: local:{tmp_path / 'a.png'}\nDuration: 0.6s\n\n"
              f"Visual: local:{tmp_path / 'b.png'}\nDuration: 0.6s")
    out = tmp_path / "out"
    out.mkdir()
    return dict(script=script, output_path=str(out / "clip.mp4"), aspect="16:9",
                resolution="720p", voice="en-US-GuyNeural", padding=0.25, music_enabled=False,
                music_path="", api_key="", pixabay_key="", cache_dir=None, captions=True)


def run(cfg, ui_class=UiBridge):
    events = queue.Queue()
    render_worker(cfg, ui_class(events))
    out = []
    while not events.empty():
        out.append(events.get())
    return out


def kinds(events):
    return [kind for kind, _payload in events]


def only(events, kind):
    return [payload for k, payload in events if k == kind]


def test_one_version_is_saved_and_reports_progress(job):
    events = run(job)
    done = only(events, "finished")[0]
    assert done["ok"] and done["paths"] == [job["output_path"]] == [done["path"]]
    assert os.path.getsize(job["output_path"]) > 1000
    values = [p["value"] for p in only(events, "progress")]
    assert values == sorted(values) and values[-1] == 1.0
    # Real encode progress: many steps, not a jump from "footage done" to "saved".
    assert len([v for v in values if 0.55 < v < 0.95]) >= 5
    assert "cancelled" not in kinds(events)


def test_two_versions_are_two_files(job):
    events = run(dict(job, versions=2))
    done = only(events, "finished")[0]
    folder = os.path.dirname(job["output_path"])
    assert done["ok"] and done["paths"] == [
        job["output_path"], os.path.join(folder, "clip (version 2).mp4")]
    assert all(os.path.getsize(path) > 1000 for path in done["paths"])
    assert "2 versions" in done["message"]
    values = [p["value"] for p in only(events, "progress")]
    assert values == sorted(values)


def test_versions_setting_falls_back_to_one(job):
    done = only(run(dict(job, versions="lots")), "finished")[0]
    assert done["paths"] == [job["output_path"]]


def test_cancel_before_starting_makes_nothing_and_is_not_a_failure(job):
    cancel = threading.Event()
    cancel.set()
    events = run(dict(job, cancel=cancel))
    assert "cancelled" in kinds(events) and "finished" not in kinds(events)
    assert os.listdir(os.path.dirname(job["output_path"])) == []
    assert any("cancelled" in p["msg"] for p in only(events, "log"))
    assert only(events, "busy")[-1] == {"on": False}


def test_cancel_during_the_encode_stops_it_and_leaves_no_file(job):
    cancel = threading.Event()

    class CancelsWhenEncoding(UiBridge):
        def status(self, msg):
            super().status(msg)
            if "Encoding" in msg:
                cancel.set()

    events = run(dict(job, cancel=cancel), CancelsWhenEncoding)
    assert "cancelled" in kinds(events) and "finished" not in kinds(events)
    assert os.listdir(os.path.dirname(job["output_path"])) == []
    assert not any("ERROR" in p["msg"] for p in only(events, "log"))


def test_temp_folder_is_removed_after_success_and_after_cancel(job, monkeypatch, tmp_path):
    root = tmp_path / "temp_root"
    monkeypatch.setattr(render, "TEMP_ROOT", str(root))
    run(job)
    assert os.listdir(root) == []
    cancel = threading.Event()
    cancel.set()
    run(dict(job, cancel=cancel))
    assert os.listdir(root) == []
