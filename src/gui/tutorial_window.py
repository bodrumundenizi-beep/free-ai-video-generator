"""Tutorial videos (5.4, plan step 5a): the Tutorial window and the Stop box.

1. Pick a window and press Analyze: everything that could go wrong is checked first (can this
   PC record it, is it on screen, does Windows name its buttons, is it run as administrator).
2. Analyze becomes Record: the user does the steps, then Stop (or Ctrl + Shift + F9).
3. The steps, one line each, editable; a step can be deleted.
4. Make: the tutorial MP4, with a text to post.
The recording itself stays in a temporary folder and is deleted when the window closes: it can
show more of the screen than the tutorial does.
"""
from __future__ import annotations

import ctypes
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback

import customtkinter as ctk

from vidgen import screen
from vidgen.tutorial import record

from .base import *  # noqa: F401,F403  (colours, Card, centre_on, writer...)
from .base import ACCENT, CARD_BG, TEXT, TEXT_DIM, asset_path, centre_on, default_output_dir


class StopBox(ctk.CTkToplevel):
    """"Recording 0:12  [Stop]": a small box at the top of the screen while the user works.

    Windows is asked to leave it out of the recording, and its clicks are left out of the log
    (``rect`` is passed to record.Session as ``ignore``). Ctrl + Shift + F9 stops too.
    """

    def __init__(self, app, on_stop, guide=()):
        super().__init__(app)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color=CARD_BG)
        self.on_stop, self.began = on_stop, time.monotonic()
        self.guide, self.done = list(guide), 0   # the steps to follow, and how many are done
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(padx=12, pady=10)
        ctk.CTkLabel(row, text="●", text_color="#E5484D", font=app.font_body).pack(side="left")
        self.clock = ctk.CTkLabel(row, text="Recording 0:00", font=app.font_body, text_color=TEXT)
        self.clock.pack(side="left", padx=(6, 12))
        ctk.CTkButton(row, text="Stop", width=70, height=30, command=self.stop,
                      fg_color=ACCENT).pack(side="left")
        ctk.CTkLabel(row, text=f"or {record.STOP_NAME}", font=app.font_tiny,
                     text_color=TEXT_DIM).pack(side="left", padx=(10, 0))
        self.steps_text = None
        if self.guide:   # a checklist: each step ticks itself off when its button is clicked
            self.steps_text = ctk.CTkLabel(self, text="", font=app.font_body, text_color=TEXT,
                                           justify="left", anchor="w")
            self.steps_text.pack(fill="x", padx=14, pady=(0, 10))
            self._show_steps()
        self.bind("<Escape>", lambda _e: self.stop())
        self.update_idletasks()
        # Tk measures in real pixels, but customtkinter scales what geometry() is given (125%
        # here put the box 1.25 times too far right, 2026-11-14): place it in its units, then
        # read back where it really is, in the real pixels the recording and the log use.
        scale = ctk.ScalingTracker.get_window_scaling(self)
        width = self.winfo_reqwidth()
        x = (self.winfo_screenwidth() - width) // 2
        self.geometry(f"+{round(x / scale)}+{round(8 / scale)}")
        self.update()
        self.rect = (self.winfo_rootx(), self.winfo_rooty(), self.winfo_width(), self.winfo_height())
        screen.hide_from_capture(ctypes.windll.user32.GetParent(self.winfo_id())
                                 if sys.platform == "win32" else 0)
        self.tick()

    def _show_steps(self):
        lines = []
        for i, step in enumerate(self.guide):
            mark = "✓" if i < self.done else ("→" if i == self.done else "  ")
            lines.append(f"{mark} {step}")
        self.steps_text.configure(text="\n".join(lines))

    def clicked(self, name: str):
        """A recorded click's name: the next step ticks off when it is that button."""
        from vidgen.tutorial.auto import _norm

        if self.done >= len(self.guide) or not name:
            return
        want, got = _norm(self.guide[self.done]), _norm(name)
        if want and got and (want == got or want in got or got in want):
            self.done += 1
            self._show_steps()

    def tick(self):
        if not self.winfo_exists():
            return
        seconds = int(time.monotonic() - self.began)
        self.clock.configure(text=f"Recording {seconds // 60}:{seconds % 60:02d}")
        self.after(500, self.tick)

    def stop(self):
        on_stop, self.on_stop = self.on_stop, None
        if on_stop is not None:
            on_stop()
        self.destroy()


class AutoBox(ctk.CTkToplevel):
    """The question box while the app does the steps: "Click Shapes? The cursor is on it."
    with Yes / I'll do it / Stop. Left out of the recording, like the Stop box; it sits at the
    bottom of the screen and moves to the top when it would cover the next button."""

    def __init__(self, app, on_answer):
        super().__init__(app)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color=CARD_BG)
        self.app, self.on_answer = app, on_answer
        self.text = ctk.CTkLabel(self, text="Starting...", font=app.font_body, text_color=TEXT,
                                 wraplength=440, justify="left", anchor="w")
        self.text.pack(fill="x", padx=14, pady=(12, 8))
        self.row = ctk.CTkFrame(self, fg_color="transparent")
        self.row.pack(fill="x", padx=14, pady=(0, 12))
        self.bind("<Escape>", lambda _e: on_answer("stop"))
        self.at_top = False
        self._place()
        screen.hide_from_capture(ctypes.windll.user32.GetParent(self.winfo_id())
                                 if sys.platform == "win32" else 0)

    def _place(self):
        self.update_idletasks()
        scale = ctk.ScalingTracker.get_window_scaling(self)
        width, height = max(self.winfo_reqwidth(), 480), self.winfo_reqheight()
        x = (self.winfo_screenwidth() - width) // 2
        y = 8 if self.at_top else self.winfo_screenheight() - height - 70
        self.geometry(f"+{round(x / scale)}+{round(y / scale)}")
        self.update()
        self.rect = (self.winfo_rootx(), self.winfo_rooty(), self.winfo_width(), self.winfo_height())

    def keep_clear_of(self, point):
        """Move to the other end of the screen if the box covers the button to click."""
        x, y, w, h = self.rect
        if point and x - 20 <= point[0] < x + w + 20 and y - 20 <= point[1] < y + h + 20:
            self.at_top = not self.at_top
            self._place()

    def ask(self, text, buttons):
        self.text.configure(text=text)
        for child in self.row.winfo_children():
            child.destroy()
        for i, (label, answer) in enumerate(buttons):
            first = i == 0
            ctk.CTkButton(self.row, text=label, height=30, width=140 if first else 110,
                          fg_color=ACCENT if first else FIELD_BG,
                          text_color=ACCENT_TEXT if first else TEXT,
                          border_width=0 if first else 1, border_color=FIELD_BORDER,
                          hover_color=ACCENT_HOVER if first else CARD_HOVER,
                          command=lambda a=answer: self.on_answer(a)).pack(
                side="left", padx=(0 if first else 6, 0))
        self._place()


EXAMPLE_STEPS = ("Insert → Shapes → Rectangle, drag on the slide, Shape Format → Shape Fill → "
                 "Orange")


def bold_names(text: str, names) -> str:
    """The user's line with the buttons it names highlighted ("click **Shapes**")."""
    for name in sorted({n for n in names if n}, key=len, reverse=True):
        found = re.search(re.escape(name), text, re.I)
        if found and "**" not in text[max(0, found.start() - 2):found.end() + 2]:
            text = (text[:found.start()] + f"**{text[found.start():found.end()]}**"
                    + text[found.end():])
    return text


def plain(text: str) -> str:
    return text.replace("**", "")


def window_label(window) -> str:
    return f"{window.app}: {window.title}"[:80]


class TutorialDialog(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Make a tutorial")
        self.configure(fg_color=MAIN_BG)
        self.transient(app)
        self.geometry("760x860")
        self.minsize(700, 600)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.events: queue.Queue = queue.Queue()
        self.stop = threading.Event()
        self.listed = []                 # the windows in the list (analyze.Window)
        self.report = None               # the last Analyze, for the window picked
        self.session = None              # recording now
        self.box = None
        self.folder = ""                 # the recording, its log and crops (deleted on close)
        self.log = None
        self.removed = set()             # clicks of deleted steps (their times)
        self.lines = {}                  # re-worded steps, by their first click's time
        self.timeline = None
        self.result = ""
        self.busy = False
        self.thumbs = []
        self.answers: queue.Queue = queue.Queue()   # the user's answers to the question box
        self.auto_stop = threading.Event()
        self.auto_actions = []
        self.auto_summary = ""
        self.offered = []        # the answers the question box offers now (first = the key's)
        self.auto_chains = []
        self.keys = None         # Ctrl + Shift + Y / N while the app does the steps

        outer = ctk.CTkScrollableFrame(self, fg_color=MAIN_BG)
        outer.pack(fill="both", expand=True, padx=8, pady=8)
        ctk.CTkLabel(outer, text="Make a tutorial", font=app.font_title, text_color=TEXT,
                     anchor="w").pack(fill="x", padx=12, pady=(6, 0))
        ctk.CTkLabel(outer, text="Show how you do something in any app. The app records it and "
                                 "makes a tutorial: zooms, a smooth cursor, labels and steps. "
                                 "Everything is made on this PC.",
                     font=app.font_tiny, text_color=TEXT_DIM, anchor="w", justify="left",
                     wraplength=660).pack(fill="x", padx=12, pady=(0, 10))
        self._window_card(outer)
        self._steps_card(outer)
        self._make_card(outer)
        self.refresh()
        self.after(150, self._poll)
        centre_on(self, app)

    # -- layout -------------------------------------------------------------------------
    def _card(self, parent, title, line=""):
        card = Card(parent)
        card.pack(fill="x", padx=6, pady=(0, 10))
        ctk.CTkLabel(card, text=title, font=self.app.font_bold, text_color=TEXT,
                     anchor="w").pack(fill="x", padx=16, pady=(12, 0))
        if line:
            ctk.CTkLabel(card, text=line, font=self.app.font_tiny, text_color=TEXT_DIM,
                         anchor="w", justify="left", wraplength=640).pack(fill="x", padx=16)
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=16, pady=(8, 14))
        return body

    def _button(self, parent, text, command, accent=False, width=150):
        return ctk.CTkButton(parent, text=text, width=width, height=34, corner_radius=4,
                             font=self.app.font_bold if accent else self.app.font_body,
                             fg_color=ACCENT if accent else FIELD_BG,
                             hover_color=ACCENT_HOVER if accent else CARD_HOVER,
                             text_color=ACCENT_TEXT if accent else TEXT,
                             text_color_disabled=ACCENT_TEXT if accent else TEXT_DIM,
                             border_width=0 if accent else 1, border_color=FIELD_BORDER,
                             command=command)

    def _window_card(self, parent):
        body = self._card(parent, "1. The app to record",
                          "Open the app you want to show, pick its window here and press "
                          "Analyze: the app checks it can record it before you start.")
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x")
        self.var_window = ctk.StringVar(value="")
        self.window_icon = ctk.CTkLabel(row, text="", width=32, height=32)   # the app's own icon
        self.window_icon.pack(side="left", padx=(0, 8))
        self.window_menu = ctk.CTkOptionMenu(row, variable=self.var_window, values=[""], width=440,
                                             height=34, corner_radius=4, font=self.app.font_body,
                                             fg_color=FIELD_BG, button_color=FIELD_BORDER,
                                             text_color=TEXT, command=lambda _v: self._picked())
        self.window_menu.pack(side="left")
        self._button(row, "Refresh", self.refresh, width=90).pack(side="left", padx=(6, 0))
        title_row = ctk.CTkFrame(body, fg_color="transparent")
        title_row.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(title_row, text="Title", font=self.app.font_body, text_color=TEXT_MUTED,
                     width=60, anchor="w").pack(side="left")
        self.title_entry = ctk.CTkEntry(title_row, width=440, height=32, corner_radius=4,
                                        font=self.app.font_body, fg_color=FIELD_BG,
                                        border_color=FIELD_BORDER, text_color=TEXT,
                                        placeholder_text="How to add a coloured rectangle")
        self.title_entry.pack(side="left")
        go = ctk.CTkFrame(body, fg_color="transparent")
        go.pack(fill="x", pady=(12, 0))
        self.go_button = self._button(go, "Analyze", self.analyze, accent=True, width=160)
        self.go_button.pack(side="left")
        self.check_text = ctk.CTkLabel(body, text="", font=self.app.font_tiny, text_color=TEXT_DIM,
                                       anchor="w", justify="left", wraplength=640)
        self.check_text.pack(fill="x", pady=(8, 0))
        # Do it for me: shown after Analyze, when Windows lets the app move the mouse
        self.auto_frame = ctk.CTkFrame(body, fg_color="transparent")
        ctk.CTkLabel(self.auto_frame, text="The steps, in order (Suggest can write them). They "
                     "guide you while you Record, or the app does them: Do it for me. Arrows join "
                     "clicks the app does in one go (a menu and the pick in it); commas part the "
                     "steps.",
                     font=self.app.font_tiny, text_color=TEXT_DIM, anchor="w", justify="left",
                     wraplength=640).pack(fill="x", pady=(10, 4))
        self.auto_steps = ctk.CTkTextbox(self.auto_frame, height=60, font=self.app.font_body,
                                         fg_color=FIELD_BG, text_color=TEXT, border_width=1,
                                         border_color=FIELD_BORDER, wrap="word")
        self.auto_steps.pack(fill="x")
        # empty: an app-specific example (PowerPoint's) only confused other apps (Deniz, 2026-11-14)
        ctk.CTkLabel(self.auto_frame, text="For example: Search → Find..., Find Next. Or press "
                     "Suggest the steps (write what to do in the Title first).",
                     font=self.app.font_tiny, text_color=TEXT_DIM, anchor="w").pack(fill="x")
        auto_row = ctk.CTkFrame(self.auto_frame, fg_color="transparent")
        auto_row.pack(fill="x", pady=(6, 0))
        self.auto_button = self._button(auto_row, "Do it for me", self.do_it, width=140)
        self.auto_button.pack(side="left")
        self.suggest_button = self._button(auto_row, "Suggest the steps", self.suggest, width=150)
        self.suggest_button.pack(side="left", padx=(6, 0))
        self.auto_note = ctk.CTkLabel(auto_row, text="", font=self.app.font_tiny,
                                      text_color=TEXT_DIM, anchor="w")
        self.auto_note.pack(side="left", padx=(10, 0))

    def _steps_card(self, parent):
        self.steps_body = self._card(parent, "2. The steps",
                                     "One line each, shown under the video. Change any line; "
                                     "delete a step you got wrong.")
        self.steps_info = ctk.CTkLabel(self.steps_body, text="Record first: the steps appear "
                                       "here.", font=self.app.font_tiny, text_color=TEXT_DIM,
                                       anchor="w")
        self.steps_info.pack(fill="x")
        self.steps_list = ctk.CTkFrame(self.steps_body, fg_color="transparent")
        self.steps_list.pack(fill="x")
        style = ctk.CTkFrame(self.steps_body, fg_color="transparent")
        style.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(style, text="Cursor", font=self.app.font_body, text_color=TEXT_MUTED,
                     width=60, anchor="w").pack(side="left")
        self.var_cursor = ctk.StringVar(value="Glide")
        ctk.CTkSegmentedButton(style, values=["Glide", "Real path"], variable=self.var_cursor,
                               font=self.app.font_body, command=lambda _v: self._rebuild()
                               ).pack(side="left")
        ctk.CTkLabel(style, text="Shape", font=self.app.font_body, text_color=TEXT_MUTED,
                     anchor="w").pack(side="left", padx=(24, 8))
        self.var_shape = ctk.StringVar(value="16:9")
        ctk.CTkSegmentedButton(style, values=["16:9", "9:16"], variable=self.var_shape,
                               font=self.app.font_body, command=lambda _v: self._rebuild()
                               ).pack(side="left")
        sound = ctk.CTkFrame(self.steps_body, fg_color="transparent")
        sound.pack(fill="x", pady=(8, 0))
        from vidgen import localvoice

        voice_ok, why = localvoice.ready()
        self.var_voice_on = ctk.BooleanVar(value=voice_ok)
        voice_box = ctk.CTkCheckBox(sound, text="Read the steps aloud (the voice from Settings)",
                                    variable=self.var_voice_on, font=self.app.font_body,
                                    text_color=TEXT)
        voice_box.pack(side="left")
        if not voice_ok:
            voice_box.configure(state="disabled")
            ctk.CTkLabel(sound, text=why, font=self.app.font_tiny, text_color=TEXT_DIM).pack(
                side="left", padx=(8, 0))
        self.var_music_on = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(sound, text="Quiet music", variable=self.var_music_on,
                        font=self.app.font_body, text_color=TEXT).pack(side="left", padx=(16, 0))

    def _make_card(self, parent):
        body = self._card(parent, "3. Make it")
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x")
        self.make_button = self._button(row, "Make tutorial", self.make, accent=True, width=200)
        self.make_button.pack(side="left")
        self.make_button.configure(state="disabled")
        self.stop_button = self._button(row, "Stop", self.stop_making, width=80)
        self.stop_button.pack(side="left", padx=(8, 0))
        self.stop_button.configure(state="disabled")
        self.progress = ctk.CTkProgressBar(body, height=8, progress_color=ACCENT)
        self.progress.set(0)
        self.progress.pack(fill="x", pady=(10, 4))
        self.status = ctk.CTkLabel(body, text="", font=self.app.font_tiny, text_color=TEXT_DIM,
                                   anchor="w", justify="left", wraplength=640)
        self.status.pack(fill="x")
        self.done_row = ctk.CTkFrame(body, fg_color="transparent")
        self._button(self.done_row, "Play", lambda: os.startfile(self.result), width=90).pack(  # noqa: S606
            side="left")
        self._button(self.done_row, "Open folder", self.open_folder, width=110).pack(
            side="left", padx=(6, 0))
        self.share = ctk.CTkTextbox(body, height=110, font=self.app.font_body, fg_color=FIELD_BG,
                                    text_color=TEXT, border_width=0, wrap="word")
        self.copy_button = self._button(body, "Copy text", self.copy_share, width=110)

    # -- 1. pick and analyze ------------------------------------------------------------
    def refresh(self):
        from vidgen.tutorial import analyze

        self.listed = analyze.windows()
        names = [window_label(w) for w in self.listed] or ["No app windows are open"]
        self.window_menu.configure(values=names)
        if self.var_window.get() not in names:
            self.var_window.set(names[0])
        self._picked()

    def chosen(self):
        names = [window_label(w) for w in self.listed]
        try:
            return self.listed[names.index(self.var_window.get())]
        except ValueError:
            return None

    def _picked(self):
        """A different window: it must be analyzed again before recording."""
        from vidgen.tutorial import analyze

        window = self.chosen()
        picture = analyze.icon_of(window.hwnd) if window else None
        if picture is not None:
            self._icon = ctk.CTkImage(light_image=picture, dark_image=picture, size=(28, 28))
            self.window_icon.configure(image=self._icon)
        else:
            self._icon = None
            self.window_icon.configure(image="")
        self.report = None
        self.go_button.configure(text="Analyze", command=self.analyze)
        self.check_text.configure(text="")
        self.auto_frame.pack_forget()

    def analyze(self):
        from vidgen.tutorial import analyze

        window = self.chosen()
        if window is None:
            self.check_text.configure(text="Open the app you want to show, then press Refresh.",
                                      text_color=ERR_COLOR)
            return
        self.go_button.configure(state="disabled")
        self.check_text.configure(text="Checking...", text_color=TEXT_DIM)

        def worker():
            try:
                self.events.put(("analyzed", (window, analyze.analyze(window.hwnd))))
            except Exception:  # noqa: BLE001
                self.events.put(("analyze_failed",
                                 traceback.format_exc().strip().splitlines()[-1]))

        threading.Thread(target=worker, daemon=True).start()

    def _analyzed(self, window, report):
        self.go_button.configure(state="normal")
        marks = {"ok": "✓", "note": "•", "problem": "✗"}
        self.check_text.configure(text="\n".join(f"{marks[k]} {t}" for k, t in report.lines),
                                  text_color=TEXT_MUTED if report.ok else ERR_COLOR)
        if report.ok and self.chosen() is window:
            self.report = report
            self.go_button.configure(text="Record", command=self.record)
            if report.can_move:
                self.auto_frame.pack(fill="x")

    # -- 2. record ----------------------------------------------------------------------
    def record(self):
        window = self.chosen()
        if self.report is None or window is None or self.busy:
            return
        self._clear_recording()
        self.folder = tempfile.mkdtemp(prefix="aivs-tutorial-rec-")
        self.go_button.configure(state="disabled")
        self._countdown(3, window)

    def _countdown(self, n, window):
        if n:
            self.check_text.configure(text=f"Recording starts in {n}... Then do your steps; "
                                           f"press Stop (or {record.STOP_NAME}) when done.",
                                      text_color=TEXT)
            self.after(1000, lambda: self._countdown(n - 1, window))
            return
        # centre_on() made this window modal: a hidden window keeping that grab swallowed every
        # click on the Stop box and the question box (2026-11-14), so let go while recording
        self.grab_release()
        self.withdraw()
        if sys.platform == "win32":   # the app to record in front
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
            ctypes.windll.user32.ShowWindow(window.hwnd, 9)
            ctypes.windll.user32.SetForegroundWindow(window.hwnd)
        self.box = StopBox(self.app, self.finish_recording, self._guide_steps())
        self._seen_clicks = 0
        try:
            self.session = record.Session(window.hwnd, self.folder, ignore=self.box.rect)
            self.session.__enter__()
        except OSError as exc:
            self.box.destroy()
            self.box, self.session = None, None
            self.deiconify()
            self._modal_again()
            self.go_button.configure(state="normal")
            self.check_text.configure(text=f"Recording didn't start: {exc}", text_color=ERR_COLOR)
            return
        self._watch_hotkey()

    def _modal_again(self):
        try:
            self.grab_set()
        except Exception:  # noqa: BLE001 - another window has the grab: fine
            pass

    def _guide_steps(self) -> list[str]:
        """The steps written or suggested in the box, to guide a recording; none while it
        still holds the PowerPoint example."""
        from vidgen.tutorial import auto

        text = self.auto_steps.get("1.0", "end").strip()
        if not text or text == EXAMPLE_STEPS:
            return []
        return [a.name for a in auto.parse(text)]

    def _tick_guide(self):
        """Tell the Stop box the names of the new clicks (they arrive a moment later)."""
        box, session = self.box, self.session
        if not isinstance(box, StopBox) or session is None or not box.guide:
            return
        clicks = session.clicks
        while self._seen_clicks < len(clicks):
            click = clicks[self._seen_clicks]
            name = click.get("name") or (session._hovered[self._seen_clicks].get("name")
                                         if self._seen_clicks < len(session._hovered) else "")
            if not name and session._now() - click["t"] < 1.5:
                break   # its name may still be on the way
            box.clicked(name or "")
            self._seen_clicks += 1

    def _watch_hotkey(self):
        if self.session is None:
            return
        self._tick_guide()
        if self.session.stopped.is_set():   # Ctrl + Shift + F9
            if self.auto_actions:
                self.auto_stop.set()        # the worker ends; auto_done finishes the recording
            elif self.box is not None:
                self.box.stop()             # calls finish_recording
            return
        self.after(150, self._watch_hotkey)

    def finish_recording(self):
        session, self.session = self.session, None
        self.box = None
        if session is None:
            return
        # Stop the screen recording before this window shows again: it appeared on top of the
        # recorded app while ffmpeg was still recording, and got into the video (2026-11-14).
        session.stopped.set()
        session.recorder.stop()
        self.deiconify()
        self.lift()
        self._modal_again()
        self.go_button.configure(state="normal")
        self.check_text.configure(text="Reading your steps (the Smart writer looks at clicks "
                                       "Windows couldn't name)...", text_color=TEXT_DIM)

        def worker():
            try:
                log = session.stop()
                try:   # names from pictures; without the Smart writer the lines stay editable
                    record.name_crops(log, record.crops(session.folder, log))
                except Exception:  # noqa: BLE001
                    pass
                self.events.put(("recorded", log))
            except Exception:  # noqa: BLE001
                self.events.put(("record_failed",
                                 traceback.format_exc().strip().splitlines()[-1]))

        threading.Thread(target=worker, daemon=True).start()

    def _recorded(self, log):
        self.log = log
        self.removed, self.lines = set(), {}
        clicks = [c for c in log["clicks"] if c.get("button", "left") == "left"]
        if not clicks:
            self.check_text.configure(text="Nothing was clicked in the recording. Record again "
                                           "and click through your steps.", text_color=ERR_COLOR)
            return
        extra = f" {self.auto_summary}" if self.auto_summary else ""
        hidden = sum(b - a for a, b in log.get("covered", []))
        if hidden:
            extra += (f" Another window covered the app for {hidden:.0f} s (a notification?): the "
                      "video shows the app as it was just before.")
        self.auto_summary = ""
        self.check_text.configure(text=f"Recorded {log['length']:.0f} s with {len(clicks)} "
                                       f"clicks.{extra}", text_color=OK_COLOR)
        self._rebuild()

    # -- Do it for me ---------------------------------------------------------------------
    def suggest(self):
        from vidgen.tutorial import auto

        task = self.title_entry.get().strip()
        window = self.chosen()
        if not task or window is None:
            self.auto_note.configure(text="Write what to do in the Title first.",
                                     text_color=ERR_COLOR)
            return
        ok, why = writer.ready()
        if not ok:
            self.auto_note.configure(text=why, text_color=ERR_COLOR)
            return
        self.suggest_button.configure(state="disabled")
        self.auto_note.configure(text="The Smart writer is thinking...", text_color=TEXT_DIM)
        gpu = bool(self.app.var_writer_gpu.get())

        def worker():
            try:
                with writer.Session(self.stop.is_set, gpu=gpu) as session:
                    def ask(system, user, schema):
                        return session._ask(system, [("user", user)], schema, 300, 0.2)
                    self.events.put(("suggested", (True, auto.suggest(task, window.app, ask))))
            except Exception as exc:  # noqa: BLE001
                self.events.put(("suggested", (False, f"The Smart writer couldn't suggest "
                                                      f"steps: {exc}")))

        threading.Thread(target=worker, daemon=True).start()

    def _suggested(self, payload):
        ok, text = payload
        self.suggest_button.configure(state="normal")
        if not ok:
            self.auto_note.configure(text=text, text_color=ERR_COLOR)
            return
        self.auto_steps.delete("1.0", "end")
        self.auto_steps.insert("1.0", text)
        self.auto_note.configure(text="Check these: the Smart writer can be wrong.",
                                 text_color=TEXT_MUTED)

    def do_it(self):
        from vidgen.tutorial import auto

        window = self.chosen()
        if self.report is None or not self.report.can_move or window is None or self.busy:
            return
        actions = auto.parse(self.auto_steps.get("1.0", "end"))
        if not actions:
            self.auto_note.configure(text="Write the clicks first.", text_color=ERR_COLOR)
            return
        why = auto.refused(window.title)
        if why:
            self.auto_note.configure(text=why, text_color=ERR_COLOR)
            return
        self._clear_recording()
        self.folder = tempfile.mkdtemp(prefix="aivs-tutorial-rec-")
        self.auto_actions = actions
        self.auto_chains = sorted({a.chain for a in actions})
        self.auto_stop.clear()
        while not self.answers.empty():
            self.answers.get_nowait()
        self.go_button.configure(state="disabled")
        self.auto_button.configure(state="disabled")
        self._auto_countdown(3, window)

    def _auto_countdown(self, n, window):
        from vidgen.tutorial import auto

        if n:
            self.check_text.configure(text=f"Starting in {n}... The app asks before each click; "
                                           "keep your hands off the mouse while it moves.",
                                      text_color=TEXT)
            self.after(1000, lambda: self._auto_countdown(n - 1, window))
            return
        # centre_on() made this window modal: a hidden window keeping that grab swallowed every
        # click on the Stop box and the question box (2026-11-14), so let go while recording
        self.grab_release()
        self.withdraw()
        if sys.platform == "win32":
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
            ctypes.windll.user32.ShowWindow(window.hwnd, 9)
            ctypes.windll.user32.SetForegroundWindow(window.hwnd)
        self.box = AutoBox(self.app, self.answers.put)
        try:
            self.session = record.Session(window.hwnd, self.folder, ignore=self.box.rect)
            self.session.__enter__()
        except OSError as exc:
            self.box.destroy()
            self.box, self.session, self.auto_actions = None, None, []
            self.deiconify()
            self._modal_again()
            self.go_button.configure(state="normal")
            self.auto_button.configure(state="normal")
            self.check_text.configure(text=f"Recording didn't start: {exc}", text_color=ERR_COLOR)
            return
        from vidgen.tutorial import hotkeys

        self.keys = hotkeys.Hotkeys(self._key)
        finder = auto.PictureFinder(self.session.area, self.folder, self.auto_stop.is_set,
                                    bool(self.app.var_writer_gpu.get()))
        runner = auto.Runner(window.hwnd, self.auto_actions, self._ask_from_worker,
                             auto.RealScreen(window.hwnd, self.session.area, finder),
                             self.auto_stop.is_set, on_click=self.session.note_click)

        def worker():
            try:
                report = runner.run()
            except PermissionError as exc:
                report = auto.Report()
                report.results.append(auto.Result(auto.Action("click", "", ""), "skipped",
                                                  str(exc)))
            except Exception:  # noqa: BLE001
                report = auto.Report()
                report.results.append(auto.Result(
                    auto.Action("click", "", ""), "skipped",
                    "The app hit a problem: " + traceback.format_exc().strip().splitlines()[-1]))
            finally:
                finder.close()
            self.events.put(("auto_done", report))

        threading.Thread(target=worker, daemon=True).start()
        self._watch_hotkey()

    def _key(self, which):
        """Ctrl + Shift + Y answers like the box's first button, N like "I'll do it". The keys
        reach this app only, so the recorded app's open menu stays open."""
        offered = list(self.offered)
        if which == "yes" and offered:
            self.answers.put(offered[0])
        elif which == "no" and "self" in offered:
            self.answers.put("self")

    def _ask_from_worker(self, kind, action, target, why):
        """Runs in the worker: show the question, wait for the answer (or Stop)."""
        self.events.put(("ask", (kind, action, target, why)))
        while True:
            if self.auto_stop.is_set():
                return "stop"
            try:
                answer = self.answers.get(timeout=0.2)
            except queue.Empty:
                continue
            if answer == "stop":
                self.auto_stop.set()
            self.offered = []
            return answer

    def _ask(self, kind, action, target, why):
        from vidgen.tutorial import hotkeys

        if self.box is None or not self.box.winfo_exists():
            return
        number = next((i + 1 for i, c in enumerate(self.auto_chains) if c == action.chain), 0)
        head = f"Step {number} of {len(self.auto_chains)}: "
        keys = (f"\nKeys: {hotkeys.YES_NAME} = {{yes}}"
                + (f", {hotkeys.NO_NAME} = I'll do it" if kind == "approve" else "")
                + " (they keep the app's menus open).") if self.keys and self.keys.ok else ""
        if kind == "approve":
            if target is not None:
                self.box.keep_clear_of((target.x, target.y))
            if self.session is not None:
                self.session.ignore = self.box.rect
            where = " The cursor is on the first one." if target is not None else (
                f" ({why}: the app looks again when it gets there.)" if why else "")
            seen = (" The app isn't sure about this one: you'll be asked to click it."
                    if target is not None and target.doubt else "")
            self.offered = ["yes", "self", "stop"]
            self.box.ask(head + f"the app clicks {action.name}.{where}{seen} OK?"
                         + keys.format(yes="Yes"),
                         [("Yes, do it", "yes"), ("I'll do it", "self"), ("Stop", "stop")])
        elif kind == "hand":
            reason = f" (The app: {why}.)" if why else ""
            self.offered = ["done", "stop"]
            self.box.ask(head + f"your turn: {action.text}.{reason} Then press Done."
                         + keys.format(yes="Done"), [("Done", "done"), ("Stop", "stop")])
        elif kind == "moved":
            self.offered = ["continue", "stop"]
            self.box.ask("You moved the mouse, so the app paused. Keep your hands off it while "
                         "it moves, then press Continue." + keys.format(yes="Continue"),
                         [("Continue", "continue"), ("Stop", "stop")])

    def _auto_done(self, report):
        self.auto_actions = []
        self.offered = []
        if self.keys is not None:
            self.keys.stop()
            self.keys = None
        if report.results and not report.results[0].action.name:
            self.auto_summary = report.results[0].why    # refused or a problem: say it
        else:
            self.auto_summary = report.summary()
        box, self.box = self.box, None
        if box is not None and box.winfo_exists():
            box.destroy()
        self.auto_button.configure(state="normal")
        self.finish_recording()

    # -- the steps ------------------------------------------------------------------------
    def _log_now(self) -> dict:
        """The log without the deleted steps' clicks."""
        log = dict(self.log)
        log["clicks"] = [c for c in self.log["clicks"] if c["t"] not in self.removed]
        return log

    def _apply_lines(self, steps):
        """Put the user's re-worded lines into the timeline (and its Done list)."""
        for i, (shown, step) in enumerate(zip(self.timeline["steps"], steps)):
            key = step[0]["t"]
            if key in self.lines:
                names = [e["name"][:1].upper() + e["name"][1:] for e in step if e["name"]]
                shown["text"] = bold_names(self.lines[key], names + ["Drag"])
                self.timeline["recap"]["rows"][i] = plain(self.lines[key])

    def _rebuild(self):
        from vidgen.tutorial import build

        if self.log is None:
            return
        title = self.title_entry.get().strip()
        cursor = "real" if self.var_cursor.get() == "Real path" else "glide"
        try:
            self.timeline = build.build(self._log_now(), title=title, cursor=cursor,
                                        shape=self.var_shape.get())
        except build.BuildError as exc:
            self.timeline = None
            self.steps_info.configure(text=str(exc), text_color=ERR_COLOR)
            self.make_button.configure(state="disabled")
            return
        steps = build.group(build.events(self._log_now()))
        self._apply_lines(steps)
        self._show_steps(steps)
        self.make_button.configure(state="normal")

    def _show_steps(self, steps):
        for child in self.steps_list.winfo_children():
            child.destroy()
        self.thumbs = []
        self.steps_info.configure(
            text=f"{len(steps)} steps. The tutorial will be about "
                 f"{self.timeline['length']:.0f} s long.", text_color=TEXT_DIM)
        video = os.path.join(self.folder, "recording.mp4")
        for i, (step, shown) in enumerate(zip(steps, self.timeline["steps"])):
            row = Card(self.steps_list)
            row.pack(fill="x", pady=(6, 0))
            first = step[0]
            picture = self._thumb(video, first)
            if picture is not None:
                image = ctk.CTkImage(light_image=picture, dark_image=picture, size=(160, 90))
                self.thumbs.append(image)
                ctk.CTkLabel(row, text="", image=image, width=160, height=90).pack(
                    side="left", padx=8, pady=8)
            middle = ctk.CTkFrame(row, fg_color="transparent")
            middle.pack(side="left", fill="x", expand=True, pady=8)
            guessed = any(e.get("guessed") for e in step)
            ctk.CTkLabel(middle, text=f"Step {i + 1}" + (" · check the name: it was read from "
                                                         "the picture" if guessed else ""),
                         font=self.app.font_tiny, text_color=WARN_COLOR if guessed else TEXT_DIM,
                         anchor="w").pack(fill="x")
            entry = ctk.CTkEntry(middle, height=32, corner_radius=4, font=self.app.font_body,
                                 fg_color=FIELD_BG, border_color=FIELD_BORDER, text_color=TEXT)
            entry.insert(0, plain(shown["text"]))
            entry.pack(fill="x", padx=(0, 8))
            key = first["t"]
            entry.bind("<FocusOut>", lambda _e, k=key, w=entry: self._reworded(k, w.get()))
            entry.bind("<Return>", lambda _e, k=key, w=entry: self._reworded(k, w.get()))
            self._button(row, "Delete", lambda k=[e["t"] for e in step]: self._delete(k),
                         width=70).pack(side="right", padx=8)

    def _thumb(self, video, first):
        """The moment just before the step's first click, cut round it."""
        from PIL import Image

        path = os.path.join(self.folder, f"thumb_{first['t']:.3f}.png")
        try:
            if not os.path.exists(path):
                record.frame_at(video, first["t"] - 0.1, path)
                with Image.open(path) as im:
                    x0 = min(max(first["x"] - 320, 0), max(im.width - 640, 0))
                    y0 = min(max(first["y"] - 180, 0), max(im.height - 360, 0))
                    piece = im.convert("RGB").crop((x0, y0, x0 + 640, y0 + 360))
                piece.save(path)
            with Image.open(path) as im:
                return im.copy()
        except (OSError, RuntimeError, ValueError):
            return None

    def _reworded(self, key, text):
        from vidgen.tutorial import build

        text = " ".join(text.split())
        if text and self.timeline is not None:
            self.lines[key] = text
            self._apply_lines(build.group(build.events(self._log_now())))

    def _delete(self, times):
        self.removed.update(times)
        self._rebuild()

    # -- 3. make --------------------------------------------------------------------------
    def make(self):
        from vidgen import tutorial
        from vidgen.promo import browser

        if self.busy or self.timeline is None:
            return
        if browser.find() is None:
            self.status.configure(text="Tutorials are drawn by a web browser, offline: install "
                                       "Google Chrome or Brave (both free), then try again.",
                                  text_color=ERR_COLOR)
            return
        title = self.title_entry.get().strip() or self.timeline["title"]["title"]
        self.timeline["title"]["title"] = title
        timeline, log, folder = self.timeline, self._log_now(), self.folder
        from vidgen.promo.sound import pick_music

        voice = self.app.var_voice.get() if self.var_voice_on.get() else None
        music_dir = asset_path("music")
        music = pick_music("polished", music_dir) if self.var_music_on.get() and music_dir else None
        sfx = asset_path("sfx")
        out = self._out_path(title)
        self.stop.clear()
        self._set_busy(True)
        self.done_row.pack_forget()
        self.share.pack_forget()
        self.copy_button.pack_forget()
        self.progress.set(0)

        def progress(done, total, phase):
            if phase == "sound" or done % 15 == 0 or done == total:
                self.events.put(("progress", (done, total, phase)))

        def worker():
            try:
                tutorial.make(timeline, folder, out, should_stop=self.stop.is_set,
                              progress=progress, recording=os.path.join(folder, "recording.mp4"),
                              log=log, voice=voice, music=music, sfx_folder=sfx)
                self.events.put(("made", (out, timeline)))
            except writer.Stopped:
                self.events.put(("failed", "Stopped."))
            except (tutorial.TutorialError, tutorial.BrowserError, OSError, RuntimeError) as exc:
                self.events.put(("failed", str(exc)))
            except Exception:  # noqa: BLE001
                self.events.put(("failed", "Something went wrong: "
                                 + traceback.format_exc().strip().splitlines()[-1]))

        threading.Thread(target=worker, daemon=True).start()

    def _out_path(self, title) -> str:
        folder = os.path.dirname(self.app.var_output.get().strip()) or default_output_dir()
        os.makedirs(folder, exist_ok=True)
        safe = re.sub(r'[<>:"/\\|?*]+', "", title).strip() or "Tutorial"
        base = os.path.join(folder, f"{safe} (tutorial)")
        path, n = base + ".mp4", 2
        while os.path.exists(path):
            path, n = f"{base} {n}.mp4", n + 1
        return path

    def stop_making(self):
        self.stop.set()
        self.status.configure(text="Stopping...", text_color=TEXT_DIM)

    def _set_busy(self, busy):
        self.busy = busy
        self.make_button.configure(state="disabled" if busy else "normal")
        self.go_button.configure(state="disabled" if busy else "normal")
        self.stop_button.configure(state="normal" if busy else "disabled")

    def _poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "analyzed":
                    self._analyzed(*payload)
                elif kind == "analyze_failed":
                    self.go_button.configure(state="normal")
                    self.check_text.configure(text="The check hit a problem: " + payload,
                                              text_color=ERR_COLOR)
                elif kind == "recorded":
                    self._recorded(payload)
                elif kind == "ask":
                    self._ask(*payload)
                elif kind == "auto_done":
                    self._auto_done(payload)
                elif kind == "suggested":
                    self._suggested(payload)
                elif kind == "record_failed":
                    self.check_text.configure(text="The recording hit a problem: " + payload,
                                              text_color=ERR_COLOR)
                elif kind == "progress":
                    done, total, phase = payload
                    share = done / total
                    if phase == "sound":
                        self.progress.set(0.96 + share * 0.04)
                        self.status.configure(text="Adding the voice and music...",
                                              text_color=TEXT_DIM)
                        continue
                    self.progress.set(share * 0.33 if phase == "prepare" else 0.33 + share * 0.63)
                    what = "Preparing the recording" if phase == "prepare" else "Drawing frame"
                    self.status.configure(text=f"{what}: {done} of {total}...",
                                          text_color=TEXT_DIM)
                elif kind == "made":
                    self._set_busy(False)
                    self._made(*payload)
                elif kind == "failed":
                    self._set_busy(False)
                    self.progress.set(0)
                    self.status.configure(text=payload, text_color=ERR_COLOR)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(150, self._poll)

    def _made(self, out, timeline):
        self.result = out
        self.progress.set(1)
        self.status.configure(text=f"Saved: {out}", text_color=OK_COLOR)
        self.done_row.pack(fill="x", pady=(8, 0))
        rows = "\n".join(f"{i + 1}. {row}" for i, row in enumerate(timeline["recap"]["rows"]))
        self.share.delete("1.0", "end")
        self.share.insert("1.0", f"{timeline['title']['title']}\n\n{rows}")
        self.share.pack(fill="x", pady=(8, 4))
        self.copy_button.pack(anchor="w")
        self.app.append_log(f"🎬 Tutorial saved: {out}")

    def copy_share(self):
        self.clipboard_clear()
        self.clipboard_append(self.share.get("1.0", "end").strip())
        self.copy_button.configure(text="Copied")
        self.after(1500, lambda: self.copy_button.configure(text="Copy text"))

    def open_folder(self):
        if self.result:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self.result)])  # noqa: S603,S607

    def _clear_recording(self):
        if self.folder:
            shutil.rmtree(self.folder, ignore_errors=True)
        self.folder, self.log, self.timeline = "", None, None

    def close(self):
        self.stop.set()
        self.auto_stop.set()
        if self.keys is not None:
            self.keys.stop()
        if self.session is not None:
            try:
                self.session.stop()
            except Exception:  # noqa: BLE001
                pass
        if self.box is not None and self.box.winfo_exists():
            self.box.destroy()
        self._clear_recording()
        self.destroy()
