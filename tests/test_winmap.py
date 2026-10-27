"""The map of Windows, and turning a tip into something to record."""
import pytest

from vidgen import tips, winmap, writer


def test_every_place_is_complete_and_ids_are_unique():
    assert len(winmap.PLACES) >= 190
    assert len(winmap.BY_ID) == len(winmap.PLACES)
    for place in winmap.PLACES:
        assert place.name and place.words and place.command()
        assert place.opens.split(":")[0] in ("ms-settings", "control", "cpl", "shell", "run", "app")


@pytest.mark.parametrize("tip, place", [
    ("how to turn on night light", "nightlight"),
    ("enable dark mode", "colors"),
    ("change mouse speed", "mouse"),
    ("disable startup apps", "startup-apps"),
    ("switch to high performance power plan", "power-options"),
    ("show file extensions in file explorer", "file-explorer-options"),
    ("turn on clipboard history", "clipboard"),
    ("make text bigger", "text-size"),
    ("turn on game mode", "game-mode"),
    ("how to change Start", "start"),
    ("how to change Troubleshoot", "troubleshoot"),
])
def test_search_finds_the_place_first(tip, place):
    assert winmap.search(tip)[0].id == place


def test_nothing_matches_nonsense():
    assert winmap.search("zzz qqq") == []


def test_security_places_are_marked_and_never_recorded():
    for ident in ("windows-security", "firewall", "uac", "registry-editor", "group-policy"):
        assert winmap.BY_ID[ident].admin
    with pytest.raises(tips.TipRefused):
        tips.fallback("turn off the firewall", [winmap.BY_ID["firewall"]])


def test_commands_open_each_kind_of_place():
    assert winmap.BY_ID["nightlight"].command() == ["explorer", "ms-settings:nightlight"]
    assert winmap.BY_ID["power-options"].command() == ["control", "/name",
                                                        "Microsoft.PowerOptions"]
    assert winmap.BY_ID["network-connections"].command() == ["control", "ncpa.cpl"]
    assert winmap.BY_ID["startup-folder"].command() == ["explorer", "shell:Startup"]
    assert winmap.BY_ID["task-manager"].command()[-1] == "taskmgr"


# --- the Smart writer's answer --------------------------------------------------------------------

def answer(steps=2, search=""):
    return {"search_words": search,
            "steps": [{"guide": f"Click thing {i}", "voice": f"Say {i}",
                       "changes_setting": i == 1} for i in range(steps)]}


def test_the_writer_is_not_asked_for_a_page_only_for_steps():
    schema = writer.tip_schema()
    assert "place" not in schema["properties"] and schema["properties"]["steps"]["maxItems"] == 4


def test_an_answer_becomes_a_tip_that_opens_the_place():
    tip = tips.from_answer("night light", answer(3), winmap.search("night light")[0])
    assert tip.opens == ["explorer", "ms-settings:nightlight"]
    assert [s.voice_off for s in tip.steps] == ["Say 0", "Say 1", "Say 2"]
    # Night light isn't watched: nothing waits for a change, nothing is changed for the user.
    assert not any(s.waits_for_change for s in tip.steps) and tip.read() is None


def test_a_watched_place_ends_its_step_on_a_change_and_puts_it_back():
    tip = tips.from_answer("mouse speed", answer(3), winmap.BY_ID["mouse"])
    assert tip.watch_change and tip.steps[1].waits_for_change
    assert not tip.steps[-1].waits_for_change            # the last step waits for Finish
    assert tip.read is tips.mouse_speed and tip.restore is tips.set_mouse_speed



def test_search_opens_settings_and_starts_with_typing():
    tip = tips.from_answer("odd tip", answer(2, "hibernate"))
    assert tip.opens == ["explorer", "ms-settings:"] and "hibernate" in tip.steps[0].guide


def test_an_admin_place_is_refused_with_a_reason():
    with pytest.raises(tips.TipRefused, match="admin rights"):
        tips.from_answer("drivers", answer(), winmap.BY_ID["device-manager"])


def test_without_the_writer_and_no_match_windows_search_is_used():
    tip = tips.from_answer("zzz", {})
    assert tip.key == "search" and len(tip.steps) == 1 and "zzz" in tip.steps[0].guide


def test_without_the_writer_the_plain_steps_still_make_a_tip():
    tip = tips.fallback("disable startup apps", winmap.search("disable startup apps"))
    assert tip.key == "startup-apps" and len(tip.steps) == 3
    with pytest.raises(tips.TipRefused, match="Nothing in the map"):
        tips.fallback("zzz", [])


def test_mouse_speed_reads_and_puts_back_through_windows(monkeypatch):
    calls = []

    class Api:
        def SystemParametersInfoW(self, action, param, value, flags):
            calls.append((action, flags))
            if action == 0x70:
                value._obj.value = 12
            return 1

    assert tips.mouse_speed(Api()) == 12
    tips.set_mouse_speed(10, Api())
    assert calls == [(0x70, 0), (0x71, 3)]
