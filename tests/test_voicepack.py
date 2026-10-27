"""The voice pack: who may install it, what it installs, and how the app talks to it."""
import io
import json
import os
import zipfile
from types import SimpleNamespace

import pytest

from vidgen import voicepack, voicepack_lock


def smi(text, code=0):
    return lambda *a, **k: SimpleNamespace(returncode=code, stdout=text)


def test_the_biggest_nvidia_card_is_read():
    out = "NVIDIA GeForce RTX 4060, 8188\nNVIDIA GeForce GT 710, 2048\n"
    assert voicepack.graphics(run=smi(out)) == ("NVIDIA GeForce RTX 4060", 8188)


def test_no_nvidia_card_or_no_driver_means_none():
    assert voicepack.graphics(run=smi("", code=9)) is None

    def missing(*a, **k):
        raise OSError("nvidia-smi not found")
    assert voicepack.graphics(run=missing) is None


@pytest.mark.parametrize("card, ok", [(("RTX 4060", 8188), True), (("RTX 3050", 6144), True),
                                      (("GTX 1650", 4096), False), (None, False)])
def test_only_cards_with_6_gb_or_more_may_use_it(card, ok):
    why = voicepack.usable(card)
    assert (why == "") is ok
    if card is None:
        assert "NVIDIA graphics card" in why
    elif not ok:
        assert "4 GB" in why


def test_every_package_is_pinned_with_a_checksum():
    lines = [line for line in voicepack_lock.LOCK.splitlines() if line.strip()]
    assert len(lines) > 50
    for line in lines:
        name, _, rest = line.partition(" @ ")
        assert name and rest.startswith("https://") and " --hash=sha256:" in rest, line
        assert len(rest.split("--hash=sha256:")[1]) == 64
    names = {line.split(" @ ")[0].lower() for line in lines}
    assert {"torch", "kittenml", "soundfile", "setuptools", "omegaconf"} <= names
    torch = next(line for line in lines if line.startswith("torch @"))
    assert "cu128" in torch and "cp312" in torch and "win_amd64" in torch


def test_only_the_known_source_package_is_built_on_the_pc():
    sources = [line for line in voicepack_lock.LOCK.splitlines() if ".tar.gz" in line]
    assert [line.split(" @ ")[0] for line in sources] == voicepack_lock.SOURCE_ONLY


def test_the_embeddable_python_is_told_to_use_its_site_packages(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("python.exe", b"MZ")
        archive.writestr("python312._pth", "python312.zip\n.\n#import site\n")
    zip_path = tmp_path / "py.zip"
    zip_path.write_bytes(data.getvalue())
    voicepack._unpack_python(str(zip_path))
    pth = (tmp_path / "AIVideoStudio" / "voicepack" / "python" / "python312._pth").read_text()
    assert "\nimport site" in pth and "Lib\\site-packages" in pth


def test_installed_needs_the_marker_of_this_pack_version(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert not voicepack.installed()
    os.makedirs(os.path.dirname(voicepack.python_exe()))
    open(voicepack.python_exe(), "w").close()
    with open(os.path.join(voicepack.folder(), "installed.json"), "w") as fh:
        json.dump({"version": "0"}, fh)
    assert not voicepack.installed()              # an older pack: installed again
    with open(os.path.join(voicepack.folder(), "installed.json"), "w") as fh:
        json.dump({"version": voicepack.PACK_VERSION}, fh)
    assert voicepack.installed()
    voicepack.remove()
    assert not os.path.exists(voicepack.folder())


class FakeSpeaker:
    """The pack's speak.py, faked: answers one JSON line per job."""

    def __init__(self, answers):
        self.answers, self.sent = list(answers), []
        self.stdin = SimpleNamespace(write=self.sent.append, flush=lambda: None, close=lambda: None)
        self.stdout = SimpleNamespace(readline=lambda: self.answers.pop(0) if self.answers else "")

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


@pytest.fixture
def installed_pack(monkeypatch):
    monkeypatch.setattr(voicepack, "installed", lambda: True)
    monkeypatch.setattr(voicepack, "model_dir", lambda: "model")


def test_the_speaker_loads_once_and_says_lines(installed_pack):
    fake = FakeSpeaker(['{"ready": true, "load_seconds": 26.3}\n',
                        '{"ok": true, "seconds": 2.8}\n'])
    with voicepack.Speaker(popen=lambda *a, **k: fake) as speaker:
        assert speaker.load_seconds == 26.3
        assert speaker.say("Hello.", "out.wav", "ref.wav", "Hi there.") == 2.8
    job = json.loads(fake.sent[0])
    assert job == {"text": "Hello.", "out": "out.wav", "reference": "ref.wav",
                   "reference_text": "Hi there."}


def test_a_failed_line_or_a_dead_speaker_is_a_clear_error(installed_pack):
    fake = FakeSpeaker(['{"ready": true}\n', '{"ok": false, "error": "CUDA out of memory"}\n'])
    speaker = voicepack.Speaker(popen=lambda *a, **k: fake)
    with pytest.raises(voicepack.PackError, match="out of memory"):
        speaker.say("Hello.", "out.wav", "ref.wav", "Hi.")
    with pytest.raises(voicepack.PackError, match="stopped"):
        speaker.say("Again.", "out.wav", "ref.wav", "Hi.")   # nothing more to read: it died


def test_a_speaker_that_never_starts_says_so(installed_pack):
    with pytest.raises(voicepack.PackError, match="could not start"):
        voicepack.Speaker(popen=lambda *a, **k: FakeSpeaker([]))


def test_no_pack_no_speaker(monkeypatch):
    monkeypatch.setattr(voicepack, "installed", lambda: False)
    with pytest.raises(voicepack.PackError, match="isn't installed"):
        voicepack.Speaker(popen=lambda *a, **k: None)
