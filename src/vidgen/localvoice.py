"""The voice, on this PC: Kokoro, an open-source speech model, with no service behind it.

Until 3.8 every line was read by Microsoft Edge's online voices. That service
changed on 2026-10-10 and refused everyone, so the voice now runs locally, the
way the Smart writer does. Two files are downloaded once (the model and its
speakers); espeak-ng, a separate program bundled with the app, turns words
into sounds; onnxruntime runs the model.

say() returns the audio and when each word is spoken, the same (start,
duration, word) timings the online voice gave, so the breath pauses, the
mastering and the captions in voice.py and render.py work unchanged.

espeak-ng is GPL software. It is run as its own program and never loaded into
this one, which is what keeps the app itself MIT.

Pure of Tk. The model, the speakers and espeak-ng can all be replaced in tests.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from dataclasses import dataclass

from . import writer
from .writer import Model

__all__ = ["FILES", "Engine", "VoiceError", "download", "installed", "ready", "say",
           "speakable"]


class VoiceError(Exception):
    """The offline voice could not be used. The message is for the user."""


# The export that also reports how long each sound lasts, which the captions need.
MODEL = Model(
    name="Kokoro 82M",
    file="kokoro-v1.0.onnx",
    url="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"
        "kokoro-v1.0.onnx",
    size=325_505_369,
    sha256="beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a",
    licence="Apache 2.0",
)
SPEAKERS = Model(
    name="Kokoro speakers",
    file="kokoro-voices-v1.0.bin",
    url="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"
        "voices-v1.0.bin",
    size=28_214_398,
    sha256="bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
    licence="Apache 2.0",
)
FILES = (MODEL, SPEAKERS)
SIZE = sum(m.size for m in FILES)

ESPEAK_EXE = "espeak-ng.exe"
RATE = 24000
MAX_TOKENS = 510  # what the model accepts in one go
BATCH_TOKENS = 400  # where a long line is cut, at a punctuation mark
CHUNK_WORDS = 30  # a run with no punctuation at all is cut here
LEAD, TAIL = 0.02, 0.05  # seconds of the model's own silence kept around a line
CONFIG_KEY = "kokoro_config"


# --- the files ---------------------------------------------------------------------

def installed() -> bool:
    return all(writer.installed(m) for m in FILES)


def espeak_path() -> str | None:
    return writer.bundled("espeak", ESPEAK_EXE)


def ready() -> tuple[bool, str]:
    """(whether the voice can run now, and if not, why - in words for the user)."""
    if espeak_path() is None:
        return False, "The voice's pronunciation program is missing from this copy of the app."
    if not installed():
        return False, "The voice has not been downloaded yet."
    return True, ""


def download(progress=None, should_stop=None, session=None) -> None:
    """Fetch both files (resumable, checked). ``progress(done, total)`` covers the two."""
    done_before = 0
    for model in FILES:
        def report(done, _total, base=done_before):
            if progress is not None:
                progress(base + done, SIZE)
        try:
            writer.download(report, should_stop, session, model)
        except writer.WriterError as exc:
            raise VoiceError(str(exc).replace("The Smart writer", "The voice")) from exc
        done_before += model.size


# --- words the model can say --------------------------------------------------------

_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
         "fourteen fifteen sixteen seventeen eighteen nineteen").split()
_TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()


def _two_digits(n: int) -> str:
    if n < 20:
        return _ONES[n]
    return _TENS[n // 10 - 2] + ("" if n % 10 == 0 else " " + _ONES[n % 10])


def _year(n: int) -> str:
    """1971 as "nineteen seventy one"; espeak-ng says "nineteen hundred seventy one"."""
    high, low = divmod(n, 100)
    if n % 1000 == 0:
        return _ONES[n // 1000] + " thousand"
    if high % 10 == 0:  # 2001 to 2009: "two thousand one"
        return _ONES[high // 10] + " thousand " + _ONES[low] if low < 10 else \
            _two_digits(high) + " " + _two_digits(low)
    if low == 0:
        return _two_digits(high) + " hundred"
    return _two_digits(high) + (" oh " + _ONES[low] if low < 10 else " " + _two_digits(low))


def speakable(word: str) -> str:
    """``word`` the way it should be read out: years and dollar amounts spelled the usual way."""
    word = word.replace("’", "'").replace("‘", "'")
    money = re.fullmatch(r"\$(\d[\d,.]*)([a-zA-Z]*)", word)
    if money:
        return f"{money.group(1)}{money.group(2)} dollars"
    if re.fullmatch(r"1[1-9]\d\d|20\d\d", word):
        return _year(int(word))
    return word


@dataclass
class _Piece:
    word: str  # as written, without the punctuation around it; "" for a lone dash
    spoken: str  # what espeak-ng is given for it
    mark: str  # the punctuation that follows, as the model knows it


_SHAPE = re.compile(r"""^["“”'‘(\[]*(.*?)["“”'’)\]]*([.,;:!?…]*)["“”'’)\]]*$""", re.S)


def _pieces(text: str) -> list[_Piece]:
    out = []
    for raw in text.split():
        if re.fullmatch(r"[-–—]+", raw):
            if out and not out[-1].mark:
                out[-1].mark = "—"
            continue
        core, marks = _SHAPE.match(raw).groups()
        mark = "…" if "..." in marks or "…" in marks else marks[:1]
        if not re.search(r"\w", core):
            if out and mark and not out[-1].mark:
                out[-1].mark = mark
            continue
        out.append(_Piece(core, speakable(core), mark))
    return out


def _chunks(pieces: list[_Piece]) -> list[list[_Piece]]:
    """Runs of words that end at a punctuation mark: espeak-ng reads one run at a time."""
    chunks, current = [], []
    for piece in pieces:
        current.append(piece)
        if piece.mark or len(current) >= CHUNK_WORDS:
            chunks.append(current)
            current = []
    if current:
        chunks.append(current)
    return chunks


def _espeak(text: str, british: bool = False) -> str:
    """The sounds of ``text`` in IPA, words separated by spaces."""
    exe = espeak_path()
    if exe is None:
        raise VoiceError("The voice's pronunciation program is missing from this copy of the app.")
    try:
        result = subprocess.run(
            [exe, "-q", "--ipa", "-v", "en-gb" if british else "en-us",
             "--path", os.path.dirname(exe), "--stdin"],
            # Without the newline espeak-ng drops the end of the last word.
            input=(text + "\n").encode("utf-8"), capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as exc:
        raise VoiceError(f"The voice's pronunciation program could not run ({exc}).") from exc
    return " ".join(result.stdout.decode("utf-8", "replace").split())


# --- the model ----------------------------------------------------------------------

def _spread(tokens: list[int], weights: list[int]) -> list[list[int]]:
    """``tokens`` shared out between words in proportion to ``weights``, at least one each."""
    total, count = sum(weights), len(tokens)
    out, start, running = [], 0, 0
    for i, weight in enumerate(weights):
        running += weight
        end = count if i == len(weights) - 1 else round(running / total * count)
        end = min(max(end, start + 1), count - (len(weights) - 1 - i))
        out.append(tokens[start:end])
        start = end
    return out


def _weight(word: str) -> int:
    """About how much there is to say in ``word``: a digit is a whole word aloud."""
    return max(len(re.sub(r"[\W\d]", "", word)) + 4 * len(re.findall(r"\d", word))
               + 7 * word.count("%")
               + 5 * len(re.findall(r"\d\.\d", word)), 1)


def _align(sounds: list[int], letters: list[int]) -> list[tuple[int, int, int, int]]:
    """Match sound-words to written words when espeak-ng did not keep them one to one.

    It runs some words together ("out of" is one sound-word) and splits others.
    ``sounds`` is how many sounds each sound-word has, ``letters`` how long each
    written word is. Returns (first word, words, first sound-word, sound-words)
    groups covering both lists in order, where one side of every group is 1:
    the pairing whose lengths agree best.
    """
    scale = sum(sounds) / max(sum(letters), 1)
    n, m = len(letters), len(sounds)
    best = {(0, 0): (0.0, None)}
    for i in range(n + 1):
        for j in range(m + 1):
            if (i, j) not in best:
                continue
            cost = best[(i, j)][0]
            for words, parts in ((1, 1), (2, 1), (3, 1), (1, 2), (1, 3)):
                if i + words > n or j + parts > m:
                    continue
                gap = abs(sum(sounds[j:j + parts]) - scale * sum(letters[i:i + words]))
                total = cost + gap + (0.0 if words == parts else 1.0)
                key = (i + words, j + parts)
                if key not in best or total < best[key][0]:
                    best[key] = (total, (i, j, words, parts))
    if (n, m) not in best:
        return []
    groups, key = [], (n, m)
    while best[key][1] is not None:
        i, j, words, parts = best[key][1]
        groups.append((i, words, j, parts))
        key = (i, j)
    return groups[::-1]


class Engine:
    """The loaded model. One is kept for the life of the app (see say())."""

    def __init__(self, session=None, speakers=None, vocab=None, phonemes=_espeak):
        if session is None:
            import numpy as np
            import onnxruntime

            try:
                session = onnxruntime.InferenceSession(
                    writer.model_path(MODEL), providers=["CPUExecutionProvider"])
                speakers = np.load(writer.model_path(SPEAKERS))
                config = session.get_modelmeta().custom_metadata_map[CONFIG_KEY]
                vocab = json.loads(config)["vocab"]
            except Exception as exc:  # noqa: BLE001 - a damaged file, an old CPU
                raise VoiceError(f"The voice could not be loaded ({exc}).") from exc
        self.session, self.speakers, self.vocab, self.phonemes = session, speakers, vocab, phonemes
        self._lock = threading.Lock()  # one line at a time: the preview and a render can meet

    def style(self, speaker: str, tokens: int):
        """The speaker's style for a line of this many sounds. "a+b" blends two speakers."""
        names = speaker.split("+")
        try:
            packs = [self.speakers[name] for name in names]
        except KeyError as exc:
            raise VoiceError(f"The voice has no speaker called {exc}.") from exc
        row = max(min(tokens, len(packs[0])) - 1, 0)
        return sum(pack[row] for pack in packs) / len(packs)

    def say(self, text: str, speaker: str, speed: float = 1.0):
        """(samples at 24 kHz, [(start, duration, word)]) for one line."""
        import numpy as np

        british = speaker.startswith("b")
        batches, batch, size = [], [], 0
        for chunk in _chunks(_pieces(text)):
            sounds = self.phonemes(" ".join(p.spoken for p in chunk), british)
            words = [[self.vocab[c] for c in word if c in self.vocab] for word in sounds.split()]
            words = [w for w in words if w]
            if not words:
                continue
            cost = sum(len(w) + 1 for w in words) + 2
            if batch and size + cost > BATCH_TOKENS:
                batches.append(batch)
                batch, size = [], 0
            batch.append((chunk, words))
            size += cost
        if batch:
            batches.append(batch)
        if not batches:
            raise VoiceError("There is nothing in this line the voice can say.")

        audio, timings, offset = [], [], 0.0
        with self._lock:
            for batch in batches:
                samples, spans = self._run(batch, speaker, speed)
                audio.append(samples)
                timings += [(start + offset, length, word) for start, length, word in spans]
                offset += len(samples) / RATE
        return np.concatenate(audio), timings

    def _run(self, batch, speaker: str, speed: float):
        import numpy as np

        space = self.vocab.get(" ")
        tokens: list[int] = []
        placed = []  # (chunk, [positions of each sound-word's tokens])
        for chunk, words in batch:
            spots = []
            for word in words:
                spots.append(list(range(len(tokens), len(tokens) + len(word))))
                tokens += word
                if space is not None:
                    tokens.append(space)
            mark = self.vocab.get(chunk[-1].mark)
            if mark is not None:  # the mark goes straight after the word, before the space
                tokens.insert(len(tokens) - (space is not None), mark)
            placed.append((chunk, spots))
        if tokens and tokens[-1] == space:
            tokens.pop()
        tokens = tokens[:MAX_TOKENS]

        outputs = self.session.run(None, {
            "input_ids": np.array([[0, *tokens, 0]], dtype=np.int64),
            "style": np.asarray(self.style(speaker, len(tokens)), dtype=np.float32),
            "speed": np.array([speed], dtype=np.float32),
        })
        samples = np.asarray(outputs[0], dtype=np.float32).ravel()
        frames = np.concatenate([[0], np.cumsum(np.asarray(outputs[1]).ravel())])
        # edges[i + 1] is where token i starts: edges[0:2] span the silent lead-in
        edges = frames * (len(samples) / max(frames[-1], 1)) / RATE
        first = max(edges[1] - LEAD, 0.0)
        last = min(edges[len(tokens) + 1] + TAIL, len(samples) / RATE)

        spans = []
        for chunk, spots in placed:
            spoken = [word for p in chunk for word in p.spoken.split()]
            letters = [_weight(word) for word in spoken]
            # Usually one sound-word per spoken word, but never assumed: a merge ("out of")
            # and a split ("200,000") in one run leave the counts equal and the words shifted.
            each = [[] for _ in spoken]
            for i, words, j, parts in _align([len(s) for s in spots], letters):
                flat = [t for word in spots[j:j + parts] for t in word]
                shares = _spread(flat, letters[i:i + words]) if len(flat) >= words else []
                for k, share in enumerate(shares):
                    each[i + k] = share
            groups, at = [], 0
            for p in chunk:  # a year or an amount is several spoken words but one caption word
                count = len(p.spoken.split())
                groups.append([t for word in each[at:at + count] for t in word])
                at += count
            for piece, group in zip(chunk, groups):
                group = [t for t in group if t < len(tokens)]
                if group:
                    start, end = edges[group[0] + 1], edges[group[-1] + 2]
                    spans.append((float(start - first), float(end - start), piece.word))
        return samples[int(first * RATE):int(last * RATE)], spans


_engine: Engine | None = None
_engine_lock = threading.Lock()


def say(text: str, speaker: str, speed: float = 1.0):
    """Read ``text`` aloud. The model is loaded on first use and kept: loading takes a second
    or two, and a video has many lines."""
    global _engine
    ok, why = ready()
    if not ok:
        raise VoiceError(why)
    with _engine_lock:
        if _engine is None:
            _engine = Engine()
    return _engine.say(text, speaker, speed)
