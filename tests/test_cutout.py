"""5.2 piece 2: Remove background (vidgen.cutout, BiRefNet lite bundled)."""

import threading

import numpy as np
import pytest
from PIL import Image

from vidgen import cutout, thumbedit


class FakeSession:
    """Answers like the model: a subject (positive logits) in the middle of the picture.
    ``hang``: runs until onnxruntime is told to give up, like a long run that is stopped."""

    def __init__(self, hang=False):
        self.hang, self.seen = hang, []

    def get_inputs(self):
        return [type("Input", (), {"name": "input_image", "type": "tensor(float)"})()]

    def run(self, _names, feeds, options):
        self.seen.append(feeds["input_image"])
        while self.hang:
            if options.terminate:
                raise RuntimeError("Exiting due to terminate flag being set to true.")
            threading.Event().wait(0.01)
        logits = np.full((1, 1, cutout.SIDE, cutout.SIDE), -20.0, np.float32)
        logits[0, 0, 256:768, 256:768] = 20.0
        return [logits]


def test_the_photo_goes_in_at_the_models_size_normalised():
    x = cutout.prepare(Image.new("RGB", (300, 200), (255, 0, 128)))
    assert x.shape == (1, 3, cutout.SIDE, cutout.SIDE) and x.dtype == np.float32
    assert x[0, 0, 0, 0] == pytest.approx((1 - 0.485) / 0.229, abs=1e-4)
    assert x[0, 1, 0, 0] == pytest.approx((0 - 0.456) / 0.224, abs=1e-4)


def test_the_answer_comes_back_as_see_through_at_the_photos_size():
    logits = np.zeros((1, 1, 8, 8), np.float32)
    logits[0, 0, :4] = 30
    logits[0, 0, 4:] = -30
    alpha = cutout.alpha_from(logits, (40, 20))
    assert alpha.mode == "L" and alpha.size == (40, 20)
    assert alpha.getpixel((5, 1)) == 255 and alpha.getpixel((5, 18)) == 0


def test_a_cut_out_keeps_the_photo_and_its_size(tmp_path):
    photo, out = str(tmp_path / "p.jpg"), str(tmp_path / "out.png")
    Image.new("RGB", (640, 480), (10, 120, 200)).save(photo)
    cutout.cut_out(photo, out, session=FakeSession())
    with Image.open(out) as image:
        assert image.mode == "RGBA" and image.size == (640, 480)
        assert image.getpixel((320, 240))[3] == 255 and image.getpixel((5, 5))[3] == 0
        assert image.getpixel((5, 5))[:3] == image.getpixel((320, 240))[:3]   # colours kept


def test_stop_ends_a_run_part_way(tmp_path):
    photo = str(tmp_path / "p.png")
    Image.new("RGB", (64, 64)).save(photo)
    flag = threading.Event()
    threading.Timer(0.2, flag.set).start()
    with pytest.raises(cutout.Stopped):
        cutout.cut_out(photo, str(tmp_path / "o.png"), flag.is_set, session=FakeSession(True))
    assert not (tmp_path / "o.png").exists()


def test_a_missing_model_says_so_instead_of_crashing(tmp_path, monkeypatch):
    photo = str(tmp_path / "p.png")
    Image.new("RGB", (64, 64)).save(photo)
    cutout.release()
    monkeypatch.setattr(cutout, "model_path", lambda: None)
    with pytest.raises(cutout.CutoutError, match="isn't downloaded yet"):
        cutout.cut_out(photo, str(tmp_path / "o.png"))


def test_one_thread_per_fast_core():
    hybrid = [(1, 1 << i, True) for i in range(8)] + [(0, 1 << i, False) for i in range(8, 16)]
    assert cutout.fast_cores(hybrid) == 8
    assert cutout.fast_cores([(0, 1, True)] * 6) == 6
    assert cutout.fast_cores([]) == 0


def test_saving_keeps_see_through_as_png_and_puts_it_on_white_as_jpg(tmp_path):
    cut = Image.new("RGBA", (20, 20), (200, 0, 0, 0))   # see-through, red underneath
    cut.paste((0, 0, 255, 255), (5, 5, 15, 15))
    cut.save(tmp_path / "cut.png")
    thumbedit.export(str(tmp_path / "cut.png"), str(tmp_path / "a.png"))
    thumbedit.export(str(tmp_path / "cut.png"), str(tmp_path / "a.jpg"))
    with Image.open(tmp_path / "a.png") as png, Image.open(tmp_path / "a.jpg") as jpg:
        assert png.mode == "RGBA" and png.getpixel((0, 0))[3] == 0
        assert all(v > 245 for v in jpg.getpixel((0, 0)))     # white, not the red underneath
        assert jpg.getpixel((10, 10))[2] > 200
    assert cutout.has_see_through(cut) and not cutout.has_see_through(Image.new("RGB", (2, 2)))


def test_a_see_through_photo_keeps_its_see_through_and_its_hidden_colours_stay_hidden(tmp_path):
    from vidgen import imagegen

    photo = tmp_path / "cut.png"
    image = Image.new("RGBA", (640, 480), (255, 0, 0, 0))    # removed, red hidden underneath
    image.paste((0, 0, 255, 255), (200, 100, 440, 380))
    image.save(photo)
    out = tmp_path / "again.png"
    cutout.cut_out(str(photo), str(out), session=FakeSession())   # the model keeps the middle
    with Image.open(out) as again:
        assert again.getchannel("A").getpixel((300, 240)) == 255    # kept by both
        assert again.getchannel("A").getpixel((170, 240)) == 0      # model keeps it; was removed
        assert again.getpixel((10, 10))[:3] == (255, 255, 255)       # on white, not red
    assert imagegen.flatten(Image.open(photo)).getpixel((10, 10)) == (255, 255, 255)


def test_a_clear_subject_is_told_from_nothing_or_an_edge_strip():
    def kept(box, size=(1000, 600)):
        alpha = Image.new("L", size, 0)
        if box:
            alpha.paste(255, box)
        return cutout.clear_subject(alpha)

    assert kept((300, 100, 700, 500))            # a thing in front
    assert not kept(None)                        # nothing kept
    assert not kept((500, 300, 510, 310))        # a speck
    assert not kept((0, 0, 120, 600))            # a sidebar strip
    assert not kept((0, 540, 1000, 600))         # a strip along the bottom
    assert kept((0, 100, 400, 600))              # a person at the edge is still a subject
