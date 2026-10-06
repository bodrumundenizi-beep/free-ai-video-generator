"""Where files go, and what to do when they can't go there.

A folder that exists is not a folder that can be written. Windows "Controlled
folder access" (ransomware protection, common on school and work PCs) lets an
unknown program list Videos, Documents and Desktop but refuses its writes -
and reports the refusal as "file not found". ``os.access`` does not see it.
The only reliable test is to write a file, so that is what ``is_writable``
does.

Two rules follow from that:

* The final video is encoded in the temp folder and then moved into place by
  this process. ffmpeg, a separate program with its own permissions, never
  writes to the user's folders.
* If the chosen folder refuses the file, the video goes to the next safe
  folder and the Log says where. A finished render is never thrown away
  because of a folder.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
import tempfile
import uuid

CSIDL_DOCUMENTS = 5
CSIDL_VIDEOS = 14
CSIDL_DESKTOP = 16


class SaveError(Exception):
    """The video could not be saved anywhere. The message is for the user."""


def known_folder(csidl: int, fallback: str) -> str:
    """A shell folder, honouring a OneDrive redirect, with a home-dir fallback.

    Asking Windows means a redirected or renamed folder is found whatever it
    is called: "Videos" is "Vidéos" in French and may live under OneDrive.
    SHGetFolderPathW is superseded by SHGetKnownFolderPath, but it is still
    present on Windows 11, it follows folder redirection, and it needs no GUID
    struct - which makes it the cheapest correct option here.
    """
    if sys.platform == "win32":
        try:
            buffer = ctypes.create_unicode_buffer(260)
            # SHGFP_TYPE_CURRENT = 0
            if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buffer) == 0:
                if buffer.value and os.path.isdir(buffer.value):
                    return buffer.value
        except Exception:
            pass
    guess = os.path.join(os.path.expanduser("~"), fallback)
    return guess if os.path.isdir(guess) else os.path.expanduser("~")


def is_writable(folder: str) -> bool:
    """Whether a file can really be created in ``folder``, creating it if needed."""
    # One direct attempt. Not tempfile.mkstemp: on Windows it takes "access
    # denied" for a name clash and retries thousands of times, which hangs for
    # minutes on exactly the folders this function exists to detect.
    probe = os.path.join(folder, f".write_test_{os.getpid()}_{uuid.uuid4().hex[:8]}")
    try:
        os.makedirs(folder, exist_ok=True)
        with open(probe, "xb"):
            pass
    except OSError:
        return False
    try:
        os.remove(probe)
    except OSError:
        pass
    return True


def safe_folders() -> list[str]:
    """Places to try, in order, when the chosen folder refuses the video.

    Downloads comes before the temp folder: folder protection leaves it alone,
    and people can find it.
    """
    folders = [known_folder(CSIDL_VIDEOS, "Videos"), known_folder(CSIDL_DESKTOP, "Desktop")]
    downloads = os.path.join(os.path.expanduser("~"), "Downloads")
    if os.path.isdir(downloads):
        folders.append(downloads)
    folders.append(os.path.join(tempfile.gettempdir(), "AIVideoStudio", "Videos"))
    return folders


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def cannot_save_message(folder: str) -> str:
    return f"Could not save the video to {folder}. Choose another folder in Settings."


def usable_output(path: str, candidates) -> tuple[str, bool]:
    """(``path``, False) if its folder takes files, else (a fallback path, True).

    The fallback keeps the file name and sits in the first of ``candidates``
    that is writable. SaveError if nowhere is.
    """
    path = os.path.abspath(path)
    folder = os.path.dirname(path)
    if is_writable(folder):
        return path, False
    for candidate in candidates:
        if not _same(candidate, folder) and is_writable(candidate):
            return os.path.join(candidate, os.path.basename(path)), True
    raise SaveError(cannot_save_message(folder))


def next_free_path(path: str) -> str:
    """``path`` if nothing is there yet, else the first free "name (2).ext", "name (3).ext"...

    The way Windows and browsers number downloads, so a new render never
    replaces an earlier video.
    """
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    number = 2
    while os.path.exists(f"{stem} ({number}){ext}"):
        number += 1
    return f"{stem} ({number}){ext}"


def deliver(finished: str, destination: str, candidates, log=lambda message: None) -> str:
    """Move the ``finished`` file to ``destination``; returns where it ended up.

    If the destination refuses it - protected, full, or open in a player - the
    file goes to the first of ``candidates`` that accepts it, under a name
    that is free there, and ``log`` is told. SaveError if nothing accepts it;
    the finished file is then left where it was.
    """
    destination = os.path.abspath(destination)
    folder = os.path.dirname(destination)
    try:
        os.makedirs(folder, exist_ok=True)
        shutil.move(finished, destination)
        return destination
    except OSError:
        pass
    for candidate in candidates:
        if _same(candidate, folder):
            continue
        try:
            os.makedirs(candidate, exist_ok=True)
            target = next_free_path(os.path.join(candidate, os.path.basename(destination)))
            shutil.move(finished, target)
        except OSError:
            continue
        log(f"⚠️ Couldn't save to {folder} - using {candidate} instead.")
        return target
    raise SaveError(cannot_save_message(folder))


def repair_output_path(saved, default_dir: str, name: str = "final_video.mp4") -> str:
    """``saved`` if its folder still exists, else the default path.

    Settings travel: a profile copied to another PC, a removed drive, a folder
    deleted since. A path into a folder that is gone is replaced rather than
    kept for every later render to trip over.
    """
    saved = str(saved or "").strip()
    folder = os.path.dirname(saved)
    if not saved or not folder or not os.path.isdir(folder) or not os.path.basename(saved):
        return os.path.join(default_dir, name)
    return saved


def existing_dir(*folders) -> str:
    """The first of ``folders`` that exists, else the home folder. For dialogs."""
    for folder in folders:
        if folder and os.path.isdir(folder):
            return folder
    return os.path.expanduser("~")
