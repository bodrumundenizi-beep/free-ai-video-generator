"""Shared test setup: no test may touch the real settings, projects or recordings.

pytest loads this before any test file, so APPDATA points at a throwaway folder before
ai_video_studio (and vidgen.projects) read it at import time. A test that forgets to patch
a path can then only ever write here. Models stay in LOCALAPPDATA and are left alone.
"""

import atexit
import os
import shutil
import tempfile

_FOLDER = tempfile.mkdtemp(prefix="aivs-tests-appdata-")
os.environ["APPDATA"] = _FOLDER
atexit.register(shutil.rmtree, _FOLDER, ignore_errors=True)

import pytest  # noqa: E402

from vidgen import keyvault  # noqa: E402

# Nor the real Windows Credential Manager: from the start (before any test builds the app),
# keys go into a dict that each test starts empty. tests/test_keyvault.py's round trip uses
# the real functions, kept here, on purpose.
REAL_VAULT = {"get": keyvault.get, "put": keyvault.put, "delete": keyvault.delete}
_VAULT: dict = {}
keyvault.get = lambda name: _VAULT.get(name, "")
keyvault.put = lambda name, value: (_VAULT.__setitem__(name, value) if value
                                    else _VAULT.pop(name, None)) or True
keyvault.delete = lambda name: _VAULT.pop(name, None) or True


@pytest.fixture(autouse=True)
def _fake_keyvault():
    _VAULT.clear()
    yield _VAULT
