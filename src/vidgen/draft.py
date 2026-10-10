"""Plain text in, a script out.

The user pastes ordinary text and says who the video is for, how long it should
be and its shape. This module turns that into Visual:/Voice: scenes without any
AI: one scene per sentence, search words taken from the sentence itself, and
the user's own words left exactly as written.

The audience changes the style here - which voice reads it and how short the
scenes are - not the wording. Each audience entry also carries a ``brief``, the
sentence a script-writing model is given for it.

Pure: no Tk, no network.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from . import pacing, voices
from .footage import STOPWORDS
from .script import parse_script

KEEP_ALL = "Keep everything"
LENGTHS = ["15 s", "30 s", "60 s", KEEP_ALL]

# How much faster than normal the voice may have to go before a sentence is
# dropped instead. pacing allows 15%; a draft should not start at the limit.
COMFORTABLE_TEMPO = 1.08
MIN_PIECE_WORDS = 3
SEARCH_WORDS = 2
TOPIC_WORDS = 5


class DraftError(Exception):
    """Text that cannot be turned into a script. The message is for the user."""


@dataclass(frozen=True)
class Content:
    voice: str  # a voices.py persona ID
    brief: str


@dataclass(frozen=True)
class Age:
    scene_words: int  # longer sentences are split into scenes of about this size
    brief: str


@dataclass(frozen=True)
class Platform:
    scene_words: int
    length: str  # the entry of LENGTHS it pre-selects
    aspect: str
    brief: str


CONTENT = {
    "Tech and how-to": Content(
        "en-US-GuyNeural", "Fast, practical and concrete. Name the exact step or result."),
    "Story and facts": Content(
        "en-US-ChristopherNeural", "Tell it like a short story that builds to a surprising fact."),
    "Motivation": Content(
        "en-US-EricNeural", "Speak directly to the viewer with short, strong statements."),
    "Business and money": Content(
        "en-US-AndrewNeural", "Clear and credible, with numbers where the text gives them."),
    "Kids and family": Content(
        "en-US-JennyNeural", "Warm, friendly and simple, with a sense of fun."),
}
AGES = {
    "Kids": Age(10, "Use very simple words and short sentences a child understands."),
    "Teens": Age(12, "Use casual, energetic language without trying to sound trendy."),
    "Young adults": Age(16, "Use a relaxed, conversational tone."),
    "Adults": Age(22, "Use a plain, respectful tone."),
}
PLATFORMS = {
    "TikTok": Platform(12, "30 s", "9:16", "Open with a hook in the first three seconds."),
    "YouTube Shorts": Platform(16, "30 s", "9:16", "Open with a hook and end on a payoff."),
    "Instagram Reels": Platform(14, "30 s", "9:16", "Keep it visual and quick to follow."),
    "YouTube long-form": Platform(26, KEEP_ALL, "16:9", "Take time to explain each point."),
}
DEFAULT_CONTENT, DEFAULT_AGE, DEFAULT_PLATFORM = "Tech and how-to", "Adults", "YouTube Shorts"


@dataclass(frozen=True)
class Audience:
    content: str = DEFAULT_CONTENT
    age: str = DEFAULT_AGE
    platform: str = DEFAULT_PLATFORM

    def _content(self) -> Content:
        return CONTENT.get(self.content, CONTENT[DEFAULT_CONTENT])

    def _age(self) -> Age:
        return AGES.get(self.age, AGES[DEFAULT_AGE])

    def _platform(self) -> Platform:
        return PLATFORMS.get(self.platform, PLATFORMS[DEFAULT_PLATFORM])

    @property
    def voice(self) -> str:
        """The persona ID that suits this audience. Children get the friendly voice."""
        return CONTENT["Kids and family"].voice if self.age == "Kids" else self._content().voice

    @property
    def scene_words(self) -> int:
        return min(self._age().scene_words, self._platform().scene_words)

    @property
    def length(self) -> str:
        return self._platform().length

    @property
    def aspect(self) -> str:
        return self._platform().aspect

    @property
    def brief(self) -> str:
        return " ".join((self._content().brief, self._age().brief, self._platform().brief))


# --- reading the text --------------------------------------------------------------

_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e", "no", "inc",
    "ltd", "co", "approx", "fig", "u.s", "u.k", "a.m", "p.m",
}
_CLOSERS = "\"'”’)]"
_BREAK = re.compile(r"""(?<=[.!?"'”’)\]])\s+""")
_BULLET = re.compile(r"^\s*(?:[-*•–—]+|\d+[.)])\s+")
_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*|\d[\d.,]*")
_CLAUSE = re.compile(r"[,;:]\s+|\s+[–—-]\s+")

# Words that say nothing about what a clip should show. Wider than the stock
# search's own list, because a sentence has far more filler than a Visual: line.
_FILLER = STOPWORDS | set("""
    i you he she it we they me him them us my your our mine yours who whom whose which what
    when where why how if then than so but not no nor yes as too also just only even still
    was were been am do does did done doing has have had having will would can could should
    shall may might must get gets got getting make makes made making go goes going went gone
    take takes took taken come comes came say says said see sees saw seen know knows knew
    want wants wanted need needs needed use uses used using let lets put keep keeps kept
    there here now ever never always often again already about after before between during
    through while until because although though since up down out off more most much many
    few less least all any each every both either neither other another such same own new
    one two three first second next last thing things way ways lot lots really actually
    simply maybe every everything something anything nothing someone anyone everyone
    today tomorrow yesterday second seconds minute minutes hour hours day days week weeks
    month months year years time times once twice far long hard easy safe good bad best
    open opens opened press type call calls called received kept try tries tried turn turns seem seems
    don't doesn't didn't can't won't isn't aren't wasn't it's that's you're i'm we're they're
    you'll i'll we'll you've i've we've there's here's let's what's
""".split())


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def split_sentences(text: str) -> list[str]:
    """The text's sentences, in order. Each line or bullet ends a sentence too."""
    found: list[str] = []
    for line in (text or "").splitlines():
        line = _clean(_BULLET.sub("", line))
        if not line:
            continue
        pieces = _BREAK.split(line)
        current = ""
        for piece in pieces:
            current = f"{current} {piece}".strip() if current else piece.strip()
            ending = current.rstrip(_CLOSERS)
            if not ending.endswith((".", "!", "?")):
                continue  # a closing quote or bracket in the middle of a sentence
            last = ending.split()[-1].rstrip(".").lower()
            # "Dr." and a lone initial like "J." do not end a sentence.
            if ending.endswith(".") and (last in _ABBREVIATIONS or len(last) == 1 and last.isalpha()):
                continue
            if current:
                found.append(current)
            current = ""
        if current:
            found.append(current)
    return found


def words_in(text: str) -> list[str]:
    return _WORD.findall(text or "")


def _content_words(text: str) -> list[str]:
    """Lower-case words that could be the subject of a clip."""
    return [w for w in (word.lower().replace("’", "'") for word in words_in(text))
            if w not in _FILLER and len(w) > 2 and not w[0].isdigit() and not w.endswith("ly")]


def topic_words(text: str, limit: int = TOPIC_WORDS) -> list[str]:
    """The words the text keeps coming back to, most used first."""
    counts = Counter(_content_words(text))
    order = {word: i for i, word in enumerate(dict.fromkeys(_content_words(text)))}
    ranked = sorted(counts, key=lambda w: (-counts[w], order[w]))
    return ranked[:limit]


def search_words(sentence: str, counts: Counter, fallback: list[str]) -> str:
    """Stock-search words for one scene: the sentence's own most telling words.

    ``counts`` is how often each content word appears in the whole text, so a
    word the text is about beats one it mentions once. A sentence with nothing
    usable ("And that is it.") falls back to the text's topic.
    """
    own = list(dict.fromkeys(_content_words(sentence)))
    if not own:
        return " ".join(fallback[:SEARCH_WORDS]) or "abstract background"
    best = sorted(own, key=lambda w: (-counts.get(w, 0), -min(len(w), 8), own.index(w)))
    chosen = set(best[:SEARCH_WORDS])
    return " ".join(w for w in own if w in chosen)


def split_long(sentence: str, limit: int) -> list[str]:
    """A long sentence as scenes of about ``limit`` words, cut where it pauses."""
    if len(words_in(sentence)) <= limit:
        return [sentence]
    clauses, start = [], 0
    for match in _CLAUSE.finditer(sentence):
        clauses.append(sentence[start:match.end()].strip())
        start = match.end()
    clauses.append(sentence[start:].strip())
    pieces: list[str] = []
    for clause in (c for c in clauses if c):
        size = len(words_in(clause))
        if pieces and (len(words_in(pieces[-1])) < MIN_PIECE_WORDS
                       or len(words_in(pieces[-1])) + size <= limit):
            pieces[-1] = f"{pieces[-1]} {clause}"
        else:
            pieces.append(clause)
    if len(pieces) > 1 and len(words_in(pieces[-1])) < MIN_PIECE_WORDS:
        pieces[-2:] = [f"{pieces[-2]} {pieces[-1]}"]
    # A scene's last word should not leave a comma hanging in the captions.
    return [p.rstrip(",;:–—- ").strip() if i < len(pieces) - 1 else p for i, p in enumerate(pieces)]


@dataclass(frozen=True)
class Analysis:
    words: int
    sentences: int
    seconds: float  # read in full, at the default voice's pace
    topics: tuple[str, ...]


def analyse(text: str, persona=None, padding: float = 0.3) -> Analysis:
    """What the window shows as soon as text is pasted."""
    persona = persona or voices.persona(None)
    sentences = split_sentences(text)
    seconds = sum(pacing.estimate_voice(s, persona.rate, persona.pause_scale, persona.enhanced)
                  + padding for s in sentences)
    return Analysis(len(words_in(text)), len(sentences), seconds, tuple(topic_words(text)))


# --- suggesting the answers --------------------------------------------------------

# Words that give a kind of video away. Deliberately short lists of unambiguous
# words: a wrong suggestion is worse than none, and the user can change it.
_TELLING = {
    "Tech and how-to": """app apps settings click install installed download computer laptop
        phone windows android iphone software browser keyboard file files folder password
        wifi internet update updates storage shortcut code website button step steps""",
    "Story and facts": """century ancient history discovered scientists scientist war king
        queen empire legend mystery planet earth space ocean species storm years ago
        once story fact facts researchers study""",
    "Motivation": """dream dreams goal goals discipline habit habits success fail failure
        believe mindset motivation courage fear quit stronger yourself confidence grow""",
    "Business and money": """money invest investing investment profit salary income budget
        business customers customer market sales revenue startup price prices savings debt
        tax stocks dollars company""",
    "Kids and family": """kids kid children child family mom dad parents toys toy puppy
        kitten animals bedtime school playground fun game games story""",
}
_TELLING = {name: frozenset(words.split()) for name, words in _TELLING.items()}
_CHILD_WORDS = frozenset("kids kid children child toddler toddlers toys bedtime".split())
_TEEN_WORDS = frozenset("teen teens teenager teenagers homework exam exams classmates".split())
CLEAR_MATCH = 2  # telling words needed before the app dares to say what a text is


@dataclass(frozen=True)
class Suggestion:
    audience: Audience
    length: str
    reason: str  # one sentence for the window


def suggest_length(text: str, audience: Audience) -> str:
    """The platform's usual length, or a shorter one the whole text already fits."""
    persona = voices.persona(audience.voice)
    scene_lists = [split_long(s, audience.scene_words) for s in split_sentences(text)]
    length = audience.length
    fitting = shortest_fit(scene_lists, persona) if scene_lists else None
    wanted = pacing.TARGETS.get(length)
    # A text that already fits something shorter should not be stretched.
    if wanted is not None and fitting and pacing.TARGETS[fitting] < wanted:
        length = fitting
    return length


def suggest(text: str, last: Audience | None = None, padding: float = 0.3) -> Suggestion:
    """Answers to the window's questions, picked from the text.

    Word rules, no AI. The kind of video comes from the telling words; where
    the text gives nothing away the user's last choice stays. The platform is
    always their last choice - the text cannot know where it will be posted.
    """
    last = last or Audience()
    words = [w.lower() for w in words_in(text)]
    seen = set(words)
    hits = {name: len(seen & telling) for name, telling in _TELLING.items()}
    best = max(hits, key=lambda name: hits[name])
    runner_up = max((count for name, count in hits.items() if name != best), default=0)
    clear = hits[best] >= CLEAR_MATCH and hits[best] > runner_up
    content = best if clear else last.content

    if seen & _CHILD_WORDS or content == "Kids and family" and clear:
        age = "Kids"
    elif seen & _TEEN_WORDS:
        age = "Teens"
    else:
        age = last.age if not clear else "Adults"
    audience = Audience(content, age, last.platform)

    persona = voices.persona(audience.voice)
    length = suggest_length(text, audience)
    seconds = round(analyse(text, persona, padding).seconds)
    if clear:
        reason = (f"This reads like {content.lower()} for {age.lower()}, "
                  f"about {seconds} s when read in full.")
    else:
        reason = (f"The text doesn't say what kind of video this is, so your last choices "
                  f"are kept. It is about {seconds} s when read in full.")
    return Suggestion(audience, length, reason)


# --- choosing what fits ------------------------------------------------------------

def _spoken(text: str, persona) -> float:
    return pacing.estimate_voice(text, persona.rate, persona.pause_scale, persona.enhanced)


def fits(scene_texts: list[str], target: float, persona) -> bool:
    """Whether these scenes reach ``target`` without rushing the voice."""
    voice = sum(_spoken(t, persona) for t in scene_texts)
    return voice / COMFORTABLE_TEMPO + len(scene_texts) * pacing.PAD_MIN <= target


def importance(sentences: list[str], counts: Counter) -> list[float]:
    """How much each sentence carries of what the text is about."""
    scores = []
    for sentence in sentences:
        own = _content_words(sentence)
        score = sum(counts[w] for w in set(own)) / math.sqrt(len(own) + 1)
        if re.search(r"\d", sentence):
            score *= 1.2  # a number is usually the point
        scores.append(score)
    if scores:
        scores[-1] *= 1.15  # the closing line is the payoff or the call to action
    return scores


def select_for_length(sentences: list[str], scenes_of, target: float, persona,
                      keep=()) -> list[int]:
    """Indexes of the sentences to keep so the video fits ``target`` seconds.

    The first sentence is the hook and always stays, as does anything in
    ``keep`` (what the user put back). The rest are added most important
    first, and the result is in the original order. ``scenes_of(i)`` gives the
    scene texts sentence ``i`` becomes.
    """
    counts = Counter(_content_words(" ".join(sentences)))
    scores = importance(sentences, counts)
    chosen = {0, *(i for i in keep if 0 <= i < len(sentences))} if sentences else set()

    def texts(indexes):
        return [t for i in sorted(indexes) for t in scenes_of(i)]

    for index in sorted(range(len(sentences)), key=lambda i: (-scores[i], i)):
        if index not in chosen and fits(texts(chosen | {index}), target, persona):
            chosen.add(index)
    return sorted(chosen)


# --- the script --------------------------------------------------------------------

@dataclass(frozen=True)
class DraftScene:
    visual: str
    voice: str
    sentence: int  # index of the sentence it came from


def _one_line(text: str) -> str:
    return _clean(text)


def _visual(words: str) -> str:
    # No colons: "local:" has a meaning in a script, and search words must never read as one.
    return _clean(words.replace(":", " ")) or "abstract background"


# --- cards ---------------------------------------------------------------------------
# Which scenes get a card (cards.py) is decided by rules, not by the Smart writer's
# model: a rule always sees "Windows key and V", and works for Quick split too.

_KEY_NAMES = {
    "windows": "Win", "win": "Win", "control": "Ctrl", "ctrl": "Ctrl", "alt": "Alt",
    "shift": "Shift", "tab": "Tab", "enter": "Enter", "escape": "Esc", "esc": "Esc",
    "delete": "Del", "backspace": "Backspace", "space": "Space", "spacebar": "Space",
    **{f"f{n}": f"F{n}" for n in range(1, 13)},
}
_ARROWS = {"left": "Left", "right": "Right", "up": "Up", "down": "Down"}
_PRESS = {"press", "pressing", "hold", "holding", "hit", "tap"}
_KEY_FILLER = _PRESS | {"the", "key", "keys", "and", "plus", "then", "a", "letter", "arrow",
                        "button"}
_MONEY = re.compile(r"\$\d[\d,.]*\d(?:\s(?:million|billion|trillion))?|\$\d")
_PERCENT = re.compile(r"\d+(?:\.\d+)?\s?%|\d+(?:\.\d+)? percent")
_BIG = re.compile(r"\b\d{1,3}(?:,\d{3})+\b|\b\d+(?:\.\d+)? (?:million|billion|trillion)\b")


def _key_card(voice: str) -> str | None:
    """"Win + V" for a line that tells the viewer to press keys, else None.

    The line has to say press (or hold, hit, tap): "Windows has a tool" and
    "the night shift" name no keys.
    """
    words = [w.strip(".,;:!?\"'()") for w in voice.split()]
    lowered = [w.lower() for w in words]
    for at, word in enumerate(lowered):
        if word not in _PRESS:
            continue
        keys = []
        for raw, low in zip(words[at + 1:], lowered[at + 1:]):
            if low in _KEY_NAMES:
                keys.append(_KEY_NAMES[low])
            elif low in _ARROWS:
                keys.append(_ARROWS[low])
            elif len(raw) == 1 and raw.isalnum() and keys and low != "a":
                keys.append(raw.upper())
            elif low not in _KEY_FILLER:
                break
        keys = list(dict.fromkeys(keys))
        if 2 <= len(keys) <= 4 and keys[0] in _KEY_NAMES.values():
            return " + ".join(keys)
    return None


def suggest_card(voice: str) -> str | None:
    """A card for this spoken line, or None: keys to press, else the number it is about.

    Years and small numbers are left alone; a card is for the one figure worth seeing.
    """
    keys = _key_card(voice)
    if keys:
        return keys
    for pattern in (_MONEY, _PERCENT, _BIG):
        found = pattern.search(voice)
        if found:
            return found.group(0).replace(" percent", "%")
    return None


def add_cards(voices: list[str]) -> list[str | None]:
    """A card or None for each line; at most half the scenes get one, so it stays special."""
    cards = [suggest_card(voice) for voice in voices]
    allowed = max(1, len(voices) // 2)
    # Key cards first: they are what the scene is for. Then numbers, earliest first.
    order = sorted((i for i, card in enumerate(cards) if card),
                   key=lambda i: ("+" not in cards[i], i))
    keep = set(order[:allowed])
    return [card if i in keep else None for i, card in enumerate(cards)]


def to_script(scenes, cards: bool = True) -> str:
    """Script text for ``scenes`` (anything with .visual and .voice).

    With ``cards``, scenes that name keys or a figure get a ``Card:`` line.
    Always parsed before it is returned, so the editor never receives a script
    the app would then refuse.
    """
    spoken = [s for s in scenes if _one_line(s.voice)]
    blocks = [f"Visual: {_visual(s.visual)}\nVoice: {_one_line(s.voice)}" for s in spoken]
    if cards:
        found = add_cards([_one_line(s.voice) for s in spoken])
        blocks = [block + (f"\nCard: {card}" if card else "")
                  for block, card in zip(blocks, found)]
    if not blocks:
        raise DraftError("There is no text to turn into a script.")
    script = "\n\n".join(blocks)
    parse_script(script)
    return script


@dataclass
class Draft:
    script: str
    scenes: list[DraftScene]
    seconds: float  # about how long it is at the voice's normal pace
    length: str  # the entry of LENGTHS it was written for
    voice: str  # persona ID
    aspect: str
    dropped: list[tuple[int, str]] = field(default_factory=list)  # (sentence index, text)
    kept: list[int] = field(default_factory=list)
    longer: str | None = None  # the shortest length that would keep every sentence
    source: str = "Quick split"

    @property
    def target(self) -> str:
        """The Create page's Length value for this draft."""
        return pacing.DEFAULT_TARGET if self.length == KEEP_ALL else self.length


def shortest_fit(scene_lists: list[list[str]], persona) -> str | None:
    """The shortest fixed length that holds every sentence, if there is one."""
    everything = [t for scene_texts in scene_lists for t in scene_texts]
    for label, seconds in pacing.TARGETS.items():
        if seconds is not None and fits(everything, seconds, persona):
            return label
    return None


def write(text: str, audience: Audience | None = None, length: str | None = None,
          keep=(), padding: float = 0.3, cards: bool = True) -> Draft:
    """The script for ``text``, keeping the user's words.

    ``length`` is an entry of LENGTHS (default: the audience's platform).
    ``keep`` holds the sentence indexes the user asked to put back.
    """
    audience = audience or Audience()
    length = length if length in LENGTHS else audience.length
    persona = voices.persona(audience.voice)
    sentences = split_sentences(text)
    if not sentences:
        raise DraftError("Paste some text first.")

    scene_lists = [split_long(s, audience.scene_words) for s in sentences]
    target = pacing.TARGETS.get(length)
    if target is None:
        kept = list(range(len(sentences)))
    else:
        kept = select_for_length(sentences, lambda i: scene_lists[i], target, persona, keep)

    counts = Counter(_content_words(text))
    topics = topic_words(text)
    scenes = [DraftScene(search_words(piece, counts, topics), piece, i)
              for i in kept for piece in scene_lists[i]]
    dropped = [(i, s) for i, s in enumerate(sentences) if i not in set(kept)]
    longer = None
    if dropped:
        longer = shortest_fit(scene_lists, persona) or KEEP_ALL
        if longer == length:
            longer = KEEP_ALL
    seconds = sum(_spoken(s.voice, persona) + padding for s in scenes)
    return Draft(to_script(scenes, cards), scenes, seconds, length, persona.id,
                 audience.aspect, dropped, kept, longer)
