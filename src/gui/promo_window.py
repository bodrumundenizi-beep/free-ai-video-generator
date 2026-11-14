"""Make a promo video (5.3): a 15-25 second launch video about the user's own app or site.

Step 1: add screenshots and (optionally) the project's folder or README; the Smart writer
fills in the form, and every line stays editable. Step 2: pick a tone, a shape, a length and
the volume, press Make. The video, a poster picture and a text to post come out.
"""
from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
import time
import traceback
from tkinter import filedialog

from gui.base import *  # noqa: F401,F403  (customtkinter, colours, Card, centre_on, writer...)
from gui.base import asset_path, centre_on, default_output_dir
from vidgen import photos, promo
from vidgen.promo import browser, form, pictures, plan, sound, tones

MAX_SHOTS = 6
SHAPES = ("16:9", "9:16", "1:1")
LENGTHS = ("15 s", "20 s", "25 s")
BROWSERS_HINT = ("Promo videos are drawn by a web browser, offline: install Google Chrome or "
                 "Brave (both free), then try again. Microsoft Edge works too.")


class PromoDialog(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Make a promo video")
        self.configure(fg_color=MAIN_BG)
        self.transient(app)
        self.geometry("760x860")
        self.minsize(700, 600)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.items: list[tuple[str, ctk.BooleanVar]] = []   # pictures: (path, ticked)
        self.thumbs = []             # keeps the thumbnails' images alive
        self.logo = ""
        self.readme_text = ""        # a GitHub README, fetched (a folder's is read when needed)
        self.project = ""            # a folder or a README file
        self.palette = None
        self.busy = False
        self.stop = threading.Event()
        self.events: queue.Queue = queue.Queue()
        self.result = ""

        outer = ctk.CTkScrollableFrame(self, fg_color=MAIN_BG)
        outer.pack(fill="both", expand=True, padx=8, pady=8)
        self.outer = outer
        self._header(outer)
        self._inputs(outer)
        self._form(outer)
        self._choices(outer)
        self._make_row(outer)
        self.after(120, self._poll)
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

    def _header(self, parent):
        ctk.CTkLabel(parent, text="Make a promo video", font=self.app.font_title, text_color=TEXT,
                     anchor="w").pack(fill="x", padx=12, pady=(6, 0))
        ctk.CTkLabel(parent, text="A 15 to 25 second launch video for your own app or website, "
                                  "from its screenshots. Everything is made on this PC.",
                     font=self.app.font_tiny, text_color=TEXT_DIM, anchor="w").pack(
            fill="x", padx=12, pady=(0, 10))

    def _inputs(self, parent):
        body = self._card(parent, "1. Your app",
                          "Give the project's folder, README or GitHub link: its pictures and logo "
                          "are found for you, and the Smart writer reads it to fill in the lines "
                          "below (never .env files, keys or .git). Add your own screenshots too.")
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x")
        self._button(row, "Project folder...", self.pick_folder).pack(side="left")
        self._button(row, "README file...", self.pick_readme, width=120).pack(side="left",
                                                                            padx=(6, 0))
        self._button(row, "Add screenshots...", self.add_shots).pack(side="left", padx=(16, 0))
        self._button(row, "Clear", self.clear_shots, width=70).pack(side="left", padx=(6, 0))
        link_row = ctk.CTkFrame(body, fg_color="transparent")
        link_row.pack(fill="x", pady=(8, 0))
        self.github = ctk.CTkEntry(link_row, width=380, height=32, corner_radius=4,
                                   font=self.app.font_body, fg_color=FIELD_BG,
                                   border_color=FIELD_BORDER, text_color=TEXT,
                                   placeholder_text="or a GitHub link: github.com/you/your-app")
        self.github.pack(side="left")
        self.github.bind("<Return>", lambda _e: self.from_github())
        self.github_button = self._button(link_row, "Get from GitHub", self.from_github, width=140)
        self.github_button.pack(side="left", padx=(6, 0))
        self.inputs_label = ctk.CTkLabel(body, text="No pictures yet.", font=self.app.font_tiny,
                                         text_color=TEXT_MUTED, anchor="w", justify="left",
                                         wraplength=640)
        self.inputs_label.pack(fill="x", pady=(8, 0))
        self.gallery = ctk.CTkFrame(body, fg_color="transparent")
        self.gallery.pack(fill="x", pady=(4, 0))
        fill_row = ctk.CTkFrame(body, fg_color="transparent")
        fill_row.pack(fill="x", pady=(8, 0))
        self.fill_button = self._button(fill_row, "Fill in with the Smart writer", self.fill,
                                        accent=True, width=230)
        self.fill_button.pack(side="left")
        self.fill_status = ctk.CTkLabel(fill_row, text="", font=self.app.font_tiny,
                                        text_color=TEXT_DIM, anchor="w", justify="left",
                                        wraplength=400)
        self.fill_status.pack(side="left", padx=(10, 0), fill="x", expand=True)

    def _entry(self, parent, label, placeholder, row, width=420):
        ctk.CTkLabel(parent, text=label, font=self.app.font_body, text_color=TEXT_MUTED,
                     anchor="w").grid(row=row, column=0, sticky="w", pady=3, padx=(0, 10))
        entry = ctk.CTkEntry(parent, width=width, height=32, corner_radius=4, font=self.app.font_body,
                             fg_color=FIELD_BG, border_color=FIELD_BORDER, text_color=TEXT,
                             placeholder_text=placeholder)
        entry.grid(row=row, column=1, columnspan=2, sticky="w", pady=3)
        return entry

    def _form(self, parent):
        body = self._card(parent, "2. What the video says",
                          "Check every line: the video only says what is here. Keep lines short.")
        self.name = self._entry(body, "Name", "AI Video Studio", 0)
        self.link = self._entry(body, "Link", "yourapp.com", 1)
        self.hook = self._entry(body, "First line", "Paste text. Get a finished video.", 2)
        self.what = self._entry(body, "What it does", "One line about it", 3)
        self.result_line = self._entry(body, "Result", "What you get, e.g. Your video is ready", 4)
        self.features = []
        for i in range(3):
            ctk.CTkLabel(body, text=f"Feature {i + 1}", font=self.app.font_body,
                         text_color=TEXT_MUTED, anchor="w").grid(row=5 + i, column=0, sticky="w",
                                                                 pady=3)
            title = ctk.CTkEntry(body, width=200, height=32, corner_radius=4, font=self.app.font_body,
                                 fg_color=FIELD_BG, border_color=FIELD_BORDER, text_color=TEXT,
                                 placeholder_text="Short title")
            title.grid(row=5 + i, column=1, sticky="w", pady=3)
            detail = ctk.CTkEntry(body, width=212, height=32, corner_radius=4,
                                  font=self.app.font_body, fg_color=FIELD_BG,
                                  border_color=FIELD_BORDER, text_color=TEXT,
                                  placeholder_text="A few words more (optional)")
            detail.grid(row=5 + i, column=2, sticky="w", pady=3, padx=(8, 0))
            self.features.append((title, detail))
        self.claim = self._entry(body, "One true claim", "Optional: a fact, e.g. Nothing is uploaded", 8)

    def _choices(self, parent):
        body = self._card(parent, "3. How it looks and sounds")
        labels = {t.label: name for name, t in tones.TONES.items()}
        self.tone_names = labels
        ctk.CTkLabel(body, text="Tone", font=self.app.font_body, text_color=TEXT_MUTED,
                     anchor="w").grid(row=0, column=0, sticky="w", pady=4, padx=(0, 10))
        self.var_tone = ctk.StringVar(value=tones.TONES[tones.DEFAULT].label)
        ctk.CTkOptionMenu(body, values=list(labels), variable=self.var_tone, width=200, height=32,
                          font=self.app.font_body, fg_color=FIELD_BG, button_color=FIELD_BORDER,
                          text_color=TEXT, command=lambda _v: self._tone_line()).grid(
            row=0, column=1, sticky="w", pady=4)
        self.tone_about = ctk.CTkLabel(body, text="", font=self.app.font_tiny, text_color=TEXT_DIM,
                                       anchor="w")
        self.tone_about.grid(row=0, column=2, sticky="w", padx=(10, 0))
        self._tone_line()
        self.var_shape = ctk.StringVar(value="16:9")
        self.var_length = ctk.StringVar(value="20 s")
        for row, (label, values, var) in enumerate((("Shape", SHAPES, self.var_shape),
                                                    ("Length", LENGTHS, self.var_length)), 1):
            ctk.CTkLabel(body, text=label, font=self.app.font_body, text_color=TEXT_MUTED,
                         anchor="w").grid(row=row, column=0, sticky="w", pady=4)
            ctk.CTkSegmentedButton(body, values=list(values), variable=var, width=240,
                                   font=self.app.font_body, selected_color=ACCENT,
                                   selected_hover_color=ACCENT_HOVER).grid(
                row=row, column=1, columnspan=2, sticky="w", pady=4)
        self.var_volume = ctk.DoubleVar(value=1.0)
        self.var_effects = ctk.DoubleVar(value=1.0)
        for row, (label, var, low) in enumerate((("Video volume", self.var_volume, 0.25),
                                                 ("Effects volume", self.var_effects, 0.0)), 3):
            ctk.CTkLabel(body, text=label, font=self.app.font_body, text_color=TEXT_MUTED,
                         anchor="w").grid(row=row, column=0, sticky="w", pady=4)
            shown = ctk.CTkLabel(body, text="100%", font=self.app.font_body, text_color=TEXT,
                                 width=50, anchor="w")
            ctk.CTkSlider(body, from_=low, to=1.5, variable=var, width=240,
                          button_color=ACCENT, progress_color=ACCENT,
                          command=lambda v, s=shown: s.configure(text=f"{round(float(v) * 100)}%"),
                          ).grid(row=row, column=1, sticky="w", pady=4)
            shown.grid(row=row, column=2, sticky="w", padx=(10, 0))
        self.var_music = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(body, text="Music (picked to fit the tone)", variable=self.var_music,
                        font=self.app.font_body, text_color=TEXT_MUTED, checkbox_width=18,
                        checkbox_height=18, border_width=1).grid(row=5, column=1, columnspan=2,
                                                                 sticky="w", pady=(6, 0))

    def _make_row(self, parent):
        body = self._card(parent, "4. Make it")
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x")
        self.make_button = self._button(row, "Make promo video", self.make, accent=True, width=200)
        self.make_button.pack(side="left")
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
        self.share = ctk.CTkTextbox(body, height=90, font=self.app.font_body, fg_color=FIELD_BG,
                                    text_color=TEXT, border_width=0, wrap="word")
        self.copy_button = self._button(body, "Copy text", self.copy_share, width=110)

    def _tone_line(self):
        name = self.tone_names.get(self.var_tone.get(), tones.DEFAULT)
        self.tone_about.configure(text=tones.get(name).about)

    # -- inputs -------------------------------------------------------------------------
    @property
    def shots(self) -> list[str]:
        """The ticked pictures, at most six, in the order shown."""
        return [path for path, ticked in self.items if ticked.get()][:MAX_SHOTS]

    def _add(self, paths, ticked=True):
        known = {os.path.normcase(p) for p, _v in self.items}
        for path in paths:
            if os.path.normcase(path) not in known and len(self.items) < MAX_SHOTS * 2:
                self.items.append((path, ctk.BooleanVar(value=ticked and len(self.shots) < MAX_SHOTS)))
                known.add(os.path.normcase(path))
        self.palette = None
        self._show_inputs()

    def add_shots(self):
        picked = filedialog.askopenfilenames(
            parent=self, title="Screenshots of your app",
            filetypes=[("Pictures", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*")])
        self._add([p for p in picked if photos.is_photo(p)])

    def clear_shots(self):
        self.items, self.logo, self.palette = [], "", None
        self._show_inputs()

    def pick_folder(self):
        folder = filedialog.askdirectory(parent=self, title="Your project's folder")
        if folder:
            self._from_project(folder)

    def pick_readme(self):
        path = filedialog.askopenfilename(parent=self, title="Your project's README",
                                          filetypes=[("README", "*.md *.txt *.rst"),
                                                     ("All files", "*.*")])
        if path:
            self._from_project(path)

    def _from_project(self, source):
        self.project, self.readme_text = source, ""
        self._find(lambda: pictures.from_folder(source), "Looking for pictures in the project...")

    def from_github(self):
        link = self.github.get().strip()
        if not pictures.parse_github(link):
            self.inputs_label.configure(text="Paste a link like github.com/you/your-app.",
                                        text_color=ERR_COLOR)
            return
        self.project = ""
        cache = os.path.join(CACHE_ROOT, "github")
        self._find(lambda: pictures.from_github(link, cache=cache),
                   "Fetching the README and its pictures "
                                                       "from GitHub...")

    def _find(self, job, message):
        if self.busy:
            return
        self._set_busy(True)
        self.inputs_label.configure(text=message, text_color=TEXT_DIM)

        def worker():
            try:
                self.events.put(("found", job()))
            except Exception:  # noqa: BLE001 - a thread must not die silently
                self.events.put(("found", pictures.Found(notes=[
                    "The pictures couldn't be read: "
                    + traceback.format_exc().strip().splitlines()[-1]])))

        threading.Thread(target=worker, daemon=True).start()

    def _found(self, found):
        if found.readme:
            self.readme_text = found.readme
        if found.logo:
            self.logo = found.logo
        self._add(found.pictures)
        if found.notes:
            self.inputs_label.configure(text=" ".join(found.notes), text_color=ERR_COLOR)

    def _show_inputs(self):
        for child in self.gallery.winfo_children():
            child.destroy()
        self.thumbs = []
        from PIL import Image

        shown = self.items + ([(self.logo, None)] if self.logo else [])
        for i, (path, ticked) in enumerate(shown):
            cell = ctk.CTkFrame(self.gallery, fg_color=FIELD_BG, corner_radius=4)
            cell.grid(row=i // 5, column=i % 5, padx=(0, 6), pady=(0, 6), sticky="n")
            try:
                with Image.open(path) as image:
                    image = image.convert("RGBA")
                    image.thumbnail((116, 72))
                    picture = ctk.CTkImage(image.copy(), size=image.size)
                self.thumbs.append(picture)
                ctk.CTkLabel(cell, text="", image=picture, width=116, height=72).pack(padx=4,
                                                                                    pady=(4, 2))
            except OSError:
                ctk.CTkLabel(cell, text="?", width=116, height=72).pack(padx=4, pady=(4, 2))
            if ticked is None:
                ctk.CTkLabel(cell, text="Logo (ending)", font=self.app.font_tiny,
                             text_color=TEXT_DIM).pack(pady=(0, 4))
            else:
                ctk.CTkCheckBox(cell, text=os.path.basename(path)[:16], variable=ticked,
                                font=self.app.font_tiny, text_color=TEXT_MUTED, checkbox_width=16,
                                checkbox_height=16, border_width=1,
                                command=self._count).pack(padx=4, pady=(0, 4), anchor="w")
        self._count()

    def _count(self):
        parts = []
        if self.items:
            parts.append(f"{len(self.shots)} of {len(self.items)} pictures used"
                         + (" (at most 6)" if len([1 for _p, v in self.items if v.get()]) > MAX_SHOTS
                            else "") + ". Untick any you don't want.")
        else:
            parts.append("No pictures yet.")
        if self.project:
            readme = form.find_readme(self.project)
            parts.append(f"Project: {self.project}" + ("" if readme else "  (no README found)"))
        elif self.readme_text:
            parts.append("README fetched from GitHub.")
        self.inputs_label.configure(text="\n".join(parts), text_color=TEXT_MUTED)

    # -- the Smart writer fills the form ----------------------------------------------
    def fill(self):
        if self.busy:
            return
        readme = form.read_readme(self.project) if self.project else self.readme_text
        if not readme and not self.shots:
            self.fill_status.configure(text="Add screenshots or a README first.",
                                       text_color=ERR_COLOR)
            return
        ok, why = writer.ready()
        if not ok:
            self.fill_status.configure(text=why + " You can fill in the lines yourself.",
                                       text_color=ERR_COLOR)
            return
        self._set_busy(True)
        self.fill_status.configure(text="The Smart writer is reading it...", text_color=TEXT_DIM)
        gpu = bool(self.app.var_writer_gpu.get())
        shots, stop = list(self.shots), self.stop

        def worker():
            try:
                text = readme
                if not text:
                    # No README: what the screenshots show is the text to work from.
                    seen = photos.describe(shots, should_stop=stop.is_set, gpu=gpu, detail=True)
                    text = "Screenshots of the app:\n" + "\n".join(f"- {s}" for s in seen if s)
                with writer.Session(stop.is_set, gpu=gpu) as session:
                    def ask(system, user, schema):
                        return session._ask(system, [("user", user)], schema, 600, 0.3)
                    filled, notes = form.fill(text, ask)
                self.events.put(("filled", (filled, notes)))
            except writer.Stopped:
                self.events.put(("fill_failed", "Stopped."))
            except (writer.WriterError, form.FormError) as exc:
                self.events.put(("fill_failed", str(exc)))
            except Exception:  # noqa: BLE001 - a thread must not die silently
                self.events.put(("fill_failed", "The Smart writer hit a problem: "
                                 + traceback.format_exc().strip().splitlines()[-1]))

        threading.Thread(target=worker, daemon=True).start()

    def _filled(self, filled, notes):
        def put(entry, value):
            entry.delete(0, "end")
            if value:
                entry.insert(0, value)

        if not self.name.get().strip():
            put(self.name, filled.name)
        put(self.hook, filled.hook)
        put(self.what, filled.what)
        put(self.result_line, filled.result)
        for (title, detail), feature in zip(self.features, filled.features + [None] * 3):
            put(title, feature.title if feature else "")
            put(detail, feature.detail if feature else "")
        self.fill_status.configure(
            text=("Filled in. " + " ".join(notes)) if notes else "Filled in. Check every line.",
            text_color=TEXT_DIM if not notes else ERR_COLOR)

    # -- making the video ---------------------------------------------------------------
    def current(self) -> promo.Promo:
        tone = self.tone_names.get(self.var_tone.get(), tones.DEFAULT)
        features = [promo.Feature(t.get().strip(), d.get().strip()) for t, d in self.features
                    if t.get().strip()]
        return promo.Promo(
            name=self.name.get().strip(), hook=self.hook.get().strip(),
            what=self.what.get().strip(), link=self.link.get().strip(), features=features,
            shots=list(self.shots), result=self.result_line.get().strip(),
            claim=self.claim.get().strip(), tone=tone, aspect=self.var_shape.get(),
            seconds=float(self.var_length.get().split()[0]))

    def make(self):
        if self.busy:
            return
        p = self.current()
        if not p.name or not p.hook:
            self.status.configure(text="Fill in at least the name and the first line.",
                                  text_color=ERR_COLOR)
            return
        if browser.find() is None:
            self.status.configure(text=BROWSERS_HINT, text_color=ERR_COLOR)
            return
        if self.palette is None and self.project and os.path.isdir(self.project):
            self.palette = form.palette_from_folder(self.project)
        out = self._out_path(p)
        music_dir = asset_path("music")
        music = sound.pick_music(p.tone, music_dir) if self.var_music.get() and music_dir else None
        effects = asset_path("sfx")
        volume, effects_volume = float(self.var_volume.get()), float(self.var_effects.get())
        palette, stop, logo = self.palette, self.stop, self.logo
        stop.clear()
        self._set_busy(True)
        self.done_row.pack_forget()
        self.share.pack_forget()
        self.copy_button.pack_forget()
        self.progress.set(0)
        self.status.configure(text="Checking the lines and laying out the scenes...",
                              text_color=TEXT_DIM)
        began = time.monotonic()

        def progress(done, total):
            if done % 15 == 0 or done == total:
                self.events.put(("progress", (done, total, time.monotonic() - began)))

        def worker():
            try:
                scenes = promo.make(p, out, palette=palette, logo=logo, music=music,
                                    sfx_folder=effects,
                                    effects_volume=effects_volume, volume=volume,
                                    should_stop=stop.is_set, progress=progress)
                poster = self._poster(out, scenes)
                self.events.put(("made", (out, poster, p)))
            except writer.Stopped:
                self.events.put(("failed", "Stopped."))
            except (promo.PromoError, promo.BrowserError, OSError, RuntimeError) as exc:
                self.events.put(("failed", str(exc)))
            except Exception:  # noqa: BLE001
                self.events.put(("failed", "Something went wrong: "
                                 + traceback.format_exc().strip().splitlines()[-1]))

        threading.Thread(target=worker, daemon=True).start()

    def _out_path(self, p) -> str:
        folder = os.path.dirname(self.app.var_output.get().strip()) or default_output_dir()
        os.makedirs(folder, exist_ok=True)
        safe = re.sub(r'[<>:"/\\|?*]+', "", p.name).strip() or "Promo"
        base = os.path.join(folder, f"{safe} promo ({tones.get(p.tone).label})")
        path, n = base + ".mp4", 2
        while os.path.exists(path):
            path, n = f"{base} {n}.mp4", n + 1
        return path

    @staticmethod
    def _poster(video: str, scenes) -> str:
        """The ending, fully settled, as a JPG beside the video (for thumbnails and posts)."""
        from vidgen import mastering

        last = scenes[-1]
        poster = os.path.splitext(video)[0] + " poster.jpg"
        try:
            mastering.run_ffmpeg(["-y", "-ss", f"{last.start + last.duration * 0.8:.2f}", "-i", video,
                                  "-frames:v", "1", "-q:v", "2", poster])
        except RuntimeError:
            return ""
        return poster

    def stop_making(self):
        self.stop.set()
        self.status.configure(text="Stopping...", text_color=TEXT_DIM)

    def _set_busy(self, busy):
        self.busy = busy
        self.make_button.configure(state="disabled" if busy else "normal")
        self.fill_button.configure(state="disabled" if busy else "normal")
        self.github_button.configure(state="disabled" if busy else "normal")
        self.stop_button.configure(state="normal" if busy else "disabled")

    def _poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    done, total, took = payload
                    self.progress.set(done / total)
                    left = took / done * (total - done) if done else 0
                    self.status.configure(text=f"Drawing frame {done} of {total} "
                                               f"(about {left:.0f} s left)...", text_color=TEXT_DIM)
                elif kind == "found":
                    self._set_busy(False)
                    self._found(payload)
                elif kind == "filled":
                    self._set_busy(False)
                    self._filled(*payload)
                elif kind == "fill_failed":
                    self._set_busy(False)
                    self.fill_status.configure(text=payload, text_color=ERR_COLOR)
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
            self.after(120, self._poll)

    def _made(self, out, poster, p):
        self.result = out
        self.progress.set(1)
        self.status.configure(text=f"Saved: {out}" + (f"\nPoster: {poster}" if poster else ""),
                              text_color=OK_COLOR)
        self.done_row.pack(fill="x", pady=(8, 0))
        text = f"{p.hook}\n\n{p.name}: {p.what}" + (f"\n{p.link}" if p.link else "")
        self.share.delete("1.0", "end")
        self.share.insert("1.0", text)
        self.share.pack(fill="x", pady=(8, 4))
        self.copy_button.pack(anchor="w")
        self.app.append_log(f"🎬 Promo video saved: {out}")

    def copy_share(self):
        self.clipboard_clear()
        self.clipboard_append(self.share.get("1.0", "end").strip())
        self.copy_button.configure(text="Copied")
        self.after(1500, lambda: self.copy_button.configure(text="Copy text"))

    def open_folder(self):
        if self.result:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self.result)])  # noqa: S603,S607

    def close(self):
        self.stop.set()
        self.destroy()
