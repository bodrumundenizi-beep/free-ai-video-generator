"""From a recording's log to a tutorial timeline, in the house style (5.4, plan step 3).

The rules below were read off the hand-made reference (tests/fixtures/tutorial_ref,
HOUSE_STYLE.md); the golden test feeds them the reference's clicks and checks the timeline that
comes out against the hand-made one.

Time: the tutorial does not keep the recording's own pace. Every click is placed on the house
rhythm (a step's first click 1.0 s after the step starts, the next 0.8 s later, ...), so long
pauses and wandering simply disappear; ``anchors`` pair each click's time in the tutorial with
its time in the recording, for the video (plan step 4) and the Real path cursor.

Coordinates: pixels of the recorded area (the window plus its margin); ``window`` says where the
window sits in it. The scene's camera looks at these pixels.
"""
from __future__ import annotations

import re

# ---- rhythm (seconds) ----
TITLE = 1.4            # the title card
FIRST = 1.0            # step start -> its first click
NEXT = 0.8             # click -> next click in the same step
IN_MENU = 0.3          # more for a click in a menu the click before opened
DRAG_WAIT = 0.8        # step start -> a drag's press
DRAG = 1.3             # press -> release
STEP_GAP = 1.0         # a step's last click (or release) -> the next step
AFTER_MENU = 0.2       # more when that click opened a menu (it is still opening)
RECAP = 1.7            # last click -> the Done card
TAIL = 2.8             # the Done card -> the end
ARRIVE = 0.15          # the cursor arrives this long before a click
DRAG_ARRIVE = 0.2      # ... before a drag's press
LEAVE = 0.1            # and stays this long after it
CALLOUT = 0.9          # longest a label shows
CALLOUT_PICK = 0.5     # a pick in a menu: the menu closes, so its label goes sooner
SCREEN_AFTER = 0.05    # a click's result shows this long after it
SCREEN_AFTER_PICK = 0.3   # ... a pick's, once its menu has closed
CAMERA_MOVE = 0.8      # a camera move takes this long
CAMERA_HOLD = 0.35     # and starts this long after the shot's last click
POPUP_FOLLOW = 0.6     # the camera follows a menu that just opened, arriving this long after
POPUP_SEEN = 0.6       # a menu that appears within this long after a click was opened by it
REAL_SPEED = 0.4       # the recording plays at its own speed this long round each click
PICK_EARLY = 0.1       # a pick in a menu is held from this long before its press
# ---- camera ----
ZOOM = 1.9             # on clicks
ZOOM_DRAG = 1.2        # while dragging
DRAG_AIM = 0.2         # ... aiming this share of the shape's height above its middle, so
                       # the page sits lower in the frame, clear of the step label (reference)
ZOOM_END = 0.9         # for the Done card
END_SHIFT = 0.18       # ... with the window moved left by this share of its width
MENU_FIT = 0.8         # a menu fills at most this share of the frame's height
OUTSIDE = 0.15         # the view may show this share of the window's size past its edge
FRAME = (1920, 1080)
TALL = (1080, 1920)    # 9:16, for Shorts: the window fits the width, the words sit lower
TALL_FIT = 0.94        # the whole window fills this share of a 9:16 frame's width
# ---- cursor ----
START = (0.54, 0.69)   # where the cursor waits at the start, as shares of the window
PARK = (0.63, 0.885)   # and where it ends
STEPS_MOST = 6
CANVAS = {"powerpoint": "slide", "word": "page", "excel": "sheet", "paint": "canvas"}


class BuildError(Exception):
    """The log can't become a tutorial. The message is for the user."""


# ---- reading the log ----------------------------------------------------------------------
def _popup_at(log: dict, t: float):
    """The program's menus open at recording time ``t``: [[x, y, w, h], ...]."""
    current = []
    for when, rects in log.get("popups", []):
        if when > t:
            break
        current = rects
    return current


def _inside(point, rect) -> bool:
    x, y = point
    return rect[0] <= x < rect[0] + rect[2] and rect[1] <= y < rect[1] + rect[3]


def _opened_by(log: dict, click: dict):
    """The menu that appeared just after ``click`` (within POPUP_SEEN), or None."""
    before = _popup_at(log, click["t"])
    for when, rects in log.get("popups", []):
        if click["t"] < when <= click["t"] + POPUP_SEEN:
            new = [r for r in rects if r not in before]
            if new:
                return max(new, key=lambda r: r[2] * r[3])
    return None


_COLOUR = re.compile(r"^(dark |light )?(red|orange|yellow|gold|green|blue|purple|pink|brown|"
                     r"teal|turquoise|plum|gr[ae]y|black|white|aqua|lime|olive|navy)(\b|,)"
                     r"|\b(accent \d|background \d|text \d|lighter \d+%|darker \d+%)", re.I)


def is_colour(name: str) -> bool:
    """A swatch's name as Office gives it ("Orange", "Orange, Accent 2", "Black, Text 1,
    Lighter 50%"): a colour pick gets its own step, whether Windows or the pixels named it
    (Deniz's recording, 2026-11-14: Windows named "Orange", and it merged into Shape Fill)."""
    return bool(_COLOUR.search(name.strip()))


def events(log: dict) -> list[dict]:
    """The clicks worth showing, each with what the builder needs to know about it."""
    out = []
    for click in log.get("clicks", []):
        if click.get("button", "left") != "left":
            continue
        point = (click["x"], click["y"])
        menu = next((r for r in _popup_at(log, click["t"]) if _inside(point, r)), None)
        out.append({
            "t": click["t"], "x": click["x"], "y": click["y"],
            "name": (click.get("name") or "").strip(),
            "kind": click.get("kind", ""),
            "drag": bool(click.get("drag")),
            "end": (click.get("up_x", click["x"]), click.get("up_y", click["y"])),
            "colour": click.get("named_by") == "colour" or (menu is not None and is_colour(
                click.get("name") or "")),
            "guessed": click.get("named_by") == "picture",   # Qwen3-VL's reading: worth a look
            "canvas": bool(click.get("on_canvas")) or click.get("drag", False),
            "in_menu": menu,
            "opens": _opened_by(log, click),
        })
    while out and out[-1]["canvas"] and not out[-1]["drag"]:
        out.pop()   # a last click on empty space only deselects: nothing to show
    top = (log.get("window") or [0, 0])[1]
    for e in out:   # a classic menu bar's menu (Notepad++'s Search, View): a step of its own
        e["menubar"] = (e["kind"] == "MenuItem" and not e["in_menu"] and bool(e["opens"])
                        and e["y"] - top < MENUBAR)
    for i, e in enumerate(out):   # a pick in the menu the click before opened
        e["picks"] = bool(i and e["in_menu"] and out[i - 1]["opens"]
                          and e["in_menu"] == out[i - 1]["opens"])
    return out


MENUBAR = 60   # pixels from a window's top where a classic menu bar sits


def group(evs: list[dict]) -> list[list[dict]]:
    """Clicks into steps: a ribbon tab, a drag, the click after a drag and a colour each start
    one; at most 3 clicks a step; at most STEPS_MOST steps (the closest ones are joined)."""
    steps = []
    for e in evs:
        new = (not steps or e["kind"] == "TabItem" or e.get("menubar") or e["drag"]
               or steps[-1][-1]["drag"]
               or e["colour"] or len(steps[-1]) >= 3)
        if new:
            steps.append([e])
        else:
            steps[-1].append(e)
    while len(steps) > STEPS_MOST:
        i = min(range(len(steps) - 1), key=lambda k: len(steps[k]) + len(steps[k + 1]))
        steps[i:i + 2] = [steps[i] + steps[i + 1]]
    return steps


# ---- words ----------------------------------------------------------------------------------
def _label(name: str) -> str:
    return name[:1].upper() + name[1:] if name else ""


def app_name(title: str) -> str:
    """"Presentation1 - PowerPoint" -> "PowerPoint"."""
    parts = [p.strip() for p in re.split(r"\s[-–—]\s", title or "") if p.strip()]
    return parts[-1] if parts else ""


def caption(step: list[dict], app: str = "") -> str:
    """The line under the video for a step; **words** are highlighted."""
    first = step[0]
    if first["drag"]:
        where = CANVAS.get(app.lower(), "")
        return f"**Drag** on the {where} to draw it" if where else "**Drag** to draw it"
    if first["colour"]:
        return f"Pick **{_label(first['name'])}**"
    names = [_label(e["name"]) for e in step if e["name"]][:2]
    if not names:
        return "Click **here**"
    if len(names) == 1:
        return f"Click **{names[0]}**"
    return f"Open **{names[0]}**, then **{names[1]}**"


def recap_row(step: list[dict], app: str = "") -> str:
    first = step[0]
    if first["drag"]:
        where = CANVAS.get(app.lower(), "")
        return f"Drag on the {where}" if where else "Drag to draw"
    if first["colour"]:
        return f"Pick {_label(first['name'])}"
    names = [_label(e["name"]) for e in step if e["name"]]
    return " → ".join(names) or "Click"


# ---- the timeline ---------------------------------------------------------------------------
def _ease(x: float) -> float:
    return 4 * x ** 3 if x < .5 else 1 - (-2 * x + 2) ** 3 / 2


def _clamp_view(cx, cy, zoom, window, frame=FRAME):
    """Keep the view on the window, letting OUTSIDE of it show past the edge."""
    wx, wy, ww, wh = window
    half_w, half_h = frame[0] / zoom / 2, frame[1] / zoom / 2
    lo_x, hi_x = wx + half_w - OUTSIDE * ww, wx + ww - half_w + OUTSIDE * ww
    lo_y, hi_y = wy + half_h - OUTSIDE * wh, wy + wh - half_h + OUTSIDE * wh
    cx = (lo_x + hi_x) / 2 if lo_x > hi_x else min(max(cx, lo_x), hi_x)
    cy = (lo_y + hi_y) / 2 if lo_y > hi_y else min(max(cy, lo_y), hi_y)
    return round(cx), round(cy)


def _fits(a, b, frame=FRAME) -> bool:
    """Two clicks close enough to share a shot."""
    return abs(a["x"] - b["x"]) < frame[0] / ZOOM * 0.5 and abs(a["y"] - b["y"]) < frame[1] / ZOOM * 0.5


def _menu_zoom(menu, frame=FRAME) -> float:
    return min(ZOOM, round(frame[1] * MENU_FIT / menu[3], 2)) if menu else ZOOM


def _menu_centre(point, menu):
    """Half way between the click and the menu's middle: both stay in view."""
    mx, my = menu[0] + menu[2] / 2, menu[1] + menu[3] / 2
    return (point[0] + mx) / 2, (point[1] + my) / 2


def build(log: dict, title: str = "", app: str = "", cursor: str = "glide",
          screens=None, images_dir: str = "", shape: str = "16:9") -> dict:
    """The timeline for scene.html. ``cursor``: "glide" (eased click to click, the default)
    or "real" (the recorded path, smoothed). ``screens``: optional still pictures, one per
    click ("" to keep the last), shown after each click (the reference's stills); without
    them the recording itself is shown (plan step 4). With stills, a drag has no picture of
    the shape half drawn, so one grows with the cursor in the colour the shape has in the
    picture after the drag (``images_dir`` holds the pictures)."""
    evs = events(log)
    if not evs:
        raise BuildError("Nothing was clicked in the recording, so there are no steps to show.")
    window = log.get("window") or [0, 0, *log.get("size", (1600, 900))]
    wx, wy, ww, wh = window
    app = app or app_name(log.get("title", ""))
    steps = group(evs)

    # 1. when everything happens, on the house rhythm
    t = TITLE - 0.1
    step_at = []
    for step in steps:
        step_at.append(round(t, 3))
        for k, e in enumerate(step):
            if e["drag"]:
                e["out"] = round(t + DRAG_WAIT, 3)
                e["release"] = round(e["out"] + DRAG, 3)
                t = e["release"]
            else:
                if k == 0:
                    gap = FIRST + (AFTER_MENU if e["picks"] else 0)
                else:
                    gap = NEXT + (IN_MENU if e["picks"] else 0)
                e["out"] = round(t + gap, 3)
                t = e["out"]
        t += STEP_GAP + (AFTER_MENU if step[-1]["opens"] else 0)
    last = evs[-1].get("release", evs[-1]["out"])
    recap_at = round(last + RECAP, 3)
    length = round(recap_at + TAIL, 1)

    # 2. the cursor
    start = (round(wx + START[0] * ww), round(wy + START[1] * wh))
    park = (round(wx + PARK[0] * ww), round(wy + PARK[1] * wh))
    if cursor == "real":
        cur = real_path(log, evs, start, park, step_at[0], recap_at)
    else:
        cur = [[0, *start], [step_at[0], *start]]
        for i, e in enumerate(evs):
            if e["drag"]:
                cur += [[round(e["out"] - DRAG_ARRIVE, 3), e["x"], e["y"]],
                        [e["out"], e["x"], e["y"]], [e["release"], *e["end"]]]
                nxt = evs[i + 1]["out"] - 1.0 if i + 1 < len(evs) else e["release"] + 1.0
                cur.append([round(max(nxt, e["release"] + 0.1), 3), *e["end"]])
            else:
                cur += [[round(e["out"] - ARRIVE, 3), e["x"], e["y"]],
                        [round(e["out"] + LEAVE, 3), e["x"], e["y"]]]
        cur.append([round(recap_at - 0.2, 3), *park])

    # 3. the camera: it moves CAMERA_MOVE after the last shot's hold ends, never later than
    #    just before the next click; it follows a menu a click opens, keeping its zoom.
    tall = shape == "9:16"
    frame = TALL if tall else FRAME
    centre = (wx + ww / 2, wy + wh / 2)
    # 16:9 starts at 1:1 (the reference); 9:16 starts with the whole window across the frame
    whole = round(min(1.0, frame[0] * TALL_FIT / ww), 2) if tall else 1
    cam = [[0, *map(round, centre), whole], [step_at[0], *map(round, centre), whole]]

    def shot(move_from, by, cx, cy, zoom, free=False):
        cx, cy = (round(cx), round(cy)) if free else _clamp_view(cx, cy, zoom, window, frame)
        if cam[-1][1:] == [cx, cy, zoom]:
            return
        move_from = max(move_from, cam[-1][0])
        if move_from > cam[-1][0]:
            cam.append([round(move_from, 3), *cam[-1][1:]])
        arrive = min(move_from + CAMERA_MOVE, by)
        cam.append([round(max(arrive, move_from + 0.2), 3), cx, cy, zoom])

    hold_end = step_at[0]
    starts = {id(step[0]) for step in steps}
    for i, e in enumerate(evs):
        if e["drag"]:
            box = (min(e["x"], e["end"][0]), min(e["y"], e["end"][1]),
                   abs(e["end"][0] - e["x"]), abs(e["end"][1] - e["y"]))
            shot(hold_end, e["out"] - 0.3, box[0] + box[2] / 2,
                 box[1] + box[3] * (0.5 - DRAG_AIM), ZOOM_DRAG)
            hold_end = e["release"] + 0.3
            continue
        if e["picks"] and id(e) not in starts:
            pass   # a pick in the menu the camera already follows: it stays
        elif e["in_menu"]:
            cx, cy = _menu_centre((e["x"], e["y"]), e["in_menu"])
            shot(hold_end, e["out"] - 0.3, cx, cy, _menu_zoom(e["in_menu"], frame))
        elif not (i and not evs[i - 1]["drag"] and not evs[i - 1]["opens"]
                  and not e["kind"] == "TabItem" and _fits(evs[i - 1], e, frame)):
            # a new shot: this click and the next ones of its step that fit beside it
            pts = [(e["x"], e["y"])]
            for f in evs[i + 1:]:
                if f["drag"] or f["in_menu"] or f["kind"] == "TabItem" or not _fits(e, f, frame):
                    break
                pts.append((f["x"], f["y"]))
            shot(hold_end, e["out"] - 0.3, sum(p[0] for p in pts) / len(pts),
                 sum(p[1] for p in pts) / len(pts), ZOOM)
        if e["opens"]:   # follow the menu the click opened, keeping the zoom
            cx, cy = _menu_centre((e["x"], e["y"]), e["opens"])
            shot(e["out"] + 0.1, e["out"] + POPUP_FOLLOW, cx, cy, cam[-1][3])
        hold_end = e["out"] + CAMERA_HOLD
    if tall:   # the Done card sits under the window: the window moves up a little
        end, end_zoom = (wx + ww / 2, wy + wh * 0.62), round(whole * 0.9, 2)
    else:
        end, end_zoom = (wx + ww / 2 + END_SHIFT * ww, wy + wh / 2), ZOOM_END
    shot(max(hold_end, recap_at - 1.3), recap_at - 0.3, end[0], end[1], end_zoom, free=True)
    cam = _tidy(cam)

    # 4. rings, labels, drags, steps, screens
    clicks = [[e["out"], e["x"], e["y"]] for e in evs]
    callouts = []
    for i, e in enumerate(evs):
        if not e["name"] or e["canvas"]:
            continue
        nxt = evs[i + 1]["out"] if i + 1 < len(evs) else recap_at
        most = CALLOUT_PICK if e["picks"] else CALLOUT
        callouts.append([e["out"], round(min(nxt - 0.1, e["out"] + most), 3), e["x"], e["y"],
                         _label(e["name"])])
    drags = [{"from": e["out"], "until": round(e["release"] + 0.1, 3), "press_until": e["release"],
              "x": e["x"], "y": e["y"]} for e in evs if e["drag"]]
    timeline = {
        "length": length,
        "stage": ({"size": list(TALL), "centre": [TALL[0] // 2, int(TALL[1] * 0.52)], "tall": True}
                  if tall else {"size": list(FRAME), "centre": [960, 520]}),
        "window": dict(zip(("w", "h"), map(int, _picture_size(log)))),
        "camera": cam, "cursor": cur, "clicks": clicks, "drags": drags, "callouts": callouts,
        "steps": [{"at": at, "text": caption(step, app)} for at, step in zip(step_at, steps)],
        "main": [step_at[0], recap_at],
        "title": {"tag": f"{app} tutorial" if app else "Tutorial",
                  "title": title or "How it's done",
                  "sub": f"{len(steps)} steps · {round(length) - 2} seconds", "until": TITLE},
        "recap": {"at": recap_at, "title": "Done!", "rows": [recap_row(s, app) for s in steps]},
        "anchors": anchors(evs, step_at[0], recap_at, log),
    }
    if screens is not None:
        for drag, e in zip(drags, [e for e in evs if e["drag"]]):
            after = screens[evs.index(e) + 1] if evs.index(e) + 1 < len(screens) else ""
            colours = _shape_colours(images_dir, after, e) if after and images_dir else None
            if colours:
                drag["fill"], drag["edge"] = colours
        shown = [{"at": 0, "image": screens[0]}]
        for e, image in zip(evs, screens[1:]):
            if image:
                after = SCREEN_AFTER_PICK if e["picks"] else SCREEN_AFTER
                shown.append({"at": round(e.get("release", e["out"]) + after, 3),
                              "image": image})
        timeline["screens"] = shown
    return timeline


def _shape_colours(images_dir: str, image: str, e: dict):
    """(fill, edge) of the shape a drag drew: the middle of its box in the picture after it,
    and a darker edge. None when the picture can't be read."""
    import os

    from PIL import Image

    try:
        with Image.open(os.path.join(images_dir, image)) as im:
            x = (e["x"] + e["end"][0]) // 2
            y = (e["y"] + e["end"][1]) // 2
            r, g, b = im.convert("RGB").getpixel((x, y))
    except (OSError, IndexError, ValueError):
        return None
    edge = tuple(round(v * 0.75) for v in (r, g, b))
    return "#%02x%02x%02x" % (r, g, b), "#%02x%02x%02x" % edge


def _picture_size(log: dict):
    """The size of what was recorded (the area), or of the still pictures."""
    if log.get("size"):
        return log["size"]
    if log.get("area"):
        return log["area"][2:]
    wx, wy, ww, wh = log["window"]
    return ww + 2 * wx, wh + 2 * wy


def _tidy(keys):
    """Camera keys in time order with no two at the same moment."""
    out = []
    for key in sorted(keys, key=lambda k: k[0]):
        if out and key[0] - out[-1][0] < 0.05:
            out[-1] = key
        else:
            out.append(key)
    return out


def anchors(evs, first_at, recap_at, log) -> list[list[float]]:
    """[tutorial time, recording time] pairs: each press (and release), the start and the end.
    Between two pairs the recording is played faster or slower (plan step 4)."""
    pairs = [[0.0, max(evs[0]["t"] - first_at - FIRST, 0.0)]]
    for e in evs:
        # real speed just before and after each click: the cursor's last moves, the press and
        # what it does (a menu closing, a colour changing) play as they happened
        pairs.append([e["out"] - REAL_SPEED, e["t"] - REAL_SPEED])
        end_out, end_rec = e["out"], e["t"]
        if e["picks"]:
            # the menu closes on the press (and the video can run a frame ahead of the log):
            # hold a frame from just before it, menu still open, as the reference does
            held = e["t"] - PICK_EARLY
            pairs += [[e["out"], held], [e["out"] + SCREEN_AFTER_PICK, held]]
            end_out = e["out"] + SCREEN_AFTER_PICK
        else:
            pairs.append([e["out"], e["t"]])
        if e["drag"]:
            up = next((c.get("up", c["t"]) for c in log["clicks"] if c["t"] == e["t"]), e["t"])
            pairs.append([e["release"], up])
            end_out, end_rec = e["release"], up
        pairs.append([end_out + REAL_SPEED, end_rec + REAL_SPEED])
    pairs.append([recap_at, min(evs[-1]["t"] + 1.0, log.get("length", evs[-1]["t"] + 1.0))])
    out = []
    for out_t, rec_t in pairs:
        if out and (out_t <= out[-1][0] or rec_t < out[-1][1]):
            continue
        out.append([round(out_t, 3), round(rec_t, 3)])
    return out


def _slopes(pairs):
    """Monotone slopes at each anchor (Fritsch-Carlson), so the speed changes smoothly between
    anchors instead of jumping (a jump every 0.4 s made the video judder, 2026-11-14)."""
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    d = [(ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]) for i in range(len(xs) - 1)]
    m = [d[0]] + [0.0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2
                  for i in range(1, len(d))] + [d[-1]]
    for i, di in enumerate(d):
        if di == 0:
            m[i] = m[i + 1] = 0.0   # a hold stays a hold
        else:
            a, b = m[i] / di, m[i + 1] / di
            if a * a + b * b > 9:
                k = 3 / (a * a + b * b) ** 0.5
                m[i], m[i + 1] = k * a * di, k * b * di
    return m


def to_recording(pairs, t: float) -> float:
    """The recording's time for tutorial time ``t``: through every anchor, never backwards,
    with the speed easing between them."""
    if t <= pairs[0][0]:
        return pairs[0][1]
    if t >= pairs[-1][0]:
        return pairs[-1][1]
    m = _slopes(pairs) if len(pairs) > 1 else [0.0]
    for i in range(len(pairs) - 1):
        (a, ra), (b, rb) = pairs[i], pairs[i + 1]
        if t <= b:
            h, u = b - a, (t - a) / (b - a)
            h00, h10 = 2 * u ** 3 - 3 * u ** 2 + 1, u ** 3 - 2 * u ** 2 + u
            h01, h11 = -2 * u ** 3 + 3 * u ** 2, u ** 3 - u ** 2
            return h00 * ra + h10 * h * m[i] + h01 * rb + h11 * h * m[i + 1]
    return pairs[-1][1]


def to_tutorial(pairs, rec_t: float) -> float:
    """The tutorial's time showing recording time ``rec_t`` (the first one, after a hold)."""
    if rec_t <= pairs[0][1]:
        return pairs[0][0]
    if rec_t >= pairs[-1][1]:
        return pairs[-1][0]
    lo, hi = pairs[0][0], pairs[-1][0]
    for _ in range(50):
        mid = (lo + hi) / 2
        if to_recording(pairs, mid) < rec_t:
            lo = mid
        else:
            hi = mid
    return hi


def real_path(log, evs, start, park, first_at, recap_at, every: float = 1 / 15,
              smooth: float = 0.15) -> list[list[float]]:
    """The recorded mouse path on the tutorial's clock, its shaking smoothed out."""
    pairs = anchors(evs, first_at, recap_at, log)
    moves = log.get("moves") or []
    if not moves:
        return [[0, *start], [recap_at, *park]]

    def at(rec_t):   # the mouse at a recording time, averaged over ``smooth`` seconds
        near = [m for m in moves if abs(m[0] - rec_t) <= smooth / 2]
        if not near:
            near = [min(moves, key=lambda m: abs(m[0] - rec_t))]
        return (round(sum(m[1] for m in near) / len(near)), round(sum(m[2] for m in near) / len(near)))

    keys, t = [[0, *at(pairs[0][1])]], every
    while t < recap_at:
        keys.append([round(t, 3), *at(to_recording(pairs, t))])
        t += every
    clicks = [e["out"] for e in evs]
    keys = [k for k in keys if all(abs(k[0] - c) >= 0.05 for c in clicks)]
    for e in evs:   # a click always lands exactly where it was
        keys.append([e["out"], e["x"], e["y"]])
    keys = sorted({k[0]: k for k in keys}.values(), key=lambda k: k[0])
    keys.append([round(recap_at - 0.2 if keys[-1][0] < recap_at - 0.2 else recap_at, 3), *park])
    return _tidy(keys)


def sample(keys, t: float):
    """A key track's values at ``t``, eased as scene.html does."""
    if t <= keys[0][0]:
        return keys[0][1:]
    for a, b in zip(keys, keys[1:]):
        if t <= b[0]:
            k = _ease((t - a[0]) / (b[0] - a[0]))
            return [x + (y - x) * k for x, y in zip(a[1:], b[1:])]
    return keys[-1][1:]


__all__ = ["BuildError", "anchors", "build", "caption", "events", "group", "sample",
           "to_recording", "to_tutorial"]
