"""Promo piece F1: pictures from the user's project (folder, README, GitHub link)."""
import io
import os

from PIL import Image

from vidgen.promo import pictures

README = """# Tiny Tasks
<p align="center"><img src="docs/logo.png" width="96"></p>

[![Build](https://github.com/me/tiny/actions/workflows/ci.yml/badge.svg)](x)
![stars](https://img.shields.io/github/stars/me/tiny)

![Main window](docs/main.png "The app")
<img src='screens/settings.jpg' alt="Settings">
![outside](../secret.png)
![remote](https://example.com/shot.png)
"""


def png(path, size, colour=(40, 90, 200)):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", size, colour).save(path)


def test_readme_images_in_order_without_badges():
    found = pictures.readme_images(README)
    assert found == ["docs/logo.png", "docs/main.png", "screens/settings.jpg", "../secret.png",
                     "https://example.com/shot.png"]
    assert pictures.is_badge("https://img.shields.io/x") and pictures.is_badge("logo.svg")


def test_a_project_folder_gives_its_pictures_and_logo(tmp_path):
    (tmp_path / "README.md").write_text(README, encoding="utf-8")
    png(str(tmp_path / "docs" / "logo.png"), (256, 256))
    png(str(tmp_path / "docs" / "main.png"), (1600, 900))
    png(str(tmp_path / "screens" / "settings.jpg"), (1200, 800), (200, 60, 60))
    png(str(tmp_path / "secret.png"), (900, 900))                         # outside: no
    png(str(tmp_path.parent / "secret.png"), (900, 900))
    png(str(tmp_path / "assets" / "big.png"), (2000, 1000), (10, 200, 10))
    png(str(tmp_path / "assets" / "tiny.png"), (64, 64))                   # an icon: no
    png(str(tmp_path / "node_modules" / "docs" / "x.png"), (900, 900))
    png(str(tmp_path / "docs" / "copy.png"), (1600, 900))                 # same as main: no
    found = pictures.from_folder(str(tmp_path), str(tmp_path / "work"))
    names = [os.path.basename(p) for p in found.pictures]
    assert names[:2] == ["main.png", "settings.jpg"]       # the README's own, in its order
    assert "big.png" in names and "tiny.png" not in names and "copy.png" not in names
    assert os.path.basename(found.logo) == "logo.png" and "logo.png" not in names
    assert "Tiny Tasks" in found.readme


def test_a_logo_file_wins_and_icons_are_turned_into_png(tmp_path):
    (tmp_path / "README.md").write_text("# X\n", encoding="utf-8")
    Image.new("RGBA", (128, 128), (255, 0, 0, 255)).save(str(tmp_path / "icon.ico"),
                                                         sizes=[(32, 32), (128, 128)])
    found = pictures.from_folder(str(tmp_path), str(tmp_path / "work"))
    assert found.logo.endswith(".png") and Image.open(found.logo).size == (128, 128)


def test_github_links_are_read():
    assert pictures.parse_github("https://github.com/me/tiny") == ("me", "tiny", "HEAD")
    assert pictures.parse_github("github.com/me/tiny.git") == ("me", "tiny", "HEAD")
    assert pictures.parse_github("https://github.com/me/tiny/tree/dev") == ("me", "tiny", "dev")
    assert pictures.parse_github("https://gitlab.com/me/tiny") is None
    assert pictures._image_url("docs/a.png", "me", "tiny", "HEAD") == \
        "https://raw.githubusercontent.com/me/tiny/HEAD/docs/a.png"
    assert pictures._image_url("https://github.com/me/tiny/blob/main/a.png", "me", "tiny",
                               "HEAD") == "https://raw.githubusercontent.com/me/tiny/main/a.png"
    assert pictures._image_url("http://insecure.test/a.png", "me", "tiny", "HEAD") is None


def test_a_github_repo_is_fetched_as_data(tmp_path):
    def bytes_of(size):
        out = io.BytesIO()
        Image.new("RGB", size, (90, 90, 160)).save(out, "PNG")
        return out.getvalue()

    served = {
        "https://raw.githubusercontent.com/me/tiny/HEAD/README.md": README.encode(),
        "https://raw.githubusercontent.com/me/tiny/HEAD/docs/logo.png": bytes_of((200, 200)),
        "https://raw.githubusercontent.com/me/tiny/HEAD/docs/main.png": bytes_of((1600, 900)),
        "https://example.com/shot.png": bytes_of((1280, 720)),
    }
    asked = []

    def fetch(url, limit):
        asked.append(url)
        return served.get(url)

    found = pictures.from_github("https://github.com/me/tiny", str(tmp_path), fetch)
    assert len(found.pictures) == 2 and found.logo and found.readme.startswith("# Tiny Tasks")
    assert not any("shields.io" in u or "badge" in u for u in asked)
    assert all(u.startswith("https://") for u in asked)


def test_a_missing_repo_says_so():
    found = pictures.from_github("https://github.com/me/nothing", fetch=lambda u, n: None)
    assert not found.pictures and "public" in found.notes[0]


def test_a_repo_fetched_again_comes_from_the_cache(tmp_path):
    asked = []
    readme = b"# Tiny\n"

    def fetch(url, limit):
        asked.append(url)
        return readme if url.endswith("README.md") else None

    cache = str(tmp_path / "cache")
    pictures.from_github("github.com/me/tiny", str(tmp_path / "a"), fetch, cache)
    first = len(asked)
    found = pictures.from_github("github.com/me/tiny", str(tmp_path / "b"), fetch, cache)
    assert first >= 1 and len(asked) == first and found.readme.startswith("# Tiny")


def test_too_many_requests_says_to_wait(tmp_path):
    def fetch(url, limit):
        raise pictures.RateLimited()

    found = pictures.from_github("github.com/me/tiny", str(tmp_path), fetch)
    assert found.notes == [pictures.RATE_LIMITED] and not found.pictures
