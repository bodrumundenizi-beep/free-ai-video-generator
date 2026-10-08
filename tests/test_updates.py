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


# --- the release's files ---------------------------------------------------------

BASE = updates.DOWNLOAD_PREFIX + "v3.8.0/"
SETUP = "AIVideoStudio-3.8.0-setup.exe"


def release_data(**changes):
    assets = [{"name": SETUP, "browser_download_url": BASE + SETUP},
              {"name": "AIVideoStudio-3.8.0-portable.zip", "browser_download_url": BASE + "p.zip"},
              {"name": updates.SUMS_NAME, "browser_download_url": BASE + updates.SUMS_NAME}]
    return dict({"tag_name": "v3.8.0", "html_url": PAGE, "assets": assets}, **changes)


def test_latest_finds_the_installer_and_its_checksums():
    found = updates.latest("3.7.0", Session(Reply(200, release_data())))
    assert (found.tag, found.version, found.page) == ("v3.8.0", "3.8.0", PAGE)
    assert found.installer_url == BASE + SETUP and found.installer_name == SETUP
    assert found.sums_url == BASE + updates.SUMS_NAME and found.can_install


@pytest.mark.parametrize("assets", [
    None, [], "junk", [None, 5],
    [{"name": SETUP, "browser_download_url": BASE + SETUP}],                 # no checksums
    [{"name": updates.SUMS_NAME, "browser_download_url": BASE + "s.txt"}],   # no installer
    [{"name": "AIVideoStudio-3.9.0-setup.exe", "browser_download_url": BASE + "x.exe"},
     {"name": updates.SUMS_NAME, "browser_download_url": BASE + "s.txt"}],   # another version's
])
def test_a_release_without_both_files_cannot_be_installed(assets):
    found = updates.latest("3.7.0", Session(Reply(200, release_data(assets=assets))))
    assert found.tag == "v3.8.0" and not found.can_install


def test_files_from_anywhere_else_are_ignored():
    assets = [{"name": SETUP, "browser_download_url": "https://evil.example/" + SETUP},
              {"name": updates.SUMS_NAME,
               "browser_download_url": "https://github.com/someone/else/releases/download/v1/S"}]
    found = updates.latest("3.7.0", Session(Reply(200, release_data(assets=assets))))
    assert found.installer_url is None and found.sums_url is None and not found.can_install


def test_checksum_lines_are_read_in_either_case_and_format():
    upper, lower = "AB" * 32, "cd" * 32
    sums = f"{upper}  {SETUP}\n{lower} *other.zip\nnot a line\n{'12' * 10}  short.exe\n"
    assert updates.expected_sha256(sums, SETUP) == upper.lower()
    assert updates.expected_sha256(sums, "other.zip") == lower
    assert updates.expected_sha256(sums, "short.exe") is None
    assert updates.expected_sha256(sums, "missing.exe") is None
    assert updates.expected_sha256("", SETUP) is None


# --- fetching the installer ------------------------------------------------------

import hashlib  # noqa: E402

from vidgen.footage import Stopped  # noqa: E402

INSTALLER = b"MZ-not-really-an-installer" * 4000


class Files:
    """A server with the release's files. ``sums`` defaults to the right checksum."""

    def __init__(self, data=INSTALLER, sums=None, error=None, status=200):
        self.data, self.error, self.status, self.asked = data, error, status, []
        self.sums = sums if sums is not None else f"{hashlib.sha256(data).hexdigest()}  {SETUP}\n"

    def get(self, url, stream=None, timeout=None, headers=None):
        assert timeout, "every request needs a timeout"
        assert url.startswith(updates.DOWNLOAD_PREFIX)
        self.asked.append(url)
        if self.error:
            raise self.error
        self.status_code = self.status
        self.text = self.sums
        self.headers = {"Content-Length": str(len(self.data))}
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(str(self.status))

    def iter_content(self, chunk_size=None):
        for i in range(0, len(self.data), 10_000):
            yield self.data[i:i + 10_000]


RELEASE = updates.Release("v3.8.0", PAGE, BASE + SETUP, SETUP, BASE + updates.SUMS_NAME)


def test_the_installer_is_downloaded_checked_and_named(tmp_path):
    seen = []
    path = updates.download_installer(RELEASE, str(tmp_path / "update"),
                                      lambda done, total: seen.append((done, total)), None, Files())
    assert path.endswith(SETUP) and open(path, "rb").read() == INSTALLER
    assert seen[-1] == (len(INSTALLER), len(INSTALLER)) and seen == sorted(seen)
    assert [p.name for p in (tmp_path / "update").iterdir()] == [SETUP]


@pytest.mark.parametrize("server,message", [
    (Files(sums=f"{'0' * 64}  {SETUP}\n"), "did not match"),
    (Files(sums=f"{'0' * 64}  something-else.exe\n"), "does not list"),
    (Files(error=requests.ConnectionError()), "internet"),
    (Files(status=404), "internet"),
])
def test_an_update_that_cannot_be_trusted_or_fetched_leaves_nothing(server, message, tmp_path):
    with pytest.raises(updates.UpdateError, match=message):
        updates.download_installer(RELEASE, str(tmp_path), None, None, server)
    assert list(tmp_path.iterdir()) == []


def test_a_release_without_files_is_refused_before_any_request(tmp_path):
    server = Files()
    with pytest.raises(updates.UpdateError, match="no installer"):
        updates.download_installer(updates.Release("v3.8.0", PAGE), str(tmp_path), None, None, server)
    assert server.asked == []


def test_cancelling_the_download_leaves_nothing(tmp_path):
    seen = []
    with pytest.raises(Stopped):
        updates.download_installer(RELEASE, str(tmp_path), lambda d, t: seen.append(d),
                                   lambda: len(seen) >= 2, Files())
    assert seen and list(tmp_path.iterdir()) == []


def test_a_runaway_download_is_cut_off(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "MAX_INSTALLER", 25_000)
    with pytest.raises(updates.UpdateError, match="larger"):
        updates.download_installer(RELEASE, str(tmp_path), None, None, Files())
    assert list(tmp_path.iterdir()) == []


# --- installing ------------------------------------------------------------------

def test_only_a_copy_with_an_uninstaller_counts_as_installed(tmp_path):
    assert not updates.is_installed_copy(str(tmp_path))
    (tmp_path / updates.UNINSTALLER).write_bytes(b"x")
    assert updates.is_installed_copy(str(tmp_path))


def test_the_installer_is_run_quietly_into_the_same_folder_and_reopens_the_app():
    command = updates.installer_command(r"C:\Temp\setup.exe", r"C:\Apps\AI Video Studio")
    assert command[0] == r"C:\Temp\setup.exe" and r"/DIR=C:\Apps\AI Video Studio" in command
    assert {"/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RELAUNCH=1"} <= set(command)
    assert "/VERYSILENT" not in command  # the user should see that something is happening
