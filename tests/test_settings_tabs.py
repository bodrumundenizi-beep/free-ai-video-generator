"""Settings in vertical tabs: one page per tab, the last tab kept, settings still saved."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import ai_video_studio as ai  # noqa: E402

TABS = ("footage", "video", "voice", "writer", "images", "app", "help", "advanced")


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    folder = tmp_path_factory.mktemp("settings")
    path = folder / "settings.json"
    path.write_text(json.dumps({"welcome_seen": True, "tour_seen": True, "whats_new_off": True,
                                "check_updates": False, "window": "1000x700"}), encoding="utf-8")
    patch = pytest.MonkeyPatch()
    patch.setattr(ai, "SETTINGS_DIR", str(folder))
    patch.setattr(ai, "SETTINGS_FILE", str(path))
    patch.setattr(ai.projects, "ROOT", str(folder / "projects"))   # never the real My videos
    try:
        window = ai.App()
    except Exception as exc:  # noqa: BLE001  (no display on this machine)
        patch.undo()
        pytest.skip(f"no window: {exc}")
    window.withdraw()
    yield window
    window.destroy()
    patch.undo()


def shown(page):
    return bool(ai.App._frame_of(page).winfo_manager())


def test_each_tab_shows_only_its_own_page(app):
    assert tuple(app.settings_tabs) == TABS
    for key in TABS:
        app.open_settings(key)
        app.update_idletasks()
        assert app.current_page == "settings"
        assert [k for k, page in app.settings_tabs.items() if shown(page)] == [key]
        assert app.settings_tab_items[key].selected


def test_the_last_tab_is_kept_when_settings_is_opened_again(app):
    app.open_settings("writer")
    app.select_page("home")
    app.select_page("settings")
    assert app.settings_tab == "writer" and shown(app.settings_tabs["writer"])


def test_each_section_sits_in_its_tab(app):
    def captions(key):
        return [w.cget("text") for w in app.settings_tabs[key].winfo_children()
                if isinstance(w, ai.ctk.CTkLabel)]
    assert captions("footage") == ["Stock footage"]
    assert captions("video") == ["Output", "Video", "Start and end cards"]
    assert captions("voice") == ["Voice", "Music and sound"]
    assert captions("writer") == ["Script writer"]
    assert captions("app") == ["Appearance", "Updates"]


def test_moved_controls_still_save_the_same_settings(app):
    app.var_sfx.set(not app.var_sfx.get())
    app.var_check_updates.set(False)
    saved = app._collect_settings()
    assert saved["sound_effects"] == app.var_sfx.get() and saved["check_updates"] is False


# --- piece 2: the old pages live in Settings, the sidebar has four entries -------------------

def test_every_page_key_still_opens(app):
    for key in ("home", "projects", "board", "settings"):
        app.select_page(key)
        assert app.current_page == key
    for key, tab in (("images", "images"), ("feedback", "help"), ("create", "advanced")):
        app.select_page(key)
        assert app.current_page == "settings" and app.settings_tab == tab


def board_view(app):
    app.update_idletasks()
    return [n for n, v in app.board_views.items() if v.winfo_manager()]


def test_a_render_shows_the_storyboard_log_and_setup_is_one_click(app):
    app.select_page("log")   # what Make video, a batch and a missing voice do
    assert app.current_page == "board" and board_view(app) == ["Log"]
    assert app.nav_items["board"].selected and app.nav_items["board"].winfo_manager()
    app.var_board_view.set("Setup")
    assert board_view(app) == ["Setup"]
    app.select_page("home")
    app.select_page("board")   # the sidebar, or opening a video: the scenes
    assert board_view(app) == ["Setup"]
    assert str(app.log_box).startswith(str(app.pages["board"]))   # one home: not in Settings


def test_the_sidebar_has_four_entries_and_storyboard_only_with_a_video(app):
    assert list(app.nav_items) == ["home", "dashboard", "projects", "board", "settings"]
    assert app.nav_items["projects"].label.cget("text") == "My videos"
    text = app.script_box.get("1.0", "end")
    try:
        app.script_box.delete("1.0", "end")
        app.select_page("home")
        assert not app.nav_items["board"].winfo_manager()
        app.script_box.insert("1.0", "Voice: Hello.\nSearch: city\n")
        app.select_page("home")
        assert app.nav_items["board"].winfo_manager()
    finally:
        app.script_box.delete("1.0", "end")
        app.script_box.insert("1.0", text)


def made_picture(tmp_path, monkeypatch):
    preview = tmp_path / "preview"
    preview.mkdir()
    (preview / "ai_try.png").write_bytes(b"png")
    monkeypatch.setattr(ai, "PREVIEW_DIR", str(preview))


def test_save_picture_copies_the_made_picture(app, tmp_path, monkeypatch):
    made_picture(tmp_path, monkeypatch)
    app.try_entry.delete(0, "end")
    app.try_entry.insert(0, "a robot: holding a clipboard?")
    asked = {}
    dest = tmp_path / "mine.png"
    app.save_try_picture(ask=lambda **kw: asked.update(kw) or str(dest))
    assert asked["initialfile"] == "a robot holding a clipboard.png"
    assert dest.read_bytes() == b"png"


def test_save_picture_falls_back_when_the_folder_is_protected(app, tmp_path, monkeypatch):
    made_picture(tmp_path, monkeypatch)
    blocked, safe = tmp_path / "blocked", tmp_path / "safe"
    safe.mkdir()
    monkeypatch.setattr(ai.paths, "is_writable", lambda folder: str(blocked) not in folder)
    monkeypatch.setattr(ai.paths, "safe_folders", lambda: [str(safe)])
    app.save_try_picture(ask=lambda **kw: str(blocked / "mine.png"))
    assert (safe / "mine.png").read_bytes() == b"png"
    assert "protected" in app.try_status.cget("text")


# --- piece 3: each choice about a video lives only on the Storyboard -------------------------

def bound_to(root, var):
    """Widgets under ``root`` that show or change ``var`` (CTk keeps it as _variable,
    FluentSegmented as variable)."""
    found, todo = [], [root]
    while todo:
        widget = todo.pop()
        if var in (getattr(widget, "_variable", None), getattr(widget, "variable", None)):
            found.append(widget)
        todo.extend(widget.winfo_children())
    return found


def per_video_vars(app):
    return {"voice": app.var_voice, "shape": app.var_aspect, "captions": app.var_captions,
            "length": app.var_target, "versions": app.var_versions, "ai look": app.var_ai_look}


def test_this_videos_choices_are_on_the_storyboard_only(app):
    for name, var in per_video_vars(app).items():
        assert bound_to(app.pages["board"], var), f"{name} is not on the Storyboard"
        assert not bound_to(app.script_editor, var), f"{name} on Script editor"
        assert not bound_to(app.pages["settings"], var), f"{name} still in Settings"


def test_storyboard_choices_still_save_the_same_settings(app):
    voice = ai.VOICE_OPTIONS[-1]
    app.var_voice.set(voice)
    app.var_aspect.set("16:9")
    app.var_captions.set(False)
    app.var_ai_look.set(list(ai.imagegen.LOOKS)[-1])
    app.var_versions.set("2")
    saved = app._collect_settings()
    assert saved["voice"] == ai.voices.persona(voice).id
    assert saved["aspect"] == "16:9" and saved["captions"] is False and saved["versions"] == 2
    assert saved["ai_images_look"] == list(ai.imagegen.LOOKS)[-1]


# --- piece 4: the new Home -------------------------------------------------------------------

def labels(widget):
    """Every label text under ``widget``."""
    found, todo = [], [widget]
    while todo:
        w = todo.pop()
        if isinstance(w, ai.ctk.CTkLabel):
            found.append(w.cget("text"))
        todo.extend(w.winfo_children())
    return found


def home(app):
    app.select_page("home")
    app.update_idletasks()
    return labels(app.home_body)


def save_video(title, updated, **extra):
    pid = ai.projects.new_id() + title[:3]
    ai.projects.save(pid, dict({"script": "Voice: Hi.", "title": title, "videos": ["v.mp4"]},
                               **extra))
    data = ai.projects.load(pid)
    ai.projects.save(pid, dict(data, updated=updated), touch=False)
    return pid


def dashboard(app):
    app.select_page("dashboard")
    app.update_idletasks()
    return labels(app.dash_body)


def test_dashboard_shows_the_newest_six_videos_and_the_numbers(app):
    for at, title in enumerate(("One", "Two", "Three", "Four", "Five", "Six", "Seven")):
        save_video(title, 3000 + at)
    text = dashboard(app)
    assert {"Seven", "Six", "Five", "Four", "Three", "Two"} <= set(text) and "One" not in text
    assert {"videos made", "Videos per week", "What you use most", "Storage", "Setup"} <= set(text)
    assert "Recent videos" not in home(app)


def test_dashboard_with_no_videos_says_how_to_start(app, monkeypatch):
    monkeypatch.setattr(ai.projects, "all_projects", lambda: [])
    text = dashboard(app)
    assert "Your numbers appear here after your first video" in text
    assert "videos made" not in text

def test_a_step_is_ticked_once_it_happened(app):
    app.started.update(done=[], skipped=[], hidden=False)
    tip = "Make a Windows tip video"
    assert tip in home(app)
    app.mark_started("tip")
    text = home(app)
    assert tip in text and "tip" in ai.load_settings()["started"]["done"]
    assert "Make a tip" not in [b.cget("text") for b in buttons(app.started_box)]


def buttons(widget):
    found, todo = [], [widget]
    while todo:
        w = todo.pop()
        if isinstance(w, ai.ctk.CTkButton):
            found.append(w)
        todo.extend(w.winfo_children())
    return found


def test_not_for_me_removes_one_step_and_counts_it(app):
    app.started.update(done=[], skipped=[], hidden=False)
    before = [t for t in home(app) if t.endswith(" done")][0]
    app.skip_started("rewrite")
    text = home(app)
    assert "Change a video with \"Say what to change\"" not in text
    after = [t for t in text if t.endswith(" done")][0]
    assert int(after.split()[0]) == int(before.split()[0]) + 1


def test_hide_this_list_lasts_and_help_brings_it_back(app):
    app.started.update(done=[], skipped=["tip"], hidden=False)
    app.hide_started()
    assert "Get started" not in home(app)
    assert ai.load_settings()["started"]["hidden"] is True
    app.show_started()
    assert "Get started" in home(app) and app.started["skipped"] == []


def test_took_is_saved_with_a_finished_video(app, tmp_path):
    video = tmp_path / "made.mp4"
    video.write_bytes(b"x")
    app.script_box.delete("1.0", "end")
    app.script_box.insert("1.0", "Visual: city at night\nVoice: A finished one.\n")
    app.project = ai.projects.new_id()   # start_render gives every render one
    app._render_info = {"at": ai.time.time() - 42, "ai": False, "tip": False}
    app.save_project(videos=[str(video)])
    assert 41 <= ai.projects.load(app.project)["took"] <= 44


def test_the_new_page_offers_text_tip_and_several(app):
    app.select_page("new")
    app.update_idletasks()
    text = labels(app.pages["new"])
    assert {"Windows tip", "Several at once"} <= set(text)
    assert app.nav_items["home"].selected


# --- piece 6: maximized window, wide panels -----------------------------------------------

def test_the_window_size_is_saved_and_a_bad_one_falls_back(app, tmp_path):
    assert ai.re.fullmatch(r"zoomed|\d+x\d+", app._collect_settings()["window"])
    path = tmp_path / "settings.json"
    for stored, loaded in (("1180x780", "1180x780"), ("zoomed", "zoomed"), ("huge", "zoomed")):
        path.write_text(ai.json.dumps({"window": stored}), encoding="utf-8")
        ai.SETTINGS_FILE, old = str(path), ai.SETTINGS_FILE
        try:
            assert ai.load_settings()["window"] == loaded
        finally:
            ai.SETTINGS_FILE = old


def test_side_panels_show_when_wide_and_options_button_when_narrow(app):
    app.select_page("board")
    app._set_wide(True)
    app.update_idletasks()
    side = ai.App._frame_of(app.board_side)
    assert side.winfo_manager() == "grid" and not app.options_button.winfo_manager()
    app._set_wide(False)
    app.update_idletasks()
    assert not side.winfo_manager() and app.options_button.winfo_manager()
    app._toggle_video_options()   # opens over the scenes
    assert ai.App._frame_of(app.board_side).winfo_manager() == "place"
    app._toggle_video_options()
    assert not ai.App._frame_of(app.board_side).winfo_manager()


def test_the_preview_follows_the_selected_scene(app):
    app.script_box.delete("1.0", "end")
    app.script_box.insert("1.0", "Visual: city\nVoice: First line.\n\nVisual: sea\nVoice: Second line.\n")
    app.select_page("board")
    app.refresh_board(force=True)
    app._board_select(1)
    assert app.preview_title.cget("text") == "Scene 2"
    assert app.preview_line.cget("text") == "Second line."


def test_this_video_box_holds_every_choice_and_settings_none(app):
    for var in (*per_video_vars(app).values(), app.var_style):
        assert bound_to(app.video_box, var)
        assert not bound_to(app.pages["settings"], var)


def test_each_feature_card_opens_its_place(app, monkeypatch):
    opened = []
    monkeypatch.setattr(ai, "TipDialog", lambda _app: opened.append("tip"))
    monkeypatch.setattr(ai, "MyVoiceDialog", lambda _app: opened.append("myvoice"))
    where = {}
    for _glyph, title, _line, _button, action in app._features():
        opened.clear()
        action()
        where[title] = opened[0] if opened else (app.current_page, app.settings_tab)
    assert where["Text to video"][0] == "new" and where["Show it on my PC"] == "tip"
    assert where["AI pictures"] == ("settings", "images")
    assert where["Smart writer"] == ("settings", "writer")
    assert where["Both shapes at once"][0] == "projects"


def test_hiding_the_features_lasts(app):
    app.features_hidden = False
    app.toggle_features()
    assert ai.load_settings()["features_hidden"] is True
    app.toggle_features()
    assert ai.load_settings()["features_hidden"] is False


def test_the_thumbnail_is_made_from_the_scenes_and_change_picture_moves_on(app, tmp_path):
    from PIL import Image
    paths = []
    for at, colour in enumerate(("red", "blue")):
        path = tmp_path / f"scene{at}.png"
        Image.new("RGB", (90, 160), colour).save(path)
        paths.append(str(path))
    app.board_paths = dict(enumerate(paths))
    app.thumb = {"scene": None, "title": "Night light"}
    app._refresh_thumb()
    assert app._thumb_scene() == 0 and app._thumb_image is not None
    app.next_thumb_scene()
    assert app._thumb_scene() == 1 and app.thumb_title() == "Night light"


def test_the_render_uses_the_chosen_scene_for_the_thumbnail():
    from vidgen import render
    starts = [0.0, 1.0, 3.0, 6.0]
    assert render.thumbnail_moment({"thumbnail_scene": 1}, starts) == 2.0
    assert render.thumbnail_moment({"thumbnail_scene": 1, "intro_on": True,
                                    "intro_text": "My channel"}, starts) == 4.5
    assert render.thumbnail_moment({}, starts) is None


def test_my_videos_side_panel_follows_the_selection_and_the_grid_widens(app):
    first = save_video("Alpha video", 2000)
    second = save_video("Beta video", 2001)
    app._set_wide(True)
    app.select_page("projects")
    app.update_idletasks()
    assert app.project_side.winfo_manager() == "grid" and app.project_columns >= 4
    app._select_project(ai.projects.load(first))
    assert "Alpha video" in labels(app.project_side)
    app._select_project(ai.projects.load(second))
    assert "Beta video" in labels(app.project_side)
    app._set_wide(False)
    app.update_idletasks()
    assert not app.project_side.winfo_manager() and app.project_columns == 3


def test_settings_rows_stop_at_a_readable_width_when_wide(app):
    app.open_settings("video")
    app._set_wide(True)
    assert ai.App._frame_of(app.settings_tabs["video"]).grid_info()["sticky"] == "nsw"
    assert app.settings_intro.cget("text") == ai.TAB_INTROS["video"]
    app._set_wide(False)
    assert ai.App._frame_of(app.settings_tabs["video"]).grid_info()["sticky"] == "nesw"


# --- the fuller Home ------------------------------------------------------------------------

def test_home_greets_and_continues_with_the_newest_video(app):
    save_video("Newest of all", 9_999_999_999)
    text = home(app)
    assert app.home_hello.cget("text").startswith("Good ")
    assert "Continue where you left off" in text and "Newest of all" in text


def test_tip_ideas_open_show_it_on_my_pc_with_the_tip(app, monkeypatch):
    asked = []
    monkeypatch.setattr(ai, "TipDialog", lambda _app, ask="": asked.append(ask))
    home(app)
    make = [b for b in buttons(app.home_body) if b.cget("text") == "Make it"]
    assert len(make) == 3
    make[0].invoke()
    assert asked and asked[0] in [t for t, _p in ai.tiplist.TIPS]


def test_whats_new_shows_only_in_the_week_after_an_update(app):
    app.version_since = {"version": ai.APP_VERSION, "at": ai.time.time()}
    assert f"What's new in {ai.APP_VERSION}" in home(app)
    app.version_since = {"version": ai.APP_VERSION, "at": 0}
    assert f"What's new in {ai.APP_VERSION}" not in home(app)
