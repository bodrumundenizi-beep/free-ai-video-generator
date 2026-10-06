"""A log file on the user's PC, and problem reports the user chooses to send.

Nothing here talks to the network. The app keeps a plain log beside its
settings; when something goes wrong the user can open a report, read every
word of it, edit it, and then send it themselves - as a GitHub issue or by
pasting it wherever they like.

A report is scrubbed before the user even sees it: API keys, the Windows user
name, and the spoken lines of their script are removed. Search words stay,
because footage problems can't be diagnosed without them; the window says so.
"""

from __future__ import annotations

import os
import platform
import re
import time
from urllib.parse import quote, urlencode

from .updates import REPO

MAX_LOG_BYTES = 500_000
REPORT_LOG_LINES = 60
URL_LIMIT = 6000  # GitHub and browsers both cut very long links
LOG_HEADING = "### Log"
KEY_REMOVED = "[key removed]"
USER = "<user>"
SCRIPT_REMOVED = "[script text removed]"
PASTE_BODY = "The report is on my clipboard - pasting it here:\n\n"

# Settings worth knowing when chasing a bug, none of them personal.
REPORT_SETTINGS = (
    "aspect", "resolution", "voice", "padding", "captions", "caption_style", "caption_size",
    "caption_color", "caption_position", "music_enabled", "ask_save", "theme", "translucent",
)


class SessionLog:
    """Timestamped lines appended to a file; one older file kept. Never raises."""

    def __init__(self, path: str, max_bytes: int = MAX_LOG_BYTES):
        self.path = path
        self.max_bytes = max_bytes

    def write(self, text: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            if os.path.exists(self.path) and os.path.getsize(self.path) > self.max_bytes:
                os.replace(self.path, self.path + ".old")
            with open(self.path, "a", encoding="utf-8") as fh:
                for line in str(text).splitlines() or [""]:
                    fh.write(f"{stamp}  {line}\n")
        except OSError:
            pass  # a log that can't be written must not take the app down with it


def redact(text: str, secrets=(), user_name: str = "") -> str:
    """``text`` without API keys, the user's name, or their script's spoken lines."""
    for secret in secrets:
        secret = (secret or "").strip()
        if len(secret) >= 6:
            text = text.replace(secret, KEY_REMOVED)
    text = re.sub(r"(?i)\b(key|api_key|apikey|token)=[^&\s'\"]+", rf"\1={KEY_REMOVED}", text)
    text = re.sub(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?)[^\s'\",}]+", rf"\1{KEY_REMOVED}", text)
    # The profile folder, whatever it is called - including short names like EXCALI~1.
    # Followed by more path, the folder name may contain spaces ("Some Body").
    text = re.sub(r"(?i)\b([a-z]:[\\/]+users[\\/]+)[^\\/:*?\"<>|\r\n]+(?=[\\/])", rf"\1{USER}", text)
    text = re.sub(r"(?i)\b([a-z]:[\\/]+users[\\/]+)[^\\/\s:*?\"<>|]+", rf"\1{USER}", text)
    text = re.sub(r"(/(?:home|Users)/)[^/\s]+", rf"\1{USER}", text)
    user_name = (user_name or "").strip()
    if len(user_name) >= 3:
        text = re.sub(rf"(?i)(?<![\w<]){re.escape(user_name)}(?![\w>])", USER, text)
    # "Generating Guy · Hype Voice: 'the whole spoken line'"
    text = re.sub(r"(Generating [^\n]*?Voice: )'[^\n]*'", rf"\1'{SCRIPT_REMOVED}'", text)
    return text


def system_info(app_version: str, frozen: bool) -> list[str]:
    kind = "installed app" if frozen else "run from source"
    return [f"AI Video Studio {app_version} ({kind})", platform.platform(),
            f"Python {platform.python_version()}"]


def settings_summary(settings: dict) -> list[str]:
    """The settings that explain behaviour, with keys reduced to set / not set."""
    lines = [f"{name}: {settings[name]}" for name in REPORT_SETTINGS if name in settings]
    for label, name in (("Pexels key", "api_key"), ("Pixabay key", "pixabay_key")):
        lines.append(f"{label}: {'set' if str(settings.get(name) or '').strip() else 'not set'}")
    return lines


def build_report(app_version: str, frozen: bool, settings: dict, log_lines, error: str = "",
                 user_name: str = "") -> str:
    """The Markdown report, already scrubbed."""
    secrets = [settings.get("api_key"), settings.get("pixabay_key")]
    tail = list(log_lines)[-REPORT_LOG_LINES:]
    parts = ["### What happened",
             "(Describe what you were doing. Anything you add here helps.)", ""]
    if error:
        parts += ["### Error", "```", str(error).strip(), "```", ""]
    parts += ["### App"] + [f"- {line}" for line in system_info(app_version, frozen)] + [""]
    parts += ["### Settings"] + [f"- {line}" for line in settings_summary(settings)] + [""]
    parts += [f"{LOG_HEADING} (last {len(tail)} lines)", "```"] + tail + ["```"]
    return redact("\n".join(parts), secrets, user_name)


def _link(title: str, body: str) -> str:
    return f"https://github.com/{REPO}/issues/new?" + urlencode(
        {"title": title, "body": body}, quote_via=quote)


def issue_url(title: str, body: str, limit: int = URL_LIMIT) -> str:
    """A link that opens a new GitHub issue with ``body`` filled in.

    Links have a length limit, so the oldest log lines are dropped until it
    fits. If it can't be made to fit, the body just asks for the report to be
    pasted - the window copies it to the clipboard for that.
    """
    url = _link(title, body)
    if len(url) <= limit:
        return url
    lines = body.split("\n")
    start = next((i for i, line in enumerate(lines) if line.startswith(LOG_HEADING)), None)
    if start is not None and len(lines) > start + 2:
        head, log = lines[:start + 2], lines[start + 2:]   # heading and the opening fence
        closing = [log.pop()] if log and log[-1].strip() == "```" else []
        while log:
            log.pop(0)   # oldest first: the newest lines are the ones near the failure
            url = _link(title, "\n".join(head + ["(earlier lines left out)"] + log + closing))
            if len(url) <= limit:
                return url
    return _link(title, PASTE_BODY)
