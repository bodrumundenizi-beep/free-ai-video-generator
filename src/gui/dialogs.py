"""The app's dialogs: welcome, tour, what's new, results, reports and the rest. (Split out of ai_video_studio.py; that file still exports every name.)"""

from __future__ import annotations

from gui.base import *  # noqa: F401,F403  (the names these had in one file)

# ---- moved from ai_video_studio.py, unchanged below ----


class WelcomeDialog(ctk.CTkToplevel):
    """First-run setup: the one thing a new user has to fetch is a footage key.

    Either service will do, so the window offers both and starts on Pixabay.
    """

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title(f"Welcome to {APP_NAME}")
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.skip)
        # One box per service, so switching doesn't lose what was typed.
        self.keys = {name: tk.StringVar(value=getattr(app, info["setting"]).get())
                     for name, info in KEY_PROVIDERS.items()}
        only_pexels = self.keys["Pexels"].get().strip() and not self.keys["Pixabay"].get().strip()
        self.var_provider = tk.StringVar(value="Pexels" if only_pexels else "Pixabay")

        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)

        ctk.CTkLabel(card, text=f"Welcome to {APP_NAME}", font=app.font_title,
                     text_color=TEXT, anchor="w").pack(fill="x", padx=20, pady=(18, 4))
        ctk.CTkLabel(
            card, font=app.font_body, text_color=TEXT_MUTED, justify="left",
            wraplength=440, anchor="w",
            text="The app finds stock footage for your script on Pixabay or Pexels. "
                 "Each gives every user a free key for that, with no payment details. "
                 "One key is enough - pick either.",
        ).pack(fill="x", padx=20, pady=(0, 12))
        FluentSegmented(card, app, list(KEY_PROVIDERS), self.var_provider, 220).pack(
            anchor="w", padx=20, pady=(0, 16))

        self._step(card, "1", "Get your free key")
        self.hint = ctk.CTkLabel(card, font=app.font_tiny, text_color=TEXT_DIM,
                                 justify="left", wraplength=410, anchor="w", height=30)
        self.hint.pack(fill="x", padx=(50, 20))
        self.open_button = ctk.CTkButton(
            card, width=210, height=32, corner_radius=4,
            font=app.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
            command=lambda: webbrowser.open(KEY_PROVIDERS[self.var_provider.get()]["page"]),
        )
        self.open_button.pack(anchor="w", padx=(50, 20), pady=(8, 16))

        self._step(card, "2", "Paste it here")
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=(50, 20), pady=(6, 0))
        self.entry = focus_accent(ctk.CTkEntry(
            row, width=300, height=32, corner_radius=4,
            font=app.font_body, fg_color=FIELD_BG, border_color=FIELD_BORDER,
            border_width=1, text_color=TEXT,
        ))
        self.entry.pack(side="left")
        self.test_button = ctk.CTkButton(
            row, text="Test key", width=92, height=32, corner_radius=4,
            font=app.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT, command=self.test,
        )
        self.test_button.pack(side="left", padx=(6, 0))
        self.result = ctk.CTkLabel(card, text="", font=app.font_tiny, text_color=TEXT_DIM,
                                   anchor="w", justify="left", wraplength=410)
        self.result.pack(fill="x", padx=(50, 20), pady=(6, 14))

        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.pack(fill="x", padx=20, pady=(0, 18))
        ctk.CTkButton(
            buttons, text="Save and continue", width=150, height=32, corner_radius=4,
            font=app.font_body, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color=ACCENT_TEXT, command=self.save,
        ).pack(side="right")
        ctk.CTkButton(
            buttons, text="Skip for now", width=110, height=32, corner_radius=4,
            font=app.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT, command=self.skip,
        ).pack(side="right", padx=(0, 8))

        self._trace = self.var_provider.trace_add("write", self._show_provider)
        self._show_provider()
        centre_on(self, app)

    def _show_provider(self, *_args):
        """Point step 1 and the key box at the chosen service."""
        if not self.winfo_exists():
            return
        name = self.var_provider.get()
        info = KEY_PROVIDERS[name]
        self.hint.configure(text=info["hint"])
        self.open_button.configure(text=info["button"])
        self.entry.configure(textvariable=self.keys[name])
        self.result.configure(text="")

    def _step(self, card, number, title):
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=20)
        ctk.CTkLabel(row, text=number, width=22, height=22, corner_radius=11,
                     font=self.app.font_caption, fg_color=ACCENT,
                     text_color=ACCENT_TEXT).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(row, text=title, font=self.app.font_bold, text_color=TEXT,
                     anchor="w").pack(side="left")

    def show_result(self, ok, message):
        if self.winfo_exists():
            self.test_button.configure(state="normal", text="Test key")
            self.result.configure(text=message, text_color=OK_COLOR if ok else ERR_COLOR)

    def test(self):
        self.test_button.configure(state="disabled", text="Testing...")
        self.result.configure(text="")
        name = self.var_provider.get()
        self.app.test_key(name, self.keys[name].get(), self.show_result)

    def save(self):
        name = self.var_provider.get()
        key = self.keys[name].get().strip()
        if not key:
            self.show_result(False, "Paste a key first, or choose Skip for now.")
            return
        self.app.welcome_seen = True
        # Saves the settings through the variable's trace.
        getattr(self.app, KEY_PROVIDERS[name]["setting"]).set(key)
        self.app.select_page("home")
        self.destroy()

    def skip(self):
        self.app.welcome_seen = True
        self.app._on_setting_changed()
        self.destroy()

class WhatsNewDialog(ctk.CTkToplevel):
    """After an update, once: what is new in this version, with "Don't show again"."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("What's new")
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)
        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        ctk.CTkLabel(card, text=f"What's new in {APP_VERSION}", font=app.font_title,
                     text_color=TEXT).pack(anchor="w", padx=18, pady=(16, 8))
        for line in WHATS_NEW:
            ctk.CTkLabel(card, text=f"•  {line}", font=app.font_body, text_color=TEXT_MUTED,
                         wraplength=440, justify="left", anchor="w").pack(fill="x", padx=18,
                                                                          pady=2)
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(12, 16))
        self.var_never = tk.BooleanVar(self, False)
        ctk.CTkCheckBox(row, text="Don't show again", variable=self.var_never,
                        font=app.font_tiny, text_color=TEXT_DIM, checkbox_width=16,
                        checkbox_height=16, border_width=1).pack(side="left")
        ctk.CTkButton(row, text="OK", width=96, height=32, corner_radius=4, font=app.font_body,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ACCENT_TEXT,
                      command=self.close).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self.close)
        centre_on(self, app)

    def close(self):
        self.app.whats_new_seen = APP_VERSION
        if self.var_never.get():
            self.app.whats_new_off = True
        save_settings(self.app._collect_settings())
        self.destroy()


class TourDialog(ctk.CTkToplevel):
    """First run only: four short screens on how the app is used.

    Never shown after an update (load_settings marks it seen when a settings
    file already exists). Settings > Help & about > Show the tour brings it back.
    """

    def __init__(self, app):
        super().__init__(app)
        self.app, self.step = app, 0
        self.title("Welcome to AI Video Studio")
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.finish)
        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        self.glyph = ctk.CTkLabel(card, text="", font=app.font_icon_lg, text_color=ACCENT)
        self.glyph.pack(pady=(22, 6))
        self.heading = ctk.CTkLabel(card, text="", font=app.font_title, text_color=TEXT)
        self.heading.pack()
        self.body = ctk.CTkLabel(card, text="", font=app.font_body, text_color=TEXT_MUTED,
                                 wraplength=400, justify="center", width=440, height=96)
        self.body.pack(padx=20, pady=(8, 4))
        self.count = ctk.CTkLabel(card, text="", font=app.font_tiny, text_color=TEXT_DIM)
        self.count.pack()
        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.pack(pady=(10, 18))
        ctk.CTkButton(buttons, text="Skip", width=96, height=34, corner_radius=4,
                      font=app.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                      border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                      command=self.finish).pack(side="left", padx=(0, 8))
        self.next_button = ctk.CTkButton(
            buttons, text="Next", width=130, height=34, corner_radius=4, font=app.font_bold,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ACCENT_TEXT, command=self.next)
        self.next_button.pack(side="left")
        self.show()
        centre_on(self, app)

    def show(self):
        glyph, heading, body = TOUR[self.step]
        self.glyph.configure(text=self.app.icon(glyph))
        self.heading.configure(text=heading)
        self.body.configure(text=body)
        self.count.configure(text=f"{self.step + 1} of {len(TOUR)}")
        self.next_button.configure(text="Got it" if self.step == len(TOUR) - 1 else "Next")

    def next(self):
        if self.step == len(TOUR) - 1:
            self.finish()
            return
        self.step += 1
        self.show()

    def finish(self):
        """Seen once is seen: remember it, then go on to the key setup if that is still due."""
        app = self.app
        app.tour_seen = True
        save_settings(app._collect_settings())
        self.destroy()
        if not app._has_footage_key() and not app.welcome_seen and not imagegen.installed():
            WelcomeDialog(app)


class SlowPicturesDialog(ctk.CTkToplevel):
    """Before a long render on a slow PC: how long, and quicker ways to do it."""

    def __init__(self, app, count):
        super().__init__(app)
        self.app = app
        self.title("This will take a while")
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)
        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        each = app.seconds_per_picture()
        measured = "measured on this PC" if app.ai_fast_seconds else "a guess until the first one"

        def minutes(seconds):
            return f"about {max(1, round(seconds / 60))} min"

        ctk.CTkLabel(card, text=f"{count} AI pictures: {minutes(each * count)}",
                     font=app.font_title, text_color=TEXT).pack(anchor="w", padx=18, pady=(16, 4))
        ctk.CTkLabel(card, text=f"This PC makes an AI picture in {minutes(each)} ({measured}). "
                                "The rest of the video takes a few minutes more. Closing other "
                                "busy programs (a game, many browser tabs) helps: in a test, a "
                                "busy PC took half as long again.",
                     font=app.font_body, text_color=TEXT_MUTED, wraplength=420, justify="left",
                     ).pack(anchor="w", padx=18, pady=(0, 12))

        def option(text, overrides):
            ctk.CTkButton(card, text=text, height=34, corner_radius=4, font=app.font_body,
                          fg_color=FIELD_BG, hover_color=CARD_HOVER, border_width=1,
                          border_color=FIELD_BORDER, text_color=TEXT, anchor="w",
                          command=lambda: self.choose(overrides)).pack(fill="x", padx=18, pady=3)

        option(f"Make all {count}  ({minutes(each * count)})", {})
        if count > SLOW_AI_SCENES:
            option(f"Make {SLOW_AI_SCENES} pictures and use them again for the other scenes  "
                   f"({minutes(each * SLOW_AI_SCENES)})", {"ai_reuse": SLOW_AI_SCENES})
        if app.ai_speed_now() != imagegen.FAST:
            fast = app.seconds_per_picture(imagegen.FAST)
            option(f"Use the Fast size  ({minutes(fast * count)})",
                   {"ai_images_speed": imagegen.FAST})
        ctk.CTkButton(card, text="Cancel", height=34, corner_radius=4, font=app.font_body,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ACCENT_TEXT,
                      command=self.destroy).pack(fill="x", padx=18, pady=(10, 16))
        centre_on(self, app)

    def choose(self, overrides):
        self.destroy()
        self.app.start_after_slow_choice(overrides)


class ClipQuestion(ctk.CTkToplevel):
    """Asked once, before a video, about the scenes where no stock clip looked right and no
    AI picture can stand in (vidgen.clipcheck): use the clip anyway, or the next one.

    ``items``: dicts with index, still (a path or None), visual and voice. ``answer``
    gets {index: "use" | "next"}, or None when the video is cancelled.
    """

    USE, NEXT = "Use this", "Try the next clip"

    def __init__(self, app, items, answer, cancel):
        from PIL import Image

        super().__init__(app)
        self.app, self.answer, self.cancel = app, answer, cancel
        self.title("Some clips may not fit")
        self.configure(fg_color=MAIN_BG)
        self.transient(app)
        self._images, self.picks = [], {}
        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        ctk.CTkLabel(card, text="These clips may not show what the scene says",
                     font=app.font_title, text_color=TEXT).pack(anchor="w", padx=18, pady=(16, 4))
        ctk.CTkLabel(card, text="Pick what each scene uses, then the video is made.",
                     font=app.font_body, text_color=TEXT_MUTED).pack(anchor="w", padx=18,
                                                                    pady=(0, 8))
        rows = ctk.CTkScrollableFrame(card, fg_color="transparent", width=560,
                                      height=min(120 * len(items), 420))
        rows.pack(fill="both", expand=True, padx=8)
        for item in items:
            row = ctk.CTkFrame(rows, fg_color="transparent")
            row.pack(fill="x", pady=6)
            still = None
            if item.get("still"):
                try:
                    with Image.open(item["still"]) as picture:
                        still = ctk.CTkImage(light_image=picture.convert("RGB").copy(),
                                             size=(176, 99))
                except OSError:
                    still = None
            if still is not None:
                self._images.append(still)
            ctk.CTkLabel(row, text="" if still else "No picture", image=still, width=176,
                         height=99, fg_color=FIELD_BG, text_color=TEXT_MUTED,
                         ).pack(side="left", padx=(0, 12))
            words = ctk.CTkFrame(row, fg_color="transparent")
            words.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(words, text=f"Scene {item['index'] + 1}: {item['visual']}",
                         font=app.font_body, text_color=TEXT, anchor="w").pack(anchor="w")
            ctk.CTkLabel(words, text=ellipsize(item.get("voice") or "", 90),
                         font=app.font_body, text_color=TEXT_MUTED, anchor="w",
                         wraplength=340, justify="left").pack(anchor="w", pady=(2, 6))
            pick = ctk.CTkSegmentedButton(words, values=[self.USE, self.NEXT],
                                          font=app.font_body)
            pick.set(self.USE)
            pick.pack(anchor="w")
            self.picks[item["index"]] = pick
        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.pack(fill="x", padx=18, pady=(10, 16))
        ctk.CTkButton(buttons, text="Make the video", height=34, corner_radius=4,
                      font=app.font_body, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      text_color=ACCENT_TEXT, command=self.done).pack(side="right")
        ctk.CTkButton(buttons, text="Cancel video", height=34, corner_radius=4,
                      font=app.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                      border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                      command=self.stop).pack(side="right", padx=(0, 8))
        self.protocol("WM_DELETE_WINDOW", self.stop)
        centre_on(self, app)
        self.after(300, self._watch)

    def _watch(self):
        """Cancel pressed in the main window: the question goes away with the video."""
        if self.answer is None:
            return
        if self.cancel.is_set():
            self.stop()
        else:
            self.after(300, self._watch)

    def _close(self, result):
        answer, self.answer = self.answer, None   # answered once, however it closes
        if answer is not None:
            self.destroy()
            answer(result)

    def done(self):
        self._close({i: ("next" if pick.get() == self.NEXT else "use")
                     for i, pick in self.picks.items()})

    def stop(self):
        self._close(None)


class BatchDialog(ctk.CTkToplevel):
    """Make several: one text per block, each becomes its own video and project."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Make several videos")
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)
        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        template = app.var_template.get()
        ctk.CTkLabel(card, text="Make several videos", font=app.font_title, text_color=TEXT,
                     ).pack(anchor="w", padx=18, pady=(16, 2))
        ctk.CTkLabel(card, text="Paste one text or topic for each video, with an empty line "
                                "between them. They are made one after another, each as its "
                                "own project, with the " + (f"{template} template."
                                                           if template != NO_TEMPLATE else
                                                           "answers New from text last used."),
                     font=app.font_tiny, text_color=TEXT_DIM, wraplength=560, justify="left",
                     ).pack(anchor="w", padx=18, pady=(0, 8))
        self.box = ctk.CTkTextbox(card, width=560, height=260, font=app.font_body,
                                  corner_radius=4, fg_color=FIELD_BG, text_color=TEXT,
                                  border_width=0, wrap="word")
        self.box.pack(padx=18)
        self.box.bind("<KeyRelease>", self._count)
        self.status = ctk.CTkLabel(card, text="", font=app.font_tiny, text_color=TEXT_DIM,
                                   anchor="w")
        self.status.pack(fill="x", padx=18, pady=(6, 8))
        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.pack(fill="x", padx=18, pady=(0, 16))
        ctk.CTkButton(buttons, text="Start", width=110, height=34, corner_radius=4,
                      font=app.font_bold, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      text_color=ACCENT_TEXT, command=self.start).pack(side="right")
        ctk.CTkButton(buttons, text="Cancel", width=96, height=34, corner_radius=4,
                      font=app.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                      border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                      command=self.destroy).pack(side="right", padx=(0, 8))
        centre_on(self, app)
        self.box.focus_set()

    def texts(self):
        return draft.batch_texts(self.box.get("1.0", "end"))

    def _count(self, _event=None):
        n = len(self.texts())
        self.status.configure(text=f"{n} video{'s' if n != 1 else ''}" + (
            f" (only the first {MAX_BATCH} are made)" if n > MAX_BATCH else ""),
            text_color=TEXT_DIM)

    def start(self):
        texts = self.texts()
        if not texts:
            self.status.configure(text="Paste some text first.", text_color=ERR_COLOR)
            return
        self.destroy()
        self.app.start_batch(texts[:MAX_BATCH])


class MyVoiceDialog(ctk.CTkToplevel):
    """Make my voice (beta): record six sentences; KittenTTS 2 in the voice pack then speaks
    scripts in a voice like yours (vidgen.myvoice, vidgen.voicepack)."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Make my voice (beta)")
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.recording = False
        self.stopped = False   # closing the window stops the pack install (what is done is kept)
        self.path = os.path.join(myvoice.MY_FOLDER, myvoice.RECORDING)
        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        ctk.CTkLabel(card, text="Make my voice (beta)", font=app.font_title, text_color=TEXT,
                     ).pack(anchor="w", padx=18, pady=(16, 2))
        ctk.CTkLabel(card, text="Press Start, read these six sentences out loud at your normal "
                                "speed, then press Stop. A quiet room and a headset microphone "
                                "work best. The recording and the voice made from it stay on "
                                "this PC.", font=app.font_tiny, text_color=TEXT_DIM,
                     wraplength=520, justify="left").pack(anchor="w", padx=18, pady=(0, 8))
        for n, line in enumerate(myvoice.SENTENCES, 1):
            ctk.CTkLabel(card, text=f"{n}.  {line}", font=app.font_body, text_color=TEXT,
                         wraplength=520, justify="left", anchor="w").pack(fill="x", padx=24,
                                                                         pady=1)
        self.status = ctk.CTkLabel(card, text="" if myvoice.installed() else
                                   f"The first time, the voice pack is installed (about "
                                   f"{myvoice.SIZE / 1e9:.0f} GB: PyTorch for your graphics card "
                                   "and the voice model). It can be stopped and carried on "
                                   "later.", font=app.font_tiny,
                                   text_color=TEXT_DIM, wraplength=520, justify="left",
                                   anchor="w")
        self.status.pack(fill="x", padx=18, pady=(10, 6))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(0, 16))

        def button(text, command, accent=False):
            widget = ctk.CTkButton(row, text=text, height=34, corner_radius=4, font=app.font_body,
                                   fg_color=ACCENT if accent else FIELD_BG,
                                   hover_color=ACCENT_HOVER if accent else CARD_HOVER,
                                   text_color=ACCENT_TEXT if accent else TEXT,
                                   text_color_disabled=ACCENT_TEXT if accent else TEXT_DIM,
                                   border_width=0 if accent else 1, border_color=FIELD_BORDER,
                                   command=command)
            widget.pack(side="left", padx=(0, 6))
            return widget

        self.record_button = button("Start recording", self.toggle)
        self.play_button = button("Play it back", self.play)
        self.make_button = button("Make my voice", self.make, accent=True)
        if myvoice.installed():
            button("Remove voice pack", self.remove_pack)
        if not os.path.isfile(self.path):
            self.play_button.configure(state="disabled")
            self.make_button.configure(state="disabled")
        centre_on(self, app)

    def toggle(self):
        try:
            if not self.recording:
                os.makedirs(myvoice.MY_FOLDER, exist_ok=True)
                mci("open new type waveaudio alias myvoice")
                mci("set myvoice bitspersample 16 channels 1 samplespersec 22050 "
                    "bytespersec 44100 alignment 2")
                mci("record myvoice")
                self.recording = True
                self.record_button.configure(text="Stop recording")
                for b in (self.play_button, self.make_button):
                    b.configure(state="disabled")
                self.status.configure(text="Recording... read the six sentences.",
                                      text_color=ERR_COLOR)
            else:
                mci("stop myvoice")
                saved = os.path.join(short_path(myvoice.MY_FOLDER), "rec.wav")
                mci(f'save myvoice "{saved}"')
                mci("close myvoice")
                os.replace(saved, self.path)
                self.recording = False
                self.record_button.configure(text="Record again")
                for b in (self.play_button, self.make_button):
                    b.configure(state="normal")
                self.status.configure(text="Recorded. Play it back if you like, then press "
                                           "Make my voice.", text_color=TEXT_DIM)
        except OSError as exc:
            self.recording = False
            try:
                mci("close myvoice")
            except OSError:
                pass
            self.status.configure(text=f"The microphone could not be used: {exc}",
                                  text_color=ERR_COLOR)

    def play(self):
        import winsound

        winsound.PlaySound(self.path, winsound.SND_FILENAME | winsound.SND_ASYNC)

    def make(self):
        self.make_button.configure(state="disabled", text="Making it...")
        self.record_button.configure(state="disabled")
        app = self.app

        def worker():
            try:
                if not myvoice.installed():
                    def progress(text, fraction):
                        app._queue.put(("myvoice", {"dialog": self,
                                                    "progress": (text, fraction)}))
                    myvoice.download(progress, should_stop=lambda: self.stopped)
                app._queue.put(("myvoice", {"dialog": self, "progress": (
                    "Learning your voice and saying a test line (about a minute)...", 1.0)}))
                myvoice.make(self.path)
                app._queue.put(("myvoice", {"dialog": self, "done": True}))
            except Exception as exc:  # noqa: BLE001 - shown in the window
                app._queue.put(("myvoice", {"dialog": self, "error": str(exc)}))

        threading.Thread(target=worker, daemon=True).start()

    def on_event(self, payload):
        if not self.winfo_exists():
            return
        if "progress" in payload:
            text, fraction = payload["progress"]
            self.status.configure(text=f"{text} ({fraction * 100:.0f}%)" if fraction < 1 else text,
                                  text_color=TEXT_DIM)
            return
        self.make_button.configure(state="normal", text="Make my voice")
        self.record_button.configure(state="normal")
        if payload.get("error"):
            self.status.configure(text=payload["error"], text_color=ERR_COLOR)
            return
        self.app.my_voice_made()
        self.status.configure(text="Done: this is how it sounds. \"My voice (beta)\" is now in "
                                   "the voice list and chosen.", text_color=OK_COLOR)
        sample = os.path.join(myvoice.MY_FOLDER, myvoice.SAMPLE)
        if os.path.isfile(sample):
            import winsound

            winsound.PlaySound(sample, winsound.SND_FILENAME | winsound.SND_ASYNC)

    def remove_pack(self):
        """Delete the voice pack (about 7 GB); My voice falls back to the normal voice."""
        myvoice.close_speaker()
        voicepack.remove()
        self.app.my_voice_made()   # refreshes the voice lists: My voice drops out
        if self.app.var_voice.get() == voices.persona(voices.MY_VOICE).label:
            self.app.var_voice.set(voices.persona(voices.DEFAULT_ID).label)
        self.status.configure(text="The voice pack was removed. Your recording is kept.",
                              text_color=TEXT_DIM)

    def close(self):
        self.stopped = True
        if self.recording:
            try:
                mci("close myvoice")
            except OSError:
                pass
        self.destroy()


class TranslateDialog(ctk.CTkToplevel):
    """The same video in another language: the Smart writer translates the lines into a copy."""

    def __init__(self, app, project):
        super().__init__(app)
        self.app, self.project = app, project
        self.title("Make it in another language")
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)
        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        ctk.CTkLabel(card, text=f"\"{project.get('title')}\" in another language",
                     font=app.font_title, text_color=TEXT, wraplength=420, justify="left",
                     ).pack(anchor="w", padx=18, pady=(16, 8))
        self.var_language = tk.StringVar(self, voices.LANGUAGES[0])
        ctk.CTkOptionMenu(
            card, values=list(voices.LANGUAGES), variable=self.var_language, width=260,
            height=32, corner_radius=4, font=app.font_body, fg_color=FIELD_BG,
            button_color=FIELD_BG, button_hover_color=CARD_HOVER, text_color=TEXT,
            dropdown_fg_color=CARD_BG, dropdown_text_color=TEXT,
            dropdown_hover_color=CARD_HOVER, dropdown_font=app.font_body,
            dynamic_resizing=False).pack(anchor="w", padx=18)
        ctk.CTkLabel(card, text="The lines are translated by the Smart writer, a small AI on "
                                "this PC. Its translations are understandable but have "
                                "mistakes, so the copy opens in the Storyboard first: check the "
                                "lines there (or have someone who speaks the language read "
                                "them) before you make the video. Pictures, clips and cards "
                                "stay the same; the voice and captions change.",
                     font=app.font_tiny, text_color=TEXT_DIM, wraplength=420, justify="left",
                     ).pack(anchor="w", padx=18, pady=(10, 8))
        self.status = ctk.CTkLabel(card, text="", font=app.font_tiny, text_color=TEXT_DIM,
                                   wraplength=420, justify="left", anchor="w")
        self.status.pack(fill="x", padx=18)
        self.go_button = ctk.CTkButton(
            card, text="Translate and open in the Storyboard", height=34, corner_radius=4,
            font=app.font_body, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color=ACCENT_TEXT, command=self.go)
        self.go_button.pack(fill="x", padx=18, pady=(8, 3))
        ctk.CTkButton(card, text="Cancel", height=34, corner_radius=4, font=app.font_body,
                      fg_color=FIELD_BG, hover_color=CARD_HOVER, border_width=1,
                      border_color=FIELD_BORDER, text_color=TEXT,
                      command=self.destroy).pack(fill="x", padx=18, pady=(3, 16))
        centre_on(self, app)

    def go(self):
        ok, why = writer.ready()
        if not ok:
            self.status.configure(text=f"Translating needs the Smart writer. {why}",
                                  text_color=ERR_COLOR)
            return
        try:
            lines = [scene.voice for scene in parse_script(self.project["script"]) if scene.voice]
        except ScriptError as exc:
            self.status.configure(text=str(exc), text_color=ERR_COLOR)
            return
        language, gpu = self.var_language.get(), bool(self.app.var_writer_gpu.get())
        self.go_button.configure(state="disabled", text="Translating...")
        self.status.configure(text=f"The Smart writer is translating {len(lines)} lines into "
                                   f"{language}...", text_color=TEXT_DIM)

        def worker():
            try:
                with writer.Session(gpu=gpu) as session:
                    found = session.translate(lines, language)
                self.app._queue.put(("translated", {"dialog": self, "lines": found,
                                                    "language": language}))
            except Exception as exc:  # noqa: BLE001 - shown in the window
                self.app._queue.put(("translated", {"dialog": self, "error": str(exc),
                                                    "language": language}))

        threading.Thread(target=worker, daemon=True).start()

    def done(self, payload):
        """UI thread: the translation came back."""
        if not self.winfo_exists():
            return
        if payload.get("error"):
            self.go_button.configure(state="normal", text="Translate and open in the Storyboard")
            self.status.configure(text=payload["error"], text_color=ERR_COLOR)
            return
        language, app, project = payload["language"], self.app, self.project
        script = boards.with_lines(project["script"], payload["lines"])
        new = projects.copy(project["id"])
        data = projects.load(new)
        projects.save(new, dict(data, script=script, language=language,
                                title=f"{project.get('title') or 'Untitled video'} ({language})"))
        was = voices.persona((project.get("settings") or {}).get("voice") or app.var_voice.get())
        self.destroy()
        if app.open_project(new, {"voice": voices.for_language(language, was).id}):
            app.board_status.configure(
                text=f"Translated into {language} by the Smart writer. Read the lines and fix "
                     "any mistakes, then press Make video.", text_color=WARN_COLOR)


class CopyDialog(ctk.CTkToplevel):
    """Make a copy of a project in the other shape or with another voice."""

    def __init__(self, app, project):
        super().__init__(app)
        self.app, self.project = app, project
        self.title("Make a copy")
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)
        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        settings = project.get("settings") or {}
        self.shape = settings.get("aspect") if settings.get("aspect") in RATIO_OPTIONS \
            else RATIO_OPTIONS[0]
        try:
            self.pictures = sum(s.is_ai for s in parse_script(project["script"]))
        except ScriptError:
            self.pictures = 0

        ctk.CTkLabel(card, text=f"Copy of \"{project.get('title')}\"", font=app.font_title,
                     text_color=TEXT, wraplength=420, justify="left",
                     ).pack(anchor="w", padx=18, pady=(16, 10))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=4)
        ctk.CTkLabel(row, text="Shape", font=app.font_body, text_color=TEXT, width=60,
                     anchor="w").pack(side="left")
        self.var_shape = tk.StringVar(self, self.shape)
        FluentSegmented(row, app, list(RATIO_OPTIONS), self.var_shape, 200).pack(side="left")
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=4)
        ctk.CTkLabel(row, text="Voice", font=app.font_body, text_color=TEXT, width=60,
                     anchor="w").pack(side="left")
        self.var_voice = tk.StringVar(
            self, voices.persona(settings.get("voice") or app.var_voice.get()).label)
        ctk.CTkOptionMenu(
            row, values=VOICE_OPTIONS, variable=self.var_voice, width=340, height=32,
            corner_radius=4, font=app.font_body, fg_color=FIELD_BG, button_color=FIELD_BG,
            button_hover_color=CARD_HOVER, text_color=TEXT, dropdown_fg_color=CARD_BG,
            dropdown_text_color=TEXT, dropdown_hover_color=CARD_HOVER,
            dropdown_font=app.font_body, dynamic_resizing=False).pack(side="left")
        self.note = ctk.CTkLabel(card, text="", font=app.font_tiny, text_color=TEXT_DIM,
                                 wraplength=420, justify="left", anchor="w")
        self.note.pack(fill="x", padx=18, pady=(8, 10))
        self.var_shape.trace_add("write", self._note)
        self._note()

        def button(text, command, accent=False):
            ctk.CTkButton(card, text=text, height=34, corner_radius=4, font=app.font_body,
                          fg_color=ACCENT if accent else FIELD_BG,
                          hover_color=ACCENT_HOVER if accent else CARD_HOVER,
                          text_color=ACCENT_TEXT if accent else TEXT,
                          border_width=0 if accent else 1, border_color=FIELD_BORDER,
                          command=command).pack(fill="x", padx=18, pady=3)

        button("Make the video", lambda: self.go(render=True), accent=True)
        button("Open the copy in the Storyboard", lambda: self.go(render=False))
        button("Cancel", self.destroy)
        ctk.CTkFrame(card, fg_color="transparent", height=10).pack()
        centre_on(self, app)

    def _note(self, *_args):
        if self.pictures and self.var_shape.get() != self.shape:
            each = self.app.seconds_per_picture()
            self.note.configure(text=f"A new shape needs new AI pictures: {self.pictures} to "
                                     f"make, about {max(1, round(each * self.pictures / 60))} "
                                     "min. Stock clips are searched again.")
        else:
            self.note.configure(text="The original is kept as it is." + (
                " Its AI pictures are reused, so the copy is quick to make."
                if self.pictures and self.var_shape.get() == self.shape else ""))

    def go(self, render):
        new = projects.copy(self.project["id"])
        changes = {"aspect": self.var_shape.get(),
                   "voice": voices.persona(self.var_voice.get()).id}
        self.destroy()
        if self.app.open_project(new, changes) and render:
            self.app.start_render()


class ResultDialog(ctk.CTkToplevel):
    """Render complete: the video, playable in the window, with its sound.

    Tk has no video widget, so vidgen.preview decodes small frames and this
    window shows them on a timer while winsound plays the soundtrack. Play
    and stop only - the default player is one button away for anything more.
    """

    def __init__(self, app, title, message, paths, names=None):
        super().__init__(app)
        self.app = app
        self.paths = list(paths)
        self.path = self.paths[0]
        self.title(title)
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.reader = None
        self.job = None
        self.wav = None
        self.playing = False
        self.poster_photo = None
        self.photo = None

        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)

        size = self._load_poster()
        portrait = size is None or size[1] >= size[0]
        info = ctk.CTkFrame(card, fg_color="transparent")
        if size is not None:
            # A plain Tk label: frames are raw pixels, already scaled for the display.
            self.screen = tk.Label(card, image=self.poster_photo, bd=0, bg="#000000",
                                   width=size[0], height=size[1])
            if portrait:
                self.screen.pack(side="left", padx=(16, 0), pady=16)
                info.pack(side="left", fill="both", expand=True)
            else:
                self.screen.pack(padx=16, pady=(16, 0))
                info.pack(fill="both", expand=True)
        else:
            info.pack(fill="both", expand=True)

        header = ctk.CTkFrame(info, fg_color="transparent")
        header.pack(fill="x", padx=18, pady=(16, 4))
        ctk.CTkLabel(header, text=app.icon("status"), font=app.font_icon_lg,
                     text_color=OK_COLOR).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(header, text=title, font=app.font_title,
                     text_color=TEXT).pack(side="left")
        if len(self.paths) > 1:
            # Both versions, side by side in time: switch, play, compare.
            self.version_names = (list(names) if names and len(names) == len(self.paths) else
                                  [f"Version {n}" for n in range(1, len(self.paths) + 1)])
            self.var_version = tk.StringVar(value=self.version_names[0])
            FluentSegmented(info, app, self.version_names, self.var_version,
                            110 * len(self.paths)).pack(anchor="w", padx=18, pady=(6, 6))
            self.var_version.trace_add("write", self._select_version)
        ctk.CTkLabel(info, text=message, font=app.font_body, text_color=TEXT_MUTED,
                     justify="left", wraplength=300 if portrait else 440,
                     anchor="w").pack(fill="x", padx=18, pady=(2, 14))

        def button(text, command, accent=False):
            widget = ctk.CTkButton(
                info, text=text, height=34, corner_radius=4, font=app.font_body,
                fg_color=ACCENT if accent else FIELD_BG,
                hover_color=ACCENT_HOVER if accent else CARD_HOVER,
                text_color=ACCENT_TEXT if accent else TEXT,
                border_width=0 if accent else 1, border_color=FIELD_BORDER, command=command,
            )
            widget.pack(fill="x", padx=18, pady=(0, 6))
            return widget

        self.play_button = None
        if size is not None:
            self.play_button = button(self._play_label(), self.toggle, accent=True)
        button("Open in player", self.open_in_player)
        button("Show in folder", self.show_in_folder)
        if os.path.isfile(self._thumb()):
            button("Open thumbnail", lambda: os.startfile(self._thumb()))
        if app.project and projects.load(app.project):
            button("Make it in another language",
                   lambda: (self.close(), TranslateDialog(app, projects.load(app.project))))

        self.upload_box = ctk.CTkFrame(info, fg_color="transparent")
        self.upload_box.pack(fill="x", padx=18, pady=(4, 2))
        ctk.CTkLabel(self.upload_box, text="Title, description and tags for posting it",
                     font=app.font_tiny, text_color=TEXT_MUTED, anchor="w").pack(fill="x")
        self.upload_note = ctk.CTkLabel(self.upload_box, text="Writing them...",
                                        font=app.font_tiny, text_color=TEXT_DIM, anchor="w")
        self.upload_note.pack(fill="x")
        self.upload_fields = {}
        for key, height in (("title", 1), ("description", 3), ("tags", 2)):
            row = ctk.CTkFrame(self.upload_box, fg_color="transparent")
            box = ctk.CTkTextbox(row, height=24 + 18 * (height - 1), font=app.font_tiny,
                                 corner_radius=4, fg_color=FIELD_BG, text_color=TEXT,
                                 border_width=0, wrap="word")
            box.pack(side="left", fill="x", expand=True)
            ctk.CTkButton(row, text="Copy", width=54, height=24, corner_radius=4,
                          font=app.font_tiny, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                          border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                          command=lambda b=box: self._copy(b)).pack(side="left", padx=(6, 0))
            self.upload_fields[key] = (row, box)
        if app.last_upload:
            self.set_upload(app.last_upload)

        ctk.CTkLabel(info, text="Want something different? Say what to change.",
                     font=app.font_tiny, text_color=TEXT_MUTED, anchor="w",
                     ).pack(fill="x", padx=18, pady=(6, 2))
        self.comment = focus_accent(ctk.CTkEntry(
            info, height=32, corner_radius=4, font=app.font_body, fg_color=FIELD_BG,
            border_color=FIELD_BORDER, text_color=TEXT,
            placeholder_text="e.g. make it shorter, or: more exciting start"))
        self.comment.pack(fill="x", padx=18, pady=(0, 6))
        self.comment.bind("<Return>", lambda _event: self.change_it())
        button("Change it", self.change_it)

        if app.key_tip and not app._has_footage_key():
            # A reminder, not a wall: the video above is complete either way.
            tip = ctk.CTkFrame(info, fg_color="transparent")
            tip.pack(fill="x", padx=18, pady=(4, 6))
            ctk.CTkLabel(tip, text="Tip: with a free Pexels or Pixabay key the app can also "
                                   "use real video clips.", font=app.font_tiny,
                         text_color=TEXT_DIM, anchor="w", justify="left",
                         wraplength=300 if portrait else 440).pack(anchor="w")
            links = ctk.CTkFrame(tip, fg_color="transparent")
            links.pack(anchor="w", pady=(2, 0))
            ctk.CTkButton(links, text="Get a free key", width=110, height=26, corner_radius=4,
                          font=app.font_tiny, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                          border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                          command=lambda: (self.close(), WelcomeDialog(app))).pack(side="left")
            self.var_no_tip = tk.BooleanVar(self, False)
            ctk.CTkCheckBox(links, text="Don't show again", variable=self.var_no_tip,
                            font=app.font_tiny, text_color=TEXT_DIM, checkbox_width=16,
                            checkbox_height=16, border_width=1,
                            command=lambda: app.set_key_tip(not self.var_no_tip.get()),
                            ).pack(side="left", padx=(12, 0))

        button("Close", self.close).pack_configure(pady=(0, 16))

        centre_on(self, app)

    def set_upload(self, upload):
        """Show the title, description and tags (written after the render)."""
        if not self.winfo_exists():
            return
        self.upload_note.pack_forget()
        for key, (row, box) in self.upload_fields.items():
            value = upload[key] if key != "tags" else ", ".join(upload[key])
            box.delete("1.0", "end")
            box.insert("1.0", value)
            row.pack(fill="x", pady=2)

    def _copy(self, box):
        self.clipboard_clear()
        self.clipboard_append(box.get("1.0", "end").strip())

    def _thumb(self):
        return os.path.splitext(self.path)[0] + ".thumbnail.jpg"

    def change_it(self):
        """Hand the comment to the Storyboard, which shows what changed before any render."""
        comment = self.comment.get().strip()
        if comment:
            self.close()
            self.app.select_page("board")
            self.app.revise_script(comment)

    # -- picture ---------------------------------------------------------------
    def _load_poster(self):
        """Read the video's size and a still frame. None if it can't be previewed."""
        try:
            from PIL import ImageTk

            width, height, self.length = preview.probe(self.path)
            # Real pixels: follow the display scaling, but never outgrow the screen.
            scale = min(ctk.ScalingTracker.get_widget_scaling(self.app),
                        self.winfo_screenheight() * 0.6 / preview.PORTRAIT_BOX[1])
            self.size = preview.preview_size((width, height), max(scale, 0.5))
            image = preview.poster(self.path, self.size, at=min(1.0, self.length / 2))
            self.poster_photo = ImageTk.PhotoImage(image, master=self)
            return self.size
        except Exception as exc:  # noqa: BLE001 - the video is saved either way
            self.app.append_log(f"⚠️ Couldn't show the preview ({exc}).")
            return None

    def _select_version(self, *_args):
        """Point the preview and the buttons at the chosen version's file."""
        if not self.winfo_exists():
            return
        self.stop()
        self.path = self.paths[self.version_names.index(self.var_version.get())]
        self._forget_sound()
        if self.play_button is not None and self._load_poster() is not None:
            self.screen.configure(image=self.poster_photo)

    def _forget_sound(self):
        if self.wav:
            try:
                os.remove(self.wav)
            except OSError:
                pass
            self.wav = None

    def _play_label(self):
        glyph = self.app.icon("stop" if self.playing else "play")
        return f"{glyph}   {'Stop' if self.playing else 'Play'}"

    # -- playback --------------------------------------------------------------
    def toggle(self):
        if self.playing:
            self.stop()
        else:
            self.play()

    def play(self):
        self.stop()
        try:
            if self.wav is None and sys.platform == "win32":
                os.makedirs(PREVIEW_DIR, exist_ok=True)
                handle, wav = tempfile.mkstemp(suffix=".wav", prefix="result_", dir=PREVIEW_DIR)
                os.close(handle)
                try:
                    self.wav = preview.extract_audio(self.path, wav)
                except Exception:  # noqa: BLE001 - a silent video still plays
                    os.remove(wav)
            self.reader = preview.FrameReader(self.path, self.size)
            first = self.reader.read()   # waits until ffmpeg is really decoding
        except Exception as exc:  # noqa: BLE001
            self.app.append_log(f"⚠️ Couldn't play the preview ({exc}).")
            self.stop()
            return
        if first is None:
            self.stop()
            return
        self.playing = True
        self.play_button.configure(text=self._play_label())
        self._show(first)
        # Sound and clock start together, after the first frame is on screen.
        if self.wav and sys.platform == "win32":
            import winsound

            winsound.PlaySound(self.wav, winsound.SND_FILENAME | winsound.SND_ASYNC
                               | winsound.SND_NODEFAULT)
        self.started = time.perf_counter()
        self.job = self.after(10, self._tick)

    def _tick(self):
        self.job = None
        if not self.playing:
            return
        wanted = int((time.perf_counter() - self.started) * preview.FPS)
        if wanted >= self.reader.index:
            # Skips frames when the window is behind, so picture follows sound.
            frame = self.reader.skip_to(wanted)
            if frame is None:
                self.stop(silence=False)   # let the last moment of sound finish
                return
            self._show(frame)
        self.job = self.after(10, self._tick)

    def _show(self, frame):
        from PIL import Image, ImageTk

        self.photo = ImageTk.PhotoImage(Image.frombytes("RGB", self.size, frame), master=self)
        self.screen.configure(image=self.photo)

    def stop(self, silence=True):
        self.playing = False
        if self.job:
            try:
                self.after_cancel(self.job)
            except Exception:
                pass
            self.job = None
        if self.reader:
            self.reader.close()
            self.reader = None
        if silence and sys.platform == "win32":
            import winsound

            winsound.PlaySound(None, winsound.SND_PURGE)
        if self.winfo_exists() and self.play_button is not None:
            self.play_button.configure(text=self._play_label())
            self.screen.configure(image=self.poster_photo)

    # -- buttons ---------------------------------------------------------------
    def open_in_player(self):
        self.stop()
        try:
            if sys.platform == "win32":
                os.startfile(self.path)  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", self.path])
        except OSError as exc:
            self.app.append_log(f"⚠️ Couldn't open the video ({exc}).")

    def show_in_folder(self):
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self.path)])
        else:
            self.app._open_folder(os.path.dirname(self.path))

    def close(self):
        self.stop()
        self._forget_sound()
        self.destroy()


class ReportDialog(ctk.CTkToplevel):
    """A problem report the user reads, edits and sends themselves.

    The app sends nothing: the buttons open a prefilled GitHub issue in the
    browser, or copy the text.
    """

    def __init__(self, app, report, error=""):
        super().__init__(app)
        self.app = app
        self.error = error
        self.title("Report a problem")
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)

        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        ctk.CTkLabel(card, text="Report a problem", font=app.font_title,
                     text_color=TEXT, anchor="w").pack(fill="x", padx=20, pady=(18, 4))
        ctk.CTkLabel(
            card, font=app.font_body, text_color=TEXT_MUTED, justify="left",
            wraplength=580, anchor="w",
            text="This is exactly what will be shared. Nothing is sent until you press a "
                 "button. API keys, your Windows user name and the spoken lines of your "
                 "script have been removed; search words are kept. You can edit the text.",
        ).pack(fill="x", padx=20, pady=(0, 10))

        self.box = ctk.CTkTextbox(card, width=600, height=320, font=app.font_mono_sm,
                                  corner_radius=4, fg_color=FIELD_BG, text_color=TEXT,
                                  border_width=0, wrap="word")
        self.box.pack(padx=20)
        self.box.insert("1.0", report)

        self.status = ctk.CTkLabel(card, text="", font=app.font_tiny, text_color=TEXT_DIM,
                                   anchor="w", justify="left", wraplength=580)
        self.status.pack(fill="x", padx=20, pady=(8, 8))

        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.pack(fill="x", padx=20, pady=(0, 18))
        ctk.CTkButton(
            buttons, text="Open GitHub issue", width=150, height=32, corner_radius=4,
            font=app.font_body, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color=ACCENT_TEXT, command=self.open_issue,
        ).pack(side="right")
        for text, command in (("Copy to clipboard", self.copy), ("Close", self.destroy)):
            ctk.CTkButton(
                buttons, text=text, width=130, height=32, corner_radius=4,
                font=app.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                border_width=1, border_color=FIELD_BORDER, text_color=TEXT, command=command,
            ).pack(side="right", padx=(0, 8))

        centre_on(self, app)

    def text(self) -> str:
        return self.box.get("1.0", "end").strip()

    def copy(self, quiet=False):
        self.app.clipboard_clear()
        self.app.clipboard_append(self.text())
        if not quiet:
            self.status.configure(text="Copied. Paste it into an email or a message.")

    def open_issue(self):
        self.copy(quiet=True)   # the fallback if the link has to leave the log out
        first = (self.error or "").strip().splitlines()[0:1]
        title = f"Render failed: {first[0][:80]}" if first else "Problem report"
        webbrowser.open(diagnostics.issue_url(self.app.scrub(title), self.text()))
        self.status.configure(
            text="GitHub opened in your browser. Press \"Submit new issue\" there to send "
                 "it (you need a GitHub account). The report is also on your clipboard.")
