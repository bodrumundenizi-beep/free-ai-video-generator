"""Where a scene's pictures come from: Pexels, Pixabay, or the user's own files.

Search order, most specific query first:

    for each query variant (as written, then its core words):
        Pexels in the frame's orientation
        Pexels in any orientation
        Pixabay, if a key is set

Pixabay is tried per variant, not after Pexels' whole chain, because a Pixabay
match for the full query is more relevant than a Pexels match for one word.

Both providers' terms shape this module. Pixabay requires search requests to be
"cached for 24 hours", and both ask that users be shown where footage came from
- hence SearchCache, and the credit lines every Candidate can produce.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass

import requests

PEXELS_URL = "https://api.pexels.com/videos/search"
PIXABAY_URL = "https://pixabay.com/api/videos/"

# (connect, read) seconds. Without a timeout a stalled connection would hang
# the render thread forever.
TIMEOUT = (10, 30)
DOWNLOAD_TIMEOUT = (10, 60)
CACHE_TTL = 24 * 3600

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

# Words that never help a stock search. Kept short on purpose: anything that
# could be the subject of a clip stays in.
STOPWORDS = {
    "a", "an", "the", "of", "on", "in", "at", "to", "for", "with", "and", "or",
    "by", "from", "into", "onto", "over", "under", "is", "are", "be", "being",
    "very", "some", "this", "that", "these", "those", "its", "their", "his", "her",
}


@dataclass
class Candidate:
    provider: str  # "Pexels" or "Pixabay"
    id: str
    url: str  # the chosen rendition's download link
    width: int
    height: int
    fps: float | None = None
    page_url: str = ""
    creator: str = ""

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.id}"

    @property
    def orientation(self) -> str:
        return "portrait" if self.height > self.width else "landscape"

    def credit(self) -> str:
        who = self.creator or "unknown creator"
        return f"{self.provider} · {who} · {self.page_url or self.url}"


def query_variants(query: str) -> list[str]:
    """Most specific first: the query as written, then its core words.

    Stopwords are dropped before shortening, then the original fallback order
    is kept - first two words, then the first. This is word position, not noun
    detection; it needs no language model and has always worked well enough
    for the short phrases a Visual: line holds.
    """
    original = " ".join(query.split())
    words = re.findall(r"[\w'-]+", original.lower())
    core = [w for w in words if w not in STOPWORDS] or words
    variants = [original, " ".join(core), " ".join(core[:2]), core[0] if core else ""]
    unique = []
    for variant in variants:
        if variant and variant.lower() not in (u.lower() for u in unique):
            unique.append(variant)
    return unique


def choose_file(files, target_w: int, target_h: int):
    """The smallest rendition that still fills the frame, else the largest.

    "Fills" means it never has to be scaled up to cover target_w x target_h,
    using the same centre-crop the renderer does. For a portrait frame that
    means a 1080x1920 file, or a 4K landscape one - a 1080p landscape file
    would have to be blown up 1.8x. Among equal sizes, <= 31 fps wins: the
    output is 30 fps, so decoding 60 fps footage is wasted work.

    ``files`` are dicts with url, width, height and optionally fps.
    """
    usable = [f for f in files if f.get("url") and f.get("width") and f.get("height")]
    if not usable:
        return None

    def upscale(f):
        return max(target_w / f["width"], target_h / f["height"])

    def rank(f):
        fps = f.get("fps") or 30
        return (f["width"] * f["height"], 0 if fps <= 31 else 1)

    covering = [f for f in usable if upscale(f) <= 1.02]
    if covering:
        return min(covering, key=rank)
    return max(usable, key=lambda f: (f["width"] * f["height"], -(f.get("fps") or 30)))


class SearchCache:
    """Search responses kept on disk for 24 hours, keyed without the API key."""

    def __init__(self, folder: str | None, ttl: float = CACHE_TTL):
        self.folder = folder
        self.ttl = ttl
        if folder:
            os.makedirs(folder, exist_ok=True)
            self._prune()

    def _path(self, provider: str, params: dict) -> str:
        public = {k: v for k, v in sorted(params.items()) if k != "key"}
        digest = hashlib.sha1(
            json.dumps([provider, public], sort_keys=True).encode("utf-8")
        ).hexdigest()
        return os.path.join(self.folder, f"{digest}.json")

    def get(self, provider: str, params: dict):
        if not self.folder:
            return None
        path = self._path(provider, params)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                stored = json.load(fh)
            if time.time() - stored["saved"] <= self.ttl:
                return stored["data"]
        except (OSError, ValueError, KeyError):
            pass
        return None

    def put(self, provider: str, params: dict, data) -> None:
        if not self.folder:
            return
        try:
            with open(self._path(provider, params), "w", encoding="utf-8") as fh:
                json.dump({"saved": time.time(), "data": data}, fh)
        except OSError:
            pass

    def _prune(self) -> None:
        cutoff = time.time() - self.ttl
        try:
            names = os.listdir(self.folder)
        except OSError:
            return
        for name in names:
            path = os.path.join(self.folder, name)
            try:
                if name.endswith(".json") and os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError:
                pass


def parse_pexels(data: dict, target_w: int, target_h: int) -> list[Candidate]:
    found = []
    for video in data.get("videos") or []:
        files = [
            {"url": f.get("link"), "width": f.get("width"), "height": f.get("height"),
             "fps": f.get("fps")}
            for f in video.get("video_files") or []
            # HLS entries have no fixed size and can't be downloaded as a file.
            if f.get("file_type", "video/mp4") == "video/mp4"
        ]
        best = choose_file(files, target_w, target_h)
        if best:
            found.append(Candidate(
                "Pexels", str(video.get("id")), best["url"], best["width"], best["height"],
                best.get("fps"), video.get("url") or "", (video.get("user") or {}).get("name", ""),
            ))
    return found


def parse_pixabay(data: dict, target_w: int, target_h: int) -> list[Candidate]:
    found = []
    for hit in data.get("hits") or []:
        # Renditions can be present but empty (url "" and size 0).
        files = [
            {"url": r.get("url"), "width": r.get("width"), "height": r.get("height")}
            for r in (hit.get("videos") or {}).values()
            if isinstance(r, dict)
        ]
        best = choose_file(files, target_w, target_h)
        if best:
            found.append(Candidate(
                "Pixabay", str(hit.get("id")), best["url"], best["width"], best["height"],
                None, hit.get("pageURL") or "", hit.get("user") or "",
            ))
    return found


class FootageSearch:
    """Stock search for one render.

    Stateful on purpose: it remembers which videos earlier scenes used, so two
    scenes with similar queries get different clips, and it stops asking a
    provider that rejected its key rather than failing every scene.
    """

    def __init__(self, pexels_key, pixabay_key, target_w, target_h, orientation,
                 quality, cache_dir=None, log=print, session=None):
        self.keys = {"Pexels": (pexels_key or "").strip(),
                     "Pixabay": (pixabay_key or "").strip()}
        self.target_w, self.target_h = target_w, target_h
        self.orientation = orientation
        # Pexels "size" is a minimum: large = 4K, medium = Full HD, small = HD.
        self.pexels_size = "medium" if quality == "1080p" else "small"
        self.cache = SearchCache(cache_dir)
        self.log = log
        self.http = session or requests.Session()
        self.used: set[str] = set()
        self.disabled: dict[str, str] = {}

    @property
    def providers(self) -> list[str]:
        return [p for p, k in self.keys.items() if k and p not in self.disabled]

    def mark_used(self, candidate: Candidate) -> None:
        self.used.add(candidate.key)

    def find(self, query: str, limit: int) -> list[Candidate]:
        """Unused candidates for ``query``, best first, at most ``limit``."""
        found: list[Candidate] = []
        seen: set[str] = set()
        for variant in query_variants(query):
            for search in self._searches(variant):
                for candidate in search():
                    if candidate.key in self.used or candidate.key in seen:
                        continue
                    seen.add(candidate.key)
                    found.append(candidate)
                if len(found) >= limit:
                    return found[:limit]
        return found

    def explain_failure(self, query: str) -> str:
        reasons = "; ".join(f"{p}: {why}" for p, why in self.disabled.items())
        tail = f" ({reasons})" if reasons else ""
        return f"No usable stock footage found for query: '{query}'{tail}"

    def _searches(self, variant: str):
        if "Pexels" in self.providers:
            yield lambda: self._search_pexels(variant, self.orientation)
            yield lambda: self._search_pexels(variant, None)
        if "Pixabay" in self.providers:
            yield lambda: self._search_pixabay(variant)

    def _search_pexels(self, query, orientation) -> list[Candidate]:
        if "Pexels" not in self.providers:
            return []
        params = {"query": query, "per_page": 15, "size": self.pexels_size}
        if orientation:
            params["orientation"] = orientation
        label = orientation or "any orientation"
        data = self._get("Pexels", PEXELS_URL, params,
                         {"Authorization": self.keys["Pexels"]},
                         f"🔍 Searching Pexels for: '{query}' ({label})...")
        return parse_pexels(data, self.target_w, self.target_h) if data else []

    def _search_pixabay(self, query) -> list[Candidate]:
        if "Pixabay" not in self.providers:
            return []
        params = {"key": self.keys["Pixabay"], "q": query[:100], "per_page": 20,
                  "safesearch": "true"}
        data = self._get("Pixabay", PIXABAY_URL, params, {},
                         f"🔍 Searching Pixabay for: '{query}'...")
        found = parse_pixabay(data, self.target_w, self.target_h) if data else []
        # Pixabay can't filter by orientation, so prefer matching clips here.
        found.sort(key=lambda c: c.orientation != self.orientation)
        return found

    def _get(self, provider, url, params, headers, message):
        cached = self.cache.get(provider, params)
        if cached is not None:
            self.log(f"{message} (cached)")
            return cached
        self.log(message)
        try:
            response = self.http.get(url, params=params, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as exc:
            self.log(f"⚠️ {provider} search failed ({exc.__class__.__name__}); trying elsewhere.")
            return None
        if response.status_code in (401, 403):
            self._disable(provider, f"the API key was rejected (HTTP {response.status_code})")
            return None
        if response.status_code == 429:
            self._disable(provider, "the rate limit was reached (HTTP 429)")
            return None
        if response.status_code != 200:
            self.log(f"⚠️ {provider} returned HTTP {response.status_code}; trying elsewhere.")
            return None
        try:
            data = response.json()
        except ValueError:
            return None
        self.cache.put(provider, params, data)
        return data

    def _disable(self, provider, reason):
        if provider not in self.disabled:
            self.disabled[provider] = reason
            self.log(f"⚠️ {provider}: {reason}. Skipping it for the rest of this render.")


def download(url: str, dest: str, session=None) -> str:
    """Stream ``url`` to ``dest``. Raises on HTTP errors or an empty body."""
    http = session or requests
    with http.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT) as response:
        response.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    fh.write(chunk)
    if os.path.getsize(dest) == 0:
        raise OSError("the download was empty")
    return dest


def resolve_local(raw_path: str, base_dir: str) -> tuple[str, str]:
    """(absolute path, "video" or "image") for a ``local:`` visual.

    A relative path is taken relative to ``base_dir`` - the folder the video
    is being saved to. Raises ValueError or FileNotFoundError with a message
    meant for the user.
    """
    path = os.path.expandvars(os.path.expanduser(raw_path.strip()))
    if not path:
        raise ValueError("local: needs a file path after it")
    if not os.path.isabs(path):
        path = os.path.join(base_dir, path)
    path = os.path.normpath(path)
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXTS:
        kind = "video"
    elif ext in IMAGE_EXTS:
        kind = "image"
    else:
        supported = ", ".join(sorted(VIDEO_EXTS | IMAGE_EXTS))
        raise ValueError(f"'{os.path.basename(path)}' is not a supported file ({supported})")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"file not found: {path}")
    return path, kind


def write_credits(video_path: str, lines: list[str], providers: set[str]) -> str:
    """``<video>.credits.txt`` beside the output, one line per stock clip."""
    path = os.path.splitext(video_path)[0] + ".credits.txt"
    sites = {"Pexels": "https://www.pexels.com", "Pixabay": "https://pixabay.com"}
    footer = " and ".join(f"{p} ({sites[p]})" for p in sorted(providers) if p in sites)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"Footage credits for {os.path.basename(video_path)}\n\n")
        fh.write("\n".join(lines) + "\n")
        if footer:
            fh.write(f"\nStock footage provided by {footer}.\n")
    return path
