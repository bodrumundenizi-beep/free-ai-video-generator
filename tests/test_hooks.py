"""The opening of a tip video: the hook and the "Right now we're going to..." line."""
import random

from vidgen import hooks, script, tips, uiread, winmap


def test_every_kind_has_enough_lines_and_every_page_a_kind():
    assert all(len(lines) >= 5 for lines in hooks.LINES.values())
    assert set(hooks.LINES) == set(hooks.REASON)
    assert set(hooks.KINDS) <= set(winmap.BY_ID)      # no typo in a page id
    assert {hooks.kind_of(p) for p in winmap.PLACES} <= set(hooks.LINES)
    assert hooks.kind_of(winmap.BY_ID["display-graphics"]) == "gaming"
    assert hooks.kind_of(None) == "handy"


def test_the_seconds_are_real_and_rounded():
    assert hooks.seconds(4) == 15 and hooks.seconds(1) == 10
    rng = random.Random(1)
    for _ in range(20):
        template, voice, card = hooks.pick("speed", 20, (), rng)
        assert "{s}" not in voice and ("in 20 seconds" in card) == ("{s}" in template)
    # Over a minute, no "in 75 seconds".
    assert all("{s}" not in hooks.pick("speed", 75, (), rng)[0] for _ in range(20))


def test_recent_hooks_are_not_used_again(tmp_path):
    recent = [line for line, _card in hooks.LINES["eyes"][:-1]]
    assert hooks.pick("eyes", 20, recent)[0] == hooks.LINES["eyes"][-1][0]
    folder = str(tmp_path)
    for line, _card in hooks.LINES["handy"]:
        hooks.remember(folder, line)
    assert hooks.remembered(folder)[0] == hooks.LINES["handy"][-1][0]
    assert len(hooks.remembered(folder)) <= hooks.RECENT


def test_the_explain_line_names_the_real_switch_and_what_happens_to_it():
    place = winmap.BY_ID["display-graphics"]
    tip = tips.fallback("turn on gpu scheduling", [place])
    tip.steps[1].target = "Hardware-accelerated GPU scheduling"   # as aim() leaves a switch step
    tip.steps[1].waits_for_change = True
    assert hooks.explain(tip, "turn on gpu scheduling", place) == (
        "Right now we're going to turn on Hardware-accelerated GPU scheduling, "
        "so games run smoother.")
    night = winmap.BY_ID["nightlight"]
    tip = tips.fallback("turn off night light", [night])
    tip.auto, tip.auto_to = uiread.Control("Night light", "Button", "On", 0, 0, 9, 9), "Off"
    assert hooks.explain(tip, "turn off night light", night).startswith(
        "Right now we're going to turn off Night light, so the screen")
    plain = tips.fallback("open about", [winmap.BY_ID["about"]])
    assert hooks.explain(plain, "open about", winmap.BY_ID["about"]) == \
        "Right now we're going to change About."


def test_a_writer_hook_that_over_promises_is_refused():
    assert hooks.check("Make your old laptop feel quick again.")
    assert not hooks.check("Double your FPS with this trick")
    assert not hooks.check("Get 30% more speed")
    assert not hooks.check(" ".join(["word"] * 15)) and not hooks.check("")


def test_the_opening_scenes_come_first_and_parse():
    tip = tips.fallback("turn on night light", [winmap.BY_ID["nightlight"]])
    text = tips.script(tip, False, ["a.mp4", "b.mp4", "c.mp4"],
                       [("hook.png", "Your eyes will thank you for this one.", "EASY ON THE EYES"),
                        ("explain.png", "Right now we're going to turn on Night light.", "")])
    scenes = script.parse_script(text)
    assert [s.card for s in scenes[:2]] == ["EASY ON THE EYES", None] and len(scenes) == 5
