"""Show it on my PC: settings are read, put back, and never turned off when they were on."""
from types import SimpleNamespace

import pytest

from vidgen import screen, tips

LIST = """Existing Power Schemes (* Active)
Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced) *
Power Scheme GUID: 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c  (High performance)
"""


class Windows:
    """powercfg, faked: remembers the active plan."""

    def __init__(self, active="381b4222-f694-41f0-9685-ff5bb260df2e", listing=LIST, broken=False):
        self.active, self.listing, self.broken, self.calls = active, listing, broken, []

    def __call__(self, args):
        self.calls.append(args)
        if self.broken:
            raise OSError("powercfg failed")
        if args[1] == "/getactivescheme":
            return f"Power Scheme GUID: {self.active}  (Something)"
        if args[1] == "/list":
            return self.listing
        if args[1] == "/setactive":
            self.active = args[2]
            return ""
        raise AssertionError(args)


def tip_on(windows):
    return tips.Tip(key="t", title="t", opens=[], read=lambda: tips.active_plan(windows),
                    is_on=tips.POWER_PLAN.is_on,
                    restore=lambda guid: tips.set_plan(guid, windows), steps=tips.POWER_PLAN.steps)


def session(tip, state):
    import ai_video_studio as studio

    logs = []
    s = studio.TipSession.__new__(studio.TipSession)
    # call_soon runs at once here: the real app hands it to the window's thread.
    app = SimpleNamespace(append_log=logs.append, call_soon=lambda fn, delay=0: fn())
    s.app, s.tip, s.state, s.restored = app, tip, state, False
    return s, logs


def test_the_plan_is_read_by_its_id_in_any_language():
    windows = Windows(active="8C5E7FDA-E8BF-4A96-9A85-A6E23A8C635C")
    assert tips.active_plan(windows) == tips.HIGH_PERFORMANCE
    assert tips.POWER_PLAN.is_on(tips.active_plan(windows))


def test_a_pc_without_the_plan_is_told_so():
    assert tips._plan_available(Windows()) == ""
    assert "no High performance" in tips._plan_available(Windows(listing="(Balanced) *"))
    assert "won't say" in tips._plan_available(Windows(broken=True))


def test_a_changed_setting_is_put_back_once():
    windows = Windows()
    tip = tip_on(windows)
    before = tip.read()
    s, logs = session(tip, before)
    windows.active = tips.HIGH_PERFORMANCE          # the user clicked during the recording
    s.restore()
    s.restore()                                      # again: nothing more happens
    assert windows.active == before
    assert sum(1 for c in windows.calls if c[1] == "/setactive") == 1 and "back" in logs[0]


def test_a_setting_that_was_already_on_is_left_on():
    windows = Windows(active=tips.HIGH_PERFORMANCE)
    tip = tip_on(windows)
    s, logs = session(tip, tip.read())
    s.restore()
    assert windows.active == tips.HIGH_PERFORMANCE
    assert not [c for c in windows.calls if c[1] == "/setactive"] and not logs


def test_when_putting_back_fails_the_user_is_told_how():
    windows = Windows()
    tip = tip_on(windows)
    s, logs = session(tip, tip.read())
    windows.broken = True
    s.restore()
    assert "by hand" in logs[0]


def test_the_voice_says_already_on_only_when_it_was():
    clips = ["a.mp4", "b.mp4", "c.mp4"]
    off = tips.script(tips.POWER_PLAN, False, clips)
    on = tips.script(tips.POWER_PLAN, True, clips)
    assert "already on" not in off and "already on" in on
    assert off.count("Visual: local:") == 3 and "Zoom: none" in off


def test_each_step_keeps_its_end_where_the_click_is():
    assert screen.spans([10.0, 11.0], 20.0) == [(7.5, 10.0), (10.0, 11.0), (17.5, 20.0)]
    assert screen.spans([], 1.0) == [(0.0, 1.0)]
    assert len(screen.spans([0.0, 0.0], 0.0)) == 3   # every step gets a clip, even an empty one


def test_cut_fits_the_window_into_the_frame_and_holds_the_end(tmp_path):
    calls = []
    out = screen.cut_all("rec.mp4", [3.0], 6.0, (1080, 1920), str(tmp_path),
                         run=lambda args: calls.append(args))
    assert [p.endswith(f"step_{i}.mp4") for i, p in enumerate(out, 1)] == [True, True]
    graph = calls[0][calls[0].index("-filter_complex") + 1]
    assert "decrease" in graph and "boxblur" in graph and "tpad=stop_mode=clone" in graph
    assert calls[0][calls[0].index("-ss") + 1] == "0.500"


def test_the_recorder_marks_steps_and_ends_the_file_with_q():
    class Process:
        sent = None

        def poll(self):
            return None if Process.sent is None else 0   # running until it gets "q"

        def communicate(self, data, timeout):
            Process.sent = data

    now = [100.0]
    rec = screen.Recorder((0, 0, 800, 600), "out.mp4", popen=lambda *a, **k: Process(),
                          clock=lambda: now[0])
    now[0] = 103.5
    rec.mark()
    now[0] = 106.0
    assert rec.stop() == pytest.approx(6.0) and rec.marks == [3.5] and Process.sent == b"q"


def test_recording_args_cover_only_the_window():
    args = screen.grab_args((100, 50, 800, 600))
    assert args[args.index("-offset_x") + 1] == "100" and "800x600" in args


def test_open_window_waits_for_the_new_window_in_front(monkeypatch):
    fronts = iter([1, 1, 1, 7, 7, 7])
    monkeypatch.setattr(screen, "foreground", lambda: next(fronts))
    opened = []
    hwnd = screen.open_window(["explorer", "ms-settings:x"], popen=lambda *a, **k: opened.append(a),
                              sleep=lambda s: None)
    assert hwnd == 7 and opened


def test_open_window_gives_up_when_only_this_app_is_in_front(monkeypatch):
    import os
    monkeypatch.setattr(screen, "foreground", lambda: 5)
    monkeypatch.setattr(screen, "_owner", lambda h: os.getpid())
    assert screen.open_window(["x"], wait=0.5, popen=lambda *a, **k: None,
                              sleep=lambda s: None) == 0
