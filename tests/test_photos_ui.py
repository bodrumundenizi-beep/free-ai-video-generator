"""5.0 in the window: Make new video's photo rows, the Image editor page and Change with words
in the thumbnail editor."""

import time
from types import SimpleNamespace

import pytest
from PIL import Image

import ai_video_studio as ai
from split_names import everywhere  # noqa: E402
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


def stroke(editor, fx, fy):
    """A mouse press at (fx, fy) of the shown photo, as fractions of its width and height."""
    view = editor.before_view
    width, height = view.shown
    x = view.winfo_rootx() + (view.winfo_width() - width) / 2 + fx * width
    y = view.winfo_rooty() + (view.winfo_height() - height) / 2 + fy * height
    event = SimpleNamespace(x_root=x, y_root=y)
    editor._paint_start(event)
    editor._paint_end(event)


def area_editor(app, tmp_path):  # noqa: F811
    app.select_page("photo")
    editor = app.photo_editor
    path = str(tmp_path / "big.png")
    Image.new("RGB", (1600, 900), "white").save(path)
    editor.set_photo(path)
    app.update()
    editor._show(editor.before_view, path)   # sized to its box now that it is laid out
    return editor


def test_painting_marks_the_area_at_the_photos_size_and_clear_removes_it(app, tmp_path):  # noqa: F811
    editor = area_editor(app, tmp_path)
    assert not editor.mask.getbbox()
    stroke(editor, 0.5, 0.5)
    assert editor.mask.size == (1600, 900)
    assert editor.mask.getpixel((800, 450)) == 255 and editor.mask.getpixel((20, 20)) == 0
    editor.tool.set(editor.RUB_OUT)
    stroke(editor, 0.5, 0.5)
    assert not editor.mask.getbbox()   # all rubbed out
    editor.tool.set(editor.PAINT)
    stroke(editor, 0.25, 0.5)
    editor.before_view.configure(width=700)   # the window changes size: the mask does not
    app.update()
    editor._show(editor.before_view, editor.photo)
    stroke(editor, 0.75, 0.5)
    assert editor.mask.size == (1600, 900)
    assert editor.mask.getpixel((400, 450)) == editor.mask.getpixel((1200, 450)) == 255
    editor.clear_area()
    assert not editor.mask.getbbox()
    editor.change.insert(0, "make it blue")
    editor.run()
    assert "Paint the part to change first" in editor.status.cget("text") and not editor.busy
    editor.change.delete(0, "end")
    editor.before_view.configure(width=editor.VIEW[0])


def test_try_again_keeps_the_painted_area(app, tmp_path, monkeypatch):  # noqa: F811
    editor = area_editor(app, tmp_path)
    given = []

    def fake_redraw(photo, change, out, amount, mask=None, **kwargs):
        assert change == "make the middle blue"   # the words as typed, no rewrite
        with Image.open(mask) as painted:
            given.append((painted.size, painted.getpixel((800, 450))))
        Image.new("RGB", (1600, 900), "blue").save(out)
        return ai.imagegen.Made(1.0, ai.imagegen.GPU, (1600, 900))

    monkeypatch.setattr(ai.imagegen, "redraw", fake_redraw)
    monkeypatch.setattr(ai.imagegen, "installed", lambda: True)
    monkeypatch.setattr(ai.photos, "ready", lambda: (True, ""))    # ready, but a painted part
    monkeypatch.setattr(ai.writer, "ready", lambda: (True, ""))    # doesn't ask either of them
    monkeypatch.setattr(ai.photos, "describe", lambda *a, **k: pytest.fail("described"))
    stroke(editor, 0.5, 0.5)
    editor.change.insert(0, "make the middle blue")
    try:
        for again in (False, True):
            editor.run(again=again)
            for _ in range(200):
                app.update()
                if not editor.busy:
                    break
                time.sleep(0.01)
        assert given == [((1600, 900), 255)] * 2 and len(editor.tries) == 2
    finally:
        editor.change.delete(0, "end")
        editor.clear_area()


def wait(app, editor):  # noqa: F811
    for _ in range(300):
        app.update()
        if not editor.busy:
            return
        time.sleep(0.01)


def test_remove_background_then_fix_it_with_the_brush(app, tmp_path, monkeypatch):  # noqa: F811
    from vidgen import cutout

    def fake_cut(photo, out, should_stop=None):
        with Image.open(photo) as image:
            cut = image.convert("RGBA")
        cut.putalpha(0)                                  # everything removed...
        cut.paste((255, 255, 255, 255), (600, 300, 1000, 600))   # ...but the middle
        cut.save(out)
        return 7.0

    monkeypatch.setattr(cutout, "cut_out", fake_cut)
    monkeypatch.setattr(cutout, "installed", lambda: True)
    editor = area_editor(app, tmp_path)
    editor.remove_background()
    wait(app, editor)
    assert editor.shown in editor.cuts and "Background removed in 7 s" in editor.status.cget("text")
    app.update()
    editor._show(editor.after_view, editor.shown)
    view = editor.after_view
    width, height = view.shown
    x = view.winfo_rootx() + (view.winfo_width() - width) / 2 + 0.1 * width
    y = view.winfo_rooty() + (view.winfo_height() - height) / 2 + 0.5 * height
    press = SimpleNamespace(x_root=x, y_root=y)
    editor._paint_start(press, view)                      # Paint brings a corner part back
    editor._paint_end(press, view)
    with Image.open(editor.shown) as saved:               # saved at once for Save and Use
        assert saved.getpixel((160, 450))[3] == 255 and saved.getpixel((1500, 450))[3] == 0
    assert not editor.mask.getbbox()   # fixing the cut-out doesn't paint Your photo
    flat = editor._usable()                               # a video has no see-through: on white
    with Image.open(flat) as image:
        assert image.mode == "RGB" and image.getpixel((1500, 450)) == (255, 255, 255)
    editor.close()


def test_change_with_words_swaps_the_layer_picture_and_undo_puts_it_back(app, tmp_path,  # noqa: F811
                                                                      monkeypatch):
    from vidgen import thumbedit

    old, new = picture(tmp_path, "old.png", "red"), picture(tmp_path, "new.png", "blue")
    everywhere(monkeypatch, "FREE_THUMBS_DIR", str(tmp_path / "free"))
    everywhere(monkeypatch, "change_photo_window",
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


def test_each_tool_shows_its_own_button_and_the_painting_is_kept(app, tmp_path):  # noqa: F811
    editor = area_editor(app, tmp_path)
    stroke(editor, 0.5, 0.5)
    for mode, button, how_much in ((editor.ERASE, "Erase", False), (editor.CUT, "Remove", False),
                                   (editor.CHANGE, "Change it", True)):
        editor.mode.set(mode)
        app.update()
        assert editor.go.cget("text") == button
        assert bool(editor.amounts.winfo_ismapped()) == how_much
        assert editor.status.cget("text") == editor.TOOLS[mode][2]
    assert editor.mask.getbbox()                         # switching tools keeps the painting
    editor.clear_area()


def erase_fakes(monkeypatch, ai_pictures):
    from vidgen import eraser

    calls = []

    def fake_erase(photo, mask, out, should_stop=None):
        calls.append("erase")
        Image.new("RGB", (1600, 900), "grey").save(out)
        return 3.0, Image.new("L", (1600, 900), 255)

    def fake_touch(photo, filled, hole, out, describe=None, **kwargs):
        calls.append("touch up")
        Image.new("RGB", (1600, 900), "green").save(out)
        return ai.imagegen.Made(25.0, ai.imagegen.GPU, (1600, 900))

    monkeypatch.setattr(eraser, "erase", fake_erase)
    monkeypatch.setattr(eraser, "installed", lambda: True)
    monkeypatch.setattr(eraser, "touch_up", fake_touch)
    monkeypatch.setattr(ai.imagegen, "installed", lambda: ai_pictures)
    monkeypatch.setattr(ai.photos, "ready", lambda: (False, ""))
    return calls


def test_erase_shows_the_plain_result_then_adds_the_touched_up_one(app, tmp_path,  # noqa: F811
                                                                   monkeypatch):
    editor = area_editor(app, tmp_path)
    calls = erase_fakes(monkeypatch, ai_pictures=True)
    editor.mode.set(editor.ERASE)
    editor.act()
    assert "Paint over what to erase first" in editor.status.cget("text")
    stroke(editor, 0.5, 0.5)
    try:
        editor.act()
        wait(app, editor)
        assert calls == ["erase", "touch up"] and len(editor.tries) == 2
        assert editor.shown == editor.tries[1]               # the touched-up one is picked
        assert "pick whichever looks better" in editor.status.cget("text")
    finally:
        editor.clear_area()
        editor.mode.set(editor.CHANGE)


def test_erase_without_ai_pictures_gives_the_plain_result_and_says_why(app, tmp_path,  # noqa: F811
                                                                     monkeypatch):
    editor = area_editor(app, tmp_path)
    calls = erase_fakes(monkeypatch, ai_pictures=False)
    editor.mode.set(editor.ERASE)
    stroke(editor, 0.5, 0.5)
    try:
        editor.act()
        wait(app, editor)
        assert calls == ["erase"] and len(editor.tries) == 1 and not editor.busy
        assert "AI pictures" in editor.status.cget("text")
    finally:
        editor.clear_area()
        editor.mode.set(editor.CHANGE)


def test_select_all_changes_the_whole_photo_with_the_words_as_typed(app, tmp_path,  # noqa: F811
                                                                    monkeypatch):
    editor = area_editor(app, tmp_path)
    given = []

    def fake_redraw(photo, change, out, amount, mask=None, **kwargs):
        given.append((change, amount, mask))
        Image.new("RGB", (1600, 900), "blue").save(out)
        return ai.imagegen.Made(1.0, ai.imagegen.GPU, (1600, 900))

    monkeypatch.setattr(ai.imagegen, "redraw", fake_redraw)
    monkeypatch.setattr(ai.imagegen, "installed", lambda: True)
    editor.select_all()
    editor.change.insert(0, "make it look old")
    try:
        editor.act()
        wait(app, editor)
        assert given == [("make it look old", editor.amount.get(), None)]   # no mask: all of it
    finally:
        editor.change.delete(0, "end")
        editor.clear_area()


def test_a_missing_model_is_downloaded_once_after_asking_then_the_tool_runs(app, tmp_path,  # noqa: F811
                                                                           monkeypatch):
    from vidgen import cutout

    asked, fetched, cut = [], [], []
    have = {"model": False}

    def fake_download(progress=None, should_stop=None, session=None):
        progress(45_000_000, cutout.WEIGHTS.size)
        fetched.append(True)
        have["model"] = True

    def fake_dialog(_app, title, message, confirm=None, confirm_text="OK", **_kw):
        asked.append((title, message, confirm_text))
        confirm()                                       # the user presses Download

    monkeypatch.setattr(cutout, "installed", lambda: have["model"])
    monkeypatch.setattr(cutout, "download", fake_download)
    def fake_cut(*_a, **_k):
        cut.append(True)
        raise cutout.Stopped()                          # enough to know it ran

    monkeypatch.setattr(cutout, "cut_out", fake_cut)
    everywhere(monkeypatch, "Dialog", fake_dialog)
    editor = area_editor(app, tmp_path)
    editor.remove_background()
    wait(app, editor)
    (title, message, button), = asked
    assert title == "One-time download" and "91 MB" in message and button == "Download"
    assert fetched == [True] and cut == [True]           # downloaded, then removed the background
