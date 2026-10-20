import numpy as np
from PIL import Image, ImageDraw

from vidgen import drawn


def picture():
    image = Image.new("RGB", (90, 160), (200, 180, 160))
    ImageDraw.Draw(image).rectangle((20, 40, 70, 120), fill=(40, 60, 200))
    return image


def test_blackboard_is_chalk_lines_on_a_dark_board():
    finished, sketch, order = drawn.prepare(picture(), (90, 160), drawn.BLACKBOARD)
    assert finished.shape == (160, 90, 3) and finished.dtype == np.uint8
    assert finished[5, 5].mean() < 60            # board
    assert finished.max() > 180                  # chalk somewhere: the square's edges


def test_drawing_starts_empty_draws_lines_then_ends_in_colour():
    finished, sketch, order = drawn.prepare(picture(), (90, 160), drawn.DRAWING)
    start = drawn.frame_at(0.0, finished, sketch, order, drawn.DRAWING)
    middle = drawn.frame_at(drawn.DRAW_SECONDS * 0.4, finished, sketch, order, drawn.DRAWING)
    end = drawn.frame_at(drawn.DRAW_SECONDS + 1, finished, sketch, order, drawn.DRAWING)
    assert (start == drawn.PAPER.astype(np.uint8)).all()   # nothing drawn yet
    assert not (middle == start).all() and (end == finished).all()
    assert end[80, 45, 2] > end[80, 45, 0]                 # the blue square is blue


def test_the_drawing_goes_top_to_bottom_in_blocks():
    order = drawn.draw_order((160, 90, 3))
    assert 0 <= order.min() and order.max() <= 1
    assert order[:20].mean() < order[-20:].mean()
