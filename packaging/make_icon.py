"""Generate assets/icon.ico for the exe, the installer and the Start menu.

Each size is drawn separately at 8x and downsampled, rather than scaling one
large bitmap: a single 256px source turns to mush at 16px, which is exactly
where the icon is seen most (taskbar, Alt-Tab, Explorer details view).

    python packaging/make_icon.py
"""

import os

from PIL import Image, ImageDraw

SIZES = [16, 24, 32, 48, 64, 128, 256]
TILE = "#0F6CBD"     # the accent the app uses for a selected segment
MARK = "#FFFFFF"
SUPERSAMPLE = 8

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "icon.ico")


def render(size: int) -> Image.Image:
    s = size * SUPERSAMPLE
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Rounded tile, inset slightly so it does not touch the icon bounds.
    inset = s * 0.045
    draw.rounded_rectangle(
        [inset, inset, s - inset, s - inset],
        radius=s * 0.215,
        fill=TILE,
    )

    # Play mark, optically centred: a triangle's visual centre sits left of its
    # bounding box centre, so it is nudged right.
    left = s * 0.400
    right = s * 0.715
    top = s * 0.305
    bottom = s * 0.695
    draw.polygon([(left, top), (left, bottom), (right, s * 0.5)], fill=MARK)

    return image.resize((size, size), Image.LANCZOS)


def main() -> None:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    frames = [render(n) for n in SIZES]
    frames[-1].save(OUT, format="ICO", sizes=[(n, n) for n in SIZES])
    print(f"wrote {OUT} ({os.path.getsize(OUT)} bytes, sizes: {SIZES})")


if __name__ == "__main__":
    main()
