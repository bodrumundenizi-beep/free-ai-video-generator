"""4.9 piece 3: background effects, glow and outline, snapping."""

from PIL import Image, ImageFilter, ImageStat

from vidgen import thumbedit, thumbnail

SIZE = thumbnail.LANDSCAPE


def _stripes(tmp_path):
    path = tmp_path / "stripes.png"
    image = Image.new("RGB", (640, 360), (200, 200, 200))
    for x in range(0, 640, 20):
        image.paste((20, 20, 20), (x, 0, x + 10, 360))
    image.save(path)
    return str(path)


def _detail(image):
    return ImageStat.Stat(image.convert("L").filter(ImageFilter.FIND_EDGES)).mean[0]


def _light(image):
    return ImageStat.Stat(image.convert("L")).mean[0]


def test_blur_darken_brightness_and_contrast_change_the_background(tmp_path):
    plain = {"kind": "background", "path": _stripes(tmp_path)}
    base = thumbedit.render([plain], SIZE)
    assert _detail(thumbedit.render([dict(plain, blur=12)], SIZE)) < _detail(base) / 2
    assert _light(thumbedit.render([dict(plain, darken=50)], SIZE)) < _light(base) * 0.6
    assert _light(thumbedit.render([dict(plain, brightness=40)], SIZE)) > _light(base)
    flat = thumbedit.render([dict(plain, contrast=-50)], SIZE).convert("L")
    assert ImageStat.Stat(flat).stddev[0] < ImageStat.Stat(base.convert("L")).stddev[0]
    small = thumbedit.render([dict(plain, blur=12)], SIZE, 0.5)   # the preview blurs as much
    assert small.size == (640, 360) and _detail(small) < _detail(base) / 2


def _yellow(image, box):
    return sum(1 for p in image.crop(box).getdata() if p[0] > 200 and p[1] > 180 and p[2] < 90)


def test_glow_on_text_and_a_sticker_paints_around_it():
    dark = [{"kind": "background", "path": ""}]
    text = thumbedit.new_text("HI", size=150, color="#ffffff", outline=0)
    left, top, right, bottom = thumbedit.box(text, SIZE)
    around = (left - 30, top - 30, right + 30, bottom + 30)
    assert _yellow(thumbedit.render(dark + [text], SIZE), around) == 0
    glowing = dict(text, glow=20, glow_color="#ffd400")
    assert _yellow(thumbedit.render(dark + [glowing], SIZE), around) > 200
    sticker = thumbedit.new_sticker("⭐", SIZE)
    left, top, right, bottom = thumbedit.box(sticker, SIZE)
    ring = (left - 25, top - 25, right + 25, bottom + 25)
    before = _yellow(thumbedit.render(dark + [sticker], SIZE), ring)
    after = _yellow(thumbedit.render(dark + [dict(sticker, glow=15, glow_color="#ffd400")],
                                     SIZE), ring)
    assert after > before + 200


def test_an_outline_goes_around_a_stickers_shape():
    dark = [{"kind": "background", "path": ""}]
    sticker = thumbedit.new_sticker("\U0001F525", SIZE)
    left, top, right, bottom = thumbedit.box(sticker, SIZE)
    whites = lambda image: sum(1 for p in image.crop((left - 15, top - 15, right + 15,
                                                      bottom + 15)).getdata() if min(p) > 230)
    plain = whites(thumbedit.render(dark + [sticker], SIZE))
    outlined = whites(thumbedit.render(dark + [dict(sticker, outline=8)], SIZE))
    assert outlined > plain + 500


def test_layers_without_the_new_keys_still_render(tmp_path):
    """An edit saved before piece 3 has no blur, glow or outline: nothing changes."""
    layers = thumbedit.automatic(_stripes(tmp_path), "Old edit", SIZE)
    assert thumbedit.render(layers, SIZE).size == SIZE


def test_snapping_to_the_centre():
    assert thumbedit.snapped(0.503, 1280, 8) == 0.5     # 4 pixels off: snaps
    assert thumbedit.snapped(0.52, 1280, 8) == 0.52     # 26 pixels off: stays
    assert thumbedit.snapped(0.49, 720, 8) == 0.5
