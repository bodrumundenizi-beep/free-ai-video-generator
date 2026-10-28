"""Home's Get started list and its "Your videos" numbers.

Pure functions, so they are tested without a window. The list is saved in settings as
{"done": [...], "skipped": [...], "hidden": bool}: "done" holds steps the app saw happen
(a tip video made, a rewrite used), "skipped" the ones the user waved off with "Not for me".
Steps that can be read from what exists (a key, a finished video, My voice) are not stored.
"""

from __future__ import annotations

import statistics

STEPS = (
    ("key", "Add a free footage key"),
    ("first", "Make your first video"),
    ("ai", "Use AI pictures in a video"),
    ("tip", "Make a Windows tip video"),
    ("rewrite", "Change a video with \"Say what to change\""),
    ("myvoice", "Make it sound like you: My voice"),
)
WEEK = 7 * 24 * 3600


def state(stored) -> dict:
    """The saved list, cleaned: unknown or broken values fall back to an empty list."""
    stored = stored if isinstance(stored, dict) else {}
    known = {key for key, _label in STEPS}

    def names(field):
        value = stored.get(field)
        return [n for n in value if n in known] if isinstance(value, list) else []

    return {"done": names("done"), "skipped": names("skipped"),
            "hidden": bool(stored.get("hidden"))}


def steps(myvoice_ok: bool) -> list[tuple[str, str]]:
    """The steps shown on this PC: My voice only where it can run."""
    return [(key, label) for key, label in STEPS if key != "myvoice" or myvoice_ok]


def finished(saved: dict, facts: dict) -> set[str]:
    """Steps that are done: seen by the app, or true now (``facts``: name -> bool)."""
    return set(saved["done"]) | {name for name, true in facts.items() if true}


def progress(saved: dict, facts: dict, shown) -> tuple[int, int]:
    """(how many of the shown steps are done or skipped, how many are shown)."""
    out = finished(saved, facts) | set(saved["skipped"])
    keys = [key for key, _label in shown]
    return sum(key in out for key in keys), len(keys)


def summary(projects: list[dict], now: float) -> dict:
    """Numbers for "Your videos": made, this week, the last title, the usual make time.

    ``projects`` newest first (as vidgen.projects.all_projects gives them). Drafts don't
    count. The usual time is the median of the last 5 renders that recorded "took".
    """
    made = [p for p in projects if p.get("videos")]
    took = [p["took"] for p in made if isinstance(p.get("took"), (int, float))][:5]
    return {"made": len(made),
            "week": sum((p.get("created") or 0) >= now - WEEK for p in made),
            "last": made[0] if made else None,
            "usual": round(statistics.median(took)) if took else None}


def per_week(projects: list[dict], now: float, weeks: int = 8) -> list[int]:
    """Videos made in each of the last ``weeks`` weeks, oldest first (by "created")."""
    counts = [0] * weeks
    for project in projects:
        if not project.get("videos"):
            continue
        back = int((now - (project.get("created") or 0)) // WEEK)
        if 0 <= back < weeks:
            counts[weeks - 1 - back] += 1
    return counts


def minutes(projects: list[dict]) -> float:
    """Minutes of finished video, from each project's "length" in seconds."""
    return sum(p.get("length") or 0 for p in projects if p.get("videos")) / 60


def most_used(projects: list[dict], key: str):
    """The value of settings[``key``] most videos used, or None."""
    from collections import Counter

    found = Counter((p.get("settings") or {}).get(key) for p in projects if p.get("videos"))
    found.pop(None, None)
    return found.most_common(1)[0][0] if found else None


def greeting(hour: int) -> str:
    return "Good morning" if 5 <= hour < 12 else "Good afternoon" if 12 <= hour < 18 \
        else "Good evening"


def status_line(numbers: dict, now: float) -> str:
    """"2 videos this week, last one 3 hours ago." from summary()'s numbers."""
    if not numbers["made"]:
        return "Make your first video: it takes a few minutes."
    week = numbers["week"]
    line = f"{week} video{'s' if week != 1 else ''} this week"
    last = numbers["last"]
    if last:
        line += f", last one {ago(last.get('updated') or now, now)}"
    return line + "."


def tip_ideas(tips, made_text: str, day: int, count: int = 3) -> list:
    """``count`` tips for today: the same all day, none already made.

    ``tips`` are (text, place) pairs (vidgen.tiplist.TIPS); ``made_text`` is every project's
    title and script, lowercased; ``day`` is a day number (date.toordinal()).
    """
    import random

    fresh = [tip for tip in tips if tip[0].lower() not in made_text]
    return random.Random(day).sample(fresh, min(count, len(fresh)))


SHOW_NEW_FOR = 7 * 24 * 3600


def whats_new_due(since, version: str, now: float) -> bool:
    """Home's What's new line: for a week after updating to ``version``.

    ``since`` is the saved {"version", "at"}: when this version was first started.
    """
    return isinstance(since, dict) and since.get("version") == version \
        and now - (since.get("at") or 0) < SHOW_NEW_FOR


def ago(then: float, now: float) -> str:
    """"just now", "5 min ago", "3 hours ago", "2 days ago"."""
    seconds = max(now - then, 0)
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "min")):
        if seconds >= size:
            n = int(seconds // size)
            return f"{n} {unit}{'s' if n != 1 and unit != 'min' else ''} ago"
    return "just now"
