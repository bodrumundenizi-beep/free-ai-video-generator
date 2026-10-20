from vidgen.stages import Stages


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_stages_add_up_to_the_total_with_other_for_the_gaps():
    clock = Clock()
    stages = Stages(clock=clock, cpu=lambda: 0.0, memory=lambda: 0)
    clock.now = 1.0                       # a second nothing claimed
    with stages.time("stock"):
        clock.now = 4.0
    stages.add("voices", 2.0)
    with stages.time("encode"):
        clock.now = 9.0
    rows = stages.table(10.0)
    assert [r[0] for r in rows] == ["voices", "stock", "encode", "other"]   # pipeline order
    assert sum(r[1] for r in rows) == 10.0
    assert stages.line(10.0) == "⏱️ voices 2.0 s · stock 3.0 s · encode 5.0 s · total 10.0 s"


def test_a_stage_timed_twice_adds_up_and_keeps_the_highest_memory():
    clock, seen = Clock(), iter([100, 50])
    stages = Stages(clock=clock, cpu=lambda: clock.now / 2, memory=lambda: next(seen))
    for _ in range(2):
        with stages.time("AI pictures"):
            clock.now += 3.0
    name, seconds, cpu, peak = stages.table(6.0)[0]
    assert (name, seconds, cpu, peak) == ("AI pictures", 6.0, 3.0, 100)


def test_a_render_logs_one_timing_line_that_adds_up(tmp_path):
    """The real worker on a tiny silent script: the line's stages sum to its total."""
    import queue
    import re

    from PIL import Image

    from vidgen.render import UiBridge, render_worker

    for name in ("a", "b"):
        Image.new("RGB", (320, 180), (40, 90, 140)).save(tmp_path / f"{name}.png")
    script = "\n\n".join(f'Visual: local: "{tmp_path / n}.png"\nDuration: 0.6' for n in "ab")

    class Bridge(UiBridge):
        stages = None

        def timings(self, stages):
            Bridge.stages = stages

    q = queue.Queue()
    render_worker({"script": script, "output_path": str(tmp_path / "out.mp4"), "aspect": "16:9",
                   "resolution": "720p", "captions": False}, Bridge(q))
    logs = []
    while not q.empty():
        kind, payload = q.get_nowait()
        if kind == "log":
            logs.append(payload["msg"])
    line = next(msg for msg in logs if msg.startswith("⏱️"))
    parts = [float(x) for x in re.findall(r"([\d.]+) s", line)]
    assert abs(sum(parts[:-1]) - parts[-1]) < 0.15      # rounding of each part only
    assert "encode" in line and "footage" not in line
    assert Bridge.stages is not None
