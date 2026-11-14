"""Pictures for a promo video, found in the user's own project: a folder, a README, or a
GitHub link. The README's own images come first (the author chose to show those), then the
usual folders; badges, tiny icons and huge files are skipped. The logo goes in the ending.

Same safety rules as the text (form.py): never .git, node_modules, .env or key files. A GitHub
link fetches only that repo's README and the images it links to, over HTTPS, as data.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass, field
from urllib.parse import unquote, urljoin, urlparse

from . import form

MAX_PICTURES = 6
MIN_SIDE = 300          # smaller is an icon or a badge, not a screenshot
MIN_LOGO = 64
MAX_BYTES = 15 * 1024 * 1024
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
LOGO_EXTS = IMAGE_EXTS + (".ico",)
FOLDERS = ("docs", "screenshots", "screenshot", "assets", "images", "img", "media", "public",
           ".github")
BADGE = re.compile(r"shields\.io|badge|badgen\.net|travis-ci|circleci|codecov|coveralls|"
                   r"/actions/workflows/|/workflows/.*\.svg|github\.com/.*/badge|gitter\.im|"
                   r"img\.shields|buymeacoffee|ko-fi|paypal|discord\.com/api/guilds|"
                   r"star-history|visitor|hits\.", re.I)
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_HTML_IMAGE = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.I)
_REF_IMAGE = re.compile(r"^\s*\[[^\]]+\]:\s*(\S+)", re.M)
GITHUB = re.compile(r"^(?:https?://)?(?:www\.)?github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?"
                    r"(?:/(?:tree|blob)/([\w./-]+?))?/?$", re.I)


RATE_LIMITED = ("GitHub asked to wait a little. Try again in an hour, or use the project "
                "folder instead.")


class RateLimited(Exception):
    """GitHub said "too many requests"."""


@dataclass
class Found:
    pictures: list[str] = field(default_factory=list)   # local paths, best first
    logo: str = ""
    readme: str = ""                                     # the README's text (scrubbed)
    notes: list[str] = field(default_factory=list)       # what went wrong, for the user


def readme_images(text: str) -> list[str]:
    """The images a README shows, in order: Markdown, HTML <img> and reference links."""
    found = []
    for match in sorted(list(_MD_IMAGE.finditer(text)) + list(_HTML_IMAGE.finditer(text)),
                        key=lambda m: m.start()):
        found.append(match.group(1))
    for match in _REF_IMAGE.finditer(text):
        if match.group(1).lower().split("?")[0].endswith(IMAGE_EXTS):
            found.append(match.group(1))
    seen, out = set(), []
    for src in found:
        if src not in seen and not is_badge(src):
            seen.add(src)
            out.append(src)
    return out


def is_badge(src: str) -> bool:
    return bool(BADGE.search(src)) or src.lower().split("?")[0].endswith(".svg")


def _size(path: str):
    """(width, height), or None for a file that isn't a readable picture."""
    from PIL import Image

    try:
        if os.path.getsize(path) > MAX_BYTES:
            return None
        with Image.open(path) as image:
            return image.size
    except (OSError, ValueError):
        return None


def _usable(path: str, folder: str) -> str | None:
    """A copy the browser shows well (a GIF's first frame, an ICO's biggest size), or the
    picture itself."""
    from PIL import Image

    ext = os.path.splitext(path)[1].lower()
    if ext not in (".gif", ".ico", ".bmp"):
        return path
    try:
        with Image.open(path) as image:
            if ext == ".ico":
                image.size = max(image.info.get("sizes", [image.size]))
            image.seek(0)
            stem = os.path.splitext(os.path.basename(path))[0][:40]
            out = os.path.join(folder, f"{stem}-{hashlib.sha1(path.encode()).hexdigest()[:6]}.png")
            image.convert("RGBA").save(out)
            return out
    except (OSError, ValueError, EOFError):
        return None


def _pick(candidates, work: str, limit: int = MAX_PICTURES) -> list[str]:
    """Readable, big enough, not a duplicate: the first ``limit``, in the order given."""
    out, hashes = [], set()
    for path in candidates:
        if len(out) >= limit:
            break
        if not os.path.isfile(path) or not form._allowed(path):
            continue
        size = _size(path)
        if not size or min(size) < MIN_SIDE:
            continue
        try:
            with open(path, "rb") as fh:
                digest = hashlib.sha1(fh.read()).hexdigest()
        except OSError:
            continue
        if digest in hashes:
            continue
        usable = _usable(path, work)
        if usable:
            hashes.add(digest)
            out.append(usable)
    return out


def _logo_in(folder: str) -> str:
    """logo.* / icon.* / favicon.* at the top or in the usual folders, biggest first."""
    found = []
    for sub in ("",) + FOLDERS:
        base = os.path.join(folder, sub)
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for name in names:
            stem, ext = os.path.splitext(name.lower())
            if ext in LOGO_EXTS and re.match(r"^(logo|icon|favicon|app[-_]?icon)([-_.].*)?$", stem):
                path = os.path.join(base, name)
                size = _size(path)
                if size and min(size) >= MIN_LOGO and form._allowed(path):
                    found.append((min(size), path))
    return max(found)[1] if found else ""


def _square_early(paths) -> str:
    """A near-square picture among the README's first images: usually the logo."""
    for path in paths[:3]:
        size = _size(path)
        if size and min(size) >= MIN_LOGO and 0.8 <= size[0] / size[1] <= 1.25 and max(size) <= 1024:
            return path
    return ""


def from_folder(source: str, work: str | None = None) -> Found:
    """Pictures and logo from a project folder, or from a README file and the folder it's in."""
    work = work or tempfile.mkdtemp(prefix="aivs-promo-pics-")
    os.makedirs(work, exist_ok=True)
    source = os.path.abspath(source)
    folder = source if os.path.isdir(source) else os.path.dirname(source)
    readme_path = form.find_readme(source)
    text = ""
    if readme_path:
        try:
            with open(readme_path, encoding="utf-8", errors="replace") as fh:
                text = fh.read(form.README_LIMIT * 3)
        except OSError:
            text = ""
    base = os.path.dirname(readme_path) if readme_path else folder
    linked = []
    for src in readme_images(text):
        if urlparse(src).scheme in ("http", "https", "data"):
            continue   # a local project: only its own files (a GitHub link fetches the rest)
        path = os.path.normpath(os.path.join(base, unquote(src.split("#")[0].split("?")[0])))
        if os.path.abspath(path).startswith(os.path.abspath(folder)):   # never outside the project
            linked.append(path)
    early_logo = _square_early(linked)
    others = []
    for sub in FOLDERS:
        top = os.path.join(folder, sub)
        for root, dirs, files in os.walk(top):
            dirs[:] = [d for d in dirs if d.lower() not in form.SKIP_DIRS]
            for name in files:
                if name.lower().endswith(IMAGE_EXTS):
                    path = os.path.join(root, name)
                    try:
                        others.append((-os.path.getsize(path), path))
                    except OSError:
                        pass
            if len(others) > 200:
                break
    logo = _logo_in(folder) or early_logo
    order = [p for p in linked if p != early_logo] + [p for _s, p in sorted(others)]
    pictures = _pick([p for p in order if os.path.normcase(p) != os.path.normcase(logo)], work)
    logo = (_usable(logo, work) or "") if logo else ""
    return Found(pictures, logo, form.scrub(text)[:form.README_LIMIT])


# --- a GitHub link ------------------------------------------------------------------------

def parse_github(url: str) -> tuple[str, str, str] | None:
    """(owner, repo, branch or "HEAD") from a GitHub link, or None."""
    match = GITHUB.match(url.strip())
    if not match:
        return None
    owner, repo, ref = match.groups()
    return owner, repo, (ref.split("/")[0] if ref else "HEAD")


def _raw(owner, repo, ref, path="") -> str:
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"


def _image_url(src: str, owner, repo, ref) -> str | None:
    """Where an image in the README really is, HTTPS only. Relative paths are in the repo;
    github.com/.../blob/... links point at a page, so they become the raw file."""
    if src.startswith("data:"):
        return None
    parsed = urlparse(src)
    if not parsed.scheme:
        return _raw(owner, repo, ref, src.lstrip("./").split("#")[0])
    if parsed.scheme != "https":
        return None
    blob = re.match(r"https://github\.com/([^/]+)/([^/]+)/(?:blob|raw)/(.+)$", src)
    if blob:
        return f"https://raw.githubusercontent.com/{blob.group(1)}/{blob.group(2)}/{blob.group(3)}"
    return src


def _fetch(url: str, limit: int = MAX_BYTES) -> bytes | None:
    """HTTPS only, at most ``limit`` bytes, a short timeout. None on any failure."""
    import requests

    if not url.startswith("https://"):
        return None
    try:
        with requests.get(url, timeout=(8, 20), stream=True,
                          headers={"User-Agent": "AI-Video-Studio"}) as reply:
            if reply.status_code == 429 or (reply.status_code == 403 and
                                            reply.headers.get("x-ratelimit-remaining") == "0"):
                raise RateLimited()
            if reply.status_code != 200:
                return None
            data = b""
            for chunk in reply.iter_content(64 * 1024):
                data += chunk
                if len(data) > limit:
                    return None
            return data
    except requests.RequestException:
        return None


def cached(fetch, folder: str | None):
    """``fetch``, keeping what it got in ``folder``: asking again for the same repo downloads
    nothing (the app's Clear cache empties the folder)."""
    if not folder:
        return fetch

    def keep(url, limit):
        path = os.path.join(folder, hashlib.sha1(url.encode()).hexdigest())
        try:
            with open(path, "rb") as fh:
                return fh.read()
        except OSError:
            pass
        data = fetch(url, limit)
        if data:
            try:
                os.makedirs(folder, exist_ok=True)
                with open(path + ".part", "wb") as fh:
                    fh.write(data)
                os.replace(path + ".part", path)
            except OSError:
                pass
        return data
    return keep


def from_github(url: str, work: str | None = None, fetch=_fetch, cache: str | None = None) -> Found:
    """The README and its pictures from a public GitHub repo. ``cache``: a folder to keep
    downloads in."""
    work = work or tempfile.mkdtemp(prefix="aivs-promo-pics-")
    os.makedirs(work, exist_ok=True)
    parsed = parse_github(url)
    if not parsed:
        return Found(notes=["That doesn't look like a GitHub link (github.com/user/repo)."])
    try:
        return _from_github(parsed, work, cached(fetch, cache))
    except RateLimited:
        return Found(notes=[RATE_LIMITED])


def _from_github(parsed, work, fetch) -> Found:
    owner, repo, ref = parsed
    text = ""
    for name in ("README.md", "readme.md", "Readme.md", "README.markdown", "README.rst", "README"):
        data = fetch(_raw(owner, repo, ref, name), 2 * 1024 * 1024)
        if data:
            text = data.decode("utf-8", "replace")
            break
    if not text:
        return Found(notes=["The repo's README couldn't be fetched: is it public, and is the "
                            "link right?"])
    files = []
    for i, src in enumerate(readme_images(text)[:20]):
        where = _image_url(src, owner, repo, ref)
        if not where:
            continue
        ext = os.path.splitext(urlparse(where).path)[1].lower()
        if ext == ".svg":
            continue
        data = fetch(where, MAX_BYTES)
        if not data:
            continue
        path = os.path.join(work, f"readme-{i:02d}{ext if ext in LOGO_EXTS else '.png'}")
        with open(path, "wb") as fh:
            fh.write(data)
        files.append(path)
        if len(files) >= MAX_PICTURES + 2:   # enough to choose from (a logo, a too-small one)
            break
    logo = _square_early(files)
    pictures = _pick([p for p in files if p != logo], work)
    notes = [] if pictures else ["The README has no screenshots to use: add your own."]
    return Found(pictures, (_usable(logo, work) or "") if logo else "",
                 form.scrub(text)[:form.README_LIMIT], notes)
