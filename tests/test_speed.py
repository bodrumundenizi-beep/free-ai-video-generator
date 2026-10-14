"""4.1: speed presets, the processor's cores, fewer AI pictures on slow PCs, the reuse option."""
import struct
from types import SimpleNamespace

import pytest

from vidgen import board, imagegen, motion, render
from vidgen.script import parse_script


# --- size by speed and by free memory ------------------------------------------------------------

@pytest.mark.parametrize("size,backend,free,speed,expected", [
    ((576, 1024), "gpu", 16e9, "Best", ((576, 1024), 8)),
    ((576, 1024), "gpu", 16e9, "Balanced", ((480, 848), 6)),
    ((576, 1024), "gpu", 16e9, "Fast", ((384, 672), 4)),
    ((1024, 576), "gpu", 16e9, "Balanced", ((848, 480), 6)),      # turned for landscape
    ((576, 1024), "gpu", 4.2e9, "Best", ((384, 672), 8)),         # memory is the smaller one
    ((576, 1024), "gpu", 4.2e9, "Fast", ((384, 672), 4)),
    ((576, 1024), "cpu", 16e9, "Best", ((384, 672), 4)),          # the processor tops out at Fast
    ((576, 1024), "cpu", 16e9, "Balanced", ((384, 672), 4)),
    ((576, 1024), "gpu", None, "Best", ((576, 1024), 8)),
    ((576, 1024), "gpu", 16e9, "Nonsense", ((576, 1024), 8)),
])
def test_fit_takes_the_smaller_of_the_speed_and_the_memory_tier(size, backend, free, speed,
                                                                 expected):
    assert imagegen.fit(size, backend, free, speed) == expected


def test_cost_grows_with_the_speed_setting():
    assert imagegen.cost("Fast") == 1.0
    assert imagegen.cost("Fast") < imagegen.cost("Balanced") < imagegen.cost("Best")


def test_a_small_picture_is_enlarged_once_to_cover_the_frame(tmp_path):
    from PIL import Image

    small = tmp_path / "small.png"
    Image.new("RGB", (384, 672), (120, 80, 40)).save(small)
    frame = motion.load_image(str(small), 1080, 1920)
    assert frame.shape[:2] == (1920, 1097)    # covers 1080 x 1920 once enlarged
    big = tmp_path / "big.png"
    Image.new("RGB", (1200, 2100)).save(big)
    assert motion.load_image(str(big), 1080, 1920).shape[:2] == (2100, 1200)   # left alone


# --- the processor's cores -------------------------------------------------------------------------

def core_record(efficiency, mask, smt):
    """One SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX record for a core, as Windows lays it out."""
    record = bytearray(48)
    struct.pack_into("<IIBB", record, 0, 0, 48, 1 if smt else 0, efficiency)
    struct.pack_into("<H", record, 30, 1)
    struct.pack_into("<QH", record, 32, mask, 0)
    return bytes(record)


def test_parse_cores_reads_kind_mask_and_hyperthreading():
    other = bytearray(48)
    struct.pack_into("<II", other, 0, 2, 48)   # a cache record, to be skipped
    raw = core_record(1, 0b11, True) + bytes(other) + core_record(0, 0b100, False)
    assert imagegen.parse_cores(raw) == [(1, 0b11, True), (0, 0b100, False)]


def test_performance_mask_and_the_choices_to_time():
    # A Core Ultra 7 155U: 2 performance cores with 2 threads, 8 efficiency, 2 low-power.
    cores = [(2, 0b11 << (2 * i), True) for i in range(2)] + \
            [(1, 1 << (4 + i), False) for i in range(8)] + \
            [(0, 1 << (12 + i), False) for i in range(2)]
    assert imagegen.performance_mask(cores) == 0b1111
    labels = [(label, threads) for label, threads, _mask in imagegen.thread_choices(cores)]
    assert labels == [("program's default", 0), ("performance cores only", 4),
                      ("all 14 threads", 14)]
    one_kind = [(0, 1 << i, False) for i in range(8)]
    assert imagegen.performance_mask(one_kind) == 0
    assert [t for _l, t, _m in imagegen.thread_choices(one_kind)] == [0, 8]


def test_threads_reach_the_program_only_on_the_processor():
    cpu = imagegen.command("sd-cli.exe", "p", (384, 672), "o.png", 1, imagegen.CPU, 4, 6)
    gpu = imagegen.command("sd-cli.exe", "p", (384, 672), "o.png", 1, imagegen.GPU, 4, 6)
    assert cpu[cpu.index("-t") + 1] == "6" and "-t" not in gpu


class FakeRun:
    """A finished program that wrote its picture, or failed with ``code``."""

    def __init__(self, args, code=0):
        self.returncode = code
        if code == 0:
            with open(args[args.index("-o") + 1], "wb") as fh:
                fh.write(b"png")

    def poll(self):
        return self.returncode


def test_benchmark_times_each_choice_and_marks_failures(monkeypatch, tmp_path):
    monkeypatch.setattr(imagegen.time, "sleep", lambda _s: None)
    seen = []

    def launch(args, log_path):
        seen.append(args)
        return FakeRun(args, code=1 if "-t" in args and args[args.index("-t") + 1] == "8"
                           else 0)

    results = imagegen.benchmark([("a", 0, 0), ("b", 8, 0)], str(tmp_path), launch)
    assert [(r[0], r[3] is not None) for r in results] == [("a", True), ("b", False)]
    assert all(a[a.index("-W") + 1] == "256" for a in seen)


def test_graphics_tells_dedicated_from_integrated(monkeypatch):
    monkeypatch.setattr(imagegen, "program_path", lambda: "sd-cli.exe")

    def listing(text):
        return lambda *_a, **_k: SimpleNamespace(stdout=text.encode(), stderr=b"")

    rtx = "ggml_vulkan: 0 = NVIDIA GeForce RTX 4060 (NVIDIA) | uma: 0 | fp16: 1\n"
    intel = "ggml_vulkan: 0 = Intel(R) Graphics (Intel Corporation) | uma: 1 | fp16: 1\n"
    assert imagegen.graphics(listing(rtx)) == "dedicated"
    assert imagegen.graphics(listing(intel)) == "integrated"
    assert imagegen.graphics(listing(intel + rtx.replace("0 =", "1 ="))) == "dedicated"
    assert imagegen.graphics(listing("CPU\tIntel Core\n")) == "none"


# --- fewer AI pictures on a slow PC ----------------------------------------------------------------

def test_limit_ai_keeps_the_first_pictures_and_switches_the_rest_to_stock():
    scenes = parse_script("Visual: ai: a robot holding a clipboard\nVoice: One.\n\n"
                          "Visual: desk\nVoice: Two.\n\n"
                          "Visual: ai: a cat on a laptop\nVoice: Three.\n\n"
                          "Visual: ai: an old plane over a forest\nVoice: Four.")
    kept = board.limit_ai(scenes, 2)
    assert [s.is_ai for s in kept] == [True, False, True, False]
    assert kept[3].visual == "old plane forest" and kept[3].picture == "an old plane over a forest"
    assert board.limit_ai(scenes, 5) == scenes


def test_reuse_makes_a_few_pictures_and_uses_them_again(monkeypatch, tmp_path):
    made, speeds = [], []

    def fake_make(prompt, size, path, look, **kwargs):
        made.append(prompt)
        speeds.append(kwargs["speed"])
        return imagegen.Made(2.0, imagegen.GPU, (384, 672))

    monkeypatch.setattr(imagegen, "make", fake_make)
    ui = SimpleNamespace(lines=[], log=lambda line: ui.lines.append(line),
                         ai_backend=lambda _b: None, ai_speed=lambda *_a: None)
    cfg = {"ai_reuse": 2, "_ai_total": 2, "ai_images_speed": "Fast"}
    sources = []
    for i in range(5):
        plan = render._ScenePlan(parse_script(f"Visual: ai: picture {i}\nVoice: Hi.")[0])
        assert render._ai_picture(plan, i, cfg, "9:16", str(tmp_path), ui, 1,
                                  SimpleNamespace(providers=[]), lambda: False)
        sources.append(plan.sources[0].path)
    assert made == ["picture 0", "picture 1"] and speeds == ["Fast", "Fast"]
    assert sources[2] == sources[0] and sources[3] == sources[1] and sources[4] == sources[0]
    assert any("1 more to make" in line for line in ui.lines)
