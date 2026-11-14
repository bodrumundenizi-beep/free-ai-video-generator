"""The promo window: it opens, reads its fields into a Promo, and asks for what's missing."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import ai_video_studio as ai  # noqa: E402
from gui import promo_window  # noqa: E402
from split_names import everywhere  # noqa: E402


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    folder = tmp_path_factory.mktemp("settings")
    path = folder / "settings.json"
    path.write_text(json.dumps({"welcome_seen": True, "tour_seen": True, "whats_new_off": True,
                                "check_updates": False, "window": "1000x700"}), encoding="utf-8")
    patch = pytest.MonkeyPatch()
    everywhere(patch, "SETTINGS_DIR", str(folder))
    everywhere(patch, "SETTINGS_FILE", str(path))
    patch.setattr(ai.projects, "ROOT", str(folder / "projects"))
    try:
        window = ai.App()
    except Exception as exc:  # noqa: BLE001
        patch.undo()
        pytest.skip(f"no window: {exc}")
    window.withdraw()
    yield window
    window.destroy()
    patch.undo()


def test_home_offers_the_promo_video(app):
    assert any(f[1] == "Promo video" for f in app._features())


def test_the_fields_become_a_promo(app):
    dialog = promo_window.PromoDialog(app)
    try:
        dialog.name.insert(0, "Tiny Tasks")
        dialog.hook.insert(0, "Your to-do list, one keypress away")
        dialog.features[0][0].insert(0, "Quick add")
        dialog.features[0][1].insert(0, "Ctrl+Space")
        dialog.var_tone.set("Chaotic")
        dialog.var_shape.set("9:16")
        dialog.var_length.set("15 s")
        p = dialog.current()
        assert (p.name, p.tone, p.aspect, p.seconds) == ("Tiny Tasks", "chaotic", "9:16", 15.0)
        assert [f.title for f in p.features] == ["Quick add"]
        assert dialog._out_path(p).endswith("Tiny Tasks promo (Chaotic).mp4")
    finally:
        dialog.destroy()


def test_making_without_a_first_line_says_so(app):
    dialog = promo_window.PromoDialog(app)
    try:
        dialog.name.insert(0, "Tiny Tasks")
        dialog.make()
        assert "first line" in dialog.status.cget("text") and not dialog.busy
    finally:
        dialog.destroy()


def test_found_pictures_are_shown_ticked_and_capped_at_six(app, tmp_path):
    from PIL import Image
    from vidgen.promo import pictures

    paths = []
    for i in range(8):
        path = str(tmp_path / f"shot{i}.png")
        Image.new("RGB", (800, 500), (i * 30, 40, 90)).save(path)
        paths.append(path)
    logo = str(tmp_path / "logo.png")
    Image.new("RGB", (128, 128)).save(logo)
    dialog = promo_window.PromoDialog(app)
    try:
        dialog._found(pictures.Found(paths, logo, "# Tiny Tasks"))
        assert len(dialog.shots) == 6 and dialog.logo == logo
        dialog.items[0][1].set(False)
        assert dialog.shots[0] == paths[1]
        assert dialog.readme_text == "# Tiny Tasks"
    finally:
        dialog.destroy()
