"""The thumbnail editor. (Split out of ai_video_studio.py; that file still exports every name.)"""

from __future__ import annotations

from gui.base import *  # noqa: F401,F403  (the names these had in one file)
from gui.photo_windows import change_photo_window

# ---- moved from ai_video_studio.py, unchanged below ----


class ThumbEditor(ctk.CTkFrame):
    """Edit thumbnail: layers (a background picture, text, shapes, stickers and photos) drawn
    by vidgen.thumbedit on a big preview. Layers are moved, resized and turned with handles on
    the preview; Save keeps it in the project, and the Storyboard's card and the render use
    it."""

    SWATCHES = ("#ffffff", "#ffd400", "#ff2a2a", "#00e0ff", "#3ddc5a", "#000000")
    # (tool, the button's text): the drawing tools make a shape by dragging on the preview
    TOOLS = (("select", "↖\nSelect"), ("text", "T\nText"), ("brush", "✎\nBrush"),
             ("arrow", "→\nArrow"), ("circle", "○\nCircle"), ("box", "□\nBox"),
             ("stickers", "☺\nStickers"), ("photos", "⌕\nPhotos"),
             ("picture", "▣\nPicture"))
    HANDLE = 7   # half a handle's size on the preview, in pixels

    def __init__(self, master, app, folder, size, layers, backgrounds=(), on_save=None,
                 on_reset=None, on_close=None, title="Edit thumbnail"):
        """A part of a page: the Thumbnails page, or the Storyboard's Thumbnail tab.

        ``backgrounds``: (label, path) of pictures for the strip; ``on_save`` runs after Save;
        ``on_reset`` is Reset to automatic and ``on_close`` the Back button (None: no such
        button). The keys come from the app window (App._editor_key).
        """
        import copy

        from vidgen import thumbedit

        super().__init__(master, fg_color=MAIN_BG, corner_radius=0)
        self.app, self.folder, self.size = app, folder, size
        self.on_save, self.on_reset, self.on_close = on_save, on_reset, on_close
        self.title_text = title
        self.layers = layers
        self.saved = copy.deepcopy(layers)   # what Back compares with: unsaved changes?
        self.history = thumbedit.History(layers)
        self.selected = next((i for i, layer in enumerate(layers) if layer["kind"] == "text"), 0)
        self.tool = "select"
        self._render_job, self._photo, self._drag, self._drags = None, None, None, 0
        self._photo_query, self._photo_results, self._photo_note = "", [], ""
        # A short window (a 1366x768 laptop at 125%): the tools in two columns, so all fit.
        self.compact = app.winfo_height() / ctk.ScalingTracker.get_window_scaling(app) < 860
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_top()
        self._build_tools()
        self.canvas = tk.Canvas(self, bg=theme_color(SIDEBAR_BG), highlightthickness=0, bd=0)
        self.canvas.grid(row=1, column=1, sticky="nsew")
        self.canvas.bind("<Configure>", lambda _e: self._render_now())
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Double-Button-1>", lambda _e: self._focus_words())
        self.side = ctk.CTkScrollableFrame(self, width=270, fg_color=CARD_BG, corner_radius=0)
        self.side.grid(row=1, column=2, sticky="ns")
        self._build_strip()
        self.set_backgrounds(backgrounds)
        self._fill_side()

    # -- building ----------------------------------------------------------------------
    def _button(self, master, text, command, width=96, accent=False):
        return ctk.CTkButton(
            master, text=text, width=width, height=32, corner_radius=4,
            font=self.app.font_bold if accent else self.app.font_body,
            fg_color=ACCENT if accent else FIELD_BG,
            hover_color=ACCENT_HOVER if accent else CARD_HOVER,
            border_width=0 if accent else 1, border_color=FIELD_BORDER,
            text_color=ACCENT_TEXT if accent else TEXT, command=command)

    def _build_top(self):
        app = self.app
        bar = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=0)
        bar.grid(row=0, column=0, columnspan=3, sticky="ew")
        if self.on_close:
            self._button(bar, "\u2190  Back", self.close, 80).pack(side="left", padx=(12, 0))
        ctk.CTkLabel(bar, text=self.title_text, font=app.font_title,
                     text_color=TEXT).pack(side="left", padx=(16, 10), pady=10)
        self._button(bar, "Save", self.save, 96, accent=True).pack(side="right", padx=(0, 16))
        self.saved_note = ctk.CTkLabel(bar, text="", font=app.font_tiny, text_color=TEXT_DIM)
        self.saved_note.pack(side="right", padx=(0, 10))
        if self.on_reset:
            self._button(bar, "Reset to automatic", self.reset, 150).pack(side="right",
                                                                          padx=(0, 16))
        self._button(bar, "Redo", self.redo, 70).pack(side="right", padx=(0, 16))
        self._button(bar, "Undo", self.undo, 70).pack(side="right", padx=(0, 6))

    def _build_tools(self):
        tools = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=0, width=74)
        tools.grid(row=1, column=0, rowspan=2, sticky="ns")
        symbol = ctk.CTkFont(family=self.app.ui_family, size=19)
        columns = 2 if self.compact else 1
        self.tool_buttons = {}
        for at, (tool, text) in enumerate(self.TOOLS):
            mark, name = text.split("\n")
            row, column = divmod(at, columns)
            self.tool_buttons[tool] = ctk.CTkButton(
                tools, text=mark, width=48, height=34 if self.compact else 36, corner_radius=6,
                font=symbol, fg_color="transparent", hover_color=CARD_HOVER, text_color=TEXT,
                command=lambda t=tool: self.use_tool(t))
            self.tool_buttons[tool].grid(row=2 * row, column=column,
                                         padx=(8, 4) if column == 0 else (0, 8), pady=(6, 0))
            label = ctk.CTkLabel(tools, text=name, height=16, font=self.app.font_tiny,
                                 text_color=TEXT_DIM, cursor="hand2")
            label.grid(row=2 * row + 1, column=column)
            label.bind("<Button-1>", lambda _e, t=tool: self.use_tool(t))
        self._show_tool()

    def _build_strip(self):
        app = self.app
        strip = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=0)
        strip.grid(row=2, column=1, columnspan=2, sticky="ew")
        self.strip_note = ctk.CTkLabel(
            strip, text="Background: a scene's picture, or a file from your PC",
            font=app.font_tiny, text_color=TEXT_DIM)
        self.strip_note.pack(anchor="w", padx=12, pady=(8, 4))
        box = self._strip_box()
        self.strip = ctk.CTkScrollableFrame(strip, orientation="horizontal",
                                            height=box[1] + 34, fg_color="transparent")
        self.strip.pack(fill="x", padx=6, pady=(0, 8))

    def _strip_box(self):
        """A strip picture's size: smaller in a short window, so the preview keeps room."""
        if self.size[1] > self.size[0]:
            return (30, 54) if self.compact else (40, 70)
        return (80, 45) if self.compact else (112, 63)

    def set_backgrounds(self, backgrounds):
        """Fill the strip with (label, path) pictures; the Thumbnails page calls it again
        when a video's frames are ready."""
        from PIL import Image, ImageOps

        if not self.winfo_exists():
            return
        app, row = self.app, self.strip
        for child in row.winfo_children():
            child.destroy()
        box = self._strip_box()
        self._strip_images = []
        for label, path in backgrounds:
            try:
                with Image.open(path) as opened:
                    small = ImageOps.fit(opened.convert("RGB"), (box[0] * 2, box[1] * 2))
            except OSError:
                continue
            image = ctk.CTkImage(light_image=small, dark_image=small, size=box)
            self._strip_images.append(image)
            ctk.CTkButton(row, image=image, text=label, compound="top",
                          width=box[0] + 8, height=box[1] + 30, corner_radius=4,
                          font=app.font_tiny, fg_color="transparent", hover_color=CARD_HOVER,
                          text_color=TEXT_DIM,
                          command=lambda p=path: self.set_background(p)).pack(side="left",
                                                                              padx=2)
        self._button(row, "From a file...", self.pick_background, 120).pack(side="left",
                                                                           padx=8)

    # -- tools -------------------------------------------------------------------------
    HINTS = {
        "select": "Drag a layer to move it (it snaps to the middle), its corners to resize it, "
                  "the round handle to turn it. Arrow keys nudge it.",
        "brush": "Draw on the picture.",
        "arrow": "Drag from where the arrow starts to where it points.",
        "circle": "Drag across what to circle.",
        "box": "Drag across what to frame.",
        "stickers": "Click a sticker to add it.",
        "photos": "Search Pexels and Pixabay photos, then click one to add it.",
    }

    def use_tool(self, tool):
        if tool == "text":
            self.add_text()
            tool = "select"
        elif tool == "picture":
            self.pick_picture()
            tool = "select"
        self.tool = tool
        self._show_tool()
        self._fill_side()

    def _show_tool(self):
        for tool, button in self.tool_buttons.items():
            button.configure(fg_color=NAV_SELECTED if tool == self.tool else "transparent")
        if hasattr(self, "canvas"):
            self._schedule(1)   # the hint under the picture follows the tool
            self.canvas.configure(cursor="crosshair" if self.tool in ("brush", "arrow",
                                                                      "circle", "box") else "")

    # -- the side panel: layers, then the selected layer's options ---------------------
    def _fill_side(self):
        for child in self.side.winfo_children():
            child.destroy()
        if self.tool == "stickers":
            self._sticker_panel()
            return
        if self.tool == "photos":
            self._photo_panel()
            return
        self._layers_panel()

    def _layers_panel(self):
        from vidgen import thumbedit

        app = self.app
        ctk.CTkLabel(self.side, text="Layers", font=app.font_bold, text_color=TEXT,
                     anchor="w").pack(fill="x", padx=8, pady=(10, 4))
        for at in range(len(self.layers) - 1, -1, -1):   # the top layer first
            layer = self.layers[at]
            row = ctk.CTkFrame(self.side, fg_color=NAV_SELECTED if at == self.selected
                               else "transparent", corner_radius=4)
            row.pack(fill="x", padx=4, pady=1)
            ctk.CTkButton(row, text=thumbedit.label(layer), anchor="w", height=30,
                          font=app.font_body, fg_color="transparent", hover_color=CARD_HOVER,
                          text_color=TEXT_DIM if layer.get("hidden") else TEXT,
                          command=lambda a=at: self.select(a)).pack(side="left", fill="x",
                                                                    expand=True)
            ctk.CTkButton(row, text="Show" if layer.get("hidden") else "Hide", width=50,
                          height=26, font=app.font_tiny, fg_color="transparent",
                          hover_color=CARD_HOVER, text_color=TEXT_DIM,
                          command=lambda a=at: self.toggle_hidden(a)).pack(side="right",
                                                                           padx=2)
        moves = ctk.CTkFrame(self.side, fg_color="transparent")
        moves.pack(fill="x", padx=8, pady=(6, 0))
        for text, command in (("Up", lambda: self.move(1)), ("Down", lambda: self.move(-1)),
                              ("Delete", self.delete)):
            self._button(moves, text, command, 70).pack(side="left", padx=(0, 6))
        ctk.CTkFrame(self.side, fg_color=DIVIDER, height=1).pack(fill="x", padx=8, pady=12)
        layer = self.layers[self.selected] if self.selected < len(self.layers) else None
        if layer is None or layer["kind"] == "background":
            ctk.CTkLabel(self.side, text="The background: pick a scene's picture below, or a "
                                         "file from your PC. Add words, shapes, stickers "
                                         "and photos with the tools on the left.",
                         font=app.font_body, text_color=TEXT_MUTED, wraplength=240,
                         justify="left", anchor="w").pack(fill="x", padx=8)
            if layer is not None:
                self._change_words_button()
            if layer is not None:   # blur or darken it so the words stand out
                for key, text, low, high in (("blur", "Blur", 0, 30),
                                             ("darken", "Darken", 0, 80),
                                             ("brightness", "Brightness", -50, 50),
                                             ("contrast", "Contrast", -50, 50)):
                    self._label(text)
                    self._slider(key, low, high, layer)
        elif layer["kind"] == "text":
            self._text_options(layer)
        elif layer["kind"] == "shape":
            self._label("Colour")
            self._swatches("color", layer, self.SWATCHES)
            self._label("Thickness")
            self._slider("width", 2, 40, layer)
            self._label("Tilt")
            self._slider("tilt", -180, 180, layer)
        else:
            self._label("Tilt")
            self._slider("tilt", -180, 180, layer)
            self._label("Outline")
            self._slider("outline", 0, 20, layer)
            self._swatches("outline_color", layer, ("#ffffff", "#000000", "#ffd400"), "#ffffff")
            self._glow(layer)
            if layer["kind"] == "image":
                self._button(self.side, "Use as the background", self.to_background,
                             200).pack(anchor="w", padx=8, pady=(14, 0))
                self._change_words_button()
            if layer.get("credit"):
                ctk.CTkLabel(self.side, text=layer["credit"], font=app.font_tiny,
                             text_color=TEXT_DIM, wraplength=240, justify="left",
                             anchor="w").pack(fill="x", padx=8, pady=(10, 0))

    def _sticker_panel(self):
        from vidgen import thumbedit

        app = self.app
        ctk.CTkLabel(self.side, text="Stickers", font=app.font_bold, text_color=TEXT,
                     anchor="w").pack(fill="x", padx=8, pady=(10, 6))
        grid = ctk.CTkFrame(self.side, fg_color="transparent")
        grid.pack(fill="x", padx=4)
        if not hasattr(self, "_sticker_images"):
            self._sticker_images = {}
            for emoji in thumbedit.STICKERS:
                drawn = thumbedit._emoji(emoji)
                fit = 36 / max(drawn.size)
                shown = (max(int(drawn.width * fit), 1), max(int(drawn.height * fit), 1))
                self._sticker_images[emoji] = ctk.CTkImage(light_image=drawn,
                                                           dark_image=drawn, size=shown)
        for at, emoji in enumerate(thumbedit.STICKERS):
            ctk.CTkButton(grid, text="", image=self._sticker_images[emoji], width=48, height=48,
                          corner_radius=6, fg_color="transparent", hover_color=CARD_HOVER,
                          command=lambda e=emoji: self.add_sticker(e)).grid(
                row=at // 5, column=at % 5, padx=1, pady=1)

    def _photo_panel(self):
        app = self.app
        ctk.CTkLabel(self.side, text="Photos", font=app.font_bold, text_color=TEXT,
                     anchor="w").pack(fill="x", padx=8, pady=(10, 6))
        row = ctk.CTkFrame(self.side, fg_color="transparent")
        row.pack(fill="x", padx=8)
        self.photo_entry = focus_accent(ctk.CTkEntry(
            row, height=32, corner_radius=4, font=app.font_body, fg_color=FIELD_BG,
            border_color=FIELD_BORDER, text_color=TEXT, placeholder_text="coffee beans"))
        if self._photo_query:
            self.photo_entry.insert(0, self._photo_query)
        self.photo_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.photo_entry.bind("<Return>", lambda _e: self.search_photos())
        self._button(row, "Search", self.search_photos, 70).pack(side="left")
        self.photo_note = ctk.CTkLabel(self.side, text=self._photo_note, font=app.font_tiny,
                                       text_color=TEXT_DIM, wraplength=240, justify="left",
                                       anchor="w")
        self.photo_note.pack(fill="x", padx=8, pady=(6, 4))
        grid = ctk.CTkFrame(self.side, fg_color="transparent")
        grid.pack(fill="x", padx=4)
        for at, (candidate, image) in enumerate(self._photo_results):
            ctk.CTkButton(grid, text="", image=image, width=122, height=80, corner_radius=4,
                          fg_color="transparent", hover_color=CARD_HOVER,
                          command=lambda c=candidate: self.add_photo(c)).grid(
                row=at // 2, column=at % 2, padx=1, pady=1)
        if self._photo_results:
            ctk.CTkLabel(self.side, text="Photos from Pexels and Pixabay. Their credit is "
                                         "saved with the video's footage credits.",
                         font=app.font_tiny, text_color=TEXT_DIM, wraplength=240,
                         justify="left", anchor="w").pack(fill="x", padx=8, pady=(6, 10))

    def _label(self, text):
        ctk.CTkLabel(self.side, text=text, font=self.app.font_tiny, text_color=TEXT_DIM,
                     anchor="w").pack(fill="x", padx=8, pady=(10, 2))

    def _slider(self, key, low, high, layer):
        row = ctk.CTkFrame(self.side, fg_color="transparent")
        row.pack(fill="x", padx=8)
        shown = ctk.CTkLabel(row, text=str(layer.get(key, 0)), width=36, font=self.app.font_body,
                             text_color=TEXT)
        shown.pack(side="right")

        def moved(value):
            shown.configure(text=str(int(value)))
            self.change(key, int(value))

        slider = ctk.CTkSlider(row, from_=low, to=high, number_of_steps=high - low,
                               command=moved, button_color=ACCENT, progress_color=ACCENT)
        slider.set(layer.get(key, 0))
        slider.pack(side="left", fill="x", expand=True)

    def _glow(self, layer):
        self._label("Glow")
        self._slider("glow", 0, 40, layer)
        self._swatches("glow_color", layer, ("#ffffff", "#ffd400", "#ff2a2a", "#00e0ff"),
                       "#ffffff")

    def _swatches(self, key, layer, colors, default="#ffffff"):
        from tkinter import colorchooser

        row = ctk.CTkFrame(self.side, fg_color="transparent")
        row.pack(fill="x", padx=8)
        for color in colors:
            ctk.CTkButton(row, text="", width=26, height=26, corner_radius=4, fg_color=color,
                          hover_color=color, border_width=2,
                          border_color=ACCENT if layer.get(key, default).lower() == color
                          else FIELD_BORDER,
                          command=lambda c=color: self.change(key, c, refill=True)).pack(
                side="left", padx=(0, 5))

        def more():
            chosen = colorchooser.askcolor(layer.get(key, default), parent=self)[1]
            if chosen:
                self.change(key, chosen.lower(), refill=True)

        self._button(row, "More", more, 50).pack(side="left")

    def _text_options(self, layer):
        from vidgen import thumbedit

        app = self.app
        self._label("Words")
        self.words = ctk.CTkTextbox(self.side, height=72, font=app.font_body, fg_color=FIELD_BG,
                                    text_color=TEXT, border_width=1, border_color=FIELD_BORDER,
                                    wrap="word")
        self.words.insert("1.0", layer["text"])
        self.words.pack(fill="x", padx=8)
        self.words.bind("<KeyRelease>", lambda _e: self.change(
            "text", self.words.get("1.0", "end-1c"), relabel=True))
        self._label("Font")
        var = tk.StringVar(value=layer["font"])
        ctk.CTkOptionMenu(self.side, values=list(thumbedit.FONTS), variable=var, height=30,
                          corner_radius=4, font=app.font_body, fg_color=FIELD_BG,
                          button_color=FIELD_BG, button_hover_color=CARD_HOVER, text_color=TEXT,
                          dropdown_fg_color=CARD_BG, dropdown_text_color=TEXT,
                          dropdown_hover_color=CARD_HOVER, dynamic_resizing=False,
                          command=lambda name: self.change("font", name)).pack(fill="x", padx=8)
        self._label("Size")
        self._slider("size", 24, 320, layer)
        self._label("Colour")
        self._swatches("color", layer, self.SWATCHES)
        self._label("Outline")
        self._slider("outline", 0, 30, layer)
        self._swatches("outline_color", layer, ("#000000", "#ffffff", "#ff2a2a"))
        self._label("Tilt")
        self._slider("tilt", -45, 45, layer)
        self._glow(layer)
        for key, text in (("shadow", "Shadow"), ("band", "Dark band behind")):
            switch = ctk.CTkSwitch(self.side, text=text, font=app.font_body, text_color=TEXT,
                                   progress_color=ACCENT,
                                   command=lambda k=key: self.change(k, not self.layers[
                                       self.selected][k]))
            if layer[key]:
                switch.select()
            switch.pack(anchor="w", padx=8, pady=(12, 0))

    # -- changes -----------------------------------------------------------------------
    def change(self, key, value, refill=False, relabel=False):
        layer = self.layers[self.selected]
        if layer.get(key) == value:
            return
        layer[key] = value
        self.history.record(self.layers, key=(key, self.selected))
        if refill:
            self._fill_side()
        elif relabel:
            self._relabel()
        self._schedule()

    def _relabel(self):
        """The layer's name in the list follows its words, without rebuilding the panel
        (which would take the focus away from the words box)."""
        from vidgen import thumbedit

        rows = [child for child in self.side.winfo_children()
                if isinstance(child, ctk.CTkFrame)][:len(self.layers)]
        row = rows[len(self.layers) - 1 - self.selected]
        row.winfo_children()[0].configure(text=thumbedit.label(self.layers[self.selected]))

    def _added(self, layer):
        """A new layer on top, selected, with the Select tool back."""
        self.layers.append(layer)
        self.selected = len(self.layers) - 1
        self.history.record(self.layers)
        self.tool = "brush" if self.tool == "brush" else "select"
        self._show_tool()
        self._fill_side()
        self._schedule()

    def _apply(self, layers):
        self.layers[:] = layers
        self.selected = min(self.selected, len(self.layers) - 1)
        self._fill_side()
        self._schedule()

    def undo(self):
        layers = self.history.undo()
        if layers is not None:
            self._apply(layers)

    def redo(self):
        layers = self.history.redo()
        if layers is not None:
            self._apply(layers)

    def select(self, at):
        self.selected = at
        if self.tool != "select":
            self.tool = "select"
            self._show_tool()
        self._fill_side()
        self._schedule()

    def add_text(self):
        from vidgen import thumbedit

        self.tool = "select"
        self._added(thumbedit.new_text(y=0.3, size=int(self.size[0] * 0.08)))
        self.after(50, self._focus_words)

    def add_sticker(self, emoji):
        from vidgen import thumbedit

        self._added(thumbedit.new_sticker(emoji, self.size))

    def toggle_hidden(self, at):
        self.layers[at]["hidden"] = not self.layers[at].get("hidden")
        self.history.record(self.layers)
        self._fill_side()
        self._schedule()

    def move(self, step):
        """Up (+1) or down (-1) in the stack; the background stays at the bottom."""
        at, to = self.selected, self.selected + step
        if self.layers[at]["kind"] == "background" or not 1 <= to < len(self.layers):
            return
        self.layers[at], self.layers[to] = self.layers[to], self.layers[at]
        self.selected = to
        self.history.record(self.layers)
        self._fill_side()
        self._schedule()

    def delete(self):
        if self.layers[self.selected]["kind"] == "background":
            return
        del self.layers[self.selected]
        self.selected = max(self.selected - 1, 0)
        self.history.record(self.layers)
        self._fill_side()
        self._schedule()

    def set_background(self, path):
        if self.layers and self.layers[0]["kind"] == "background":
            self.layers[0] = dict(self.layers[0], path=path, hidden=False)
        else:
            self.layers.insert(0, {"kind": "background", "path": path, "hidden": False})
            self.selected += 1
        self.history.record(self.layers)
        self._fill_side()
        self._schedule()

    def _change_words_button(self):
        self._button(self.side, "Change with words...", self.change_with_words,
                     200).pack(anchor="w", padx=8, pady=(10, 0))

    def change_with_words(self):
        """The selected picture changed by the Image editor; Undo puts the old one back."""
        at = self.selected
        path = self.layers[at].get("path")
        if not path or not os.path.isfile(path):
            return

        def use(new):
            if at < len(self.layers) and self.layers[at].get("path") == path:
                self.selected = at
                self.change("path", new)
                self._fill_side()
        change_photo_window(self.app, path, "", use, "Change the picture")

    def to_background(self):
        """The selected picture becomes the background, filling the thumbnail."""
        path = self.layers[self.selected]["path"]
        del self.layers[self.selected]
        self.selected = 0
        self.set_background(path)

    def _ask_picture(self, title):
        return filedialog.askopenfilename(
            parent=self, title=title,
            filetypes=[("Pictures", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*")])

    def pick_background(self):
        chosen = self._ask_picture("Background picture for the thumbnail")
        if chosen:
            self.set_background(chosen)

    def pick_picture(self):
        from vidgen import thumbedit

        chosen = self._ask_picture("Picture to put on the thumbnail")
        if chosen:
            self._added(thumbedit.new_image(chosen, self.size))

    def _focus_words(self):
        words = getattr(self, "words", None)
        if words is not None and words.winfo_exists():
            words.focus_set()
            words.tag_add("sel", "1.0", "end-1c")

    # -- photos from Pexels and Pixabay ------------------------------------------------
    def search_photos(self):
        query = self.photo_entry.get().strip()
        pexels, pixabay = self.app.var_api.get().strip(), self.app.var_pixabay.get().strip()
        if not query:
            self._note_photos("Type what the photo should show.")
            return
        if not (pexels or pixabay):
            self._note_photos("Add a Pexels or Pixabay key in Settings > Footage keys first. "
                              "Both are free.")
            return
        self._photo_query = query
        self._note_photos("Searching...")
        orientation = "portrait" if self.size[1] > self.size[0] else "landscape"
        quality = self.app.var_resolution.get()

        if not hasattr(self, "_http"):
            import requests

            # One connection kept open for all the small pictures: a new one each took up
            # to 8 s (measured: 1.8 s for 30 pictures instead of 3 to 9 s).
            self._http = requests.Session()
            self._http.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=8))
        http = self._http

        def work():
            search = footage.FootageSearch(pexels, pixabay, self.size[0], self.size[1],
                                           orientation, quality, SEARCH_CACHE_DIR,
                                           lambda _message: None, session=http)
            try:
                found = search.photos(query)
            except Exception as exc:  # noqa: BLE001 - shown in the panel
                found, problem = [], str(exc)
            else:
                problem = "; ".join(f"{name}: {why}" for name, why in search.disabled.items())
            # 8 at a time: one by one, a few slow ones took 45 s.
            with concurrent.futures.ThreadPoolExecutor(8) as pool:
                paths = list(pool.map(lambda c: scenes.fetch_thumb(c, THUMB_DIR, http), found))
            shown = list(zip(found, paths))
            self.app.call_soon(lambda: self._photos_found(query, shown, problem))

        threading.Thread(target=work, daemon=True).start()

    def _photos_found(self, query, shown, problem):
        from PIL import Image, ImageOps

        if not self.winfo_exists() or query != self._photo_query:
            return
        self._photo_results = []
        for candidate, path in shown:
            try:
                with Image.open(path) as opened:
                    small = ImageOps.fit(opened.convert("RGB"), (232, 148))
            except (OSError, TypeError, AttributeError):
                continue
            self._photo_results.append(
                (candidate, ctk.CTkImage(light_image=small, dark_image=small, size=(116, 74))))
        self._photo_note = (f"Nothing found for \"{query}\". Try other words."
                            if not self._photo_results else "")
        if problem:
            self._photo_note = (self._photo_note + " " + problem).strip()
        if self.tool == "photos":
            self._fill_side()

    def _note_photos(self, text):
        self._photo_note = text
        note = getattr(self, "photo_note", None)
        if note is not None and note.winfo_exists():
            note.configure(text=text)

    def add_photo(self, candidate):
        """Download the big photo into the project and put it on the thumbnail."""
        from vidgen import thumbedit

        self._note_photos("Getting the photo...")
        dest = os.path.join(self.folder, thumbedit.FILES,
                            f"{candidate.provider}-{candidate.id}.jpg".lower())

        def work():
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                if not os.path.isfile(dest):
                    footage.download(candidate.url, dest, getattr(self, "_http", None))
                problem = None
            except Exception as exc:  # noqa: BLE001 - shown in the panel
                problem = f"Couldn't get the photo ({exc.__class__.__name__}). Try another."
            self.app.call_soon(lambda: self._photo_ready(candidate, dest, problem))

        threading.Thread(target=work, daemon=True).start()

    def _photo_ready(self, candidate, dest, problem):
        from vidgen import thumbedit

        if not self.winfo_exists():
            return
        if problem:
            self._note_photos(problem)
            return
        self._photo_note = ""
        self._added(thumbedit.new_image(dest, self.size, candidate.credit()))

    # -- the preview -------------------------------------------------------------------
    def _schedule(self, delay=80):
        if self._render_job:
            self.after_cancel(self._render_job)
        self._render_job = self.after(delay, self._render_now)

    def _place(self):
        """(scale, left, top) of the picture on the canvas."""
        cw, ch = max(self.canvas.winfo_width(), 50), max(self.canvas.winfo_height(), 50)
        scale = min((cw - 48) / self.size[0], (ch - 48) / self.size[1])
        return scale, (cw - self.size[0] * scale) / 2, (ch - self.size[1] * scale) / 2

    def _frame(self):
        """The selected layer's box on the canvas, or None: (left, top, right, bottom)."""
        from vidgen import thumbedit

        layer = self.layers[self.selected] if self.selected < len(self.layers) else None
        if not layer or layer["kind"] == "background" or layer.get("hidden"):
            return None
        scale, left, top = self._place()
        x0, y0, x1, y1 = thumbedit.box(layer, self.size)
        return (left + x0 * scale - 3, top + y0 * scale - 3, left + x1 * scale + 3,
                top + y1 * scale + 3)

    def _handles(self):
        """{name: (x, y)} of the selected layer's handles: four corners and "turn"."""
        frame = self._frame()
        if frame is None:
            return {}
        x0, y0, x1, y1 = frame
        return {"nw": (x0, y0), "ne": (x1, y0), "sw": (x0, y1), "se": (x1, y1),
                "turn": ((x0 + x1) / 2, y0 - 24)}

    def _render_now(self):
        from PIL import ImageTk
        from vidgen import thumbedit

        self._render_job = None
        scale, left, top = self._place()
        if scale <= 0:
            return
        self._photo = ImageTk.PhotoImage(thumbedit.render(self.layers, self.size, scale))
        self.canvas.delete("all")
        self.canvas.create_image(left, top, image=self._photo, anchor="nw")
        # What the tool does, under the picture: the top bar has no room on a laptop.
        self.canvas.create_text(left, top + self.size[1] * scale + 6, anchor="nw",
                                text=self.HINTS.get(self.tool, ""), fill=theme_color(TEXT_DIM),
                                font=(self.app.ui_family, -12))
        frame = self._frame()
        if frame is None:
            return
        accent, h = theme_color(ACCENT), self.HANDLE
        layer = self.layers[self.selected]
        if self._drag and "move" in self._drag:   # the centre lines it snapped to
            w, hh = self.size[0] * scale, self.size[1] * scale
            if layer["x"] == 0.5:
                self.canvas.create_line(left + w / 2, top, left + w / 2, top + hh,
                                        fill="#ff3ec8", width=1)
            if layer["y"] == 0.5:
                self.canvas.create_line(left, top + hh / 2, left + w, top + hh / 2,
                                        fill="#ff3ec8", width=1)
        self.canvas.create_rectangle(*frame, outline=accent, width=2, dash=(6, 4))
        handles = self._handles()
        tx, ty = handles.pop("turn")
        self.canvas.create_line(tx, ty, tx, frame[1], fill=accent, width=2)
        self.canvas.create_oval(tx - h, ty - h, tx + h, ty + h, fill="white", outline=accent,
                                width=2)
        for x, y in handles.values():
            self.canvas.create_rectangle(x - h, y - h, x + h, y + h, fill="white",
                                         outline=accent, width=2)

    @staticmethod
    def _typing(event):
        """Whether a key went to a text box (the words, the photo search)."""
        return isinstance(event.widget, (tk.Entry, tk.Text))

    def _nudge(self, event, step, pixels):
        if self._typing(event) or self.layers[self.selected]["kind"] == "background":
            return
        layer = self.layers[self.selected]
        layer["x"] = min(max(layer["x"] + step[0] * pixels / self.size[0], 0.0), 1.0)
        layer["y"] = min(max(layer["y"] + step[1] * pixels / self.size[1], 0.0), 1.0)
        self.history.record(self.layers, key=("nudge", self.selected))
        self._schedule(15)
        return "break"

    def reset(self):
        """Back to the automatic thumbnail: the saved edit is removed (on_reset)."""
        Dialog(self.app, "Reset to automatic?", "Your edits to this thumbnail are removed and "
                                               "the app makes it from the scenes again.",
               ok=False, confirm=self.on_reset, confirm_text="Reset")

    def _point(self, event):
        scale, left, top = self._place()
        return (event.x - left) / scale, (event.y - top) / scale

    def _press(self, event):
        from vidgen import thumbedit

        self._drags += 1
        if self.tool in ("brush", "arrow", "circle", "box"):
            self._drag = {"draw": self.tool, "from": (event.x, event.y),
                          "points": [(event.x, event.y)]}
            return
        for name, (x, y) in self._handles().items():
            if abs(event.x - x) <= self.HANDLE + 3 and abs(event.y - y) <= self.HANDLE + 3:
                frame = self._frame()
                centre = ((frame[0] + frame[2]) / 2, (frame[1] + frame[3]) / 2)
                self._drag = {"handle": name, "centre": centre, "from": (event.x, event.y),
                              "layer": dict(self.layers[self.selected]), "id": self._drags}
                return
        point = self._point(event)
        at = thumbedit.hit(self.layers, self.size, point)
        if at is None:
            self._drag = None
            if self.selected != 0:
                self.select(0)
            return
        if at != self.selected:
            self.select(at)
        layer = self.layers[at]
        self._drag = {"move": (point[0] - layer["x"] * self.size[0],
                               point[1] - layer["y"] * self.size[1]), "id": self._drags}

    def _motion(self, event):
        from vidgen import thumbedit

        drag = self._drag
        if not drag:
            return
        if "draw" in drag:
            self._draft(drag, event)
            return
        layer = self.layers[self.selected]
        if "move" in drag:
            dx, dy = drag["move"]
            x, y = self._point(event)
            near = 8 / self._place()[0]   # 8 pixels on the screen, in full-size pixels
            layer["x"] = thumbedit.snapped(min(max((x - dx) / self.size[0], 0.0), 1.0),
                                           self.size[0], near)
            layer["y"] = thumbedit.snapped(min(max((y - dy) / self.size[1], 0.0), 1.0),
                                           self.size[1], near)
        elif drag["handle"] == "turn":
            cx, cy = drag["centre"]
            start = math.atan2(drag["from"][1] - cy, drag["from"][0] - cx)
            now = math.atan2(event.y - cy, event.x - cx)
            layer["tilt"] = thumbedit.turned(drag["layer"].get("tilt", 0)
                                             - math.degrees(now - start))
        else:
            cx, cy = drag["centre"]
            start = math.hypot(drag["from"][0] - cx, drag["from"][1] - cy) or 1
            ratio = math.hypot(event.x - cx, event.y - cy) / start
            layer.update(thumbedit.resize(dict(drag["layer"]), max(ratio, 0.05)))
        self.history.record(self.layers, key=("drag", drag["id"]))
        self._schedule(15)

    def _draft(self, drag, event):
        """The shape being drawn, as a plain outline until the mouse is let go."""
        self.canvas.delete("draft")
        (x0, y0), red = drag["from"], "#ff2a2a"
        if drag["draw"] == "brush":
            drag["points"].append((event.x, event.y))
            if len(drag["points"]) > 1:
                self.canvas.create_line(*[v for p in drag["points"] for v in p], fill=red,
                                        width=4, smooth=True, capstyle="round", tags="draft")
        elif drag["draw"] == "arrow":
            self.canvas.create_line(x0, y0, event.x, event.y, fill=red, width=4,
                                    arrow="last", arrowshape=(16, 20, 7), tags="draft")
        else:
            make = self.canvas.create_oval if drag["draw"] == "circle" \
                else self.canvas.create_rectangle
            make(x0, y0, event.x, event.y, outline=red, width=4, tags="draft")
        drag["to"] = (event.x, event.y)

    def _release(self, event):
        from vidgen import thumbedit

        drag, self._drag = self._drag, None
        if not drag or "draw" not in drag:
            if drag and "move" in drag:
                self._schedule(1)   # without the centre lines
            return
        self.canvas.delete("draft")
        scale, left, top = self._place()
        full = lambda p: ((p[0] - left) / scale, (p[1] - top) / scale)
        (ax, ay), (bx, by) = full(drag["from"]), full(drag.get("to", drag["from"]))
        W, H = self.size
        shape, width = drag["draw"], max(int(min(self.size) * 0.02), 6)
        if shape == "brush":
            points = [full(p) for p in drag["points"]]
            if len(points) < 2:
                return
            layer = thumbedit.brush(points, self.size, width)
        elif shape == "arrow":
            length = math.hypot(bx - ax, by - ay)
            if length < 20:   # a click: a ready-made arrow pointing at it
                ax, ay, length = bx - 0.2 * W, by + 0.1 * H, math.hypot(0.2 * W, 0.1 * H)
            layer = thumbedit.new_shape(
                "arrow", (ax + bx) / 2 / W, (ay + by) / 2 / H, length / W, 0.05, width=width,
                tilt=thumbedit.turned(-math.degrees(math.atan2(by - ay, bx - ax))))
        else:
            w, h = abs(bx - ax), abs(by - ay)
            if w < 20 or h < 20:   # a click: a ready-made one around it
                ax, ay, w, h = bx - 0.12 * W, by - 0.12 * W, 0.24 * W, 0.24 * W
                bx, by = ax + w, ay + h
            layer = thumbedit.new_shape(shape, (ax + bx) / 2 / W, (ay + by) / 2 / H, w / W,
                                        h / H, width=width)
        self._added(layer)

    # -- keeping it --------------------------------------------------------------------
    def save(self):
        from vidgen import thumbedit

        try:
            thumbedit.save(self.folder, self.layers, self.size)
        except OSError as exc:
            Dialog(self.app, "Couldn't save the thumbnail", str(exc), ok=False)
            return
        import copy

        self.saved = copy.deepcopy(self.layers)
        if self.on_save:
            self.on_save()
        if self.winfo_exists():
            self.saved_note.configure(text="Saved " + time.strftime("%H:%M"))

    def changed(self) -> bool:
        return self.layers != self.saved

    def close(self):
        """Back: asks first when there are changes that are not saved."""
        if not self.changed():
            self.on_close()
            return
        Dialog(self.app, "Leave without saving?", "Your changes to the thumbnail are lost.",
               ok=False, confirm=self.on_close, confirm_text="Leave",
               cancel_text="Keep editing")
