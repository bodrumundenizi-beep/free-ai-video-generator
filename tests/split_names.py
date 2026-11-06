"""Swapping a name for a test, now that the windows live in src/gui/.

ai_video_studio and each gui module hold their own copy of a moved or shared name
(SETTINGS_DIR, GuideBox, Dialog...), so a test swaps every copy: what one swap did when it
was all one file.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import ai_video_studio  # noqa: E402
from gui import base, dialogs, photo_windows, thumb_editor, tips  # noqa: E402

MODULES = (ai_video_studio, base, tips, dialogs, photo_windows, thumb_editor)


def everywhere(patch, name, value):
    """``patch.setattr`` on every module that has ``name``; it must exist somewhere."""
    found = [m for m in MODULES if hasattr(m, name)]
    assert found, f"no module has {name}"
    for module in found:
        patch.setattr(module, name, value)
