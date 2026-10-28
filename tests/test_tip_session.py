"""Show it on my PC, start to Storyboard, with fake Windows: it never gets stuck.

The fake window only accepts calls on the test's own thread, like Tkinter: a background thread
touching it directly fails the test (that is what left the real one stuck at Finish).
"""
import queue
import threading
import time
from types import SimpleNamespace

import pytest

ai = pytest.importorskip("ai_video_studio")
from vidgen import drive, screen, tips, uiread  # noqa: E402

MAIN = threading.main_thread()


def on_main(*_args):
    assert threading.current_thread() is MAIN, "a worker thread touched the window"


class FakeApp:
    def __init__(self):
        self.jobs, self.logs, self.script, self.pages = queue.Queue(), [], "", []
        self.var_aspect = SimpleNamespace(get=lambda: "9:16")
        self.var_resolution = SimpleNamespace(get=lambda: "Draft")
        self.script_box = SimpleNamespace(delete=lambda *a: None,
                                          insert=lambda _i, text: setattr(self, "script", text))

    def call_soon(self, fn, delay_ms=0):
        self.jobs.put(fn)            # any thread may do this: that's the point

    def after(self, _ms, fn):
        on_main()
        self.jobs.put(fn)

    def append_log(self, text):
        on_main()
        self.logs.append(text)

    iconify = deiconify = _commit_script = on_main

    def select_page(self, page):
        on_main()
        self.pages.append(page)

    def run(self, until, seconds=10):
        """Process the window's jobs, like Tk's main loop, until ``until()`` or time is up."""
        end = time.monotonic() + seconds
        while not until() and time.monotonic() < end:
            try:
                self.jobs.get(timeout=0.05)()
            except queue.Empty:
                pass
        return until()


class FakeBox:
    shown = []

    def __init__(self, app, rect, on_next, on_cancel):
        on_main()

    def show(self, text, button="Next", waiting=False):
        on_main()
        FakeBox.shown.append((text, button))

    def destroy(self):
        on_main()


class FakeRecorder:
    def __init__(self, rect, path):
        self.path, self.marks, self.began = path, [], 0.0

    def clock(self):
        return 1.0

    def mark(self):
        self.marks.append(len(self.marks) + 1.0)

    def stop(self):
        time.sleep(0.2)   # the real one waits for ffmpeg: must not freeze the window
        return 5.0


@pytest.fixture
def session(monkeypatch, tmp_path):
    switch = {"state": "Off"}
    monkeypatch.setattr(screen, "open_window", lambda cmd: 99)
    monkeypatch.setattr(screen, "foreground_rect", lambda: (0, 0, 800, 600))
    monkeypatch.setattr(screen, "can_record", lambda rect: "")
    monkeypatch.setattr(screen, "Recorder", FakeRecorder)
    monkeypatch.setattr(screen, "mouse_down", lambda: False)
    monkeypatch.setattr(screen, "cut_all", lambda *a, **k: ["intro.mp4"])
    monkeypatch.setattr(screen, "cut", lambda path, span, size, dest, **k: dest)
    # The app finding the page itself has tests of its own below; never the real mouse here.
    monkeypatch.setattr(drive, "route", lambda place: "")
    monkeypatch.setattr(uiread, "recall", lambda store, place_id: [])   # not this PC's cache
    monkeypatch.setattr(drive, "close", lambda hwnd: None)               # never a real window
    reports = []
    monkeypatch.setattr(ai, "report_tip_failure", lambda app, message: reports.append(message))
    FakeBox.reports = reports
    monkeypatch.setattr(uiread, "read", lambda hwnd: [
        uiread.Control("Night light", "Button", switch["state"], 50, 60, 72, 40)])
    monkeypatch.setattr(ai, "GuideBox", FakeBox)
    monkeypatch.setattr(ai, "RECORDINGS_DIR", str(tmp_path))
    FakeBox.shown = []
    tip = tips.Tip(key="nightlight", title="night light", opens=["x"], read=lambda: None,
                   is_on=lambda s: False, restore=lambda s: None, steps=[
                       tips.Step("Open it", "Open night light."),
                       tips.Step("Click Night light", "Turn it on.", waits_for_change=True,
                                 target="Night light"),
                       tips.Step("Done", "That's it.")])
    app = FakeApp()
    released = []
    dialog = SimpleNamespace(app=app, withdraw=on_main, deiconify=on_main, destroy=on_main,
                             winfo_exists=lambda: True, say=on_main, page_hwnd=0, page_states={},
                             lift=on_main, attributes=on_main, focus_force=on_main,
                             grab_release=lambda: released.append(True))
    app.released = released
    return ai.TipSession(dialog, tip, None, "turn on night light"), app, switch


def started(s, app):
    s.open()
    assert app.released, "the modal dialog must let go of its grab, or the box can't be clicked"
    assert app.run(lambda: s.recorder is not None)


def test_a_flipped_switch_ends_its_step_and_put_back_reaches_the_storyboard(session):
    s, app, switch = session
    started(s, app)
    s.next()                                        # step 1 -> the switch step
    switch["state"] = "On"                          # the user flips it: the step ends itself
    assert app.run(lambda: s.step == 2)
    s.next()                                        # Finish
    assert FakeBox.shown[-1][0].startswith("Saving")
    assert app.run(lambda: "put back" in FakeBox.shown[-1][0])
    assert "Switch Night light back to Off" in FakeBox.shown[-1][0]
    switch["state"] = "Off"                         # put back: the box closes by itself
    assert app.run(lambda: app.script != "")
    assert app.pages == ["board"] and app.script.count("Visual: local:") == 3


def test_next_works_while_a_step_waits_for_a_switch_that_never_changes(session):
    s, app, _switch = session
    started(s, app)
    s.next()
    s.next()                                        # pressed while it waits: still goes on
    assert s.step == 2
    s.next()                                        # Finish
    assert app.run(lambda: app.script != "")         # nothing changed: no put-back step


def test_skip_during_put_back_says_what_is_still_changed(session):
    s, app, switch = session
    started(s, app)
    s.next()
    switch["state"] = "On"
    app.run(lambda: s.step == 2)
    s.next()
    app.run(lambda: "put back" in FakeBox.shown[-1][0])
    s.next()                                        # the box's button is Skip now
    assert app.run(lambda: app.script != "")
    assert any("Night light (was Off)" in line for line in app.logs)


def test_cancel_while_saving_keeps_the_recording(session):
    s, app, _switch = session
    started(s, app)
    s.next()
    s.next()
    s.next()                                        # Finish: saving starts
    s.cancel()                                      # Esc while saving: ignored
    assert app.run(lambda: app.script != "")


def test_every_box_state_has_a_button(session):
    s, app, _switch = session
    started(s, app)
    for _ in range(3):
        s.next()
    app.run(lambda: app.script != "")
    assert FakeBox.shown and all(b in ("Next", "Finish", "Skip") for _t, b in FakeBox.shown)


# --- the app finds the page itself ---------------------------------------------------------

def to_the_end(s, app, switch):
    s.next()                                        # the switch step
    switch["state"] = "On"
    app.run(lambda: s.step == len(s.tip.steps) - 1)
    s.next()                                        # Finish
    app.run(lambda: "put back" in FakeBox.shown[-1][0])
    switch["state"] = "Off"
    assert app.run(lambda: app.script != "")


def test_settings_search_steps_come_first_in_the_video(session, monkeypatch):
    s, app, switch = session
    s.route = "settings"
    box = uiread.Control("Search box, Find a setting", "Edit", None, 10, 10, 200, 30)

    def search(hwnd, place, did, stop):
        assert threading.current_thread() is not MAIN   # the mouse glides off the window's thread
        did("Press Windows and I to open Settings, then search for Night light.", box)
        did("Pick Night light.", box)
        return True

    monkeypatch.setattr(drive, "settings_search", search)
    started(s, app)
    assert s.phase == "auto" and "searches for Night light" in FakeBox.shown[-1][0]
    assert app.run(lambda: s.phase == "record")
    assert s.step == 2 and s.recorder.marks == [1.0, 2.0]   # past the app's own two steps
    to_the_end(s, app, switch)
    assert app.script.count("Visual: local:") == 5
    assert app.script.index("search for Night light") < app.script.index("Turn it on.")
    assert app.script.count("Card: Win + I") == 1   # the keys, drawn as keycaps
    assert s.route == "settings"


def test_nothing_found_in_settings_opens_the_page_directly(session, monkeypatch):
    s, app, switch = session
    s.route = "settings"
    opened = []
    monkeypatch.setattr(drive, "settings_search", lambda *a: False)
    monkeypatch.setattr(ai.subprocess, "Popen", lambda cmd, **k: opened.append(cmd))
    monkeypatch.setattr(ai.time, "sleep", lambda _s: None)
    started(s, app)
    assert app.run(lambda: s.phase == "record")
    assert opened == [["explorer", "ms-settings:nightlight"]] and s.route == "direct"
    to_the_end(s, app, switch)
    assert "Open Night light." in app.script


def test_the_run_box_is_recorded_on_its_own_before_the_page(session, monkeypatch):
    s, app, switch = session
    s.route = "run"
    made = []
    monkeypatch.setattr(screen, "Recorder", lambda rect, path: made.append(rect) or
                        FakeRecorder(rect, path))

    def run_box(place, record, did, stop):
        assert record((0, 900, 400, 200))
        did("Press Windows and R, type mmsys.cpl, and press Enter.", None)
        return 77

    monkeypatch.setattr(drive, "run_box", run_box)
    started(s, app)
    assert s.hwnd == 77 and made[0] == (0, 900, 400, 200) and s.intro["marks"] == []
    assert s.step == 1 and s.recorder.marks == []   # the page's recording starts at its steps
    to_the_end(s, app, switch)
    assert app.script.count("Visual: local:") == 4 and "intro.mp4" in app.script


def test_the_closing_step_finishes_by_itself(session):
    s, app, _switch = session
    s.tip.steps[-1] = tips.Step(tips.CLOSING, "That's all it takes.")
    started(s, app)
    s.next()
    s.next()                                        # on to the closing step: no Finish needed
    assert app.run(lambda: app.script != "")


def test_a_click_on_the_steps_button_ends_the_step(session, monkeypatch):
    s, app, _switch = session
    started(s, app)
    s.next()                                        # the step that names "Night light"
    assert app.run(lambda: 1 in s.found)            # its button found on the page
    monkeypatch.setattr(screen, "mouse_down", lambda: True)
    monkeypatch.setattr(screen, "cursor", lambda: (60, 70))   # inside it
    assert app.run(lambda: s.step == 2)


# --- the app flips the switch itself (the user said yes) -------------------------------------

def flipping(monkeypatch, tmp_path, switch, works=True):
    from vidgen import putback
    monkeypatch.setattr(ai, "SETTINGS_DIR", str(tmp_path))
    monkeypatch.setattr(ai.time, "sleep", lambda _s: None)
    flips = []

    def flip(hwnd, name, to, stop=None, clicking=lambda: None):
        clicking()
        flips.append((name, to, putback.read(str(tmp_path))))   # written down before?
        if works:
            switch["state"] = to
        return works

    monkeypatch.setattr(drive, "flip", flip)
    return flips


def test_the_app_flips_the_switch_and_puts_it_back(session, monkeypatch, tmp_path):
    from vidgen import putback
    s, app, switch = session
    s.tip.auto, s.tip.auto_to = uiread.Control("Night light", "Button", "Off", 50, 60, 72, 40), "On"
    s.auto = s.tip.auto
    flips = flipping(monkeypatch, tmp_path, switch)
    started(s, app)
    s.next()                                        # the switch step: the app clicks it
    assert "the app turns Night light on" in FakeBox.shown[-1][0]
    assert app.run(lambda: s.step == 2)             # the change ended the step
    s.next()                                        # Finish
    assert app.run(lambda: app.script != "")
    assert [f[:2] for f in flips] == [("Night light", "On"), ("Night light", "Off")]
    assert flips[0][2]["state"] == "Off"            # written down before the click
    assert putback.read(str(tmp_path)) is None and switch["state"] == "Off"
    assert any("back off" in line for line in app.logs)


def test_cancel_after_the_flip_still_puts_it_back(session, monkeypatch, tmp_path):
    s, app, switch = session
    s.tip.auto, s.tip.auto_to = uiread.Control("Night light", "Button", "Off", 50, 60, 72, 40), "On"
    s.auto = s.tip.auto
    flips = flipping(monkeypatch, tmp_path, switch)
    started(s, app)
    s.next()
    assert app.run(lambda: s.step == 2)
    s.cancel()
    assert app.run(lambda: switch["state"] == "Off")
    assert flips[-1][:2] == ("Night light", "Off")


def test_a_failed_put_back_keeps_the_note_for_the_next_start(session, monkeypatch, tmp_path):
    from vidgen import putback
    s, app, switch = session
    s.tip.auto, s.tip.auto_to = uiread.Control("Night light", "Button", "Off", 50, 60, 72, 40), "On"
    s.auto = s.tip.auto
    flipping(monkeypatch, tmp_path, switch)
    started(s, app)
    s.next()
    assert app.run(lambda: s.step == 2)
    monkeypatch.setattr(drive, "flip", lambda *a, **k: False)   # the put-back click fails
    s.next()
    app.run(lambda: any("Couldn't switch" in line for line in app.logs))
    assert putback.read(str(tmp_path))["switch"] == "Night light"


def test_the_next_start_puts_back_what_a_crash_left(monkeypatch, tmp_path):
    from vidgen import putback
    monkeypatch.setattr(ai, "SETTINGS_DIR", str(tmp_path))
    putback.write(str(tmp_path), "nightlight", "Night light", "Off")
    monkeypatch.setattr(screen, "open_window", lambda cmd: 7)
    done = []
    monkeypatch.setattr(drive, "flip", lambda hwnd, name, to: done.append((hwnd, name, to)) or True)
    app = FakeApp()
    ai.App._put_back_left(app, putback.read(str(tmp_path)))
    app.run(lambda: app.logs)
    assert done == [(7, "Night light", "Off")] and putback.read(str(tmp_path)) is None
    assert "back off" in app.logs[0]


# --- every step goes on by itself -------------------------------------------------------------

def test_steps_the_app_can_do_go_on_by_themselves(session, monkeypatch):
    s, app, switch = session
    monkeypatch.setattr(ai.time, "sleep", lambda _s: None)
    opened = []
    monkeypatch.setattr(drive, "open_more", lambda hwnd, rect, **k: opened.append(k["name"]) or
                        (uiread.Control(k["name"], "Button", None, 0, 0, 9, 9), []))
    monkeypatch.setattr(drive.Driver, "point", lambda self, c: None)
    s.tip.steps[0] = tips.Step("Watch", "First, open Show more.", target="Show more", nav=True)
    s.tip.steps[1] = tips.Step("Already on", "It's on.", target="Night light", already=True)
    started(s, app)
    assert app.run(lambda: s.step == 2)             # opened, pointed at: no Next pressed
    assert opened == ["Show more"]


def test_a_plain_step_holds_then_goes_on_but_a_users_click_is_waited_for(session, monkeypatch):
    s, app, switch = session
    monkeypatch.setattr(ai.time, "sleep", lambda _s: None)
    monkeypatch.setattr(drive.Driver, "point", lambda self, c: None)
    s.tip.steps[1] = tips.Step("Click Run now", "Run it.", target="Night light")   # an action
    started(s, app)
    assert app.run(lambda: s.step == 1)             # "Open it" had nothing to click
    app.run(lambda: False, seconds=0.5)
    assert s.step == 1 and "click it yourself" in FakeBox.shown[-1][0]


def test_the_video_starts_with_a_hook_and_what_is_about_to_happen(session, monkeypatch, tmp_path):
    s, app, switch = session
    s.hook = True
    monkeypatch.setattr(ai, "SETTINGS_DIR", str(tmp_path))
    monkeypatch.setattr(ai.mastering, "run_ffmpeg", lambda args: None)
    started(s, app)
    to_the_end(s, app, switch)
    first = app.script.split("\n\n")
    assert "intro_hook.png" in first[0] and "Card:" in first[0]
    assert "Right now we're going to turn on Night light" in first[1]
    assert app.script.count("Visual: local:") == 5


# --- check first, explain, never fail out of sight (school laptop, 2026-10-28) ---------------

def test_a_pc_that_cannot_record_is_told_before_anything_opens(monkeypatch, tmp_path):
    monkeypatch.setattr(ai, "RECORDINGS_DIR", str(tmp_path))
    monkeypatch.setattr(screen, "monitors", lambda: [(0, 0, 1366, 768)])
    asked = []
    monkeypatch.setattr(screen, "can_record", lambda rect: asked.append(rect) or "blocked here")
    monkeypatch.setattr(screen, "LAST_FAILURE", "gdi: refused || dda: refused")
    monkeypatch.setattr(screen, "open_window", lambda cmd: pytest.fail("opened a window"))
    app = FakeApp()
    fake = SimpleNamespace(app=app)
    assert ai.TipDialog.preflight(fake) == "blocked here"
    assert asked == [(523, 264, 320, 240)]            # a piece of the middle of the screen
    app.run(lambda: app.logs)
    assert "dda: refused" in app.logs[0]


def test_a_failure_closes_the_opened_window_and_comes_to_the_front(session, monkeypatch):
    s, app, _switch = session
    closed, front = [], []
    monkeypatch.setattr(drive, "close", lambda hwnd: closed.append(hwnd))
    s.dialog.lift = lambda: front.append("lift")
    s.dialog.focus_force = lambda: front.append("focus")
    started(s, app)
    s.fail("This PC doesn't allow recording the screen.")
    assert closed == [99] and front == ["lift", "focus"]
    assert any("doesn't allow recording" in line for line in app.logs)


def test_a_failed_recording_offers_report_this_but_a_cancel_does_not(session):
    s, app, _switch = session
    started(s, app)
    s.cancel()
    assert FakeBox.reports == []
    s.phase = "record"
    s.fail("This PC doesn't allow recording the screen.")
    assert FakeBox.reports == ["This PC doesn't allow recording the screen."]


def test_report_this_sends_the_full_reason(monkeypatch):
    shown = {}

    class FakeDialog:
        def __init__(self, app, title, message, ok=True, on_report=None, **_kw):
            shown.update(title=title, message=message, ok=ok)
            on_report()

    opened = []
    app = SimpleNamespace(open_report=lambda error: opened.append(error))
    monkeypatch.setattr(ai, "Dialog", FakeDialog)
    monkeypatch.setattr(screen, "LAST_FAILURE", "gdi: refused || dda: refused")
    ai.report_tip_failure(app, "This PC doesn't allow recording the screen.")
    assert shown["ok"] is False and "couldn't record" in shown["title"]
    assert "dda: refused" in opened[0] and opened[0].startswith("This PC doesn't")
