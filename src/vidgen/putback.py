"""The switch the app flipped for a video, written down before the click.

``pending_putback.json`` in the settings folder holds the page, the switch and how it was. It
is written before the app clicks and deleted once the switch is back and checked; if the app
crashes or the PC turns off in between, the next start puts it back (App._put_back_left).
"""

from __future__ import annotations

import json
import os
import time

NAME = "pending_putback.json"


def write(folder: str, place_id: str, switch: str, state: str) -> None:
    path = os.path.join(folder, NAME)
    part = path + ".part"
    with open(part, "w", encoding="utf-8") as fh:
        json.dump({"place": place_id, "switch": switch, "state": state,
                   "time": time.strftime("%Y-%m-%d %H:%M:%S")}, fh)
    os.replace(part, path)   # whole or not at all, even if the power goes


def read(folder: str) -> dict | None:
    try:
        with open(os.path.join(folder, NAME), encoding="utf-8") as fh:
            entry = json.load(fh)
    except (OSError, ValueError):
        return None
    return entry if isinstance(entry, dict) and {"place", "switch", "state"} <= set(entry) \
        else None


def clear(folder: str) -> None:
    try:
        os.remove(os.path.join(folder, NAME))
    except OSError:
        pass
