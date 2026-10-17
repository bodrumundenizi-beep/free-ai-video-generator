from PIL import Image, ImageDraw

from vidgen import thumbnail


def test_a_detailed_frame_beats_a_flat_one():
    flat = Image.new("RGB", (160, 90), (40, 40, 40))
    busy = flat.copy()
    draw = ImageDraw.Draw(busy)
    for x in range(0, 160, 8):
        draw.line((x, 0, x, 90), fill="white")
    assert thumbnail.sharpness(busy) > thumbnail.sharpness(flat)


def test_frames_are_taken_away_from_the_start_and_end_cards():
    times = thumbnail.moments(30.0)
    assert len(times) == thumbnail.FRAMES
    assert min(times) > thumbnail.SKIP and max(times) < 30.0 - thumbnail.SKIP
    assert all(0 < t < 4 for t in thumbnail.moments(4.0))   # a very short video still works


def test_a_long_title_fits_three_lines():
    text = thumbnail.lines("one two three four five six seven eight nine ten eleven twelve "
                           "thirteen fourteen fifteen", 14)
    assert len(text) == 3 and text[-1].endswith("...") and all(len(t) <= 17 for t in text)
    assert thumbnail.lines("Hi", 14) == ["HI"]


def test_the_title_is_drawn_on_the_picture():
    image = Image.new("RGB", thumbnail.LANDSCAPE, (0, 0, 0))
    thumbnail.draw_title(image, "Three things")
    assert image.getbbox() is not None   # something white was drawn
