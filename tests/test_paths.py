import os

import pytest

from vidgen import paths
from vidgen.paths import (
    SaveError, deliver, existing_dir, is_writable, repair_output_path, usable_output,
)


def blocked(tmp_path):
    """A folder that can't exist: its parent is a file. Stands in for a
    protected or read-only folder, and behaves the same on every system."""
    wall = tmp_path / "not_a_folder"
    wall.write_text("x")
    return str(wall / "Captures")


# --- is_writable -----------------------------------------------------------------

def test_a_missing_folder_is_created_and_counts_as_writable(tmp_path):
    folder = tmp_path / "Videos" / "Captures"
    assert not folder.exists()
    assert is_writable(str(folder)) is True
    assert folder.is_dir()
    assert list(folder.iterdir()) == []  # the test file is cleaned up


def test_a_folder_that_refuses_files_is_not_writable(tmp_path):
    assert is_writable(blocked(tmp_path)) is False


@pytest.fixture
def write_denied(tmp_path):
    """A real folder that exists and lists, but refuses new files - what folder
    protection looks like to a program. Windows only (needs icacls)."""
    import subprocess

    folder = tmp_path / "Protected"
    folder.mkdir()
    account = subprocess.run(["whoami"], capture_output=True, text=True).stdout.strip()
    rule = f"{account}:(OI)(CI)(WD,AD)"
    subprocess.run(["icacls", str(folder), "/deny", rule], capture_output=True, check=True)
    yield str(folder)
    subprocess.run(["icacls", str(folder), "/remove:d", account], capture_output=True)


@pytest.mark.skipif(os.name != "nt", reason="uses Windows permissions")
def test_a_write_protected_folder_is_detected_at_once(write_denied):
    import time

    assert os.path.isdir(write_denied)
    assert os.access(write_denied, os.W_OK)  # the check the app used to rely on is fooled
    started = time.perf_counter()
    assert is_writable(write_denied) is False
    assert time.perf_counter() - started < 2  # no thousand-retry hang


@pytest.mark.skipif(os.name != "nt", reason="uses Windows permissions")
def test_a_write_protected_folder_falls_back(write_denied, tmp_path):
    good = str(tmp_path / "Downloads")
    path, moved = usable_output(os.path.join(write_denied, "final_video.mp4"), [good])
    assert (path, moved) == (os.path.join(good, "final_video.mp4"), True)
    source = tmp_path / "output.mp4"
    source.write_bytes(b"video")
    said = []
    result = deliver(str(source), os.path.join(write_denied, "clip.mp4"), [good], said.append)
    assert result == os.path.join(good, "clip.mp4") and len(said) == 1


# --- choosing where to save ------------------------------------------------------

def test_a_missing_output_folder_gets_created_and_used(tmp_path):
    target = str(tmp_path / "new" / "deeper" / "final_video.mp4")
    assert usable_output(target, []) == (target, False)
    assert os.path.isdir(os.path.dirname(target))


def test_an_unwritable_folder_falls_back_to_the_next_candidate(tmp_path):
    target = os.path.join(blocked(tmp_path), "final_video.mp4")
    also_blocked = os.path.join(blocked(tmp_path), "deeper")
    good = str(tmp_path / "Desktop")
    path, moved = usable_output(target, [also_blocked, good, str(tmp_path / "Later")])
    assert moved is True
    assert path == os.path.join(good, "final_video.mp4")  # same name, first working folder
    assert not (tmp_path / "Later").exists()  # stopped at the first that works


def test_nowhere_writable_is_a_clear_error(tmp_path):
    folder = blocked(tmp_path)
    with pytest.raises(SaveError) as error:
        usable_output(os.path.join(folder, "final_video.mp4"), [folder])
    assert str(error.value) == (
        f"Could not save the video to {folder}. Choose another folder in Settings.")


# --- delivering the finished file ------------------------------------------------

def finished_video(tmp_path):
    source = tmp_path / "work" / "output.mp4"
    source.parent.mkdir()
    source.write_bytes(b"video")
    return str(source)


def test_the_finished_file_is_moved_into_place(tmp_path):
    source = finished_video(tmp_path)
    target = str(tmp_path / "Videos" / "clip.mp4")
    assert deliver(source, target, []) == target
    assert open(target, "rb").read() == b"video" and not os.path.exists(source)


def test_delivery_replaces_a_video_the_user_chose_to_overwrite(tmp_path):
    source = finished_video(tmp_path)
    target = tmp_path / "clip.mp4"
    target.write_bytes(b"old")
    deliver(source, str(target), [])
    assert target.read_bytes() == b"video"


def test_delivery_falls_back_and_says_so(tmp_path):
    source = finished_video(tmp_path)
    folder = blocked(tmp_path)
    good = tmp_path / "Downloads"
    good.mkdir()
    (good / "clip.mp4").write_bytes(b"earlier")  # never replaced in a fallback folder
    said = []
    result = deliver(source, os.path.join(folder, "clip.mp4"), [str(good)], said.append)
    assert result == str(good / "clip (2).mp4")
    assert (good / "clip.mp4").read_bytes() == b"earlier"
    assert said == [f"⚠️ Couldn't save to {folder} - using {good} instead."]


def test_delivery_with_nowhere_to_go_keeps_the_finished_file(tmp_path):
    source = finished_video(tmp_path)
    folder = blocked(tmp_path)
    with pytest.raises(SaveError):
        deliver(source, os.path.join(folder, "clip.mp4"), [])
    assert os.path.exists(source)


# --- stale settings --------------------------------------------------------------

def test_a_stale_saved_folder_is_reset_to_the_default(tmp_path):
    default = str(tmp_path / "Videos")
    stale = str(tmp_path / "Videos" / "Captures" / "final_video.mp4")
    assert repair_output_path(stale, default) == os.path.join(default, "final_video.mp4")


def test_a_saved_folder_that_still_exists_is_kept(tmp_path):
    saved = str(tmp_path / "my clip.mp4")
    assert repair_output_path(saved, "anything") == saved


@pytest.mark.parametrize("saved", ["", None, "   ", "final_video.mp4"])
def test_an_empty_or_folderless_setting_is_reset(saved, tmp_path):
    default = str(tmp_path)
    assert repair_output_path(saved, default) == os.path.join(default, "final_video.mp4")


# --- dialogs ---------------------------------------------------------------------

def test_existing_dir_skips_folders_that_are_gone(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    assert existing_dir(str(tmp_path / "gone"), "", None, str(real)) == str(real)


def test_existing_dir_ends_at_the_home_folder(tmp_path):
    assert existing_dir(str(tmp_path / "gone")) == os.path.expanduser("~")


# --- the real folders ------------------------------------------------------------

def test_safe_folders_end_in_the_temp_folder_and_start_somewhere_real():
    folders = paths.safe_folders()
    assert os.path.isdir(folders[0])
    assert folders[-1].endswith(os.path.join("AIVideoStudio", "Videos"))


# --- versions --------------------------------------------------------------------

def test_version_one_keeps_its_name_and_two_is_labelled():
    assert paths.version_path(r"C:\Videos\clip.mp4", 1) == r"C:\Videos\clip.mp4"
    assert paths.version_path(r"C:\Videos\clip.mp4", 2) == r"C:\Videos\clip (version 2).mp4"
    assert paths.version_path("take.one.MOV", 2) == "take.one (version 2).MOV"

