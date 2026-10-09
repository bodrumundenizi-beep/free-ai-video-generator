import hashlib
import json
import threading

import pytest
import requests

from vidgen import draft, pacing, voices, writer
from vidgen.script import parse_script
from vidgen.writer import Model, Session, Stopped, WriterError

TEXT = ("Your PC could be hoarding gigabytes of junk you will never use. "
        "Open Settings, go to System, then Storage, and turn on Storage Sense. "
        "It deletes temporary files automatically every week.")
PERSONA = voices.persona(draft.Audience().voice)


def scenes_json(lines, visual="laptop desk"):
    return json.dumps({"scenes": [{"visual": visual, "voice": line} for line in lines]})


SHORT = ["Your computer is hiding junk you never asked for.",
         "One switch in Settings clears it for you every week."]


# --- fakes -----------------------------------------------------------------------

class Reply:
    def __init__(self, content=None, status=200):
        self.status_code, self._content = status, content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class FakeModel:
    """Stands in for llama.cpp: a process that is alive, and an HTTP API that answers."""

    def __init__(self, answers=(), fail_health=False, on_post=None):
        self.answers, self.bodies = list(answers), []
        self.fail_health, self.on_post = fail_health, on_post
        self.alive, self.launched, self.killed = True, 0, 0
        self.gpus, self.gpu_broken = [], False

    # the process
    def launch(self, exe, model_file, port, gpu=False):
        self.launched += 1
        self.gpus.append(gpu)
        self.alive = not (gpu and self.gpu_broken)
        return self

    def poll(self):
        return None if self.alive else 1

    def kill(self):
        self.alive = False
        self.killed += 1

    def wait(self, timeout=None):
        return 0

    # the HTTP API
    def get(self, url, timeout=None):
        if self.fail_health:
            raise requests.ConnectionError()
        return Reply()

    def post(self, url, json=None, timeout=None):
        self.bodies.append(json)
        if self.on_post:
            self.on_post(self)
        if not self.alive:
            raise requests.ConnectionError()
        return Reply(self.answers.pop(0))


def session(model, should_stop=None, check=lambda: (True, ""), gpu=False):
    return Session(should_stop, launch=model.launch, http=model, check=check, gpu=gpu)


@pytest.fixture(autouse=True)
def no_real_files(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(writer, "runtime_path", lambda: "llama-server.exe")


# --- the prompt ------------------------------------------------------------------

def test_the_prompt_carries_the_audience_the_length_and_the_scene_count():
    audience = draft.Audience("Kids and family", "Kids", "TikTok")
    prompt = writer.build_prompt(audience, 66)
    assert audience.brief in prompt and "about 66 words" in prompt
    assert f"exactly {writer.scene_count(66)} scenes" in prompt and "Do not copy" in prompt


def test_the_schema_fixes_the_number_of_scenes():
    scenes = writer.script_schema(5)["properties"]["scenes"]
    assert scenes["minItems"] == scenes["maxItems"] == 5


def test_more_seconds_mean_more_words_and_a_faster_voice_means_more_too():
    slow = voices.persona("en-US-ChristopherNeural")
    assert writer.target_words(60, PERSONA) > writer.target_words(30, PERSONA) > 8
    assert writer.target_words(30, PERSONA) > writer.target_words(30, slow)
    assert writer.scene_count(5) == 2 and writer.scene_count(10_000) == writer.MAX_SCENES


# --- reading the answer ----------------------------------------------------------

def test_scenes_are_read_and_repeated_or_empty_lines_dropped():
    answer = json.dumps({"scenes": [
        {"visual": "laptop", "voice": " One  line. "}, {"visual": "desk", "voice": "one line."},
        {"visual": "desk", "voice": ""}, "nonsense", {"visual": "cup", "voice": "Two."}]})
    scenes = writer.parse_scenes(answer)
    assert [(s.visual, s.voice) for s in scenes] == [("laptop", "One line."), ("cup", "Two.")]


@pytest.mark.parametrize("answer", [
    "not json", "{}", '{"scenes": "x"}', '{"scenes": []}', '{"scenes": [{"visual": "a"}]}'])
def test_an_answer_that_cannot_be_used_is_a_writer_error(answer):
    with pytest.raises(WriterError):
        writer.parse_scenes(answer)


# --- writing ---------------------------------------------------------------------

def test_a_script_is_written_for_the_audience_and_length():
    model = FakeModel([scenes_json(SHORT)])
    audience = draft.Audience("Story and facts", "Adults", "YouTube long-form")
    with session(model) as s:
        result = s.write(TEXT, audience, "15 s")
    assert result.source == "Smart writer" and result.length == "15 s" and result.target == "15 s"
    assert result.voice == audience.voice and result.aspect == "16:9" and not result.dropped
    assert [s.voice for s in parse_script(result.script)] == SHORT
    body = model.bodies[0]
    assert body["messages"][0]["content"] == writer.build_prompt(
        audience, writer.target_words(15, voices.persona(audience.voice)))
    assert body["messages"][-1] == {"role": "user", "content": TEXT}
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["response_format"]["json_schema"]["schema"]["properties"]["scenes"]["minItems"] >= 2
    assert model.killed == 1  # the model does not stay in memory


def test_search_words_from_the_model_can_never_point_at_a_local_file():
    model = FakeModel([scenes_json(SHORT, visual="local: C:/secret.mp4")])
    with session(model) as s:
        result = s.write(TEXT, length="15 s")
    assert not any(scene.is_local for scene in parse_script(result.script))


def test_keep_everything_asks_for_as_many_words_as_the_text_has():
    model = FakeModel([scenes_json(SHORT)])
    with session(model) as s:
        result = s.write(TEXT, length=draft.KEEP_ALL)
    assert f"about {len(draft.words_in(TEXT))} words" in model.bodies[0]["messages"][0]["content"]
    assert result.target == pacing.DEFAULT_TARGET


def test_a_script_that_is_too_long_is_asked_for_again_and_then_cut():
    long_lines = [f"This is sentence number {n} and it goes on for quite a while longer." for n in range(12)]
    model = FakeModel([scenes_json(long_lines), scenes_json(long_lines)])
    with session(model) as s:
        result = s.write(TEXT, length="15 s")
    first, second = (body["messages"][0]["content"] for body in model.bodies)
    assert first != second and len(model.bodies) == 2  # the retry asked for fewer words
    assert draft.fits([scene.voice for scene in result.scenes], 15.0, PERSONA)
    assert result.scenes[0].voice == long_lines[0] and 0 < len(result.scenes) < 12


def test_nothing_pasted_is_refused_before_the_model_starts():
    model = FakeModel()
    with session(model) as s, pytest.raises(draft.DraftError):
        s.write("   ")
    assert model.launched == 0


def test_text_too_long_for_the_model_is_refused_with_a_word_limit():
    model = FakeModel()
    with session(model) as s, pytest.raises(WriterError, match="too long"):
        s.write("word " * 6000, length="30 s")
    assert model.bodies == []


# --- things that go wrong --------------------------------------------------------

def test_not_ready_says_why_and_starts_nothing():
    model = FakeModel()
    with session(model, check=lambda: (False, "Not downloaded.")) as s:
        with pytest.raises(WriterError, match="Not downloaded"):
            s.write(TEXT)
    assert model.launched == 0


def test_a_model_that_dies_while_loading_is_reported():
    model = FakeModel(fail_health=True)
    model.launch = lambda *a, **k: (setattr(model, "alive", False), model)[1]
    with session(model) as s, pytest.raises(WriterError, match="while loading"):
        s.write(TEXT)


def test_a_model_that_dies_while_writing_is_reported():
    model = FakeModel(on_post=lambda m: m.kill())
    with session(model) as s, pytest.raises(WriterError, match="before it finished"):
        s.write(TEXT)


def test_cancel_while_loading_stops_the_model():
    model = FakeModel(fail_health=True)
    with session(model, should_stop=lambda: True) as s, pytest.raises(Stopped):
        s.write(TEXT)
    assert model.killed >= 1 and not model.alive


def test_cancel_while_writing_stops_the_model():
    stop = threading.Event()
    model = FakeModel(on_post=lambda m: (stop.set(), m.kill()))
    with session(model, should_stop=stop.is_set) as s, pytest.raises(Stopped):
        s.write(TEXT)
    assert not model.alive


def test_a_slow_pc_is_given_up_on(monkeypatch):
    monkeypatch.setattr(writer, "WRITE_TIMEOUT", 0.05)
    gate = threading.Event()

    def slow(model):
        gate.wait(5)  # until the watcher gives up and stops the model

    model = FakeModel(on_post=slow)
    original_kill = model.kill
    model.kill = lambda: (original_kill(), gate.set())
    with session(model) as s, pytest.raises(WriterError, match="too slow"):
        s.write(TEXT)


def test_one_session_serves_several_requests_and_closes_once():
    model = FakeModel([json.dumps({"content": "Motivation", "age": "Teens"}), scenes_json(SHORT)])
    with session(model) as s:
        found = s.suggest(TEXT, draft.Audience(platform="TikTok"))
        s.write(TEXT, found.audience, found.length)
    assert model.launched == 1 and model.killed == 1
    assert found.audience == draft.Audience("Motivation", "Teens", "TikTok")  # platform is kept
    assert "motivation for teens" in found.reason and found.length in draft.LENGTHS


def test_a_suggestion_outside_the_lists_is_refused():
    model = FakeModel([json.dumps({"content": "Cooking", "age": "Adults"})])
    with session(model) as s, pytest.raises(WriterError):
        s.suggest(TEXT)


# --- the graphics card -----------------------------------------------------------

def test_the_graphics_card_is_only_used_when_asked_for(monkeypatch):
    commands = []
    monkeypatch.setattr(writer.subprocess, "Popen", lambda command, **kw: commands.append(command))
    writer._launch("llama-server.exe", "model.gguf", 1234)
    writer._launch("llama-server.exe", "model.gguf", 1234, gpu=True)
    off, on = (command[command.index("-ngl") + 1] for command in commands)
    assert off == "0" and on == writer.GPU_LAYERS != "0"


def test_a_graphics_card_that_cannot_be_used_falls_back_to_the_processor():
    model = FakeModel([scenes_json(SHORT)])
    model.gpu_broken = True
    with session(model, gpu=True) as s:
        result = s.write(TEXT, length="15 s")
        assert s.gpu_failed and not s.gpu
    assert model.gpus == [True, False] and result.source == "Smart writer"


def test_a_working_graphics_card_is_used_and_nothing_is_reported():
    model = FakeModel([scenes_json(SHORT)])
    with session(model, gpu=True) as s:
        s.write(TEXT, length="15 s")
        assert model.gpus == [True] and not s.gpu_failed


# --- being ready -----------------------------------------------------------------

def small_model(data=b"model-bytes" * 1000):
    return Model("Tiny", "tiny.gguf", "https://example.test/tiny.gguf", len(data),
                 hashlib.sha256(data).hexdigest(), "Apache 2.0"), data


def test_ready_needs_the_program_the_model_and_memory(monkeypatch):
    model, data = small_model()
    monkeypatch.setattr(writer, "MODEL", model)
    monkeypatch.setattr(writer, "installed", lambda m=model: False)
    assert writer.ready() == (False, "The Smart writer has not been downloaded yet.")
    monkeypatch.setattr(writer, "installed", lambda m=model: True)
    assert writer.ready(memory=lambda: 1_000_000_000)[1].startswith("This PC doesn't have enough")
    assert writer.ready(memory=lambda: 16_000_000_000) == (True, "")
    assert writer.ready(memory=lambda: None) == (True, "")  # cannot ask: try anyway
    monkeypatch.setattr(writer, "runtime_path", lambda: None)
    assert "missing" in writer.ready()[1]


# --- the download ----------------------------------------------------------------

class Download:
    """A server holding ``data`` that understands Range, or one that ignores it."""

    def __init__(self, data, ranges=True, error=None, cut_at=None):
        self.data, self.ranges, self.error, self.cut_at = data, ranges, error, cut_at
        self.asked = []

    def get(self, url, stream=None, timeout=None, headers=None):
        if self.error:
            raise self.error
        start = 0
        sent = (headers or {}).get("Range")
        self.asked.append(sent)
        if sent and self.ranges:
            start = int(sent.split("=")[1].rstrip("-"))
        self.status_code = 206 if (sent and self.ranges) else 200
        self.body = self.data[start:self.cut_at]
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=None):
        for i in range(0, len(self.body), 1000):
            yield self.body[i:i + 1000]


def test_download_saves_the_model_and_reports_progress():
    model, data = small_model()
    seen = []
    path = writer.download(lambda done, total: seen.append((done, total)), None, Download(data), model)
    assert open(path, "rb").read() == data and writer.installed(model)
    assert seen[-1] == (len(data), len(data)) and seen == sorted(seen)
    assert not writer.os.path.exists(path + ".part")


def test_download_is_skipped_when_the_model_is_already_there():
    model, data = small_model()
    writer.download(None, None, Download(data), model)
    server = Download(data)
    writer.download(None, None, server, model)
    assert server.asked == []


def test_a_stopped_download_carries_on_from_where_it_was():
    model, data = small_model()
    calls = []
    with pytest.raises(Stopped):
        writer.download(lambda d, t: calls.append(d), lambda: len(calls) >= 3, Download(data), model)
    part = writer.model_path(model) + ".part"
    have = writer.os.path.getsize(part)
    assert 0 < have < len(data) and not writer.installed(model)
    server = Download(data)
    writer.download(None, None, server, model)
    assert server.asked == [f"bytes={have}-"] and writer.installed(model)
    assert open(writer.model_path(model), "rb").read() == data


def test_a_server_that_ignores_resume_starts_the_file_again():
    model, data = small_model()
    writer.os.makedirs(writer.models_dir())
    open(writer.model_path(model) + ".part", "wb").write(data[:3000])
    writer.download(None, None, Download(data, ranges=False), model)
    assert open(writer.model_path(model), "rb").read() == data


@pytest.mark.parametrize("server_data", [b"x" * 11000, None])
def test_a_file_that_is_wrong_or_short_is_rejected_and_removed(server_data):
    model, data = small_model()
    server = Download(server_data or data, cut_at=None if server_data else 5000)
    with pytest.raises(WriterError, match="intact"):
        writer.download(None, None, server, model)
    assert not writer.installed(model)
    assert not writer.os.path.exists(writer.model_path(model) + ".part")


def test_no_connection_is_a_plain_message():
    model, _data = small_model()
    with pytest.raises(WriterError, match="internet"):
        writer.download(None, None, Download(b"", error=requests.ConnectionError()), model)


def test_remove_deletes_the_model_and_any_unfinished_download():
    model, data = small_model()
    writer.download(None, None, Download(data), model)
    open(writer.model_path(model) + ".part", "wb").write(b"x")
    writer.remove(model)
    assert not writer.installed(model) and writer.os.listdir(writer.models_dir()) == []
    writer.remove(model)  # nothing there: no error
