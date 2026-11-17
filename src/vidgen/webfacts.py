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
                     # 1-2 digits only: "top 5" goes, a year like 1453 stays (without it the
                     # conquest of Istanbul found "Mehmed II" and "Turhan Sultan", 2026-11-16)
                     r"about|on|top|best|\d{1,2}|list|some|facts?|explain|what|are)\b", re.I)


def wants_facts(idea: str) -> bool:
    """An idea that asks about real things (rankings, records, recent models), not a story."""
    return bool(_FACTUAL.search(idea)) and not _STORY.search(idea)


def search_words(idea: str) -> str:
    """'top 5 AMD GPUs' -> 'AMD GPUs': the topic, without what the video should do with it."""
    return " ".join(_FILLER.sub(" ", idea).split()) or idea


_CLAUSE = re.compile(r",|;|\b(?:when|who|which|that|where|while)\b", re.I)


def queries(idea: str) -> list[str]:
    """The idea's first part, then all of it: "conquest of Istanbul in 1453, when Sultan
    Mehmed II took Constantinople" finds Fall of Constantinople by its first part, while the
    whole leans to Mehmed II."""
    words = search_words(idea)
    head = _CLAUSE.split(words)[0].strip()
    return [head, words] if head and head != words and len(head.split()) >= 2 else [words]


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


# The page as plain sentences, for everything but lists: the HTML's infobox gave the writer
# "Born | 30 March 1432Edirne, Ottoman Sultanate" and used up its room (2026-11-16).
_HEADING = re.compile(r"\n+=+ *([^=\n]+?) *=+\n+")
_SKIP = {"see also", "references", "notes", "further reading", "external links", "sources",
         "bibliography", "citations", "footnotes"}


def _extract(http, title: str) -> str:
    found = http.get(API, params={"action": "query", "prop": "extracts", "explaintext": 1,
                                  "titles": title, "redirects": 1, "format": "json"},
                     headers=HEADERS, timeout=TIMEOUT).json()
    return next(iter(found["query"]["pages"].values())).get("extract", "")


def plain_sections(extract: str) -> list[str]:
    """A plain-text extract cut at its headings, the introduction first; reference lists out."""
    pieces = _HEADING.split("\n" + extract)
    parts = [pieces[0].strip()]
    for heading, body in zip(pieces[1::2], pieces[2::2]):
        if heading.strip().lower() not in _SKIP and body.strip():
            parts.append(f"{heading.strip()}: {body.strip()}")
    return [p for p in parts if p]


def facts(idea: str, http=None) -> tuple[str, list[str]]:
    """(the facts, as text, and the page titles they came from); ("", []) if none."""
    http = http or requests
    try:
        hits = []
        for query in queries(idea):
            found = http.get(API, params={"action": "query", "list": "search", "format": "json",
                                          "srsearch": ("list of " if wants_recent(idea) else "")
                                          + query, "srlimit": PAGES},
                             headers=HEADERS, timeout=TIMEOUT).json()
            hits.append([hit["title"] for hit in found["query"]["search"]])
        # the top page of each search first, then the rest
        titles = []
        for title in [h[0] for h in hits if h] + [t for h in hits for t in h[1:]]:
            if title not in titles:
                titles.append(title)
        titles = titles[:PAGES]
        parts, room = [], BUDGET
        for title in titles:
            share = room // max(len(titles) - len(parts), 1)
            if wants_recent(idea) or title.lower().startswith("list of"):   # needs the tables
                page = http.get(API, params={"action": "parse", "page": title, "prop": "text",
                                             "format": "json", "redirects": 1},
                                headers=HEADERS, timeout=TIMEOUT).json()
                text = choose(sections(page["parse"]["text"]["*"]), share, wants_recent(idea))
            else:
                text = choose(plain_sections(_extract(http, title)), share, False)
            parts.append(f"From Wikipedia, \"{title}\":\n{text}")
            room -= len(text)
        return "\n\n".join(parts), titles
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return "", []
