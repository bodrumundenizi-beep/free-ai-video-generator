"""The render worker: script in, MP4 out, on a background thread.

The worker never touches a widget. Everything it reports goes through UiBridge,
a queue the window drains on its own thread.

Order of work, cheapest check first so mistakes surface before any download:

    1. parse the script; check every local: file exists
    2. voiceovers  - each voice decides its scene's length
    3. footage     - search, download, credit
    4. timeline    - where every shot sits, with crossfades centred on cuts
    5. pictures    - framed, zoomed, faded, composited, captions on top
    6. soundtrack  - voices at their scene starts, music ducked beneath them
    7. encode      - into the temp folder, then moved into place (see paths.py)

Steps 3 to 7 run once per version when two are asked for; the voices are made
once and shared. Cancel is checked between steps and on every encoded frame.
"""

from __future__ import annotations

import dataclasses
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field

from . import footage, paths
from .formats import resolve_target
from .paths import next_free_path  # noqa: F401 - re-exported for the window and tests
from .script import Scene, parse_script
from .timeline import boundaries, shot_spans
from .voice import generate_voiceover_timed

LONG_SCENE = 6.0  # a voice longer than this gets two clips, cut at the middle
MIN_SCENE = 0.5
DEFAULT_PADDING = 0.25
SPARE_CANDIDATES = 2  # extra search results to fall back on if a download fails
RANKED = 6  # search results compared by description (measured 2026-10-16: 6 of 7 bad fixed)

# Scratch space for a render.  Never the working directory: once installed, that
# is the (possibly read-only) install folder, not somewhere we may write.
TEMP_ROOT = os.path.join(tempfile.gettempdir(), "AIVideoStudio")


def new_work_dir() -> str:
    """A private directory for one render's intermediate files."""
    os.makedirs(TEMP_ROOT, exist_ok=True)
    return tempfile.mkdtemp(prefix="run_", dir=TEMP_ROOT)


def sweep_old_work_dirs(max_age_hours: int = 24) -> None:
    """Remove scratch dirs a crashed render left behind."""
    cutoff = time.time() - max_age_hours * 3600
    try:
        names = os.listdir(TEMP_ROOT)
    except OSError:
        return
    for name in names:
        path = os.path.join(TEMP_ROOT, name)
        try:
            if name.startswith("run_") and os.path.getmtime(path) < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


class UiBridge:
    """Thread-safe hand-off from the worker thread to the Tk main loop."""

    def __init__(self, q):
        self._q = q

    def _post(self, kind, **payload):
        self._q.put((kind, payload))

    def log(self, msg):
        self._post("log", msg=msg)

    def status(self, msg):
        self._post("status", msg=msg)

    def progress(self, value):
        self._post("progress", value=value)

    def spinner(self, on):
        self._post("spinner", on=on)

    def busy(self, on):
        self._post("busy", on=on)

    def ai_backend(self, backend):
        """What AI pictures turned out to need on this PC, for the window to remember."""
        self._post("ai_backend", backend=backend)

    def ai_speed(self, seconds, speed):
        """How long a picture just took at ``speed``: the window's estimates learn from it."""
        self._post("ai_speed", seconds=seconds, speed=speed)

    def finished(self, ok, title, message, path=None, paths=None):
        """``paths`` are the saved videos (one per version), so the window can
        show them; ``path`` is the first."""
        self._post("finished", ok=ok, title=title, message=message, path=path,
                   paths=list(paths or ([path] if path else [])))

    def cancelled(self):
        self._post("cancelled")


@dataclass
class _Source:
    path: str
    kind: str  # "video" or "image"


@dataclass
class _ScenePlan:
    scene: object
    voice: object = None  # AudioFileClip, or None for a silent scene
    voice_path: str = ""  # the file behind ``voice``
    trim: tuple = (0.0, 0.0)  # (start, end) of the speech inside that file
    voice_len: float = 0.0
    words: list = field(default_factory=list)  # (start, duration, text) within the voice
    duration: float = 0.0
    parts: int = 1
    sources: list = field(default_factory=list)
    # What the scene shows, for the Log's line per scene: a name ("stock clip", "still AI
    # picture"...) and, after a fallback, why ("moving picture failed: ...").
    kind: str = ""
    note: str = ""

    @property
    def flexible(self) -> bool:
        """Whether a target length may change this scene's pause and voice speed."""
        return self.scene.duration is None and self.scene.padding is None and self.voice is not None


class _Render:
    """Every file-backed clip one render opens, so all of them get closed.

    MoviePy readers hold their files open. On Windows an open file can't be
    deleted (WinError 32), so a clip left open - say, by a render that failed
    halfway - would stop the temp folder being cleaned up.
    """

    def __init__(self):
        self.resources = []

    def keep(self, clip):
        self.resources.append(clip)
        return clip

    def close_all(self):
        for clip in reversed(self.resources):
            try:
                clip.close()
            except Exception:
                pass
        self.resources.clear()


def _as_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class RenderError(Exception):
    """A failure with a plain message for the user and the raw cause for the Log."""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail


class RenderCancelled(Exception):
    """The user pressed Cancel. Not a failure: nothing is reported as one."""


def _one_line(error, limit: int = 600) -> str:
    text = " ".join(str(error).split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _stopper(cfg):
    """A function that says whether Cancel was pressed (``cfg["cancel"]`` is a
    threading.Event, or absent)."""
    event = cfg.get("cancel")
    return event.is_set if event is not None else (lambda: False)


def _check(stop):
    if stop():
        raise RenderCancelled()


def render_worker(cfg: dict, ui: UiBridge):
    """Render ``cfg["script"]`` to ``cfg["output_path"]``, reporting through ``ui``."""
    # Allocated before the try so the finally can always clean it up.
    work_dir = new_work_dir()
    job = _Render()
    try:
        _render(cfg, ui, work_dir, job)
    except (RenderCancelled, footage.Stopped):
        ui.spinner(False)
        ui.progress(0.0)
        ui.log("⏹️ Render cancelled.")
        ui.cancelled()
    except Exception as e:  # noqa: BLE001 - surfaced to the user
        ui.spinner(False)
        ui.progress(0.0)
        ui.log(f"❌ ERROR: {str(e)}")
        detail = getattr(e, "detail", "")
        if detail:
            # The raw tool output helps a bug report; it doesn't belong in the popup.
            ui.log(f"🎞️ Technical details: {detail}")
        ui.finished(False, "Render failed", str(e))
    finally:
        # Close before deleting: an open reader would keep its file locked.
        job.close_all()
        shutil.rmtree(work_dir, ignore_errors=True)
        ui.busy(False)


BOOKEND = 2.0  # seconds of the channel-name card before the video, and the end card after


def with_bookends(scenes, cfg, work_dir, size):
    """The scenes with the intro and end cards from Settings added, if they are on.

    They are scenes of their own, on a plain dark background, so they need no search
    and no AI picture, and the script is left as the user wrote it. Choices made in the
    Storyboard are by scene number, so they move up one when an intro comes first.
    """
    intro = (cfg.get("intro_text") or "").strip() if cfg.get("intro_on") else ""
    outro = (cfg.get("outro_text") or "").strip() if cfg.get("outro_on") else ""
    if not (intro or outro):
        return scenes, cfg
    from PIL import Image

    background = os.path.join(work_dir, "bookend.png")
    shade = Image.linear_gradient("L").resize(size).point(lambda v: 14 + v * 30 // 255)
    Image.merge("RGB", (shade, shade, shade.point(lambda v: v + 12))).save(background)

    def card(text):
        return Scene(visual=f'local: "{background}"', duration=BOOKEND, card=text)

    scenes = ([card(intro)] if intro else []) + list(scenes) + ([card(outro)] if outro else [])
    if intro:
        cfg = dict(cfg, **{key: {i + 1: v for i, v in (cfg.get(key) or {}).items()}
                           for key in ("ai_pictures", "clip_choices")})
    return scenes, cfg


def _render(cfg, ui, work_dir, job):
    save_path = (cfg.get("output_path") or "").strip()
    script_text = (cfg.get("script") or "").strip()
    if not save_path:
        raise Exception("Save location cannot be blank.")
    if not script_text:
        raise Exception("Script cannot be blank.")

    stop = _stopper(cfg)
    versions = 2 if str(cfg.get("versions", 1)) == "2" else 1
    ratio = cfg.get("aspect", "9:16")
    quality = cfg.get("resolution", "1080p")
    target_w, target_h, orientation, bitrate = resolve_target(ratio, quality)
    default_padding = _as_float(cfg.get("padding"), DEFAULT_PADDING)

    # 1. Script, and every local file, before anything slow happens.
    warnings = []
    scenes = parse_script(script_text, warnings)
    for warning in warnings:
        ui.log(f"⚠️ {warning}")
    scenes, cfg = with_bookends(scenes, cfg, work_dir, resolve_target(ratio, quality)[:2])

    save_path = os.path.abspath(save_path)
    # Relative local: files are looked up beside where the user asked to save,
    # even if the video itself ends up somewhere else.
    out_dir = os.path.dirname(save_path)

    # Find out now, not after ten minutes of rendering, whether the folder
    # takes files. Raises a plain SaveError if nowhere does.
    fallbacks = paths.safe_folders()
    usable, moved = paths.usable_output(save_path, fallbacks)
    if moved:
        ui.log(f"⚠️ Couldn't save to {out_dir} - using {os.path.dirname(usable)} instead.")
        save_path = usable
    if moved or not cfg.get("overwrite"):
        free_path = next_free_path(save_path)
        if free_path != save_path:
            ui.log(f"💾 {os.path.basename(save_path)} already exists - saving as "
                   f"{os.path.basename(free_path)}")
            save_path = free_path

    plans = [_ScenePlan(scene) for scene in scenes]
    for plan in plans:
        if plan.scene.is_local:
            try:
                path, kind = footage.resolve_local(plan.scene.local_path, out_dir)
            except (ValueError, OSError) as exc:
                raise Exception(f"Scene at line {plan.scene.line}: {exc}") from exc
            plan.sources = [_Source(path, kind)]

    search = footage.FootageSearch(
        cfg.get("api_key"), cfg.get("pixabay_key"), target_w, target_h,
        orientation, quality, cfg.get("cache_dir"), ui.log,
    )
    # No stock key, but AI images are here: draw every scene. The app then needs no key.
    if not search.providers:
        from . import imagegen

        if imagegen.ready()[0]:
            for plan in plans:
                if not plan.scene.is_local and not plan.scene.is_ai:
                    plan.scene = _ai_instead(plan.scene)

    # AI pictures are optional. Without them those scenes use stock footage instead.
    wants_ai = [p for p in plans if p.scene.is_ai]
    if wants_ai:
        from . import imagegen

        ok, why = imagegen.ready()
        if not ok:
            if not search.providers:
                raise Exception(f"The scene at line {wants_ai[0].scene.line} asks for an AI "
                                f"picture. {why}")
            ui.log(f"⚠️ {why} Using stock footage for those scenes.")
            for plan in wants_ai:
                plan.scene = _stock_instead(plan.scene)

    ready = cfg.get("ai_pictures") or {}
    cfg["_ai_total"] = sum(1 for i, p in enumerate(plans) if p.scene.is_ai and i not in ready)
    if cfg.get("ai_reuse"):
        cfg["_ai_total"] = min(cfg["_ai_total"], cfg["ai_reuse"])
    needs_stock = [p.scene for p in plans if not p.scene.is_local and not p.scene.is_ai]
    if needs_stock and not search.providers:
        raise Exception(
            "Add a stock footage key (Pexels or Pixabay) in Settings "
            f"- the scene at line {needs_stock[0].line} needs stock footage."
        )

    # MoviePy only now, so a script error is reported instantly.
    from moviepy import AudioFileClip

    from . import audio

    ui.log(
        f"🚀 Starting {ratio} render at {target_w}x{target_h} "
        f"({quality}, {bitrate})..."
    )
    ui.status(f"Rendering {target_w}x{target_h}...")
    total_scenes = len(plans)
    spent = {"voices": 0.0, "footage": 0.0, "encode": 0.0}
    began = time.perf_counter()

    # 2. Voiceovers. Each one decides its scene's length.
    for i, plan in enumerate(plans):
        _check(stop)
        scene = plan.scene
        ui.log(f"🎬 Scene {i + 1} of {total_scenes}")
        if scene.voice:
            voice_path = os.path.join(work_dir, f"voice_{i}.mp3")
            # Studio voices come back as a mastered .wav, classic ones as the .mp3.
            voice_path, words = generate_voiceover_timed(
                scene.voice, voice_path, ui.log, cfg.get("voice"),
                bool(cfg.get("voice_online")))
            # The file is registered for closing; the trimmed copy shares its reader.
            raw = job.keep(AudioFileClip(voice_path))
            lead, tail = audio.speech_window(raw)
            plan.voice = raw if (lead, tail) == (0.0, raw.duration) else raw.subclipped(lead, tail)
            plan.voice_path, plan.trim = voice_path, (lead, tail)
            plan.voice_len = float(plan.voice.duration)
            # Word timings follow the trim, so captions stay on the words.
            plan.words = [(max(start - lead, 0.0), length, text)
                          for start, length, text in words]
        ui.progress(VOICE_SHARE * (i + 1) / total_scenes)

    paddings = _pace(plans, cfg.get("target_length"), default_padding, work_dir, job, ui)

    for plan in plans:
        scene = plan.scene
        if scene.duration is None:
            plan.duration = plan.voice_len + paddings[id(plan)]
        else:
            plan.duration = scene.duration
            if scene.padding is not None:
                ui.log(f"⚠️ Line {scene.line}: Padding: is ignored when Duration: is set.")
            if plan.voice_len > plan.duration + 0.05:
                ui.log(
                    f"⚠️ Line {scene.line}: the voice runs {plan.voice_len:.1f}s but "
                    f"Duration: is {plan.duration:g}s, so it will be cut short."
                )
        plan.duration = max(plan.duration, MIN_SCENE)
    spent["voices"] = time.perf_counter() - began

    # 3-7. Once per version: footage, timeline, pictures, sound, encode.
    choices = cfg.get("clip_choices") or {}
    saved = []
    for version in range(1, versions + 1):
        _check(stop)
        if versions > 1:
            ui.log(f"🎞️ Version {version} of {versions}")
        wanted = paths.version_path(save_path, version)
        if version > 1:
            wanted = next_free_path(wanted)
        share = (VOICE_SHARE + (1 - VOICE_SHARE) * (version - 1) / versions,
                 (1 - VOICE_SHARE) / versions)
        saved.append(_make_version(
            cfg, ui, work_dir, job, plans, search, choices, version, wanted, fallbacks,
            (target_w, target_h, ratio, bitrate), share, spent, stop))

    total_time = time.perf_counter() - began
    # Where the time went: the first thing asked when a render "feels slow".
    ui.log(f"⏱️ Voices {spent['voices']:.1f} s · footage {spent['footage']:.1f} s · "
           f"encode {spent['encode']:.1f} s · total {total_time:.1f} s")
    ui.log("🧹 Releasing memory and deleting temp files...")
    ui.progress(1.0)
    listing = "\n".join(saved)
    ui.log(f"✅ DONE! {target_w}x{target_h} video saved to:\n{listing}")
    made = "video" if len(saved) == 1 else f"{len(saved)} versions"
    ui.finished(
        True,
        "Render complete",
        f"{target_w}x{target_h} {made} rendered successfully.\n\nSaved at:\n{listing}",
        path=saved[0], paths=saved,
    )


VOICE_SHARE = 0.25  # of the progress bar; the rest is split between the versions


def _pace(plans, target, default_padding, work_dir, job, ui):
    """The pause after each scene, by plan id - fitting a target length if asked.

    With a target, the pauses of the flexible scenes are adjusted first; if
    that is not enough their voices are sped up or slowed down (pitch kept),
    and the word timings with them, so captions stay on the words.
    """
    paddings = {
        id(p): (p.scene.padding if p.scene.padding is not None else default_padding)
        for p in plans
    }
    target = _as_float(target, None)
    flexible = [p for p in plans if p.flexible]
    if not target or not flexible:
        return paddings

    from . import pacing

    def fixed_seconds():
        return sum(
            p.scene.duration if p.scene.duration is not None
            else p.voice_len + paddings[id(p)]
            for p in plans if not p.flexible)

    voice_seconds = sum(p.voice_len for p in flexible)
    plan_fit = pacing.fit(voice_seconds, len(flexible), fixed_seconds(), target)

    if abs(plan_fit.tempo - 1.0) > 0.005:
        try:
            for index, plan in enumerate(flexible):
                _retime(plan, plan_fit.tempo, os.path.join(work_dir, f"paced_{index}.wav"), job)
        except Exception as exc:  # noqa: BLE001 - the natural pace is a fine fallback
            ui.log(f"⚠️ Couldn't change the voice speed ({_one_line(exc, 120)}); "
                   "keeping the natural pace.")
        voice_seconds = sum(p.voice_len for p in flexible)

    padding = pacing.settle_padding(voice_seconds, len(flexible), fixed_seconds(), target)
    for plan in flexible:
        paddings[id(plan)] = padding
    achieved = fixed_seconds() + voice_seconds + len(flexible) * padding

    change = (plan_fit.tempo - 1.0) * 100
    speed = "" if abs(change) < 0.5 else f", voice speed {change:+.0f}%"
    if abs(achieved - target) <= pacing.CLOSE_ENOUGH:
        ui.log(f"🎯 Length: fitted to {target:g} s (pauses {padding:.2f} s{speed}).")
    else:
        words = sum(len(p.scene.voice.split()) for p in flexible)
        count = pacing.words_to_change(achieved, target, words, voice_seconds)
        advice = (f"cut about {count} words" if count > 0 else f"add about {-count} words")
        ui.log(f"⚠️ Closest to {target:g} s is {achieved:.0f} s - {advice} to reach it.")
    return paddings


def _retime(plan, tempo, dest, job):
    """Replace a scene's voice with one ``tempo`` times as fast, same pitch."""
    from moviepy import AudioFileClip

    from . import mastering

    lead, tail = plan.trim
    mastering.run_ffmpeg([
        "-y", "-ss", f"{lead:.3f}", "-to", f"{tail:.3f}", "-i", plan.voice_path,
        "-filter:a", f"atempo={tempo:.4f}", "-ar", "44100", "-c:a", "pcm_s16le", dest,
    ])
    plan.voice = job.keep(AudioFileClip(dest))
    plan.voice_path, plan.trim = dest, (0.0, float(plan.voice.duration))
    plan.voice_len = float(plan.voice.duration)
    plan.words = [(start / tempo, length / tempo, text) for start, length, text in plan.words]


def _make_version(cfg, ui, work_dir, job, plans, search, choices, version, save_path,
                  fallbacks, frame, share, spent, stop):
    """Footage to finished file for one version; returns where it was saved."""
    from moviepy import CompositeVideoClip, ImageClip, VideoFileClip, vfx

    from . import audio, motion

    target_w, target_h, ratio, bitrate = frame
    base_progress, span = share
    total_scenes = len(plans)

    # 3. Footage. FootageSearch remembers what it has handed out, so a second
    #    version gets different clips without being asked to.
    began = time.perf_counter()
    credits, providers_used = [], set()
    ai_seen = 0
    for i, plan in enumerate(plans):
        _check(stop)
        if plan.scene.is_ai and not _ai_picture(plan, i, cfg, ratio, work_dir, ui, version,
                                                search, stop):
            plan.scene = _stock_instead(plan.scene)
        if plan.scene.is_ai:
            plan.kind = "still AI picture"
            _moving_picture(plan, i, cfg, ratio, work_dir, ui, version, ai_seen, stop)
            ai_seen += 1
        if plan.scene.is_local:
            plan.kind = "your file"
            ui.log(f"📁 Scene {i + 1}: {os.path.basename(plan.sources[0].path)}")
        elif plan.scene.is_ai:
            pass   # its picture was just made
        else:
            plan.parts = 2 if (plan.scene.duration is None and plan.voice_len > LONG_SCENE) else 1
            previous, plan.sources = plan.sources, []
            _acquire_stock(plan, i, search, work_dir, ui, credits, providers_used,
                           choices.get(i), version, previous, stop)
        ui.progress(base_progress + span * 0.35 * (i + 1) / total_scenes)
    spent["footage"] += time.perf_counter() - began

    # 4. Timeline. With music, the cuts between scenes move onto its beats.
    music_file = (cfg.get("music_path") or "").strip()
    if cfg.get("smart_motion") and cfg.get("music_enabled") and os.path.isfile(music_file):
        try:
            hits = audio.beats(music_file, sum(p.duration for p in plans))
            snapped = audio.snap_cuts([p.duration for p in plans],
                                      [p.voice_len + 0.05 for p in plans], hits)
            moved = sum(abs(a - p.duration) > 0.01 for a, p in zip(snapped, plans))
            for plan, length in zip(plans, snapped):
                plan.duration = length
            ui.log(f"🥁 Cuts on the beat: {moved} of {max(len(plans) - 1, 0)}")
        except Exception as exc:  # noqa: BLE001 - the cuts just stay where they were
            ui.log(f"⚠️ Couldn't find the music's beats ({exc}).")
    durations = [p.duration for p in plans]
    starts = boundaries(durations)
    total = starts[-1]
    shots = shot_spans(durations, [p.parts for p in plans])

    # 5. Pictures.
    _check(stop)
    for line in scene_lines(plans):
        ui.log(line)
    ui.log(f"✂️ Stitching {ratio} scenes at {target_w}x{target_h}...")
    from PIL import Image

    from . import drawn

    style = cfg.get("style") if cfg.get("style") in drawn.STYLES else drawn.NORMAL
    if style != drawn.NORMAL:
        ui.log(f"✏️ {style} style: every scene is drawn on screen")
    layers, flip = [], False
    for shot in shots:
        plan = plans[shot.scene]
        source = plan.sources[shot.part]
        if style != drawn.NORMAL:   # drawn on screen from one still, instead of the footage
            if source.kind == "image":
                still = Image.open(source.path)
            else:
                footage_clip = job.keep(VideoFileClip(source.path, audio=False))
                still = Image.fromarray(footage_clip.get_frame((footage_clip.duration or 0) / 2))
            layers.append(drawn.clip(still, shot.length, (target_w, target_h), style)
                          .with_start(shot.start))
            continue
        if source.kind == "image":
            still = motion.load_image(source.path, target_w, target_h)
            base, movement = ImageClip(still), None
        else:
            base = job.keep(VideoFileClip(source.path, audio=False))
            movement = motion.measure_motion(base)
        zoom = motion.zoom_range(plan.scene.zoom, source.kind, movement, flip)
        flip = not flip
        focus = None
        if cfg.get("smart_motion"):
            try:   # a few frames of the clip say where its detail is
                length = base.duration or 0.0
                focus = motion.busiest_point(
                    [base.get_frame(length * k) for k in (0.2, 0.5, 0.8)] if length
                    else [base.get_frame(0)])
            except Exception:  # noqa: BLE001 - the middle, as before
                focus = None
        clip = motion.framed_shot(base, shot.length, (target_w, target_h), zoom, focus)
        if shot.fade_in > 0:
            clip = clip.with_effects([vfx.CrossFadeIn(shot.fade_in)])
        layers.append(clip.with_start(shot.start))

    # Cards go over the footage and under the captions. They never sink a render either.
    carded = [(i, plan.scene.card) for i, plan in enumerate(plans) if plan.scene.card]
    if carded:
        from . import captions, cards

        look = captions.CaptionOptions.from_cfg(cfg)
        accent = captions.COLORS[look.color].fill
        accent = captions.FILL if accent == captions.STROKE else accent  # black on a dark panel
        for i, text in carded:
            try:
                layers.append(cards.layer(text, (target_w, target_h), starts[i], durations[i],
                                          accent, look.position))
            except Exception as exc:  # noqa: BLE001
                ui.log(f"⚠️ Couldn't draw the card for scene {i + 1} ({exc}); left out.")
        ui.log(f"🃏 Cards: {len(carded)}")

    # Captions sit on top of everything. Like music, they never sink a render.
    cues = []
    if cfg.get("captions", True):
        try:
            from . import captions

            options = captions.CaptionOptions.from_cfg(cfg)
            words = _timeline_words(plans, starts)
            _shown, states = captions.plan(words, options)
            # The subtitle file reads better in phrases, whatever is on screen.
            cues = captions.group_words(words)
            layer = captions.caption_layer(states, (target_w, target_h), options, total)
            if layer is not None:
                layers.append(layer)
                ui.log(f"💬 Captions: {len(words)} words, {options.style.lower()} style")
        except Exception as exc:  # noqa: BLE001
            ui.log(f"⚠️ Couldn't add captions ({exc}); rendering without them.")
            cues = []

    video = job.keep(
        CompositeVideoClip(layers, size=(target_w, target_h), bg_color=(0, 0, 0))
        .with_duration(total)
    )

    # 6. Soundtrack.
    voices, speech = [], []
    for plan, start in zip(plans, starts):
        if plan.voice is None:
            continue
        voice = plan.voice
        if plan.voice_len > plan.duration:
            voice = audio.trim_voice(voice, plan.duration)
        voices.append((voice, start))
        speech.append((start, start + min(plan.voice_len, plan.duration)))

    music = None
    music_path = (cfg.get("music_path") or "").strip()
    if cfg.get("music_enabled") and music_path:
        try:
            music = audio.music_track(music_path, total, speech, job.keep)
            ui.log(f"🎵 Background music: {os.path.basename(music_path)}, ducked under the voice")
        except Exception as exc:  # noqa: BLE001 - a bad track shouldn't sink the render
            ui.log(f"⚠️ Couldn't use the music file ({exc}); rendering without music.")
            music = None
    elif cfg.get("music_enabled"):
        ui.log("⚠️ Background music is on but no track is chosen; rendering without music.")

    if cfg.get("sound_effects"):
        from . import cards

        try:
            effects = audio.sound_effects(
                starts[1:], [starts[i] + cards.MARGIN for i, p in enumerate(plans)
                             if p.scene.card], total)
            voices += effects
            ui.log(f"🔊 Sound effects: {len(effects)}")
        except Exception as exc:  # noqa: BLE001 - like music, never sinks a render
            ui.log(f"⚠️ Couldn't add the sound effects ({exc}).")
    soundtrack = audio.mix_audio(voices, music, total)
    if soundtrack is not None:
        video = video.with_audio(job.keep(soundtrack))

    # 7. Encode.
    _check(stop)
    began = time.perf_counter()
    ui.status("Encoding final video...")
    # Encoded here, in the temp folder, then moved: ffmpeg is a separate
    # program, and folder protection may refuse it where it allows this app.
    encoded = os.path.join(work_dir, f"output_{version}.mp4")
    encode_from, encode_span = base_progress + span * 0.35, span * 0.6

    def encoding(fraction):
        ui.progress(encode_from + encode_span * fraction)

    try:
        _encode(video, encoded, soundtrack is not None, bitrate, work_dir, encoding, stop)
    except RenderCancelled:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RenderError("Couldn't encode the video. The Log has the technical details.",
                          _one_line(exc)) from exc
    save_path = paths.deliver(encoded, save_path, fallbacks, ui.log)
    spent["encode"] += time.perf_counter() - began
    ui.progress(base_progress + span)

    if credits:
        try:
            credits_path = footage.write_credits(save_path, credits, providers_used)
            ui.log(f"🎞️ Footage credits saved to {os.path.basename(credits_path)}")
        except OSError as exc:
            ui.log(f"⚠️ Couldn't save the footage credits file ({exc}).")

    if cues:
        try:
            srt_path = captions.write_srt(save_path, cues)
            ui.log(f"💬 Subtitles saved to {os.path.basename(srt_path)}")
        except OSError as exc:
            ui.log(f"⚠️ Couldn't save the subtitle file ({exc}).")

    try:
        from . import projects, thumbnail

        thumb = os.path.splitext(save_path)[0] + ".thumbnail.jpg"
        # Between two spoken lines no caption is on screen: the cleanest frames.
        pauses = [(a_end + b_start) / 2 for (_a, a_end), (b_start, _b) in zip(speech, speech[1:])
                  if b_start - a_end > 0.12]
        thumbnail.make(save_path, projects.default_title(cfg.get("script") or "", 60), thumb,
                       pauses)
        ui.log(f"🖼️ Thumbnail saved to {os.path.basename(thumb)}")
    except Exception as exc:  # noqa: BLE001 - the video is saved either way
        ui.log(f"⚠️ Couldn't make the thumbnail ({exc}).")
    return save_path


def _encode_logger(on_progress, stop):
    """A MoviePy progress logger that reports to the window and obeys Cancel.

    MoviePy's default logger draws a bar on stderr, which does not exist in the
    installed app and used to kill the render. This one writes nowhere. It is
    called for every audio chunk and video frame, which is also the only place
    a running encode can be interrupted.
    """
    from proglog import ProgressBarLogger

    class EncodeLogger(ProgressBarLogger):
        def bars_callback(self, bar, attr, value, old_value=None):
            if stop():
                raise RenderCancelled()
            if attr != "index":
                return
            total = self.bars[bar].get("total") or 0
            if total:
                done = min(value / total, 1.0)
                # The sound is written first and is quick; the frames are the wait.
                on_progress(0.08 * done if bar == "chunk" else 0.08 + 0.92 * done)

    return EncodeLogger()


def _encode(video, path, with_audio, bitrate, work_dir, on_progress=lambda f: None,
            stop=lambda: False):
    video.write_videofile(
        path,
        codec="libx264",
        audio_codec="aac",
        audio=with_audio,
        audio_fps=44100,
        fps=30,
        preset="fast",
        bitrate=bitrate,
        # Without this MoviePy drops TEMP_MPY_wvf_snd.mp3 beside the output.
        temp_audiofile_path=work_dir,
        logger=_encode_logger(on_progress, stop),
    )


def _timeline_words(plans, starts):
    """Every spoken word as (start, duration, text) on the video's timeline.

    Words a forced Duration: cuts off are dropped, and one straddling the cut
    is shortened, so no caption outlives its voice.
    """
    words = []
    for plan, scene_start in zip(plans, starts):
        limit = min(plan.voice_len, plan.duration)
        for start, length, text in plan.words:
            if start >= limit:
                break
            words.append((scene_start + start, min(length, limit - start), text))
    return words


def _minutes(seconds: float) -> str:
    return f"{seconds:.0f} s" if seconds < 90 else f"{seconds / 60:.0f} min"


def _ai_instead(scene):
    """``scene`` asking for an AI picture in place of stock footage: its Picture: line if it
    has one, else its search words and what is said over them."""
    described = scene.picture or ", ".join(filter(None, [scene.visual, scene.voice]))
    return dataclasses.replace(scene, visual="ai: " + described.replace(":", " "))


def _stock_instead(scene):
    """``scene`` with search words in place of its AI picture, for when none can be made."""
    words = [w for w in re.findall(r"[a-z]+", scene.ai_prompt.lower())
             if len(w) > 2 and w not in footage.STOPWORDS]
    return dataclasses.replace(scene, visual=" ".join(words[:3]) or "abstract background")


def scene_lines(plans) -> list[str]:
    """One Log line per scene: what it shows, and what replaced what when something failed."""
    return [f"🧩 Scene {i + 1}: {plan.kind or 'stock clip'}"
            + (f" ({plan.note})" if plan.note else "") for i, plan in enumerate(plans)]


def _moving_picture(plan, index, cfg, ratio, work_dir, ui, version, ai_before, stop) -> None:
    """Turn the scene's AI picture into a short moving clip, if Moving pictures asks for it.

    Like the picture itself, never sinks a render: on any failure the still stays.
    """
    from . import animate

    choice = cfg.get("animate") or ""
    if cfg.get("style") in ("Blackboard", "Drawing"):
        return   # the scene is drawn from the still picture anyway
    if not animate.wanted(choice, index, ai_before) or plan.sources[0].kind != "image":
        return
    if not animate.installed():
        ui.log("🎞️ Moving pictures are on but not downloaded; the scene keeps its still picture.")
        return
    if cfg.get("ai_images_backend") == "cpu":   # minutes per picture there: hours per clip
        ui.log("🎞️ Moving pictures need the graphics card; the scene keeps its still picture.")
        return
    if cfg.get("_moving_failed"):
        # It failed earlier in this render: the same settings on the same card would again.
        plan.note = "moving picture skipped: it failed earlier in this video"
        return
    clip = os.path.join(work_dir, f"moving_{version}_{index}.avi")
    try:
        moved = animate.make(plan.sources[0].path, plan.scene.ai_prompt or "", ratio, clip,
                             should_stop=stop, log=ui.log,
                             shared=cfg.get("ai_gpu") == "integrated")
        looped = animate.ping_pong(clip, os.path.join(work_dir, f"moving_{version}_{index}.mp4"),
                                   moved.frames - (animate.FRAMES - animate.GOOD_FRAMES))
    except animate.Stopped as exc:
        raise RenderCancelled() from exc
    except Exception as exc:  # noqa: BLE001 - the still picture is a fine scene too
        cfg["_moving_failed"] = True
        ui.log(f"⚠️ Scene {index + 1}: {exc}")
        plan.note = "moving picture failed, the still is used"
        return
    ui.log(f"🎞️ Scene {index + 1}: the picture moves (made in {moved.seconds:.0f} s)")
    plan.parts, plan.sources = 1, [_Source(looped, "video")]
    plan.kind = "moving AI picture"


def _ai_picture(plan, index, cfg, ratio, work_dir, ui, version, search, stop) -> bool:
    """Make the scene's AI picture and use it as the scene's footage.

    False when it could not be made and stock footage should be used instead:
    like captions and music, an AI picture never sinks a render.
    """
    from . import imagegen

    # A picture already made in the Storyboard is the one the user looked at: use it.
    ready_made = (cfg.get("ai_pictures") or {}).get(index)
    if version == 1 and ready_made and os.path.isfile(ready_made):
        ui.log(f"🎨 Scene {index + 1}: using the AI picture from the Storyboard")
        plan.parts, plan.sources = 1, [_Source(ready_made, "image")]
        return True
    # On a slow PC the user may have chosen to make a few pictures and use them again.
    made_here = cfg.setdefault("_ai_made", [])
    reuse = cfg.get("ai_reuse") or 0
    if reuse and len(made_here) >= reuse:
        again = made_here[cfg.setdefault("_ai_reused", 0) % reuse]
        cfg["_ai_reused"] += 1
        ui.log(f"🎨 Scene {index + 1}: using an AI picture made for an earlier scene")
        plan.parts, plan.sources = 1, [_Source(again, "image")]
        return True
    path = os.path.join(work_dir, f"ai_{version}_{index}.png")
    ui.log(f"🎨 Scene {index + 1}: making an AI picture of '{plan.scene.ai_prompt}'...")
    speed = cfg.get("ai_images_speed") or imagegen.BEST
    try:
        made = imagegen.make(plan.scene.ai_prompt, imagegen.size_for(ratio), path,
                             cfg.get("ai_images_look") or imagegen.DEFAULT_LOOK,
                             should_stop=stop, backend=cfg.get("ai_images_backend") or "",
                             log=ui.log, speed=speed,
                             threads=tuple(cfg.get("ai_threads") or (0, 0)))
    except imagegen.Stopped as exc:
        raise RenderCancelled() from exc
    except imagegen.ImageError as exc:
        if not search.providers:
            raise Exception(f"Scene {index + 1}: {exc}") from exc
        ui.log(f"⚠️ {exc} Using stock footage for scene {index + 1}.")
        plan.note = "AI picture failed, a stock clip is used"
        return False
    made_here.append(path)
    if cfg.get("project_dir"):
        # The work dir is deleted after the render; the project keeps the picture.
        from . import projects

        look = cfg.get("ai_images_look") or imagegen.DEFAULT_LOOK
        keep = projects.picture_file(cfg["project_dir"], f"{plan.scene.ai_prompt}|{look}|{ratio}")
        try:
            os.makedirs(os.path.dirname(keep), exist_ok=True)
            shutil.copyfile(path, keep)
        except OSError as exc:
            ui.log(f"⚠️ Couldn't keep the picture in the project ({exc}).")
    ui.ai_speed(made.seconds, speed)
    left = max((cfg.get("_ai_total") or 0) - len(made_here), 0)
    ui.log(f"🎨 Picture made in {made.seconds:.0f} s"
           + (f" · {left} more to make, about {_minutes(made.seconds * left)} left" if left else ""))
    if made.backend == imagegen.CPU and cfg.get("ai_images_backend") != imagegen.CPU:
        # The card failed and the processor worked: don't pay for that failure again,
        # in this video's other scenes or the next video.
        cfg["ai_images_backend"] = imagegen.CPU
        ui.ai_backend(imagegen.CPU)
    plan.parts, plan.sources = 1, [_Source(path, "image")]
    return True


def _acquire_stock(plan, index, search, work_dir, ui, credits, providers_used,
                   preferred=None, version=1, previous=(), stop=lambda: False):
    """Search and download the clip(s) for one stock scene.

    ``preferred`` are clips the user picked in the scene preview, best first;
    they are tried before the search's own results. ``previous`` are this
    scene's clips from an earlier version, reused if nothing new can be found.
    """
    query = plan.scene.visual
    wanted = plan.parts
    picked = [c for c in (preferred or []) if c.key not in search.used]
    seen = {c.key for c in picked}
    # The top results, the one whose description fits the scene best first.
    found = footage.rank(search.find(query, max(RANKED, wanted + SPARE_CANDIDATES)), query,
                         plan.scene.voice or "")
    candidates = picked + [c for c in found if c.key not in seen]
    for candidate in candidates:
        if len(plan.sources) == wanted:
            break
        _check(stop)
        dest = os.path.join(work_dir, f"clip_{version}_{index}_{len(plan.sources)}.mp4")
        ui.log(
            f"⬇️ Downloading {candidate.provider} clip "
            f"({candidate.width}x{candidate.height})..."
        )
        try:
            footage.download(candidate.url, dest, should_stop=stop)
        except footage.Stopped:
            raise
        except Exception as exc:  # noqa: BLE001 - try the next result instead
            ui.log(f"⚠️ That download failed ({exc.__class__.__name__}); trying another clip.")
            continue
        search.mark_used(candidate)
        plan.sources.append(_Source(dest, "video"))
        providers_used.add(candidate.provider)
        credits.append(f"Scene {index + 1}: {candidate.credit()}")
        plan.kind = plan.kind or f"stock clip ({candidate.provider})"
        ui.log(f"🎞️ {candidate.credit()}")

    if not plan.sources and previous:
        plan.sources = list(previous)
        ui.log(f"⚠️ No other clip found for '{query}'; this version reuses the same one.")
    if not plan.sources:
        raise Exception(search.explain_failure(query))
    if len(plan.sources) < wanted:
        ui.log(f"⚠️ Only one clip found for '{query}'; the scene uses it throughout.")
        plan.parts = len(plan.sources)
    elif wanted == 2:
        ui.log(f"✂️ Long line - cutting between two clips of '{query}'.")
