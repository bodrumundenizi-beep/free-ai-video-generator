import os

from vidgen.render import next_free_path


def touch(path):
    path.write_bytes(b"")
    return str(path)


def test_a_free_path_is_used_as_is(tmp_path):
    target = str(tmp_path / "final_video.mp4")
    assert next_free_path(target) == target


def test_an_existing_file_gets_the_next_number(tmp_path):
    target = touch(tmp_path / "final_video.mp4")
    assert next_free_path(target) == str(tmp_path / "final_video (2).mp4")
    touch(tmp_path / "final_video (2).mp4")
    assert next_free_path(target) == str(tmp_path / "final_video (3).mp4")


def test_gaps_are_filled_from_the_lowest_number(tmp_path):
    target = touch(tmp_path / "clip.mp4")
    touch(tmp_path / "clip (3).mp4")
    assert next_free_path(target) == str(tmp_path / "clip (2).mp4")


def test_a_name_that_already_ends_in_a_number_still_works(tmp_path):
    target = touch(tmp_path / "clip (2).mp4")
    assert next_free_path(target) == str(tmp_path / "clip (2) (2).mp4")


def test_folder_and_extension_are_kept(tmp_path):
    folder = tmp_path / "My Videos.final"
    folder.mkdir()
    target = touch(folder / "take.one.MOV")
    result = next_free_path(target)
    assert os.path.dirname(result) == str(folder)
    assert os.path.basename(result) == "take.one (2).MOV"
