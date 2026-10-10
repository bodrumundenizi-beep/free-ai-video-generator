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


def launcher(seen, **kwargs):
    def launch(args, log_path):
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write("loading\n[E] out of memory\n")
        seen.append(FakeProcess(args, **kwargs))
        return seen[-1]
    return launch


def ok():
    return True, ""


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(imagegen.time, "sleep", lambda _s: None)
    return tmp_path


# --- making a picture ----------------------------------------------------------------

def test_make_runs_the_program_with_the_prompt_the_look_and_the_size(home):
    seen = []
    out = str(home / "scene.png")
    took = imagegen.make("a robot  holding a clipboard.", (576, 1024), out, "3D render",
                         seed=7, launch=launcher(seen), check=ok)
    args = seen[0].args
    assert took >= 0 and os.path.isfile(out)
    assert args[args.index("-p") + 1] == "a robot holding a clipboard, " + imagegen.LOOKS["3D render"]
    assert args[args.index("-W") + 1:args.index("-W") + 4] == ["576", "-H", "1024"]
    assert args[args.index("-s") + 1] == "7"
    assert args[args.index("--llm") + 1] == writer.model_path()  # the Smart writer's own file
    assert args[args.index("--diffusion-model") + 1].endswith(imagegen.MODEL.file)


def test_each_picture_gets_its_own_seed_unless_one_is_given(home):
    seen = []
    for _ in range(2):
        imagegen.make("a cat", (576, 1024), str(home / "a.png"), launch=launcher(seen), check=ok)
    seeds = [p.args[p.args.index("-s") + 1] for p in seen]
    assert seeds[0] != seeds[1]


def test_cancel_kills_the_program_at_once(home):
    seen = []
    with pytest.raises(imagegen.Stopped):
        imagegen.make("a cat", (576, 1024), str(home / "a.png"), should_stop=lambda: True,
                      launch=launcher(seen, polls=50), check=ok)
    assert seen[0].killed


def test_a_failed_run_says_what_the_program_said(home):
    with pytest.raises(ImageError, match="could not be made .*out of memory"):
        imagegen.make("a cat", (576, 1024), str(home / "a.png"),
                      launch=launcher([], code=1), check=ok)
    with pytest.raises(ImageError, match="could not be made"):  # ran, but wrote nothing
        imagegen.make("a cat", (576, 1024), str(home / "b.png"),
                      launch=launcher([], write=False), check=ok)


def test_too_slow_is_stopped(home, monkeypatch):
    clock = iter(range(0, 100000, 500))
    monkeypatch.setattr(imagegen.time, "monotonic", lambda: next(clock))
    seen = []
    with pytest.raises(ImageError, match="too long"):
        imagegen.make("a cat", (576, 1024), str(home / "a.png"),
                      launch=launcher(seen, polls=50), check=ok)
    assert seen[0].killed


def test_no_description_and_not_ready_are_image_errors(home):
    with pytest.raises(ImageError, match="no description"):
        imagegen.make(" ,. ", (576, 1024), str(home / "a.png"), launch=launcher([]), check=ok)
    with pytest.raises(ImageError, match="not downloaded yet"):
        imagegen.make("a cat", (576, 1024), str(home / "a.png"), launch=launcher([]))


def test_long_descriptions_are_cut_and_unknown_looks_use_the_default():
    text = imagegen.full_prompt("word " * 200, "Oil painting")
    assert len(text) < imagegen.MAX_PROMPT + 80
    assert text.endswith(imagegen.LOOKS[imagegen.DEFAULT_LOOK])


def test_size_follows_the_video_shape():
    assert imagegen.size_for("9:16") == (576, 1024) and imagegen.size_for("16:9") == (1024, 576)
    assert imagegen.size_for("1:1") == (576, 1024)


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
    assert "AI images tab" in imagegen.ready()[1]
    monkeypatch.setattr(imagegen, "installed", lambda: True)
    assert "Smart writer's file" in imagegen.ready()[1]
    monkeypatch.setattr(writer, "installed", lambda model=None: True)
    assert "enough free memory" in imagegen.ready(lambda: 2_000_000_000)[1]
    assert imagegen.ready(lambda: 16_000_000_000) == (True, "")
    assert imagegen.ready(lambda: None) == (True, "")


def test_download_failure_speaks_of_ai_images(home, monkeypatch):
    def failing(*_args):
        raise writer.WriterError("The Smart writer could not be downloaded.")

    monkeypatch.setattr(writer, "download", failing)
    with pytest.raises(ImageError, match="AI images could not be downloaded"):
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


def test_quick_split_can_ask_for_pictures_too():
    from vidgen import draft

    text = "Open the clipboard history in the settings menu. A boy walked along the river bank."
    script = draft.write(text, length=draft.KEEP_ALL, cards=False, ai=draft.AI_UNFILMABLE).script
    assert script.count("Visual: ai: ") == 1 and "river" in script.split("Visual: ")[2]
    assert "ai:" not in draft.write(text, length=draft.KEEP_ALL).script
