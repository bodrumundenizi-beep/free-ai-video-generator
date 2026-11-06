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
