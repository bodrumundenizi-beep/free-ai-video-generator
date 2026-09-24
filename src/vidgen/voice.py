"""Neural voiceover through Microsoft Edge's text-to-speech, made to sound human.

Studio personas (see voices.py) get three things plain TTS lacks:

1. Their own speed and pitch.
2. Breath pauses. edge-tts escapes everything it's sent, so SSML <break>
   tags can't reach the service. Instead each line is synthesised once - which
   keeps its natural intonation - with word timings, and silence is inserted
   locally after words followed by punctuation: short at commas, a breath at
   full stops. Splitting the line into separately-read phrases was rejected;
   each phrase restarts its intonation and the result sounds choppy.
3. Broadcast mastering (mastering.py): EQ warmth, compression, -14 LUFS.

Classic personas are read exactly as 3.0 read them.
"""

from __future__ import annotations

import os
import re

from . import voices

# Extra silence (seconds) inserted after a word, before persona scaling.
PAUSE_COMMA = 0.12  # , ; :
PAUSE_DASH = 0.15  # - (as a dash), en/em dash, ellipsis
PAUSE_STOP = 0.26  # . ! ?  - a breath
SYNTH_RATE = 24000  # edge-tts delivers 24 kHz mono
SYNTH_ATTEMPTS = 3

# Kept for anything still importing the old name.
VOICE_MAP = {p.id: p.voice for p in voices.CATALOG if not p.enhanced}


def plan_pauses(words, text: str, scale: float = 1.0):
    """[(time, seconds)] of silence to insert, from edge-tts word timings.

    ``words`` are (start, duration, word) tuples in order. Each word is found
    in ``text`` to see what punctuation follows it; the pause goes at the
    midpoint of the gap before the next word, so neither word gets clipped.
    Nothing is added after the last word - the scene's Padding: handles that.
    """
    pauses = []
    cursor = 0
    for i, (start, duration, word) in enumerate(words):
        found = text.find(word, cursor)
        if found < 0:
            continue
        cursor = found + len(word)
        if i == len(words) - 1:
            break
        following = re.match(r"[^\w]*", text[cursor:]).group(0)
        if re.search(r"[.!?]", following) and "..." not in following:
            length = PAUSE_STOP
        elif re.search(r"\.\.\.|…|—|–| - ", following):
            length = PAUSE_DASH
        elif re.search(r"[,;:]", following):
            length = PAUSE_COMMA
        else:
            continue
        end = start + duration
        next_start = words[i + 1][0]
        pauses.append(((end + max(end, next_start)) / 2, round(length * scale, 3)))
    return pauses


def insert_pauses(samples, rate: int, pauses):
    """``samples`` with silence spliced in at each (time, seconds)."""
    import numpy as np

    if not pauses:
        return samples
    pieces, last = [], 0
    for time, seconds in sorted(pauses):
        cut = min(max(int(time * rate), last), len(samples))
        pieces.append(samples[last:cut])
        pieces.append(np.zeros(int(seconds * rate), dtype=samples.dtype))
        last = cut
    pieces.append(samples[last:])
    return np.concatenate(pieces)


def _synthesise(text, persona, mp3_path, want_words):
    import asyncio

    import edge_tts

    async def run():
        speaker = edge_tts.Communicate(
            text, persona.voice, rate=persona.rate, pitch=persona.pitch,
            boundary="WordBoundary" if want_words else "SentenceBoundary",
        )
        words = []
        with open(mp3_path, "wb") as fh:
            async for chunk in speaker.stream():
                if chunk["type"] == "audio":
                    fh.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    words.append((chunk["offset"] / 1e7, chunk["duration"] / 1e7, chunk["text"]))
        return words

    # The free service occasionally returns nothing, especially after many
    # requests in a row; a short wait and a retry almost always succeeds.
    # Safe: this runs on a worker thread, which has no event loop of its own.
    import time

    for attempt in range(SYNTH_ATTEMPTS):
        try:
            return asyncio.run(run())
        except Exception:
            if attempt == SYNTH_ATTEMPTS - 1:
                raise
            time.sleep(1.5 * (attempt + 1))


def generate_voiceover(text, filename, log_func, voice_choice):
    """Voice ``text`` into ``filename``; returns the path actually written.

    Studio personas produce a mastered WAV beside ``filename`` (same name,
    .wav); classic ones produce the MP3 exactly as before. If mastering fails
    the line is still used, unmastered, with a warning - polish never sinks a
    render.
    """
    persona = voices.persona(voice_choice)
    log_func(f"🗣️ Generating {persona.short} Voice: '{text}'")

    base = os.path.splitext(filename)[0]
    mp3_path = base + ".tts.mp3" if persona.enhanced else filename
    try:
        words = _synthesise(text, persona, mp3_path, want_words=persona.enhanced)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user
        raise Exception(f"Voice generation failed! ({exc})") from exc

    if not os.path.exists(mp3_path) or os.path.getsize(mp3_path) == 0:
        raise Exception(
            "Voice generation failed! No audio came back from the voice service "
            "- check your internet connection."
        )
    if not persona.enhanced:
        return mp3_path

    from . import mastering

    paced = base + ".paced.wav"
    wav_path = base + ".wav"
    try:
        samples = mastering.decode(mp3_path, SYNTH_RATE)
        pauses = plan_pauses(words, text, persona.pause_scale)
        mastering.write_wav(paced, insert_pauses(samples, SYNTH_RATE, pauses), SYNTH_RATE)
        mastering.master(paced, wav_path)
        return wav_path
    except Exception as exc:  # noqa: BLE001
        log_func(f"⚠️ Studio processing failed ({exc}); using the plain voice.")
        return mp3_path
