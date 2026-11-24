"""One slow AI picture must not make a fast PC "slow" (5.5.0: a story kept only 2 pictures)."""
from types import SimpleNamespace

import ai_video_studio as studio
from vidgen import imagegen


def app(kept):
    return SimpleNamespace(ai_fast_seconds=kept, ai_speed_chosen=True,
                           _collect_settings=lambda: {})


def note(one, seconds, speed=imagegen.FAST):
    studio.App.note_picture_time(one, seconds, speed)
    return one.ai_fast_seconds


def test_one_slow_picture_only_raises_the_kept_time_a_little(monkeypatch):
    monkeypatch.setattr(studio, "save_settings", lambda _s: None)
    one = app(6.0)
    assert note(one, 200.0) == 9.0                       # a game on the card: still fast
    assert note(one, 6.0) == 6.0                         # a fast one counts at once
    assert one.ai_fast_seconds <= studio.SLOW_FAST_SECONDS


def test_a_really_slow_pc_is_still_found(monkeypatch):
    monkeypatch.setattr(studio, "save_settings", lambda _s: None)
    assert note(app(0.0), 270.0) == 270.0                # the first picture ever: taken as it is
    one = app(6.0)
    for _ in range(6):                                   # slow picture after slow picture
        note(one, 270.0)
    assert one.ai_fast_seconds > studio.SLOW_FAST_SECONDS


def test_fewer_pictures_follows_the_pc_until_the_user_chooses(monkeypatch):
    one = SimpleNamespace(story_fewer_chosen=None, ai_slow=lambda: True)
    assert studio.App.story_fewer(one) is True            # a laptop: ticked
    one.ai_slow = lambda: False
    assert studio.App.story_fewer(one) is False           # a fast PC: not ticked
    one.story_fewer_chosen = True                         # clicked on: stays on
    assert studio.App.story_fewer(one) is True


def test_fewer_pictures_is_remembered_only_once_chosen(monkeypatch, tmp_path):
    import json

    from gui import base

    monkeypatch.setattr(base, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    assert base.load_settings()["story_fewer"] is None       # never clicked: follows the PC
    for chosen in (False, True):
        (tmp_path / "settings.json").write_text(json.dumps({"story_fewer": chosen}))
        assert base.load_settings()["story_fewer"] is chosen
