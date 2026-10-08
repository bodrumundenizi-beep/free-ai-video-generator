"""Finding out whether a newer release exists, and fetching its installer.

The check asks GitHub for the latest release's tag and, if that is newer, the
app offers it. Nothing is downloaded until the user asks for the update. Then
the installer is fetched from this project's own release and only accepted if
it matches the SHA-256 that release published for it; running it is the app's
job (see installer_command).

The requests carry no personal data - just the app's version in the
User-Agent, which GitHub's API requires some value for. A failed *check* is
silent: it must never get in the way of making a video. A failed *download*
raises UpdateError with a sentence for the user.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass

import requests

from .footage import Stopped

REPO = "bodrumundenizi-beep/free-ai-video-generator"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
# Release files are only ever taken from here, whatever an API answer says.
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
SUMS_NAME = "SHA256SUMS.txt"
UNINSTALLER = "unins000.exe"  # what Inno Setup leaves beside an installed copy
TIMEOUT = (5, 10)
DOWNLOAD_TIMEOUT = (10, 60)
CHUNK = 1024 * 1024
MAX_INSTALLER = 500 * 1024 * 1024  # far above any real installer; stops a runaway download


class UpdateError(Exception):
    """The update could not be fetched. The message is for the user."""


@dataclass(frozen=True)
class Release:
    tag: str
    page: str
    installer_url: str | None = None
    installer_name: str | None = None
    sums_url: str | None = None

    @property
    def version(self) -> str:
        return self.tag.lstrip("vV")

    @property
    def can_install(self) -> bool:
        """Whether the release has an installer and the checksums to verify it with."""
        return bool(self.installer_url and self.installer_name and self.sums_url)


def parse_version(text) -> tuple[int, ...] | None:
    """"v3.4.0" or "3.4" as a tuple of numbers; None if it isn't a version."""
    match = re.fullmatch(r"\s*v?(\d+(?:\.\d+)*)\s*", str(text or ""), re.IGNORECASE)
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(latest, current) -> bool:
    """Whether ``latest`` is a later version than ``current``.

    Numbers are compared, so 3.10.0 is newer than 3.9.0, and 3.4 equals 3.4.0.
    Anything unreadable counts as not newer.
    """
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def latest_release(current: str = "", session=None) -> tuple[str, str] | None:
    """(tag, page URL) of the latest published release, or None if unknown."""
    found = latest(current, session)
    return (found.tag, found.page) if found else None


def _own_file(assets, name: str) -> str | None:
    """The download address of the release file called ``name``, if it is this project's."""
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("name") != name:
            continue
        url = asset.get("browser_download_url")
        if isinstance(url, str) and url.startswith(DOWNLOAD_PREFIX):
            return url
    return None


def latest(current: str = "", session=None) -> Release | None:
    """The latest published release, or None if unknown.

    GitHub's "latest" leaves out drafts and pre-releases.
    """
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": f"AIVideoStudio/{current or 'unknown'}"}
    try:
        response = (session or requests).get(LATEST_URL, headers=headers, timeout=TIMEOUT)
        if response.status_code != 200:
            return None
        data = response.json()
        tag = data.get("tag_name")
    except (requests.RequestException, ValueError, AttributeError):
        return None
    version = parse_version(tag)
    if version is None:
        return None
    tag = tag.strip()
    page = data.get("html_url")
    # Only ever send the user to this project's own release pages.
    if not (isinstance(page, str) and page.startswith(f"https://github.com/{REPO}/")):
        page = RELEASES_PAGE
    assets = data.get("assets")
    assets = assets if isinstance(assets, list) else []
    installer = f"AIVideoStudio-{'.'.join(map(str, version))}-setup.exe"
    installer_url = _own_file(assets, installer)
    return Release(tag, page, installer_url, installer if installer_url else None,
                   _own_file(assets, SUMS_NAME))


# --- fetching the installer ------------------------------------------------------

def expected_sha256(sums_text: str, name: str) -> str | None:
    """The checksum SHA256SUMS.txt lists for ``name``, lower-case, or None."""
    for line in (sums_text or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name \
                and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()
    return None


def download_installer(release: Release, folder: str, progress=None, should_stop=None,
                       session=None) -> str:
    """Fetch the release's installer into ``folder`` and return its path.

    The file is written under a .part name and only gets its real name once
    its SHA-256 matches the one the release published, so nothing unverified
    is ever left where it could be run. ``progress(done, total)`` is called
    as it goes; ``should_stop`` is asked between chunks (raises Stopped).
    """
    if not release.can_install:
        raise UpdateError("This release has no installer the app can use.")
    http = session or requests
    path = os.path.join(folder, release.installer_name)
    part = path + ".part"
    try:
        os.makedirs(folder, exist_ok=True)
        sums = http.get(release.sums_url, timeout=DOWNLOAD_TIMEOUT)
        sums.raise_for_status()
        wanted = expected_sha256(sums.text, release.installer_name)
        if wanted is None:
            raise UpdateError("The release does not list a checksum for its installer.")
        digest, done = hashlib.sha256(), 0
        try:
            with http.get(release.installer_url, stream=True,
                          timeout=DOWNLOAD_TIMEOUT) as response:
                response.raise_for_status()
                total = int(response.headers.get("Content-Length") or 0)
                with open(part, "wb") as fh:
                    for chunk in response.iter_content(chunk_size=CHUNK):
                        if should_stop is not None and should_stop():
                            raise Stopped()
                        if not chunk:
                            continue
                        done += len(chunk)
                        if done > MAX_INSTALLER:
                            raise UpdateError("The update is far larger than it should be.")
                        fh.write(chunk)
                        digest.update(chunk)
                        if progress is not None:
                            progress(done, max(total, done))
            if digest.hexdigest() != wanted:
                raise UpdateError("The downloaded update did not match the release's "
                                  "checksum, so it was not used.")
            os.replace(part, path)
        finally:
            if os.path.exists(part):  # stopped, failed or refused: leave nothing behind
                os.remove(part)
    except requests.RequestException as exc:
        raise UpdateError("The update could not be downloaded. Check your internet "
                          f"connection and try again. ({type(exc).__name__})") from exc
    except OSError as exc:
        raise UpdateError(f"The update could not be saved: {exc}") from exc
    return path


def is_installed_copy(app_dir: str) -> bool:
    """Whether the app in ``app_dir`` was put there by the installer, not the portable ZIP."""
    return os.path.isfile(os.path.join(app_dir, UNINSTALLER))


def installer_command(path: str, app_dir: str) -> list[str]:
    """Run the installer over the copy in ``app_dir``: no questions, then reopen the app.

    /SILENT still shows the small progress window, so the user sees something
    is happening. /RELAUNCH=1 is this project's own switch (installer.iss).
    """
    return [path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS",
            "/RELAUNCH=1", f"/DIR={app_dir}"]
