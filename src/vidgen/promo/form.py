"""Filling in the promo form from the user's project: its README, its CSS, its screenshots.

Safety first (as in /brag): never read .env files, keys, credentials or .git, and drop any
line that looks like a secret, since whatever is read can end up in a public video.
The Smart writer fills the form; every feature must come with words quoted from the README
(checked here), so the video claims nothing the project doesn't say. If its quotes fail
three times, the README's own headings are used, and the user can edit everything.
"""
from __future__ import annotations

import json
import os
import re

from . import colours
from .plan import Feature, Promo

README_LIMIT = 7000   # characters: the model holds 4096 tokens, with room for its answer
CSS_LIMIT = 200_000
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "dist", "build", ".next", "out", "venv",
             ".venv", "env", "__pycache__", "secrets", "credentials", ".idea", ".vscode"}
SKIP_FILE = re.compile(r"(^\.env)|(\.(pem|key|p12|pfx|crt|keystore|jks|kdbx)$)|"
                       r"(^id_(rsa|dsa|ecdsa|ed25519))|(secret|credential|token|password)", re.I)
SECRET_LINE = re.compile(
    r"(api[_-]?key|secret|token|passw(or)?d|private[_-]?key|client[_-]?secret|bearer|"
    r"authorization)\s*[:=]|"
    r"\b(sk|pk|rk|ghp|gho|github_pat|xox[abpr]|AKIA)[-_A-Za-z0-9]{12,}|"
    r"\b[A-Fa-f0-9]{32,}\b|-----BEGIN [A-Z ]*PRIVATE KEY", re.I)


class FormError(Exception):
    """The form could not be filled in. The message is for the user."""


def scrub(text: str) -> str:
    """The text without any line that looks like it holds a secret."""
    return "\n".join(line for line in text.splitlines() if not SECRET_LINE.search(line))


def _allowed(path: str) -> bool:
    parts = os.path.normpath(path).split(os.sep)
    return not any(p.lower() in SKIP_DIRS for p in parts[:-1]) and not SKIP_FILE.search(parts[-1])


def find_readme(folder_or_file: str) -> str | None:
    """The README to read: the file given, or the one at the top of the folder."""
    if os.path.isfile(folder_or_file):
        return folder_or_file if _allowed(folder_or_file) else None
    try:
        names = sorted(os.listdir(folder_or_file), key=str.lower)
    except OSError:
        return None
    for want in ("readme.md", "readme.markdown", "readme.txt", "readme.rst", "readme"):
        for name in names:
            if name.lower() == want:
                return os.path.join(folder_or_file, name)
    return None


def read_readme(folder_or_file: str) -> str:
    """The README's text, secrets taken out, cut to what the model can read. "" if none."""
    path = find_readme(folder_or_file)
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return scrub(fh.read(README_LIMIT * 3))[:README_LIMIT]
    except OSError:
        return ""


def palette_from_folder(folder: str):
    """The project's colours from its stylesheets (the biggest first), or None."""
    if not os.path.isdir(folder):
        return None
    found = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith(".")]
        for name in files:
            path = os.path.join(root, name)
            if name.lower().endswith((".css", ".scss")) and _allowed(path):
                try:
                    found.append((os.path.getsize(path), path))
                except OSError:
                    pass
        if len(found) > 40:
            break
    css = ""
    for _size, path in sorted(found, reverse=True)[:5]:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                css += fh.read(CSS_LIMIT)
        except OSError:
            continue
    return colours.from_css(css) if css else None


def title_of(readme: str) -> str:
    """The README's first heading, without Markdown."""
    for line in readme.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return re.sub(r"[#*_`\[\]]", "", line).strip()[:40]
    return ""


def headings(readme: str) -> list[str]:
    """The README's second-level headings: the fallback features."""
    found = [re.sub(r"[#*_`\[\]]", "", line).strip() for line in readme.splitlines()
             if re.match(r"^#{2,3}\s+\S", line.strip())]
    boring = re.compile(r"install|licen|contribut|usage|build|setup|requirement|faq|credit|"
                        r"changelog|table of contents|download|support|thanks|security", re.I)
    return [h[:40] for h in found if not boring.search(h)][:3]


def norm(text: str) -> str:
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)   # Markdown links: keep the words
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def quote_found(quote, readme: str) -> bool:
    """Every piece of the quote is in the README (case and punctuation aside), 3+ words in
    all. Pieces, because the model likes to glue two nearby phrases into one."""
    pieces = quote if isinstance(quote, list) else [quote]
    page = norm(readme)
    words = 0
    for piece in pieces:
        n = norm(str(piece))
        if not n or n not in page:
            return False
        words += len(n.split())
    return words >= 3


SYSTEM = ("You fill in a form for a 20-second launch video about a software project, using only "
          "its README. Every feature comes with a quote: one or two short pieces copied exactly "
          "from the README that say it. Never add a claim the README does not make. The hook "
          "is a short, curious first line (at most 8 words) that is true for this project; no "
          "generic phrases like 'streamline your workflow', 'game changer' or 'revolutionize'. "
          "'result' is what the user has at the end of using it (at most 5 words); each "
          "feature's 'title' at most 4 words and 'detail' at most 6 words, complete phrases. "
          "Answer with JSON only.")
SCHEMA = {"type": "object", "required": ["name", "what", "hook", "result", "features"],
          "properties": {
              "name": {"type": "string", "maxLength": 40},
              "what": {"type": "string", "maxLength": 110},
              "hook": {"type": "string", "maxLength": 60},
              "result": {"type": "string", "maxLength": 40},
              "features": {"type": "array", "minItems": 3, "maxItems": 3, "items": {
                  "type": "object", "required": ["title", "detail", "quote"], "properties": {
                      "title": {"type": "string", "maxLength": 32},
                      "detail": {"type": "string", "maxLength": 50},
                      "quote": {"type": "array", "minItems": 1, "maxItems": 2,
                                "items": {"type": "string", "maxLength": 120}}}}}}}
GENERIC = re.compile(r"streamline|game.?changer|revolutioni|seamless|unleash|supercharge|"
                     r"next.level|cutting.edge", re.I)


def fill(readme: str, ask, tries: int = 3) -> tuple[Promo, list[str]]:
    """The form from the README. ``ask(system, user, schema)`` returns the model's JSON text.
    (form, notes): notes say what was replaced, for the user to check."""
    notes, problems, form = [], [], None
    for _attempt in range(tries):
        note = ("" if not problems else "\n\nYour last answer was rejected: " + "; ".join(problems)
                + ". Each quote must be copied exactly from the README.")
        try:
            form = json.loads(ask(SYSTEM, "README:\n" + readme + note, SCHEMA))
        except (ValueError, TypeError):
            problems = ["it was not valid JSON"]
            continue
        problems = [f"the quote for '{f['title']}' is not in the README"
                    for f in form["features"] if not quote_found(f["quote"], readme)]
        if GENERIC.search(json.dumps(form)):
            problems.append("it used generic marketing words")
        if not problems:
            break
    if form is None:
        raise FormError("The Smart writer couldn't read this README. Fill in the form yourself.")
    features = [Feature(f["title"], f.get("detail", "")) for f in form["features"]
                if quote_found(f["quote"], readme)]
    if len(features) < 3:
        notes.append("Some features couldn't be found word for word in the README, so its own "
                     "headings were used. Check them.")
        for heading in headings(readme):
            if len(features) >= 3:
                break
            if all(heading.lower() != f.title.lower() for f in features):
                features.append(Feature(heading))
    name = title_of(readme) or form["name"]
    return Promo(name=name, hook=form["hook"], what=form["what"], result=form["result"],
                 features=features[:3]), notes
