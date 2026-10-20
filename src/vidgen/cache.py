"""Files kept between renders: downloaded clips, clips cut to the video's size, and voices.

Everything is keyed by what changes the result (a clip by its link and the video size, a
voice by its text, speaker and app version), so a changed setting simply misses. Writes go
to a temporary name first, so a crash never leaves half a file behind under a real key.
The folder has a size limit; the files used longest ago go first. Log lines name files by
their key only, never by a path into the user's folder.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time

LIMIT_BYTES = 2 * 1024 ** 3   # all caches together; the oldest files go first above this
KINDS = ("clips", "voices", "search")


def key(*parts) -> str:
    """A file name for ``parts``: the same parts always give the same name."""
    text = json.dumps([str(p) for p in parts], ensure_ascii=False)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:24]


class Cache:
    """One cache folder (``root/kind``). With ``root`` None it keeps nothing."""

    def __init__(self, root: str | None, kind: str):
        self.folder = os.path.join(root, kind) if root else None

    def path(self, name: str) -> str | None:
        """Where ``name`` lives; None when this cache is switched off."""
        return os.path.join(self.folder, name) if self.folder else None

    def get(self, name: str) -> str | None:
        """The kept file, marked as just used; None if it isn't there."""
        path = self.path(name)
        if not path or not os.path.isfile(path):
            return None
        try:
            os.utime(path)   # used now: the size limit removes it last
        except OSError:
            pass
        return path

    def put(self, name: str, source: str, move: bool = False) -> str | None:
        """Keep a copy of ``source`` (or move it) as ``name``. None if it couldn't be kept."""
        path = self.path(name)
        if not path:
            return None
        try:
            os.makedirs(self.folder, exist_ok=True)
            part = f"{path}.{os.getpid()}.part"
            (shutil.move if move else shutil.copyfile)(source, part)
            os.replace(part, path)
            return path
        except OSError:
            return None

    def get_json(self, name: str):
        path = self.get(name)
        if not path:
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def put_json(self, name: str, data) -> None:
        path = self.path(name)
        if not path:
            return
        try:
            os.makedirs(self.folder, exist_ok=True)
            part = f"{path}.{os.getpid()}.part"
            with open(part, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(part, path)
        except OSError:
            pass


def _files(root: str):
    for kind in KINDS:
        folder = os.path.join(root, kind)
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            path = os.path.join(folder, name)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            yield path, stat.st_size, stat.st_mtime


def size(root: str) -> int:
    return sum(size for _path, size, _mtime in _files(root))


def trim(root: str, limit: int = LIMIT_BYTES, stale_parts: float = 3600) -> int:
    """Remove the files used longest ago until the caches fit ``limit``; returns bytes freed.

    Leftover ``.part`` files from a crash older than ``stale_parts`` seconds go too.
    """
    files = sorted(_files(root), key=lambda f: f[2])
    total, freed, now = sum(f[1] for f in files), 0, time.time()
    for path, nbytes, mtime in files:
        stale = path.endswith(".part") and now - mtime > stale_parts
        if total - freed <= limit and not stale:
            continue
        try:
            os.remove(path)
            freed += nbytes
        except OSError:
            pass
    return freed


def clear(root: str) -> int:
    """Remove everything in the caches (Settings > Clear cache); returns bytes freed."""
    freed = size(root)
    for kind in KINDS:
        shutil.rmtree(os.path.join(root, kind), ignore_errors=True)
    return freed - size(root)
