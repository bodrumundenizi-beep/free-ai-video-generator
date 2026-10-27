"""The app finding a page itself: which way, what it types and clicks, and when it stops."""
import pytest

from vidgen import drive, screen, uiread, winmap

C = uiread.Control


def test_each_kind_of_place_has_its_way_there():
    assert drive.route(winmap.BY_ID["nightlight"]) == "settings"
    assert drive.route(winmap.BY_ID["task-manager"]) == "taskbar"
    assert drive.route(winmap.BY_ID["sound-classic"]) == "run"
    assert drive.route(winmap.BY_ID["startup-folder"]) == "run"
    assert drive.route(winmap.BY_ID["steps-recorder"]) == ""      # an app: opened directly
    assert drive.route(winmap.BY_ID["firewall"]) == ""            # admin: refused anyway
    assert drive.route(None) == ""


def test_the_run_box_gets_the_command_a_person_would_type():
    assert drive.run_text(winmap.BY_ID["sound-classic"]) == "mmsys.cpl"
    assert drive.run_text(winmap.BY_ID["power-options"]) == "control /name Microsoft.PowerOptions"
    assert drive.run_text(winmap.BY_ID["startup-folder"]) == "shell:Startup"
    assert drive.run_text(winmap.BY_ID["dxdiag"]) == "dxdiag"


def test_the_search_result_for_the_page_is_picked():
    # As read from Settings on 2026-10-26 after typing "Night light".
    found = [C("Night light", "ListItem", "Selected", 1927, 873, 496, 44),
             C("Night light automatic on with time", "ListItem", "NotSelected", 1927, 917, 496, 44),
             C("Thumbs up, good response", "Button", "Off", 2348, 978, 32, 32)]
    assert drive.pick(found, winmap.BY_ID["nightlight"]) is found[0]
    menu = [C("Task Manager", "MenuItem", None, 2205, 2068, 149, 28),
            C("Taskbar settings", "MenuItem", None, 2205, 2103, 149, 28)]
    assert drive.pick(menu, winmap.BY_ID["task-manager"]) is menu[0]
    assert drive.pick(menu, winmap.BY_ID["nightlight"]) is None


def test_only_what_typing_brought_up_counts_as_results():
    home = [C("System", "ListItem", None, 0, 0, 10, 10)]
    after = home + [C("Night light", "ListItem", None, 0, 20, 10, 10)]
    assert drive.new_controls(home, after) == after[1:]
    assert drive.search_box([C("Search box, Find a setting", "Edit", None, 0, 0, 5, 5)])


def test_a_result_that_led_to_another_page_is_noticed():
    sense = [C("Run Storage Sense now", "Button", None, 0, 0, 9, 9),
             C("Cleanup of temporary files", "CheckBox", "On", 0, 20, 9, 9)]
    storage = [C("Installed apps", "Button", None, 0, 0, 9, 9),
               C("Storage Sense", "Button", "On", 0, 20, 9, 9)]
    assert drive.same_page(sense, sense) and not drive.same_page(storage, sense)
    assert drive.same_page(storage, [])          # nothing planned to compare with


def test_the_taskbar_is_right_clicked_in_its_widest_empty_stretch():
    bar = [C("Start", "Button", "Off", 0, 2112, 55, 48),
           C("Settings - 1 running window", "Button", None, 804, 2112, 96, 48),
           C("Show Hidden Icons", "Button", None, 3586, 2112, 32, 48)]
    assert drive.taskbar_gap(bar) == ((900 + 3586) // 2, 2136)
    side = [C("Start", "Button", None, 0, 0, 48, 48), C("x", "Button", None, 0, 60, 48, 48),
            C("y", "Button", None, 0, 120, 48, 48)]
    assert drive.taskbar_gap(side) is None


def test_the_menu_is_recorded_with_a_margin_inside_the_screen():
    items = [C("Task Manager", "MenuItem", None, 2205, 2068, 149, 28),
             C("Taskbar settings", "MenuItem", None, 2205, 2103, 149, 28)]
    left, top, width, height = drive.menu_rect(items, (3840, 2160))
    assert (left, top) == (2145, 2008) and top + height <= 2160 and width % 2 == height % 2 == 0


def test_typing_sends_each_letter_down_then_up():
    assert drive.key_events("ab") == [(0, 97, 4), (0, 97, 6), (0, 98, 4), (0, 98, 6)]


@pytest.fixture
def desk(monkeypatch):
    state = {"front": 5, "cursor": (100, 100)}
    monkeypatch.setattr(screen, "foreground", lambda: state["front"])
    monkeypatch.setattr(screen, "cursor", lambda: state["cursor"])
    return state


def test_it_stops_when_another_window_comes_up_or_the_mouse_is_moved(desk):
    d = drive.Driver(5)
    d.check()
    desk["front"] = 6
    with pytest.raises(drive.Interrupted, match="another window"):
        d.check()
    desk["front"], d.at = 5, (100, 100)
    desk["cursor"] = (300, 100)                       # the person moved the mouse
    with pytest.raises(drive.Interrupted, match="mouse"):
        d.check()
    with pytest.raises(drive.Interrupted, match="cancelled"):
        drive.Driver(5, stop=lambda: True).check()


def test_it_stops_when_a_window_with_admin_rights_is_in_front(desk, monkeypatch):
    monkeypatch.setattr(drive, "elevated", lambda hwnd: hwnd == 5)   # Task Manager
    with pytest.raises(drive.Interrupted, match="admin rights"):
        drive.Driver(None).check()


def test_settings_search_types_the_name_and_clicks_its_result(desk, monkeypatch):
    done, typed = [], []
    box = C("Search box, Find a setting", "Edit", None, 10, 10, 200, 30)
    result = C("Night light", "ListItem", None, 10, 50, 200, 40)
    reads = iter([[box], [box], [box, result]])
    monkeypatch.setattr(drive.Driver, "click", lambda self, c, right=False: done.append(c.name))
    monkeypatch.setattr(drive.Driver, "type", lambda self, text: typed.append(text))
    found = drive.settings_search(5, winmap.BY_ID["nightlight"],
                                  lambda voice, c: done.append(voice), read=lambda h: next(reads))
    assert found and typed == ["Night light"]
    assert done == [box.name, "Press Windows and I to open Settings, then search for Night light.", "Night light",
                    "Pick Night light."]


def test_settings_search_gives_up_without_a_result(desk, monkeypatch):
    box = C("Search box, Find a setting", "Edit", None, 10, 10, 200, 30)
    keys = []
    monkeypatch.setattr(drive, "FIND_FOR", 1.0)
    monkeypatch.setattr(drive.Driver, "click", lambda self, c, right=False: None)
    monkeypatch.setattr(drive.Driver, "type", lambda self, text: None)
    monkeypatch.setattr(drive.Driver, "key", lambda self, vk: keys.append(vk))
    assert not drive.settings_search(5, winmap.BY_ID["nightlight"], lambda *a: None,
                                     read=lambda h: [box])
    assert keys == [drive.VK_ESCAPE]


def test_show_more_is_found_by_scrolling_then_clicked(desk, monkeypatch):
    more = C("Show more graphics settings", "Button", None, 10, 900, 300, 40)
    reads = iter([[], [], [more], [more], [more]])
    done = []
    monkeypatch.setattr(drive.Driver, "scroll", lambda self, x, y, ticks=5: done.append("scroll"))
    monkeypatch.setattr(drive.Driver, "click", lambda self, c, right=False: done.append(c.name))
    found, _after = drive.open_more(5, (0, 0, 800, 600), read=lambda h: next(reads),
                                    sleep=lambda s: None)
    assert found is more and done == ["scroll", "scroll", more.name]


def test_show_more_gives_up_after_three_scrolls(desk, monkeypatch):
    scrolls = []
    monkeypatch.setattr(drive.Driver, "scroll", lambda self, x, y, ticks=5: scrolls.append(1))
    assert drive.open_more(5, (0, 0, 800, 600), read=lambda h: [], sleep=lambda s: None)[0] is None
    assert len(scrolls) == 3


def test_the_page_is_read_once_it_has_settled():
    loading = [C("Storage Sense", "Button", "Off", 0, 0, 9, 9)]
    ready = [C("Storage Sense", "Button", "On", 0, 0, 9, 9)]
    reads = iter([loading, ready, ready])
    assert drive.settled(5, read=lambda h: next(reads), sleep=lambda s: None) == ready


def test_names_with_numbers_dont_decide_the_page():
    planned = [C("Installed apps 120 GB", "Button", None, 0, 0, 9, 9),
               C("Cleanup recommendations", "Button", None, 0, 0, 9, 9)]
    now = [C("Installed apps 3.2 GB", "Button", None, 0, 0, 9, 9),
           C("Cleanup recommendations", "Button", None, 0, 0, 9, 9)]
    assert drive.same_page(now, planned)
