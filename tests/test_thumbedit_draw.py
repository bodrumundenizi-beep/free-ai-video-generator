"""4.9 piece 2: shapes, stickers, photos, and the handles' resize and turn."""

import os

import pytest
from PIL import Image

from test_footage import FakeResponse, FakeSession
from vidgen import footage, render, thumbedit, thumbnail

SIZE = thumbnail.LANDSCAPE
BLANK = [{"kind": "background", "path": ""}]   # the plain dark canvas


def _red_outside(image, box, margin=3):
    """Red pixels drawn outside ``box`` (and how many inside)."""
    left, top, right, bottom = box
    outside = inside = 0
    for (x, y), p in zip(((x, y) for y in range(image.height) for x in range(image.width)),
                         image.getdata()):
        if p[0] > 150 and p[1] < 90:
            if left - margin <= x <= right + margin and top - margin <= y <= bottom + margin:
                inside += 1
            else:
                outside += 1
    return outside, inside


@pytest.mark.parametrize("shape", ["box", "circle", "arrow"])
@pytest.mark.parametrize("tilt", [0, 30])
def test_each_shape_draws_inside_its_box(shape, tilt):
    layer = thumbedit.new_shape(shape, 0.4, 0.5, 0.3, 0.4, tilt=tilt, width=16)
    image = thumbedit.render(BLANK + [layer], SIZE)
    outside, inside = _red_outside(image, thumbedit.box(layer, SIZE))
    assert inside > 500 and outside == 0


def test_brush_follows_its_points_and_stays_in_its_box():
    points = [(200, 200), (400, 260), (600, 200), (700, 500)]
    layer = thumbedit.brush(points, SIZE, width=12)
    image = thumbedit.render(BLANK + [layer], SIZE)
    outside, inside = _red_outside(image, thumbedit.box(layer, SIZE))
    assert inside > 500 and outside == 0
    for x, y in points:   # the stroke passes through every point
        assert image.getpixel((x, y))[0] > 150


def test_a_sticker_renders_in_colour_in_its_box():
    layer = thumbedit.new_sticker("\U0001F525", SIZE)
    left, top, right, bottom = thumbedit.box(layer, SIZE)
    image = thumbedit.render(BLANK + [layer], SIZE).crop((left, top, right, bottom))
    colours = {p for p in image.getdata() if max(p) - min(p) > 60}
    assert len(colours) > 20


def test_every_sticker_draws_something():
    for emoji in thumbedit.STICKERS:
        drawn = thumbedit._emoji(emoji)
        assert drawn.getbbox() and min(drawn.size) > 20, hex(ord(emoji))


def test_an_image_layer_keeps_its_shape_and_draws(tmp_path):
    path = tmp_path / "tall.png"
    Image.new("RGB", (300, 600), (10, 200, 10)).save(path)
    layer = thumbedit.new_image(str(path), SIZE, "Pexels · Ana · https://pexels.com/p/1")
    assert layer["w"] * SIZE[0] / (layer["h"] * SIZE[1]) == pytest.approx(0.5, abs=0.01)
    image = thumbedit.render(BLANK + [layer], SIZE)
    assert image.getpixel((640, 360))[1] > 150
    assert thumbedit.credits(BLANK + [layer]) == ["Pexels · Ana · https://pexels.com/p/1"]
    assert thumbedit.credits(BLANK + [dict(layer, hidden=True)]) == []


def test_resize_and_turn_change_the_layer():
    shape = thumbedit.new_shape("circle", 0.5, 0.5, 0.2, 0.2)
    before = thumbedit.box(shape, SIZE)
    thumbedit.resize(shape, 2)
    after = thumbedit.box(shape, SIZE)
    assert (after[2] - after[0]) == pytest.approx(2 * (before[2] - before[0]), abs=2)
    text = thumbedit.new_text(size=100)
    assert thumbedit.resize(text, 1.5)["size"] == 150
    assert thumbedit.resize(text, 100)["size"] == 480   # kept to a sane size
    assert thumbedit.turned(190) == -170 and thumbedit.turned(-30) == -30
    wide = thumbedit.new_shape("box", 0.5, 0.5, 0.4, 0.05)
    flat_h = thumbedit.box(wide, SIZE)[3] - thumbedit.box(wide, SIZE)[1]
    wide["tilt"] = 90
    assert thumbedit.box(wide, SIZE)[3] - thumbedit.box(wide, SIZE)[1] > 3 * flat_h


def test_hit_finds_shapes_and_stickers_not_the_background():
    sticker = thumbedit.new_sticker("⭐", SIZE)
    layers = BLANK + [thumbedit.new_shape("box", 0.5, 0.5, 0.5, 0.5), sticker]
    assert thumbedit.hit(layers, SIZE, (640, 360)) == 1
    left, top, right, bottom = thumbedit.box(sticker, SIZE)
    assert thumbedit.hit(layers, SIZE, ((left + right) / 2, (top + bottom) / 2)) == 2
    assert thumbedit.hit(layers, SIZE, (5, 5)) is None


PEXELS_PHOTOS = {"photos": [
    {"id": 11, "width": 4000, "height": 3000, "url": "https://www.pexels.com/photo/beans-11/",
     "photographer": "Ana", "alt": "coffee beans",
     "src": {"large2x": "https://p/11-big.jpg", "medium": "https://p/11-mid.jpg",
             "tiny": "https://p/11-small.jpg"}},
    {"id": 12, "src": {}},   # no picture: skipped
]}
PIXABAY_PHOTOS = {"hits": [
    {"id": 21, "imageWidth": 1920, "imageHeight": 1280, "pageURL": "https://pixabay.com/p-21/",
     "user": "Bo", "tags": "coffee, cup", "largeImageURL": "https://x/21-big.jpg",
     "previewURL": "https://x/21-small.jpg"},
]}


def test_photo_answers_of_both_sites_are_parsed():
    pexels = footage.parse_pexels_photos(PEXELS_PHOTOS)
    assert [(c.id, c.url, c.thumb) for c in pexels] == [
        ("photo-11", "https://p/11-big.jpg", "https://p/11-small.jpg")]
    assert pexels[0].credit() == "Pexels · Ana · https://www.pexels.com/photo/beans-11/"
    pixabay = footage.parse_pixabay_photos(PIXABAY_PHOTOS)
    assert [(c.id, c.url, c.thumb, c.creator) for c in pixabay] == [
        ("photo-21", "https://x/21-big.jpg", "https://x/21-small.jpg", "Bo")]
    assert pexels[0].key != "Pexels:11"   # never the same key as a clip


def test_photo_search_asks_both_photo_apis_and_mixes_them():
    session = FakeSession({
        (footage.PEXELS_PHOTO_URL, "coffee beans"): FakeResponse(200, PEXELS_PHOTOS),
        (footage.PIXABAY_PHOTO_URL, "coffee beans"): FakeResponse(200, PIXABAY_PHOTOS),
    })
    search = footage.FootageSearch("xk", "pk", 1280, 720, "landscape", "1080p",
                                   log=lambda m: None, session=session)
    found = search.photos("  coffee   beans ")
    assert [c.key for c in found] == ["Pexels:photo-11", "Pixabay:photo-21"]
    pixabay_params = [p for p in session.params if "image_type" in p][0]
    assert pixabay_params["orientation"] == "horizontal" and pixabay_params["q"] == "coffee beans"


def test_photo_search_with_one_key_asks_one_site():
    session = FakeSession({(footage.PIXABAY_PHOTO_URL, "cup"): FakeResponse(200, PIXABAY_PHOTOS)})
    search = footage.FootageSearch("", "pk", 720, 1280, "portrait", "1080p",
                                   log=lambda m: None, session=session)
    assert [c.provider for c in search.photos("cup")] == ["Pixabay"]
    assert session.params[0]["orientation"] == "vertical"


class _Log:
    def __init__(self):
        self.lines = []

    def log(self, line):
        self.lines.append(line)


def test_the_render_credits_the_thumbnails_photos(tmp_path):
    photo = tmp_path / "photo.jpg"
    Image.new("RGB", (400, 300), (200, 200, 0)).save(photo)
    folder = str(tmp_path / "project")
    layers = BLANK + [thumbedit.new_image(str(photo), SIZE, "Pixabay · Bo · https://px/1")]
    thumbedit.save(folder, layers, SIZE)
    video = str(tmp_path / "video.mp4")
    credits = tmp_path / "video.credits.txt"
    credits.write_text("Footage credits for video.mp4\n\nPexels · Ana · https://p/1\n",
                       encoding="utf-8")
    render._thumbnail({"project_dir": folder, "aspect": "16:9"}, _Log(), video, [])
    text = credits.read_text(encoding="utf-8")
    assert "Pexels · Ana" in text and "Thumbnail photos:\nPixabay · Bo · https://px/1" in text
    assert os.path.isfile(tmp_path / "video.thumbnail.jpg")
