"""Photos: the Image editor, the photo list and New from text (DraftDialog). (Split out of ai_video_studio.py; that file still exports every name.)"""

from __future__ import annotations

from gui.base import *  # noqa: F401,F403  (the names these had in one file)

# ---- moved from ai_video_studio.py, unchanged below ----


class ImageEditor(ctk.CTkFrame):
    """Change a photo with words (imagegen.redraw): the photo and the try side by side, How
    much, Change it, Try again, and a row of the tries. ``on_use(path)``: what the button
    under the tries does with the one shown (Image editor page: none, then Save and Use in a
    new video; a reference photo or a thumbnail layer: Use this)."""

    VIEW = (440, 380)   # the smallest a picture is shown; it grows with its box

    def __init__(self, master, app, photo="", about="", on_use=None, use_text="Use this"):
        super().__init__(master, fg_color="transparent")
        self.app, self.on_use = app, on_use
        self.photo, self.about, self.detail = "", "", {}
        self.tries, self.shown, self.busy = [], None, False
        self.stop = threading.Event()
        self.grid_columnconfigure((0, 1), weight=1, uniform="views")

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self._button(top, "Open photo...", self.open_photo).pack(side="left")
        self.name = ctk.CTkLabel(top, text="", font=app.font_tiny, text_color=TEXT_DIM,
                                 anchor="w")
        self.name.pack(side="left", padx=10)
        self.before_view = self._view(0, "Your photo")
        self.after_view = self._view(1, "Changed")

        ask = ctk.CTkFrame(self, fg_color="transparent")
        ask.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ask.grid_columnconfigure(0, weight=1)
        self.change = focus_accent(ctk.CTkEntry(
            ask, height=34, corner_radius=4, font=app.font_body, fg_color=FIELD_BG,
            border_color=FIELD_BORDER, text_color=TEXT,
            placeholder_text="What should change? For example: make it a chocolate cake"))
        self.change.grid(row=0, column=0, sticky="ew")
        self.change.bind("<Return>", lambda _e: self.run())
        self.amount = tk.StringVar(self, imagegen.SOME)
        FluentSegmented(ask, app, list(imagegen.AMOUNTS), self.amount, 240).grid(
            row=0, column=1, padx=(10, 0))
        self.go = ctk.CTkButton(ask, text="Change it", width=110, height=34, corner_radius=4,
                                font=app.font_body, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                text_color=ACCENT_TEXT, command=self.run)
        self.go.grid(row=0, column=2, padx=(10, 0))
        self.again = self._button(ask, "Try again", lambda: self.run(again=True))
        self.again.grid(row=0, column=3, padx=(8, 0))
        self.status = ctk.CTkLabel(self, text="How much: each keeps the photo's layout. Little "
                                              "changes it a bit, A lot changes all of it.",
                                   font=app.font_tiny, text_color=TEXT_DIM, anchor="w",
                                   justify="left", wraplength=880)
        self.status.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.strip = ctk.CTkFrame(self, fg_color="transparent")
        self.strip.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        if on_use is None:
            self.use_buttons = [
                self._button(bottom, "Save as picture", self.save_as),
                self._button(bottom, "Use in a new video", self.use_in_video)]
        else:
            self.use_buttons = [self._button(bottom, use_text, lambda: self.on_use(self.shown))]
        for button in self.use_buttons:
            button.pack(side="left", padx=(0, 8))
        self._ready_buttons()
        if photo:
            self.set_photo(photo, about)

    def _button(self, master, text, command):
        return ctk.CTkButton(master, text=text, width=140, height=34, corner_radius=4,
                             font=self.app.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                             border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                             command=command)

    def _view(self, column, title):
        ctk.CTkLabel(self, text=title, font=self.app.font_tiny, text_color=TEXT_MUTED,
                     anchor="w").grid(row=1, column=column, sticky="w", padx=4)
        view = ctk.CTkLabel(self, text="", fg_color=FIELD_BG, corner_radius=4,
                            width=self.VIEW[0], height=self.VIEW[1], text_color=TEXT_DIM)
        view.grid(row=2, column=column, sticky="nsew", padx=4)
        view.path, view.job = "", None

        def resized(_event):   # the window changed size: the picture fills its box again
            if view.path:
                if view.job:
                    self.after_cancel(view.job)
                view.job = self.after(150, lambda: self._show(view, view.path))
        view.bind("<Configure>", resized, add="+")
        return view

    def _show(self, view, path, size=None):
        from PIL import Image, ImageOps

        if size is None:   # as big as the box, in the units CTkImage takes
            view.path, view.job = path, None
            scale = ctk.ScalingTracker.get_widget_scaling(view)
            size = (max(self.VIEW[0], round(view.winfo_width() / scale) - 8),
                    max(self.VIEW[1], round(view.winfo_height() / scale) - 8))
        try:
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image).convert("RGB")
                image.thumbnail(size or self.VIEW)
        except OSError:
            view.path = ""
            view.configure(image=None, text="This picture can't be opened.")
            return None
        picture = ctk.CTkImage(light_image=image, dark_image=image, size=image.size)
        view.image = picture   # kept so Tk doesn't drop it; one per view, not one per resize
        view.configure(image=picture, text="")
        return picture

    def _ready_buttons(self):
        busy = "disabled" if self.busy or not self.photo else "normal"
        self.go.configure(state=busy)
        self.again.configure(state="disabled" if self.busy or not self.tries else "normal")
        for button in self.use_buttons:
            button.configure(state="disabled" if self.busy or not self.shown else "normal")

    def open_photo(self):
        path = filedialog.askopenfilename(
            parent=self, title="Open a photo",
            initialdir=paths.known_folder(paths.CSIDL_MYPICTURES, "Pictures"),
            filetypes=[("Photos", " ".join("*" + e for e in sorted(footage.IMAGE_EXTS)))])
        if path:
            self.set_photo(os.path.normpath(path))

    def set_photo(self, path, about=""):
        if self.busy:
            return
        self.photo, self.about, self.tries, self.shown = path, about, [], None
        self.name.configure(text=os.path.basename(path))
        self._show(self.before_view, path)
        self.after_view.path = ""
        self.after_view.configure(image=None, text="Type a change below and press Change it.")
        for child in self.strip.winfo_children():
            child.destroy()
        self._ready_buttons()

    def run(self, again=False):
        """Change the photo on a worker thread; Try again keeps the words, new seed."""
        if self.busy or not self.photo:
            return
        change = " ".join(self.change.get().split())
        if not change:
            self.status.configure(text="Say what should change first.", text_color=ERR_COLOR)
            return
        if not imagegen.installed():
            self.status.configure(text="Changing a photo uses AI pictures: download them in "
                                       "Settings > AI pictures first.", text_color=ERR_COLOR)
            return
        app = self.app
        self.busy = True
        self.stop.clear()
        self._ready_buttons()
        self.status.configure(text="Changing your photo. This takes about 20 seconds with a "
                                   "graphics card, a few minutes without one...",
                              text_color=TEXT_MUTED)
        photo, about, amount = self.photo, self.about, self.amount.get()
        gpu = bool(app.var_writer_gpu.get())
        backend, speed, threads = app.ai_backend_now(), app.ai_speed_now(), app.threads_pair()

        def post(fn):
            app._queue.put(("call", {"fn": fn}))

        def worker():
            nonlocal about
            try:
                # The redraw needs the whole photo in words (sky, light, colours), not the
                # short sentence the video writer uses, or the picture model makes them up.
                looks = self.detail.get(photo, "")
                if not looks and photos.ready()[0]:
                    looks = photos.describe([photo], self.stop.is_set, gpu, detail=True)[0]
                    self.detail[photo] = looks
                about = about or looks
                rewrite = None
                if writer.ready()[0]:
                    rewrite = lambda a, c: writer.changed_picture(a, c, self.stop.is_set, gpu)  # noqa: E731
                os.makedirs(PREVIEW_DIR, exist_ok=True)
                out = os.path.join(PREVIEW_DIR, f"redraw_{projects.new_id()}.png")
                made = imagegen.redraw(photo, change, out, amount, looks or about, rewrite,
                                       should_stop=self.stop.is_set, log=app.ui.log,
                                       backend=backend, speed=speed, threads=threads)
                post(lambda: self._made(out, about, made))
            except imagegen.Stopped:
                post(lambda: self._failed("Stopped."))
            except Exception as exc:  # noqa: BLE001 - shown under the pictures
                message = str(exc) or type(exc).__name__
                post(lambda: self._failed(message))

        threading.Thread(target=worker, daemon=True).start()

    def _made(self, path, about, made):
        self.busy, self.about = False, about or self.about
        if made.backend == imagegen.CPU:
            self.app.remember_ai_backend(imagegen.CPU)
        self.tries.append(path)
        self.status.configure(text=f"Made in {made.seconds:.0f} s. Try again for another "
                                   "version, or pick one below.", text_color=TEXT_DIM)
        self.pick(path)
        thumb = ctk.CTkLabel(self.strip, text="", fg_color=FIELD_BG, corner_radius=4,
                             cursor="hand2")
        thumb.pack(side="left", padx=(0, 6))
        self._show(thumb, path, (96, 64))
        thumb.bind("<Button-1>", lambda _e: self.pick(path))

    def _failed(self, message):
        self.busy = False
        self.status.configure(text=message, text_color=ERR_COLOR)
        self._ready_buttons()

    def pick(self, path):
        self.shown = path
        self._show(self.after_view, path)
        self._ready_buttons()

    def close(self):
        self.stop.set()

    def save_as(self):
        if not self.shown:
            return
        base = os.path.splitext(os.path.basename(self.photo))[0]
        dest = filedialog.asksaveasfilename(
            parent=self, title="Save the picture", defaultextension=".png",
            initialdir=paths.known_folder(paths.CSIDL_MYPICTURES, "Pictures"),
            initialfile=f"{base} changed.png",
            filetypes=[("PNG picture", "*.png"), ("JPEG picture", "*.jpg")])
        if not dest:
            return
        from vidgen import thumbedit

        try:
            thumbedit.export(self.shown, dest)
            self.status.configure(text=f"Saved as {os.path.basename(dest)}.", text_color=TEXT_DIM)
        except OSError as exc:
            self.status.configure(text=f"Couldn't save the picture: {exc}", text_color=ERR_COLOR)

    def use_in_video(self):
        """A new video from text, with this picture already added as a reference photo."""
        if not self.shown:
            return
        keep = os.path.join(SETTINGS_DIR, "photos")
        os.makedirs(keep, exist_ok=True)
        path = os.path.join(keep, os.path.basename(self.shown))
        shutil.copyfile(self.shown, path)   # previews are cleared; this one must stay
        dialog = DraftDialog(self.app)
        dialog.add_photo(path, self.about)


class PhotoList(ctk.CTkFrame):
    """Reference photos: + Add photos, then a row each with a thumbnail, what is in it (typed,
    or filled by photos.describe), Use in the video, Change it... and remove. On the Make new
    video page and in New from text. ``on_change(count)`` hears when photos come and go."""

    def __init__(self, master, app, on_change=None):
        super().__init__(master, fg_color="transparent")
        self.app, self.on_change, self.photos = app, on_change, []
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=14)
        ctk.CTkLabel(head, text="Photos", font=app.font_body, text_color=TEXT,
                     anchor="w").pack(side="left")
        ctk.CTkButton(head, text="+ Add photos", width=120, height=32, corner_radius=4,
                      font=app.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                      border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                      command=self.pick).pack(side="left", padx=(10, 0))
        self.note = ctk.CTkLabel(head, text="Optional. The script is written around them.",
                                 font=app.font_tiny, text_color=TEXT_DIM, anchor="w")
        self.note.pack(side="left", padx=(10, 0))
        self.rows = ctk.CTkScrollableFrame(self, height=112, fg_color="transparent")
        # ponytail: CTk's scrollbar asks for 200 px and would make the list that tall
        self.rows._scrollbar.configure(height=0)

    def hint(self, text):
        self.note.configure(text=text)

    def pick(self):
        room = photos.MAX_PHOTOS - len(self.photos)
        if room <= 0:
            self.hint(f"{photos.MAX_PHOTOS} photos at most.")
            return
        picked = filedialog.askopenfilenames(
            parent=self, title="Add photos",
            filetypes=[("Photos", " ".join("*" + e for e in sorted(footage.IMAGE_EXTS)))])
        for path in [os.path.normpath(p) for p in picked if photos.is_photo(p)][:room]:
            self.add(path)

    def add(self, path, about="", show=True):
        """One photo (Image editor's Use in a new video comes in here too)."""
        if path in {p["path"] for p in self.photos} or len(self.photos) >= photos.MAX_PHOTOS:
            return
        app = self.app
        row = ctk.CTkFrame(self.rows, fg_color="transparent")
        row.pack(fill="x", pady=2)
        picture = ctk.CTkLabel(row, text="?", width=64, text_color=TEXT_DIM)
        picture.pack(side="left")
        self._thumb(picture, path)
        entry = ctk.CTkEntry(row, width=250, height=30, corner_radius=4, font=app.font_tiny,
                             fg_color=FIELD_BG, border_color=FIELD_BORDER, text_color=TEXT,
                             placeholder_text="What is in this photo?")
        entry.pack(side="left", padx=8)
        if about:
            entry.insert(0, about)
        chosen = tk.BooleanVar(self, show)
        ctk.CTkSwitch(row, text="Use in the video", variable=chosen, font=app.font_tiny,
                      text_color=TEXT, progress_color=ACCENT).pack(side="left")
        photo = {"path": path, "entry": entry, "show": chosen, "row": row, "picture": picture}
        ctk.CTkButton(row, text="✕", width=28, height=28, corner_radius=4, fg_color="transparent",
                      hover_color=CARD_HOVER, text_color=TEXT_MUTED, font=app.font_body,
                      command=lambda: self.remove(photo)).pack(side="right", padx=(0, 4))
        ctk.CTkButton(row, text="Change it...", width=90, height=28, corner_radius=4,
                      font=app.font_tiny, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                      border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                      command=lambda: self.change(photo)).pack(side="right", padx=4)
        self.photos.append(photo)
        self.rows.pack(fill="x", padx=8, pady=(6, 0))
        if self.on_change:
            self.on_change(len(self.photos))

    def change(self, photo):
        """The Image editor on this photo; the picture kept replaces it for the video."""
        def use(path):
            keep = os.path.join(SETTINGS_DIR, "photos")
            os.makedirs(keep, exist_ok=True)
            kept = os.path.join(keep, os.path.basename(path))
            shutil.copyfile(path, kept)   # previews are cleared; the video needs this one
            photo["path"] = kept
            self._thumb(photo["picture"], kept)
        change_photo_window(self.app, photo["path"], " ".join(photo["entry"].get().split()),
                            use)

    @staticmethod
    def _thumb(label, path):
        from PIL import Image, ImageOps

        try:
            with Image.open(path) as image:
                small = ImageOps.exif_transpose(image).convert("RGB")
                small.thumbnail((64, 40))
        except (OSError, ValueError):
            return
        picture = ctk.CTkImage(light_image=small, dark_image=small, size=small.size)
        label.configure(image=picture, text="")
        label.image = picture

    def remove(self, photo):
        photo["row"].destroy()
        self.photos.remove(photo)
        if not self.photos:
            self.rows.pack_forget()
        if self.on_change:
            self.on_change(len(self.photos))

    def clear(self):
        for photo in list(self.photos):
            self.remove(photo)

    def photo_list(self) -> list:
        """The photos as the writer takes them. Read on the UI thread."""
        return [draft.Photo(p["path"], " ".join(p["entry"].get().split()), bool(p["show"].get()))
                for p in self.photos]


def change_photo_window(app, photo, about, on_use, title="Change the photo"):
    """The Image editor in a window of its own, for a reference photo or a thumbnail layer.
    ``on_use(path)`` gets the picture the user kept; the window then closes."""
    window = ctk.CTkToplevel(app)
    window.title(title)
    window.configure(fg_color=MAIN_BG)
    window.transient(app)

    def use(path):
        editor.close()
        window.destroy()
        on_use(path)

    def close():
        editor.close()
        window.destroy()

    editor = ImageEditor(window, app, photo, about, on_use=use, use_text="Use this")
    editor.pack(fill="both", expand=True, padx=16, pady=16)
    window.protocol("WM_DELETE_WINDOW", close)
    centre_on(window, app)
    return window


class DraftDialog(ctk.CTkToplevel):
    """New from text: paste plain text, say who it is for, get a script.

    Three pages in one window: the text, the questions with answers picked
    from it, then the script to review. The Smart writer (a model on this PC)
    runs on a worker thread and reports through the app's UI queue; Quick
    split is instant and is also what every Smart writer failure falls back to.
    """

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.result = None
        self.keep = set()        # dropped sentences the user put back
        self.style = ""          # a Home template's extra instruction for the writer
        self.confirming = False  # "Use this script" was pressed over the user's own script
        self.busy = False        # a worker is running, or the model is downloading
        self.stop = threading.Event()
        self.session = None      # the loaded model, kept from Analyse to Write script
        self.note = ""           # why Quick split was used instead of the Smart writer
        self.looked = set()      # photo paths the photo model has already been asked about
        self._after_described = self.analyse   # what carries on once the photos are described
        self._job = None
        self._idle = None
        self._after_download = None
        self._restore = self.show_text
        self.title("New from text")
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.close)

        saved = app.audience
        self.var_content = tk.StringVar(self, saved["audience_content"])
        self.var_age = tk.StringVar(self, saved["audience_age"])
        self.var_platform = tk.StringVar(self, saved["audience_platform"])
        self.var_length = tk.StringVar(self, self.audience().length)
        self.var_aspect = tk.StringVar(self, self.audience().aspect)
        self.var_mode = tk.StringVar(self, app.writer_mode)
        self.var_platform.trace_add("write", self._on_platform)
        self.var_mode.trace_add("write", lambda *_: self._on_mode())

        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        ctk.CTkLabel(card, text="New from text", font=app.font_title, text_color=TEXT,
                     anchor="w").pack(fill="x", padx=20, pady=(18, 2))
        self.intro = ctk.CTkLabel(card, font=app.font_body, text_color=TEXT_MUTED,
                                  justify="left", wraplength=600, anchor="w")
        self.intro.pack(fill="x", padx=20, pady=(0, 10))

        self.text_page = ctk.CTkFrame(card, fg_color="transparent")
        self.ask_page = ctk.CTkFrame(card, fg_color="transparent")
        self.review_page = ctk.CTkFrame(card, fg_color="transparent")
        self._build_text(self.text_page)
        self._build_ask(self.ask_page)
        self._build_review(self.review_page)

        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.pack(side="bottom", fill="x", padx=20, pady=(12, 18))
        self.main_button = ctk.CTkButton(
            buttons, width=170, height=32, corner_radius=4, font=app.font_body,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ACCENT_TEXT,
            text_color_disabled=ACCENT_TEXT)
        self.main_button.pack(side="right")
        self.back_button = self._plain(buttons, "Back", self.show_text)
        self._plain(buttons, "Close", self.close).pack(side="left")

        # What the Smart writer is doing, shown only while it is doing it.
        self.working = ctk.CTkFrame(card, fg_color="transparent")
        self.working_label = ctk.CTkLabel(self.working, text="", font=app.font_tiny,
                                          text_color=TEXT_MUTED, anchor="w")
        self.working_label.pack(fill="x")
        self.working_bar = ctk.CTkProgressBar(self.working, height=4, corner_radius=2,
                                              progress_color=ACCENT, fg_color=FIELD_BG)
        self.working_bar.pack(fill="x", pady=(4, 0))

        app.writer_listeners.append(self._on_download)
        self.show_text()
        centre_on(self, app)
        self.text_box.focus_set()

    def _plain(self, master, text, command, width=96):
        return ctk.CTkButton(
            master, text=text, width=width, height=32, corner_radius=4, font=self.app.font_body,
            fg_color=FIELD_BG, hover_color=CARD_HOVER, border_width=1, border_color=FIELD_BORDER,
            text_color=TEXT, command=command)

    def _menu(self, master, values, variable):
        return ctk.CTkOptionMenu(
            master, values=list(values), variable=variable, width=230, height=32,
            corner_radius=4, font=self.app.font_body, fg_color=FIELD_BG, button_color=FIELD_BG,
            button_hover_color=CARD_HOVER, text_color=TEXT, dropdown_fg_color=CARD_BG,
            dropdown_text_color=TEXT, dropdown_hover_color=CARD_HOVER,
            dropdown_font=self.app.font_body, dynamic_resizing=False)

    def _show(self, page, intro, button, command, back=None):
        """Bring one of the three pages forward."""
        self.confirming = False
        for other in (self.text_page, self.ask_page, self.review_page):
            other.pack_forget()
        page.pack(fill="both", expand=True)
        self.intro.configure(text=intro)
        self.main_button.configure(text=button, command=command, state="normal")
        self.back_button.pack_forget()
        if back:
            self.back_button.configure(text="Back", command=back)
            self.back_button.pack(side="right", padx=(0, 8))

    def smart(self) -> bool:
        return self.var_mode.get() == WRITER_MODES[0]

    # -- working: the Smart writer or its download is busy -------------------------
    def _working(self, message, fraction=None):
        """Show what is happening and turn Back into Cancel."""
        self.busy = True
        self.main_button.configure(state="disabled")
        self.back_button.configure(text="Cancel", command=self.cancel)
        self.back_button.pack(side="right", padx=(0, 8))
        self.working_label.configure(text=message)
        if not self.working.winfo_ismapped():
            self.working.pack(side="bottom", fill="x", padx=20, pady=(8, 0))
        if fraction is None:
            if self.working_bar.cget("mode") != "indeterminate":
                self.working_bar.configure(mode="indeterminate")
                self.working_bar.start()
        else:
            self.working_bar.stop()
            self.working_bar.configure(mode="determinate")
            self.working_bar.set(fraction)

    def _rest(self):
        """Back to the page that was showing before the work began."""
        self.busy = False
        self.working_bar.stop()
        self.working_bar.configure(mode="determinate")
        self.working.pack_forget()
        self._restore()

    def cancel(self):
        self.working_label.configure(text="Stopping...")
        self.stop.set()
        if self.app.writer_downloading:
            self.app.cancel_writer_download()

    def _unload(self):
        """Give the model's memory back when the user has stopped to think."""
        self._idle = None
        if not self.busy and self.session is not None:
            self.session.close()

    def _start(self, job, message):
        """Run ``job`` ("suggest" or "write") on a worker thread."""
        if self._idle:
            self.after_cancel(self._idle)
            self._idle = None
        self.stop.clear()
        self._working(message)
        # Everything the worker needs is read here: it must not touch Tk variables.
        args = (job, self.text(), self.audience(), self.var_length.get(),
                self.app.settings["padding"], bool(self.app.var_writer_gpu.get()),
                bool(self.app.var_auto_cards.get()), self.app.ai_mode(),
                bool(self.app.var_web_facts.get()), self.photo_list())
        threading.Thread(target=self._work, args=args, daemon=True).start()

    def _post(self, **payload):
        self.app._queue.put(("draft", dict(payload, dialog=self)))

    def _work(self, job, text, audience, length, padding, gpu, cards, ai, web=False, shots=()):
        """Worker thread: never touches a widget."""
        keep_loaded = False
        try:
            if job == "describe":   # the photo model runs alone, before the writer loads
                paths = [p.path for p in shots if not p.about]
                photos.describe(paths, self.stop.is_set, gpu, done=lambda i, about: self._post(
                    event="photo", path=paths[i], about=about))
                self._post(event="described")
                return
            if self.session is None:
                self.session = writer.Session(self.stop.is_set, gpu=gpu)
            if job == "suggest":
                found = self.session.suggest(text, audience, padding)
                keep_loaded = True  # Write script comes next; don't load it twice
                self._post(event="suggested", found=found)
            else:
                self._post(event="written",
                           result=self.session.write(
                               text, audience, length, padding, cards, ai, web,
                               progress=lambda message, fraction: self._post(
                                   event="progress", message=message, fraction=fraction),
                               photos=shots))
        except writer.Stopped:
            self._post(event="stopped")
        except (writer.WriterError, draft.DraftError, ScriptError) as exc:
            self._post(event="failed", job=job, message=str(exc))
        except Exception as exc:  # noqa: BLE001 - a thread must not die silently
            self._post(event="failed", job=job, trace=traceback.format_exc(),
                       message=f"The Smart writer hit an unexpected problem ({type(exc).__name__}).")
        finally:
            if not keep_loaded and self.session is not None:
                self.session.close()

    def on_event(self, payload):
        """UI thread: a worker finished."""
        if not self.winfo_exists():
            return
        event = payload["event"]
        if event == "progress":   # a long video's outline and parts, one by one
            if self.busy:
                self._working(payload["message"], payload["fraction"])
            return
        if event == "photo":      # one photo described: fill its box unless the user typed
            for photo in self.photos:
                if photo["path"] == payload["path"] and not photo["entry"].get().strip():
                    photo["entry"].insert(0, payload["about"])
            return
        if payload.get("trace"):
            self.app.session_log.write(payload["trace"])
        if self.session is not None and self.session.gpu_failed:
            self.session.gpu_failed = False  # say it once
            self.app.append_log("⚠️ The graphics card could not be used for the Smart writer, "
                                "so it ran on the processor.")
        self.busy = False
        if event == "stopped":
            self._rest()
        elif event == "described":
            self._rest()
            after, self._after_described = self._after_described, self.analyse
            after()
        elif event == "suggested":
            self._rest()
            self._apply(payload["found"])
            # The model stays loaded for Write script, but not for ever.
            self._idle = self.after(120_000, self._unload)
        elif event == "written":
            self._rest()
            self.note, self.result = "", payload["result"]
            self.show_review()
        elif payload["job"] == "describe":   # no descriptions: the user can type them
            self._rest()
            self.found.configure(text=payload["message"], text_color=ERR_COLOR)
        elif payload["job"] == "suggest":
            self._rest()
            self._apply(self._rule_suggestion())
        else:
            self._rest()
            self._quick_split(payload["message"])

    # -- page 1: the text ----------------------------------------------------------
    def _build_text(self, page):
        app = self.app
        self.text_box = ctk.CTkTextbox(
            page, width=620, height=260, font=app.font_body, corner_radius=4,
            fg_color=FIELD_BG, text_color=TEXT, border_width=0, wrap="word")
        self.text_box.pack(padx=14)
        for event in ("<KeyRelease>", "<<Paste>>", "<<Cut>>"):
            self.text_box.bind(event, self._on_text)
        self.found = ctk.CTkLabel(page, text="Paste or type your text above.", font=app.font_tiny,
                                  text_color=TEXT_DIM, anchor="w", justify="left", wraplength=600)
        self.found.pack(fill="x", padx=20, pady=(6, 8))
        self.photo_box = PhotoList(page, app, self._photos_changed)
        self.photo_box.pack(fill="x", padx=6)

    # -- reference photos ---------------------------------------------------------
    @property
    def photos(self):
        return self.photo_box.photos

    def add_photo(self, path, about="", show=True):
        self.photo_box.add(path, about, show)

    def _photos_changed(self, count):
        # The text box gives up the room, so the window still fits a laptop's screen.
        self.text_box.configure(height=130 if count else 260)
        if count:
            self.photo_box.hint("Analyse looks at them first." if photos.ready()[0] else
                                "Say what is in each one, or download the Smart writer to "
                                "fill these in.")

    def photo_list(self) -> list:
        return self.photo_box.photo_list()

    def show_text(self):
        self._restore = self.show_text
        self._show(self.text_page, "Paste any text and press Analyse. The app reads it and "
                   "suggests who the video is for and how long it should be.",
                   "Analyse", self.analyse)

    def prefill(self, text):
        """Start with ``text`` already in the box: plain text the user tried to render."""
        self.text_box.insert("1.0", text)
        self._show(self.text_page, "This is plain text, not a script yet, so it is here in "
                   "New from text. Press Analyse and the app turns it into a script.",
                   "Analyse", self.analyse)
        self._on_text()

    def _rule_suggestion(self):
        return draft.suggest(self.text(), self.audience(), self.app.settings["padding"])

    def analyse(self):
        """Read the text and pick answers to the questions; the user can change them all."""
        if self.busy:
            return
        if not self.text():
            self.found.configure(text="Paste some text first.", text_color=ERR_COLOR)
            return
        self.keep = set()
        new = [p for p in self.photo_list() if not p.about and p.path not in self.looked]
        if new and photos.ready()[0]:
            self.looked.update(p.path for p in new)
            self._start("describe", "Looking at your photos...")
        elif self.smart() and writer.ready()[0]:
            self._start("suggest", "The Smart writer is reading your text...")
        else:
            self._apply(self._rule_suggestion())

    def _apply(self, found):
        self.var_content.set(found.audience.content)
        self.var_age.set(found.audience.age)
        self.var_length.set(found.length)  # after the platform: it presets the length
        self.reason.configure(text=found.reason)
        self.show_ask()

    # -- page 2: the questions -----------------------------------------------------
    def _build_ask(self, page):
        app = self.app
        self.reason = ctk.CTkLabel(page, text="", font=app.font_body, text_color=TEXT,
                                   anchor="w", justify="left", wraplength=600)
        self.reason.pack(fill="x", padx=20, pady=(0, 4))
        self.ask_error = ctk.CTkLabel(page, text="", font=app.font_tiny, text_color=TEXT_DIM,
                                      anchor="w", justify="left", wraplength=600)
        self.ask_error.pack(fill="x", padx=20, pady=(0, 8))
        questions = ctk.CTkFrame(page, fg_color="transparent")
        questions.pack(fill="x", padx=20)
        rows = (
            ("Who is it for?", lambda m: self._menu(m, draft.CONTENT, self.var_content)),
            ("Age", lambda m: FluentSegmented(m, app, list(draft.AGES), self.var_age, 380)),
            ("Where will it go?", lambda m: self._menu(m, draft.PLATFORMS, self.var_platform)),
            ("How long?", lambda m: self._menu(m, draft.LENGTHS, self.var_length)),
            ("Aspect ratio", lambda m: FluentSegmented(m, app, RATIO_OPTIONS, self.var_aspect, 170)),
            ("Written by", lambda m: FluentSegmented(m, app, list(WRITER_MODES), self.var_mode, 260)),
        )
        for position, (label, make) in enumerate(rows):
            ctk.CTkLabel(questions, text=label, font=app.font_body, text_color=TEXT, width=140,
                         anchor="w").grid(row=position, column=0, sticky="w", pady=5)
            make(questions).grid(row=position, column=1, sticky="w", pady=5)
        self.mode_hint = ctk.CTkLabel(page, text="", font=app.font_tiny, text_color=TEXT_DIM,
                                      anchor="w", justify="left", wraplength=600)
        self.mode_hint.pack(fill="x", padx=20, pady=(6, 0))

    def audience(self) -> draft.Audience:
        return draft.Audience(self.var_content.get(), self.var_age.get(), self.var_platform.get(),
                              self.style)

    def _on_platform(self, *_args):
        """A platform suggests a length and a shape; both can still be changed."""
        self.var_length.set(self.audience().length)
        self.var_aspect.set(self.audience().aspect)

    def _on_text(self, _event=None):
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(250, self._analyse)

    def text(self) -> str:
        return self.text_box.get("1.0", "end").strip()

    def _analyse(self):
        self._job = None
        found = draft.analyse(self.text(), padding=self.app.settings["padding"])
        self.found.configure(text_color=TEXT_DIM)
        if not found.words:
            self.found.configure(text="Paste or type your text above.")
            return
        parts = [f"{found.words} words", f"{found.sentences} sentences",
                 f"about {round(found.seconds)} s read in full"]
        if found.topics:
            parts.append("about: " + ", ".join(found.topics[:4]))
        self.found.configure(text=" · ".join(parts))

    def needs_download(self) -> bool:
        """The writer isn't there, or photos were added and its photo part isn't."""
        return self.smart() and writer.runtime_path() is not None and (
            not writer.installed() or (bool(self.photos) and not writer.sees()))

    def _on_mode(self):
        """Say what the chosen writer does, and what the button will do."""
        if self._restore != self.show_ask or self.busy:
            return
        model, size = writer.MODEL, writer_size()
        if self.needs_download() and writer.installed():
            hint = (f"To look at your photos the Smart writer needs its photo part, a one-time "
                    f"download of {size} ({writer.VISION.name}, {writer.VISION.licence} "
                    "licence). It runs on this PC: your photos are never sent anywhere.")
            button = f"Download ({size})"
        elif self.needs_download():
            hint = (f"The Smart writer is a one-time download of {size} "
                    f"({model.name}, {model.licence} licence). After that it runs on this PC: "
                    "your text is never sent anywhere.")
            button = f"Download ({size})"
        elif self.smart():
            hint = ("The Smart writer rewrites your text to fit the length and picks the search "
                    "words. It runs on this PC and takes 10 to 60 seconds; a long video is "
                    "written a part at a time, a minute or two.")
            button = "Write script"
        else:
            hint = ("Quick split keeps your words exactly as written and splits them into "
                    "scenes. It is instant.")
            button = "Write script"
        self.mode_hint.configure(text=hint)
        self.main_button.configure(text=button, command=self.write)

    def show_ask(self):
        self._restore = self.show_ask
        self.ask_error.configure(text="These answers were picked from your text. Change "
                                      "anything that isn't right.", text_color=TEXT_DIM)
        self._show(self.ask_page, "Who is the video for, and how long should it be?",
                   "Write script", self.write, back=self.show_text)
        self._on_mode()

    # -- writing -------------------------------------------------------------------
    def write(self):
        if self.busy:
            return
        if not self.smart():
            self._quick_split()
        elif self.needs_download():
            self._after_download = self.write
            self.app.start_writer_download()
            self._working(f"Downloading the Smart writer ({writer_size()})...", 0)
        else:
            ok, why = writer.ready()
            new = [p for p in self.photo_list() if not p.about and p.path not in self.looked]
            if ok and new and photos.ready()[0]:   # photos added after Analyse
                self.looked.update(p.path for p in new)
                self._after_described = self.write
                self._start("describe", "Looking at your photos...")
            elif ok:
                self._start("write", "The Smart writer is writing your script. This takes "
                                     "10 to 60 seconds...")
            else:
                self._quick_split(why)

    def _quick_split(self, because=""):
        """Write with the user's own words. ``because`` is why the Smart writer wasn't used."""
        try:
            self.result = draft.write(self.text(), self.audience(), self.var_length.get(),
                                      keep=self.keep, padding=self.app.settings["padding"],
                                      cards=bool(self.app.var_auto_cards.get()),
                                      ai=self.app.ai_mode(), photos=self.photo_list())
        except (draft.DraftError, ScriptError) as exc:
            self.ask_error.configure(text=str(exc), text_color=ERR_COLOR)
            return
        self.note = because
        self.show_review()

    def _on_download(self, payload):
        """UI thread: the model download moved on (it may have been started in Settings)."""
        if not self.winfo_exists() or not self.busy or self._after_download is None:
            return
        event = payload["event"]
        if event == "progress":
            done, total = payload["done"], payload["total"]
            self._working(f"Downloading the Smart writer: {done / 1e9:.2f} of "
                          f"{total / 1e9:.2f} GB. You can cancel and carry on later.",
                          done / total if total else 0)
            return
        after, self._after_download = self._after_download, None
        self._rest()
        if event == "done":
            after()
        elif event == "failed":
            self.ask_error.configure(text=payload["message"], text_color=ERR_COLOR)
        else:
            self.ask_error.configure(text="Download paused. It carries on from here next time.",
                                     text_color=TEXT_DIM)

    # -- page 3: the script --------------------------------------------------------
    def _build_review(self, page):
        app = self.app
        self.script_view = ctk.CTkTextbox(
            page, width=620, height=230, font=app.font_mono, corner_radius=4,
            fg_color=FIELD_BG, text_color=TEXT, border_width=0, wrap="word")
        self.script_view.pack(padx=14)
        self.summary = ctk.CTkLabel(page, text="", font=app.font_tiny, text_color=TEXT_MUTED,
                                    anchor="w", justify="left", wraplength=600)
        self.summary.pack(fill="x", padx=20, pady=(6, 0))
        self.dropped_box = ctk.CTkFrame(page, fg_color="transparent")
        self.dropped_box.pack(fill="x", padx=14, pady=(6, 0))

    def show_review(self):
        app, result = self.app, self.result
        self._restore = self.show_review
        self._show(self.review_page, "Here is the script. You can still edit it after it is in "
                   "the editor, and the Storyboard shows the clips it finds.",
                   "Use this script", self.use, back=self.show_ask)
        self.script_view.configure(state="normal")
        self.script_view.delete("1.0", "end")
        self.script_view.insert("1.0", result.script)
        self.script_view.configure(state="disabled")
        plural = "scene" if len(result.scenes) == 1 else "scenes"
        self.summary.configure(
            text=f"{result.source} · about {round(result.seconds)} s · {len(result.scenes)} "
                 f"{plural} · voice: {voices.persona(result.voice).short} · {result.aspect}",
            text_color=TEXT_MUTED)

        for child in self.dropped_box.winfo_children():
            child.destroy()
        if self.note:
            ctk.CTkLabel(self.dropped_box, font=app.font_tiny, text_color=ERR_COLOR, anchor="w",
                         justify="left", wraplength=590,
                         text=f"{self.note} This script was made with Quick split instead."
                         ).pack(fill="x", padx=6, pady=(0, 4))
        if result.source == "Smart writer":
            again = ctk.CTkFrame(self.dropped_box, fg_color="transparent")
            again.pack(fill="x", padx=6)
            ctk.CTkLabel(again, font=app.font_tiny, text_color=TEXT_DIM, anchor="w",
                         text="Not quite right? Each try comes out a little different."
                         ).pack(side="left")
            self._plain(again, "Write again", self.write, width=110).pack(side="right")
        if not result.dropped:
            return
        count = len(result.dropped)
        header = ctk.CTkFrame(self.dropped_box, fg_color="transparent")
        header.pack(fill="x", padx=6)
        ctk.CTkLabel(header, font=app.font_body, text_color=TEXT, anchor="w",
                     text=f"Left out to fit {result.length}: {count} "
                          f"sentence{'' if count == 1 else 's'}").pack(side="left")
        longer = result.longer
        label = "Keep everything" if longer == draft.KEEP_ALL else f"Use {longer} and keep everything"
        self._plain(header, label, lambda: self.use_length(longer), width=230).pack(side="right")
        listing = ctk.CTkScrollableFrame(
            self.dropped_box, width=600, height=min(count, 3) * 36, fg_color="transparent",
            scrollbar_fg_color=SCROLLBAR_BG, scrollbar_button_color=SCROLLBAR_THUMB,
            scrollbar_button_hover_color=SCROLLBAR_THUMB_HOVER)
        listing.pack(fill="x")
        listing.grid_columnconfigure(0, weight=1)
        for position, (index, sentence) in enumerate(result.dropped):
            ctk.CTkLabel(listing, text=ellipsize(sentence, 74), font=app.font_tiny,
                         text_color=TEXT_MUTED, anchor="w").grid(row=position, column=0,
                                                                 sticky="w", pady=3)
            self._plain(listing, "Put back", lambda i=index: self.put_back(i),
                        width=84).grid(row=position, column=1, padx=(8, 4), pady=3)

    def put_back(self, index):
        self.keep.add(index)
        self._quick_split(self.note)

    def use_length(self, length):
        self.var_length.set(length)
        self._quick_split(self.note)

    def use(self):
        if self.busy:
            return
        # Never silently replace a script the user wrote: ask once, in place.
        if self.app.script_is_the_users_own() and not self.confirming:
            self.confirming = True
            self.summary.configure(text="This replaces the script in the editor. Press again "
                                        "to replace it.", text_color=ERR_COLOR)
            self.main_button.configure(text="Replace my script")
            return
        self.app.source_text = self.text()   # what "make it longer" can draw on later
        self.app.use_draft(self.result, self.audience(), self.var_mode.get())
        self.close()

    def close(self):
        """Stop the model (a download carries on; Settings shows it) and close."""
        self.stop.set()
        if self.session is not None:
            self.session.close()
        if self._on_download in self.app.writer_listeners:
            self.app.writer_listeners.remove(self._on_download)
        self.destroy()
