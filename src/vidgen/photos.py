"""Reference photos: what is in each one, in a sentence, for the Smart writer.

The photo model (writer.VISION, SmolVLM) is run by the same llama.cpp program as the
writer, but on its own and only while photos are being looked at: it is stopped before
the writer starts, so the two never hold memory at the same time. Without the model the
user types the descriptions; nothing here is needed to make a video.

Pure of Tk. The model process and the network can be replaced in tests.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import re
import subprocess

from . import writer
from .footage import IMAGE_EXTS, Stopped

MAX_PHOTOS = 10
SIDE = 512   # the model reads a 512 px picture; a bigger one only costs time
ABOUT_TOKENS = 80
_PROMPT = ("Describe this photo in one sentence of 10 to 25 words: the main thing in it, "
           "what it is doing, the place and the light. Answer as JSON: {\"about\": \"...\"}.")
_SCHEMA = {"type": "object", "properties": {"about": {"type": "string", "maxLength": 240}},
           "required": ["about"]}
# For the Image editor: what the redraw must keep, so the picture model doesn't make it up.
_DETAIL_PROMPT = ("Describe this photo in detail in 40 to 70 words: the main thing and its "
                  "colours and pattern, what is around it, the background, the time of day, the "
                  "light and the colours of the sky or walls. Answer as JSON: {\"about\": \"...\"}.")
_DETAIL_SCHEMA = {"type": "object", "properties": {"about": {"type": "string",
                                                             "maxLength": 500}},
                  "required": ["about"]}


def is_photo(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in IMAGE_EXTS


def _launch(exe: str, _model_file: str, port: int, gpu: bool = False):
    """writer._launch, with the photo model and its image reader: the new Smart writer when
    it is there, else 5.2's SmolVLM."""
    model, reader = ((writer.NEW, writer.NEW_PROJ) if writer.upgraded()
                     else (writer.VISION, writer.VISION_PROJ))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        [exe, "-m", writer.model_path(model), "--mmproj", writer.model_path(reader),
         "--host", "127.0.0.1", "--port", str(port), "-c", "4096", "-np", "1", "--no-webui",
         "-ngl", writer.GPU_LAYERS if gpu else "0"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        creationflags=flags)


def ready() -> tuple[bool, str]:
    if writer.runtime_path() is None:
        return False, "The Smart writer's program is missing from this copy of the app."
    if not writer.sees():
        return False, "The Smart writer's photo part has not been downloaded yet."
    return True, ""


def data_url(path: str) -> str:
    """The photo, made small, as a data: URL the model's server reads."""
    from PIL import Image, ImageOps

    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((SIDE, SIDE))
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def tidy(about: str, whole: bool = False) -> str:
    """One plain sentence: the model sometimes starts with "The image shows"."""
    about = " ".join(str(about).split())
    about = re.sub(r"^(the|this) (image|photo|picture) (shows|features|depicts|is of)\s+|"
                   r"^this is (a|an) (image|photo|picture) of\s+", "", about, flags=re.I)
    sentences = re.split(r"(?<=[a-z0-9])\. ", about)
    if not whole:
        about = sentences[0]   # the first sentence: the rest wanders
    else:   # the small model repeats itself and guesses more as it goes: the first 5, once each
        kept = []
        for sentence in sentences:
            sentence = sentence.strip().rstrip(".")
            if sentence and sentence.lower() not in (k.lower() for k in kept):
                kept.append(sentence)
        about = ". ".join(kept[:5]) + "."
    return (about[:1].upper() + about[1:]).rstrip(" ,;")


def session_for(should_stop=None, gpu: bool = False, launch=_launch, http=None):
    """The photo model, to pass to describe() several times; close it with ``with``."""
    kwargs = {"http": http} if http is not None else {}
    return writer.Session(should_stop, launch=launch, check=ready, gpu=gpu, **kwargs)


def describe(paths, should_stop=None, gpu: bool = False, done=None,
             launch=_launch, http=None, detail: bool = False, session=None) -> list[str]:
    """A sentence for each photo ("" for one that could not be read).

    ``done(index, sentence)`` hears each one as it is ready. Raises writer.WriterError when
    the model cannot run, Stopped when ``should_stop`` says so. ``detail``: a longer
    description of everything in the photo, for the Image editor. ``session``: one from
    session_for(), kept open by the caller (clipcheck asks several times: no restart).
    """
    prompt, schema, tokens = ((_DETAIL_PROMPT, _DETAIL_SCHEMA, 160) if detail
                              else (_PROMPT, _SCHEMA, ABOUT_TOKENS))
    own = session is None
    session = session or session_for(should_stop, gpu, launch, http)
    found = []
    with session if own else contextlib.nullcontext():
        session.start()
        for index, path in enumerate(paths):
            try:
                picture = data_url(path)
            except (OSError, ValueError):
                found.append("")
            else:
                content = session._ask("", [("user", [
                    {"type": "image_url", "image_url": {"url": picture}},
                    {"type": "text", "text": prompt}])], schema, tokens, 0.2)
                try:
                    found.append(tidy(json.loads(content)["about"], whole=detail))
                except (ValueError, KeyError, TypeError):
                    found.append("")
            if done is not None:
                done(index, found[-1])
    return found


__all__ = ["MAX_PHOTOS", "Stopped", "data_url", "describe", "is_photo", "ready", "tidy"]
