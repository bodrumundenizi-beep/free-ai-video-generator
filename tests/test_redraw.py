"""5.0 piece 2: a photo changed with words (imagegen.redraw)."""

import os

import pytest
from PIL import Image

from test_imagegen import FakeProcess, launcher, ok, plenty
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


def painter(seen):
    """A fake program that paints its whole picture blue and notes what it was given."""
    def launch(args, log_path):
        open(log_path, "w").close()
        size = int(args[args.index("-W") + 1]), int(args[args.index("-H") + 1])
        if "--mask" in args:
            with Image.open(args[args.index("--mask") + 1]) as given:
                seen.append({"args": args, "mask": given.size,
                             "start": Image.open(args[args.index("-i") + 1]).convert("RGB")})
        else:
            seen.append({"args": args})
        Image.new("RGB", size, (0, 0, 255)).save(args[args.index("-o") + 1])
        return FakeProcess(args, write=False)
    return launch


def ball(tmp_path):
    """A white photo with a painted square in the middle (white = may change)."""
    path, mask = str(tmp_path / "ball.png"), str(tmp_path / "mask.png")
    Image.new("RGB", (800, 600), "white").save(path)
    painted = Image.new("L", (400, 300), 0)   # the view's size, not the photo's
    painted.paste(255, (150, 100, 250, 200))
    painted.save(mask)
    return path, mask


def masked(tmp_path, seen, photo, mask, change="make the ball blue", **kwargs):
    return imagegen.redraw(photo, change, str(tmp_path / "out.png"), mask=mask, check=ok,
                           launch=painter(seen), memory=plenty, verify=lambda _stop: [],
                           **kwargs)


def test_the_mask_is_given_only_when_an_area_is_painted(tmp_path, photo):
    seen = []
    redraw(tmp_path, photo, seen)
    assert "--mask" not in seen[0].args
    args = imagegen.command("sd", "p", (512, 512), "o.png", 1, init=("i.png", 0.7), mask="m.png")
    assert args[args.index("--mask") + 1] == "m.png"
    assert "--mask" not in imagegen.command("sd", "p", (512, 512), "o.png", 1, mask="m.png")


def test_only_the_painted_part_changes_and_the_rest_is_pixel_identical(tmp_path):
    photo, mask = ball(tmp_path)
    seen = []
    made = masked(tmp_path, seen, photo, mask, amount=imagegen.SOME)
    args = seen[0]["args"]
    assert seen[0]["mask"] == (int(args[args.index("-W") + 1]), int(args[args.index("-H") + 1]))
    assert args[args.index("--strength") + 1] == "0.75"   # more than a whole-photo change
    with Image.open(str(tmp_path / "out.png")) as out:
        assert out.size == (800, 600) == made.size          # the photo's own size
        assert out.getpixel((400, 300)) == (0, 0, 255)       # the painted middle changed
        assert out.getpixel((20, 20)) == out.getpixel((780, 580)) == (255, 255, 255)
    assert sorted(os.listdir(tmp_path)) == ["ball.png", "mask.png", "out.png", "out.png.log"]


def test_a_colour_word_tints_the_painted_part_first(tmp_path):
    photo, mask = ball(tmp_path)
    seen = []
    masked(tmp_path, seen, photo, mask)
    start = seen[0]["start"]
    red, green, blue = start.getpixel((start.width // 2, start.height // 2))
    assert blue > 200 and red < 80                           # the white middle given as blue
    assert start.getpixel((2, 2)) == (255, 255, 255)         # the rest as it was
    seen = []
    masked(tmp_path, seen, photo, mask, change="make it a basketball")
    start = seen[0]["start"]
    assert start.getpixel((start.width // 2, start.height // 2)) == (255, 255, 255)
    assert imagegen.colour_in("Make the ball Blue!") == imagegen.COLOURS["blue"]
    assert imagegen.colour_in("redo the sky") is None


def test_nothing_painted_says_paint_first(tmp_path):
    photo, mask = ball(tmp_path)
    Image.new("L", (400, 300), 0).save(mask)
    with pytest.raises(imagegen.ImageError, match="Paint the part to change first"):
        masked(tmp_path, [], photo, mask)


def test_without_the_writer_the_words_are_used_as_typed():
    def broken(_about, _change):
        raise writer.WriterError("not downloaded")

    assert imagegen.redraw_prompt("  make it  blue ", "a car", broken) == "make it blue"
    with pytest.raises(imagegen.ImageError, match="Say what to change"):
        imagegen.redraw_prompt("  ")


def test_the_touch_up_redraws_lightly_without_a_colour_hint(tmp_path):
    photo, mask = ball(tmp_path)
    seen = []
    masked(tmp_path, seen, photo, mask, change="a red floor, a natural photo",
           amount=imagegen.TOUCH_UP)
    args, start = seen[0]["args"], seen[0]["start"]
    assert args[args.index("--strength") + 1] == "0.30"
    assert start.getpixel((start.width // 2, start.height // 2)) == (255, 255, 255)   # no tint
