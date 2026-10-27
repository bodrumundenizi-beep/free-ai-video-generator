"""The app flipping a switch for a video itself: only safe ones, written down first, put back."""
from vidgen import drive, putback, tips, uiread, winmap

C = uiread.Control


def test_only_simple_settings_switches_may_be_flipped():
    night = C("Night light", "Button", "Off", 0, 0, 9, 9)
    assert tips.can_flip(winmap.BY_ID["nightlight"], night)
    assert tips.can_flip(winmap.BY_ID["colors"], C("Transparency effects", "Button", "On", 0, 0, 9, 9))
    assert not tips.can_flip(winmap.BY_ID["wifi"], C("Wi-Fi", "Button", "On", 0, 0, 9, 9))
    assert not tips.can_flip(winmap.BY_ID["display"], C("Use HDR", "Button", "Off", 0, 0, 9, 9))
    assert not tips.can_flip(winmap.BY_ID["nightlight"], C("Schedule", "ComboBox", None, 0, 0, 9, 9))
    assert not tips.can_flip(winmap.BY_ID["power-options"], night)          # not a Settings page
    # A risky name on an ordinary page.
    assert not tips.can_flip(winmap.BY_ID["notifications"],
                             C("Restart notifications", "Button", "Off", 0, 0, 9, 9))


def test_the_plan_offers_the_switch_the_tip_turns():
    tip = tips.fallback("turn on night light", [winmap.BY_ID["nightlight"]])
    tips.aim(tip, [C("Night light", "Button", "Off", 0, 0, 9, 9)], "turn on night light",
             winmap.BY_ID["nightlight"])
    assert tip.auto.name == "Night light" and tip.auto_to == "On"
    already = tips.fallback("turn on night light", [winmap.BY_ID["nightlight"]])
    tips.aim(already, [C("Night light", "Button", "On", 0, 0, 9, 9)], "turn on night light",
             winmap.BY_ID["nightlight"])
    assert already.auto is None                    # already on: pointed at, never clicked


def test_the_switch_is_written_down_and_read_back(tmp_path):
    folder = str(tmp_path)
    assert putback.read(folder) is None
    putback.write(folder, "nightlight", "Night light", "Off")
    assert putback.read(folder)["switch"] == "Night light"
    putback.clear(folder)
    assert putback.read(folder) is None
    (tmp_path / putback.NAME).write_text("{broken", encoding="utf-8")
    assert putback.read(folder) is None


def test_flip_clicks_then_checks_and_tries_once_more(monkeypatch):
    state = {"now": "Off", "clicks": 0, "works_on": 2}

    def click(self, control, right=False):
        state["clicks"] += 1
        if state["clicks"] >= state["works_on"]:   # the first click didn't take
            state["now"] = "On"

    monkeypatch.setattr(drive.Driver, "click", click)
    read = lambda hwnd: [C("Night light", "Button", state["now"], 0, 0, 9, 9)]
    assert drive.flip(5, "Night light", "On", read=read, sleep=lambda s: None)
    assert state["clicks"] == 2
    assert drive.flip(5, "Night light", "On", read=read, sleep=lambda s: None)   # already so
    assert not drive.flip(5, "Gone", "On", read=read, sleep=lambda s: None)
    state.update(now="Off", clicks=0, works_on=9)
    assert not drive.flip(5, "Night light", "On", read=read, sleep=lambda s: None)


def test_a_person_taking_over_stops_the_flip(monkeypatch):
    def click(self, control, right=False):
        raise drive.Interrupted("the mouse was moved")

    monkeypatch.setattr(drive.Driver, "click", click)
    read = lambda hwnd: [C("Night light", "Button", "Off", 0, 0, 9, 9)]
    assert not drive.flip(5, "Night light", "On", read=read, sleep=lambda s: None)


def test_a_show_more_step_is_one_the_app_clicks():
    place = winmap.BY_ID["display-graphics"]
    text = "turn on hardware accelerated gpu scheduling"
    tip = tips.fallback(text, [place])
    tips.aim(tip, [C("Show more graphics settings", "Button", None, 0, 0, 9, 9)], text, place)
    assert tip.steps[0].nav and tip.steps[0].target == "Show more graphics settings"
    # Opened while planning: the switch is known, and the tip still starts by opening it.
    opened = C("Show more graphics settings", "Button", None, 0, 0, 9, 9)
    tip = tips.fallback(text, [place])
    tips.aim(tip, [C("Hardware-accelerated GPU scheduling", "Button", "Off", 0, 0, 9, 9)], text,
             place, opened)
    assert tip.steps[0].nav
    assert "Hardware-accelerated GPU scheduling" in [s.target for s in tip.steps[1:]]
    assert tip.auto is None                         # needs a restart: never flipped by the app


def test_the_switch_is_flipped_not_the_page_title_with_its_name(monkeypatch):
    clicked = []
    monkeypatch.setattr(drive.Driver, "click", lambda self, c, right=False: clicked.append(c))
    page = [C("Notifications", "Button", None, 0, 0, 300, 40),       # the title, first
            C("Notifications", "Button", "Off", 600, 90, 40, 20)]    # the switch
    drive.flip(5, "Notifications", "On", read=lambda h: page, sleep=lambda s: None)
    assert clicked and clicked[0].state == "Off"
    assert uiread.find(page, "Notifications").state == "Off"
