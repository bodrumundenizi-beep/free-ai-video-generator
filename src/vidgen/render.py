"""The render worker: script in, MP4 out, on a background thread.

The worker never touches a widget. Everything it reports goes through UiBridge,
a queue the window drains on its own thread.

Order of work, cheapest check first so mistakes surface before any download:

    1. parse the script; check every local: file exists
    2. voiceovers  - each voice decides its scene's length
    3. footage     - search, download, credit
    4. timeline    - where every shot sits, with crossfades centred on cuts
    5. pictures    - framed, zoomed, faded, composited
    6. soundtrack  - voices at their scene starts, music ducked beneath them
    7. encode
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field

from . import footage
from .formats import resolve_target
from .script import parse_script
from .timeline import boundaries, shot_spans
from .voice import generate_voiceover

LONG_SCENE = 6.0  # a voice longer than this gets two clips, cut at the middle
MIN_SCENE = 0.5
DEFAULT_PADDING = 0.25
SPARE_CANDIDATES = 2  # extra search results to fall back on if a download fails

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

    def finished(self, ok, title, message):
        self._post("finished", ok=ok, title=title, message=message)


@dataclass
class _Source:
    path: str
    kind: str  # "video" or "image"


@dataclass
class _ScenePlan:
    scene: object
    voice: object = None  # AudioFileClip, or None for a silent scene
    voice_len: float = 0.0
    duration: float = 0.0
    parts: int = 1
    sources: list = field(default_factory=list)


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


def render_worker(cfg: dict, ui: UiBridge):
    """Render ``cfg["script"]`` to ``cfg["output_path"]``, reporting through ``ui``."""
    # Allocated before the try so the finally can always clean it up.
    work_dir = new_work_dir()
    job = _Render()
    try:
        _render(cfg, ui, work_dir, job)
    except Exception as e:  # noqa: BLE001 - surfaced to the user verbatim
        ui.spinner(False)
        ui.progress(0.0)
        ui.log(f"❌ ERROR: {str(e)}")
        ui.finished(False, "Render failed", str(e))
    finally:
        # Close before deleting: an open reader would keep its file locked.
        job.close_all()
        shutil.rmtree(work_dir, ignore_errors=True)
        ui.busy(False)


def _render(cfg, ui, work_dir, job):
    save_path = (cfg.get("output_path") or "").strip()
    script_text = (cfg.get("script") or "").strip()
    if not save_path:
        raise Exception("Save location cannot be blank.")
    if not script_text:
        raise Exception("Script cannot be blank.")

    ratio = cfg.get("aspect", "9:16")
    quality = cfg.get("resolution", "1080p")
    target_w, target_h, orientation, bitrate = resolve_target(ratio, quality)
    default_padding = _as_float(cfg.get("padding"), DEFAULT_PADDING)

    # 1. Script, and every local file, before anything slow happens.
    warnings = []
    scenes = parse_script(script_text, warnings)
    for warning in warnings:
        ui.log(f"⚠️ {warning}")

    save_path = os.path.abspath(save_path)
    out_dir = os.path.dirname(save_path)
    os.makedirs(out_dir, exist_ok=True)

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
    needs_stock = [p.scene for p in plans if not p.scene.is_local]
    if needs_stock and not search.providers:
        raise Exception(
            "API Key cannot be blank. Add a Pexels or Pixabay API key in Settings "
            f"- the scene at line {needs_stock[0].line} needs stock footage."
        )

    # MoviePy only now, so a script error is reported instantly.
    from moviepy import AudioFileClip, CompositeVideoClip, ImageClip, VideoFileClip, vfx

    from . import audio, motion

    ui.log(
        f"🚀 Starting {ratio} render at {target_w}x{target_h} "
        f"({quality}, {bitrate})..."
    )
    ui.status(f"Rendering {target_w}x{target_h}...")
    total_scenes = len(plans)

    # 2. Voiceovers. Each one decides its scene's length.
    for i, plan in enumerate(plans):
        scene = plan.scene
        ui.log(f"🎬 Scene {i + 1} of {total_scenes}")
        if scene.voice:
            voice_path = os.path.join(work_dir, f"voice_{i}.mp3")
            # Studio voices come back as a mastered .wav, classic ones as the .mp3.
            voice_path = generate_voiceover(scene.voice, voice_path, ui.log, cfg.get("voice"))
            # The file is registered for closing; the trimmed copy shares its reader.
            plan.voice = audio.trim_to_speech(job.keep(AudioFileClip(voice_path)))
            plan.voice_len = float(plan.voice.duration)

        if scene.duration is None:
            padding = scene.padding if scene.padding is not None else default_padding
            plan.duration = plan.voice_len + padding
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

        if not scene.is_local and scene.duration is None and plan.voice_len > LONG_SCENE:
            plan.parts = 2
        ui.progress(0.35 * (i + 1) / total_scenes)

    # 3. Footage.
    credits, providers_used = [], set()
    for i, plan in enumerate(plans):
        if plan.scene.is_local:
            ui.log(f"📁 Scene {i + 1}: {os.path.basename(plan.sources[0].path)}")
        else:
            _acquire_stock(plan, i, search, work_dir, ui, credits, providers_used)
        ui.progress(0.35 + 0.4 * (i + 1) / total_scenes)

    # 4. Timeline.
    durations = [p.duration for p in plans]
    starts = boundaries(durations)
    total = starts[-1]
    shots = shot_spans(durations, [p.parts for p in plans])

    # 5. Pictures.
    ui.log(f"✂️ Stitching {ratio} scenes at {target_w}x{target_h}...")
    layers, flip = [], False
    for shot in shots:
        plan = plans[shot.scene]
        source = plan.sources[shot.part]
        if source.kind == "image":
            still = motion.load_image(source.path, target_w, target_h)
            base, movement = ImageClip(still), None
        else:
            base = job.keep(VideoFileClip(source.path, audio=False))
            movement = motion.measure_motion(base)
        zoom = motion.zoom_range(plan.scene.zoom, source.kind, movement, flip)
        flip = not flip
        clip = motion.framed_shot(base, shot.length, (target_w, target_h), zoom)
        if shot.fade_in > 0:
            clip = clip.with_effects([vfx.CrossFadeIn(shot.fade_in)])
        layers.append(clip.with_start(shot.start))
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

    soundtrack = audio.mix_audio(voices, music, total)
    if soundtrack is not None:
        video = video.with_audio(job.keep(soundtrack))

    # 7. Encode.
    ui.status("Encoding final video...")
    ui.spinner(True)
    video.write_videofile(
        save_path,
        codec="libx264",
        audio_codec="aac",
        audio=soundtrack is not None,
        audio_fps=44100,
        fps=30,
        preset="fast",
        bitrate=bitrate,
        # Without this MoviePy drops TEMP_MPY_wvf_snd.mp3 beside the output.
        temp_audiofile_path=work_dir,
        # logger defaults to "bar", whose tqdm writes to sys.stderr - which is
        # None in a windowed build, killing the render here.  The UI already
        # shows an indeterminate spinner for this phase.
        logger=None,
    )
    ui.spinner(False)
    ui.progress(0.95)

    if credits:
        credits_path = footage.write_credits(save_path, credits, providers_used)
        ui.log(f"🎞️ Footage credits saved to {os.path.basename(credits_path)}")

    ui.log("🧹 Releasing memory and deleting temp files...")
    ui.progress(1.0)
    ui.log(f"✅ DONE! {target_w}x{target_h} video saved to:\n{save_path}")
    ui.finished(
        True,
        "Render complete",
        f"{target_w}x{target_h} video rendered successfully.\n\nSaved at:\n{save_path}",
    )


def _acquire_stock(plan, index, search, work_dir, ui, credits, providers_used):
    """Search and download the clip(s) for one stock scene."""
    query = plan.scene.visual
    wanted = plan.parts
    candidates = search.find(query, wanted + SPARE_CANDIDATES)
    for candidate in candidates:
        if len(plan.sources) == wanted:
            break
        dest = os.path.join(work_dir, f"clip_{index}_{len(plan.sources)}.mp4")
        ui.log(
            f"⬇️ Downloading {candidate.provider} clip "
            f"({candidate.width}x{candidate.height})..."
        )
        try:
            footage.download(candidate.url, dest)
        except Exception as exc:  # noqa: BLE001 - try the next result instead
            ui.log(f"⚠️ That download failed ({exc.__class__.__name__}); trying another clip.")
            continue
        search.mark_used(candidate)
        plan.sources.append(_Source(dest, "video"))
        providers_used.add(candidate.provider)
        credits.append(f"Scene {index + 1}: {candidate.credit()}")
        ui.log(f"🎞️ {candidate.credit()}")

    if not plan.sources:
        raise Exception(search.explain_failure(query))
    if len(plan.sources) < wanted:
        ui.log(f"⚠️ Only one clip found for '{query}'; the scene uses it throughout.")
        plan.parts = len(plan.sources)
    elif wanted == 2:
        ui.log(f"✂️ Long line - cutting between two clips of '{query}'.")
