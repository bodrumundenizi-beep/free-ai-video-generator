"""5.0 in the window: Make new video's photo rows, the Image editor page and Change with words
in the thumbnail editor."""

import pytest
from PIL import Image

import ai_video_studio as ai
from test_settings_tabs import app  # noqa: F401  (the window, shared)


def picture(tmp_path, name, colour):
    path = str(tmp_path / name)
    Image.new("RGB", (320, 180), colour).save(path)
    return path


def test_photos_are_added_once_and_read_for_the_writer(app, tmp_path):  # noqa: F811
    dialog = ai.DraftDialog(app)
    try:
        first = picture(tmp_path, "a.png", "red")
        dialog.add_photo(first, "A red wall")
        dialog.add_photo(first)                       # the same photo twice: once
        dialog.add_photo(picture(tmp_path, "b.png", "blue"))
        dialog.photos[1]["show"].set(False)
        found = dialog.photo_list()
        assert [(p.about, p.show) for p in found] == [("A red wall", True), ("", False)]
        dialog.photo_box.remove(dialog.photos[0])
        assert [p.path for p in dialog.photo_list()] == [found[1].path]
    finally:
        dialog.close()


def test_the_image_editor_page_is_in_the_sidebar(app, tmp_path):  # noqa: F811
    app.select_page("photo")
    editor = app.photo_editor
    editor.set_photo(picture(tmp_path, "c.png", "green"))
    assert editor.go.cget("state") == "normal" and editor.again.cget("state") == "disabled"
    editor._made(picture(tmp_path, "d.png", "black"), "A green field",
                 ai.imagegen.Made(12.0, ai.imagegen.GPU, (320, 180)))
    assert editor.shown and editor.about == "A green field"
    assert all(b.cget("state") == "normal" for b in editor.use_buttons)


def test_change_with_words_swaps_the_layer_picture_and_undo_puts_it_back(app, tmp_path,  # noqa: F811
                                                                      monkeypatch):
    from vidgen import thumbedit

    old, new = picture(tmp_path, "old.png", "red"), picture(tmp_path, "new.png", "blue")
    monkeypatch.setattr(ai, "FREE_THUMBS_DIR", str(tmp_path / "free"))
    monkeypatch.setattr(ai, "change_photo_window",
                        lambda _app, photo, _about, use, _title="": use(new))
    layers = thumbedit.blank((320, 180))
    layers.insert(0, {"kind": "background", "path": old, "hidden": False})
    editor = app._open_thumbs_editor(str(tmp_path / "free" / "x"), (320, 180), layers, "Test")
    editor.selected = 0
    editor.change_with_words()
    assert editor.layers[0]["path"] == new
    editor.undo()
    assert editor.layers[0]["path"] == old
    app._close_thumbs_editor()


def test_photos_on_make_new_video_go_to_the_writer(app, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(app, "_has_footage_key", lambda: True)
    monkeypatch.setattr(ai.DraftDialog, "analyse", lambda self: None)   # no model here
    app.door_box.insert("1.0", "A weekend on a farm.")
    app.door_photos.add(picture(tmp_path, "e.png", "yellow"), "A yellow field", show=False)
    try:
        app.make_from_text()
        dialog = next(w for w in app.winfo_children() if isinstance(w, ai.DraftDialog))
        assert [(p.about, p.show) for p in dialog.photo_list()] == [("A yellow field", False)]
        dialog.close()
    finally:
        app.door_box.delete("1.0", "end")
        app.door_photos.clear()
