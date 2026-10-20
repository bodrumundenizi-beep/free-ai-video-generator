"""Facts from Wikipedia for the Smart writer, so a "top 5" video isn't the model's guess.

The Smart writer is a small model: asked for the top 5 AMD graphics cards it named an old,
low-end card (2026-10-17). For an idea that asks for facts, the app first reads the best
matching Wikipedia pages (its free API, no key) and the writer is told to use only those.

Only the search words leave the PC. DuckDuckGo was tried first and refuses automated searches
with a bot check, so it isn't used. Every failure returns no facts: the script is then written
as before, and the window says the web couldn't be reached.
"""

from __future__ import annotations

import html
import re

import requests

API = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "AIVideoStudio (https://github.com/bodrumundenizi-beep/"
                         "free-ai-video-generator)"}
TIMEOUT = (5, 10)
PAGES = 2
BUDGET = 4500   # characters of facts: what fits beside the instructions and the answer

_FACTUAL = re.compile(
    r"\b(top\s*\d+|best|worst|latest|newest|fastest|slowest|biggest|largest|smallest|"
    r"tallest|richest|most|least|how many|history of|facts? about|list of|ranking|"
    r"compared?|vs\.?|versus|(19|20)\d\d)\b", re.I)
_STORY = re.compile(r"\b(story|stories|tale|horror|thriller|scary|creepy|poem)\b", re.I)
_FILLER = re.compile(r"\b(please|write|make|create|give|tell|me|a|an|the|video|short|script|"
                     r"about|on|top|best|\d+|list|some|facts?|explain|what|are)\b", re.I)


def wants_facts(idea: str) -> bool:
    """An idea that asks about real things (rankings, records, recent models), not a story."""
    return bool(_FACTUAL.search(idea)) and not _STORY.search(idea)


def search_words(idea: str) -> str:
    """'top 5 AMD GPUs' -> 'AMD GPUs': the topic, without what the video should do with it."""
    return " ".join(_FILLER.sub(" ", idea).split()) or idea


_RECENT = re.compile(r"\b(top|best|latest|newest|new|current|fastest|most powerful)\b", re.I)
_RANKED = re.compile(r"\b(history|tallest|biggest|largest|smallest|oldest|richest)\b", re.I)


def wants_recent(idea: str) -> bool:
    """'top 5 AMD GPUs' wants the newest models; 'tallest buildings' wants the ranking."""
    return bool(_RECENT.search(idea)) and not _RANKED.search(idea)


def page_text(html_text: str) -> str:
    """A page's readable text, tables included (a row per line, cells split by '|')."""
    text = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", " ", html_text)
    text = re.sub(r"(?is)<sup.*?</sup>", "", text)
    text = re.sub(r"(?i)\[edit\]", "", text)
    text = re.sub(r"(?i)</t[dh]>", " | ", text)
    text = re.sub(r"(?i)</(tr|p|li|h\d)>", "\n", text)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    lines = [" ".join(line.split()).strip(" |") for line in text.split("\n")]
    return "\n".join(line for line in lines if len(line) > 2 and not line.startswith("^"))


def sections(html_text: str) -> list[str]:
    """The page cut at its headings: the introduction first, then each section's text."""
    parts = re.split(r"(?i)<h[23]\b", html_text)
    return [text for text in (page_text(("<h2" if i else "") + part)
                              for i, part in enumerate(parts)) if text]


def newest_year(text: str) -> int:
    years = [int(y) for y in re.findall(r"\b(19[5-9]\d|20\d\d)\b", text)]
    return max(years, default=0)


def choose(parts: list[str], room: int, recent: bool) -> str:
    """As much of the page as fits ``room``: from the top, or, for the newest things, the
    introduction and then the sections that mention the latest years. Kept in page order."""
    order = list(range(len(parts)))
    if recent and parts:
        order = [0] + sorted(order[1:], key=lambda i: -newest_year(parts[i]))
    picked, used = [], 0
    for i in order:
        if used >= room:
            break
        # Lists of models and records run oldest first: the newest rows are at the end.
        piece = parts[i][:room - used]
        if recent and i and len(parts[i]) > room - used:
            text = parts[i]
            start = text.find("\n", len(text) - (room - used) - 1)   # from a whole row
            piece = text[start + 1:] if start != -1 else ""
        picked.append((i, piece))
        used += len(piece)
    return "\n".join(piece for _i, piece in sorted(picked))


def facts(idea: str, http=None) -> tuple[str, list[str]]:
    """(the facts, as text, and the page titles they came from); ("", []) if none."""
    http = http or requests
    try:
        found = http.get(API, params={"action": "query", "list": "search", "format": "json",
                                      "srsearch": ("list of " if wants_recent(idea) else "")
                                      + search_words(idea), "srlimit": PAGES},
                         headers=HEADERS, timeout=TIMEOUT).json()
        titles = [hit["title"] for hit in found["query"]["search"]][:PAGES]
        parts, room = [], BUDGET
        for title in titles:
            page = http.get(API, params={"action": "parse", "page": title, "prop": "text",
                                         "format": "json", "redirects": 1},
                            headers=HEADERS, timeout=TIMEOUT).json()
            text = choose(sections(page["parse"]["text"]["*"]),
                          room // max(len(titles) - len(parts), 1), wants_recent(idea))
            parts.append(f"From Wikipedia, \"{title}\":\n{text}")
            room -= len(text)
        return "\n\n".join(parts), titles
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return "", []
