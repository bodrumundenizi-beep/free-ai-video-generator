"""4.9.2 piece 2: long videos are made in chunks, joined, with the sound mixed once."""

import queue

import numpy as np
import pytest
from PIL import Image

pytest.importorskip("moviepy")

from test_render_speed import Search, fetcher, plan  # noqa: E402
from vidgen import mastering, render  # noqa: E402


# --- where the chunks are cut ---------------------------------------------------------------

def test_chunks_cut_where_a_scene_starts_on_a_frame_and_cover_the_whole_video():
    starts = [0.0, 1.0, 2.03, 3.5, 4.0, 5.21]   # five scenes; the last number is the total
    chunks = render.chunk_ranges(starts, 2)
    assert chunks[0][0] == 0.0 and chunks[-1][1] == pytest.approx(5.2)   # 5.21 on a frame
    assert [round(b * render.FPS, 6) % 1 for _a, b in chunks] == [0, 0, 0]   # whole frames
    assert chunks[0][1] == pytest.approx(round(2.03 * 30) / 30)   # scene 3's start
    assert all(a == b for (_x, a), (b, _y) in zip(chunks, chunks[1:]))   # no gap, no overlap
    assert render.chunk_ranges([0.0, 1.0, 2.0], 20) == [(0.0, 2.0)]   # short: one chunk


def test_150_scenes_make_eight_chunks():
    starts = [i * 4.0 for i in range(151)]   # 150 scenes of 4 s: 10 minutes
    chunks = render.chunk_ranges(starts, render.CHUNK_SCENES)
    assert len(chunks) == 8 and chunks[-1][1] == 600.0


def test_the_concat_list_escapes_quotes_and_uses_forward_slashes():
    assert render.concat_list([r"C:\a b\it's.mp4", "D:/x.mp4"]) == (
        "file 'C:/a b/it'\\''s.mp4'\nfile 'D:/x.mp4'\n")


# --- stock clips a chunk ahead --------------------------------------------------------------

def test_stock_is_searched_only_a_chunk_ahead(monkeypatch):
    log = []
    stock = fetcher(monkeypatch, log)
    plans = [plan() for _ in range(8)]
    stock.start(plans, Search(n=20), {}, ahead=3)
    assert len(stock.queued) == 3   # not all eight scenes at the start
    stock.fill(6)
    assert len(stock.queued) == 6
    stock.fill(6)   # asking again queues nothing twice
    assert len(stock.queued) == 6
    stock.pool.shutdown(wait=True)


# --- a real render forced into chunks -------------------------------------------------------

@pytest.fixture
def scenes(tmp_path):
    blocks = []
    colours = [(220, 40, 40), (40, 200, 60), (40, 60, 220), (230, 200, 40)]
    for n, colour in enumerate(colours):
        path = tmp_path / f"{n}.png"
        # Smooth pictures: encoding noise stays small, so a join a frame off would show.
        shade = np.linspace(0.6, 1.0, 320)[None, :, None] * np.array(colour)[None, None, :]
        Image.fromarray(np.repeat(shade, 180, axis=0).astype(np.uint8)).save(path)
        blocks.append(f"Visual: local:{path}\nDuration: 0.7s" + ("\nCard: Win + V" if n == 2
                                                                 else ""))
    music = tmp_path / "music.wav"
    mastering.run_ffmpeg(["-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=6",
                          str(music)])
    return dict(script="\n\n".join(blocks), aspect="16:9", resolution="720p",
                captions=False, music_enabled=True, music_path=str(music), cache_root=None,
                api_key="", pixabay_key="")


def _render(cfg):
    events = queue.Queue()
    render.render_worker(cfg, render.UiBridge(events))
    out = []
    while not events.empty():
        out.append(events.get_nowait())
    done = [p for k, p in out if k == "finished"]
    assert done and done[0]["ok"], out
    return done[0]["path"], out


def test_a_render_in_chunks_is_as_long_as_one_pass_and_has_sound_to_the_end(scenes, tmp_path):
    from moviepy import VideoFileClip

    single, _ = _render(dict(scenes, output_path=str(tmp_path / "one.mp4")))
    chunked, events = _render(dict(scenes, output_path=str(tmp_path / "parts.mp4"),
                                   chunk_scenes=2))
    logs = [p["msg"] for k, p in events if k == "log"]
    assert any("Part 2 of 2" in line for line in logs)
    with VideoFileClip(single) as one, VideoFileClip(chunked) as parts:
        assert parts.duration == pytest.approx(one.duration, abs=0.05)
        assert parts.duration == pytest.approx(2.8, abs=0.1)
        sound = parts.audio.subclipped(parts.duration - 0.4, parts.duration - 0.1)
        assert np.abs(sound.to_soundarray(fps=8000)).max() > 0.01   # music to the end
        # The same picture either side of the join as in one pass.
        for t in (0.7, 1.32, 1.39, 1.45, 1.5, 2.1):   # the join is at 1.4 s, in a crossfade
            assert np.abs(parts.get_frame(t).astype(int) - one.get_frame(t).astype(int)
                          ).mean() < 6, t


# --- shots opened only while they show ------------------------------------------------------

def test_a_lazy_layer_opens_when_it_shows_and_closes_when_it_is_over():
    from moviepy import ColorClip

    from vidgen import compose

    opened, closed = [], []

    class Reader:
        def close(self):
            closed.append(self)

    def make(keep):
        reader = keep(Reader())   # keep hands back what it registers
        opened.append(reader)
        return ColorClip((8, 8), (200, 0, 0), duration=1.0)

    layer = compose.Lazy(make, 1.0, 2.0)
    video = compose.layered([layer], (8, 8), 3.0)
    assert video.get_frame(0.5).max() == 0 and not opened   # not open before it shows
    assert video.get_frame(1.5)[0, 0, 0] == 200 and len(opened) == 1
    video.get_frame(2.5)
    assert closed == opened   # closed once it is over


def test_a_short_video_opens_each_clip_only_while_it_shows(tmp_path, monkeypatch):
    """4.9.2: short videos too; before, every shot's reader (~370 MB at 1080p) was open."""
    import moviepy

    blocks = []
    for n in range(5):
        path = tmp_path / f"{n}.mp4"
        mastering.run_ffmpeg(["-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30",
                              "-t", "2", "-pix_fmt", "yuv420p", str(path)])
        blocks.append(f"Visual: local:{path}\nDuration: 0.6s")
    real, open_now, most = moviepy.VideoFileClip, [0], [0]

    class Counted(real):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            open_now[0] += 1
            most[0] = max(most[0], open_now[0])

        def close(self):
            if getattr(self, "_counted_closed", False) is False:
                self._counted_closed = True
                open_now[0] -= 1
            super().close()

    monkeypatch.setattr(moviepy, "VideoFileClip", Counted)
    path, _ = _render(dict(script="\n\n".join(blocks), aspect="16:9", resolution="720p",
                           captions=False, output_path=str(tmp_path / "short.mp4")))
    assert most[0] <= 2   # the shot showing, and the next during the crossfade
