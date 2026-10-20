"""4.6 render speed-ups: background clip fetching, kept voices, the encoder choice, hints."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from vidgen import cache, render, stages
from vidgen.footage import Candidate


def candidate(n):
    return Candidate(provider="Pexels", id=str(n), url=f"https://x/{n}.mp4", width=1080,
                     height=1920, creator="A", about=f"clip {n}")


class Search:
    """Hands out the same results for every query; remembers what was used."""

    def __init__(self, n=4):
        self.results, self.used = [candidate(i) for i in range(n)], set()

    def find(self, query, limit):
        return [c for c in self.results if c.key not in self.used][:limit]

    def mark_used(self, c):
        self.used.add(c.key)

    def explain_failure(self, query):
        return "nothing found"


def plan(visual="city", voice_len=3.0, kind="stock"):
    scene = SimpleNamespace(visual=visual, voice="a line", duration=None, is_local=False,
                            is_ai=kind == "ai")
    return render._ScenePlan(scene, voice_len=voice_len)


def fake_ready(log, fail=(), slow=()):
    def ready(url, *args, **kwargs):
        if url in slow:
            time.sleep(0.2)
        log.append((url, threading.current_thread().name))
        if url in fail:
            raise ConnectionError("broken")
        return {"path": url + ".cut", "original": url, "motion": 1.0, "focus": [0.5, 0.5]}
    return ready


def fetcher(monkeypatch, log, **kw):
    from vidgen import clips

    monkeypatch.setattr(clips, "ready", fake_ready(log, **kw))
    return render._StockFetch(cache.Cache(None, "clips"), (1080, 1920), True, ".",
                              lambda: False, ThreadPoolExecutor(render.DOWNLOADS))


def test_clips_are_queued_in_scene_order_and_a_later_scene_skips_a_taken_clip(monkeypatch):
    log = []
    stock = fetcher(monkeypatch, log)
    plans = [plan(), plan(kind="ai"), plan(voice_len=9.0)]   # the last wants two clips
    stock.start(plans, Search(), {})
    assert list(stock.queued) == ["https://x/0.mp4", "https://x/1.mp4", "https://x/2.mp4"]
    stock.pool.shutdown(wait=True)
    assert sorted(url for url, _name in log) == list(stock.queued)


def test_scenes_get_their_clips_in_order_even_when_downloads_finish_out_of_order(monkeypatch):
    log = []
    stock = fetcher(monkeypatch, log, slow={"https://x/0.mp4"})
    plans, search, credits = [plan(), plan()], Search(), []
    stock.start(plans, search, {})
    ui = SimpleNamespace(log=lambda m: None)
    for i, p in enumerate(plans):
        render._acquire_stock(p, i, search, ui, credits, set(), None, (), lambda: False, stock)
    stock.pool.shutdown(wait=True)
    assert [p.sources[0].original for p in plans] == ["https://x/0.mp4", "https://x/1.mp4"]
    assert [u for u, _ in log][0] == "https://x/1.mp4"   # the second one really finished first
    assert plans[0].sources[0].cut and plans[0].sources[0].focus == (0.5, 0.5)


def test_a_failed_download_falls_back_to_the_next_clip(monkeypatch):
    log = []
    stock = fetcher(monkeypatch, log, fail={"https://x/0.mp4"})
    p, search, messages = plan(), Search(), []
    stock.start([p], search, {})
    render._acquire_stock(p, 0, search, SimpleNamespace(log=messages.append), [], set(), None,
                          (), lambda: False, stock)
    stock.pool.shutdown(wait=True)
    assert p.sources[0].original == "https://x/1.mp4"
    assert any("failed" in m for m in messages)


def test_a_cancel_inside_a_background_download_still_cancels(monkeypatch):
    from vidgen import clips, footage

    def stopped(*args, **kwargs):
        raise footage.Stopped()

    monkeypatch.setattr(clips, "ready", stopped)
    stock = render._StockFetch(cache.Cache(None, "clips"), (1080, 1920), True, ".",
                               lambda: False, ThreadPoolExecutor(1))
    p, search = plan(), Search()
    stock.start([p], search, {})
    with pytest.raises(footage.Stopped):
        render._acquire_stock(p, 0, search, SimpleNamespace(log=lambda m: None), [], set(),
                              None, (), lambda: False, stock)


# --- voices ------------------------------------------------------------------------------

def test_an_unchanged_line_reuses_its_voice_and_a_change_makes_a_new_one(tmp_path, monkeypatch):
    made = []

    def generate(text, path, log, voice, online):
        made.append((text, voice, online))
        wav = path[:-4] + ".wav"
        open(wav, "wb").write(b"RIFF" + text.encode())
        return wav, [(0.0, 0.4, "Hello"), (0.4, 0.3, "there")]

    monkeypatch.setattr(render, "generate_voiceover_timed", generate)
    ui = SimpleNamespace(log=lambda m: None)
    cfg = {"cache_root": str(tmp_path / "cache"), "voice": "en-US-Guy", "app_version": "4.6.0"}
    first = render._voice("Hello there", str(tmp_path / "v0.mp3"), cfg, ui)
    again = render._voice("Hello there", str(tmp_path / "v1.mp3"), cfg, ui)
    assert len(made) == 1 and again[1] == first[1] and open(again[0], "rb").read() == b"RIFFHello there"
    for change in ({"voice": "en-US-Ava"}, {"voice_online": True}, {"app_version": "4.7.0"}):
        render._voice("Hello there", str(tmp_path / "v2.mp3"), dict(cfg, **change), ui)
    render._voice("Hello you", str(tmp_path / "v3.mp3"), cfg, ui)
    assert len(made) == 5


def test_without_a_cache_every_voice_is_made(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(render, "generate_voiceover_timed",
                        lambda text, path, *a: (calls.append(text), (path, []))[1])
    for _ in range(2):
        render._voice("Hi", str(tmp_path / "v.mp3"), {}, SimpleNamespace(log=lambda m: None))
    assert calls == ["Hi", "Hi"]


# --- the encoder ---------------------------------------------------------------------------

def test_quick_sync_is_used_only_when_asked_for_and_working(monkeypatch):
    monkeypatch.setattr(render, "quick_sync_works", lambda: True)
    assert render.encoder({"quick_sync": True}) == render.QSV
    assert render.encoder({}) == render.X264
    monkeypatch.setattr(render, "quick_sync_works", lambda: False)
    assert render.encoder({"quick_sync": True}) == render.X264


def test_a_quick_sync_encode_that_fails_is_made_again_with_x264(monkeypatch):
    used, said = [], []

    def write(video, path, with_audio, bitrate, work_dir, on_progress, stop, codec):
        used.append(codec)
        if codec == render.QSV:
            raise OSError("MFX session failed")

    monkeypatch.setattr(render, "_write", write)
    render._encode(None, "out.mp4", True, "8000k", ".", codec=render.QSV, log=said.append)
    assert used == [render.QSV, render.X264] and "normal encoder" in said[0]


def test_an_x264_failure_is_not_hidden(monkeypatch):
    def write(*args):
        raise OSError("disk full")

    monkeypatch.setattr(render, "_write", write)
    with pytest.raises(OSError):
        render._encode(None, "out.mp4", True, "8000k", ".", codec=render.X264)


def test_a_cancelled_quick_sync_encode_is_not_retried(monkeypatch):
    used = []

    def write(*args):
        used.append(args[-1])
        raise render.RenderCancelled()

    monkeypatch.setattr(render, "_write", write)
    with pytest.raises(render.RenderCancelled):
        render._encode(None, "out.mp4", True, "8000k", ".", codec=render.QSV)
    assert used == [render.QSV]


def test_quick_sync_check_says_no_on_a_pc_without_it(monkeypatch):
    from vidgen import mastering

    def fails(args, stdin=None):
        raise RuntimeError("ffmpeg failed: No device available")

    render.quick_sync_works.cache_clear()
    monkeypatch.setattr(mastering, "run_ffmpeg", fails)
    try:
        assert render.quick_sync_works() is False
    finally:
        render.quick_sync_works.cache_clear()


# --- the hint before a render --------------------------------------------------------------

def test_the_hint_names_what_is_short_and_is_quiet_otherwise():
    assert stages.load_hint(8e9, 0.2) == ""
    assert stages.load_hint(None, None) == ""
    low = stages.load_hint(1.5e9, 0.1)
    assert "1.5 GB memory free" in low and "close other apps" in low
    both = stages.load_hint(2e9, 0.9)
    assert "90 % busy" in both and both.startswith("💡 Only")


def test_cpu_busy_is_a_share_or_unknown():
    busy = stages.cpu_busy(0.05)
    assert busy is None or 0.0 <= busy <= 1.0


# --- a drawn video after the encode changes --------------------------------------------------

def test_a_blackboard_video_still_has_its_scenes_and_length(tmp_path):
    import queue

    from moviepy import VideoFileClip
    from PIL import Image

    for name, colour in (("a", (200, 40, 40)), ("b", (40, 40, 200))):
        Image.new("RGB", (320, 180), colour).save(tmp_path / f"{name}.png")
    script = "\n\n".join(f"Visual: local:{tmp_path / n}.png\nDuration: 0.8s" for n in "ab")
    events = queue.Queue()
    render.render_worker({"script": script, "output_path": str(tmp_path / "out.mp4"),
                          "aspect": "16:9", "resolution": "720p", "captions": False,
                          "style": "Blackboard", "cache_root": str(tmp_path / "cache")},
                         render.UiBridge(events))
    done = []
    while not events.empty():
        kind, payload = events.get_nowait()
        if kind == "finished":
            done.append(payload)
    assert done and done[0]["ok"], done
    with VideoFileClip(done[0]["path"]) as video:
        assert video.duration == pytest.approx(1.6, abs=0.1)
        # The board is dark at the start of each scene, then the chalk appears.
        assert video.get_frame(0.02).mean() < video.get_frame(0.79).mean() + 1


# --- draft render ----------------------------------------------------------------------------

def test_draft_is_small_quick_and_makes_no_new_ai_picture():
    from vidgen.formats import DRAFT, resolve_target

    assert resolve_target("9:16", DRAFT) == (360, 640, "portrait", "1200k")
    p = plan(kind="ai")
    p.scene.ai_prompt = "a cat"
    made = render._ai_picture(p, 0, {"resolution": DRAFT}, "9:16", ".",
                              SimpleNamespace(log=lambda m: None), 1,
                              SimpleNamespace(providers=["Pexels"]), lambda: False)
    assert made is False and "draft" in p.note
