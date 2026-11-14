"""API keys live in Windows Credential Manager, not in settings.json."""
import json
import sys
import uuid

import pytest

from gui import base
from vidgen import keyvault


def test_keys_are_moved_out_of_an_old_settings_file(tmp_path, monkeypatch, _fake_keyvault):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"api_key": "pexels-secret", "pixabay_key": "pixa-secret",
                                "tour_seen": True}), encoding="utf-8")
    monkeypatch.setattr(base, "SETTINGS_FILE", str(path))
    monkeypatch.setattr(base, "SETTINGS_DIR", str(tmp_path))
    data = base.load_settings()
    assert data["api_key"] == "pexels-secret" and data["pixabay_key"] == "pixa-secret"
    assert _fake_keyvault == {"api_key": "pexels-secret", "pixabay_key": "pixa-secret"}
    text = path.read_text(encoding="utf-8")
    assert "pexels-secret" not in text and "pixa-secret" not in text


def test_saving_never_writes_a_key_to_the_file(tmp_path, monkeypatch, _fake_keyvault):
    monkeypatch.setattr(base, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(base, "SETTINGS_DIR", str(tmp_path))
    settings = base.default_settings() | {"api_key": "new-key"}
    base.save_settings(settings)
    assert "new-key" not in (tmp_path / "settings.json").read_text(encoding="utf-8")
    assert settings["api_key"] == "new-key"              # the caller's copy is untouched
    assert base.load_settings()["api_key"] == "new-key"  # and it comes back from the vault
    base.save_settings(settings | {"api_key": ""})       # cleared in Settings: gone
    assert "api_key" not in _fake_keyvault


def test_a_key_stays_in_the_file_if_credential_manager_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(keyvault, "put", lambda name, value: False)
    monkeypatch.setattr(base, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(base, "SETTINGS_DIR", str(tmp_path))
    base.save_settings(base.default_settings() | {"api_key": "keep-me"})
    assert "keep-me" in (tmp_path / "settings.json").read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_the_real_credential_manager_round_trip(monkeypatch):
    from conftest import REAL_VAULT
    for fn, real in REAL_VAULT.items():
        monkeypatch.setattr(keyvault, fn, real)
    name = "test-" + uuid.uuid4().hex
    try:
        assert keyvault.get(name) == ""
        assert keyvault.put(name, "s3cret-ü")
        assert keyvault.get(name) == "s3cret-ü"
        assert keyvault.put(name, "") and keyvault.get(name) == ""
    finally:
        keyvault.delete(name)
