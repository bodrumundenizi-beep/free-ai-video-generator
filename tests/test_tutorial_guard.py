"""Guard (5.4 plan B): another program's window over the recorded app never gets into a video."""
from vidgen.tutorial import frames, record

from test_tutorial_record import FakeNamer, FakeRecorder


def test_covered_moments_show_the_last_clean_frame():
    pairs = [[0, 0], [4.0, 4.0]]                     # real speed
    plain = frames.sources(pairs, 4.0, fps=10)
    held = frames.sources(pairs, 4.0, fps=10, covered=[[1.0, 2.0]])
    assert plain[15] == 15 and held[15] == 9         # 1.5 s: the frame just before 1.0 s
    assert held[25] == 25                            # after it: back to the recording


def test_the_recorder_logs_when_the_app_was_covered(tmp_path, monkeypatch):
    monkeypatch.setattr(record, "window_rect", lambda hwnd: (10, 10, 500, 300))
    monkeypatch.setattr(record, "area_for", lambda window, margin: (0, 0, 600, 400))
    ticks = iter([i * 0.1 for i in range(1000)])
    session = record.Session(1, str(tmp_path), recorder=FakeRecorder, read=lambda hwnd: [],
                             namer=FakeNamer(), clock=lambda: next(ticks))
    session.recorder = FakeRecorder(None, None, mouse=False)
    looks = iter([False] * 5 + [True] * 6 + [False] * 5)
    keys = iter([set()] * 15 + [{0x11, 0x10, 0x78}])        # then the stop hotkey
    session._watch(cursor=lambda: (100, 100), down=lambda codes: next(keys),
                   sleep=lambda s: None, popups=lambda hwnd: [], is_covered=lambda: next(looks))
    assert len(session.covers) == 1
    start, end = session.covers[0]
    assert 0.4 <= end - start <= 0.8   # about the 6 covered looks, 0.1 s apart
