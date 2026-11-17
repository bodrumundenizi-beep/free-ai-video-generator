"""5.5 story looks: the four new looks, their example pictures and hints."""
import os

from gui import base
from vidgen import imagegen

STORY = ("Anime film", "Dark comic", "Painted history", "TV cartoon")


def first(look):
    """A story look is said before the scene too (2 of 11 pictures lost it otherwise)."""
    return imagegen.LOOKS[look] + ": "


def test_story_looks_join_the_old_ones():
    assert list(imagegen.LOOKS)[:3] == ["Realistic photo", "3D render", "Illustration"]
    for look in STORY:
        assert look in imagegen.LOOKS
        assert imagegen.full_prompt("a man at an airport", look).startswith(
            first(look) + "a man at an airport, ")
        assert len(imagegen.LOOKS[look]) < 150   # leaves room for the scene and a character
    assert imagegen.full_prompt("a dog", "Realistic photo").startswith("a dog, ")   # as before


def test_every_look_has_a_hint_and_an_example_picture():
    for look in imagegen.LOOKS:
        assert base.AI_LOOK_HINTS.get(look), look
        name = base.look_picture_name(look)
        assert name == "looks/" + look.lower().replace(" ", "-") + ".jpg"
        assert base.asset_path(name), f"missing assets/{name}"


def test_unknown_look_still_falls_back():
    assert imagegen.full_prompt("a cat", "No such look").endswith(imagegen.LOOKS["Realistic photo"])


HERO = "young man, short black wavy hair, tan skin, navy shirt, grey trousers"


def test_hero_goes_where_the_scene_says():
    line = imagegen.full_prompt("{hero} at the airport holding a suitcase.", "Anime film", HERO)
    assert line.startswith(first("Anime film") + HERO + " at the airport holding a suitcase, ")
    assert line.endswith(imagegen.LOOKS["Anime film"])
    # a scene without {hero} gets no character words
    assert HERO not in imagegen.full_prompt("an empty city at night", "Anime film", HERO)
    # {hero} with no character written: still a picture of someone
    assert imagegen.full_prompt("{hero} in the rain", "Dark comic", "").startswith(
        first("Dark comic") + "a person in the rain")


def test_long_scene_is_cut_never_the_character():
    scene = "{hero} " + "walking through a very long and crowded market full of stalls " * 10
    line = imagegen.full_prompt(scene, "Painted history", HERO)
    assert len(line) <= imagegen.MAX_PROMPT
    assert line.startswith(first("Painted history") + HERO + " walking")
    assert line.endswith(imagegen.LOOKS["Painted history"])
    twice = imagegen.full_prompt("{hero} meets " + "x" * 500 + " and {hero}", "TV cartoon", HERO)
    assert twice.count(HERO) == 2 and len(twice) <= imagegen.MAX_PROMPT


def test_picture_keys_and_seed():
    old = imagegen.picture_key("a dog", "Realistic photo", "9:16")
    assert old == "a dog|Realistic photo|9:16"   # pictures kept before 5.5 are still found
    new = imagegen.picture_key("{hero} cooks", "Anime film", "9:16", HERO, 42)
    assert new != imagegen.picture_key("{hero} cooks", "Anime film", "9:16", HERO, 43)
    assert new != imagegen.picture_key("{hero} cooks", "Anime film", "9:16", "old woman", 42)
    assert new == imagegen.picture_key("{hero} cooks", "Anime film", "9:16", "  " + HERO, 42)
    assert 1 <= imagegen.new_seed() < 2**31


def test_make_uses_the_videos_seed_and_hero(monkeypatch, tmp_path):
    seen = {}

    def stop_here(exe, text, size, out, seed, *a, **k):
        seen.update(text=text, seed=seed)
        raise imagegen.Stopped()

    monkeypatch.setattr(imagegen, "command", stop_here)
    monkeypatch.setattr(imagegen, "fit", lambda size, *a, **k: (size, 8))
    try:
        imagegen.make("{hero} at night", (576, 1024), str(tmp_path / "a.png"), "Dark comic",
                      seed=1234, hero=HERO, check=lambda: (True, ""), verify=lambda *a: [],
                      backend=imagegen.GPU)
    except imagegen.Stopped:
        pass
    assert seen["seed"] == 1234 and seen["text"].startswith(first("Dark comic") + HERO + " at night")


def test_settings_keep_hero_and_seed():
    data = base.default_settings()
    assert data["ai_hero"] == "" and data["ai_seed"] == 0
