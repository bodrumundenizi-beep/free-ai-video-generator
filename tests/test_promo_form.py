"""Promo piece D: reading the user's project safely, and filling in the form."""
import json
import os

from vidgen.promo import form

README = """# Tiny Tasks

Tiny Tasks keeps a to-do list in your system tray. API_KEY=sk-abcdefghijklmnopqrstuvwx

## Quick add
Press Ctrl+Space anywhere to add a task without leaving what you are doing.

## Due dates that nag
Tasks due today turn red and pop up a reminder at nine.

## Installation
Run the installer.
"""


def test_secret_looking_lines_are_dropped():
    clean = form.scrub(README + "\ntoken: abc\npassword = hunter2\nghp_" + "a" * 30 + "\n")
    assert "sk-abcdef" not in clean and "hunter2" not in clean and "ghp_" not in clean
    assert "Quick add" in clean


def test_env_files_keys_and_git_are_never_read(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "README.md").write_text("# secret history")
    assert form.find_readme(str(tmp_path / ".env")) is None
    (tmp_path / ".env").write_text("A=1")
    assert form.find_readme(str(tmp_path / ".env")) is None
    (tmp_path / "id_rsa").write_text("x")
    assert form.find_readme(str(tmp_path / "id_rsa")) is None
    assert form.read_readme(str(tmp_path)) == ""          # only .git had one
    (tmp_path / "ReadMe.md").write_text(README)
    assert "Quick add" in form.read_readme(str(tmp_path))


def test_css_colours_skip_node_modules(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "big.css").write_text(":root{--bg:#ff0000;--accent:#00ff00}" * 50)
    (tmp_path / "site.css").write_text(":root{--bg:#101820;--accent:#ffb000}")
    palette = form.palette_from_folder(str(tmp_path))
    assert palette.background == "#101820"


def test_quotes_must_be_in_the_readme_but_may_come_in_pieces():
    assert form.quote_found(["Press Ctrl+Space anywhere", "without leaving"], README)
    assert form.quote_found("tasks due today turn red", README)
    assert not form.quote_found("Press Ctrl+Space to add a task", README)     # glued: not there
    assert not form.quote_found(["red"], README)                              # too short


def answer(features, hook="Your to-do list, one keypress away"):
    return json.dumps({"name": "Tiny Tasks", "what": "A to-do list in the tray", "hook": hook,
                       "result": "Nothing forgotten", "features": features})


def test_the_form_is_filled_and_checked():
    good = [{"title": "Quick add", "detail": "Ctrl+Space anywhere",
             "quote": ["Press Ctrl+Space anywhere to add a task"]},
            {"title": "Nagging due dates", "detail": "Red, with a reminder",
             "quote": ["Tasks due today turn red"]},
            {"title": "In the tray", "detail": "",
             "quote": ["keeps a to-do list in your system tray"]}]
    filled, notes = form.fill(README, lambda *_a: answer(good))
    assert filled.name == "Tiny Tasks" and notes == []
    assert [f.title for f in filled.features] == ["Quick add", "Nagging due dates", "In the tray"]


def test_made_up_features_are_asked_again_then_replaced_by_headings():
    asked = []
    bad = [{"title": "AI powered", "detail": "", "quote": ["uses advanced AI"]},
           {"title": "Quick add", "detail": "", "quote": ["Press Ctrl+Space anywhere"]},
           {"title": "Cloud sync", "detail": "", "quote": ["syncs to the cloud"]}]

    def ask(system, user, schema):
        asked.append(user)
        return answer(bad)

    filled, notes = form.fill(README, ask)
    assert len(asked) == 3 and "rejected" in asked[1]
    titles = [f.title for f in filled.features]
    assert "AI powered" not in titles and "Cloud sync" not in titles
    assert titles[:2] == ["Quick add", "Due dates that nag"] and notes
    assert "Installation" not in titles


def test_generic_marketing_words_are_sent_back():
    ok = [{"title": "Quick add", "detail": "", "quote": ["Press Ctrl+Space anywhere"]}] * 3
    replies = iter([answer(ok, hook="Supercharge your workflow"), answer(ok)])
    asked = []
    form.fill(README, lambda s, u, sc: asked.append(u) or next(replies))
    assert len(asked) == 2 and "generic" in asked[1]
