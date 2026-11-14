# Tutorial house style (the reference)

The hand-made PowerPoint test of 2026-11-14, "Add a coloured rectangle" (Insert → Shapes →
Rectangle, drag, Shape Format → Shape Fill → Gold). The tutorial feature (5.4) must make its
twin. Numbers below are copied from `tut.html`; that file is the truth if they ever disagree.

## Files

| File | What |
|---|---|
| `tut.html` | The page: timeline written by hand, drawn by `promo/browser.py` `record()` at 1920×1080, 30 fps, 17 s |
| `s1.png` Home, `s1b.png` Insert tab, `s2.png` Shapes menu, `s3.png` rectangle drawn, `s4.png` Shape Format, `s5.png` Shape Fill menu, `s6.png` gold result | Real PowerPoint screenshots, 1586×926, account initials hidden; the tip box on `s5` covered with the strip from `s4` |
| `reference.mp4` | The finished video (kept locally only: `*.mp4` is git-ignored). Rebuild it from `tut.html` + clicks below |

## Stage

- 1920×1080. The app window (screenshot pixels) is placed so the camera centre lands at
  (960, 520): `translate(960 − cx·z, 520 − cy·z) scale(z)`, origin top-left.
- Window: corner radius 10 px, shadow `0 40px 100px rgba(0,0,0,.65)` + 1 px white 8% edge.
- Backdrop: the first screenshot, blur 44 px, brightness .42, saturation 1.1; two accent blobs,
  opacity .16, blur 130 px.

## Look (taken from the recorded app)

- Accent `#E8763A` (PowerPoint's own Share-button orange); dark text on accent `#1A0D05`.
- Font Segoe UI (the recorded app's font): step pill 600 26 px, step title 600 30 px on a dark
  box, caption 600 44 px, callout 600 30 px, title 700 76 px, recap rows 400 30 px.
- Office corners: 4–10 px, never round pills.

## Motion

| Thing | Value |
|---|---|
| Easing (camera, cursor) | ease-in-out cubic |
| Easing (rings, slides) | ease-out cubic |
| Zoom on ribbon/menu clicks | 1.9 (1.75 for the colour menu) |
| Zoom while dragging on the slide | 1.2 |
| Zoom at the end (Done card) | 0.9, window shifted left (centre x 1080) |
| Camera move | 0.6–0.8 s, arrives just before the cursor |
| Cursor travel between clicks | 0.65–0.85 s (Glide: eased curve, click to click) |
| Cursor press | scale .86 for 0.12 s at each click, and through a drag |
| Click ring | 2 rings, 0.6 s, size 30 → 140 px, fade out; accent glow |
| Callout (button name) | shows at the click for 0.45–1.05 s, fades in/out 0.2 s, 46 px below the click, rises 14 px |
| Spotlight | radial, clear 60 px → 520 px, darkness .26 at full callout |
| Screen change | crossfade 0.18 s, starts 0.05 s after the click |
| Drag | 1.3 s, rectangle grows from the press point to the cursor |
| Caption | changes per step, slides up 20 px over 0.3 s |
| Step pill + 4 bars | "Step N of 4", bars light up to N |
| Progress bar | 7 px, the bottom, fills over the whole video |
| Title card | 0 – 1.4 s, fade 0.3 s: tag "PowerPoint tutorial", title, "4 steps · 15 seconds" |
| Done card | from 14.2 s, slides in 40 px over 0.4 s; rows tick in every 0.3 s from 14.5 s |
| Length | 17 s for 7 clicks + 1 drag in 4 steps |

## Timeline (seconds)

Clicks (and their sounds): 2.3 Insert · 3.1 Shapes · 4.2 Rectangle · 6.0 drag start · 9.3 Shape
Format · 10.15 Shape Fill · 12.5 Gold.

Steps: 1.3 "Open **Insert**, then **Shapes**" · 5.2 "**Drag** on the slide to draw it" ·
8.3 "**Shape Format**, then **Shape Fill**" · 11.3 "Pick a **colour**".

## Sound

- `assets/sfx/click_003.ogg` at every click, volume 0.7. No music, no voice in the reference
  (5.4 adds the voice and quiet music on top; they must not change the timing above).
