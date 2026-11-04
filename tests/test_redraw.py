"""5.0 piece 2: a photo changed with words (imagegen.redraw)."""

import os

import pytest
from PIL import Image

from test_imagegen import launcher, ok, plenty
from vidgen import imagegen, writer


@pytest.fixture
def photo(tmp_path):
    path = str(tmp_path / "tall photo.jpg")
    Image.new("RGB", (600, 900), "pink").save(path)
    return path


def redraw(tmp_path, photo, seen, **kwargs):
    return imagegen.redraw(photo, kwargs.pop("change", "make it a chocolate cake"),
                           str(tmp_path / "out.png"), check=ok, launch=launcher(seen),
                           memory=plenty, verify=lambda _stop: [], **kwargs)


def test_the_photo_is_given_in_its_own_shape_with_the_amount(tmp_path, photo):
    seen = []
    made = redraw(tmp_path, photo, seen, amount=imagegen.A_LOT)
    args = seen[0].args
    start = args[args.index("-i") + 1]
    assert args[args.index("--strength") + 1] == "0.75"
    assert args[args.index("--steps") + 1] == "12"   # finer amounts than the usual 8
    width, height = made.size
    assert height > width and width % 16 == 0 and height % 16 == 0   # the photo's shape
    assert args[args.index("-W") + 1] == str(width)
    assert not os.path.exists(start)                    # the resized copy is cleaned up
    assert "chocolate cake" in args[args.index("-p") + 1]


def test_each_amount_draws_more_of_it_again():
    assert imagegen.AMOUNTS[imagegen.LITTLE] < imagegen.AMOUNTS[imagegen.SOME] \
        < imagegen.AMOUNTS[imagegen.A_LOT] <= 1


def test_the_size_keeps_about_the_pixels_asked_for():
    width, height = imagegen.shaped_like((4000, 3000), (1024, 576))
    assert abs(width / height - 4 / 3) < 0.05 and abs(width * height - 1024 * 576) < 40_000


def test_a_photo_that_cannot_be_read_says_so(tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"no")
    with pytest.raises(imagegen.ImageError, match="photo could not be read"):
        redraw(tmp_path, str(bad), [])


def test_the_writer_joins_the_description_and_the_change():
    joined = imagegen.redraw_prompt("make it chocolate", "A strawberry cake on a plate",
                                    lambda about, change: f"{about}, but {change}")
    assert joined == "A strawberry cake on a plate, but make it chocolate"
    assert imagegen.redraw_prompt("make it chocolate") == "make it chocolate"


def test_without_the_writer_the_words_are_used_as_typed():
    def broken(_about, _change):
        raise writer.WriterError("not downloaded")

    assert imagegen.redraw_prompt("  make it  blue ", "a car", broken) == "make it blue"
    with pytest.raises(imagegen.ImageError, match="Say what to change"):
        imagegen.redraw_prompt("  ")
