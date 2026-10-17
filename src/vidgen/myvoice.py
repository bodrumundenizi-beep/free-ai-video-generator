"""My voice (beta): the offline voice, changed to sound like a recording of the user.

OpenVoice V2's tone-colour converter (MIT), in an ONNX export run by onnxruntime, which the
app already has for Kokoro: no PyTorch. Kokoro still says the words (and times them for the
captions); the converter then gives the speech the user's voice. The recording and the
"tone" learnt from it stay in MY_FOLDER, on this PC.

Measured 2026-10-17 on the i7-13700F: 4 s of speech converted in about 2.6 s. Pure of Tk.
"""

from __future__ import annotations

import json
import os
import wave

import numpy as np

from . import writer
from .writer import Model

TONE_COLOR = Model(
    name="OpenVoice V2 tone converter",
    file="openvoice-tone-color.onnx",
    url="https://huggingface.co/eugenehp/openvoice/resolve/"
        "be5196ef50a8029ab820e5e3fe8cb055850d301a/tone_color.onnx",
    size=157_196_170,
    sha256="896195b84b0cb87a828bb8cab06577e9c024356bc9727b1a8f4174154bc0affa",
    licence="MIT",
)
TONE_EXTRACT = Model(
    name="OpenVoice V2 tone extractor",
    file="openvoice-tone-extract.onnx",
    url="https://huggingface.co/eugenehp/openvoice/resolve/"
        "be5196ef50a8029ab820e5e3fe8cb055850d301a/tone_extract.onnx",
    size=3_364_792,
    sha256="e91c2cb696e199d2519ed8b62ca6e3c8e42cb99ca13955dd6e188051486e681c",
    licence="MIT",
)
FILES = (TONE_COLOR, TONE_EXTRACT)
SIZE = sum(m.size for m in FILES)
PERSONA_ID = "my-voice"
# Hidden in 4.4: tried on a real recording (2026-10-17), a young man's voice came out sounding
# like a woman and rough, whatever was tuned. Kept for a better model; see README.
ENABLED = False
MY_FOLDER = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                         "AIVideoStudio", "myvoice")
RECORDING = "recording.wav"
VOICE_FILE = "voice.json"
RATE, NFFT, HOP = 22050, 1024, 256   # what the converter was trained at
KOKORO_RATE = 24000
TAU = 0.3
CHUNK = 3.0   # seconds: a tone is the average over chunks of this length, steadier than one
SENTENCES = (
    "Hello, this is how my voice sounds when I read out loud.",
    "I am reading these lines at my normal speed, in a quiet room.",
    "The quick brown fox jumps over the lazy dog, again and again.",
    "On sunny days I like to walk to the market and buy fresh bread.",
    "Numbers like twenty one, three hundred and two thousand sound like this.",
    "That is all it needs, and now the app can speak in my voice.",
)
# Built-in voices to start from: the one whose sound is closest to the recording is used.
BASES = ("am_michael", "am_adam", "am_echo", "am_puck", "bm_george", "af_heart", "af_bella",
         "bf_emma")
_BASE_TEXT = ("Here is a longer passage in this voice, so the app learns how it sounds. It talks "
              "about the weather, numbers like twenty one, and everyday things. The more it "
              "hears, the steadier its idea of the voice becomes.")


def installed() -> bool:
    return all(writer.installed(m) for m in FILES)


def ready() -> bool:
    """Whether "My voice" can speak: switched on, the models here and a voice made."""
    return ENABLED and installed() and os.path.isfile(os.path.join(MY_FOLDER, VOICE_FILE))


def download(progress=None, should_stop=None, session=None) -> None:
    done_before = 0
    for model in FILES:
        def report(done, _total, base=done_before):
            if progress is not None:
                progress(base + done, SIZE)
        writer.download(report, should_stop, session, model)
        done_before += model.size


# --- sound helpers --------------------------------------------------------------------

def resample(samples: np.ndarray, src: int, dst: int) -> np.ndarray:
    """Band-limited resampling by FFT (what scipy.signal.resample does), no aliasing."""
    samples = np.asarray(samples, dtype=np.float32)
    if src == dst or not len(samples):
        return samples
    n = max(int(round(len(samples) * dst / src)), 1)
    spectrum = np.fft.rfft(samples)
    keep = min(len(spectrum), n // 2 + 1)
    out = np.zeros(n // 2 + 1, dtype=np.complex128)
    out[:keep] = spectrum[:keep]
    return (np.fft.irfft(out, n) * (n / len(samples))).astype(np.float32)


def spectrogram(samples: np.ndarray) -> np.ndarray:
    """(frames, 513) magnitudes, as OpenVoice computes them (reflect pad, Hann, no centring)."""
    pad = (NFFT - HOP) // 2
    y = np.pad(np.asarray(samples, dtype=np.float32), (pad, pad), mode="reflect")
    window = np.hanning(NFFT + 1)[:-1].astype(np.float32)
    frames = max(1 + (len(y) - NFFT) // HOP, 1)
    y = np.pad(y, (0, max(NFFT + HOP * (frames - 1) - len(y), 0)))
    index = np.arange(NFFT)[None, :] + HOP * np.arange(frames)[:, None]
    spec = np.fft.rfft(y[index] * window, axis=1)
    return np.sqrt(spec.real ** 2 + spec.imag ** 2 + 1e-6).astype(np.float32)


def pitch(samples: np.ndarray, rate: int) -> float:
    """Median pitch in Hz of the voiced parts, by autocorrelation; 0 if none."""
    found, n = [], int(0.04 * rate)
    for start in range(0, len(samples) - n, n):
        frame = samples[start:start + n]
        if np.sqrt((frame ** 2).mean()) < 0.02:
            continue
        frame = frame - frame.mean()
        ac = np.correlate(frame, frame, "full")[n - 1:]
        lo, hi = int(rate / 400), int(rate / 70)
        lag = lo + int(np.argmax(ac[lo:hi]))
        if ac[lag] > 0.3 * ac[0]:
            found.append(rate / lag)
    return float(np.median(found)) if found else 0.0


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


# --- the models -----------------------------------------------------------------------

class Converter:
    def __init__(self, extract=None, convert=None):
        if extract is None or convert is None:
            import onnxruntime as ort

            options = ["CPUExecutionProvider"]
            extract = extract or ort.InferenceSession(writer.model_path(TONE_EXTRACT),
                                                      providers=options)
            convert = convert or ort.InferenceSession(writer.model_path(TONE_COLOR),
                                                      providers=options)
        self.extract, self.convert = extract, convert

    def tone(self, samples: np.ndarray) -> np.ndarray:
        """(1, 256): what a voice sounds like, averaged over CHUNK-second pieces."""
        step = int(CHUNK * RATE)
        parts = [samples[i:i + step] for i in range(0, max(len(samples) - step // 2, 1), step)]
        tones = [self.extract.run(None, {"input": spectrogram(p)[None]})[0] for p in parts if len(p)]
        return np.mean(tones, axis=0).astype(np.float32)

    def change(self, samples: np.ndarray, source: np.ndarray, target: np.ndarray) -> np.ndarray:
        spec = spectrogram(samples).T[None]
        out = self.convert.run(None, {
            "audio": spec, "audio_length": np.array([spec.shape[2]], dtype=np.int64),
            "src_tone": np.asarray(source, np.float32).reshape(1, 256, 1),
            "dest_tone": np.asarray(target, np.float32).reshape(1, 256, 1),
            "tau": np.array([TAU], dtype=np.float32)})[0]
        return np.asarray(out, dtype=np.float32).ravel()


_converter: Converter | None = None
_voice: dict | None = None


def _get_converter() -> Converter:
    global _converter
    if _converter is None:
        _converter = Converter()
    return _converter


def make(recording: str, say, converter: Converter | None = None) -> dict:
    """Learn the voice in ``recording`` (a WAV) and save it. ``say(text, speaker)`` is the
    offline voice (localvoice.say): it reads a passage in the base speaker, whose own tone the
    converter needs to know. Returns what was saved."""
    global _voice
    converter = converter or _get_converter()
    samples, rate = read_wav(recording)
    samples = resample(samples, rate, RATE)
    if len(samples) < 5 * RATE:
        raise ValueError("The recording is too short: read all six sentences.")
    peak = float(np.abs(samples).max())
    if peak < 0.02:
        raise ValueError("The recording is silent. Check that the microphone is on.")
    samples = samples / peak * 0.9
    target = converter.tone(samples).ravel()
    # Start from the built-in voice that already sounds most like the recording. Pitch alone
    # chose wrong (2026-10-17: a young man's voice at 176 Hz got a woman's voice).
    best = None
    for speaker in BASES:
        spoken, _times = say(_BASE_TEXT, speaker)
        tone = converter.tone(resample(spoken, KOKORO_RATE, RATE)).ravel()
        close = float(tone @ target / (np.linalg.norm(tone) * np.linalg.norm(target) or 1))
        if best is None or close > best[0]:
            best = (close, speaker, tone)
    voice = {"base": best[1], "pitch": pitch(samples, RATE), "likeness": best[0],
             "source": best[2].tolist(), "target": target.tolist()}
    os.makedirs(MY_FOLDER, exist_ok=True)
    with open(os.path.join(MY_FOLDER, VOICE_FILE), "w", encoding="utf-8") as fh:
        json.dump(voice, fh)
    _voice = voice
    return voice


def voice() -> dict | None:
    global _voice
    if _voice is None:
        try:
            with open(os.path.join(MY_FOLDER, VOICE_FILE), encoding="utf-8") as fh:
                _voice = json.load(fh)
        except (OSError, ValueError):
            return None
    return _voice


def base_speaker() -> str:
    return (voice() or {}).get("base", "am_michael")


def convert(samples: np.ndarray, converter: Converter | None = None) -> np.ndarray:
    """Kokoro's speech (24 kHz) in the user's voice, the same length, so word times hold."""
    made = voice()
    if made is None:
        return samples
    converter = converter or _get_converter()
    out = converter.change(resample(samples, KOKORO_RATE, RATE), made["source"], made["target"])
    out = resample(out, RATE, KOKORO_RATE)
    out = np.pad(out, (0, max(len(samples) - len(out), 0)))[:len(samples)]
    return out.astype(np.float32)
