"""Recording the screen on more PCs: the area fits the monitor, and a second way to record."""
from vidgen import screen

LAPTOP = [(0, 0, 1366, 768)]
TWO = [(0, 0, 1920, 1080), (1920, 0, 3840, 1080)]


def test_a_maximized_window_is_cut_to_the_screen_with_even_sizes():
    # A maximized window's frame reaches a few pixels past every edge of a laptop screen.
    assert screen.fit_to_screen((-7, -7, 1381, 752), LAPTOP) == ((0, 0, 1366, 744), 0)
    assert screen.fit_to_screen((100, 50, 801, 601), LAPTOP) == ((100, 50, 800, 600), 0)


def test_the_monitor_holding_most_of_the_window_is_used():
    rect, index = screen.fit_to_screen((1800, 100, 1000, 600), TWO)
    assert index == 1 and rect == (1920, 100, 880, 600)
    assert screen.fit_to_screen((10, 10, 300, 200), []) == ((10, 10, 300, 200), 0)


def test_desktop_duplication_counts_from_its_monitors_corner():
    args = screen.grab_args((2000, 100, 640, 360), method="dda", screens=TWO)
    source = args[args.index("-i") + 1]
    assert args[:2] == ["-f", "lavfi"] and "output_idx=1" in source
    assert "offset_x=80:offset_y=100:video_size=640x360" in source
    assert "-f" in screen.grab_args((0, 0, 640, 360)) and "gdigrab" in screen.grab_args((0, 0, 640, 360))


def test_when_the_older_grab_is_refused_desktop_duplication_records(monkeypatch):
    monkeypatch.setattr(screen, "monitors", lambda: LAPTOP)
    tried = []

    def run(args):
        tried.append("gdigrab" if "gdigrab" in args else "ddagrab")
        if "gdigrab" in args:
            raise RuntimeError("ffmpeg failed: Capture area extends outside window area")

    assert screen.can_record((0, 0, 640, 360), run=run) == ""
    assert tried == ["gdigrab", "ddagrab"] and screen.METHOD == "dda"
    monkeypatch.setattr(screen, "METHOD", "gdi")


def test_when_neither_works_the_message_is_short_and_both_reasons_are_kept(monkeypatch):
    monkeypatch.setattr(screen, "monitors", lambda: LAPTOP)

    def run(args):
        raise RuntimeError("ffmpeg failed: " + ("gdi refused" if "gdigrab" in args else "dda refused"))

    why = screen.can_record((0, 0, 640, 360), run=run)
    assert why.startswith("This PC doesn't allow recording the screen") and "refused" not in why
    assert "gdi refused" in screen.LAST_FAILURE and "dda refused" in screen.LAST_FAILURE
