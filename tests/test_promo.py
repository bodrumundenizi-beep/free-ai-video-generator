"""Promo videos (5.3, piece B): storyboard timing, reading time, tones, colours, the page."""
import json
import os

import pytest
from PIL import Image

from vidgen import promo
from vidgen.promo import browser, colours, plan, tones

DOCS = os.path.join(os.path.dirname(__file__), os.pardir, "docs")


def form(tone="default", aspect="16:9", shots=2, hook="Paste text. Get a finished video."):
    return promo.Promo(
        name="AI Video Studio", hook=hook, what="Free, on your own PC.",
        link="freeaivideostudio.com",
        features=[promo.Feature("Writes the script", "Paste an article"),
                  promo.Feature("Reads it aloud", "An offline voice"),
                  promo.Feature("Edit every scene", "Swap a clip")],
        shots=[os.path.join(DOCS, n) for n in ("home.png", "create.png", "storyboard.png")][:shots],
        result="Your video is ready", claim="Nothing is uploaded.", tone=tone, aspect=aspect)


def test_reading_time_follows_brags_floors():
    assert plan.read_time("") == 0
    assert plan.read_time("Open") == 0.8
    assert plan.read_time("one two three four five six seven eight") == pytest.approx(2.4)
    assert plan.read_time("one two three four") == 1.2


@pytest.mark.parametrize("tone", sorted(tones.TONES))
def test_every_tone_gives_a_readable_15_to_25_second_storyboard(tone):
    scenes = plan.storyboard(form(tone))
    assert 15 <= plan.total(scenes) <= 25.01
    low, high = tones.get(tone).scenes
    assert len(scenes) <= high
    assert scenes[0].kind == "hook" and scenes[-1].kind == "outro"
    assert plan.reading_problems(scenes, tone) == []
    for before, after in zip(scenes, scenes[1:]):
        assert after.start == pytest.approx(before.start + before.duration, abs=0.01)


def test_cards_arrive_one_by_one_after_each_is_readable():
    scenes = plan.storyboard(form())
    cards = next(s for s in scenes if s.kind == "cards")
    times = cards.data["times"]
    assert times == sorted(times)
    for i, item in enumerate(cards.data["items"][:-1]):
        assert times[i + 1] - times[i] >= plan.read_time(item["title"]) - 1e-6


def test_a_long_hook_holds_long_enough_to_read_even_past_25_seconds():
    hook = " ".join(["word"] * 60)
    scenes = plan.storyboard(form(hook=hook))
    assert scenes[0].duration >= plan.read_time(hook)
    assert plan.reading_problems(scenes, "default") == []


def test_no_screenshots_still_makes_a_video():
    scenes = plan.storyboard(form(shots=0))
    assert [s.kind for s in scenes][0] == "hook" and "shot" not in [s.kind for s in scenes]


def test_yc_parody_uses_sentence_case():
    hook = next(s for s in plan.storyboard(form("yc-parody", hook="Paste TEXT")) if s.kind == "hook")
    assert hook.data["text"] == "Paste text"


def test_the_palette_comes_from_the_screenshots(tmp_path):
    shot = Image.new("RGB", (400, 300), (12, 14, 20))
    for x in range(100, 220):
        for y in range(100, 160):
            shot.putpixel((x, y), (40, 120, 255))
    path = str(tmp_path / "shot.png")
    shot.save(path)
    palette = colours.from_screenshots([path])
    assert colours.luminance(colours._rgb(palette.background)) < 0.05
    accent = colours._rgb(palette.accent)
    assert accent[2] > accent[0] and colours.contrast(accent, colours._rgb(palette.background)) >= 3
    assert palette.text == "#FFFFFF"


def test_the_palette_comes_from_css_custom_properties():
    palette = colours.from_css(":root{--bg:#ffffff;--accent:#ff3366;--x:#123}")
    assert palette.background == "#FFFFFF" and palette.text == "#11141C"
    assert colours.contrast(colours._rgb(palette.accent), (255, 255, 255)) >= 3


def test_the_page_carries_the_storyboard_fonts_and_pictures(tmp_path):
    page, scenes = promo.build_page(form(), str(tmp_path))
    html = open(page, encoding="utf-8").read()
    data = json.loads(html.split("const P = ", 1)[1].split(";\n", 1)[0])
    assert data["size"] == [1920, 1080] and len(data["scenes"]) == len(scenes)
    pictures = [s["data"]["image"] for s in data["scenes"] if s["data"].get("image")]
    assert pictures and all(os.path.isfile(tmp_path / p) and not os.path.isabs(p) for p in pictures)
    assert sorted(os.listdir(tmp_path / "fonts")) == ["atkinson-400.woff2", "atkinson-700.woff2",
                                                      "bricolage.woff2"]


def test_no_browser_is_said_plainly(monkeypatch, tmp_path):
    for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.setenv(key, str(tmp_path))
    assert browser.find() is None
    with pytest.raises(browser.BrowserError, match="Edge, Google Chrome or Brave"):
        browser.Browser(100, 100)


needs_browser = pytest.mark.skipif(browser.find() is None, reason="no Chromium browser here")


@needs_browser
@pytest.mark.parametrize("aspect", ["16:9", "9:16", "1:1"])
def test_every_shape_passes_the_checks(aspect):
    assert promo.check(form(aspect=aspect)) == []


@needs_browser
def test_text_too_big_for_its_box_is_caught():
    issues = promo.check(form(hook=" ".join(["overflowing"] * 250)))
    assert any("doesn't fit" in i or "off the frame" in i for i in issues)


@needs_browser
def test_a_short_video_is_drawn_frame_by_frame(tmp_path):
    out = str(tmp_path / "promo.mp4")
    page, scenes = promo.build_page(form(shots=1), str(tmp_path / "page"))
    done = []
    browser.record(page, (320, 180), 0.5, out, fps=10, progress=lambda d, t: done.append(d))
    assert os.path.getsize(out) > 1000 and done[-1] == 5


# --- piece C: sound ---------------------------------------------------------------------
from vidgen.promo import sound  # noqa: E402

ASSETS = os.path.join(os.path.dirname(__file__), os.pardir, "assets")


def test_each_tone_gets_music_that_suits_it():
    music = os.path.join(ASSETS, "music")
    assert os.path.basename(sound.pick_music("cinematic", music)).startswith("dramatic")
    assert os.path.basename(sound.pick_music("deadpan", music)).startswith("calm")
    assert os.path.basename(sound.pick_music("chaotic", music)).startswith("upbeat")
    assert sound.pick_music("default", os.path.join(ASSETS, "nowhere")) is None


def test_cuts_move_to_beats_but_never_cut_reading_short():
    scenes = plan.storyboard(form())
    beats = [round(i * 0.31, 2) for i in range(100)]
    snapped = sound.snap(scenes, beats, "default")
    tone = tones.get("default")
    for before, after in zip(scenes[:-1], snapped[:-1]):
        cut = after.start + after.duration
        assert abs(cut - (before.start + before.duration)) <= sound.CUT_REACH + 1e-6
        assert after.duration >= plan._need(after.kind, after.data, tone) - 1e-6
    cards = next(s for s in snapped if s.kind == "cards")
    for i, item in enumerate(cards.data["items"][:-1]):
        assert cards.data["times"][i + 1] - cards.data["times"][i] >= plan.read_time(item["title"]) - 1e-6
    assert sound.snap(scenes, [], "default") == scenes


def test_effects_follow_the_motion():
    scenes = plan.storyboard(form())
    found = sound.events(scenes, "default")
    names = [n for n, _t in found]
    assert names.count("card") == 3 and names.count("click") == 1 and names[-1] == "bell"
    assert all(0 <= t < plan.total(scenes) for _n, t in found)
    assert not any(n.startswith("key") for n in names)        # only a typing hook clicks keys
    typed = sound.events(plan.storyboard(form("yc-parody")), "yc-parody")
    assert sum(n.startswith("key") for n, _t in typed) >= 5


def test_the_mix_is_as_long_as_the_video_and_at_the_apps_loudness(tmp_path):
    from vidgen import mastering
    scenes = plan.storyboard(form())
    seconds = plan.total(scenes)
    music = sound.pick_music("default", os.path.join(ASSETS, "music"))
    out = sound.mix(scenes, seconds, "default", str(tmp_path / "mix.wav"), music,
                    os.path.join(ASSETS, "sfx"))
    import wave
    with wave.open(out) as fh:
        assert fh.getnchannels() == 2 and abs(fh.getnframes() / fh.getframerate() - seconds) < 0.05
    lufs, peak = mastering.loudness(out)
    assert abs(lufs - sound.TARGET_LUFS) < 1.0 and peak < 0


def test_every_effect_file_is_shipped():
    for files, _level in sound.EFFECTS.values():
        for name in files:
            assert os.path.isfile(os.path.join(ASSETS, "sfx", name)), name


def test_the_effects_slider_scales_only_the_effects(tmp_path):
    import wave
    scenes = plan.storyboard(form(shots=1))
    seconds = plan.total(scenes)
    sfx = os.path.join(ASSETS, "sfx")
    peaks = []
    for volume in (0.0, 1.0):
        out = sound.mix(scenes, seconds, "default", str(tmp_path / f"{volume}.wav"), None, sfx,
                        effects_volume=volume)
        with wave.open(out) as fh:
            frames = fh.readframes(fh.getnframes())
        peaks.append(max(frames))
    assert peaks[0] == 0 and peaks[1] > 0     # no music: off is silence, on is not


# --- piece F2: richer look --------------------------------------------------------------
def test_the_page_gets_every_screenshot_and_how_dark_to_make_each(tmp_path):
    page, _scenes = promo.build_page(form(shots=3), str(tmp_path))
    html = open(page, encoding="utf-8").read()
    data = json.loads(html.split("const P = ", 1)[1].split(";\n", 1)[0])
    assert len(data["shots"]) == 3 and len(data["veils"]) == 3
    assert all(0.45 <= v <= 0.92 for v in data["veils"])


def test_a_bright_screenshot_is_veiled_more_than_a_dark_one(tmp_path):
    dark, bright = str(tmp_path / "dark.png"), str(tmp_path / "bright.png")
    Image.new("RGB", (400, 300), (15, 15, 20)).save(dark)
    Image.new("RGB", (400, 300), (245, 245, 250)).save(bright)
    palette = colours.FALLBACK                      # white text on a dark background
    assert colours.backdrop_veil(bright, palette) > colours.backdrop_veil(dark, palette)
    veil = colours.backdrop_veil(bright, palette)
    mixed = veil * colours.luminance(colours._rgb(palette.background)) + (1 - veil) * colours.luminance((245, 245, 250))
    assert (1 + 0.05) / (mixed + 0.05) >= 4.5 or veil == 0.92


def test_with_no_browser_windows_own_webview2_steps_in(monkeypatch, tmp_path):
    from vidgen.promo import webhost
    for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.setenv(key, str(tmp_path))
    monkeypatch.setattr(webhost, "available", lambda: True)
    assert browser.find() == (browser.WEBVIEW2, "")
    assert browser.find(fallback=False) is None
    command = webhost.command(9999)
    assert command[-3:] == [webhost.ARG, "9999", str(os.getpid())]


def test_the_webview2_runtime_is_found_where_windows_puts_it(monkeypatch, tmp_path):
    from vidgen.promo import webhost
    for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.setenv(key, str(tmp_path))
    assert not webhost.available()
    folder = tmp_path / "Microsoft" / "EdgeWebView" / "Application" / "157.0.1.2"
    folder.mkdir(parents=True)
    (folder / "msedgewebview2.exe").write_bytes(b"")
    assert webhost.available()
