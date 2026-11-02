import os

from PIL import Image

from vidgen import render, thumbedit, thumbnail


def _picture(tmp_path, name="scene.png", color=(200, 30, 30)):
    path = tmp_path / name
    Image.new("RGB", (640, 360), color).save(path)
    return str(path)


def test_render_background_and_text_gives_the_size(tmp_path):
    layers = thumbedit.automatic(_picture(tmp_path), "Five coffee facts", thumbnail.LANDSCAPE)
    assert [layer["kind"] for layer in layers] == ["background", "text"]
    image = thumbedit.render(layers, thumbnail.LANDSCAPE)
    assert image.size == (1280, 720) and image.mode == "RGB"
    assert image.getpixel((20, 20)) == (200, 30, 30)        # the background fills it
    assert thumbedit.render(layers, thumbnail.PORTRAIT, 0.25).size == (180, 320)


def test_text_is_drawn_where_it_is_and_hidden_layers_are_not(tmp_path):
    text = thumbedit.new_text("HI", x=0.25, y=0.5, size=200, color="#00ff00", outline=0)
    layers = [{"kind": "background", "path": _picture(tmp_path)}, text]
    left, top, right, bottom = thumbedit.box(text, thumbnail.LANDSCAPE)
    assert left < 320 < right and top < 360 < bottom
    greens = lambda image: sum(1 for p in image.crop((left, top, right, bottom)).getdata()
                               if p[1] > 200 and p[0] < 80)
    assert greens(thumbedit.render(layers, thumbnail.LANDSCAPE)) > 100
    text["hidden"] = True
    assert greens(thumbedit.render(layers, thumbnail.LANDSCAPE)) == 0


def test_tilt_and_shadow_change_the_box_and_hit_finds_the_top_layer(tmp_path):
    flat = thumbedit.new_text("WIDE WORDS", size=120)
    tilted = dict(flat, tilt=30)
    w = lambda b: b[2] - b[0]
    h = lambda b: b[3] - b[1]
    assert h(thumbedit.box(tilted, thumbnail.LANDSCAPE)) > h(thumbedit.box(flat, thumbnail.LANDSCAPE))
    assert w(thumbedit.box(dict(flat, shadow=True), thumbnail.LANDSCAPE)) > \
        w(thumbedit.box(flat, thumbnail.LANDSCAPE))
    layers = [{"kind": "background", "path": ""}, flat, dict(flat, text="TOP")]
    assert thumbedit.hit(layers, thumbnail.LANDSCAPE, (640, 360)) == 2
    assert thumbedit.hit(layers, thumbnail.LANDSCAPE, (5, 5)) is None


def test_save_and_load_round_trip_keeps_the_background(tmp_path):
    scene = _picture(tmp_path)
    folder = str(tmp_path / "project")
    layers = thumbedit.automatic(scene, "Coffee", thumbnail.LANDSCAPE)
    layers[1]["color"] = "#ffd400"
    picture = thumbedit.save(folder, layers, thumbnail.LANDSCAPE)
    assert os.path.isfile(picture) and Image.open(picture).size == (1280, 720)
    os.remove(scene)   # the scene pictures' cache was cleared: the copy is used
    loaded, size = thumbedit.load(folder)
    assert size == thumbnail.LANDSCAPE and loaded[1] == layers[1]
    assert os.path.dirname(loaded[0]["path"]) == os.path.join(folder, thumbedit.FILES)
    assert os.path.isfile(loaded[0]["path"])
    assert thumbedit.edited(folder, portrait=False) == picture
    assert thumbedit.edited(folder, portrait=True) is None   # the other shape: automatic
    thumbedit.remove(folder)
    assert thumbedit.load(folder) is None and thumbedit.edited(folder, False) is None


def test_undo_redo_and_one_step_per_drag():
    first = [thumbedit.new_text("A")]
    history = thumbedit.History(first)
    moved = [dict(first[0], x=0.3)]
    history.record(moved, key="move")
    history.record([dict(first[0], x=0.2)], key="move")   # the same drag: one step
    history.record([dict(first[0], text="B")], key="text")
    assert history.undo()[0]["x"] == 0.2
    assert history.undo()[0]["x"] == 0.5
    assert history.undo() is None
    assert history.redo()[0]["x"] == 0.2
    history.record([dict(first[0], color="#ff0000")])
    assert history.redo() is None   # a new change drops what was undone


class _Log:
    def __init__(self):
        self.lines = []

    def log(self, line):
        self.lines.append(line)


def test_render_copies_an_edited_thumbnail(tmp_path):
    folder = str(tmp_path / "project")
    thumbedit.save(folder, thumbedit.automatic(_picture(tmp_path, color=(10, 200, 10)), "Hi",
                                               thumbnail.PORTRAIT), thumbnail.PORTRAIT)
    video = str(tmp_path / "video.mp4")
    ui = _Log()
    render._thumbnail({"project_dir": folder, "aspect": "9:16"}, ui, video, [])
    made = str(tmp_path / "video.thumbnail.jpg")
    assert os.path.isfile(made) and "your edit" in ui.lines[-1]
    with Image.open(made) as image:
        assert image.size == (720, 1280)
        r, g, b = image.getpixel((10, 10))
        assert g > 150 and r < 60
