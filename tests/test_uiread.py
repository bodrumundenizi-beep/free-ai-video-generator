"""Reading a window's real buttons, hiding private details, zooming onto the click."""
import json
from types import SimpleNamespace

import pytest

from vidgen import cache, screen, uiread, writer

PAGE = [
    {"name": "Minimize Settings", "kind": "Button", "state": None, "x": 2778, "y": 820, "w": 46, "h": 32},
    {"name": "Deniz Kartal Ser bodrum@example.com", "kind": "Button", "state": None,
     "x": 1439, "y": 869, "w": 290, "h": 75},
    {"name": "User profile picture", "kind": "Image", "state": None, "x": 1450, "y": 877, "w": 60, "h": 60},
    {"name": "Deniz Kartal Ser", "kind": "Text", "state": None, "x": 1522, "y": 890, "w": 158, "h": 19},
    {"name": "bodrum@example.com", "kind": "Text", "state": None, "x": 1522, "y": 909, "w": 158, "h": 16},
    {"name": "System", "kind": "ListItem", "state": "Selected", "x": 1450, "y": 1003, "w": 280, "h": 36},
    {"name": "Turn on now", "kind": "Button", "state": None, "x": 2679, "y": 951, "w": 130, "h": 32},
    {"name": "Turn on now", "kind": "Text", "state": None, "x": 2690, "y": 955, "w": 80, "h": 20},
    {"name": "Schedule night light", "kind": "Button", "state": "Off", "x": 2737, "y": 1091, "w": 72, "h": 40},
    {"name": "Balanced", "kind": "RadioButton", "state": "Selected", "x": 10, "y": 10, "w": 20, "h": 20},
]


def controls():
    return uiread.parse(json.dumps(PAGE))


def test_parse_reads_lists_single_controls_and_survives_garbage():
    assert len(controls()) == len(PAGE)
    assert len(uiread.parse(json.dumps(PAGE[6]))) == 1          # PowerShell's single item
    assert uiread.parse("not json") == [] and uiread.parse("[{\"kind\": 1}]") == []


def test_a_blocked_powershell_gives_no_buttons():
    blocked = lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="blocked")
    assert uiread.read(123, run=blocked) == []

    def missing(*a, **k):
        raise OSError("no powershell")
    assert uiread.read(123, run=missing) == []


def test_the_script_is_sent_encoded_with_the_window_handle():
    seen = {}

    def run(args, **kwargs):
        seen["args"] = args
        return SimpleNamespace(returncode=0, stdout=json.dumps(PAGE[6:7]))

    assert uiread.read(4242, run=run)[0].name == "Turn on now"
    import base64
    script = base64.b64decode(seen["args"][-1]).decode("utf-16-le")
    assert "-Handle 4242" in script and "UIAutomationClient" in script


def test_the_account_name_email_and_picture_are_hidden():
    rects = uiread.private(controls())
    assert (1439, 869, 290, 75) in rects        # the account button
    assert (1450, 877, 60, 60) in rects         # the picture
    assert (1522, 890, 158, 19) in rects        # the name, found from the account button
    assert (2679, 951, 130, 32) not in rects


def test_the_name_is_hidden_where_it_shows_in_part():
    C = uiread.Control
    page = [C("Deniz Kartal Ser bodrum@example.com", "Button", None, 0, 0, 290, 75),
            C("Deniz Kartal - Personal", "Text", None, 100, 900, 200, 19),
            C("Locally available cloud content", "Text", None, 100, 850, 200, 19)]
    rects = uiread.private(page)
    assert (100, 900, 200, 19) in rects and (100, 850, 200, 19) not in rects


def test_the_first_name_alone_and_the_pc_name_under_it_are_hidden():
    C = uiread.Control
    page = [C("Deniz Kartal Ser bodrum@example.com", "Button", None, 0, 0, 290, 75),
            C("Deniz", "Text", None, 385, 640, 60, 20),                  # Settings home card
            C("EXCALIBUR GAMING E750", "Text", None, 385, 664, 115, 16),
            C("Rename", "Hyperlink", None, 385, 682, 40, 16),
            C("Recommended settings", "Text", None, 303, 740, 160, 20)]
    rects = uiread.private(page)
    assert (385, 640, 60, 20) in rects and (385, 664, 115, 16) in rects
    assert (385, 682, 40, 16) in rects and (303, 740, 160, 20) not in rects


def test_targets_are_real_clickable_buttons_without_chrome_or_private_ones():
    names = [c.name for c in uiread.targets(controls())]
    assert names == ["System", "Turn on now", "Schedule night light", "Balanced"]


def test_only_switches_and_option_buttons_count_as_settings():
    states = uiread.states(controls())
    assert states == {"Schedule night light": "Off", "Balanced": "Selected"}
    assert uiread.changed(states, {"Schedule night light": "On", "Balanced": "Selected"}) == \
        {"Schedule night light": "Off"}
    assert uiread.changed(states, states) == {}


def test_a_page_is_kept_per_place_without_private_details(tmp_path):
    store = cache.Cache(str(tmp_path), "winui")
    uiread.remember(store, "nightlight", controls())
    kept = uiread.recall(store, "nightlight")
    assert [c.name for c in kept] == ["System", "Turn on now", "Schedule night light", "Balanced"]
    assert not any("@" in c.name for c in kept) and uiread.recall(store, "colors") == []
    assert uiread.page_key("nightlight") != uiread.page_key("colors")


def test_the_writer_can_only_name_real_buttons():
    schema = writer.tip_schema(["Turn on now", "Strength", "Turn on now"])
    target = schema["properties"]["steps"]["items"]["properties"]["target"]
    assert target["enum"] == ["Turn on now", "Strength", ""]
    assert "target" not in writer.tip_schema()["properties"]["steps"]["items"]["properties"]


# --- zoom, ring, blocks ------------------------------------------------------------------------

def test_the_zoom_ends_on_the_button_and_stays_inside_the_window():
    zoom, left, top = screen.zoom_plan((1484, 1122), (1246, 132, 130, 32))
    assert zoom == 2.5
    assert left == pytest.approx(1484 - 1484 / 2.5) and top == 0       # kept inside
    view_w = 1484 / zoom
    assert left <= 1246 and 1246 + 130 <= left + view_w                # the button is in view


def test_a_click_without_a_button_zooms_on_the_area_round_it():
    zoom, left, top = screen.zoom_plan((1000, 600), (500, 300, 0, 0))
    assert 1.0 < zoom <= screen.ZOOM_MOST and 0 <= left and 0 <= top


def test_the_cut_hides_private_parts_zooms_and_rings():
    calls = []
    screen.cut("rec.mp4", (0.0, 2.5), (1080, 1920), "out.mp4", run=calls.append,
               window=(1484, 1122), focus=(1246, 132, 130, 32), blurs=[(6, 50, 290, 75)])
    graph = calls[0][calls[0].index("-filter_complex") + 1]
    assert "flags=neighbor" in graph and "zoompan" in graph and "drawbox" in graph
    assert graph.index("neighbor") < graph.index("zoompan")   # hidden before anything else


def test_no_window_size_means_no_zoom_and_no_blocks():
    calls = []
    screen.cut("rec.mp4", (0.0, 2.5), (1080, 1920), "out.mp4", run=calls.append,
               focus=(1, 1, 5, 5), blurs=[(1, 1, 50, 50)])
    graph = calls[0][calls[0].index("-filter_complex") + 1]
    assert "zoompan" not in graph and "neighbor" not in graph


def test_the_last_click_in_a_step_is_used_in_window_pixels():
    clicks = [(0.5, 1500, 900), (1.8, 1600, 950), (4.0, 1700, 1000), (1.9, 50, 50)]
    assert screen.last_click(clicks, (0.0, 2.0), (1433, 819, 1484, 1122)) == (167, 131, 0, 0)
    assert screen.last_click(clicks, (5.0, 6.0), (1433, 819, 1484, 1122)) is None


def test_the_side_menu_path_search_and_internal_names_are_left_out():
    page = controls() + uiread.parse(json.dumps([
        {"name": "Search box, Find a setting", "kind": "Edit", "state": None, "x": 1917, "y": 828, "w": 516, "h": 32},
        {"name": "Display", "kind": "Button", "state": None, "x": 1959, "y": 871, "w": 135, "h": 44},
        {"name": "SystemSettings.ViewModel.SettingEntry", "kind": "ListItem", "state": None, "x": 1826, "y": 971, "w": 1000, "h": 70},
        {"name": "?", "kind": "Button", "state": None, "x": 2785, "y": 990, "w": 32, "h": 32},
    ]))
    names = [c.name for c in uiread.targets(page, (1433, 819, 1484, 1122))]
    assert names == ["Turn on now", "Schedule night light", "Balanced"]


def test_buttons_are_described_by_kind_for_the_writer():
    by_name = {c.name: c for c in controls()}
    assert uiread.label(by_name["Schedule night light"]) == "Schedule night light (switch, now off)"
    assert uiread.label(by_name["Balanced"]) == "Balanced (option, chosen)"
    assert uiread.label(by_name["Turn on now"]) == "Turn on now (button)"


def test_a_switch_with_an_internal_name_takes_its_rows_name():
    row = uiread.parse(json.dumps([
        {"name": "Spotify", "kind": "Text", "state": None, "x": 100, "y": 205, "w": 80, "h": 16},
        {"name": "App.ViewModel.Entry", "kind": "Button", "state": "On", "x": 600, "y": 200, "w": 72, "h": 40},
    ]))
    assert uiread.states(row) == {"Spotify": "On"} and uiread.find(row, "Spotify").x == 600


# --- the app picks the button, not the Smart writer --------------------------------------------

def graphics_page():
    return uiread.parse(json.dumps([
        {"name": "Auto HDR", "kind": "Button", "state": "On", "x": 0, "y": 300, "w": 72, "h": 40},
        {"name": "Optimizations for windowed games", "kind": "Button", "state": "On", "x": 0, "y": 400, "w": 72, "h": 40},
        {"name": "Hardware-accelerated GPU scheduling", "kind": "Button", "state": "Off", "x": 0, "y": 500, "w": 72, "h": 40},
    ]))


def test_the_button_is_matched_by_the_tips_words_and_short_forms():
    from vidgen import winmap

    place = winmap.BY_ID["display-graphics"]
    words = ["turn on hags"] + [w for w in place.words if "hags" in w or "gpu" in w]
    assert uiread.best_match(graphics_page(), words).name == "Hardware-accelerated GPU scheduling"
    assert uiread.best_match(graphics_page()[:2], ["turn on gpu scheduling"]) is None


def plan(text, page):
    from vidgen import tips, winmap

    place = winmap.BY_ID["display-graphics"]
    answer = {"search_words": "", "steps": [
        {"guide": "Click Auto HDR", "voice": "Turn on HDR.", "changes_setting": True, "target": "Auto HDR"},
        {"guide": "Done", "voice": "That's it.", "changes_setting": False, "target": ""}]}
    tip = tips.from_answer(text, answer, place)
    return tip, tips.aim(tip, page, text, place)


def test_the_writers_wrong_button_is_replaced_by_the_matching_one():
    tip, note = plan("how to enable hardware accelerated gpu scheduling", graphics_page())
    assert note == "" and tip.steps[0].target == "Hardware-accelerated GPU scheduling"
    assert tip.steps[0].waits_for_change and not tip.steps[0].already


def test_a_switch_already_on_is_said_and_not_waited_for():
    page = graphics_page()
    page[2] = page[2].__class__(page[2].name, page[2].kind, "On", 0, 500, 72, 40)
    tip, _note = plan("turn on hags", page)
    step = tip.steps[0]
    assert step.already and not step.waits_for_change and "already on" in step.voice_on
    from vidgen import tips
    assert "already on" in tips.script(tip, False, ["a.mp4", "b.mp4"])


def test_no_matching_button_names_no_button_at_all():
    tip, note = plan("turn on gpu scheduling", graphics_page()[:2])
    assert "zooms where you click" in note
    assert all(s.target == "" for s in tip.steps)       # the writer's "Auto HDR" is gone
    assert "gpu scheduling" in tip.steps[0].guide and tip.steps[0].waits_for_change
    assert not any("HDR" in s.guide or "HDR" in s.voice_off for s in tip.steps)
    assert tip.steps[0].voice_off.startswith("Here's how to turn on gpu scheduling")


def test_a_hidden_switch_gets_a_first_step_that_opens_its_section():
    more = uiread.parse(json.dumps([{"name": "Show more settings", "kind": "Button",
                                     "state": None, "x": 0, "y": 600, "w": 200, "h": 40}]))
    tip, note = plan("turn on hags", graphics_page()[:2] + more)
    assert "Show more settings" in note
    assert tip.steps[0].target == "Show more settings" and len(tip.steps) == 3
    assert tip.steps[1].target == "" and tip.steps[1].waits_for_change


def test_match_words_expands_short_forms_from_the_map():
    from vidgen import tips, winmap

    words = tips.match_words("turn on hags", winmap.BY_ID["display-graphics"])
    assert "hardware accelerated gpu scheduling" in words and "hags" in words


def test_after_the_main_step_only_a_closing_step_is_left():
    from vidgen import tips, winmap

    place = winmap.BY_ID["gaming-mode"] if "gaming-mode" in winmap.BY_ID else winmap.BY_ID["game-mode"]
    page = uiread.parse(json.dumps([{"name": "Game Mode", "kind": "Button", "state": "Off",
                                     "x": 0, "y": 0, "w": 72, "h": 40}]))
    answer = {"search_words": "", "steps": [
        {"guide": "Click Game Mode", "voice": "Turn it on.", "changes_setting": True, "target": "Game Mode"},
        {"guide": "click Graphics", "voice": "Now graphics.", "changes_setting": False, "target": ""},
        {"guide": "click Captures", "voice": "And captures.", "changes_setting": False, "target": ""}]}
    tip = tips.from_answer("turn on game mode", answer, place)
    tips.aim(tip, page, "turn on game mode", place)
    assert [s.guide for s in tip.steps] == ["Click Game Mode", "That's it. Press Finish."]


def test_power_plan_tips_use_the_ready_made_one():
    from vidgen import tips

    assert tips.ready_made("switch to the high performance power plan") is tips.POWER_PLAN
    assert tips.ready_made("change my power plan") is tips.POWER_PLAN
    assert tips.ready_made("turn on night light") is None
