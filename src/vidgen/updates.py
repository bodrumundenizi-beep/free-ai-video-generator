"""Finding out whether a newer release exists.

The app only ever *tells* the user: it asks GitHub for the latest release's
tag and, if that is newer, offers a link to its page. Nothing is downloaded or
installed here.

The request carries no personal data - just the app's version in the
User-Agent, which GitHub's API requires some value for. Every failure is
silent: an update check must never get in the way of making a video.
"""

from __future__ import annotations

import re

import requests

REPO = "bodrumundenizi-beep/free-ai-video-generator"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
TIMEOUT = (5, 10)


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
    """(tag, page URL) of the latest published release, or None if unknown.

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
    if parse_version(tag) is None:
        return None
    page = data.get("html_url")
    # Only ever send the user to this project's own release pages.
    if not (isinstance(page, str) and page.startswith(f"https://github.com/{REPO}/")):
        page = RELEASES_PAGE
    return tag, page
