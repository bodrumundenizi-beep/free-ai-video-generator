"""Do it for me (5.4, plan step 5b) against a fake window: every way it can go wrong."""
import json

import pytest

from vidgen import uiread
from vidgen.tutorial import auto


def control(name, x, y, kind="Button", w=40, h=20):
    return uiread.Control(name, kind, None, x, y, w, h)


class FakeScreen:
    """A pretend app: Insert (a tab), Shapes (opens a menu with Rectangle), Shape Fill. A click
    on a working button changes the screen; ``dead`` buttons do nothing; ``hidden`` ones aren't
    in Windows' list; ``hands`` make the user's hand move the mouse during the next glides."""

    def __init__(self, dead=(), hidden=(), hands=0, picture=None, front=True, title="Paint"):
        self.dead, self.hidden, self.hands, self.picture = set(dead), set(hidden), hands, picture
        self.front, self._title = front, title
        self.area = (0, 0, 1000, 600)
        self.ribbon_changes = True   # a tab's click changes the buttons under it
        self.sticky_menu = False     # a pick leaves its menu open
        self.menu_open = False
        self.screen_id = 0
        self.clicked, self.glides = [], []

    def title(self, hwnd):
        return self._title

    def controls(self, hwnd):
        found = [control("Insert", 100, 10, "TabItem"), control("Shapes", 200, 40),
                 control("Shape Fill", 300, 40, "SplitButton"), control("Close", 900, 0)]
        popups = []
        if self.menu_open:
            found.append(control("Rectangle", 210, 80, "ListItem"))
            popups.append([190, 60, 120, 200])
        return [c for c in found if c.name not in self.hidden], popups

    def by_picture(self, name):
        return self.picture

    def popups(self, hwnd):
        return self.controls(hwnd)[1]

    def signature(self):
        return self.screen_id

    def difference(self, a, b, part=None):
        if a == b:
            return 0.0
        return 50.0 if part is None or self.ribbon_changes else 0.0

    def to_front(self, hwnd):
        return self.front

    def glide(self, x, y, seconds):
        self.glides.append((x, y))
        if self.hands:
            self.hands -= 1
            return False
        return True

    def click(self, x, y):
        self.clicked.append((x, y))
        hit = None
        hidden, self.hidden = self.hidden, set()   # hidden from Windows' list, still on screen
        everything = self.controls(1)[0]
        self.hidden = hidden
        for c in everything:
            if c.x <= x < c.x + c.w and c.y <= y < c.y + c.h:
                hit = c.name
        if hit and hit not in self.dead:
            self.screen_id += 1
            self.menu_open = hit in ("Shapes", "More Options") or (self.sticky_menu and self.menu_open)

    @staticmethod
    def sleep(seconds):
        pass


def always(answer):
    asked = []

    def ask(kind, action, target, why):
        asked.append((kind, action.name, why))
        return {"approve": answer, "hand": "done", "moved": "continue"}[kind]
    ask.asked = asked
    return ask


def test_parse_the_users_steps():
    actions = auto.parse("Insert → Shapes -> click the Rectangle button, drag on the slide; "
                         "Shape Fill then Gold")
    assert [(a.kind, a.name) for a in actions] == [
        ("click", "Insert"), ("click", "Shapes"), ("click", "Rectangle"),
        ("hand", "drag on the slide"), ("click", "Shape Fill"), ("click", "Gold")]


def test_the_app_does_every_step_it_can_and_hands_over_the_rest():
    io = FakeScreen()
    ask = always("yes")
    report = auto.Runner(1, auto.parse("Insert → Shapes → Rectangle, drag it"), ask, io).run()
    assert [r.done_by for r in report.results] == ["app", "app", "app", "user"]
    assert io.clicked == [(120, 20), (220, 50), (230, 90)]   # Rectangle: in the menu that opened
    # one question for the chain written with arrows: no click on the app's own box between
    # Shapes opening its menu and the pick in it (that click closed the menu)
    assert [k for k, *_ in ask.asked] == ["approve", "hand"]
    assert ask.asked[0][1] == "Insert → Shapes → Rectangle"
    assert report.summary().startswith("The app did 3 of 4 steps.")


def test_nothing_happens_without_a_yes():
    io = FakeScreen()
    report = auto.Runner(1, auto.parse("Insert, Shapes"), always("self"), io).run()
    assert io.clicked == [] and [r.done_by for r in report.results] == ["user", "user"]


def test_stop_stops_at_once():
    io = FakeScreen()
    report = auto.Runner(1, auto.parse("Insert, Shapes"), always("stop"), io).run()
    assert io.clicked == [] and report.results == []


def test_a_click_that_changes_nothing_is_handed_over_with_the_reason():
    io = FakeScreen(dead={"Shapes"})
    report = auto.Runner(1, auto.parse("Shapes"), always("yes"), io).run()
    assert report.results[0].done_by == "user"
    assert "didn't change anything" in report.results[0].why


def test_a_missing_button_is_found_by_picture_or_handed_over():
    missing = auto.Runner(1, auto.parse("Gold"), always("yes"), FakeScreen()).run()
    assert missing.results[0].done_by == "user" and "couldn't find" in missing.results[0].why
    # found only in the picture: not 100 % sure, so the cursor shows it and the user clicks
    io = FakeScreen(hidden={"Shapes"}, picture=(220, 50))
    guessed = auto.Runner(1, auto.parse("Shapes"), always("yes"), io).run()
    assert guessed.results[0].done_by == "user" and io.clicked == []
    assert "picture" in guessed.results[0].why and (220, 50) in io.glides


def test_the_users_hand_pauses_the_app():
    io = FakeScreen(hands=1)
    ask = always("yes")
    report = auto.Runner(1, auto.parse("Insert"), ask, io).run()
    assert ask.asked[0][0] == "moved" and report.results[0].done_by == "app"
    stubborn = auto.Runner(1, auto.parse("Insert"), always("yes"), FakeScreen(hands=5)).run()
    assert stubborn.results[0].done_by == "user" and "kept moving" in stubborn.results[0].why


def test_a_window_that_wont_come_to_the_front_is_handed_over():
    report = auto.Runner(1, auto.parse("Insert"), always("yes"), FakeScreen(front=False)).run()
    assert report.results[0].done_by == "user" and "front" in report.results[0].why


def test_never_in_security_or_sign_in_windows():
    for title in ("Windows Security", "User Account Control", "Sign in to your account",
                  "Enter your password"):
        with pytest.raises(PermissionError):
            auto.Runner(1, auto.parse("OK"), always("yes"), FakeScreen(title=title)).run()


def test_window_buttons_only_when_asked_exactly():
    controls = [uiread.Control("Close", "Button", None, 900, 0, 40, 20),
                uiread.Control("Close Ribbon", "Button", None, 10, 10, 40, 20)]
    assert auto.find(controls, "close").name == "Close"
    assert auto.find(controls, "clos") is None or auto.find(controls, "clos").name == "Close Ribbon"


def test_open_menus_win_over_the_rest():
    controls = [uiread.Control("Rectangle", "Button", None, 10, 10, 40, 20),
                uiread.Control("Rectangle", "ListItem", None, 210, 80, 40, 20)]
    assert auto.find(controls, "Rectangle", popups=[[190, 60, 120, 200]]).x == 210


def test_suggestions_come_back_as_editable_text():
    def ask(system, user, schema):
        assert "PowerPoint" in user
        return json.dumps({"steps": ["Insert", " Shapes ", "drag on the slide"]})
    assert auto.suggest("add a rectangle", "PowerPoint", ask) == "Insert → Shapes → drag on the slide"


def test_a_tab_that_is_already_open_counts_as_done():
    io = FakeScreen()
    open_tab = uiread.Control("Insert", "TabItem", "Selected", 100, 10, 40, 20)
    io.controls = lambda hwnd: ([open_tab, control("Shapes", 200, 40)], [])
    report = auto.Runner(1, auto.parse("Insert, Shapes"), always("yes"), io).run()
    assert [r.done_by for r in report.results] == ["app", "app"]
    assert report.results[0].why == "already open" and io.clicked == [(220, 50)]


def test_chains_come_from_arrows_and_commas_part_them():
    actions = auto.parse("Insert → Shapes → Rectangle, drag on the slide, Shape Format → Shape Fill → Gold")
    assert [[a.name for a in c] for c in auto.chains(actions)] == [
        ["Insert", "Shapes", "Rectangle"], ["drag on the slide"],
        ["Shape Format", "Shape Fill", "Gold"]]


def test_ill_do_it_hands_over_the_whole_chain_once():
    io = FakeScreen()
    ask = always("self")
    report = auto.Runner(1, auto.parse("Insert → Shapes, Shape Fill"), ask, io).run()
    assert io.clicked == []
    assert [k for k, *_ in ask.asked] == ["approve", "hand", "approve", "hand"]
    assert [r.done_by for r in report.results] == ["user", "user", "user"]


def test_a_chain_into_a_menu_clicks_the_split_buttons_arrow():
    """Shape Fill's main half paints straight away; its arrow opens the colours."""
    io = FakeScreen()
    fill = control("Shape Fill", 300, 40, "Button", 24, 24)
    arrow = control("More Options", 324, 40, "MenuItem", 24, 24)
    orange = control("Orange", 330, 120, "ListItem")
    io.controls = lambda hwnd: ([fill, arrow, orange], [[300, 70, 200, 200]] if io.menu_open else [])
    told = []
    auto.Runner(1, auto.parse("Shape Fill → Orange"), always("yes"), io,
                on_click=lambda x, y, name: told.append(name)).run()
    assert io.clicked[0] == (336, 52)            # the arrow, not the paint half
    assert told == ["Shape Fill", "Orange"]       # the user's words for the labels
    alone = FakeScreen()
    alone.controls = lambda hwnd: ([fill, arrow], [[300, 70, 200, 200]] if alone.menu_open else [])
    auto.Runner(1, auto.parse("Shape Fill"), always("yes"), alone).run()
    assert alone.clicked == [(312, 52)]          # on its own: the button itself


def test_a_tab_that_changes_nothing_was_already_open():
    io = FakeScreen(dead={"Insert"})               # Office: every tab says "not selected"
    report = auto.Runner(1, auto.parse("Insert"), always("yes"), io).run()
    assert report.results[0].done_by == "app" and report.results[0].why == "already open"


def test_only_sure_clicks_similar_or_doubled_names_go_to_the_user():
    similar = FakeScreen()
    report = auto.Runner(1, auto.parse("Shape"), always("yes"), similar).run()   # "Shapes"…
    assert report.results[0].done_by == "user" and "similar" in report.results[0].why
    assert similar.clicked == []
    doubled = FakeScreen()
    twins = [control("Save", 100, 10), control("Save", 600, 300)]
    doubled.controls = lambda hwnd: (twins, [])
    report = auto.Runner(1, auto.parse("Save"), always("yes"), doubled).run()
    assert report.results[0].done_by == "user" and "2 buttons" in report.results[0].why
    assert doubled.clicked == []


def test_office_names_with_their_value_count_as_sure():
    controls = [control("Shape Fill Orange, Accent 2", 300, 40)]
    found, doubt = auto.certain(controls, "Shape Fill")
    assert found is controls[0] and doubt == ""


def test_the_same_item_twice_in_the_open_menu_is_sure():
    controls = [control("Rectangle", 455, 189, "ListItem"), control("Rectangle", 407, 276, "ListItem")]
    found, doubt = auto.certain(controls, "Rectangle", popups=[[381, 159, 309, 407]])
    assert doubt == "" and found.name == "Rectangle"


def test_a_click_that_should_open_a_menu_but_doesnt_is_handed_over():
    io = FakeScreen()   # Shape Fill (no arrow here) changes the screen but opens no menu
    report = auto.Runner(1, auto.parse("Shape Fill → Orange"), always("yes"), io).run()
    assert report.results[0].done_by == "user" and "didn't open a menu" in report.results[0].why


def test_a_pick_that_leaves_its_menu_open_is_handed_over():
    io = FakeScreen()
    io.sticky_menu = True
    report = auto.Runner(1, auto.parse("Shapes → Rectangle"), always("yes"), io).run()
    assert [r.done_by for r in report.results] == ["app", "user"]
    assert "stayed open" in report.results[1].why


def test_a_tab_whose_buttons_dont_change_is_handed_over():
    io = FakeScreen()
    io.ribbon_changes = False          # the click changed something, but not under the tab
    report = auto.Runner(1, auto.parse("Insert"), always("yes"), io).run()
    assert report.results[0].done_by == "user" and "not the buttons under it" in report.results[0].why


def test_a_small_menu_in_a_big_window_counts_as_a_change():
    import numpy as np
    io = auto.RealScreen(1, (0, 0, 2000, 1200))
    before = np.full((288, 480), 250, dtype=np.int16)          # a big white page
    after = before.copy()
    # Notepad++'s Search menu at its real size there: 352 x 614 of a 3348-wide area -> 50 x 88
    after[10:98, 40:90] = 240                                    # a white menu: faint...
    after[14:96:4, 45:80] = 60                                   # ...but its lines of text
    assert io.difference(before, after) >= auto.CHANGED          # the average alone was 0.46
    assert io.difference(before, before) == 0


def test_names_keep_their_dots():
    assert [a.name for a in auto.parse("Search → Find..., Save.")] == ["Search", "Find...", "Save"]
