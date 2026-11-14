"""Tutorial recorder (5.4, plan step 2): the log, names, privacy, with no real screen."""
import json

from PIL import Image

from vidgen import uiread
from vidgen.tutorial import record


def test_area_grows_round_the_window_and_stays_on_its_monitor():
    screens = [(0, 0, 1920, 1080)]
    assert record.area_for((200, 100, 1000, 500), 0.1, screens) == (100, 50, 1200, 600)
    left, top, width, height = record.area_for((0, 0, 1600, 930), 0.12, screens)
    assert (left, top) == (0, 0) and width <= 1920 and height <= 1080
    assert width % 2 == 0 and height % 2 == 0


def test_shortcuts_are_kept_but_typed_letters_never_are():
    assert record.combo(0x53, {0x11, 0x53}) == "Ctrl + S"
    assert record.combo(0x5A, {0x11, 0x10, 0x5A}) == "Ctrl + Shift + Z"
    assert record.combo(0x0D, {0x0D}) == "Enter"
    assert record.combo(0x73, {0x12, 0x73}) == "Alt + F4"
    assert record.combo(0x41, {0x41}) is None            # "a": typing
    assert record.combo(0x41, {0x10, 0x41}) is None      # "A": typing too
    assert record.combo(0x20, {0x20}) is None            # Space: typing


def test_useful_names():
    assert record.useful_name({"name": "Insert", "kind": "TabItem", "x": 1, "y": 2, "w": 3,
                               "h": 4})["rect"] == [1, 2, 3, 4]
    # the arrow part of a split button takes the button's own name
    assert record.useful_name({"name": "More Options", "kind": "MenuItem",
                               "parent": "Shape Fill"})["name"] == "Shape Fill"
    # the whole window is not a button: the picture names it later
    assert record.useful_name({"name": "Presentation1 - PowerPoint", "kind": "Window"}) is None
    assert record.useful_name({"name": "", "kind": "Button"}) is None
    # private: an email, or the account's own name
    assert record.useful_name({"name": "pat@example.com", "kind": "Button"}) is None
    assert record.useful_name({"name": "Pat Lee Smith", "kind": "MenuItem"},
                              ["Pat Lee Smith"]) is None


def control(name, kind, x, y, w=40, h=30):
    return uiread.Control(name, kind, None, x, y, w, h)


def test_the_account_button_is_found_beside_minimize_only():
    window = (0, 0, 1600, 930)
    controls = [control("From Beginning", "Button", 260, 10), control("More Options", "Button", 215, 10),
                control("Pat Lee Smith", "MenuItem", 1398, 1, 50, 48),
                control("Minimize", "Button", 1448, 1, 48, 48), control("Close", "Button", 1544, 1),
                control("Share", "MenuItem", 1511, 49)]
    assert record.account_buttons(controls, window) == [(1398, 1, 50, 48)]
    assert record.account_buttons(controls[:3], window) == []   # no Minimize: nothing guessed


def test_colour_swatches_are_named_from_their_pixels():
    gold = Image.new("RGB", (400, 200), (40, 40, 40))
    gold.paste((255, 192, 0), (190, 90, 210, 110))
    assert record.swatch_name(gold) == "Orange"   # Office's name for #FFC000
    assert record.swatch_name(Image.new("RGB", (400, 200), (60, 60, 60))) is None   # a grey panel
    text = Image.new("RGB", (400, 200), (255, 255, 255))
    text.paste((0, 0, 0), (199, 95, 201, 105))                                      # an edge
    assert record.swatch_name(text) is None


class FakeRecorder:
    def __init__(self, rect, path, mouse=True):
        assert mouse is False   # tutorials draw their own cursor
        self.began = 0.0

    def stop(self):
        return 3.0


class FakeNamer:
    accounts = []

    def __init__(self):
        self.asked = []

    def ask(self, x, y, into):
        self.asked.append((x, y))
        if (x, y) == (110, 60):
            into.update(name="Insert", kind="TabItem", rect=[100, 50, 40, 20])

    def close(self):
        pass


def test_the_watcher_logs_moves_clicks_drags_keys_and_menus(tmp_path, monkeypatch):
    monkeypatch.setattr(record, "window_rect", lambda hwnd: (10, 10, 500, 300))
    monkeypatch.setattr(record, "area_for", lambda window, margin: (0, 0, 600, 400))
    ticks = iter([i * 0.1 for i in range(1000)])
    session = record.Session(1, str(tmp_path), recorder=FakeRecorder, read=lambda hwnd: [],
                             namer=FakeNamer(), clock=lambda: next(ticks))
    # (cursor, keys down, own pop-ups) per look
    frames = ([((110, 60), set(), [])] * 4                         # rests on Insert
              + [((110, 60), {0x01}, [])]                          # press
              + [((110, 60), set(), [(50, 80, 100, 200)])]         # release; a menu opens
              + [((300, 200), {0x01}, [(50, 80, 100, 200), (50, 80, 100, 200)])]   # press
              + [((340, 230), {0x01}, [])] + [((380, 260), set(), [])]           # drag
              + [((380, 260), {0x11}, [])] + [((380, 260), {0x11, 0x53}, [])]   # Ctrl + S
              + [((380, 260), {0x41}, [])] + [((380, 260), set(), [])]          # typing "a"
              + [((380, 260), {0x11, 0x10, 0x78}, [])])                         # stop hotkey
    looks = iter(frames)
    state = {}

    def cursor():
        state["now"] = next(looks)
        return state["now"][0]

    session.thread = None
    session.recorder = FakeRecorder(None, None, mouse=False)
    session._watch(cursor=cursor, down=lambda codes: state["now"][1],
                   sleep=lambda s: None, popups=lambda hwnd: state["now"][2])
    assert session.stopped.is_set()
    first, second = session.clicks
    assert (first["x"], first["y"], first["drag"]) == (110, 60, False)
    assert (second["x"], second["y"], second["drag"]) == (300, 200, True)
    assert (second["up_x"], second["up_y"]) == (380, 260)
    assert session._hovered[0] == {"name": "Insert", "kind": "TabItem", "rect": [100, 50, 40, 20]}
    assert [k for _t, k in session.keys] == ["Ctrl + S"]
    assert len(session.typing) == 1                         # that typing happened, nothing more
    assert "A" not in json.dumps(session.typing)
    # one rectangle for a menu and its shadow, and the moment it closed
    assert [rects for _t, rects in session.popups] == [[], [[50, 80, 100, 200]], []]


def test_stop_writes_the_log_with_names_and_private_areas(tmp_path, monkeypatch):
    monkeypatch.setattr(record, "window_rect", lambda hwnd: (10, 10, 500, 300))
    monkeypatch.setattr(record, "area_for", lambda window, margin: (5, 5, 600, 400))
    controls = [control("Pat Lee Smith", "MenuItem", 400, 11, 30, 30),
                control("Minimize", "Button", 440, 11), control("pat@example.com", "Text", 50, 50)]
    session = record.Session(1, str(tmp_path), recorder=FakeRecorder, read=lambda hwnd: controls,
                             namer=FakeNamer())
    session.__enter__()
    session.stopped.set()
    session.clicks = [{"t": 1.0, "x": 105, "y": 55, "button": "left",
                       "rect": [105, 55, 40, 20]}, {"t": 2.0, "x": 5, "y": 5, "button": "left"}]
    session._hovered = [{}, {"name": "Rectangle", "kind": "ListItem", "rect": [0, 0, 1, 1]}]
    log = session.stop()
    assert log["window"] == [5, 5, 500, 300] and log["length"] == 3.0
    assert log["clicks"][0]["rect"] == [100, 50, 40, 20]   # recording pixels
    assert log["clicks"][1]["name"] == "Rectangle"          # from where the mouse rested
    assert [395, 6, 30, 30] in log["private"] and [45, 45, 40, 30] in log["private"]
    saved = json.loads((tmp_path / "log.json").read_text(encoding="utf-8"))
    assert saved["clicks"][1]["name"] == "Rectangle"


def test_clicks_the_app_makes_are_logged_with_the_users_name(tmp_path, monkeypatch):
    monkeypatch.setattr(record, "window_rect", lambda hwnd: (10, 10, 500, 300))
    monkeypatch.setattr(record, "area_for", lambda window, margin: (5, 5, 600, 400))
    ticks = iter([1.0, 1.1, 5.0])
    session = record.Session(1, str(tmp_path), recorder=FakeRecorder, read=lambda hwnd: [],
                             namer=FakeNamer(), clock=lambda: next(ticks))
    session.recorder = FakeRecorder(None, None, mouse=False)
    polled = {"t": 0.95, "x": 100, "y": 50, "button": "left", "name": "Shape Fill Orange, Accent 2"}
    session.clicks.append(polled)
    session._hovered.append({})
    session.note_click(105, 55, "Shape Fill")            # the same click: merged, renamed
    assert len(session.clicks) == 1 and polled["name"] == "Shape Fill" and polled["by_app"]
    session.note_click(305, 155, "Orange")               # one the polling missed: added
    assert len(session.clicks) == 2 and session.clicks[1]["name"] == "Orange"
    assert session._noted(300, 150, 1.15)               # the watcher won't add it twice


def test_a_button_called_account_beside_minimize_is_hidden():
    window = (0, 0, 1600, 930)
    controls = [control("Account manager", "Button", 1398, 1, 50, 48),
                control("Minimize", "Button", 1448, 1, 48, 48)]
    assert record.account_buttons(controls, window) == [(1398, 1, 50, 48)]
