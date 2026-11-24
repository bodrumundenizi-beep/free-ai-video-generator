import hashlib
import io
import os
import zipfile

import pytest

from vidgen import imagegen, writer
from vidgen.imagegen import ImageError
from vidgen.writer import Model


class FakeProcess:
    """A program that finishes after ``polls`` looks, optionally writing its picture."""

    def __init__(self, args, polls=1, code=0, write=True):
        self.args, self.polls, self.code, self.write = args, polls, code, write
        self.returncode, self.killed = None, False

    def poll(self):
        if self.returncode is None:
            self.polls -= 1
            if self.polls <= 0:
                self.returncode = self.code
                if self.write and self.code == 0:
                    with open(self.args[self.args.index("-o") + 1], "wb") as fh:
                        fh.write(b"png")
        return self.returncode

    def kill(self):
        self.killed, self.returncode = True, -9

    def wait(self):
        return self.returncode


def launcher(seen, fails=(), **kwargs):
    """Starts fake programs. ``fails`` names the backends ("gpu", "cpu") that exit with 1."""
    def launch(args, log_path):
        backend = imagegen.CPU if "--backend" in args else imagegen.GPU
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write("ggml_vulkan: 0 = Intel(R) Graphics (Intel) | uma: 1 | fp16: 1\n"
                     f"[I] loading from {os.path.expanduser('~')}\\models\n"
                     "  |====>   | 1/8 - 5.18s/it\r  |=====>  | 2/8\n"
                     "[E] vk::Device::allocateMemory: ErrorOutOfDeviceMemory\n"
                     "[E] generate failed --- main.cpp:939\n")
        code = 1 if backend in fails else kwargs.get("code", 0)
        seen.append(FakeProcess(args, **dict(kwargs, code=code)))
        return seen[-1]
    return launch


def ok():
    return True, ""


def plenty():
    return 16_000_000_000


def make(home, seen=None, name="a.png", **kwargs):
    """imagegen.make with a fake program, plenty of memory and files taken as sound."""
    kwargs.setdefault("launch", launcher([] if seen is None else seen))
    kwargs.setdefault("memory", plenty)
    kwargs.setdefault("verify", lambda _stop: [])
    return imagegen.make(kwargs.pop("prompt", "a cat"), kwargs.pop("size", (576, 1024)),
                         str(home / name), check=ok, **kwargs)


def backends(seen):
    return [imagegen.CPU if "--backend" in p.args else imagegen.GPU for p in seen]


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(imagegen.time, "sleep", lambda _s: None)
    return tmp_path


# --- making a picture ----------------------------------------------------------------

def test_make_runs_the_program_with_the_prompt_the_look_and_the_size(home):
    seen = []
    made = make(home, seen, "scene.png", prompt="a robot  holding a clipboard.", look="3D render",
                seed=7)
    args = seen[0].args
    assert made.backend == imagegen.GPU and made.size == (576, 1024) and made.seconds >= 0
    assert os.path.isfile(home / "scene.png")
    assert args[args.index("-p") + 1] == "a robot holding a clipboard, " + imagegen.LOOKS["3D render"]
    assert args[args.index("-W") + 1:args.index("-W") + 4] == ["576", "-H", "1024"]
    assert args[args.index("-s") + 1] == "7" and args[args.index("--steps") + 1] == "8"
    assert args[args.index("--llm") + 1] == writer.model_path()  # the Smart writer's own file
    assert args[args.index("--diffusion-model") + 1].endswith(imagegen.MODEL.file)
    # the weights stay on disk (a third of the memory); nothing forces the processor
    assert args[args.index("--params-backend") + 1] == "disk"
    assert "--offload-to-cpu" not in args and "--backend" not in args


def test_each_picture_gets_its_own_seed_unless_one_is_given(home):
    seen = []
    for _ in range(2):
        make(home, seen)
    seeds = [p.args[p.args.index("-s") + 1] for p in seen]
    assert seeds[0] != seeds[1]


def test_cancel_kills_the_program_at_once_and_tries_nothing_else(home):
    seen = []
    with pytest.raises(imagegen.Stopped):
        make(home, seen, should_stop=lambda: True, launch=launcher(seen, polls=50))
    assert seen[0].killed and len(seen) == 1


def test_too_slow_is_stopped(home, monkeypatch):
    clock = iter(range(0, 100000, 500))
    monkeypatch.setattr(imagegen.time, "monotonic", lambda: next(clock))
    seen = []
    with pytest.raises(ImageError, match="too long"):
        make(home, seen, launch=launcher(seen, polls=50))
    assert seen[0].killed


def test_no_description_and_not_ready_are_image_errors(home):
    with pytest.raises(ImageError, match="no description"):
        make(home, prompt=" ,. ")
    with pytest.raises(ImageError, match="not downloaded yet"):
        imagegen.make("a cat", (576, 1024), str(home / "a.png"), launch=launcher([]))


def test_long_descriptions_are_cut_and_unknown_looks_use_the_default():
    text = imagegen.full_prompt("word " * 200, "Oil painting")
    assert len(text) < imagegen.MAX_PROMPT + 80
    assert text.endswith(imagegen.LOOKS[imagegen.DEFAULT_LOOK])


def test_size_follows_the_video_shape():
    assert imagegen.size_for("9:16") == (576, 1024) and imagegen.size_for("16:9") == (1024, 576)
    assert imagegen.size_for("1:1") == (576, 1024)


# --- when the graphics card cannot do it (issue #4) -------------------------------------

def test_the_graphics_card_fails_and_the_processor_makes_it_smaller(home):
    seen, said = [], []
    made = make(home, seen, launch=launcher(seen, fails=("gpu",)), log=said.append)
    assert backends(seen) == [imagegen.GPU, imagegen.CPU]
    assert made.backend == imagegen.CPU and made.size == (384, 672)
    cpu = seen[1].args
    assert cpu[cpu.index("--backend") + 1] == "cpu" and cpu[cpu.index("--steps") + 1] == "4"
    assert cpu[cpu.index("-W") + 1:cpu.index("-W") + 4] == ["384", "-H", "672"]


def test_a_card_that_starts_cleanly_and_writes_nothing_still_falls_back(home):
    # Exit code 0 and a named device, but no picture: the start-up check passing proves nothing.
    seen = []

    def launch(args, log_path):
        process = launcher(seen)(args, log_path)
        process.write = "--backend" in args
        return process

    made = make(home, seen, launch=launch)
    assert backends(seen) == [imagegen.GPU, imagegen.CPU] and made.backend == imagegen.CPU


def test_both_fail_and_the_error_carries_what_the_program_said(home):
    seen, said = [], []
    with pytest.raises(ImageError, match="could not be made on the processor") as caught:
        make(home, seen, launch=launcher(seen, fails=("gpu", "cpu")), log=said.append)
    assert backends(seen) == [imagegen.GPU, imagegen.CPU]
    detail = caught.value.detail
    assert "exit code 1" in detail and "ErrorOutOfDeviceMemory" in detail
    assert "generate failed" in detail and "GB free at that moment" in detail
    assert "1/8" not in detail                                   # progress bars are noise
    assert os.path.basename(os.path.expanduser("~")) not in detail   # no user name
    logged = "\n".join(said)
    assert "Graphics card used: Intel(R) Graphics" in logged and "ErrorOutOfDeviceMemory" in logged
    assert "576x1024, 8 steps" in logged and "16.0 of" in logged and "picture model" in logged


def test_a_pc_where_the_card_is_known_to_fail_goes_straight_to_the_processor(home):
    seen = []
    assert make(home, seen, backend=imagegen.CPU).backend == imagegen.CPU
    assert backends(seen) == [imagegen.CPU]


def test_a_picture_left_by_a_failed_try_does_not_pass_for_the_next(home):
    (home / "a.png").write_bytes(b"old")
    with pytest.raises(ImageError):
        make(home, launch=launcher([], write=False))


@pytest.mark.parametrize("free,backend,expected", [
    (16e9, "gpu", ((576, 1024), 8)),
    (5e9, "gpu", ((576, 1024), 8)),
    (4.2e9, "gpu", ((384, 672), 8)),      # short of memory: smaller, not nothing
    (None, "gpu", ((576, 1024), 8)),      # memory cannot be asked: assume it is fine
    (16e9, "cpu", ((384, 672), 4)),
])
def test_fit_picks_a_size_the_pc_can_manage(free, backend, expected):
    assert imagegen.fit((576, 1024), backend, free) == expected
    assert imagegen.smaller((1024, 576)) == (672, 384)


def test_low_memory_makes_a_smaller_picture_and_too_little_makes_none(home):
    seen = []
    made = make(home, seen, memory=lambda: 4_200_000_000)
    assert made.size == (384, 672) and backends(seen) == [imagegen.GPU]
    with pytest.raises(ImageError, match="enough free memory"):
        imagegen.fit((576, 1024), "gpu", 3e9)
    seen = []
    with pytest.raises(ImageError, match="enough free memory"):   # too little for either
        make(home, seen, memory=lambda: 3_000_000_000)
    assert seen == []
    # enough for a small picture on the card, not for the processor: the retry is skipped
    seen = []
    with pytest.raises(ImageError, match="on the graphics card"):
        make(home, seen, memory=lambda: 4_000_000_000, launch=launcher(seen, fails=("gpu",)))
    assert backends(seen) == [imagegen.GPU]


def test_a_damaged_file_is_found_after_both_tries_fail(home):
    with pytest.raises(ImageError, match="damaged and has been removed .Z-Image Turbo."):
        make(home, launch=launcher([], fails=("gpu", "cpu")), verify=lambda _stop: ["Z-Image Turbo"])


def test_damaged_files_checks_contents_and_removes_what_is_wrong(home, monkeypatch):
    contents = small_files(monkeypatch, zipped(["sd-cli.exe"]))
    monkeypatch.setattr(writer, "MODEL", Model("Qwen", "q.gguf", "u", 4,
                                               __import__("hashlib").sha256(b"qwen").hexdigest(), "t"))
    os.makedirs(writer.models_dir())
    for model, data in ((imagegen.DECODER, b"decoder"), (imagegen.MODEL, b"pictur3"),
                        (writer.MODEL, b"qwen")):
        with open(writer.model_path(model), "wb") as fh:
            fh.write(data)
    assert imagegen.damaged_files() == [imagegen.MODEL.name]     # same size, wrong bytes
    assert not os.path.exists(writer.model_path(imagegen.MODEL))
    assert os.path.exists(writer.model_path(imagegen.DECODER))
    assert contents


def test_the_renderer_remembers_the_processor_and_falls_back_to_stock(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from vidgen import render
    from vidgen.script import parse_script

    class Ui:
        def __init__(self):
            self.lines, self.backend = [], None

        def log(self, line):
            self.lines.append(line)

        def ai_backend(self, backend):
            self.backend = backend

        def ai_speed(self, seconds, speed):
            pass

    def plan():
        return render._ScenePlan(parse_script("Visual: ai: a cat\nVoice: Hi.")[0])

    search = SimpleNamespace(providers=["Pixabay"])
    calls = []

    def made_on_cpu(prompt, size, path, look, **kwargs):
        calls.append(kwargs["backend"])
        return imagegen.Made(3.0, imagegen.CPU, (384, 672))

    monkeypatch.setattr(imagegen, "make", made_on_cpu)
    ui, cfg = Ui(), {}
    for _ in range(2):   # two scenes of one video
        assert render._ai_picture(plan(), 0, cfg, "9:16", str(tmp_path), ui, 1, search,
                                  lambda: False)
    assert calls == ["", imagegen.CPU] and ui.backend == imagegen.CPU

    def fails(*_args, **_kwargs):
        raise ImageError("The AI picture could not be made on the processor.", "native text")

    monkeypatch.setattr(imagegen, "make", fails)
    ui = Ui()
    assert render._ai_picture(plan(), 2, {}, "9:16", str(tmp_path), ui, 1, search,
                              lambda: False) is False
    assert "Using stock footage for scene 3" in ui.lines[-1]
    with pytest.raises(Exception, match="Scene 3: The AI picture could not be made"):
        render._ai_picture(plan(), 2, {}, "9:16", str(tmp_path), ui, 1,
                           SimpleNamespace(providers=[]), lambda: False)


# --- the files ---------------------------------------------------------------------

def small_files(monkeypatch, program_zip: bytes):
    """Stand-ins for the three pinned files, small enough to write in a test."""
    def model(name, data):
        return Model(name, name, "https://example.invalid/" + name, len(data),
                     hashlib.sha256(data).hexdigest(), "test")

    program = model("sd.zip", program_zip)
    decoder, picture = model("ae.safetensors", b"decoder"), model("z.gguf", b"picture")
    monkeypatch.setattr(imagegen, "PROGRAM", program)
    monkeypatch.setattr(imagegen, "DECODER", decoder)
    monkeypatch.setattr(imagegen, "MODEL", picture)
    monkeypatch.setattr(imagegen, "FILES", (program, decoder, picture))
    return {program.file: program_zip, decoder.file: b"decoder", picture.file: b"picture",
            writer.MODEL.file: b"qwen"}


def zipped(names):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for name in names:
            archive.writestr(name, b"x")
    return data.getvalue()


def fake_download(contents, fetched):
    def download(progress, should_stop, session, model):
        os.makedirs(writer.models_dir(), exist_ok=True)
        with open(writer.model_path(model), "wb") as fh:
            fh.write(contents[model.file])
        fetched.append(model.file)
        progress(model.size, model.size)
    return download


def test_download_fetches_unpacks_and_is_then_installed(home, monkeypatch):
    contents = small_files(monkeypatch, zipped(["sd-cli.exe", "ggml.dll", "../../evil.exe"]))
    fetched, reports = [], []
    monkeypatch.setattr(writer, "download", fake_download(contents, fetched))
    assert not imagegen.installed()
    imagegen.download(lambda done, total: reports.append((done, total)))
    # the Smart writer's file was missing, so it came too
    assert fetched == [m.file for m in imagegen.FILES] + [writer.MODEL.file]
    assert reports[-1][0] == reports[-1][1] and reports[0][0] < reports[-1][0]
    assert imagegen.installed() and imagegen.program_path().endswith("sd-cli.exe")
    # a path inside the zip is never followed out of the program folder
    assert sorted(os.listdir(imagegen.program_dir())) == [".version", "evil.exe", "ggml.dll",
                                                          "sd-cli.exe"]
    assert not os.path.exists(os.path.join(str(home), "evil.exe"))


def test_a_zip_without_the_program_is_refused(home, monkeypatch):
    contents = small_files(monkeypatch, zipped(["readme.txt"]))
    monkeypatch.setattr(writer, "download", fake_download(contents, []))
    with pytest.raises(ImageError, match="did not contain its program"):
        imagegen.download()
    assert not imagegen.installed()


def test_another_version_of_the_program_does_not_count(home, monkeypatch):
    contents = small_files(monkeypatch, zipped(["sd-cli.exe"]))
    monkeypatch.setattr(writer, "download", fake_download(contents, []))
    imagegen.download()
    with open(os.path.join(imagegen.program_dir(), ".version"), "w") as fh:
        fh.write("an older checksum")
    assert imagegen.program_path() is None and not imagegen.installed()


def test_remove_keeps_the_smart_writer(home, monkeypatch):
    contents = small_files(monkeypatch, zipped(["sd-cli.exe"]))
    monkeypatch.setattr(writer, "download", fake_download(contents, []))
    imagegen.download()
    imagegen.remove()
    assert not imagegen.installed() and not os.path.isdir(imagegen.program_dir())
    assert os.listdir(writer.models_dir()) == [writer.MODEL.file]


def test_ready_explains_what_is_missing(home, monkeypatch):
    assert "Settings > AI pictures" in imagegen.ready()[1]
    monkeypatch.setattr(imagegen, "installed", lambda: True)
    assert "Smart writer's file" in imagegen.ready()[1]
    monkeypatch.setattr(writer, "installed", lambda model=None: True)
    assert "enough free memory" in imagegen.ready(lambda: 2_000_000_000)[1]
    assert imagegen.ready(lambda: 16_000_000_000) == (True, "")
    assert imagegen.ready(lambda: 4_000_000_000) == (True, "")   # enough for a small picture
    assert imagegen.ready(lambda: None) == (True, "")


def test_download_failure_speaks_of_ai_images(home, monkeypatch):
    def failing(*_args):
        raise writer.WriterError("The Smart writer could not be downloaded.")

    monkeypatch.setattr(writer, "download", failing)
    with pytest.raises(ImageError, match="AI pictures could not be downloaded"):
        imagegen.download()


# --- in a script and in the renderer --------------------------------------------------

def test_ai_scenes_in_a_script():
    from vidgen.script import ScriptError, parse_script

    scenes = parse_script("Visual: AI: a robot holding a clipboard\nVoice: Hello.\n\n"
                          "Visual: air balloon\nVoice: Up.")
    assert scenes[0].is_ai and scenes[0].ai_prompt == "a robot holding a clipboard"
    assert not scenes[0].is_local and not scenes[1].is_ai and scenes[1].ai_prompt is None
    with pytest.raises(ScriptError, match="ai: and a description"):
        parse_script("Visual: ai:\nVoice: Hello.")


def test_the_scene_preview_does_not_search_for_ai_scenes():
    from vidgen import scenes

    rows = scenes.plan_preview("Visual: ai: a robot\nVoice: Hi.\n\nVisual: desk\nVoice: Yo.")
    assert rows[0].is_ai and not rows[0].is_stock and rows[0].visual == "a robot"
    assert rows[1].is_stock


def test_a_scene_falls_back_to_search_words_from_its_description():
    from vidgen import render
    from vidgen.script import parse_script

    scene = parse_script("Visual: ai: a robot holding an office clipboard\nVoice: Hi.\n"
                         "Card: Win + V")[0]
    stock = render._stock_instead(scene)
    assert stock.visual == "robot holding office" and not stock.is_ai
    assert (stock.voice, stock.card) == ("Hi.", "Win + V")


# --- New from text ---------------------------------------------------------------------

def test_the_smart_writer_is_asked_for_pictures_only_when_wanted():
    import json

    from vidgen import draft

    audience = draft.Audience()
    assert "picture" not in writer.build_prompt(audience, 66)
    assert "picture" not in json.dumps(writer.script_schema(6))
    assert '"picture" is one sentence' in writer.build_prompt(audience, 66, pictures=True)
    item = writer.script_schema(6, pictures=True)["properties"]["scenes"]["items"]
    assert item["required"] == ["visual", "voice", "picture"]
    example = json.loads(writer._EXAMPLE_OUT_PICTURES)["scenes"]
    assert all(scene["picture"] and scene["voice"] for scene in example)
    assert writer._answer_tokens(66, True) > writer._answer_tokens(66)


def test_parse_scenes_keeps_the_picture_and_marks_what_stock_cannot_show():
    import json

    scenes = writer.parse_scenes(json.dumps({"scenes": [
        {"visual": "clipboard history", "voice": "Press it.",
         "picture": "a laptop screen  showing a list"},
        {"visual": "river bank", "voice": "He left."}]}))
    assert scenes[0].picture == "a laptop screen showing a list" and scenes[0].unfilmable
    assert scenes[1].picture == "" and not scenes[1].unfilmable


def test_to_script_gives_ai_pictures_to_the_scenes_that_were_asked_for():
    from vidgen import draft
    from vidgen.script import parse_script

    scenes = [draft.DraftScene("person typing laptop", "Press it.", 0,
                               "a laptop screen: a list", True),
              draft.DraftScene("river bank", "He left.", 1, "a muddy river bank"),
              draft.DraftScene("old plane", "It flew on.", 2)]

    def visuals(ai):
        return [s.visual for s in parse_script(draft.to_script(scenes, False, ai))]

    assert visuals(draft.AI_NONE) == ["person typing laptop", "river bank", "old plane"]
    assert visuals(draft.AI_UNFILMABLE) == ["ai: a laptop screen a list", "river bank",
                                            "old plane"]
    # with no description from the writer, one is made from the scene itself
    assert visuals(draft.AI_ALL) == ["ai: a laptop screen a list", "ai: a muddy river bank",
                                     "ai: old plane, It flew on."]


def test_brand_names_are_kept_out_of_picture_descriptions():
    from vidgen import draft

    scene = draft.DraftScene("x", "y", 0, "a laptop showing a Microsoft account login on Windows")
    assert draft.picture_for(scene) == "a laptop showing a account login on"
    # a version number goes with its brand (issue #4 drew "on a 11 laptop")
    scene = draft.DraftScene("x", "y", 0, "a user opening the Device Manager app on a "
                                          "Windows 11 laptop, 2 monitors, iPhone 15 on the desk")
    assert draft.picture_for(scene) == ("a user opening the Device Manager app on a laptop, "
                                        "2 monitors, on the desk")


def test_quick_split_can_ask_for_pictures_too():
    from vidgen import draft

    text = "Open the clipboard history in the settings menu. A boy walked along the river bank."
    script = draft.write(text, length=draft.KEEP_ALL, cards=False, ai=draft.AI_UNFILMABLE).script
    assert script.count("Visual: ai: ") == 1 and "river" in script.split("Visual: ")[2]
    assert "ai:" not in draft.write(text, length=draft.KEEP_ALL).script


# --- the sharpener (Real-ESRGAN) ------------------------------------------------------

def sharpener_here(monkeypatch, tmp_path):
    exe = tmp_path / imagegen.SHARPENER
    exe.write_bytes(b"")
    monkeypatch.setattr(writer, "bundled", lambda folder, name: str(exe) if (
        folder, name) == ("realesrgan", imagegen.SHARPENER) else None)
    monkeypatch.setattr(imagegen, "SHARPENER", imagegen.SHARPENER)


def picture(path, size=(384, 672)):
    from PIL import Image
    Image.new("RGB", size, "red").save(path)
    return str(path)


def fake_esrgan(calls, scale=4, fail=False):
    def run(args, **_kw):
        from PIL import Image
        import subprocess
        calls.append(args)
        if fail:
            raise subprocess.CalledProcessError(1, args)
        with Image.open(args[args.index("-i") + 1]) as im:
            im.resize((im.width * scale, im.height * scale)).save(args[args.index("-o") + 1])
    return run


def test_sharpen_makes_the_picture_bigger_up_to_the_cap(monkeypatch, tmp_path):
    from PIL import Image
    sharpener_here(monkeypatch, tmp_path)
    calls = []
    small = picture(tmp_path / "a.png")
    assert imagegen.sharpen(small, "Dark comic", run=fake_esrgan(calls))
    assert Image.open(small).size == (1463, 2560)   # 4x is 1536x2688: cut to SHARP_LONG
    assert calls[0][calls[0].index("-n") + 1] == imagegen.SHARP_MODEL
    assert not os.path.exists(small + ".x4.png")


def test_sharpen_leaves_photos_and_failures_alone(monkeypatch, tmp_path):
    from PIL import Image
    calls = []
    small = picture(tmp_path / "a.png")
    assert not imagegen.sharpen(small, "Dark comic", run=fake_esrgan(calls))   # not bundled
    sharpener_here(monkeypatch, tmp_path)
    assert not imagegen.sharpen(small, "Realistic photo", run=fake_esrgan(calls))
    assert not imagegen.sharpen(small, "Dark comic", run=fake_esrgan(calls, fail=True))
    assert Image.open(small).size == (384, 672) and len(calls) == 1


def test_a_made_picture_is_sharpened_but_a_changed_photo_is_not(monkeypatch, tmp_path):
    done = []
    monkeypatch.setattr(imagegen, "sharpen",
                        lambda path, look, strength: done.append((look, strength)) or True)
    said = []
    imagegen.make("a cat", (576, 1024), str(tmp_path / "a.png"), "Dark comic", speed="Fast",
                  launch=launcher([]), check=ok, memory=plenty, log=said.append)
    assert done == [("Dark comic", 1.0)] and any("Sharpened" in line for line in said)
    photo = picture(tmp_path / "photo.png")
    imagegen.make("a dog", (576, 1024), str(tmp_path / "b.png"), "Dark comic", speed="Fast",
                  launch=launcher([]), check=ok, memory=plenty, init=(photo, 0.6))
    assert done == [("Dark comic", 1.0)]


def test_fast_is_sharpened_fully_balanced_by_half_and_best_not_at_all(monkeypatch, tmp_path):
    done = []
    monkeypatch.setattr(imagegen, "sharpen",
                        lambda path, look, strength: done.append(strength) or True)
    for n, speed in enumerate(("Fast", "Balanced", "Best")):
        imagegen.make("a cat", (576, 1024), str(tmp_path / f"{n}.png"), "Dark comic",
                      speed=speed, launch=launcher([]), check=ok, memory=plenty)
    imagegen.make("a cat", (576, 1024), str(tmp_path / "cpu.png"), "Dark comic", speed="Best",
                  backend=imagegen.CPU, launch=launcher([]), check=ok, memory=plenty)
    assert done == [1.0, 0.5, 1.0]   # the processor draws at Fast size whatever was chosen


def test_half_strength_mixes_the_sharpened_and_the_plain_picture(monkeypatch, tmp_path):
    from PIL import Image
    sharpener_here(monkeypatch, tmp_path)

    def run(args, **_kw):   # "sharpened" = white, so the mix of white and red shows
        with Image.open(args[args.index("-i") + 1]) as im:
            Image.new("RGB", (im.width * 4, im.height * 4), "white").save(
                args[args.index("-o") + 1])

    small = picture(tmp_path / "a.png", (100, 100))
    assert imagegen.sharpen(small, "Dark comic", 0.5, run=run)
    assert Image.open(small).getpixel((10, 10)) == (255, 127, 127)   # half way, rounded down


# --- one picture server for a render ----------------------------------------------------

class FakeServer:
    """Stands in for _Server: draws by writing the file, or fails on ``fail``."""
    made, fail = [], False

    def __init__(self, exe, keep, say, launch):
        FakeServer.made.append(self)
        self.keep, self.closed, self.drawn = keep, False, []

    def draw(self, text, size, steps, seed, out_path, should_stop, began):
        if FakeServer.fail:
            raise OSError("connection refused")
        if should_stop and should_stop():
            raise imagegen.Stopped()
        self.drawn.append((size, steps, seed))
        with open(out_path, "wb") as fh:
            fh.write(b"png")

    def close(self):
        self.closed = True


@pytest.fixture
def server(monkeypatch, home):
    exe = home / "sd" / "sd-cli.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    (exe.parent / imagegen.SERVER).write_bytes(b"")
    monkeypatch.setattr(imagegen, "program_path", lambda: str(exe))
    monkeypatch.setattr(imagegen, "_Server", FakeServer)
    monkeypatch.setattr(imagegen, "sharpen", lambda path, look, strength=1.0: False)
    FakeServer.made, FakeServer.fail = [], False
    yield FakeServer
    imagegen.serve(False)


def test_pictures_in_a_render_share_one_server_and_it_stops_after(server, home):
    seen = []
    imagegen.serve()
    for n in range(3):
        made = make(home, seen, f"{n}.png", seed=5, speed=imagegen.FAST)
        assert made.backend == imagegen.GPU and os.path.isfile(home / f"{n}.png")
    assert seen == [] and len(server.made) == 1   # no sd-cli at all, one server
    assert server.made[0].drawn[0] == ((384, 672), 4, 5)
    imagegen.serve(False)
    assert server.made[0].closed


def test_the_server_keeps_the_drawing_part_only_above_16_gb(monkeypatch, server, home):
    for total, keep in ((32 * 2 ** 30, True), (16 * 2 ** 30, False), (None, False)):
        monkeypatch.setattr(imagegen.writer, "total_memory", lambda t=total: t)
        imagegen.serve()
        make(home)
        assert server.made[-1].keep is keep
        imagegen.serve(False)


def test_outside_a_render_and_for_photos_and_the_processor_sd_cli_is_used(server, home):
    seen = []
    make(home, seen)                                     # Try it / Make picture: no server
    imagegen.serve()
    make(home, seen, backend=imagegen.CPU)               # the processor
    make(home, seen, init=(picture(home / "photo.png"), 0.6))   # a redrawn photo
    assert len(seen) == 3 and server.made == []


def test_a_broken_server_falls_back_to_sd_cli_for_the_rest_of_the_render(monkeypatch, server,
                                                                          home):
    monkeypatch.setattr(imagegen.writer, "total_memory", lambda: 16 * 2 ** 30)   # nothing kept
    seen, said = [], []
    server.fail = True
    imagegen.serve()
    make(home, seen, "a.png", log=said.append)
    imagegen.serve()   # turned on again in the same render (pictures drawn ahead): still broken
    make(home, seen, "b.png")
    assert os.path.isfile(home / "a.png") and os.path.isfile(home / "b.png")
    assert len(seen) == 2 and len(server.made) == 1 and server.made[0].closed
    assert sum("picture server didn't work" in line for line in said) == 1
    imagegen.serve(False)
    imagegen.serve()   # the next render tries the server again
    server.fail = False
    make(home, seen, "c.png")
    assert len(seen) == 2 and len(server.made) == 2


def test_a_kept_server_short_of_card_memory_goes_on_reading_from_disk(monkeypatch, server,
                                                                       home):
    monkeypatch.setattr(imagegen.writer, "total_memory", lambda: 32 * 2 ** 30)
    seen, said = [], []
    server.fail = True
    imagegen.serve()
    make(home, seen, "a.png", log=said.append)      # kept server fails: sd-cli makes this one
    server.fail = False
    make(home, seen, "b.png", log=said.append)      # a new server, reading from disk
    assert len(seen) == 1 and [s.keep for s in server.made] == [True, False]
    assert any("ran short of memory" in line for line in said)
    imagegen.serve(False)
    imagegen.serve()
    make(home, seen, "c.png")                       # the next render tries keeping it again
    assert server.made[-1].keep is True


def test_stop_during_a_server_picture_stops_the_server(server, home):
    imagegen.serve()
    make(home)
    with pytest.raises(imagegen.Stopped):
        make(home, should_stop=lambda: True)
    assert server.made[0].closed and len(server.made) == 1


def test_the_real_server_class_asks_over_http_and_saves_the_png(monkeypatch, tmp_path):
    """_Server against a tiny local web server standing in for sd-server.exe."""
    import base64
    import http.server
    import json
    import threading

    asked = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"[]")

        def do_POST(self):
            asked.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps({"images": [base64.b64encode(b"PNGDATA").decode()]}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    started = []

    def launch(args, log_path):
        open(log_path, "w").write("ggml_vulkan: 0 = NVIDIA GeForce RTX 4060 (NVIDIA) | uma: 0\n")
        web = http.server.HTTPServer(("127.0.0.1", int(args[args.index("--listen-port") + 1])),
                                     Handler)
        threading.Thread(target=web.serve_forever, daemon=True).start()
        started.append((args, web))

        class Running:
            returncode = None

            def poll(self):
                return self.returncode

            def kill(self):
                web.shutdown()
                web.server_close()
                self.returncode = -9

            def wait(self):
                return self.returncode
        return Running()

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    said = []
    one = imagegen._Server("sd-server.exe", True, said.append, launch)
    args = started[0][0]
    assert args[args.index("--params-backend") + 1] == "te=disk,vae=disk"
    assert "RTX 4060" in said[0] and "kept loaded" in said[0]
    out = tmp_path / "p.png"
    one.draw("a cat", (384, 672), 4, 9, str(out), None, imagegen.time.monotonic())
    assert out.read_bytes() == b"PNGDATA"
    assert asked == [{"prompt": "a cat", "steps": 4, "cfg_scale": 1.0, "width": 384,
                      "height": 672, "seed": 9}]
    one.close()
    assert one.process.poll() == -9


def test_the_tiny_decoder_is_used_when_bundled_but_not_for_a_redrawn_photo(monkeypatch, tmp_path):
    tae = tmp_path / "taef1.safetensors"
    tae.write_bytes(b"")
    monkeypatch.setattr(writer, "bundled", lambda folder, name: str(tae) if (
        folder, name) == ("taef1", imagegen.TINY_DECODER) else None)
    drawn = imagegen.command("sd-cli.exe", "a cat", (384, 672), "o.png", 1)
    assert drawn[drawn.index("--taesd") + 1] == str(tae)
    redrawn = imagegen.command("sd-cli.exe", "a cat", (384, 672), "o.png", 1,
                               init=("photo.png", 0.6))
    assert "--taesd" not in redrawn
    monkeypatch.setattr(writer, "bundled", lambda folder, name: None)
    assert "--taesd" not in imagegen.command("sd-cli.exe", "a cat", (384, 672), "o.png", 1)
