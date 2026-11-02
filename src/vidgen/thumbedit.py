"""The thumbnail editor's picture: a list of layers drawn in order (4.9).

A layer is a plain dict, so it saves as JSON as it is:

- ``background``: ``path`` of a picture (a scene's, an AI picture, a file), filled to the size.
- ``text``: ``text`` (lines split on newlines), centre ``x``/``y`` as fractions of the size,
  ``size`` in pixels at full size, ``font`` (a name in FONTS), ``color``, ``outline`` width
  and ``outline_color``, ``shadow``, ``band`` (a dark strip across, behind the words) and
  ``tilt`` in degrees.

- ``shape``: ``shape`` (box, circle, arrow or brush), ``color`` and line ``width`` in pixels.
  A brush keeps its ``points`` as fractions of its own box; an arrow points right before
  its tilt.
- ``image``: ``path`` of a picture (a photo from Pexels or Pixabay, a file), with the
  ``credit`` line a stock photo needs.
- ``sticker``: one ``emoji``, drawn from Windows' colour emoji font.

Effects (4.9 piece 3): a background can have ``blur`` (pixels), ``darken`` (percent),
``brightness`` and ``contrast`` (-50 to 50). Text, stickers and pictures can have a
``glow`` (pixels) in ``glow_color``; stickers and pictures an ``outline`` in
``outline_color`` around their shape (text has its own outline).

Shapes, images and stickers have a centre ``x``/``y`` and a ``w``/``h`` as fractions of the
size, and a ``tilt``. Every layer can have ``hidden``. The editor keeps its layers in the project folder as
``thumb_edit.json`` next to the finished ``thumb_edit.png``; the render copies that PNG as
the video's thumbnail when its shape matches. Pure PIL, no window.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import shutil
from functools import lru_cache

from . import captions, thumbnail

EDIT_FILE = "thumb_edit.json"
PICTURE_FILE = "thumb_edit.png"
FILES = "thumb_files"   # copies of the pictures in use: the scene cache can be cleared

# Name shown in the editor -> file in Windows' Fonts folder. The first three are the
# captions' fonts; a missing one falls back to them.
FONTS = {
    "Segoe Black": "seguibl.ttf",
    "Arial Bold": "arialbd.ttf",
    "Impact": "impact.ttf",
    "Arial Black": "ariblk.ttf",
    "Georgia Bold": "georgiab.ttf",
    "Comic Sans Bold": "comicbd.ttf",
}
DEFAULT_FONT = "Segoe Black"

TEXT_DEFAULTS = {"kind": "text", "text": "Your words", "x": 0.5, "y": 0.5, "size": 110,
                 "font": DEFAULT_FONT, "color": "#ffffff", "outline": 10,
                 "outline_color": "#000000", "shadow": False, "band": False, "tilt": 0,
                 "hidden": False}


# Stickers: single characters only. Without the raqm library Pillow can't join an emoji
# with its colour mark (U+FE0F), and such pairs come out blank.
STICKERS = ("\U0001F525", "\U0001F631", "\U0001F92F", "\U0001F602", "\U0001F60D",
            "\U0001F914", "\U0001F440", "\U0001F4AF", "\u274C", "\u2705", "\u2B50",
            "\U0001F4B0", "\u2757", "\u2753", "\U0001F389", "\U0001F44D", "\U0001F44E",
            "\U0001F4A1", "\U0001F680", "\u23F0", "\U0001F4C8", "\U0001F6A8", "\U0001F449",
            "\U0001F448", "\U0001F446", "\U0001F447", "\u2B06", "\u27A1", "\u2B07", "\u2B05")
SHAPES = ("box", "circle", "arrow", "brush")
SHAPE_COLOR = "#ff2a2a"   # YouTube's red circle and arrow


def new_text(text: str = "Your words", **changes) -> dict:
    return dict(TEXT_DEFAULTS, text=text, **changes)


def automatic(picture: str, title: str, size) -> list[dict]:
    """Today's automatic thumbnail as layers: the scene's picture and the title over its
    lower part, on a dark band (thumbnail.draw_title's look)."""
    w, h = size
    portrait = h > w
    lines = thumbnail.lines(title, 14 if portrait else 22)
    pixels = int(w * (0.11 if portrait else 0.075))
    return [{"kind": "background", "path": picture, "hidden": False},
            new_text("\n".join(lines), y=0.62 if portrait else 0.58, size=pixels,
                     outline=max(pixels // 9, 3), band=True)]


def new_shape(shape: str, x: float, y: float, w: float, h: float, **changes) -> dict:
    return dict({"kind": "shape", "shape": shape, "x": x, "y": y, "w": w, "h": h, "tilt": 0,
                 "color": SHAPE_COLOR, "width": 14, "hidden": False}, **changes)


def new_sticker(emoji: str, size) -> dict:
    drawn = _emoji(emoji)   # in its own shape: a "!" is narrow
    side = 0.22 * min(size)
    return {"kind": "sticker", "emoji": emoji, "x": 0.8, "y": 0.25,
            "w": side * drawn.width / drawn.height / size[0], "h": side / size[1], "tilt": 0,
            "hidden": False}


def new_image(path: str, size, credit: str = "") -> dict:
    """A picture on top, half the thumbnail's width and height at most, in its own shape."""
    pw, ph = _picture_size(path)
    fit = min(0.5 * size[0] / pw, 0.5 * size[1] / ph)
    return {"kind": "image", "path": path, "credit": credit, "x": 0.5, "y": 0.5,
            "w": pw * fit / size[0], "h": ph * fit / size[1], "tilt": 0, "hidden": False}


def brush(points, size, width: int = 14, color: str = SHAPE_COLOR) -> dict:
    """A brush stroke from ``points`` in full-size pixels."""
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    inner_w, inner_h = max(max(xs) - min(xs), 1), max(max(ys) - min(ys), 1)
    return new_shape("brush", (min(xs) + max(xs)) / 2 / size[0],
                     (min(ys) + max(ys)) / 2 / size[1], (inner_w + width) / size[0],
                     (inner_h + width) / size[1], color=color, width=width,
                     points=[[(px - min(xs)) / inner_w, (py - min(ys)) / inner_h]
                             for px, py in points])


def _picture_size(path):
    from PIL import Image

    try:
        with Image.open(path) as opened:
            return opened.size
    except OSError:
        return (16, 9)


def label(layer: dict) -> str:
    """The layer's name in the layers list."""
    if layer["kind"] == "background":
        return "Background"
    if layer["kind"] == "shape":
        return layer.get("shape", "shape").capitalize()
    if layer["kind"] == "image":
        return "Photo" if layer.get("credit") else "Picture"
    if layer["kind"] == "sticker":
        return "Sticker " + layer.get("emoji", "")
    words = " ".join(layer.get("text", "").split())
    return words[:22] + ("..." if len(words) > 22 else "") if words else "Text"


# --- drawing ---------------------------------------------------------------------

def font(name: str, pixels: int, text: str = ""):
    from PIL import ImageFont

    pixels = max(int(pixels), 4)
    if captions.devanagari(text):
        return captions._font(pixels, True)
    folder = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    try:
        return ImageFont.truetype(os.path.join(folder, FONTS.get(name, FONTS[DEFAULT_FONT])),
                                  pixels)
    except OSError:
        return captions._font(pixels)


def _rgb(color: str, alpha: int = 255):
    color = (color or "#ffffff").lstrip("#")
    try:
        return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4)) + (alpha,)
    except ValueError:
        return (255, 255, 255, alpha)


def _words_image(layer: dict, scale: float):
    """The words alone on a transparent picture just big enough, before the tilt."""
    from PIL import Image, ImageDraw

    text = layer.get("text", "") or " "
    pixels = layer.get("size", 100) * scale
    face = font(layer.get("font", DEFAULT_FONT), pixels, text)
    stroke = int(round(layer.get("outline", 0) * scale))
    spacing = int(pixels * 0.12)
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    x0, y0, x1, y1 = (int(round(v)) for v in probe.multiline_textbbox((0, 0), text, font=face, spacing=spacing,
                                              align="center", stroke_width=stroke))
    shadow = int(max(pixels * 0.06, 2)) if layer.get("shadow") else 0
    pad = 2 + shadow
    image = Image.new("RGBA", (x1 - x0 + 2 * pad, y1 - y0 + 2 * pad), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    at = (pad - x0, pad - y0)
    if shadow:
        draw.multiline_text((at[0] + shadow, at[1] + shadow), text, font=face,
                            fill=(0, 0, 0, 170), spacing=spacing, align="center",
                            stroke_width=stroke, stroke_fill=(0, 0, 0, 170))
    draw.multiline_text(at, text, font=face, fill=_rgb(layer.get("color")), spacing=spacing,
                        align="center", stroke_width=stroke,
                        stroke_fill=_rgb(layer.get("outline_color", "#000000")))
    return image


@lru_cache(maxsize=64)
def _emoji(emoji: str):
    """The sticker drawn big once, cropped to the drawing."""
    from PIL import Image, ImageDraw, ImageFont

    folder = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    try:
        face = ImageFont.truetype(os.path.join(folder, "seguiemj.ttf"), 160)
    except OSError:
        face = captions._font(160)
    image = Image.new("RGBA", (260, 260), (0, 0, 0, 0))
    ImageDraw.Draw(image).text((130, 130), emoji, font=face, anchor="mm", embedded_color=True)
    return image.crop(image.getbbox() or (0, 0, 260, 260))


def _tile_size(layer: dict, size, scale: float):
    w = max(int(round(layer.get("w", 0.2) * size[0] * scale)), 2)
    if layer.get("shape") == "arrow":   # as thick as its head, whatever its box says
        return w, max(int(round(layer.get("width", 14) * scale * 4.4)) + 2, 2)
    return w, max(int(round(layer.get("h", 0.2) * size[1] * scale)), 2)


def _tile(layer: dict, size, scale: float):
    """A shape, picture or sticker in its own box, before the tilt."""
    from PIL import Image, ImageDraw, ImageOps

    tw, th = _tile_size(layer, size, scale)
    tile = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    if layer["kind"] == "image":
        try:
            with Image.open(layer["path"]) as opened:
                return ImageOps.fit(opened.convert("RGBA"), (tw, th))
        except (OSError, KeyError, ValueError):
            return tile
    if layer["kind"] == "sticker":
        return _emoji(layer.get("emoji", "\u2B50")).resize((tw, th), Image.LANCZOS)
    draw, color = ImageDraw.Draw(tile), _rgb(layer.get("color", SHAPE_COLOR))
    line = max(int(round(layer.get("width", 14) * scale)), 1)
    half = line / 2
    shape = layer.get("shape")
    if shape == "box":
        draw.rectangle((half, half, tw - 1 - half, th - 1 - half), outline=color, width=line)
    elif shape == "circle":
        draw.ellipse((half, half, tw - 1 - half, th - 1 - half), outline=color, width=line)
    elif shape == "arrow":
        head, mid = min(tw * 0.45, line * 3.2), th / 2
        draw.line((half, mid, tw - head, mid), fill=color, width=line)
        draw.ellipse((0, mid - half, line, mid + half), fill=color)
        draw.polygon([(tw - head - 1, mid - line * 2.2), (tw - 1, mid),
                      (tw - head - 1, mid + line * 2.2)], fill=color)
    elif shape == "brush":
        points = [(half + px * (tw - line), half + py * (th - line))
                  for px, py in layer.get("points") or []]
        if len(points) > 1:
            draw.line(points, fill=color, width=line, joint="curve")
        for px, py in points[:1] + points[-1:]:   # round ends
            draw.ellipse((px - half, py - half, px + half, py + half), fill=color)
    return tile


def _halo(image, layer: dict, scale: float):
    """``image`` with an outline around its shape and a soft glow behind it, when the layer
    has them. Text draws its own outline, so only its glow is added here."""
    from PIL import Image, ImageFilter

    outline = 0 if layer["kind"] == "text" else int(round(layer.get("outline", 0) * scale))
    glow = int(round(layer.get("glow", 0) * scale))
    if not (outline or glow):
        return image
    pad = outline + 2 * glow + 2
    out = Image.new("RGBA", (image.width + 2 * pad, image.height + 2 * pad), (0, 0, 0, 0))
    shape = Image.new("L", out.size, 0)
    shape.paste(image.getchannel("A"), (pad, pad))
    if glow:
        soft = shape.filter(ImageFilter.GaussianBlur(glow)).point(lambda v: min(v * 2, 255))
        out.paste(_rgb(layer.get("glow_color", "#ffffff")), (0, 0), soft)
    if outline:   # the shape grown by ``outline``: a blur, then everything it reached
        grown = shape.filter(ImageFilter.GaussianBlur(outline / 2)).point(
            lambda v: 255 if v > 10 else 0)
        out.paste(_rgb(layer.get("outline_color", "#ffffff")), (0, 0), grown)
    out.alpha_composite(image, (pad, pad))
    return out


def _background(layer: dict, out):
    """The background picture filled to ``out`` with its effects, or None."""
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    try:
        with Image.open(layer["path"]) as opened:
            picture = ImageOps.fit(opened.convert("RGB"), out)
    except (OSError, KeyError, ValueError):
        return None
    if layer.get("blur"):   # in full-size pixels: the preview blurs as much, scaled
        picture = picture.filter(ImageFilter.GaussianBlur(layer["blur"] * out[0] / 1280))
    if layer.get("brightness"):
        picture = ImageEnhance.Brightness(picture).enhance(1 + layer["brightness"] / 100)
    if layer.get("contrast"):
        picture = ImageEnhance.Contrast(picture).enhance(1 + layer["contrast"] / 100)
    if layer.get("darken"):
        picture = Image.blend(picture, Image.new("RGB", out), layer["darken"] / 100)
    return picture


def _placed(layer: dict, size, scale: float):
    """(the tilted layer's picture, its top-left corner) on a canvas of size * scale."""
    words = _words_image(layer, scale) if layer["kind"] == "text" else _tile(layer, size, scale)
    if layer["kind"] != "shape":
        words = _halo(words, layer, scale)
    if layer.get("tilt"):
        from PIL import Image

        words = words.rotate(layer["tilt"], resample=Image.BICUBIC, expand=True)
    cx, cy = layer.get("x", 0.5) * size[0] * scale, layer.get("y", 0.5) * size[1] * scale
    return words, (int(round(cx - words.width / 2)), int(round(cy - words.height / 2)))


def box(layer: dict, size) -> tuple[int, int, int, int]:
    """Where a layer sits at full size: (left, top, right, bottom), around its tilt."""
    if layer["kind"] == "text":
        words, (x, y) = _placed(layer, size, 1.0)
        return x, y, x + words.width, y + words.height
    tw, th = _tile_size(layer, size, 1.0)
    turn = math.radians(layer.get("tilt", 0))
    bw = abs(tw * math.cos(turn)) + abs(th * math.sin(turn))
    bh = abs(tw * math.sin(turn)) + abs(th * math.cos(turn))
    cx, cy = layer.get("x", 0.5) * size[0], layer.get("y", 0.5) * size[1]
    return (int(round(cx - bw / 2)), int(round(cy - bh / 2)), int(round(cx + bw / 2)),
            int(round(cy + bh / 2)))


def render(layers: list[dict], size, scale: float = 1.0):
    """The thumbnail: ``layers`` drawn in order on ``size`` (scaled by ``scale``), as RGB."""
    from PIL import Image, ImageDraw

    out = (max(int(size[0] * scale), 1), max(int(size[1] * scale), 1))
    canvas = Image.new("RGBA", out, (24, 24, 28, 255))
    for layer in layers:
        if layer.get("hidden"):
            continue
        if layer["kind"] == "background":
            picture = _background(layer, out)
            if picture is not None:   # a missing picture leaves the plain canvas
                canvas.paste(picture, (0, 0))
        else:
            words, (x, y) = _placed(layer, size, scale)
            if layer.get("band"):
                band = Image.new("RGBA", out, (0, 0, 0, 0))
                pixels = layer.get("size", 100) * scale
                ImageDraw.Draw(band).rectangle(
                    (0, max(y - pixels * 0.4, 0), out[0], min(y + words.height + pixels * 0.4,
                                                              out[1])), fill=(0, 0, 0, 175))
                canvas = Image.alpha_composite(canvas, band)
            canvas.alpha_composite(words, (max(x, 0), max(y, 0)),
                                   (max(-x, 0), max(-y, 0)))
    return canvas.convert("RGB")


def hit(layers: list[dict], size, point) -> int | None:
    """The index of the top visible layer under ``point`` (full-size pixels), the background
    not counted."""
    for at in range(len(layers) - 1, -1, -1):
        layer = layers[at]
        if layer["kind"] != "background" and not layer.get("hidden"):
            left, top, right, bottom = box(layer, size)
            if left <= point[0] <= right and top <= point[1] <= bottom:
                return at
    return None


# --- keeping it --------------------------------------------------------------------

def resize(layer: dict, ratio: float) -> dict:
    """``layer`` made ``ratio`` times bigger around its centre (a corner handle)."""
    if layer["kind"] == "text":
        layer["size"] = int(min(max(layer.get("size", 100) * ratio, 12), 480))
    elif layer["kind"] != "background":
        w, h = layer.get("w", 0.2) * ratio, layer.get("h", 0.2) * ratio
        if 0.01 <= min(w, h) and max(w, h) <= 4:
            layer["w"], layer["h"] = w, h
    return layer


def snapped(value: float, length: int, near: float) -> float:
    """``value`` (a fraction of ``length``) on the centre line when it is within ``near``
    pixels of it."""
    return 0.5 if abs(value - 0.5) * length <= near else value


def turned(tilt: float) -> int:
    """A tilt from the round handle, kept between -180 and 180 degrees."""
    return int(round((tilt + 180) % 360 - 180))


def credits(layers: list[dict]) -> list[str]:
    """The credit lines of the stock photos shown."""
    return [layer["credit"] for layer in layers
            if layer.get("credit") and not layer.get("hidden")]


def save(folder: str, layers: list[dict], size) -> str:
    """Write ``thumb_edit.json`` and ``thumb_edit.png`` in ``folder``; returns the PNG.

    Every picture used is copied into the folder, so the edit survives the scene pictures'
    cache being cleared.
    """
    os.makedirs(folder, exist_ok=True)
    layers = copy.deepcopy(layers)
    files = os.path.join(folder, FILES)
    for layer in layers:
        path = layer.get("path")
        if path and os.path.isfile(path) and \
                os.path.dirname(os.path.abspath(path)) != os.path.abspath(files):
            os.makedirs(files, exist_ok=True)
            name = hashlib.sha1(os.path.abspath(path).encode("utf-8")).hexdigest()[:10]
            keep = os.path.join(files, name + os.path.splitext(path)[1].lower())
            shutil.copyfile(path, keep)
            layer["path"] = keep
    picture = os.path.join(folder, PICTURE_FILE)
    render(layers, size).save(picture)
    with open(os.path.join(folder, EDIT_FILE), "w", encoding="utf-8") as f:
        json.dump({"size": list(size), "layers": layers}, f, indent=1)
    return picture


def load(folder: str):
    """(layers, size) saved in ``folder``, or None."""
    try:
        with open(os.path.join(folder, EDIT_FILE), encoding="utf-8") as f:
            data = json.load(f)
        return data["layers"], tuple(data["size"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def edited(folder: str | None, portrait: bool) -> str | None:
    """The edited thumbnail's PNG in ``folder`` when it is the video's shape, else None."""
    if not folder:
        return None
    found = load(folder)
    picture = os.path.join(folder, PICTURE_FILE)
    if found is None or not os.path.isfile(picture):
        return None
    w, h = found[1]
    return picture if (h > w) == portrait else None


def remove(folder: str):
    """Back to the automatic thumbnail."""
    for name in (EDIT_FILE, PICTURE_FILE):
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass


class History:
    """Undo and redo: copies of the layer list. Changes with the same ``key`` in a row (one
    slider being dragged, one box being typed in) are one step."""

    def __init__(self, layers):
        self.past, self.future = [copy.deepcopy(layers)], []
        self._key = None

    def record(self, layers, key=None):
        if key is not None and key == self._key and len(self.past) > 1:
            self.past[-1] = copy.deepcopy(layers)
        else:
            self.past.append(copy.deepcopy(layers))
        self._key = key
        self.future.clear()

    def undo(self):
        """The layers one step back, or None when there is nothing to undo."""
        self._key = None
        if len(self.past) < 2:
            return None
        self.future.append(self.past.pop())
        return copy.deepcopy(self.past[-1])

    def redo(self):
        self._key = None
        if not self.future:
            return None
        self.past.append(self.future.pop())
        return copy.deepcopy(self.past[-1])


# --- the Thumbnails page: videos that are not open, and free thumbnails --------------------

def blank(size) -> list[dict]:
    """Start over: a plain dark background (pick one in the strip) and one text box."""
    return [{"kind": "background", "path": "", "hidden": False},
            new_text("Your title", y=0.5, size=int(min(size) * 0.14))]


def video_backgrounds(project_dir: str, videos, frames: int = 8) -> list[tuple[str, str]]:
    """(label, path) of pictures for a video's thumbnail when it is not open in the
    Storyboard: frames of the made video (cut once into the project's ``thumb_files``), then
    its AI pictures and its first picture. Slow (ffmpeg): call it from a thread."""
    from . import preview

    found = []
    video = next((v for v in videos or [] if os.path.isfile(v)), None)
    if video:
        out = os.path.join(project_dir, FILES)
        stem = hashlib.sha1(os.path.abspath(video).encode("utf-8")).hexdigest()[:8]
        try:
            width, height, length = preview.probe(video)
            box = preview.fit((width, height), (1280, 1280))
            os.makedirs(out, exist_ok=True)
            for at, moment in enumerate(thumbnail.moments(length, frames)):
                path = os.path.join(out, f"frame-{stem}-{at}.jpg")
                if not os.path.isfile(path):
                    preview.poster(video, box, at=moment).convert("RGB").save(path, quality=90)
                found.append((f"Frame {at + 1}", path))
        except Exception:  # noqa: BLE001 - a broken video: the pictures below still help
            pass
    pictures = os.path.join(project_dir, "pictures")
    try:
        names = sorted(n for n in os.listdir(pictures) if n.lower().endswith(".png"))
    except OSError:
        names = []
    found += [(f"AI picture {i + 1}", os.path.join(pictures, n)) for i, n in enumerate(names)]
    first = os.path.join(project_dir, "thumb.png")
    if os.path.isfile(first):
        found.append(("First picture", first))
    return found


def export(picture: str, dest: str) -> str:
    """``picture`` (the saved PNG) as ``dest``: a .jpg next to a video, or a PNG anywhere."""
    from PIL import Image

    with Image.open(picture) as opened:
        image = opened.convert("RGB")
    if dest.lower().endswith((".jpg", ".jpeg")):
        image.save(dest, quality=92)
    else:
        image.save(dest)
    return dest


def replace_video_thumbnails(picture: str, videos, portrait: bool) -> list[str]:
    """Write ``picture`` over each made video's ``.thumbnail.jpg`` of the same shape; the
    ones written. Videos that are gone, or of the other shape, are left alone."""
    from . import preview

    done = []
    for video in videos or []:
        if not os.path.isfile(video):
            continue
        try:
            width, height, _length = preview.probe(video)
        except Exception:  # noqa: BLE001 - not a video ffmpeg can read
            continue
        if (height > width) == portrait:
            done.append(export(picture, os.path.splitext(video)[0] + ".thumbnail.jpg"))
    return done
