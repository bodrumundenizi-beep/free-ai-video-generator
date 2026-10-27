"""My voice (beta): the user's own voice, cloned by KittenTTS 2 from a recording of six sentences.

KittenTTS 2 runs in the voice pack (voicepack.py): PyTorch on an NVIDIA graphics card. On the
processor it is about 10 times slower than the video, so My voice is only offered with a card
of 6 GB or more. The recording, and the reference cut from it, stay in MY_FOLDER on this PC.

Before 4.7 "My voice" changed the offline voice's colour with OpenVoice: a young man came out
sounding like a rough woman's voice (2026-10-17), and it was hidden. KittenTTS 2 speaks the
words itself in the cloned voice: "about 70 percent" like the user in a test (2026-10-23).
Pure of Tk.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import wave

import numpy as np

from . import voicepack

PERSONA_ID = "my-voice"
MY_FOLDER = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                         "AIVideoStudio", "myvoice")
RECORDING = "recording.wav"
REFERENCE = "reference.wav"   # what KittenTTS learns from: the recording up to a sentence end
VOICE_FILE = "voice.json"
SAMPLE = "sample.wav"         # a test line in the cloned voice, played after making it
SAMPLE_LINE = "Hello, this is my own voice in AI Video Studio."
SIZE = voicepack.SIZE
REFERENCE_SENTENCES = 5       # 5 of the 6, about 27 s: inside KittenTTS's 5-30 s, best by ear
SENTENCES = (
    "Hello, this is how my voice sounds when I read out loud.",
    "I am reading these lines at my normal speed, in a quiet room.",
    "The quick brown fox jumps over the lazy dog, again and again.",
    "On sunny days I like to walk to the market and buy fresh bread.",
    "Numbers like twenty one, three hundred and two thousand sound like this.",
    "That is all it needs, and now the app can speak in my voice.",
)

_available: str | None = None
_speaker = None
_voice: dict | None = None


def available() -> str:
    """"" when this PC can use My voice (checked once), else why not, for the user."""
    global _available
    if _available is None:
        _available = voicepack.usable(voicepack.graphics())
    return _available


def installed() -> bool:
    return voicepack.installed()


def ready() -> bool:
    """Whether "My voice" can speak: the card, the pack and a voice made from a recording."""
    made = voice()
    return bool(made and os.path.isfile(made.get("reference", "")) and installed()
                and available() == "")


def download(progress=None, should_stop=None, session=None) -> None:
    """Install the voice pack. ``progress(text, fraction)``."""
    voicepack.install(progress, should_stop, session)


# --- the recording -----------------------------------------------------------------------

def read_wav(path: str) -> tuple[np.ndarray, int]:
    with wave.open(path, "rb") as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        data = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError("the recording must be 16-bit")
    samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate


def write_wav(path: str, samples: np.ndarray, rate: int) -> None:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def sentence_ends(samples: np.ndarray, rate: int) -> list[float]:
    """Seconds where a pause of 0.35 s or more falls between sentences (by loudness)."""
    frame = max(int(rate * 0.05), 1)
    energy = np.array([np.sqrt(np.mean(samples[i:i + frame] ** 2))
                       for i in range(0, max(len(samples) - frame, 0), frame)])
    if not len(energy):
        return []
    quiet = energy < max(float(energy.max()) * 0.04, 1e-4)
    loud = np.flatnonzero(~quiet)
    if not len(loud):
        return []
    start, ends, run = loud[0] * 0.05, [], 0
    for i, still in enumerate(quiet):
        run = run + 1 if still else 0
        if run == 7 and (i - 3) * 0.05 > start + 1.0:
            ends.append((i - 3) * 0.05)
    # Two pauses closer than 1.5 s: a comma breath just before the sentence ends ("...my
    # normal speed, in a quiet room."). The later one is the end of the sentence.
    merged = []
    for end in ends:
        if merged and end - merged[-1] < 1.5:
            merged[-1] = end
        else:
            merged.append(end)
    return merged


def reference_cut(samples: np.ndarray, rate: int, wanted: int = REFERENCE_SENTENCES,
                  longest: float = 30.0) -> tuple[float, int]:
    """(seconds, sentences): where to cut so the reference holds whole sentences, under 30 s.

    The transcript given with the reference must be exactly what it says (2026-10-23: a cut in
    the middle of the last sentence sounded less like the user).
    """
    ends = [e for e in sentence_ends(samples, rate) if e <= longest]
    length = len(samples) / rate
    if not ends:
        return min(length, longest), len(SENTENCES) if length <= longest else 1
    count = min(wanted, len(ends))
    return ends[count - 1], count


# --- making the voice ---------------------------------------------------------------------

def make(recording: str, speaker_factory=None) -> dict:
    """Make "My voice" from ``recording``: cut the reference, save it, speak a test line.

    Returns what was saved. Raises ValueError for a recording that can't be used and
    voicepack.PackError when the pack can't speak.
    """
    global _voice
    samples, rate = read_wav(recording)
    if len(samples) < 5 * rate:
        raise ValueError("The recording is too short: read all six sentences.")
    if float(np.abs(samples).max()) < 0.02:
        raise ValueError("The recording is silent. Check that the microphone is on.")
    cut, count = reference_cut(samples, rate)
    os.makedirs(MY_FOLDER, exist_ok=True)
    reference = os.path.join(MY_FOLDER, REFERENCE)
    write_wav(reference, samples[: int(cut * rate)], rate)
    made = {"reference": reference, "text": " ".join(SENTENCES[:count]),
            "seconds": round(cut, 1), "sentences": count,
            "key": hashlib.sha1(open(reference, "rb").read()).hexdigest()}
    with open(os.path.join(MY_FOLDER, VOICE_FILE), "w", encoding="utf-8") as fh:
        json.dump(made, fh)
    _voice = made
    close_speaker()   # a new voice: a speaker started for the old one must not be reused
    factory = speaker_factory or voicepack.Speaker
    with factory() as speaker:
        speaker.say(SAMPLE_LINE, os.path.join(MY_FOLDER, SAMPLE), reference, made["text"])
    return made


def voice() -> dict | None:
    global _voice
    if _voice is None:
        try:
            with open(os.path.join(MY_FOLDER, VOICE_FILE), encoding="utf-8") as fh:
                loaded = json.load(fh)
        except (OSError, ValueError):
            return None
        if "reference" not in loaded:
            return None   # a voice made by the old OpenVoice version: make it again
        _voice = loaded
    return _voice


def voice_key() -> str:
    """Changes when the voice is made again: part of the voice cache's key for My voice."""
    return (voice() or {}).get("key", "")


# --- speaking --------------------------------------------------------------------------------

def speak(text: str, out: str, speaker_factory=None) -> float:
    """Speak ``text`` into ``out`` (a WAV, 24 kHz) in the user's voice; returns its length.

    One speaker serves a whole video (loading takes about 26 s); render_worker closes it.
    """
    global _speaker
    made = voice()
    if made is None:
        raise voicepack.PackError("My voice hasn't been made yet.")
    if _speaker is None:
        _speaker = (speaker_factory or voicepack.Speaker)()
    try:
        return _speaker.say(text, out, made["reference"], made["text"])
    except voicepack.PackError:
        close_speaker()   # it may have died: the next line starts a fresh one
        raise


def close_speaker() -> None:
    """Stop the speaker and give the graphics memory back. Safe to call any time."""
    global _speaker
    speaker, _speaker = _speaker, None
    if speaker is not None:
        speaker.close()


atexit.register(close_speaker)   # no speaker left holding the graphics card
