"""Render benchmark: fixed sample videos, timed per stage. A developer tool, not a feature.

Run from source:   python -m vidgen.bench --runs 3 --cases a,b,c,d,e
Installed app:     AIVideoStudio.exe --bench            (writes a report to the Desktop)

Cases: a = 15 s, stock clips only; b = the same with 3 AI pictures; c = one moving picture;
d = case a in the Blackboard style; e = case a twice in a row (the second run shows caching).
The first run downloads the sample clips with your key and keeps them, with the search
results, in %LOCALAPPDATA%\\AIVideoStudio-bench; after that it runs offline. AI and moving
pictures need those downloads installed in the app. Nothing is sent anywhere.
"""

from __future__ import annotations

import argparse
import os
import platform
import queue
import shutil
import statistics
import sys
import time

BENCH_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                         "AIVideoStudio-bench")
APP_CACHE = os.path.join(BENCH_DIR, "app-cache")   # the app's own cache, emptied per run

LINES = (
    ("city traffic at night", "Every night the city keeps moving, long after most people "
                              "have gone to sleep."),
    ("ocean waves on a beach", "Out at the coast, the waves roll in and out the same way "
                               "they have for thousands of years."),
    ("coffee pouring into a cup", "And somewhere a cup of coffee is being poured, ready for "
                                  "the first shift of the morning."),
)


def script(ai_scenes: int = 0) -> str:
    blocks = []
    for i, (visual, voice) in enumerate(LINES):
        shown = f"ai: {visual}, photo, natural light" if i < ai_scenes else visual
        blocks.append(f"Visual: {shown}\nVoice: {voice}")
    return "\n\n".join(blocks)


CASES = {
    "a": dict(name="stock only", script=script(0)),
    "b": dict(name="3 AI pictures", script=script(3)),
    "c": dict(name="1 moving picture", script=script(1), animate="First AI scene"),
    "d": dict(name="Blackboard", script=script(0), style="Blackboard"),
    "e": dict(name="stock only, twice", script=script(0), twice=True),
}


def _offline(cache_dir: str):
    """Search results kept for good and clips downloaded once: later runs need no network."""
    from . import footage

    footage.SearchCache.__init__.__defaults__ = (10 ** 9,)
    clips = os.path.join(cache_dir, "clips")
    os.makedirs(clips, exist_ok=True)
    real = footage.download

    def download(url, dest, session=None, should_stop=None):
        import hashlib

        kept = os.path.join(clips, hashlib.sha1(url.encode()).hexdigest() + ".mp4")
        if not os.path.isfile(kept):
            real(url, kept + ".part", session, should_stop)
            os.replace(kept + ".part", kept)
        shutil.copyfile(kept, dest)
        return dest

    footage.download = download


class _Bridge:
    """Collects what the render reports instead of showing it."""

    def __init__(self):
        from .render import UiBridge

        class Bridge(UiBridge):
            stages = None

            def timings(self, stages):
                self.stages = stages

        self.ui = Bridge(queue.Queue())

    def logs(self) -> list[str]:
        out = []
        while not self.ui._q.empty():
            kind, payload = self.ui._q.get_nowait()
            if kind == "log":
                out.append(payload["msg"])
            elif kind == "finished" and not payload["ok"]:
                out.append(f"FAILED: {payload['message']}")
        return out


def settings_for(case: dict, base: dict, out_dir: str) -> dict:
    from . import animate, drawn

    cfg = dict(base)
    cfg.update(
        script=case["script"], output_path=os.path.join(out_dir, "bench.mp4"), overwrite=True,
        aspect="9:16", resolution="1080p", versions=1, captions=True,
        caption_style="Highlight", sound_effects=True, smart_motion=True, intro_on=False,
        outro_on=False, music_enabled=False, music_path="", target_length=None,
        cache_dir=os.path.join(BENCH_DIR, "search"), cache_root=APP_CACHE, clip_choices={},
        ai_pictures={}, app_version=base.get("_version", "bench"),
        ai_reuse=0, project_dir=None, animate=case.get("animate", ""),
        style=case.get("style", drawn.NORMAL), ai_threads=(0, 0),
        ai_images_speed=base.get("ai_images_speed") or "Fast",
        ai_images_backend=base.get("ai_images_backend") or "",
        ai_gpu=base.get("ai_gpu") or "")
    assert cfg["animate"] in ("", animate.FIRST)
    return cfg


def run_once(cfg: dict, split: bool = False):
    """One render: (Stages, Log lines, compose-only seconds or None)."""
    from . import render

    bridge = _Bridge()
    composed = {}
    if split:   # Python compositing alone, then the real encode: the difference is ffmpeg
        real = render._encode

        def timed(video, *args, **kwargs):
            began = time.perf_counter()
            for _frame in video.iter_frames(fps=30):
                pass
            composed["s"] = time.perf_counter() - began
            return real(video, *args, **kwargs)

        render._encode = timed
    try:
        render.render_worker(dict(cfg), bridge.ui)
    finally:
        if split:
            render._encode = real
    return bridge.ui.stages, bridge.logs(), composed.get("s")


def machine() -> list[str]:
    from . import writer

    lines = [f"{platform.processor() or platform.machine()}, {os.cpu_count()} threads, "
             f"{(writer.total_memory() or 0) / 1e9:.1f} GB RAM "
             f"({(writer.free_memory() or 0) / 1e9:.1f} GB free at start)",
             f"{platform.platform()}, Python {platform.python_version()}"]
    try:
        from . import imagegen

        lines.append(f"Graphics for AI pictures: {imagegen.graphics() or 'none found'}")
    except Exception:  # noqa: BLE001 - only a label
        pass
    return lines


def report(results: dict, version: str, runs: int, split_note: str) -> str:
    out = [f"AI Video Studio {version} render benchmark, {time.strftime('%Y-%m-%d %H:%M')}",
           *machine(), f"Median of {runs} run(s). App CPU = this program only (ffmpeg and the "
                       "AI engines are separate programs; 100 % = one thread busy).",
           "RAM = the app's own memory at the end of the stage.", ""]
    for key, (case, runs_done) in results.items():
        ok = [r for r in runs_done if r[0] is not None]
        out.append(f"== {key}: {case['name']} ({len(ok)} of {len(runs_done)} finished)")
        if not ok:
            out += [f"   {line}" for line in runs_done[-1][1][-5:]]
            out.append("")
            continue
        names = []
        for stages, *_rest in ok:
            names += [n for n, *_rest in stages.table() if n not in names]
        out.append(f"   {'stage':<16}{'seconds':>9}{'app CPU':>9}{'RAM':>9}")
        for name in names:
            if all(next((r[1] for r in st.table(total) if r[0] == name), 0.0) < 0.05
                   for st, _l, _c, total in ok):
                continue   # pacing, deliver and the like: nothing to see
            rows = [next((r for r in st.table(total) if r[0] == name), (name, 0.0, 0.0, 0))
                    for st, _l, _c, total in ok]
            seconds = statistics.median(r[1] for r in rows)
            cpu = statistics.median(r[2] for r in rows)
            memory = max(r[3] for r in rows)
            share = f"{100 * cpu / seconds:.0f} %" if seconds > 0.05 and cpu else "-"
            mem = f"{memory / 1e6:.0f} MB" if memory else "-"
            out.append(f"   {name:<16}{seconds:>9.1f}{share:>9}{mem:>9}")
        totals = [total for *_rest, total in ok]
        out.append(f"   {'total':<16}{statistics.median(totals):>9.1f}   "
                   f"(runs: {', '.join(f'{t:.1f}' for t in totals)})")
        out.append("")
    if split_note:
        out += [split_note, ""]
    return "\n".join(out)


def main(argv=None, version: str = "?", base_settings: dict | None = None,
         to_file: str | None = None) -> str:
    parser = argparse.ArgumentParser(prog="bench")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--cases", default="a,b,c,d,e")
    parser.add_argument("--split", action="store_true",
                        help="also measure Python compositing apart from ffmpeg (case a)")
    args, _unknown = parser.parse_known_args(argv)
    if base_settings is None:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import ai_video_studio as studio

        base_settings, version = studio.load_settings(), studio.APP_VERSION
    base_settings = dict(base_settings, _version=version)
    out_dir = os.path.join(BENCH_DIR, "out")
    os.makedirs(out_dir, exist_ok=True)
    _offline(BENCH_DIR)

    results = {}
    for key in [k.strip() for k in args.cases.split(",") if k.strip() in CASES]:
        case = CASES[key]
        cfg = settings_for(case, base_settings, out_dir)
        done = []
        for _ in range(args.runs):
            shutil.rmtree(APP_CACHE, ignore_errors=True)   # each case starts with nothing kept
            for _again in range(2 if case.get("twice") else 1):
                stages, logs, _c = run_once(cfg)
                total = stages.total() if stages else 0.0
                done.append((stages, logs, None, total) if stages else (None, logs, None, 0))
                print(f"{key}: {total:.1f} s" if stages else f"{key}: failed", flush=True)
        if case.get("twice"):   # report the runs separately: first, then cached
            results["e1"] = (dict(case, name="first run"), done[0::2])
            results["e2"] = (dict(case, name="second run, same project"), done[1::2])
        else:
            results[key] = (case, done)

    split_note = ""
    if args.split:
        shutil.rmtree(APP_CACHE, ignore_errors=True)
        stages, _logs, composed = run_once(settings_for(CASES["a"], base_settings, out_dir),
                                           split=True)
        if stages and composed:
            encode = stages.seconds.get("encode", 0.0)
            split_note = (f"Encode split (case a, one run): Python compositing alone "
                          f"{composed:.1f} s; the real encode {encode:.1f} s, of which about "
                          f"{max(encode - composed, 0):.1f} s is ffmpeg/x264 and waiting.")
    text = report(results, version, args.runs, split_note)
    if to_file:
        with open(to_file, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return text


if __name__ == "__main__":
    print(main(sys.argv[1:]))
