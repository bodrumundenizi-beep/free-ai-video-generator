import os
import time

from vidgen import cache


def test_the_same_parts_give_the_same_key_and_any_change_another():
    assert cache.key("voice", "Hi", "en-US", False) == cache.key("voice", "Hi", "en-US", False)
    assert cache.key("voice", "Hi", "en-US", False) != cache.key("voice", "Hi", "en-US", True)
    assert cache.key("clip", "https://x/1") != cache.key("clip", "https://x/2")


def test_put_then_get_and_nothing_kept_when_switched_off(tmp_path):
    source = tmp_path / "a.mp4"
    source.write_bytes(b"clip")
    store = cache.Cache(str(tmp_path / "root"), "clips")
    assert store.get("x.mp4") is None
    kept = store.put("x.mp4", str(source))
    assert kept and open(kept, "rb").read() == b"clip" and source.exists()
    assert store.get("x.mp4") == kept
    store.put_json("x.json", {"motion": 2.5})
    assert store.get_json("x.json") == {"motion": 2.5}

    off = cache.Cache(None, "clips")
    assert off.put("x.mp4", str(source)) is None and off.get("x.mp4") is None
    assert off.get_json("x.json") is None


def test_trim_removes_the_files_used_longest_ago_first(tmp_path):
    root = tmp_path / "root"
    for i, name in enumerate(("old", "middle", "new")):
        path = root / "clips" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 100)
        os.utime(path, (1000 + i, 1000 + i))
    assert cache.trim(str(root), limit=150) == 200
    assert sorted(os.listdir(root / "clips")) == ["new"]


def test_a_file_used_again_is_kept_over_a_newer_one(tmp_path):
    store = cache.Cache(str(tmp_path), "voices")
    src = tmp_path / "v.wav"
    src.write_bytes(b"v" * 100)
    first, second = store.put("first.wav", str(src)), store.put("second.wav", str(src))
    os.utime(first, (1000, 1000))
    os.utime(second, (2000, 2000))
    store.get("first.wav")                     # used now
    cache.trim(str(tmp_path), limit=150)
    assert os.path.exists(first) and not os.path.exists(second)


def test_old_part_files_from_a_crash_are_removed(tmp_path):
    part = tmp_path / "clips" / "x.mp4.123.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"half")
    os.utime(part, (time.time() - 7200,) * 2)
    cache.trim(str(tmp_path))
    assert not part.exists()


def test_clear_empties_every_cache_and_says_how_much(tmp_path):
    for kind in cache.KINDS:
        (tmp_path / kind).mkdir()
        (tmp_path / kind / "f").write_bytes(b"x" * 10)
    (tmp_path / "settings.json").write_text("{}")   # not a cache: left alone
    assert cache.clear(str(tmp_path)) == 30
    assert cache.size(str(tmp_path)) == 0 and (tmp_path / "settings.json").exists()
