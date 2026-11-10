"""5.2 piece A: Erase (vidgen.eraser, LaMa bundled) and its touch-up."""

import threading

import numpy as np
import pytest
from PIL import Image

from vidgen import eraser, imagegen


class FakeLama:
    """Answers like LaMa, in 0-255: the hole filled with plain green.
    ``hang``: runs until onnxruntime is told to give up."""

    def __init__(self, hang=False):
        self.hang, self.seen = hang, []

    def run(self, _names, feeds, options):
        self.seen.append({k: v.shape for k, v in feeds.items()})
        while self.hang:
            if options.terminate:
                raise RuntimeError("Exiting due to terminate flag being set to true.")
            threading.Event().wait(0.01)
        out = feeds["image"][0] * 255
        out[:, feeds["mask"][0, 0] > 0.5] = np.array([0, 200, 0], np.float32)[:, None]
        return [out[None]]


def photo_and_mask(tmp_path, size=(1200, 800), box=(500, 300, 700, 500)):
    photo, mask = str(tmp_path / "p.png"), str(tmp_path / "m.png")
    rng = np.random.default_rng(1)
    Image.fromarray(rng.integers(0, 255, (size[1], size[0], 3), np.uint8)).save(photo)
    painted = Image.new("L", (size[0] // 2, size[1] // 2), 0)    # painted at the view's size
    painted.paste(255, tuple(v // 2 for v in box))
    painted.save(mask)
    return photo, mask


def test_the_hole_reaches_past_the_painting_and_the_square_is_tight(tmp_path):
    painted = Image.new("L", (1000, 1000), 0)
    painted.paste(255, (400, 400, 600, 600))
    hard, soft = eraser.hole(painted, (1000, 1000))
    x0, y0, x1, y1 = hard.getbbox()
    assert x0 < 400 and x1 > 600 and (x1 - x0) < 260         # past the soft edge, not far
    assert soft.getpixel((500, 500)) == 255 and soft.getpixel((10, 10)) == 0
    assert eraser.square((400, 400, 600, 600), (1000, 1000)) == (244, 244, 756, 756)   # >= 512
    left, top, right, bottom = eraser.square((100, 100, 900, 900), (1000, 1000))
    assert right - left == bottom - top == 1000                # 1.4x, kept inside the photo
    assert eraser.hole(Image.new("L", (10, 10), 0), (10, 10)) is None


def test_only_the_hole_changes_and_the_rest_is_pixel_identical(tmp_path):
    photo, mask = photo_and_mask(tmp_path)
    out = str(tmp_path / "o.png")
    lama = FakeLama()
    seconds, soft = eraser.erase(photo, mask, out, session=lama)
    assert lama.seen == [{"image": (1, 3, 512, 512), "mask": (1, 1, 512, 512)}]
    with Image.open(photo) as before, Image.open(out) as after:
        assert after.size == before.size == soft.size
        assert after.getpixel((600, 400)) == (0, 200, 0)          # the middle of the hole
        b, a, s = (np.asarray(x) for x in (before.convert("RGB"), after.convert("RGB"), soft))
        assert (a[s == 0] == b[s == 0]).all()                     # outside: untouched


def test_stop_ends_a_run_part_way_and_a_missing_model_says_so(tmp_path, monkeypatch):
    photo, mask = photo_and_mask(tmp_path)
    flag = threading.Event()
    threading.Timer(0.2, flag.set).start()
    with pytest.raises(eraser.Stopped):
        eraser.erase(photo, mask, str(tmp_path / "o.png"), flag.is_set, session=FakeLama(True))
    eraser.release()
    monkeypatch.setattr(eraser, "model_path", lambda: None)
    with pytest.raises(eraser.EraseError, match="isn't downloaded yet"):
        eraser.erase(photo, mask, str(tmp_path / "o.png"))
    Image.new("L", (10, 10), 0).save(mask)
    with pytest.raises(eraser.EraseError, match="Paint over what to erase"):
        eraser.erase(photo, mask, str(tmp_path / "o.png"), session=FakeLama())


def test_the_painted_share_and_the_grain_added_only_inside(tmp_path):
    _photo, mask = photo_and_mask(tmp_path, box=(0, 0, 1200, 400))
    assert 0.45 < eraser.share(mask) < 0.55
    rng = np.random.default_rng(2)
    grainy = Image.fromarray(rng.integers(100, 160, (200, 200, 3), np.uint8))
    smooth = Image.new("RGB", (200, 200), (130, 130, 130))
    soft = Image.new("L", (200, 200), 0)
    soft.paste(255, (60, 60, 140, 140))
    out = np.asarray(eraser.grain(grainy, smooth, soft), np.float32)
    assert 3 < out[100, 60:140].std() <= 6                      # noise added in the hole, capped
    assert (out[10, :] == 130).all()                             # none outside


def test_the_touch_up_describes_around_the_hole_and_redraws_only_it(tmp_path, monkeypatch):
    photo, mask = photo_and_mask(tmp_path)
    filled = str(tmp_path / "filled.png")
    _s, soft = eraser.erase(photo, mask, filled, session=FakeLama())
    asked, described = [], []

    def fake_redraw(src, words, out, amount, mask=None, **kwargs):
        asked.append((src, words, amount, Image.open(mask).getbbox() == soft.getbbox()))
        Image.open(src).save(out)
        return imagegen.Made(20.0, imagegen.GPU, (1200, 800))

    def describe(path):
        with Image.open(path) as around:
            described.append(around.size)
        return "a grey stone wall"

    monkeypatch.setattr(imagegen, "redraw", fake_redraw)
    eraser.touch_up(photo, filled, soft, str(tmp_path / "real.png"), describe)
    (src, words, amount, same_hole), = asked
    assert src == filled and amount == imagegen.TOUCH_UP and same_hole
    assert words.startswith("a grey stone wall")
    x0, y0, x1, y1 = soft.getbbox()
    assert described[0][0] > x1 - x0                             # it saw around the hole too
    assert sorted(p.name for p in tmp_path.iterdir()) == ["filled.png", "m.png", "p.png", "real.png"]
