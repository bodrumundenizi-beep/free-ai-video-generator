"""Saved projects: each video's script, settings and AI pictures, so it can be opened again.

A project is a folder under ``ROOT``: ``project.json``, ``thumb.png`` and ``pictures/``.
A project with no video yet is a draft. Nothing here touches Tk.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import time

ROOT = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                    "AIVideoStudio", "projects")
DATA = "project.json"
THUMB = "thumb.png"

# The settings a video was made with; opening the project puts them back.
KEYS = ("aspect", "resolution", "voice", "captions", "caption_style", "caption_size",
        "caption_color", "caption_position", "ai_images_look", "target_length",
        "music_enabled", "music_path", "style")


def new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def folder(project_id: str) -> str:
    return os.path.join(ROOT, project_id)


def picture_file(project_dir: str, key: str) -> str:
    """Where the AI picture for ``key`` (prompt|look|aspect) is kept in a project."""
    name = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return os.path.join(project_dir, "pictures", f"{name}.png")


def default_title(script: str, limit: int = 40) -> str:
    """The first spoken line, cut on a word."""
    for line in (script or "").splitlines():
        if line.strip().lower().startswith("voice:"):
            text = line.split(":", 1)[1].strip()
            if len(text) <= limit:
                return text or "Untitled video"
            return text[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + "..."
    return "Untitled video"


def save(project_id: str, data: dict, touch: bool = True) -> dict:
    """Write the project (replacing it whole, so a crash never leaves half a file).

    ``touch`` moves it to the top of the list; a rename or a thumbnail doesn't.
    """
    path = folder(project_id)
    os.makedirs(path, exist_ok=True)
    now = time.time()
    data = dict(data, id=project_id)
    if touch or not data.get("updated"):
        data["updated"] = now
    data.setdefault("created", now)
    data.setdefault("title", default_title(data.get("script", "")))
    data.setdefault("videos", [])
    partial = os.path.join(path, DATA + ".part")
    with open(partial, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(partial, os.path.join(path, DATA))
    return data


def load(project_id: str) -> dict | None:
    try:
        with open(os.path.join(folder(project_id), DATA), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("script"), str):
        return None
    data["id"] = project_id
    return data


def all_projects() -> list[dict]:
    """Every readable project, the most recently changed first."""
    try:
        names = os.listdir(ROOT)
    except OSError:
        return []
    found = [p for p in (load(name) for name in names) if p is not None]
    return sorted(found, key=lambda p: p.get("updated") or 0, reverse=True)


def is_draft(project: dict) -> bool:
    return not project.get("videos")


def matches(project: dict, text: str) -> bool:
    text = (text or "").strip().lower()
    return not text or text in (project.get("title") or "").lower() \
        or text in (project.get("script") or "").lower()


def copy(project_id: str) -> str:
    """A copy of the project, with its pictures, as a new draft. Returns the new id."""
    source = load(project_id)
    if source is None:
        raise FileNotFoundError(project_id)
    new = new_id()
    shutil.copytree(folder(project_id), folder(new))
    for name in (DATA, THUMB):
        try:
            os.remove(os.path.join(folder(new), name))
        except OSError:
            pass
    keep = {k: v for k, v in source.items() if k not in ("id", "created", "updated")}
    save(new, dict(keep, title=f"{source.get('title') or 'Untitled video'} (copy)",
                   videos=[], length=0))
    return new


def delete(project_id: str) -> None:
    """Remove the project's folder; the video files it points to are left alone."""
    root = os.path.realpath(ROOT)
    path = os.path.realpath(folder(project_id))
    if os.path.dirname(path) != root:
        raise ValueError(f"not a project folder: {project_id}")
    shutil.rmtree(path, ignore_errors=True)
