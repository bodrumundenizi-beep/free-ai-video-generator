import json
import os
import time

import pytest

from vidgen import footage
from vidgen.footage import (
    FootageSearch, SearchCache, choose_file, parse_pexels, parse_pixabay,
    query_variants, resolve_local,
)

# --- query cleaning -------------------------------------------------------------

def test_query_variants_most_specific_first():
    assert query_variants("frustrated person typing laptop") == [
        "frustrated person typing laptop", "frustrated person", "frustrated"]


def test_stopwords_are_dropped_before_shortening():
    assert query_variants("a view of the ocean") == [
        "a view of the ocean", "view ocean", "view"]


def test_short_queries_are_not_repeated():
    assert query_variants("keyboard") == ["keyboard"]
    assert query_variants("  hard   drive ") == ["hard drive", "hard"]


# --- choosing a rendition -------------------------------------------------------

LANDSCAPE = [
    {"url": "4k", "width": 3840, "height": 2160, "fps": 25},
    {"url": "fhd", "width": 1920, "height": 1080, "fps": 25},
    {"url": "hd", "width": 1280, "height": 720, "fps": 25},
    {"url": "sd", "width": 640, "height": 360, "fps": 25},
]


def test_1080p_landscape_prefers_full_hd_over_4k():
    assert choose_file(LANDSCAPE, 1920, 1080)["url"] == "fhd"


def test_720p_landscape_prefers_hd():
    assert choose_file(LANDSCAPE, 1280, 720)["url"] == "hd"


def test_portrait_from_landscape_needs_the_4k_file():
    # A centre-cropped 1080p landscape file would be blown up 1.8x.
    assert choose_file(LANDSCAPE, 1080, 1920)["url"] == "4k"


def test_portrait_source_covers_exactly():
    files = LANDSCAPE + [{"url": "tall", "width": 1080, "height": 1920}]
    assert choose_file(files, 1080, 1920)["url"] == "tall"


def test_30fps_wins_over_60fps_at_equal_size():
    files = [{"url": "60", "width": 1920, "height": 1080, "fps": 59.94},
             {"url": "30", "width": 1920, "height": 1080, "fps": 29.97}]
    assert choose_file(files, 1920, 1080)["url"] == "30"


def test_falls_back_to_the_largest_when_nothing_covers():
    assert choose_file(LANDSCAPE[1:], 1080, 1920)["url"] == "fhd"


def test_empty_renditions_are_ignored():
    assert choose_file([{"url": "", "width": 0, "height": 0}], 1920, 1080) is None


# --- provider responses -----------------------------------------------------------

PEXELS = {"videos": [{
    "id": 101, "url": "https://www.pexels.com/video/101/", "user": {"name": "Ana"},
    "video_files": [
        {"link": "https://x/hls", "file_type": "video/hls", "width": None, "height": None},
        {"link": "https://x/fhd.mp4", "file_type": "video/mp4", "width": 1920, "height": 1080, "fps": 25},
        {"link": "https://x/4k.mp4", "file_type": "video/mp4", "width": 3840, "height": 2160, "fps": 25},
    ]}]}

PIXABAY = {"hits": [
    {"id": 7, "pageURL": "https://pixabay.com/videos/id-7/", "user": "ben",
     "videos": {"large": {"url": "https://p/l.mp4", "width": 1920, "height": 1080},
                "medium": {"url": "https://p/m.mp4", "width": 1280, "height": 720},
                "small": {"url": "", "width": 0, "height": 0}}},
    {"id": 8, "pageURL": "https://pixabay.com/videos/id-8/", "user": "cy",
     "videos": {"large": {"url": "https://p/tall.mp4", "width": 1080, "height": 1920}}},
]}


def test_parse_pexels_skips_hls_and_picks_full_hd():
    (clip,) = parse_pexels(PEXELS, 1920, 1080)
    assert (clip.provider, clip.id, clip.url) == ("Pexels", "101", "https://x/fhd.mp4")
    assert clip.creator == "Ana" and clip.page_url.endswith("/101/")
    assert clip.credit() == "Pexels · Ana · https://www.pexels.com/video/101/"


def test_parse_pixabay_skips_empty_renditions():
    clips = parse_pixabay(PIXABAY, 1280, 720)
    assert [(c.id, c.url) for c in clips] == [("7", "https://p/m.mp4"), ("8", "https://p/tall.mp4")]


# --- the search itself, against a fake network ------------------------------------

class FakeResponse:
    def __init__(self, status, data=None):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, routes):
        self.routes = routes  # (url, query) -> FakeResponse
        self.calls = []
        self.params = []

    def get(self, url, params=None, headers=None, timeout=None):
        query = params.get("query") or params.get("q")
        self.calls.append((url, query, params.get("orientation")))
        self.params.append(dict(params))
        assert timeout, "every request needs a timeout"
        return self.routes.get((url, query), FakeResponse(200, {"videos": [], "hits": []}))


def make_search(routes, pixabay_key="pk", tmp_path=None):
    return FootageSearch(
        "xk", pixabay_key, 1920, 1080, "landscape", "1080p",
        cache_dir=str(tmp_path) if tmp_path else None, log=lambda m: None,
        session=FakeSession(routes),
    )


def test_pexels_is_asked_for_full_hd_not_4k():
    # "large" is Pexels' 4K minimum, which the old code sent.
    search = make_search({})
    search.find("ocean", 1)
    sizes = {p["size"] for p in search.http.params if "size" in p}
    assert sizes == {"medium"}


def test_720p_asks_pexels_for_hd():
    search = FootageSearch("xk", "", 1280, 720, "landscape", "720p",
                           log=lambda m: None, session=FakeSession({}))
    search.find("ocean", 1)
    assert {p["size"] for p in search.http.params} == {"small"}


def test_queries_go_through_params_so_they_are_encoded():
    search = make_search({})
    search.find("c++ & rust", 1)
    assert search.http.params[0]["query"] == "c++ & rust"


def test_pixabay_is_tried_before_shortening_the_query():
    # Full query: Pexels empty, Pixabay has it - so Pexels is never asked for
    # just "frustrated".
    search = make_search({(footage.PIXABAY_URL, "frustrated person"): FakeResponse(200, PIXABAY)})
    found = search.find("frustrated person", 1)
    assert found[0].provider == "Pixabay"
    assert (footage.PEXELS_URL, "frustrated", "landscape") not in search.http.calls


def test_used_clips_are_skipped():
    search = make_search({(footage.PEXELS_URL, "ocean"): FakeResponse(200, PEXELS)})
    first = search.find("ocean", 1)[0]
    search.mark_used(first)
    assert all(c.key != first.key for c in search.find("ocean", 3))


def test_rejected_key_disables_the_provider_and_falls_back():
    search = make_search({
        (footage.PEXELS_URL, "ocean"): FakeResponse(401),
        (footage.PIXABAY_URL, "ocean"): FakeResponse(200, PIXABAY),
    })
    found = search.find("ocean", 1)
    assert found[0].provider == "Pixabay"
    assert "Pexels" not in search.providers
    assert "rejected" in search.explain_failure("ocean")


def test_no_pixabay_key_means_no_pixabay_calls():
    search = make_search({}, pixabay_key="")
    search.find("ocean", 1)
    assert search.providers == ["Pexels"]
    assert all(url == footage.PEXELS_URL for url, _, _ in search.http.calls)


# --- cache ------------------------------------------------------------------------

def test_cache_round_trip_ignores_the_api_key(tmp_path):
    cache = SearchCache(str(tmp_path))
    cache.put("Pixabay", {"key": "secret", "q": "ocean"}, {"hits": [1]})
    assert cache.get("Pixabay", {"key": "different", "q": "ocean"}) == {"hits": [1]}
    for name in os.listdir(tmp_path):
        assert "secret" not in (tmp_path / name).read_text()


def test_cache_expires(tmp_path):
    cache = SearchCache(str(tmp_path), ttl=60)
    cache.put("Pexels", {"query": "ocean"}, {"videos": []})
    path = cache._path("Pexels", {"query": "ocean"})
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"saved": time.time() - 120, "data": {"videos": []}}, fh)
    assert cache.get("Pexels", {"query": "ocean"}) is None


def test_second_search_is_served_from_cache(tmp_path):
    routes = {(footage.PEXELS_URL, "ocean"): FakeResponse(200, PEXELS)}
    first = make_search(routes, tmp_path=tmp_path)
    first.find("ocean", 1)
    second = make_search(routes, tmp_path=tmp_path)
    second.find("ocean", 1)
    assert second.http.calls == []


# --- local media ------------------------------------------------------------------

def test_resolve_local_relative_to_the_output_folder(tmp_path):
    clip = tmp_path / "b-roll" / "clip.MP4"
    clip.parent.mkdir()
    clip.write_bytes(b"x")
    path, kind = resolve_local("b-roll/clip.MP4", str(tmp_path))
    assert path == str(clip) and kind == "video"


def test_resolve_local_images(tmp_path):
    image = tmp_path / "logo.png"
    image.write_bytes(b"x")
    assert resolve_local(str(image), "unused")[1] == "image"


def test_resolve_local_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        resolve_local("missing.mp4", str(tmp_path))
    (tmp_path / "notes.txt").write_text("x")
    with pytest.raises(ValueError, match="not a supported file"):
        resolve_local("notes.txt", str(tmp_path))



# --- checking a key ----------------------------------------------------------------

class KeySession:
    """Answers every request with one status, and records what was sent."""

    def __init__(self, status=200, error=None):
        self.status, self.error, self.sent = status, error, []

    def get(self, url, params=None, headers=None, timeout=None):
        assert timeout, "every request needs a timeout"
        self.sent.append((url, dict(params), dict(headers)))
        if self.error:
            raise self.error
        return FakeResponse(self.status, {})


def test_check_key_accepts_a_working_pexels_key():
    session = KeySession(200)
    assert footage.check_key("Pexels", "  abc  ", session) == (True, "Key works.")
    url, _params, headers = session.sent[0]
    assert url == footage.PEXELS_URL and headers == {"Authorization": "abc"}


def test_check_key_sends_the_pixabay_key_as_a_parameter():
    session = KeySession(200)
    assert footage.check_key("Pixabay", "pk", session)[0] is True
    url, params, headers = session.sent[0]
    assert url == footage.PIXABAY_URL and params["key"] == "pk" and headers == {}


@pytest.mark.parametrize("status", [400, 401, 403])
def test_check_key_reports_a_rejected_key(status):
    ok, message = footage.check_key("Pexels", "bad", KeySession(status))
    assert ok is False and "rejected" in message


def test_check_key_counts_a_rate_limit_as_a_known_key():
    ok, message = footage.check_key("Pexels", "abc", KeySession(429))
    assert ok is True and "rate limit" in message


def test_check_key_reports_no_connection():
    import requests

    ok, message = footage.check_key("Pexels", "abc", KeySession(error=requests.ConnectionError()))
    assert ok is False and "internet" in message


def test_check_key_needs_a_key_and_makes_no_request_without_one():
    session = KeySession(200)
    assert footage.check_key("Pexels", "   ", session) == (False, "Paste a key first.")
    assert session.sent == []


def test_check_key_rejects_an_unknown_provider():
    with pytest.raises(ValueError):
        footage.check_key("Giphy", "abc", KeySession(200))


# --- stills for the scene preview ---------------------------------------------------

def test_pexels_candidates_carry_the_preview_picture():
    data = {"videos": [{"id": 1, "url": "https://pexels.com/v/1", "image": "https://img/1.jpg",
                        "user": {"name": "Ann"},
                        "video_files": [{"link": "https://v/1.mp4", "width": 1920, "height": 1080}]}]}
    (clip,) = parse_pexels(data, 1920, 1080)
    assert clip.thumb == "https://img/1.jpg"


def test_pixabay_candidates_carry_a_rendition_thumbnail():
    data = {"hits": [{"id": 7, "pageURL": "https://pixabay.com/v/7", "user": "Bo", "videos": {
        "large": {"url": "https://p/l.mp4", "width": 1920, "height": 1080, "thumbnail": "https://t/l.jpg"},
        "tiny": {"url": "https://p/t.mp4", "width": 640, "height": 360, "thumbnail": "https://t/t.jpg"}}}]}
    (clip,) = parse_pixabay(data, 1920, 1080)
    assert clip.thumb in ("https://t/l.jpg", "https://t/t.jpg")


def test_results_without_a_picture_still_parse():
    # Older cached replies, and clips the service has no still for.
    data = {"videos": [{"id": 1, "video_files": [{"link": "https://v/1.mp4", "width": 1920,
                                                   "height": 1080}]}]}
    (clip,) = parse_pexels(data, 1920, 1080)
    assert clip.thumb == ""
    (old,) = parse_pixabay(PIXABAY, 1280, 720)[:1]
    assert old.thumb == ""


# --- stopping a download ------------------------------------------------------------

class ChunkedResponse:
    def __init__(self, chunks):
        self.chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=None):
        yield from self.chunks


class ChunkedSession:
    def __init__(self, chunks):
        self.chunks = chunks

    def get(self, url, stream=None, timeout=None):
        return ChunkedResponse(self.chunks)


def test_download_writes_every_chunk(tmp_path):
    dest = str(tmp_path / "clip.mp4")
    footage.download("https://x/clip.mp4", dest, ChunkedSession([b"ab", b"cd"]))
    assert open(dest, "rb").read() == b"abcd"


def test_download_stops_when_asked(tmp_path):
    dest = str(tmp_path / "clip.mp4")
    asked = []

    def stop_after_two():
        asked.append(1)
        return len(asked) > 2

    with pytest.raises(footage.Stopped):
        footage.download("https://x/clip.mp4", dest, ChunkedSession([b"a"] * 10), stop_after_two)
    assert open(dest, "rb").read() == b"aa"

