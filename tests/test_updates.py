import pytest
import requests

from vidgen import updates
from vidgen.updates import is_newer, latest_release, parse_version


# --- versions --------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("v3.4.0", (3, 4, 0)), ("3.4", (3, 4)), ("V10.0.1", (10, 0, 1)), (" v3.4.0 ", (3, 4, 0)),
])
def test_parse_version(text, expected):
    assert parse_version(text) == expected


@pytest.mark.parametrize("text", ["", None, "latest", "v3.4.0-beta", "3..4", "three"])
def test_parse_version_rejects_junk(text):
    assert parse_version(text) is None


@pytest.mark.parametrize("latest,current,newer", [
    ("v3.5.0", "3.4.0", True),
    ("v3.4.1", "3.4.0", True),
    ("v3.10.0", "3.9.0", True),   # numbers, not text
    ("v4.0", "3.9.9", True),
    ("v3.4.0", "3.4.0", False),
    ("v3.4", "3.4.0", False),     # the same version, written shorter
    ("v3.3.0", "3.4.0", False),
    ("nonsense", "3.4.0", False),
    ("v3.5.0", "", False),
])
def test_is_newer(latest, current, newer):
    assert is_newer(latest, current) is newer


# --- asking GitHub ---------------------------------------------------------------

class Reply:
    def __init__(self, status=200, data=None, bad_json=False):
        self.status_code, self._data, self._bad = status, data, bad_json

    def json(self):
        if self._bad:
            raise ValueError("not JSON")
        return self._data


class Session:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.sent = reply, error, []

    def get(self, url, headers=None, timeout=None):
        assert timeout, "every request needs a timeout"
        self.sent.append((url, headers))
        if self.error:
            raise self.error
        return self.reply


PAGE = f"https://github.com/{updates.REPO}/releases/tag/v3.5.0"


def test_latest_release_returns_tag_and_page():
    session = Session(Reply(200, {"tag_name": "v3.5.0", "html_url": PAGE}))
    assert latest_release("3.4.0", session) == ("v3.5.0", PAGE)
    url, headers = session.sent[0]
    assert url == updates.LATEST_URL
    assert headers["User-Agent"] == "AIVideoStudio/3.4.0"


def test_a_link_to_anywhere_else_is_replaced_with_the_project_page():
    session = Session(Reply(200, {"tag_name": "v3.5.0", "html_url": "https://evil.example/x"}))
    assert latest_release("3.4.0", session) == ("v3.5.0", updates.RELEASES_PAGE)


@pytest.mark.parametrize("session", [
    Session(Reply(404, {"message": "Not Found"})),
    Session(Reply(403, {})),
    Session(error=requests.ConnectionError()),
    Session(error=requests.Timeout()),
    Session(Reply(200, bad_json=True)),
    Session(Reply(200, {"html_url": PAGE})),            # no tag
    Session(Reply(200, {"tag_name": "nightly"})),       # not a version
    Session(Reply(200, ["unexpected", "shape"])),
])
def test_latest_release_is_silent_about_every_failure(session):
    assert latest_release("3.4.0", session) is None
