# -*- coding: utf-8 -*-
"""
AI Video Studio - Fluent Edition
================================
The window: a Windows 11 / "Wintoys"-style shell around the video engine.

This file is the interface - settings, pages, window effects. The engine
(script parser, footage search, motion, music, rendering) lives in the
``vidgen`` package beside it.

  * Fluent-style shell: near-black sidebar, lighter content area, rounded cards.
  * Pages: Home / My videos / Storyboard / Settings (tabs, incl. AI pictures, Help, Advanced).
  * Settings are persisted to a small JSON file, including the API keys, which
    are never in the source.
  * Mica / acrylic translucency + dark title bar on Windows 11.
  * Thread-safe UI: the render thread never touches a widget.  It pushes
    messages onto a queue that the main thread drains from a window.after()
    loop.

Requirements
------------
    pip install -r requirements.txt

Run
---
    python src/ai_video_studio.py
"""

# ---- moved from ai_video_studio.py, imports ----
from __future__ import annotations

from gui.base import *  # noqa: F401,F403  (setup, constants, settings, widgets)
from gui.tips import TipPicker, GuideBox, TipDialog, TipSession  # noqa: F401
from gui.dialogs import WelcomeDialog, WhatsNewDialog, TourDialog, SlowPicturesDialog, ClipQuestion, BatchDialog, MyVoiceDialog, TranslateDialog, CopyDialog, ResultDialog, ReportDialog  # noqa: F401
from gui.photo_windows import ImageEditor, PhotoList, change_photo_window, DraftDialog  # noqa: F401
from gui.thumb_editor import ThumbEditor  # noqa: F401
from gui.base import BUILD, CLOSING_WAIT, FlipDialog, HOLD_WAIT, KEY_PROVIDERS, RECORDINGS_DIR, TOUR, WHATS_NEW, WRITER_MODES, centre_on, mci, report_tip_failure, short_path, show_whats_new, writer_size  # noqa: F401
# ---- moved from ai_video_studio.py, unchanged below ----


# =============================================================================
#  SECTION 7 - APPLICATION SHELL
# =============================================================================


class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self._launched = time.perf_counter()
        self.session_log = diagnostics.SessionLog(LOG_FILE)
        self.recent_lines = collections.deque(maxlen=300)   # what a report quotes from
        self.settings = load_settings()
        ctk.set_appearance_mode(self.settings["theme"])
        ctk.set_default_color_theme("blue")

        self.title(f"{APP_NAME}")
        icon_path = asset_path("icon.ico")
        if icon_path:
            try:
                self.iconbitmap(icon_path)
            except Exception:
                pass
        self.geometry("1180x780")
        # Fits a 1366x768 laptop at 125% (1093x614 left for the window).
        self.minsize(900, 560)
        self.wide = None   # extra panels on (WIDE or more), set by _on_resize
        self.configure(fg_color=MAIN_BG)

        self._queue: queue.Queue = queue.Queue()
        self.ui = UiBridge(self._queue)
        self.is_rendering = False
        self._previewing = False
        self.sidebar_collapsed = False
        self.effect_note = ""
        self._refresh_job = None
        self.current_page = None
        self._page_anim = None
        self._key_tests = {}   # token -> callback(ok, message)
        self._cancel = None    # threading.Event of the render in progress
        self.board = None          # the Storyboard's scenes (vidgen.board.Board)
        self.board_text = None     # the script the Storyboard was last built from
        self.board_clips = {}      # search words -> stock clips found for them
        self.board_pos = {}        # search words -> which of those clips is chosen
        self.board_pictures = {}   # AI picture key -> the file made for it
        self.board_marked = set()  # scenes the last "Change it" rewrote
        self.board_history, self.board_future = [], []   # Storyboard Undo / Redo
        self.board_cards, self.board_images = [], {}
        self.board_paths = {}      # scene index -> the picture its card shows (for the panel)
        self.thumb = {"scene": None, "title": ""}   # the thumbnail: which scene, which words
        self._thumb_job, self._thumb_image, self._timeline_pictures = None, None, {}
        self.board_gen = 0         # goes up on every rebuild; late worker news is dropped
        self._revising = False
        self.source_text = ""      # the text the script in the editor was written from
        self._after_draft = "board"   # or "render": what New from text does when used
        self._slow_ok = None       # the script a slow processor render was agreed to for
        self._last_draft = None    # the last script "New from text" put in the editor
        self.writer_listeners = []     # callables told how the model download is going
        self.writer_downloading = False
        self.voice_downloading = False
        self.images_downloading = False
        self._images_stop = threading.Event()
        self._images_confirm = False
        self._making_picture = False
        self._try_image = None   # kept so Tk doesn't drop the picture
        self._writer_stop = threading.Event()

        self._build_fonts()
        self._build_vars()
        self._build_shell()
        self._build_pages()
        self.select_page("home")
        self._set_wide(False)
        self.bind("<Configure>", self._on_resize, add="+")
        self._restore_window(self.settings["window"])

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(120, self._post_init)
        self.after(60, self._drain_queue)

    # -- window size: maximized by default, extra panels when wide -----------
    def _restore_window(self, saved):
        if saved == "zoomed":
            self.after(0, lambda: self.state("zoomed"))
        else:
            self.geometry(saved)

    def _window_now(self) -> str:
        """"zoomed", or the window's size in the units geometry() takes."""
        try:
            if self.state() == "zoomed":
                return "zoomed"
            scale = ctk.ScalingTracker.get_window_scaling(self)
            return f"{round(self.winfo_width() / scale)}x{round(self.winfo_height() / scale)}"
        except tk.TclError:
            return self.settings.get("window", "zoomed")

    def _on_resize(self, event):
        if event.widget is not self:
            return
        size = (event.width, event.height)
        if size == getattr(self, "_last_size", None):
            return
        first = getattr(self, "_last_size", None) is None
        self._last_size = size
        if first or self.current_page is None:
            if self.current_page is not None:   # opening maximized: the side panels show
                self.after_idle(self._resized)
            return
        # customtkinter redraws every rounded widget on each step of a drag (CTk issue #2617):
        # the pages are taken off screen behind a plain panel until the drag stops.
        if getattr(self, "_resize_job", None):
            self.after_cancel(self._resize_job)
        elif self.content.winfo_manager():
            self.content.grid_remove()
            self._resize_cover.configure(bg=theme_color(MAIN_BG))
            self._resize_cover.grid(row=1, column=1, sticky="nsew")
        self._resize_job = self.after(150, self._resized)

    def _resized(self):
        self._resize_job = None
        self._resize_cover.grid_remove()
        self.content.grid()
        scale = ctk.ScalingTracker.get_window_scaling(self)
        self._set_wide(self.winfo_width() / scale >= WIDE)

    def _set_wide(self, wide):
        """Lay the pages out for a wide window (side panels) or a narrow one (one column)."""
        if wide == self.wide:
            return
        self.wide = wide
        self._layout_board()
        self._layout_settings()
        if self.current_page == "home":
            self._fill_home()
        if self.current_page == "projects":
            self.refresh_projects()
        if self.current_page == "thumbs":
            self.refresh_thumbs()

    # -- fonts & icons -------------------------------------------------------
    def _build_fonts(self):
        families = set(tkfont.families(self))
        self.ui_family = "Segoe UI" if "Segoe UI" in families else "Helvetica"
        if "Segoe Fluent Icons" in families:
            self.icon_family, self._icon_index = "Segoe Fluent Icons", 0
        elif "Segoe MDL2 Assets" in families:
            self.icon_family, self._icon_index = "Segoe MDL2 Assets", 0
        else:
            self.icon_family, self._icon_index = self.ui_family, 1

        self.font_body = ctk.CTkFont(family=self.ui_family, size=13)
        self.font_bold = ctk.CTkFont(family=self.ui_family, size=13, weight="bold")
        self.font_tiny = ctk.CTkFont(family=self.ui_family, size=11)
        self.font_caption = ctk.CTkFont(family=self.ui_family, size=12, weight="bold")
        self.font_title = ctk.CTkFont(family=self.ui_family, size=16, weight="bold")
        self.font_value = ctk.CTkFont(family=self.ui_family, size=15)
        self.font_icon = ctk.CTkFont(family=self.icon_family, size=15)
        self.font_icon_lg = ctk.CTkFont(family=self.icon_family, size=18)
        self.font_mono = ctk.CTkFont(family="Consolas", size=12)
        self.font_mono_sm = ctk.CTkFont(family="Consolas", size=11)

    def icon(self, name: str) -> str:
        return GLYPHS.get(name, ("", "?"))[self._icon_index]

    # -- state ---------------------------------------------------------------
    def _build_vars(self):
        s = self.settings
        self.var_api = tk.StringVar(value=s["api_key"])
        self.var_output = tk.StringVar(value=s["output_path"])
        self.var_aspect = tk.StringVar(value=s["aspect"])
        self.var_resolution = tk.StringVar(value=s["resolution"])
        self.var_voice = tk.StringVar(value=voices.persona(s["voice"]).label)
        self.var_voice_speed = tk.StringVar(value=s["voice_speed"])
        self.var_footage_order = tk.StringVar(value=s["footage_order"])
        self.var_theme = tk.StringVar(value=s["theme"])
        self.var_translucent = tk.BooleanVar(value=bool(s["translucent"]))
        self.var_pixabay = tk.StringVar(value=s["pixabay_key"])
        padding_label = {v: k for k, v in PADDING_OPTIONS.items()}[s["padding"]]
        self.var_padding = tk.StringVar(value=padding_label)
        self.var_music_enabled = tk.BooleanVar(value=s["music_enabled"])
        self.var_ask_save = tk.BooleanVar(value=s["ask_save"])
        self.welcome_seen = s["welcome_seen"]
        self.tour_seen = s["tour_seen"]
        self.started = s["started"]
        self.features_hidden = s["features_hidden"]
        self.version_since = s["version_since"]
        self._tip_pending = False   # the next render is a Show it on my PC video
        self._render_info = None    # {"at", "ai", "tip"} of the render running now
        self.var_check_updates = tk.BooleanVar(value=s["check_updates"])
        self.var_versions = tk.StringVar(value=str(s["versions"]))
        self.var_target = tk.StringVar(value=s["target_length"])
        self.var_captions = tk.BooleanVar(value=s["captions"])
        self.caption_vars = {key: tk.StringVar(value=s[key])
                             for key, _allowed, _default in CAPTION_SETTINGS}
        self.music_path = s["music_path"]
        self.audience = {key: s[key] for key, _allowed, _default in AUDIENCE_SETTINGS}
        self.writer_mode = s["writer_mode"]
        self.var_writer_gpu = tk.BooleanVar(value=s["writer_gpu"])
        self.var_voice_online = tk.BooleanVar(value=s["voice_online"])
        self.var_auto_cards = tk.BooleanVar(value=s["auto_cards"])
        self.var_ai_for = tk.StringVar(value=s["ai_images_for"])
        self.var_ai_look = tk.StringVar(value=s["ai_images_look"])
        self.ai_backend = s["ai_images_backend"]
        self.var_ai_device = tk.StringVar(value=s["ai_device"])
        self.project = s["project"] or None   # vidgen.projects id of what the script is
        self.var_intro_on = tk.BooleanVar(value=s["intro_on"])
        self.var_intro_text = tk.StringVar(value=s["intro_text"])
        self.var_outro_on = tk.BooleanVar(value=s["outro_on"])
        self.var_outro_text = tk.StringVar(value=s["outro_text"])
        self.var_sfx = tk.BooleanVar(value=s["sound_effects"])
        self.var_smart_motion = tk.BooleanVar(value=s["smart_motion"])
        self.var_check_clips = tk.BooleanVar(value=s["check_clips"])
        self.var_animate = tk.StringVar(value=s["animate"])
        self.var_web_facts = tk.BooleanVar(value=s["web_facts"])
        self.var_style = tk.StringVar(value=s["style"])
        self.var_quick_sync = tk.BooleanVar(value=s["quick_sync"])
        self.var_style.trace_add("write", lambda *_args: getattr(self, "board_cards", None)
                                 and self.refresh_board(force=True))   # cards show the style
        self.whats_new_seen, self.whats_new_off = s["whats_new_seen"], s["whats_new_off"]
        self.key_tip = s["key_tip"]
        self.ai_gpu, self.ai_fast_seconds = s["ai_gpu"], s["ai_fast_seconds"]
        self.gpu_looked = s["gpu_looked"]
        self.ai_threads_best = s["ai_threads_best"]
        self.ai_speed_chosen = bool(s["ai_images_speed"])
        self.var_ai_speed = tk.StringVar(value=s["ai_images_speed"] or self.default_ai_speed())
        self.var_ai_threads = tk.StringVar(value=str(s["ai_threads"]))
        self.var_ai_speed.trace_add("write", self._ai_speed_picked)
        self._render_overrides = {}   # one render's answer to the slow-PC question
        self._both = None   # "Make both shapes": {shape, other, first: the first videos}
        self._batch = None  # "Make several": the texts, how far it is, what was made
        self.last_upload = None   # title, description, tags of the last video made
        self.voice_menus = []     # the voice dropdowns, to add My voice to once it is made
        self.var_music_track = tk.StringVar(value="")
        self._tracks = {}

        for var in (self.var_api, self.var_output, self.var_aspect,
                    self.var_resolution, self.var_voice, self.var_voice_speed, self.var_pixabay,
                    self.var_footage_order,
                    self.var_padding, self.var_music_enabled, self.var_captions,
                    self.var_ask_save, self.var_check_updates, self.var_versions,
                    self.var_writer_gpu, self.var_voice_online, self.var_auto_cards,
                    self.var_ai_for, self.var_ai_look,
                    self.var_target, self.var_intro_on, self.var_intro_text,
                    self.var_outro_on, self.var_outro_text, self.var_sfx,
                    self.var_smart_motion, self.var_animate, self.var_web_facts,
                    self.var_style, self.var_quick_sync, self.var_check_clips,
                    *self.caption_vars.values()):
            var.trace_add("write", self._on_setting_changed)

    def _collect_settings(self) -> dict:
        return {
            "api_key": self.var_api.get().strip(),
            "pixabay_key": self.var_pixabay.get().strip(),
            "padding": PADDING_OPTIONS.get(self.var_padding.get(), DEFAULT_PADDING),
            "music_enabled": bool(self.var_music_enabled.get()),
            "ask_save": bool(self.var_ask_save.get()),
            "welcome_seen": self.welcome_seen,
            "tour_seen": self.tour_seen,
            "started": self.started,
            "check_updates": bool(self.var_check_updates.get()),
            "versions": 2 if self.var_versions.get() == "2" else 1,
            "target_length": self.var_target.get(),
            **self.audience,
            "writer_mode": self.writer_mode,
            "writer_gpu": bool(self.var_writer_gpu.get()),
            "voice_online": bool(self.var_voice_online.get()),
            "auto_cards": bool(self.var_auto_cards.get()),
            "ai_images_for": self.var_ai_for.get(),
            "ai_images_look": self.var_ai_look.get(),
            "ai_images_backend": self.ai_backend,
            "ai_device": self.var_ai_device.get(),
            "project": self.project or "",
            "intro_on": bool(self.var_intro_on.get()),
            "intro_text": self.var_intro_text.get().strip(),
            "outro_on": bool(self.var_outro_on.get()),
            "outro_text": self.var_outro_text.get().strip(),
            "sound_effects": bool(self.var_sfx.get()),
            "smart_motion": bool(self.var_smart_motion.get()),
            "check_clips": bool(self.var_check_clips.get()),
            "animate": self.var_animate.get(),
            "web_facts": bool(self.var_web_facts.get()),
            "style": self.var_style.get(),
            "quick_sync": bool(self.var_quick_sync.get()),
            "whats_new_seen": self.whats_new_seen,
            "whats_new_off": self.whats_new_off,
            "key_tip": self.key_tip,
            "ai_images_speed": self.var_ai_speed.get() if self.ai_speed_chosen else "",
            "ai_threads": self.var_ai_threads.get(),
            "ai_threads_best": self.ai_threads_best,
            "ai_fast_seconds": self.ai_fast_seconds,
            "ai_gpu": self.ai_gpu,
            "gpu_looked": self.gpu_looked,
            "music_path": self.music_path,
            "output_path": self.var_output.get().strip(),
            "aspect": self.var_aspect.get(),
            "resolution": self.var_resolution.get(),
            "voice": voices.persona(self.var_voice.get()).id,
            "voice_speed": self.var_voice_speed.get(),
            "footage_order": self.var_footage_order.get(),
            "captions": bool(self.var_captions.get()),
            **{key: var.get() for key, var in self.caption_vars.items()},
            "theme": self.var_theme.get(),
            "translucent": bool(self.var_translucent.get()),
            "window": self._window_now(),
            "features_hidden": self.features_hidden,
            "version_since": self.version_since,
            "script": self.script_box.get("1.0", "end").strip()
            if hasattr(self, "script_box") else self.settings["script"],
        }

    def _on_setting_changed(self, *_args):
        self.settings = self._collect_settings()
        save_settings(self.settings)
        self.refresh_home()

    # -- shell ---------------------------------------------------------------
    def _build_shell(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)   # row 0 is the update bar, when shown

        self.sidebar = ctk.CTkFrame(self, width=SIDEBAR_WIDTH, corner_radius=0,
                                    fg_color=SIDEBAR_BG)
        self.sidebar.grid(row=0, column=0, rowspan=2, sticky="nsw")
        # Children here are packed, so pack_propagate() is what stops them from
        # dictating the sidebar's width (grid_propagate would be a no-op).
        self.sidebar.pack_propagate(False)
        self.sidebar.grid_propagate(False)

        self.hamburger = ctk.CTkButton(
            self.sidebar, text=self.icon("menu"), font=self.font_icon,
            width=36, height=34, corner_radius=5, fg_color="transparent",
            hover_color=NAV_HOVER, text_color=TEXT, command=self.toggle_sidebar,
        )
        self.hamburger.pack(anchor="w", padx=6, pady=(10, 8))

        self.nav_items = {}
        for key, glyph, label in (
            ("home", "home", "Home"),
            ("dashboard", "chart", "Dashboard"),
            ("projects", "projects", "My videos"),
            ("thumbs", "images", "Thumbnails"),
            ("photo", "edit", "Image editor"),
            ("board", "scenes", "Storyboard"),
        ):
            item = NavItem(self.sidebar, self, key, self.icon(glyph), label,
                           self.select_page)
            item.pack(fill="x", padx=6, pady=1)
            self.nav_items[key] = item

        bottom = ctk.CTkFrame(self.sidebar, fg_color="transparent",
                              width=SIDEBAR_WIDTH, height=46)
        bottom.pack(side="bottom", fill="x", pady=(0, 10))
        bottom.pack_propagate(False)
        for key, glyph, label in (
            ("settings", "settings", "Settings"),
        ):
            item = NavItem(bottom, self, key, self.icon(glyph), label,
                           self.select_page)
            item.pack(fill="x", padx=6, pady=1)
            self.nav_items[key] = item

        # Shown only when a newer release exists; see check_for_updates().
        self.update_url = updates.RELEASES_PAGE
        self.update_bar = Card(self)
        self.update_bar.grid(row=0, column=1, sticky="ew", padx=EDGE, pady=(14, 0))
        self.update_bar.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self.update_bar, text=self.icon("info"), font=self.font_icon,
                     text_color=ACCENT).grid(row=0, column=0, padx=(16, 10), pady=10)
        self.update_bar_label = ctk.CTkLabel(self.update_bar, text="", font=self.font_body,
                                             text_color=TEXT, anchor="w")
        self.update_bar_label.grid(row=0, column=1, sticky="w")
        # "Update now" for an installed copy, "Download" (the release page) otherwise.
        self.update_action = ctk.CTkButton(
            self.update_bar, text="Download", width=110, height=30, corner_radius=4,
            font=self.font_body, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color=ACCENT_TEXT, text_color_disabled=ACCENT_TEXT,
            command=self.open_update_page,
        )
        self.update_action.grid(row=0, column=2, padx=(8, 0))
        self.update_later = ctk.CTkButton(
            self.update_bar, text="Later", width=72, height=30, corner_radius=4,
            font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
            command=self.update_bar.grid_remove,
        )
        self.update_later.grid(row=0, column=3, padx=(6, 12))
        self.update_bar.grid_remove()
        self.update_release = None     # the newer release, once one is found
        self._updating = False
        self._update_stop = threading.Event()

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=1, column=1, sticky="nsew")
        # Stands in for the pages while the window is dragged bigger or smaller (_on_resize).
        self._resize_cover = tk.Frame(self, bg=theme_color(MAIN_BG), highlightthickness=0, bd=0)
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

    def toggle_sidebar(self):
        self.sidebar_collapsed = not self.sidebar_collapsed
        width = SIDEBAR_COLLAPSED if self.sidebar_collapsed else SIDEBAR_WIDTH
        self.sidebar.configure(width=width)
        for item in self.nav_items.values():
            item.configure(width=width - 12)
            item.set_collapsed(self.sidebar_collapsed)
        # Keep the grid column in step with the frame so the content reflows.
        self.grid_columnconfigure(0, minsize=width)
        self.update_idletasks()

    def select_page(self, key: str):
        if key in SETTINGS_PAGES:   # moved into Settings in 4.8; the old keys still work
            self.open_settings(SETTINGS_PAGES[key])
            return
        if key in ("board", "log"):   # the Log is the Storyboard's second tab
            self.var_board_view.set("Log" if key == "log" else "Setup")
            key = "board"
        if self.current_page == "board" and key != "board":
            self.save_project()   # a Storyboard left without a video is kept as a draft
        if key != self.current_page:
            self._show_page(key)

        self._show_board_nav()
        for name, item in self.nav_items.items():
            item.set_selected(name == ("home" if key == "new" else key))
        if key == "home":
            self.refresh_home()
        elif key == "board":
            self.refresh_board()
        elif key == "projects":
            self.refresh_projects()
        elif key == "thumbs":
            self.refresh_thumbs()
        elif key == "dashboard":
            self.refresh_dashboard()
        elif key == "settings":
            self._refresh_tracks()

    def _show_board_nav(self):
        """Storyboard is in the sidebar while there is a video (a script) or it is open."""
        item = self.nav_items["board"]
        if self.script_box.get("1.0", "end").strip() or self.current_page == "board":
            if not item.winfo_manager():
                item.pack(fill="x", padx=6, pady=1, after=self.nav_items["photo"])
        else:
            item.pack_forget()

    def _show_page(self, key):
        if self._page_anim:
            self._page_anim.cancel()
            self._page_anim = None
        for name, page in self.pages.items():
            if name != key:
                page.grid_remove()
                tk.Frame.place_forget(self._frame_of(page))

        page = self.pages[key]
        if self.current_page is None:   # first page at startup: no entrance
            self._grid_page(page)
        else:
            self._slide_in(page)
        self.current_page = key

    @staticmethod
    def _frame_of(page):
        """The Tk frame that is actually laid out (a scrollable page wraps one)."""
        return getattr(page, "_parent_frame", page)

    @staticmethod
    def _grid_page(page):
        page.grid(row=0, column=0, sticky="nsew", padx=(EDGE, EDGE), pady=(14, 14))

    def _slide_in(self, page):
        """Fluent page entrance: rise into place, decelerating.

        While moving, the page is placed at exactly the size the grid gives it,
        so nothing inside re-lays out; the grid takes over again at the end.
        CTk's own place() refuses width/height, hence the plain Tk call.
        """
        frame = self._frame_of(page)
        page.grid_remove()
        s = ctk.ScalingTracker.get_widget_scaling(self)

        def step(t):
            tk.Frame.place(frame, x=EDGE * s, y=(14 + PAGE_RISE * (1 - t)) * s,
                           relwidth=1, relheight=1, width=-2 * EDGE * s, height=-28 * s)

        def done():
            self._page_anim = None
            tk.Frame.place_forget(frame)
            self._grid_page(page)

        self._page_anim = Animation(self, ANIM_PAGE, step, done)

    # -- page scaffolding ----------------------------------------------------
    def _scroll_page(self, parent=None):
        return ctk.CTkScrollableFrame(
            parent or self.content, fg_color="transparent",
            scrollbar_fg_color=SCROLLBAR_BG,
            scrollbar_button_color=SCROLLBAR_THUMB,
            scrollbar_button_hover_color=SCROLLBAR_THUMB_HOVER,
        )

    def _caption(self, parent, text, row, pady=(10, 4)):
        label = ctk.CTkLabel(parent, text=text, font=self.font_caption,
                             text_color=TEXT_MUTED, anchor="w")
        label.grid(row=row, column=0, columnspan=4, sticky="w",
                   padx=2, pady=pady)
        return label

    def _build_pages(self):
        self.pages = {
            "home": self._build_home(),
            "new": self._build_new(),
            "dashboard": self._build_dashboard(),
            "projects": self._build_projects(),
            "thumbs": self._build_thumbs(),
            "photo": self._build_photo_editor(),
            "board": self._build_board(),
            "settings": self._build_settings(),
        }

    # ---------------------------------------------------------------- HOME --
    def _build_home(self):
        """Two doors (make a new video, edit one) above the dashboard (_fill_home)."""
        page = self._scroll_page()
        page.grid_columnconfigure((0, 1), weight=1, uniform="doors")
        hello = ctk.CTkFrame(page, fg_color="transparent")
        hello.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(4, 12))
        self.home_hello = ctk.CTkLabel(hello, text="", font=ctk.CTkFont(
            family=self.ui_family, size=24, weight="bold"), text_color=TEXT, anchor="w")
        self.home_hello.pack(anchor="w", padx=2)
        self.home_status = ctk.CTkLabel(hello, text="", font=self.font_body,
                                        text_color=TEXT_MUTED, anchor="w")
        self.home_status.pack(anchor="w", padx=2)
        self._door(page, self.icon("add"), "Make new video", "From text, a Windows tip, or several at once",
                   lambda: self.select_page("new"), True).grid(
            row=1, column=0, sticky="nsew", padx=(0, PAD // 2), pady=(0, PAD))
        self._door(page, self.icon("edit"), "Edit a video", "Open one of your videos in the Storyboard",
                   lambda: self.select_page("projects"), False).grid(
            row=1, column=1, sticky="nsew", padx=(PAD // 2, 0), pady=(0, PAD))
        self.home_body = ctk.CTkFrame(page, fg_color="transparent")
        self.home_body.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.home_body.grid_columnconfigure(0, weight=1)
        return page

    def _build_new(self):
        """Make new video: from text (the big one), a Windows tip, or several at once."""
        page = self._scroll_page()
        page.grid_columnconfigure((0, 1), weight=1, uniform="doors")
        self._caption(page, "Make new video", 0, pady=(0, 6))
        # From text: most people arrive with text, not with a script (issues #2, #3).
        door = Card(page)
        door.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, PAD))
        door.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(door, text="From text: paste it or describe your video", font=self.font_title,
                     text_color=TEXT, anchor="w").grid(row=0, column=0, columnspan=3, sticky="w",
                                                       padx=16, pady=(14, 2))
        ctk.CTkLabel(door, text="An article, a tip, a story, notes. The app writes the script, "
                                "finds the pictures, reads it out and adds captions.",
                     font=self.font_tiny, text_color=TEXT_DIM, anchor="w",
                     ).grid(row=1, column=0, columnspan=3, sticky="w", padx=16)
        self.door_box = ctk.CTkTextbox(door, height=130, font=self.font_body, corner_radius=4,
                                       fg_color=FIELD_BG, text_color=TEXT, border_width=0,
                                       wrap="word")
        template_bar = ctk.CTkFrame(door, fg_color="transparent")
        template_bar.grid(row=2, column=0, columnspan=3, sticky="w", padx=16, pady=(10, 0))
        ctk.CTkLabel(template_bar, text="Template", font=self.font_body, text_color=TEXT_MUTED,
                     ).pack(side="left", padx=(0, 10))
        self.var_template = tk.StringVar(value=NO_TEMPLATE)
        FluentSegmented(template_bar, self, [NO_TEMPLATE, *draft.TEMPLATES], self.var_template,
                        540).pack(side="left")
        ctk.CTkLabel(template_bar, text="Style", font=self.font_body, text_color=TEXT_MUTED,
                     ).pack(side="left", padx=(16, 8))
        ctk.CTkOptionMenu(
            template_bar, values=list(drawn.STYLES), variable=self.var_style, width=120,
            height=30, corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
            button_color=FIELD_BG, button_hover_color=CARD_HOVER, text_color=TEXT,
            dropdown_fg_color=CARD_BG, dropdown_text_color=TEXT, dropdown_hover_color=CARD_HOVER,
            dropdown_font=self.font_body, dynamic_resizing=False).pack(side="left")
        self.door_box.grid(row=3, column=0, columnspan=3, sticky="ew", padx=14, pady=(8, 8))
        self.door_photos = PhotoList(door, self)
        self.door_photos.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        self.door_status = ctk.CTkLabel(door, text="", font=self.font_tiny, text_color=TEXT_DIM,
                                        anchor="w")
        self.door_status.grid(row=5, column=0, sticky="w", padx=16, pady=(0, 14))
        self.var_straight = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(door, text="Make it straight away", variable=self.var_straight,
                        font=self.font_body, text_color=TEXT_MUTED, checkbox_width=18,
                        checkbox_height=18, border_width=1,
                        ).grid(row=5, column=1, sticky="e", padx=(0, 14), pady=(0, 14))
        ctk.CTkButton(door, text=f"{self.icon('play')}   Make video", width=170, height=36,
                      corner_radius=4, font=self.font_bold, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, text_color=ACCENT_TEXT,
                      command=self.make_from_text).grid(row=5, column=2, sticky="e",
                                                        padx=(0, 14), pady=(0, 14))

        self._door(page, self.icon("play"), "Windows tip", "Show it on my PC: the app opens the setting, "
                   "records it and explains each step", lambda: TipDialog(self), False).grid(
            row=2, column=0, sticky="nsew", padx=(0, PAD // 2))
        self._door(page, self.icon("scenes"), "Several at once", "Paste several texts and make a video of each, "
                   "one after another", lambda: BatchDialog(self), False).grid(
            row=2, column=1, sticky="nsew", padx=(PAD // 2, 0))
        return page

    def _door(self, parent, glyph, title, line, command, accent):
        """A big clickable card: a glyph, a title and one line under it."""
        fg, hover = (ACCENT, ACCENT_HOVER) if accent else (CARD_BG, CARD_HOVER)
        text, dim = (ACCENT_TEXT, ACCENT_TEXT) if accent else (TEXT, TEXT_MUTED)
        card = ctk.CTkFrame(parent, fg_color=fg, corner_radius=8, height=104)
        card.grid_propagate(False)
        card.grid_columnconfigure(1, weight=1)
        card.grid_rowconfigure((0, 1), weight=1)
        parts = (ctk.CTkLabel(card, text=glyph, font=self.font_icon_lg, text_color=text, width=36),
                 ctk.CTkLabel(card, text=title, font=self.font_title, text_color=text, anchor="sw"),
                 ctk.CTkLabel(card, text=line, font=self.font_body, text_color=dim, anchor="nw",
                              justify="left", wraplength=420))
        parts[0].grid(row=0, column=0, rowspan=2, padx=(20, 12))
        parts[1].grid(row=0, column=1, sticky="sw", padx=(0, 16))
        parts[2].grid(row=1, column=1, sticky="nw", padx=(0, 16))
        for widget in (card, *parts):
            widget.bind("<Button-1>", lambda _e: command())
            widget.bind("<Enter>", lambda _e: card.configure(fg_color=hover))
            widget.bind("<Leave>", lambda _e: card.configure(fg_color=fg))
            widget.configure(cursor="hand2")
        return card

    def make_from_text(self):
        """From text's Make video: write a script from the pasted text, then show it."""
        text = self.door_box.get("1.0", "end").strip()
        if self.is_rendering:
            return
        if not text:
            self.door_status.configure(text="Paste or type some text first.",
                                       text_color=ERR_COLOR)
            return
        if not self._has_footage_key() and not imagegen.installed():
            WelcomeDialog(self)   # nothing to show the scenes with yet: the free key comes first
            return
        self.door_status.configure(text="", text_color=TEXT_DIM)
        self._after_draft = "render" if self.var_straight.get() else "board"
        dialog = DraftDialog(self)
        dialog.prefill(text)
        for photo in self.door_photos.photo_list():   # the dialog describes and places them
            dialog.add_photo(photo.path, photo.about, photo.show)
        name = self.var_template.get()
        template = draft.TEMPLATES.get(name)
        if template is None:
            dialog.analyse()   # straight to the questions: the text is already here
            return
        # A template answers the questions itself; they are still shown, to change.
        self.var_auto_cards.set(template.cards)
        self.caption_vars["caption_style"].set(template.caption_style)
        self.var_style.set(template.look)
        dialog.style = template.audience.style
        dialog.var_platform.set(template.audience.platform)   # before the length it presets
        dialog._apply(draft.Suggestion(template.audience, template.length,
                                       f"The {name} template. Change any answer if you like."))

    def refresh_home(self):
        self.refresh_estimate()
        if self.current_page == "home" and hasattr(self, "home_body"):
            self._fill_home()

    def _fill_home(self):
        """Home: greeting, then Continue, Tip ideas, Get started, What's new and the features.
        Wide: two columns; narrow: one, in that order. Your videos are on the Dashboard."""
        for child in self.home_body.winfo_children():
            child.destroy()
        self.home_images = []
        every = projects.all_projects()
        now = time.time()
        numbers = started.summary(every, now)
        self.home_hello.configure(text=started.greeting(time.localtime(now).tm_hour))
        self.home_status.configure(text=started.status_line(numbers, now))
        body = self.home_body
        body.grid_columnconfigure((0, 1), weight=0, uniform="")
        left = [self._continue_card(every[0], body) if every else None,
                self._ideas_card(every, body)]
        right = [None if self.started["hidden"] else self._started_card(numbers, body),
                 self._news_card(body) if started.whats_new_due(
                     self.version_since, APP_VERSION, now) else None]
        below = [self._features_card(body)]
        if not self.wide:
            body.grid_columnconfigure(0, weight=1)
            for row, card in enumerate(c for c in left + right + below if c is not None):
                card.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, PAD))
            return
        body.grid_columnconfigure((0, 1), weight=1, uniform="home")
        for column, cards in ((0, left), (1, right)):
            stack = ctk.CTkFrame(body, fg_color="transparent")
            stack.grid(row=0, column=column, sticky="new",
                       padx=(0, PAD // 2) if column == 0 else (PAD // 2, 0))
            stack.grid_columnconfigure(0, weight=1)
            for row, card in enumerate(c for c in cards if c is not None):
                card.grid(in_=stack, row=row, column=0, sticky="ew", pady=(0, PAD))
                card.lift(stack)
        below[0].grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, PAD))

    # ----------------------------------------------------------- DASHBOARD --
    def _build_dashboard(self):
        page = self._scroll_page()
        page.grid_columnconfigure(0, weight=1)
        self._caption(page, "Dashboard", 0, pady=(0, 6))
        self.dash_body = ctk.CTkFrame(page, fg_color="transparent")
        self.dash_body.grid(row=1, column=0, sticky="ew")
        return page

    def refresh_dashboard(self):
        """Your numbers, a chart of the last 8 weeks, what you use most, recent videos,
        storage and setup. With no videos yet, one box that says how to start."""
        body = self.dash_body
        for child in body.winfo_children():
            child.destroy()
        self.home_images = []
        every = projects.all_projects()
        now = time.time()
        numbers = started.summary(every, now)
        body.grid_columnconfigure((0, 1), weight=1, uniform="dash")
        row = itertools.count()
        if not numbers["made"]:
            card, inner = self._home_card("Your numbers appear here after your first video",
                                          parent=body)
            ctk.CTkButton(inner, text="Make your first video", width=180, height=32,
                          corner_radius=4, font=self.font_body, fg_color=ACCENT,
                          hover_color=ACCENT_HOVER, text_color=ACCENT_TEXT,
                          command=lambda: self.select_page("new")).grid(row=0, column=0,
                                                                        sticky="w")
            card.grid(row=next(row), column=0, columnspan=2, sticky="ew", pady=(0, PAD))
        else:
            tiles = ctk.CTkFrame(body, fg_color="transparent")
            tiles.grid(row=next(row), column=0, columnspan=2, sticky="ew", pady=(0, PAD))
            tiles.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="tiles")
            usual = numbers["usual"]
            for at, (value, label) in enumerate((
                    (numbers["made"], "videos made"),
                    (f"{started.minutes(every):.1f}", "minutes of video"),
                    (numbers["week"], "made this week"),
                    (f"{usual} s" if usual else "–", "usual make time"))):
                tile = Card(tiles)
                tile.grid(row=0, column=at, sticky="nsew", padx=(0 if at == 0 else PAD, 0))
                ctk.CTkLabel(tile, text=str(value), font=ctk.CTkFont(
                    family=self.ui_family, size=28, weight="bold"), text_color=TEXT,
                    anchor="w").pack(anchor="w", padx=16, pady=(14, 0))
                ctk.CTkLabel(tile, text=label, font=self.font_body, text_color=TEXT_MUTED,
                             anchor="w").pack(anchor="w", padx=16, pady=(0, 14))
            at = next(row)
            self._chart_card(every, now, body).grid(row=at, column=0, sticky="nsew",
                                                    padx=(0, PAD // 2), pady=(0, PAD))
            self._most_used_card(every, body).grid(row=at, column=1, sticky="nsew",
                                                   padx=(PAD // 2, 0), pady=(0, PAD))
            self._recent_card(every[:6], body).grid(row=next(row), column=0, columnspan=2,
                                                    sticky="ew", pady=(0, PAD))
        at = next(row)
        self._storage_card(body).grid(row=at, column=0, sticky="nsew", padx=(0, PAD // 2))
        self._setup_card(body).grid(row=at, column=1, sticky="nsew", padx=(PAD // 2, 0))

    def _chart_card(self, every, now, parent):
        """Videos per week, the last 8 weeks, as bars."""
        card, body = self._home_card("Videos per week", parent=parent)
        counts = started.per_week(every, now)
        canvas = tk.Canvas(body, height=150, highlightthickness=0, bd=0,
                           bg=theme_color(CARD_BG))
        canvas.grid(row=0, column=0, columnspan=2, sticky="ew")

        def draw(_event=None):
            canvas.delete("all")
            width, height, top = max(canvas.winfo_width(), 200), 150, max(max(counts), 1)
            step = width / len(counts)
            for i, count in enumerate(counts):
                bar = (height - 48) * count / top   # room for its number above
                x0, x1 = i * step + step * 0.2, (i + 1) * step - step * 0.2
                canvas.create_rectangle(x0, height - 20 - bar, x1, height - 20, outline="",
                                        fill=theme_color(ACCENT if i == len(counts) - 1
                                                         else FIELD_BORDER))
                if count:
                    canvas.create_text((x0 + x1) / 2, height - 26 - bar, text=str(count),
                                       anchor="s", font=self.font_tiny, fill=theme_color(TEXT))
                week = "this week" if i == len(counts) - 1 else f"{len(counts) - 1 - i} wk ago"
                canvas.create_text((x0 + x1) / 2, height - 4, text=week, anchor="s",
                                   font=self.font_tiny, fill=theme_color(TEXT_DIM))

        canvas.bind("<Configure>", draw)
        return card

    def _most_used_card(self, every, parent):
        card, body = self._home_card("What you use most", parent=parent)
        voice = started.most_used(every, "voice")
        found = (("Voice", voices.persona(voice).short if voice else None),
                 ("Shape", started.most_used(every, "aspect")),
                 ("Style", started.most_used(every, "style")),
                 ("Captions", started.most_used(every, "caption_style")))
        for at, (label, value) in enumerate(found):
            ctk.CTkLabel(body, text=label, font=self.font_body, text_color=TEXT_MUTED,
                         anchor="w", width=90).grid(row=at, column=0, sticky="w", pady=3)
            ctk.CTkLabel(body, text=value or "–", font=self.font_bold, text_color=TEXT,
                         anchor="w").grid(row=at, column=1, sticky="w", pady=3)
        return card

    def _storage_card(self, parent):
        card, body = self._home_card("Storage", parent=parent)
        for at, (label, folder) in enumerate((
                ("Kept clips and voices", CACHE_ROOT),
                ("Your videos' AI pictures and scripts", projects.ROOT),
                ("Show it on my PC recordings", RECORDINGS_DIR))):
            size = cache.size(folder) if os.path.isdir(folder) else 0
            ctk.CTkLabel(body, text=label, font=self.font_body, text_color=TEXT_MUTED,
                         anchor="w").grid(row=at, column=0, sticky="w", pady=3)
            ctk.CTkLabel(body, text=f"{size / 1e6:.0f} MB", font=self.font_bold,
                         text_color=TEXT, anchor="e").grid(row=at, column=1, sticky="e", pady=3)
        ctk.CTkButton(body, text="Clear kept clips and voices", width=200, height=28,
                      corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
                      hover_color=CARD_HOVER, border_width=1, border_color=FIELD_BORDER,
                      text_color=TEXT, command=lambda: (self.clear_cache(),
                                                        self.refresh_dashboard())).grid(
            row=3, column=0, sticky="w", pady=(8, 0))
        return card

    def _setup_card(self, parent):
        card, body = self._home_card("Setup", parent=parent)
        found = [("Footage key", self._has_footage_key(), "Add key",
                  lambda: self.open_settings("footage")),
                 ("Smart writer", writer.installed(), "Set up",
                  lambda: self.open_settings("writer")),
                 ("AI pictures", imagegen.installed(), "Get them",
                  lambda: self.open_settings("images"))]
        if not myvoice.available():
            found.append(("My voice", myvoice.installed(), "Make it",
                          lambda: MyVoiceDialog(self)))
        for at, (label, ready, button, command) in enumerate(found):
            ctk.CTkLabel(body, text="✓" if ready else "○", font=self.font_bold, width=22,
                         text_color=OK_COLOR if ready else TEXT_DIM).grid(row=at, column=0)
            ctk.CTkLabel(body, text=label, font=self.font_body, text_color=TEXT,
                         anchor="w").grid(row=at, column=1, sticky="w", pady=3)
            if not ready:
                ctk.CTkButton(body, text=button, width=100, height=28, corner_radius=4,
                              font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                              border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                              command=command).grid(row=at, column=2, padx=(8, 0), pady=3)
        return card

    def _continue_card(self, project, parent):
        """Continue where you left off: the newest video or draft."""
        card, body = self._home_card("Continue where you left off", parent=parent)
        pid, now = project["id"], time.time()
        box = (54, 96) if (project.get("settings") or {}).get("aspect", "9:16") == "9:16" \
            else (128, 72)
        picture = ctk.CTkLabel(body, text=self.icon("projects"), font=self.font_icon,
                               text_color=TEXT_DIM, width=box[0], height=box[1],
                               fg_color=FIELD_BG, corner_radius=4)
        photo = self._project_thumb_image(pid, box)
        if photo is not None:
            self.home_images.append(photo)
            picture.configure(image=photo, text="")
        picture.grid(row=0, column=0, rowspan=3, padx=(0, 12))
        ctk.CTkLabel(body, text=project.get("title") or "Untitled video", font=self.font_bold,
                     text_color=TEXT, anchor="w", justify="left", wraplength=320).grid(
            row=0, column=1, sticky="w")
        when = started.ago(project.get("updated") or now, now)
        ctk.CTkLabel(body, text=f"Draft, changed {when}" if projects.is_draft(project) else
                     f"Made {when}", font=self.font_tiny, text_color=TEXT_DIM,
                     anchor="w").grid(row=1, column=1, sticky="w")
        ctk.CTkButton(body, text="Open in Storyboard", width=150, height=30, corner_radius=4,
                      font=self.font_body, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      text_color=ACCENT_TEXT, command=lambda: self.open_project(pid)).grid(
            row=2, column=1, sticky="w", pady=(8, 0))
        return card

    def _ideas_card(self, every, parent):
        """Three ready-made Windows tips for today, none the user has made already."""
        card, body = self._home_card("Tip ideas for today", parent=parent)
        made = " ".join(f"{p.get('title', '')} {p.get('script', '')}" for p in every).lower()
        ideas = started.tip_ideas(tiplist.TIPS, made, datetime.date.today().toordinal())
        if not ideas:
            ctk.CTkLabel(body, text="You have made every ready-made tip. Type your own in Show "
                                    "it on my PC.", font=self.font_body, text_color=TEXT_MUTED,
                         anchor="w").grid(row=0, column=0, columnspan=3, sticky="w")
            return card
        for row, (text, _place) in enumerate(ideas):
            ctk.CTkLabel(body, text=self.icon("play"), font=self.font_icon, text_color=ACCENT,
                         width=22).grid(row=row, column=0, sticky="w")
            ctk.CTkLabel(body, text=text, font=self.font_body, text_color=TEXT, anchor="w").grid(
                row=row, column=1, sticky="w", pady=3)
            ctk.CTkButton(body, text="Make it", width=90, height=28, corner_radius=4,
                          font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                          border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                          command=lambda t=text: TipDialog(self, ask=t)).grid(
                row=row, column=2, padx=(8, 0), pady=3)
        return card

    def _news_card(self, parent):
        """What's new in this version: its first line, for a week after an update."""
        card, body = self._home_card(
            f"What's new in {APP_VERSION}",
            lambda head: self._link(head, "See all", lambda: WhatsNewDialog(self)).pack(
                side="right"), parent)
        ctk.CTkLabel(body, text=WHATS_NEW[0], font=self.font_body, text_color=TEXT_MUTED,
                     anchor="w", justify="left", wraplength=520).grid(row=0, column=0,
                                                                      columnspan=2, sticky="w")
        return card

    def _home_card(self, title, right=None, parent=None):
        """A dashboard card with its title; returns (card, body)."""
        card = Card(parent or self.home_body)
        card.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        ctk.CTkLabel(head, text=title, font=self.font_bold, text_color=TEXT).pack(side="left")
        if right:
            right(head)
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 14))
        body.grid_columnconfigure(1, weight=1)
        return card, body

    def _link(self, parent, text, command):
        return ctk.CTkButton(parent, text=text, width=10, height=26, corner_radius=4,
                             font=self.font_tiny, fg_color="transparent", hover_color=CARD_HOVER,
                             text_color=TEXT_MUTED, command=command)

    def _started_card(self, numbers, parent=None):
        saved = self.started
        facts = {"key": self._has_footage_key(), "first": numbers["made"] > 0,
                 "myvoice": myvoice.installed()}
        shown = started.steps(not myvoice.available())
        finished = started.finished(saved, facts)
        count, total = started.progress(saved, facts, shown)
        actions = {
            "key": ("Add key", lambda: self.open_settings("footage")),
            "first": ("Make video", lambda: self.select_page("new")),
            "ai": ("Get AI pictures", lambda: self.open_settings("images")),
            "tip": ("Make a tip", lambda: TipDialog(self)),
            "rewrite": ("Open My videos", lambda: self.select_page("projects")),
            "myvoice": ("Make my voice", lambda: MyVoiceDialog(self)),
        }

        def right(head):
            self._link(head, "Hide this list", self.hide_started).pack(side="right")
            ctk.CTkLabel(head, text=f"{count} of {total} done", font=self.font_tiny,
                         text_color=TEXT_MUTED).pack(side="right", padx=(0, 10))

        card, body = self._home_card("Get started", right, parent)
        self.started_box = card
        if count == total:
            ctk.CTkLabel(body, text="All done. Nice work!", font=self.font_body,
                         text_color=OK_COLOR, anchor="w").grid(row=0, column=0, sticky="w")
            return card
        for at, (key, label) in enumerate(shown):
            if key in saved["skipped"]:
                continue
            done = key in finished
            ctk.CTkLabel(body, text="✓" if done else "○", font=self.font_bold, width=22,
                         text_color=OK_COLOR if done else TEXT_DIM).grid(row=at, column=0)
            ctk.CTkLabel(body, text=label, font=self.font_body, anchor="w",
                         text_color=TEXT_MUTED if done else TEXT).grid(row=at, column=1,
                                                                       sticky="w", pady=3)
            if done:
                continue
            text, command = actions[key]
            ctk.CTkButton(body, text=text, width=130, height=28, corner_radius=4,
                          font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                          border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                          command=command).grid(row=at, column=2, padx=(8, 4), pady=3)
            self._link(body, "Not for me", lambda k=key: self.skip_started(k)).grid(
                row=at, column=3, pady=3)
        return card

    def _recent_card(self, recent, parent=None):
        card, body = self._home_card(
            "Recent videos",
            lambda head: self._link(head, "All videos", lambda: self.select_page("projects"),
                                    ).pack(side="right"), parent)
        if not recent:
            ctk.CTkLabel(body, text="Nothing yet. Your videos appear here.", font=self.font_body,
                         text_color=TEXT_MUTED, anchor="w").grid(row=0, column=0, columnspan=2,
                                                                 sticky="w")
            ctk.CTkButton(body, text="Make your first video", width=170, height=30,
                          corner_radius=4, font=self.font_body, fg_color=ACCENT,
                          hover_color=ACCENT_HOVER, text_color=ACCENT_TEXT,
                          command=lambda: self.select_page("new")).grid(
                row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
            return card
        body.grid_columnconfigure((0, 1, 2), weight=1, uniform="recent")
        now = time.time()
        for at, project in enumerate(recent):
            pid = project["id"]
            item = ctk.CTkFrame(body, fg_color=FIELD_BG, corner_radius=6)
            item.grid(row=at // 3, column=at % 3, sticky="nsew",
                      padx=(0 if at % 3 == 0 else 4, 0), pady=(0, 4))
            box = (54, 96) if (project.get("settings") or {}).get("aspect", "9:16") == "9:16" \
                else (128, 72)
            picture = ctk.CTkLabel(item, text=self.icon("projects"), font=self.font_icon,
                                   text_color=TEXT_DIM, width=box[0], height=box[1])
            photo = self._project_thumb_image(pid, box)
            if photo is not None:
                self.home_images.append(photo)
                picture.configure(image=photo, text="")
            picture.pack(side="left", padx=10, pady=10)
            words = ctk.CTkFrame(item, fg_color="transparent")
            words.pack(side="left", fill="x", expand=True, padx=(0, 10))
            ctk.CTkLabel(words, text=project.get("title") or "Untitled video",
                         font=self.font_body, text_color=TEXT, anchor="w", justify="left",
                         wraplength=180).pack(anchor="w")
            when = started.ago(project.get("updated") or now, now)
            length = project.get("length") or 0
            shape = (project.get("settings") or {}).get("aspect", "9:16")
            info = f"Draft · {when}" if projects.is_draft(project) else \
                f"{int(length // 60)}:{int(length % 60):02d} · {shape} · {when}"
            if project.get("took") and not projects.is_draft(project):
                info += f"\ntook {project['took']} s to make"
            ctk.CTkLabel(words, text=info, font=self.font_tiny, text_color=TEXT_DIM,
                         anchor="w").pack(anchor="w")
            for widget in (item, picture, words, *words.winfo_children()):
                widget.bind("<Button-1>", lambda _e, p=pid: self.open_project(p))
                widget.configure(cursor="hand2")
            videos = [v for v in project.get("videos") or [] if os.path.isfile(v)]
            actions = ctk.CTkFrame(words, fg_color="transparent")
            actions.pack(anchor="w", pady=(6, 0))
            for text, command, usable in (
                    ("Open", lambda p=pid: self.open_project(p), True),
                    ("Play", lambda v=videos: os.startfile(v[0]), bool(videos)),
                    ("Show in folder", lambda v=videos: subprocess.Popen(
                        ["explorer", "/select,", os.path.normpath(v[0])]), bool(videos))):
                ctk.CTkButton(actions, text=text, width=10, height=24, corner_radius=4,
                              font=self.font_tiny, fg_color=CARD_BG, hover_color=CARD_HOVER,
                              border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                              state="normal" if usable else "disabled",
                              command=command).pack(side="left", padx=(0, 4))
        return card

    def _numbers_card(self, numbers, parent=None):
        card, body = self._home_card("Your videos", parent=parent)
        lines = [f"{numbers['made']} made · {numbers['week']} this week"]
        if numbers["last"]:
            last = numbers["last"]
            lines.append(f"Last: \"{last.get('title') or 'Untitled video'}\", "
                         f"{started.ago(last.get('updated') or time.time(), time.time())}")
        if numbers["usual"]:
            usual = numbers["usual"]
            lines.append("Usually takes " + (f"{usual} s" if usual < 90 else
                                             f"about {round(usual / 60)} min") + " on this PC")
        for at, line in enumerate(lines):
            ctk.CTkLabel(body, text=line, font=self.font_body,
                         text_color=TEXT if at == 0 else TEXT_MUTED, anchor="w").grid(
                row=at, column=0, columnspan=2, sticky="w", pady=1)
        return card

    def _features(self):
        """Home's "What this app can do": (glyph, title, line, button, action)."""
        found = [
            ("create", "Text to video", "Paste any text: the app writes the script, finds the "
             "pictures, reads it out and adds captions", "Make one",
             lambda: self.select_page("new")),
            ("play", "Show it on my PC", "Type a Windows tip: the app opens the setting and "
             "records each step for you", "Make a tip", lambda: TipDialog(self)),
            ("images", "AI pictures", "Pictures made on your PC for the scenes no stock clip "
             "can show", "Open", lambda: self.open_settings("images")),
            ("voice", "My voice", "Record six sentences, and your videos are read in a voice "
             "like yours", "Make my voice", lambda: MyVoiceDialog(self)),
            ("script", "Smart writer", "Writes the script from your text, offline: nothing is "
             "sent anywhere", "Open", lambda: self.open_settings("writer")),
            ("aspect", "Both shapes at once", "The same video as a Short and as a YouTube video, "
             "from the Storyboard", "Open My videos", lambda: self.select_page("projects")),
        ]
        return [f for f in found if f[1] != "My voice" or not myvoice.available()]

    def _features_card(self, parent):
        def right(head):
            self._link(head, "Show" if self.features_hidden else "Hide",
                       self.toggle_features).pack(side="right")

        card, body = self._home_card("What this app can do", right, parent)
        if self.features_hidden:
            body.grid_forget()   # folded to its title line
            return card
        features = self._features()
        # All in one row only when each card still has room for its text.
        roomy = self.winfo_width() / ctk.ScalingTracker.get_window_scaling(self) >= 1800
        per_row = len(features) if roomy else 3
        body.grid_columnconfigure(tuple(range(per_row)), weight=1, uniform="features")
        for at, (glyph, title, line, button, action) in enumerate(features):
            tile = ctk.CTkFrame(body, fg_color=FIELD_BG, corner_radius=6)
            tile.grid(row=at // per_row, column=at % per_row, sticky="nsew",
                      padx=(0 if at % per_row == 0 else 6, 0), pady=(0, 6))
            ctk.CTkLabel(tile, text=self.icon(glyph), font=self.font_icon_lg,
                         text_color=ACCENT).pack(anchor="w", padx=12, pady=(12, 4))
            ctk.CTkLabel(tile, text=title, font=self.font_bold, text_color=TEXT,
                         anchor="w").pack(fill="x", padx=12)
            ctk.CTkLabel(tile, text=line, font=self.font_tiny, text_color=TEXT_MUTED, anchor="w",
                         justify="left", wraplength=230).pack(
                fill="x", padx=12, pady=(2, 8))
            ctk.CTkButton(tile, text=button, height=28, corner_radius=4, font=self.font_tiny,
                          fg_color=CARD_BG, hover_color=CARD_HOVER, border_width=1,
                          border_color=FIELD_BORDER, text_color=TEXT,
                          command=action).pack(anchor="w", padx=12, pady=(0, 12))
        return card

    def toggle_features(self):
        self.features_hidden = not self.features_hidden
        save_settings(self._collect_settings())
        self.refresh_home()

    def mark_started(self, name):
        """A Get started step happened (a tip video made, a rewrite used)."""
        if name not in self.started["done"]:
            self.started["done"].append(name)
            save_settings(self._collect_settings())

    def skip_started(self, name):
        if name not in self.started["skipped"]:
            self.started["skipped"].append(name)
        save_settings(self._collect_settings())
        self.refresh_home()

    def hide_started(self):
        self.started["hidden"] = True
        save_settings(self._collect_settings())
        self.refresh_home()

    def show_started(self):
        """Help & about: bring the Get started list back, skipped steps included."""
        self.started.update(hidden=False, skipped=[])
        self.features_hidden = False
        save_settings(self._collect_settings())
        self.select_page("home")

    def refresh_estimate(self):
        """About how long the script is, and whether a chosen length is in reach."""
        if not hasattr(self, "estimate_label"):
            return
        try:
            parsed = parse_script(self.script_box.get("1.0", "end"))
        except ScriptError:
            self.estimate_label.configure(text="")
            return
        guess = pacing.estimate(
            parsed, self.voice_now(),
            PADDING_OPTIONS.get(self.var_padding.get(), DEFAULT_PADDING))
        text, warning = pacing.describe(guess, len(parsed),
                                        pacing.TARGETS.get(self.var_target.get()))
        pictures = sum(scene.is_ai for scene in parsed)
        if pictures and imagegen.installed():
            wait = pictures * 20 * (2 if self.var_versions.get() == "2" else 1)
            text += (f" · {pictures} AI picture{'s' if pictures != 1 else ''}, about "
                     + (f"{wait} s" if wait < 90 else f"{round(wait / 60)} min") + " more")
        self.estimate_label.configure(text=text,
                                      text_color=WARN_COLOR if warning else TEXT_MUTED)

    # -------------------------------------------------------------- CREATE --
    def _build_create(self, parent):
        page = ctk.CTkFrame(parent, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        self._caption(page, "Script", 0, pady=(0, 6))

        editor_card = Card(page)
        editor_card.grid(row=1, column=0, sticky="nsew", pady=(0, PAD))
        editor_card.grid_columnconfigure(0, weight=1)
        editor_card.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(editor_card, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        ctk.CTkLabel(header, text=self.icon("script"), font=self.font_icon,
                     text_color=TEXT_MUTED).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(header, text="Your script", font=self.font_body,
                     text_color=TEXT).pack(side="left")
        ctk.CTkLabel(header, text="Visual: and Voice: per scene · optional Duration:, "
                                  "Padding:, Zoom:",
                     font=self.font_tiny, text_color=TEXT_DIM).pack(side="right")

        self.script_box = ctk.CTkTextbox(
            editor_card, font=self.font_mono, corner_radius=4,
            fg_color=FIELD_BG, text_color=TEXT, border_width=0, wrap="word",
        )
        self.script_box.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        self.script_box.insert("1.0", self.settings["script"])
        self.script_box.bind("<KeyRelease>", self._on_script_changed)

        control_card = Card(page)
        control_card.grid(row=2, column=0, sticky="ew")
        control_card.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(control_card, fg_color="transparent")
        top.grid(row=4, column=0, sticky="ew", padx=16, pady=(12, 8))
        top.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(top, text="Ready", font=self.font_body,
                                         text_color=TEXT_MUTED, anchor="w")
        self.status_label.grid(row=0, column=0, sticky="w")
        # Pexels and Pixabay both ask apps to show where footage comes from.
        ctk.CTkLabel(top, text="Stock footage from Pexels and Pixabay",
                     font=self.font_tiny, text_color=TEXT_DIM,
                     anchor="w").grid(row=1, column=0, sticky="w")

        self.render_button = ctk.CTkButton(
            top, text=f"{self.icon('play')}   Make video", font=self.font_bold,
            width=190, height=38, corner_radius=4, fg_color=ACCENT,
            hover_color=ACCENT_HOVER, text_color=ACCENT_TEXT,
            command=self.start_render,
        )
        self.render_button.grid(row=0, column=3, sticky="e")
        self.scenes_button = ctk.CTkButton(
            top, text=f"{self.icon('scenes')}   Storyboard", font=self.font_body,
            width=150, height=38, corner_radius=4, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
            command=self.open_scene_preview,
        )
        self.scenes_button.grid(row=0, column=2, sticky="e", padx=(0, 8))
        self.draft_button = ctk.CTkButton(
            top, text=f"{self.icon('script')}   New from text", font=self.font_body,
            width=150, height=38, corner_radius=4, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
            command=self.open_draft,
        )
        self.draft_button.grid(row=0, column=1, sticky="e", padx=(0, 8))

        self.progress = ctk.CTkProgressBar(control_card, height=4, corner_radius=2,
                                           progress_color=ACCENT, fg_color=FIELD_BG)
        self.progress.grid(row=5, column=0, sticky="ew", padx=16, pady=(0, 16))
        self.progress.set(0)
        self.progress.grid_remove()
        return page

    def _shape_changed(self, *_args):
        """Another shape means other clips and other picture sizes: the Storyboard starts over."""
        self.board_clips, self.board_pos, self.board_text = {}, {}, None
        if self.current_page == "board":
            self.refresh_board(force=True)

    def _sync_look_bar(self):
        """Show the Storyboard's AI pictures row only while AI pictures are installed."""
        if not hasattr(self, "look_bar"):
            return
        if imagegen.installed():
            if not self.look_bar.winfo_manager():
                self.look_bar.pack(fill="x", padx=12, pady=(0, 8), before=self.style_card)
        else:
            self.look_bar.pack_forget()

    def _sync_caption_controls(self, *_args):
        """The caption choices only matter while captions are on."""
        state = "normal" if self.var_captions.get() else "disabled"
        for menu in self.caption_menus:
            menu.configure(state=state)
        self._draw_caption_sample()

    def _draw_caption_sample(self):
        """A few words drawn the way the captions will look: style, size, colour, place."""
        sample = getattr(self, "caption_sample", None)
        if sample is None:
            return
        if self._caption_words is not None:
            self._caption_words.destroy()
        self._caption_words = row = ctk.CTkFrame(sample, fg_color="transparent")
        if not self.var_captions.get():
            ctk.CTkLabel(row, text="Captions are off", font=self.font_tiny,
                         text_color="#8A8A8A").pack()
            row.place(relx=0.5, rely=0.5, anchor="center")
            return
        style = self.caption_vars["caption_style"].get()
        size = captions.SIZES.get(self.caption_vars["caption_size"].get(), 1.0)
        color = captions.COLORS.get(self.caption_vars["caption_color"].get())
        font = ctk.CTkFont(family=self.ui_family, size=round(15 * size), weight="bold")
        words = ("appear",) if style == "One word" else ("Words", "appear", "here")
        for word in words:
            lit = word == "appear" and style != "Plain" and color is not None
            fg, text = ("transparent", "#FFFFFF")
            if lit:   # Black is a black word on a white stroke: shown as a white pill
                fg, text = (color.stroke, color.fill) if color.fill == "#000000" else \
                    ("transparent", color.fill)
            elif color is not None and style == "Highlight":
                text = color.others
            ctk.CTkLabel(row, text=word.upper(), font=font, text_color=text, fg_color=fg,
                         corner_radius=3).pack(side="left", padx=3)
        where = captions.POSITIONS.get(self.caption_vars["caption_position"].get(), 0.72)
        row.place(relx=0.5, rely=min(max(where, 0.22), 0.78), anchor="center")

    def _on_script_changed(self, _event=None):
        if self._refresh_job:
            try:
                self.after_cancel(self._refresh_job)
            except Exception:
                pass
        self._refresh_job = self.after(350, self._commit_script)

    def _commit_script(self):
        self._refresh_job = None
        self.settings = self._collect_settings()
        save_settings(self.settings)
        self.refresh_home()
        self._show_board_nav()

    # ----------------------------------------------------------------- LOG --
    def _build_log(self, parent):
        page = ctk.CTkFrame(parent, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)

        self._caption(page, "Event log", 0, pady=(0, 6))

        card = Card(page)
        card.grid(row=1, column=0, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        ctk.CTkLabel(header, text=self.icon("log"), font=self.font_icon,
                     text_color=TEXT_MUTED).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(header, text="Output", font=self.font_body,
                     text_color=TEXT).pack(side="left")
        ctk.CTkButton(header, text=f"{self.icon('clear')}  Clear", width=88,
                      height=28, corner_radius=4, font=self.font_tiny,
                      fg_color=FIELD_BG, hover_color=CARD_HOVER,
                      border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                      command=self.clear_log).pack(side="right")
        # Packed only while a render is running (see _set_busy).
        self.log_cancel = ctk.CTkButton(
            header, text=f"{self.icon('stop')}  Cancel", width=88, height=28, corner_radius=4,
            font=self.font_tiny, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
            command=self.cancel_render)

        self.log_box = ctk.CTkTextbox(
            card, font=self.font_body, corner_radius=4, fg_color=FIELD_BG,
            text_color=LOG_BODY, border_width=0, wrap="word",
        )
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        self._configure_log_tags()

        # Render jumps to this page, so progress has to be visible here too.
        self.log_progress = ctk.CTkProgressBar(card, height=4, corner_radius=2,
                                               progress_color=ACCENT, fg_color=FIELD_BG)
        self.log_progress.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 14))
        self.log_progress.set(0)
        self.log_progress.grid_remove()

        self.log_empty = ctk.CTkLabel(
            card, text="Nothing logged yet.", font=self.font_body,
            text_color=TEXT_DIM, fg_color=FIELD_BG,   # blend into the well
        )
        self.log_empty.place(relx=0.5, rely=0.55, anchor="center")

        self.log_box.configure(state="disabled")
        return page

    def _configure_log_tags(self):
        """(Re)apply severity colours and spacing - rerun on a theme change.

        Margins live on the tags rather than the widget: CustomTkinter's
        configure() rejects lmargin/rmargin, but Tk text tags accept them.
        """
        for name, color in LOG_TAGS.items():
            self.log_box.tag_config(
                name, foreground=theme_color(color),
                spacing1=3, spacing3=3, lmargin1=10, lmargin2=28, rmargin=10,
            )
        # Defined last so it outranks the severity tags on lmargin1: an embedded
        # newline (the "saved to:" message) is a real line, not a wrapped one,
        # so it needs the hanging indent applied explicitly.
        self.log_box.tag_config("cont", lmargin1=28, spacing1=0)

    def append_log(self, message: str):
        """Main-thread only.  Worker threads go through UiBridge.log()."""
        self.session_log.write(message)
        self.recent_lines.extend(message.split("\n"))
        severity = log_severity(message)
        self.log_box.configure(state="normal")
        for index, line in enumerate(message.split("\n")):
            tags = (severity,) if index == 0 else (severity, "cont")
            self.log_box.insert("end", line + "\n", tags)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        self.log_empty.place_forget()
        last_line = message.strip().split("\n")[0]
        if hasattr(self, "status_label") and last_line:
            self.status_label.configure(text=ellipsize(last_line, 80))

    def clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")
        self.log_empty.place(relx=0.5, rely=0.55, anchor="center")

    # ------------------------------------------------------------ FEEDBACK --
    def _build_feedback(self, parent):
        page = self._scroll_page(parent)
        page.grid_columnconfigure(0, weight=1)

        self._caption(page, "About", 0, pady=(0, 6))

        card = Card(page)
        card.grid(row=1, column=0, sticky="ew", pady=(0, PAD))
        ctk.CTkLabel(card, text=f"{APP_NAME}  {APP_VERSION}", font=self.font_title,
                     text_color=TEXT, anchor="w").pack(anchor="w", padx=18, pady=(16, 2))
        ctk.CTkLabel(
            card,
            text="Pexels & Pixabay stock footage · edge-tts neural voices · MoviePy render",
            font=self.font_body, text_color=TEXT_MUTED, anchor="w",
        ).pack(anchor="w", padx=18, pady=(0, 10))
        update_row = ctk.CTkFrame(card, fg_color="transparent")
        update_row.pack(fill="x", padx=18, pady=(0, 16))
        self.update_button = ctk.CTkButton(
            update_row, text="Check for updates", width=140, height=30, corner_radius=4,
            font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
            command=lambda: self.check_for_updates(manual=True),
        )
        self.update_button.pack(side="left")
        self.update_status = ctk.CTkLabel(update_row, text="", font=self.font_body,
                                          text_color=TEXT_MUTED, anchor="w")
        self.update_status.pack(side="left", padx=(12, 0))

        self._caption(page, "System", 2)

        self.row_effect = InfoRow(page, self, self.icon("glass"), "Transparency", "-")
        self.row_effect.grid(row=3, column=0, sticky="ew", pady=PAD // 2)

        row_py = InfoRow(page, self, self.icon("info"), "Python",
                         sys.version.split()[0])
        row_py.grid(row=4, column=0, sticky="ew", pady=PAD // 2)

        row_ctk = InfoRow(page, self, self.icon("info"), "CustomTkinter",
                          getattr(ctk, "__version__", "?"))
        row_ctk.grid(row=5, column=0, sticky="ew", pady=PAD // 2)

        row_cfg = InfoRow(page, self, self.icon("folder"), "Settings",
                          ellipsize(SETTINGS_FILE, 54))
        row_cfg.grid(row=6, column=0, sticky="ew", pady=PAD // 2)

        self._caption(page, "Shortcuts", 7)

        actions = Card(page)
        actions.grid(row=8, column=0, sticky="ew", pady=PAD // 2)
        bar = ctk.CTkFrame(actions, fg_color="transparent")
        bar.pack(fill="x", padx=14, pady=14)
        ctk.CTkButton(bar, text=f"{self.icon('folder')}  Open output folder",
                      height=32, corner_radius=4, font=self.font_body,
                      fg_color=FIELD_BG, hover_color=CARD_HOVER,
                      border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                      command=self.open_output_folder).pack(side="left", padx=(0, 8))
        ctk.CTkButton(bar, text=f"{self.icon('settings')}  Open settings folder",
                      height=32, corner_radius=4, font=self.font_body,
                      fg_color=FIELD_BG, hover_color=CARD_HOVER,
                      border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                      command=self.open_settings_folder).pack(side="left", padx=(0, 8))
        ctk.CTkButton(bar, text=f"{self.icon('key')}  Setup guide",
                      height=32, corner_radius=4, font=self.font_body,
                      fg_color=FIELD_BG, hover_color=CARD_HOVER,
                      border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                      command=lambda: WelcomeDialog(self)).pack(side="left")
        ctk.CTkButton(bar, text=f"{self.icon('info')}  Show the tour",
                      height=32, corner_radius=4, font=self.font_body,
                      fg_color=FIELD_BG, hover_color=CARD_HOVER,
                      border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                      command=lambda: TourDialog(self)).pack(side="left", padx=(8, 0))
        ctk.CTkButton(bar, text=f"{self.icon('status')}  Show the Get started list",
                      height=32, corner_radius=4, font=self.font_body,
                      fg_color=FIELD_BG, hover_color=CARD_HOVER,
                      border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                      command=self.show_started).pack(side="left", padx=(8, 0))
        help_bar = ctk.CTkFrame(actions, fg_color="transparent")
        help_bar.pack(fill="x", padx=14, pady=(0, 14))
        ctk.CTkButton(help_bar, text=f"{self.icon('feedback')}  Report a problem",
                      height=32, corner_radius=4, font=self.font_body,
                      fg_color=FIELD_BG, hover_color=CARD_HOVER,
                      border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                      command=self.open_report).pack(side="left", padx=(0, 8))
        ctk.CTkButton(help_bar, text=f"{self.icon('log')}  Open log folder",
                      height=32, corner_radius=4, font=self.font_body,
                      fg_color=FIELD_BG, hover_color=CARD_HOVER,
                      border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                      command=lambda: self._open_made_folder(LOG_DIR)).pack(side="left")
        return page

    def open_output_folder(self):
        target = os.path.dirname(self.var_output.get().strip()) or default_output_dir()
        self._open_folder(target)

    # -- problem reports ------------------------------------------------------
    def scrub(self, text: str) -> str:
        """``text`` without keys or the names this PC's user goes by."""
        text = diagnostics.redact(text, [self.var_api.get(), self.var_pixabay.get()],
                                  os.environ.get("USERNAME", ""))
        # The profile folder can be named differently from the account.
        return diagnostics.redact(text, user_name=os.path.basename(os.path.expanduser("~")))

    def open_report(self, error=""):
        """Show the report window. Nothing leaves the PC unless the user sends it."""
        report = diagnostics.build_report(
            APP_VERSION, IS_FROZEN, self._collect_settings(), list(self.recent_lines),
            error, os.environ.get("USERNAME", ""))
        ReportDialog(self, self.scrub(report), error)

    def report_callback_exception(self, exc, val, tb):
        """Tk calls this for an error in a button or timer: keep it, don't lose it."""
        trace = "".join(traceback.format_exception(exc, val, tb))
        self.session_log.write(trace)
        self._unexpected(trace)

    def _unexpected(self, trace: str):
        """Main thread. Put an already-logged trace in the Log (Storyboard > Log) and into reports."""
        try:
            self.recent_lines.extend(trace.rstrip().split("\n"))
            last = trace.strip().splitlines()[-1] if trace.strip() else "unknown"
            self.append_log(f"❌ Unexpected error: {last}")
        except Exception:
            pass   # reporting an error must never raise another

    def _install_error_hooks(self):
        """Keep uncaught errors from any thread.

        The trace is written to the log file at once, in case the app is going
        down, then handed to the window through the UI queue. The normal
        handlers still run, so a console shows the error as before.
        """
        usual_thread_hook, usual_hook = threading.excepthook, sys.excepthook

        def keep(trace):
            self.session_log.write(trace)
            self._queue.put(("crash", {"trace": trace}))

        def from_thread(args):
            keep("".join(traceback.format_exception(
                args.exc_type, args.exc_value, args.exc_traceback)))
            usual_thread_hook(args)

        def from_main(exc, val, tb):
            keep("".join(traceback.format_exception(exc, val, tb)))
            usual_hook(exc, val, tb)

        threading.excepthook = from_thread
        sys.excepthook = from_main

    def open_settings_folder(self):
        self._open_made_folder(SETTINGS_DIR)

    def _open_made_folder(self, folder):
        """Open ``folder`` in Explorer, creating it first; say so if it can't be."""
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            self.append_log(f"⚠️ Couldn't create {folder} ({exc.strerror or exc}).")
            return
        self._open_folder(folder)

    @staticmethod
    def _open_folder(path):
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass

    # ------------------------------------------------------------ SETTINGS --
    def _build_settings(self):
        """Settings in vertical tabs: a tab list on the left, one page per tab."""
        outer = ctk.CTkFrame(self.content, fg_color="transparent")
        outer.grid_columnconfigure(1, weight=1)
        outer.grid_rowconfigure(0, weight=1)
        self.settings_nav = ctk.CTkFrame(outer, fg_color="transparent")
        self.settings_nav.grid(row=0, column=0, sticky="ns", padx=(0, 16))
        self.settings_body = ctk.CTkFrame(outer, fg_color="transparent")
        self.settings_body.grid(row=0, column=1, sticky="nsew")
        self.settings_body.grid_rowconfigure(1, weight=1)
        self.settings_intro = ctk.CTkLabel(self.settings_body, text="", font=self.font_body,
                                           text_color=TEXT_MUTED, anchor="w", justify="left")
        self.settings_intro.grid(row=0, column=0, sticky="ew", padx=4, pady=(0, 10))
        self.settings_body.grid_columnconfigure(0, weight=1)
        self.settings_tabs, self.settings_tab_items = {}, {}
        self.settings_tab = "footage"   # the last tab shown, kept while the app runs

        page, rows = self._settings_tab("footage", "key", "Footage keys"), itertools.count()
        self._caption(page, "Stock footage", next(rows), pady=(0, 6))
        self.api_entry = self._key_row(
            page, next(rows), "Pexels", "Pexels API key",
            "One footage key is enough. Stored locally in settings.json",
            self.var_api, "Paste your Pexels API key",
        )
        self.pixabay_entry = self._key_row(
            page, next(rows), "Pixabay", "Pixabay API key",
            "Works on its own, or together with Pexels: the best clip wins",
            self.var_pixabay, "Paste your Pixabay API key",
        )
        order_row = SettingRow(page, self, self.icon("scenes"), "With both keys",
                               "Both: each scene gets the best-fitting clip from either site")
        order_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self._segmented(order_row.control, list(footage.ORDERS), self.var_footage_order, 330)

        page, rows = self._settings_tab("video", "resolution", "Video & saving"), itertools.count()
        self._caption(page, "Output", next(rows), pady=(0, 6))

        path_row = SettingRow(page, self, self.icon("folder"), "Save video to",
                              "Default folder and name for new videos")
        path_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        focus_accent(ctk.CTkEntry(
            path_row.control, textvariable=self.var_output, width=300, height=32,
            corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
            border_color=FIELD_BORDER, border_width=1, text_color=TEXT,
        )).pack(side="left")
        ctk.CTkButton(
            path_row.control, text="Browse", width=84, height=32, corner_radius=4,
            font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER,
            text_color=TEXT, command=self.choose_save_location,
        ).pack(side="left", padx=(6, 0))

        ask_row = SettingRow(page, self, self.icon("folder"), "Ask where to save each video",
                             "Opens a Save window when you render. Off: saves to the path "
                             "above, numbering new videos so none is overwritten")
        ask_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkSwitch(
            ask_row.control, text="", width=44, variable=self.var_ask_save,
            onvalue=True, offvalue=False, progress_color=ACCENT,
        ).pack(side="left")

        self._caption(page, "Video", next(rows))

        res_row = SettingRow(page, self, self.icon("resolution"), "Resolution",
                             "1080p renders at 8000k, 720p at 5000k")
        res_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self._segmented(res_row.control, RESOLUTION_OPTIONS, self.var_resolution, 170)

        padding_row = SettingRow(page, self, self.icon("timer"), "Scene padding",
                                 "Pause after each voice line. A script's Padding: overrides it")
        padding_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self._segmented(padding_row.control, list(PADDING_OPTIONS), self.var_padding, 260)

        motion_row = SettingRow(page, self, self.icon("scenes"), "Cuts on the beat, smart framing",
                                "Scene changes land on the music's beats; the picture moves "
                                "toward where the detail is")
        motion_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkSwitch(motion_row.control, text="", width=44, variable=self.var_smart_motion,
                      onvalue=True, offvalue=False, progress_color=ACCENT).pack(side="left")
        qsv_row = SettingRow(page, self, self.icon("scenes"), "Encode on the Intel graphics chip",
                             "Quick Sync: faster on Intel laptops. Falls back to the normal "
                             "encoder by itself where it isn't there or fails")
        qsv_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkSwitch(qsv_row.control, text="", width=44, variable=self.var_quick_sync,
                      onvalue=True, offvalue=False, progress_color=ACCENT).pack(side="left")
        cache_row = SettingRow(page, self, self.icon("folder"), "Kept clips and voices",
                               "Re-renders reuse downloaded clips and spoken lines. "
                               f"Limit {cache.LIMIT_BYTES // 1024 ** 3} GB; oldest go first")
        cache_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self.cache_label = ctk.CTkLabel(cache_row.control, text="", font=self.font_body,
                                        text_color=TEXT_MUTED)
        ctk.CTkButton(
            cache_row.control, text="Clear cache", width=110, height=32, corner_radius=4,
            font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER, border_width=1,
            border_color=FIELD_BORDER, text_color=TEXT, command=self.clear_cache,
        ).pack(side="left")
        self.cache_label.pack(side="left", padx=(10, 0))
        self._show_cache_size()

        self._caption(page, "Start and end cards", next(rows))
        for var_on, var_text, title, subtitle, hint in (
                (self.var_intro_on, self.var_intro_text, "Channel name card",
                 "A 2-second card before every video", "Your channel name"),
                (self.var_outro_on, self.var_outro_text, "End card",
                 "A 2-second card after every video", "Subscribe for more")):
            bookend_row = SettingRow(page, self, self.icon("captions"), title, subtitle)
            bookend_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
            ctk.CTkSwitch(bookend_row.control, text="", width=44, variable=var_on,
                          onvalue=True, offvalue=False, progress_color=ACCENT,
                          ).pack(side="left", padx=(0, 10))
            focus_accent(ctk.CTkEntry(
                bookend_row.control, textvariable=var_text, width=240, height=32,
                corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
                border_color=FIELD_BORDER, text_color=TEXT, placeholder_text=hint,
            )).pack(side="left")

        page, rows = self._settings_tab("voice", "voice", "Voice & music"), itertools.count()
        self._caption(page, "Voice", next(rows), pady=(0, 6))
        why_not = myvoice.available()   # an NVIDIA card with 6 GB or more (checked once)
        mine_row = SettingRow(page, self, self.icon("voice"), "My voice (beta)",
                              why_not or "Record six sentences, and scripts are read in a voice "
                                         "like yours. Needs the voice pack (about 7 GB)")
        mine_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkButton(mine_row.control, text="Make my voice", width=130, height=32,
                      corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
                      hover_color=CARD_HOVER, border_width=1, border_color=FIELD_BORDER,
                      text_color=TEXT, command=lambda: MyVoiceDialog(self),
                      state="disabled" if why_not else "normal").pack(side="left")

        voice_row = SettingRow(
            page, self, self.icon("voice"), "Offline voice",
            f"{localvoice.MODEL.name}, an open-source voice that speaks on this PC. "
            "No internet needed once it is downloaded")
        voice_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self.voice_state = ctk.CTkLabel(voice_row.control, text="", font=self.font_body,
                                        text_color=TEXT_MUTED)
        self.voice_state.pack(side="left", padx=(0, 12))
        self.voice_button = ctk.CTkButton(
            voice_row.control, text="Download", width=96, height=32, corner_radius=4,
            font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
            command=self.start_voice_download)
        self.voice_button.pack(side="left")
        self._voice_row_changed()

        online_row = SettingRow(
            page, self, self.icon("info"), "Use Microsoft's online voices",
            "The voices the app used before 3.8. Microsoft's service stopped answering in "
            "October 2026, so leave this off unless it works again")
        online_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkSwitch(
            online_row.control, text="", width=44, variable=self.var_voice_online,
            onvalue=True, offvalue=False, progress_color=ACCENT,
        ).pack(side="left")

        self._caption(page, "Music and sound", next(rows))

        music_row = SettingRow(page, self, self.icon("music"), "Background music",
                               "Plays under the video and ducks while the voice speaks")
        music_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkSwitch(
            music_row.control, text="", width=44, variable=self.var_music_enabled,
            onvalue=True, offvalue=False, progress_color=ACCENT,
        ).pack(side="left")

        track_row = SettingRow(page, self, self.icon("folder"), "Track",
                               "Drop tracks in the Music folder, or browse for any file")
        track_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self.music_menu = ctk.CTkOptionMenu(
            track_row.control, values=["No tracks yet"], variable=self.var_music_track,
            command=self._pick_track, width=220, height=32, corner_radius=4,
            font=self.font_body, fg_color=FIELD_BG, button_color=FIELD_BG,
            button_hover_color=CARD_HOVER, text_color=TEXT, dropdown_fg_color=CARD_BG,
            dropdown_text_color=TEXT, dropdown_hover_color=CARD_HOVER,
            dropdown_font=self.font_body, dynamic_resizing=False,
        )
        self.music_menu.pack(side="left")
        for label, command in (("Browse", self.browse_music),
                               ("Open folder", self.open_music_folder)):
            ctk.CTkButton(
                track_row.control, text=label, width=96, height=32, corner_radius=4,
                font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                border_width=1, border_color=FIELD_BORDER,
                text_color=TEXT, command=command,
            ).pack(side="left", padx=(6, 0))
        self._refresh_tracks()

        sfx_row = SettingRow(page, self, self.icon("music"), "Sound effects",
                             "A soft pop as a card appears")
        sfx_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkSwitch(sfx_row.control, text="", width=44, variable=self.var_sfx, onvalue=True,
                      offvalue=False, progress_color=ACCENT).pack(side="left")

        page, rows = self._settings_tab("writer", "script", "Smart writer"), itertools.count()
        self._caption(page, "Script writer", next(rows), pady=(0, 6))
        web_row = SettingRow(page, self, self.icon("info"), "Look facts up on Wikipedia",
                             "For an idea that asks for facts (top 5, latest, fastest...), the "
                             "Smart writer reads Wikipedia first. Only the search words are sent")
        web_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkSwitch(web_row.control, text="", width=44, variable=self.var_web_facts,
                      onvalue=True, offvalue=False, progress_color=ACCENT).pack(side="left")

        writer_row = SettingRow(
            page, self, self.icon("script"), "Smart writer",
            f"{writer.MODEL.name}, an open-source AI that writes scripts on this PC. "
            "Nothing you paste is sent anywhere")
        writer_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self.writer_state = ctk.CTkLabel(writer_row.control, text="", font=self.font_body,
                                         text_color=TEXT_MUTED)
        self.writer_state.pack(side="left", padx=(0, 12))
        self.writer_button = ctk.CTkButton(
            writer_row.control, text="", width=96, height=32, corner_radius=4,
            font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT)
        self.writer_button.pack(side="left")
        self.writer_listeners.append(self._writer_row_changed)
        self._writer_row_changed()

        gpu_row = SettingRow(
            page, self, self.icon("resolution"), "Use the graphics card",
            "Faster on PCs with a dedicated graphics card. Turn it off if the Smart "
            "writer fails to start")
        gpu_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkSwitch(
            gpu_row.control, text="", width=44, variable=self.var_writer_gpu,
            onvalue=True, offvalue=False, progress_color=ACCENT,
        ).pack(side="left")

        self.check_clips_row = SettingRow(
            page, self, self.icon("images"), "Check stock clips",
            "Before a video is made, the Smart writer and the photo model look at each "
            "stock clip and swap ones that show something else. Adds about a minute")
        self.check_clips_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkSwitch(
            self.check_clips_row.control, text="", width=44, variable=self.var_check_clips,
            onvalue=True, offvalue=False, progress_color=ACCENT,
        ).pack(side="left")
        self.writer_listeners.append(self._check_clips_row_shown)
        self._check_clips_row_shown()

        cards_row = SettingRow(
            page, self, self.icon("script"), "Add cards automatically",
            "New from text puts key combos like Win + V and big numbers on screen as "
            "animated cards. You can edit or delete the Card: lines in the script")
        cards_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkSwitch(
            cards_row.control, text="", width=44, variable=self.var_auto_cards,
            onvalue=True, offvalue=False, progress_color=ACCENT,
        ).pack(side="left")

        self._settings_tab("images", "images", "AI pictures",
                           self._build_images(self.settings_body))
        page, rows = self._settings_tab("app", "theme", "App & updates"), itertools.count()
        self._caption(page, "Appearance", next(rows), pady=(0, 6))

        theme_row = SettingRow(page, self, self.icon("theme"), "Dark theme",
                               "Switches the whole app between dark and light")
        theme_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkSwitch(
            theme_row.control, text="", width=44, variable=self.var_theme,
            onvalue="dark", offvalue="light", progress_color=ACCENT,
            command=self.apply_theme,
        ).pack(side="left")

        glass_row = SettingRow(page, self, self.icon("glass"), "Window translucency",
                               "Mica / acrylic where supported, alpha elsewhere")
        glass_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkSwitch(
            glass_row.control, text="", width=44, variable=self.var_translucent,
            onvalue=True, offvalue=False, progress_color=ACCENT,
            command=self.apply_effects,
        ).pack(side="left")

        self._caption(page, "Updates", next(rows))

        updates_row = SettingRow(page, self, self.icon("info"), "Check for updates on startup",
                                 "Asks GitHub for the latest version number. Nothing is "
                                 "installed automatically")
        updates_row.grid(row=next(rows), column=0, sticky="ew", pady=(PAD // 2, PAD))
        ctk.CTkSwitch(
            updates_row.control, text="", width=44, variable=self.var_check_updates,
            onvalue=True, offvalue=False, progress_color=ACCENT,
        ).pack(side="left")
        self._settings_tab("help", "feedback", "Help & about",
                           self._build_feedback(self.settings_body))
        self.script_editor = self._build_create(self.settings_body)
        self._settings_tab("advanced", "create", "Advanced", self.script_editor)
        self._show_settings_tab(self.settings_tab)
        return outer

    def _settings_tab(self, key, glyph, text, page=None):
        """A Settings tab: its entry in the tab list and its own page (scrolling by default)."""
        if page is None:
            page = self._scroll_page(self.settings_body)
            page.grid_columnconfigure(0, weight=1)
        if isinstance(page, ctk.CTkScrollableFrame):
            page.configure(width=SETTINGS_WIDTH)
        self.settings_tabs[key] = page
        item = NavItem(self.settings_nav, self, key, self.icon(glyph), text,
                       self._show_settings_tab)
        item.pack(fill="x", pady=1)
        self.settings_tab_items[key] = item
        return page

    def _show_settings_tab(self, key):
        for name, page in self.settings_tabs.items():
            if name == key:
                capped = self.wide and isinstance(page, ctk.CTkScrollableFrame)
                page.grid(row=1, column=0, sticky="nsw" if capped else "nsew")
            else:
                page.grid_remove()
            self.settings_tab_items[name].set_selected(name == key)
        self.settings_tab = key
        self.settings_intro.configure(text=TAB_INTROS.get(key, ""))

    def _layout_settings(self):
        if hasattr(self, "settings_tabs"):
            self._show_settings_tab(self.settings_tab)

    def open_settings(self, tab):
        """Settings, opened on one tab (e.g. from a Home setup button)."""
        self.select_page("settings")
        self._show_settings_tab(tab)

    def _key_row(self, page, row, provider, title, subtitle, variable, placeholder):
        """A masked API-key entry with reveal and test buttons. Returns the entry."""
        key_row = SettingRow(page, self, self.icon("key"), title, subtitle)
        key_row.grid(row=row, column=0, sticky="ew", pady=PAD // 2)
        entry = ctk.CTkEntry(
            key_row.control, textvariable=variable, width=260, height=32,
            corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
            border_color=FIELD_BORDER, border_width=1, text_color=TEXT,
            placeholder_text=placeholder, show="•",
        )
        focus_accent(entry).pack(side="left")
        ctk.CTkButton(
            key_row.control, text=self.icon("eye"), font=self.font_icon, width=34,
            height=32, corner_radius=4, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER,
            text_color=TEXT_MUTED, command=lambda: self.toggle_key_visibility(entry),
        ).pack(side="left", padx=(6, 0))
        test = ctk.CTkButton(
            key_row.control, text="Test", width=64, height=32, corner_radius=4,
            font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
        )
        test.pack(side="left", padx=(6, 0))

        def done(ok, message):
            test.configure(state="normal", text="Works" if ok else "Failed",
                           text_color=OK_COLOR if ok else ERR_COLOR)
            self.append_log(f"{'✅' if ok else '⚠️'} {provider} key: {message}")

        def run():
            test.configure(state="disabled", text="...", text_color=TEXT)
            self.test_key(provider, variable.get(), done)

        test.configure(command=run)
        return entry

    def test_key(self, provider, key, callback):
        """Check a footage key off the UI thread; ``callback(ok, message)`` on it."""
        token = object()
        self._key_tests[token] = callback

        def worker():
            try:
                ok, message = footage.check_key(provider, key)
            except Exception as exc:  # noqa: BLE001 - reported in the window
                ok, message = False, f"The test failed ({exc})."
            self._queue.put(("keytest", {"token": token, "ok": ok, "message": message}))

        threading.Thread(target=worker, daemon=True).start()

    def _segmented(self, parent, values, variable, width):
        FluentSegmented(parent, self, values, variable, width).pack(side="left")

    def toggle_key_visibility(self, entry=None):
        entry = entry or self.api_entry
        showing = entry.cget("show") == ""
        entry.configure(show="•" if showing else "")

    # -- background music ----------------------------------------------------
    def _refresh_tracks(self):
        """Re-read the Music folder; a browsed file outside it stays listed."""
        if not hasattr(self, "music_menu"):
            return
        tracks = {AUTO_LABEL: AUTO_TRACK, **music_tracks()} if bundled_tracks()             else music_tracks()
        if self.music_path and os.path.isfile(self.music_path):
            known = {os.path.normcase(p) for p in tracks.values()}
            if os.path.normcase(self.music_path) not in known:
                tracks[f"{os.path.basename(self.music_path)} (browsed)"] = self.music_path
        self._tracks = tracks
        self.music_menu.configure(values=list(tracks) or ["No tracks yet"])
        current = next(
            (name for name, path in tracks.items()
             if os.path.normcase(path) == os.path.normcase(self.music_path or "")),
            None,
        )
        self.var_music_track.set(current or ("Choose a track" if tracks else "No tracks yet"))

    def _pick_track(self, name):
        path = self._tracks.get(name)
        if not path:
            return
        self.music_path = path
        # Choosing a track means wanting music; don't make them flip the switch too.
        self.var_music_enabled.set(True)
        self._on_setting_changed()

    def browse_music(self):
        start = MUSIC_DIR if os.path.isdir(MUSIC_DIR) else os.path.expanduser("~")
        chosen = filedialog.askopenfilename(
            title="Choose background music",
            initialdir=start,
            filetypes=[("Audio", " ".join(f"*{ext}" for ext in MUSIC_EXTS)),
                       ("All Files", "*.*")],
        )
        if chosen:
            self.music_path = os.path.normpath(chosen)
            self.var_music_enabled.set(True)
            self._on_setting_changed()
            self._refresh_tracks()

    def open_music_folder(self):
        self._open_made_folder(MUSIC_DIR)

    def _save_dialog(self, suggested, title):
        """The Windows Save window, opened on ``suggested``. '' if cancelled.

        It opens in a folder that exists and takes files - Windows' own Save
        window answers "file not found" in a protected one - and it never
        hands back a path into a folder that isn't there.
        """
        suggested = suggested or os.path.join(default_output_dir(), "final_video.mp4")
        folder = os.path.dirname(os.path.abspath(suggested))
        name = os.path.basename(suggested) or "final_video.mp4"
        try:
            usable, moved = paths.usable_output(os.path.join(folder, name), paths.safe_folders())
        except paths.SaveError as exc:
            Dialog(self, "Can't save here", str(exc), ok=False)
            return ""
        if moved:
            self.append_log(f"⚠️ Couldn't save to {folder} - using "
                            f"{os.path.dirname(usable)} instead.")
            usable = next_free_path(usable)
        try:
            chosen = filedialog.asksaveasfilename(
                parent=self, title=title, defaultextension=".mp4",
                filetypes=[("MP4 Video", "*.mp4"), ("All Files", "*.*")],
                initialdir=paths.existing_dir(os.path.dirname(usable), default_output_dir()),
                initialfile=os.path.basename(usable),
            )
        except tk.TclError as exc:
            self.append_log(f"⚠️ The Save window couldn't open ({exc}).")
            return ""
        if not chosen:
            return ""
        chosen = paths.as_video(os.path.normpath(chosen))
        if not paths.is_writable(os.path.dirname(chosen)):
            Dialog(self, "Can't save here",
                   paths.cannot_save_message(os.path.dirname(chosen)), ok=False)
            return ""
        return chosen

    def choose_save_location(self):
        chosen = self._save_dialog(self.var_output.get().strip(), "Default save location")
        if chosen:
            self.var_output.set(chosen)

    def _ask_save_path(self):
        """Where this render goes. Suggests a name that isn't taken yet."""
        current = self.var_output.get().strip() or os.path.join(
            default_output_dir(), "final_video.mp4")
        return self._save_dialog(next_free_path(os.path.abspath(current)), "Save video as")

    def apply_theme(self):
        ctk.set_appearance_mode(self.var_theme.get())
        self._configure_log_tags()   # text tags hold single colours, not pairs
        self.apply_effects()         # also saves the settings

    def apply_effects(self):
        self.effect_note = apply_window_effects(
            self,
            dark=self.var_theme.get() == "dark",
            translucent=bool(self.var_translucent.get()),
        )
        if hasattr(self, "row_effect"):
            self.row_effect.set_value(self.effect_note)
        self._on_setting_changed()

    def _put_back_left(self, entry):
        """Worker: open the page and switch back what a recording left flipped, then check."""
        place = winmap.BY_ID.get(entry["place"])
        name, was = entry["switch"], entry["state"]
        hwnd = 0
        if place is not None:
            try:
                hwnd = screen.open_window(place.command())
            except OSError:
                hwnd = 0
        if hwnd and drive.flip(hwnd, name, was):
            putback.clear(SETTINGS_DIR)
            self.call_soon(lambda: self.append_log(
                f"↩️ {name} is back {was.lower()}: the app had changed it for a video and "
                "was closed before it could switch it back."))
        elif place is None:
            putback.clear(SETTINGS_DIR)
        else:
            where = place.name
            self.call_soon(lambda: self.append_log(
                f"⚠️ A video recording left {name} changed. Switch it back to {was.lower()} in "
                f"{where}; the app will try again next time it starts."))

    # -- startup / queue -----------------------------------------------------
    def _post_init(self):
        self.apply_effects()
        self._install_error_hooks()
        self.append_log(f"{APP_NAME} {APP_VERSION} ready.")
        self.append_log(f"⏱️ Window ready in {time.perf_counter() - self._launched:.1f} s")
        self.append_log(f"🪟 Window effect: {self.effect_note}")
        self.append_log(f"🎞️ ffmpeg: {FFMPEG_EXE or 'system / imageio auto-detect'}")
        if not self.var_api.get().strip() and not self.var_pixabay.get().strip():
            self.append_log(
                "🎨 No stock footage key: every scene will get an AI picture."
                if imagegen.installed() else
                "⚠️ No footage API key yet - add a Pexels or Pixabay key in Settings > Footage keys.")
        self.refresh_home()
        threading.Thread(target=self._warm_moviepy, daemon=True).start()
        left = putback.read(SETTINGS_DIR)
        if left:   # the app closed while a switch it flipped for a video was still flipped
            threading.Thread(target=self._put_back_left, args=(left,), daemon=True).start()
        if self.version_since.get("version") != APP_VERSION:
            # A first install has nothing "new": its What's new line never shows.
            self.version_since = {"version": APP_VERSION,
                                  "at": time.time() if self.tour_seen else 0}
            save_settings(self._collect_settings())
            self.refresh_home()
        if not self.tour_seen:
            self.whats_new_seen = APP_VERSION   # a first install: nothing is "new" yet
            self.after(300, lambda: TourDialog(self))   # it leads into the key setup itself
        elif show_whats_new(self.whats_new_seen, self.whats_new_off, False):
            self.after(300, lambda: WhatsNewDialog(self))
        elif not self._has_footage_key() and not self.welcome_seen:
            self.after(300, lambda: WelcomeDialog(self))
        self.detect_graphics()
        self.look_for_graphics()
        if self.var_check_updates.get():
            self.check_for_updates()

    # -- updates -------------------------------------------------------------
    def check_for_updates(self, manual=False):
        """Ask GitHub for the latest release, off the UI thread.

        ``manual`` is the Check button: only then is a failed check mentioned.
        """
        if manual:
            self.update_button.configure(state="disabled")
            self.update_status.configure(text="Checking...", text_color=TEXT_MUTED)

        def worker():
            try:
                found = updates.latest(APP_VERSION)
            except Exception:  # noqa: BLE001 - an update check never interrupts
                found = None
            self._queue.put(("update", {"found": found, "manual": manual}))

        threading.Thread(target=worker, daemon=True).start()

    def _update_checked(self, found, manual):
        self.update_button.configure(state="normal")
        if found is None:
            if manual:
                self.update_status.configure(
                    text="Couldn't check right now - try again later.", text_color=TEXT_MUTED)
            return
        tag, page = found.tag, found.page
        if updates.is_newer(tag, APP_VERSION):
            version = found.version
            self.update_url = page
            if not self._updating:
                self.update_release = found
                self._offer_update()
            self.update_status.configure(text=f"Version {version} is available.",
                                         text_color=ACCENT)
        else:
            self.update_status.configure(text="You have the latest version.",
                                         text_color=OK_COLOR)

    def open_update_page(self):
        webbrowser.open(self.update_url)

    # -- updating from inside the app ----------------------------------------
    def _app_dir(self) -> str:
        """The folder this copy of the app runs from."""
        return os.path.dirname(os.path.abspath(sys.executable))

    def _can_self_update(self) -> bool:
        """A built copy: installed ones run the new installer, portable ones the new ZIP."""
        found = self.update_release
        if not (IS_FROZEN and found):
            return False
        if updates.is_installed_copy(self._app_dir()):
            return found.can_install
        return found.can_unpack and updates.update_target(self._app_dir()) is not None

    def _why_no_update(self) -> str:
        """Why Update now isn't offered, in a few words for the bar and the log."""
        found = self.update_release
        if not IS_FROZEN:
            return "This copy runs from source, so it can't replace itself."
        if updates.is_installed_copy(self._app_dir()):
            return "" if found.can_install else "This release has no installer the app can use."
        if not found.can_unpack:
            return "This release has no portable version the app can use."
        return ("This PC doesn't let the app write its own folder or AppData, so it can't "
                "update itself.")

    def _offer_update(self, message=None, allow_install=True):
        """Show the bar: Update now where the app can do it, the release page otherwise."""
        found = self.update_release
        self.update_bar_label.configure(
            text=message or f"Version {found.version} is available. You have {APP_VERSION}.")
        if allow_install and self._can_self_update():
            self.update_action.configure(text="Update now", command=self.start_update,
                                         state="normal")
        else:
            why = self._why_no_update() if allow_install else ""
            if why and not message:   # say why, instead of only sending to the page
                self.update_bar_label.configure(
                    text=f"Version {found.version} is available. {why}")
                self.append_log(f"ℹ️ Update: {why} Download it from the release page.")
            self.update_action.configure(text="Download", command=self.open_update_page,
                                         state="normal")
        self.update_later.configure(text="Later", command=self.update_bar.grid_remove)
        self.update_bar.grid()

    def start_update(self):
        """Download the new installer (checked against the release's checksum), then run it."""
        if self._updating or not self._can_self_update():
            return
        if self.is_rendering:
            self._offer_update("Finish or cancel the render first, then press Update now.")
            return
        found = self.update_release
        self._updating = True
        self._update_stop = stop = threading.Event()
        self.update_bar_label.configure(text=f"Downloading version {found.version}...")
        self.update_action.configure(text="Updating...", state="disabled")
        self.update_later.configure(text="Cancel", command=stop.set)
        self.append_log(f"⬇️ Downloading version {found.version}...")
        folder = os.path.join(TEMP_ROOT, "update")
        installed = updates.is_installed_copy(self._app_dir())

        def worker():
            last = 0.0

            def progress(done, total):
                nonlocal last
                now = time.monotonic()
                if now - last >= 0.25 or done >= total:
                    last = now
                    self._queue.put(("updater", {"event": "progress", "done": done,
                                                 "total": total}))

            try:
                if installed:
                    path = updates.download_installer(found, folder, progress, stop.is_set)
                else:
                    archive = updates.download_portable(found, folder, progress, stop.is_set)
                    path = updates.unpack_portable(archive, os.path.join(folder, "portable"))
                    os.remove(archive)   # unpacked: the 120 MB ZIP isn't needed again
                self._queue.put(("updater", {"event": "ready", "path": path}))
            except footage.Stopped:
                self._queue.put(("updater", {"event": "stopped"}))
            except updates.UpdateError as exc:
                self._queue.put(("updater", {"event": "failed", "message": str(exc)}))
            except Exception as exc:  # noqa: BLE001 - a thread must not die silently
                self._queue.put(("updater", {
                    "event": "failed", "trace": traceback.format_exc(),
                    "message": f"The update hit an unexpected problem ({type(exc).__name__})."}))

        threading.Thread(target=worker, daemon=True).start()

    def _updater_event(self, payload):
        """UI thread: the update download moved on."""
        event, found = payload["event"], self.update_release
        if event == "progress":
            self.update_bar_label.configure(
                text=f"Downloading version {found.version}: {payload['done'] / 1e6:.0f} of "
                     f"{payload['total'] / 1e6:.0f} MB")
            return
        self._updating = False
        if payload.get("trace"):
            self.session_log.write(payload["trace"])
        if event == "ready":
            self._install_update(payload["path"])
        elif event == "stopped":
            self.append_log("⏹️ Update cancelled.")
            self._offer_update()
        else:
            self.append_log(f"⚠️ {payload['message']}")
            # Whatever went wrong, the release page still works.
            self._offer_update(f"{payload['message']} You can still get it from the "
                               "release page.", allow_install=False)

    def _install_update(self, path):
        """Hand over to the installer and close, so it can replace this copy's files."""
        if self.is_rendering:   # a render was started while the update downloaded
            self._offer_update("The update is ready. Finish or cancel the render first, "
                               "then press Update now.")
            return
        try:
            if updates.is_installed_copy(self._app_dir()):
                command = updates.installer_command(path, self._app_dir())
                # Detached: the installer must outlive this process, which it replaces.
                flags = getattr(subprocess, "DETACHED_PROCESS", 0)
            else:
                # Portable: a script copies the unpacked app over this one once it has closed,
                # or into AppData when this folder can't be written (a school PC, a USB stick).
                target = updates.update_target(self._app_dir()) or self._app_dir()
                moved = os.path.normcase(target) != os.path.normcase(self._app_dir())
                if moved:
                    self.append_log(f"📦 This copy's folder is locked: the new version goes to "
                                    f"{target} and opens from there.")
                script = os.path.join(TEMP_ROOT, "update", "update.cmd")
                with open(script, "w", encoding="utf-8") as fh:
                    fh.write(updates.portable_script(path, target, os.getpid(), shortcut=moved))
                command = ["cmd", "/c", script]
                # A hidden console, shared by robocopy, instead of windows popping up.
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            subprocess.Popen(command, creationflags=flags, close_fds=True)
        except OSError as exc:
            self.append_log(f"⚠️ The installer could not be started: {exc}")
            self._offer_update("The update could not be started. You can still get it from "
                               "the release page.", allow_install=False)
            return
        self.append_log("🔄 Installing the update; the app will reopen.")
        # Nothing of ours may still be running when the installer replaces the files.
        self._writer_stop.set()
        for window in list(self.winfo_children()):
            if isinstance(window, DraftDialog):
                window.close()
        self._on_close()

    def _has_footage_key(self) -> bool:
        return bool(self.var_api.get().strip() or self.var_pixabay.get().strip())

    def _needs_stock_footage(self) -> bool:
        """Whether the script has a scene that isn't the user's own file."""
        try:
            scenes = parse_script(self.script_box.get("1.0", "end"))
        except ScriptError:
            return False   # let the render report the script problem itself
        return any(not scene.is_local and not scene.is_ai for scene in scenes)

    def _warm_moviepy(self):
        sweep_old_work_dirs()
        cache.trim(CACHE_ROOT)   # back under the size limit, oldest files first
        shutil.rmtree(THUMB_DIR, ignore_errors=True)   # last session's preview stills
        try:
            # Importing the engine's heavy half pulls in MoviePy, NumPy and PIL.
            import vidgen.audio  # noqa: F401
            import vidgen.motion  # noqa: F401
            self.ui.log("📦 MoviePy loaded.")
        except Exception as exc:  # noqa: BLE001
            self.ui.log(f"⚠️ MoviePy not available: {exc}")

    def call_soon(self, fn, delay_ms: int = 0):
        """Run ``fn`` on the window's thread: the only safe way for a worker thread to touch it.

        Tkinter calls (after, configure...) from another thread can be lost without a word,
        which left "Show it on my PC" stuck at Finish (2026-10-21).
        """
        self._queue.put(("call", {"fn": (lambda: self.after(delay_ms, fn)) if delay_ms else fn}))

    def _drain_queue(self):
        """The single point where background work touches the interface."""
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    self.append_log(payload["msg"])
                elif kind == "status":
                    self.status_label.configure(text=payload["msg"])
                elif kind == "progress":
                    for bar in self._progress_bars():
                        bar.configure(mode="determinate")
                        bar.set(payload["value"])
                elif kind == "spinner":
                    for bar in self._progress_bars():
                        if payload["on"]:
                            bar.configure(mode="indeterminate")
                            bar.start()
                        else:
                            bar.stop()
                            bar.configure(mode="determinate")
                elif kind == "busy":
                    self._set_busy(payload["on"])
                elif kind == "preview":
                    self._preview_done(payload["path"], payload["error"])
                elif kind == "cancelled":
                    if self._batch:
                        self.append_log("📚 The rest of the videos were not made.")
                        self._batch = None
                    if self._both:
                        self._shape_back(self._both)
                        self._both = None
                    self.refresh_home()
                elif kind == "board":
                    self._board_event(payload)
                elif kind == "line":
                    self._line_ready(payload)
                elif kind == "draft":
                    try:
                        payload["dialog"].on_event(payload)
                    except tk.TclError:
                        pass   # the window was closed meanwhile
                elif kind == "writer":
                    self._writer_event(payload)
                elif kind == "updater":
                    self._updater_event(payload)
                elif kind == "voice":
                    self._voice_event(payload)
                elif kind == "images":
                    self._images_event(payload)
                elif kind == "project":
                    self._project_thumb_done(payload)
                elif kind == "batch":
                    self._batch_event(payload)
                elif kind == "moving":
                    self._moving_row_changed(payload)
                elif kind == "myvoice":
                    payload["dialog"].on_event(payload)
                elif kind == "translated":
                    payload["dialog"].done(payload)
                elif kind == "upload":
                    self._upload_done(payload)
                elif kind == "ai_speed":
                    self.note_picture_time(payload["seconds"], payload["speed"])
                elif kind == "ai_gpu":
                    self._graphics_found(payload["found"])
                elif kind == "ai_backend":
                    self.remember_ai_backend(payload["backend"])
                elif kind == "crash":
                    self._unexpected(payload["trace"])
                elif kind == "call":
                    try:
                        payload["fn"]()
                    except tk.TclError:
                        pass   # its window was closed meanwhile
                elif kind == "update":
                    self._update_checked(payload["found"], payload["manual"])
                elif kind == "keytest":
                    callback = self._key_tests.pop(payload["token"], None)
                    if callback:
                        try:
                            callback(payload["ok"], payload["message"])
                        except tk.TclError:
                            pass   # its window was closed meanwhile
                elif kind == "finished":
                    made = [p for p in (payload.get("paths") or [payload.get("path")])
                            if p and os.path.isfile(p)]
                    info = self._render_info or {}
                    if payload["ok"] and made:
                        for step in ("ai", "tip"):
                            if info.get(step):
                                self.mark_started(step)
                    if self._batch:
                        b = self._batch
                        self.save_project(videos=made)
                        if payload["ok"] and made:
                            b["made"].extend(made)
                            if b.get("upload"):
                                self._upload_done({"upload": b.pop("upload"), "videos": made,
                                                   "project": self.project})
                        else:
                            b["failed"].append((b["at"] + 1, payload["message"]))
                        b["at"] += 1
                        self.after(500, self._batch_next)
                        continue
                    both, self._both = self._both, None
                    if both and payload["ok"] and made and not both.get("first"):
                        # The first shape is done: the other one next, in the same project.
                        self.save_project(videos=made)
                        self._both = dict(both, first=made)
                        self.after(300, self._second_shape)
                        continue
                    if both:
                        made = both.get("first", []) + made
                        self._shape_back(both)
                    self.save_project(videos=made)
                    self.last_upload = None
                    if payload["ok"] and made:
                        self.make_upload_text(made)
                        ResultDialog(self, payload["title"], payload["message"], made,
                                     names=both and [both["shape"], both["other"]])
                    else:
                        message = payload["message"]
                        Dialog(self, payload["title"], message, ok=payload["ok"],
                               on_report=None if payload["ok"] else
                               (lambda: self.open_report(message)))
        except queue.Empty:
            pass
        finally:
            self.after(60, self._drain_queue)

    def _set_busy(self, busy: bool):
        self.is_rendering = busy
        # While rendering, the Render button is the way to stop.
        if busy:
            self.render_button.configure(state="normal", text=f"{self.icon('stop')}   Cancel",
                                         command=self.cancel_render)
            self.log_cancel.configure(state="normal", text=f"{self.icon('stop')}  Cancel")
            self.log_cancel.pack(side="right", padx=(0, 8))
        else:
            self.render_button.configure(state="normal", command=self.start_render,
                                         text=f"{self.icon('play')}   Make video")
            self.log_cancel.pack_forget()
        self.scenes_button.configure(state="disabled" if busy else "normal")
        self.draft_button.configure(state="disabled" if busy else "normal")
        for bar in self._progress_bars():
            if busy:
                bar.grid()
            else:
                bar.grid_remove()
        if not busy:
            self.status_label.configure(text="Ready")
        self.refresh_home()

    def _progress_bars(self):
        return (self.progress, self.log_progress)

    def cancel_render(self):
        """Ask the render to stop. It does so at its next safe point."""
        if not self.is_rendering or self._cancel is None or self._cancel.is_set():
            return
        self._cancel.set()
        self.render_button.configure(state="disabled", text="Cancelling...")
        self.log_cancel.configure(state="disabled", text="Cancelling...")
        self.status_label.configure(text="Cancelling...")

    # -- scene preview -------------------------------------------------------
    def frame_label(self) -> str:
        return f"{self.var_aspect.get()} {self.var_resolution.get()}"

    def new_footage_search(self):
        """A stock search for the current keys and frame, quiet (no Log lines)."""
        width, height, orientation, _bitrate = resolve_target(
            self.var_aspect.get(), self.var_resolution.get())
        return footage.FootageSearch(
            self.var_api.get(), self.var_pixabay.get(), width, height, orientation,
            self.var_resolution.get(), SEARCH_CACHE_DIR, lambda _message: None,
            order=self.var_footage_order.get())

    def open_scene_preview(self):
        if self.is_rendering:
            return
        script = self.script_box.get("1.0", "end")
        if looks_like_plain_text(script):   # as Make video does: it wants New from text
            DraftDialog(self).prefill(script.strip())
            return
        self.select_page("board")

    # -- new from text -------------------------------------------------------
    def open_draft(self):
        if not self.is_rendering:
            DraftDialog(self)

    def script_is_the_users_own(self) -> bool:
        """Whether replacing the editor's script would lose something they wrote."""
        current = self.script_box.get("1.0", "end").strip()
        return current not in ("", DEFAULT_SCRIPT.strip(), self._last_draft)

    def use_draft(self, result, audience, mode=None):
        """Put a written script in the editor, with the settings it was written for."""
        script = result.script
        self.save_project()
        self.project = None   # a new video: its own project from the first save
        self.thumb = {"scene": None, "title": ""}
        self.board_history, self.board_future = [], []
        if self.ai_slow() and self._has_footage_key():
            # Each picture takes minutes here: keep the first few, stock for the rest.
            capped = boards.Board(boards.limit_ai(parse_script(script), SLOW_AI_SCENES)).text()
            if capped != script:
                self.append_log(f"🎨 This PC makes AI pictures slowly, so only the first "
                                f"{SLOW_AI_SCENES} scenes get one. The Storyboard can switch "
                                "any other scene in one click.")
            script = capped
        self._last_draft = script.strip()
        self.writer_mode = mode or self.writer_mode
        self.audience = {"audience_content": audience.content, "audience_age": audience.age,
                         "audience_platform": audience.platform}
        self.script_box.delete("1.0", "end")
        self.script_box.insert("1.0", script)
        self.var_voice.set(voices.persona(result.voice).label)
        self.var_aspect.set(result.aspect)
        self.var_target.set(result.target)
        self._commit_script()
        # Look before it is made, unless the user asked for the video straight away.
        after, self._after_draft = self._after_draft, "board"
        self.select_page("board")
        if after == "render":
            self.start_render()

    # -- the Smart writer's one-time download --------------------------------
    def start_writer_download(self):
        """Fetch the model on a worker thread. Listeners hear how it goes."""
        if self.writer_downloading:
            return
        self.writer_downloading = True
        self._writer_stop = stop = threading.Event()
        self.append_log(f"⬇️ Downloading the Smart writer ({writer_size()})...")

        def worker():
            last = 0.0

            def progress(done, total):
                nonlocal last
                now = time.monotonic()
                if now - last >= 0.25 or done >= total:  # a few times a second is plenty
                    last = now
                    self._queue.put(("writer", {"event": "progress", "done": done, "total": total}))

            try:
                writer.download_all(progress, stop.is_set)
                self._queue.put(("writer", {"event": "done"}))
            except writer.Stopped:
                self._queue.put(("writer", {"event": "stopped"}))
            except writer.WriterError as exc:
                self._queue.put(("writer", {"event": "failed", "message": str(exc)}))
            except Exception as exc:  # noqa: BLE001 - a thread must not die silently
                self._queue.put(("writer", {
                    "event": "failed", "trace": traceback.format_exc(),
                    "message": f"The download hit an unexpected problem ({type(exc).__name__})."}))

        threading.Thread(target=worker, daemon=True).start()
        # Tell the listeners at once, so their buttons read Cancel before the first byte.
        self._writer_event({"event": "progress", "done": 0,
                            "total": sum(m.size for m in writer.missing()) or 1})

    def cancel_writer_download(self):
        self._writer_stop.set()

    def repair_writer(self):
        """Throw the model away and fetch it again."""
        if not self.writer_downloading:
            for model in writer.FILES:
                writer.remove(model)
            self.start_writer_download()

    def _writer_event(self, payload):
        """UI thread: pass the download's news to whoever is showing it."""
        event = payload["event"]
        if event != "progress":
            self.writer_downloading = False
            if payload.get("trace"):
                self.session_log.write(payload["trace"])
            self.append_log({"done": "✅ The Smart writer is ready.",
                             "stopped": "⏸️ Smart writer download paused.",
                             "failed": f"⚠️ {payload.get('message', '')}"}[event])
        for listener in list(self.writer_listeners):
            try:
                listener(payload)
            except tk.TclError:
                self.writer_listeners.remove(listener)   # its window is gone

    def _check_clips_row_shown(self, _payload=None):
        """Check stock clips needs both the Smart writer and its photo part."""
        if writer.installed() and writer.sees():
            self.check_clips_row.grid()
        else:
            self.check_clips_row.grid_remove()

    def _writer_row_changed(self, payload=None):
        """The Settings row: what state the Smart writer is in, and what the button does."""
        self._writer_confirm = False
        model = writer.MODEL
        if payload and payload["event"] == "progress":
            done, total = payload["done"], payload["total"]
            self.writer_state.configure(
                text=f"Downloading {done / 1e9:.2f} of {total / 1e9:.2f} GB", text_color=TEXT_MUTED)
            self.writer_button.configure(text="Cancel", command=self.cancel_writer_download)
            return
        if payload and payload["event"] == "failed":
            self.writer_state.configure(text=ellipsize(payload["message"], 60), text_color=ERR_COLOR)
        elif writer.runtime_path() is None:
            self.writer_state.configure(text="Not included in this copy", text_color=TEXT_MUTED)
        elif writer.installed() and not writer.sees():
            self.writer_state.configure(text=f"Photo part not downloaded · {writer_size()}",
                                        text_color=TEXT_MUTED)
            self.writer_button.configure(text="Download", command=self.start_writer_download)
            return
        elif writer.installed():
            self.writer_state.configure(
                text=f"Installed · {sum(m.size for m in writer.FILES) / 1e9:.1f} GB",
                text_color=OK_COLOR)
        else:
            self.writer_state.configure(text=f"Not downloaded · {writer_size()}",
                                        text_color=TEXT_MUTED)
        if writer.installed():
            self.writer_button.configure(text="Repair", command=self._confirm_repair)
        else:
            self.writer_button.configure(text="Download", command=self.start_writer_download)

    def _confirm_repair(self):
        """Repair downloads all 3 GB again, so it takes a second press."""
        if not self._writer_confirm:
            self._writer_confirm = True
            self.writer_state.configure(
                text=f"Press again to download "
                     f"{sum(m.size for m in writer.FILES) / 1e9:.1f} GB again",
                text_color=ERR_COLOR)
            return
        self.repair_writer()

    # ------------------------------------------------------------ PROJECTS --
    # Every video made (and every Storyboard left unfinished), from vidgen.projects.
    def _build_projects(self):
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=1)
        self._caption(page, "My videos", 0, pady=(0, 6))
        top = ctk.CTkFrame(page, fg_color="transparent")
        top.grid(row=1, column=0, sticky="ew", pady=(0, PAD // 2))
        top.grid_columnconfigure(0, weight=1)
        self.project_search = focus_accent(ctk.CTkEntry(
            top, height=34, corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
            border_color=FIELD_BORDER, text_color=TEXT,
            placeholder_text="Search titles and scripts"))
        self.project_search.grid(row=0, column=0, sticky="ew")
        self.project_search.bind("<KeyRelease>", lambda _event: self.refresh_projects())
        self.projects_count = ctk.CTkLabel(top, text="", font=self.font_tiny,
                                           text_color=TEXT_DIM)
        self.projects_count.grid(row=0, column=1, padx=(12, 4))
        self.projects_list = ctk.CTkScrollableFrame(
            page, fg_color="transparent", scrollbar_fg_color=SCROLLBAR_BG,
            scrollbar_button_color=SCROLLBAR_THUMB,
            scrollbar_button_hover_color=SCROLLBAR_THUMB_HOVER)
        self.projects_list.grid(row=2, column=0, sticky="nsew")
        self.project_images = []   # kept so Tk doesn't drop the thumbnails
        # Wide windows: the selected video's details and actions, beside the grid.
        self.project_side = Card(page, width=SIDE)
        self.project_side.grid_columnconfigure((0, 1), weight=1)
        self.project_selected = None
        return page

    # -------------------------------------------------------------- THUMBNAILS --
    def _build_photo_editor(self):
        """Change a photo with words (ImageEditor), on a page of its own."""
        page = self._scroll_page()
        page.grid_columnconfigure(0, weight=1)
        self._caption(page, "Image editor", 0, pady=(0, 6))
        self.photo_editor = ImageEditor(page, self)
        self.photo_editor.grid(row=1, column=0, sticky="ew")
        return page

    def _build_thumbs(self):
        """Every video's thumbnail to edit, and free thumbnails that belong to no video."""
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=1)
        self._caption(page, "Thumbnails", 0, pady=(0, 6))
        top = ctk.CTkFrame(page, fg_color="transparent")
        top.grid(row=1, column=0, sticky="ew", pady=(0, PAD // 2))
        top.grid_columnconfigure(0, weight=1)
        self.thumbs_search = focus_accent(ctk.CTkEntry(
            top, height=34, corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
            border_color=FIELD_BORDER, text_color=TEXT, placeholder_text="Search titles"))
        self.thumbs_search.grid(row=0, column=0, sticky="ew")
        self.thumbs_search.bind("<KeyRelease>", lambda _event: self.refresh_thumbs())
        for column, shape in ((1, "16:9"), (2, "9:16")):
            ctk.CTkButton(top, text=f"+ New {shape} thumbnail", width=170, height=34,
                          corner_radius=4, font=self.font_body,
                          fg_color=ACCENT if shape == "16:9" else FIELD_BG,
                          hover_color=ACCENT_HOVER if shape == "16:9" else CARD_HOVER,
                          border_width=0 if shape == "16:9" else 1, border_color=FIELD_BORDER,
                          text_color=ACCENT_TEXT if shape == "16:9" else TEXT,
                          command=lambda a=shape: self.new_free_thumb(a)).grid(
                row=0, column=column, padx=(12, 0))
        self.thumbs_list = ctk.CTkScrollableFrame(
            page, fg_color="transparent", scrollbar_fg_color=SCROLLBAR_BG,
            scrollbar_button_color=SCROLLBAR_THUMB,
            scrollbar_button_hover_color=SCROLLBAR_THUMB_HOVER)
        self.thumbs_list.grid(row=2, column=0, sticky="nsew")
        self.thumb_images = []   # kept so Tk doesn't drop the pictures
        self.thumbs_page, self.thumbs_top, self.thumbs_editor = page, top, None
        return page

    def _open_thumbs_editor(self, folder, size, layers, title, on_save=None, on_reset=None):
        """The editor in the Thumbnails page, where the list was; Back brings the list back."""
        self._close_thumbs_editor()
        self.thumbs_top.grid_remove()
        self.thumbs_list.grid_remove()

        def saved():
            if on_save:
                on_save()

        def reset():
            on_reset()
            self._close_thumbs_editor()

        editor = ThumbEditor(self.thumbs_page, self, folder, size, layers, on_save=saved,
                             on_reset=reset if on_reset else None,
                             on_close=self._close_thumbs_editor, title=title)
        editor.grid(row=1, column=0, rowspan=2, sticky="nsew")
        self.thumbs_editor = editor
        return editor

    def _close_thumbs_editor(self):
        if self.thumbs_editor is not None and self.thumbs_editor.winfo_exists():
            self.thumbs_editor.destroy()
        self.thumbs_editor = None
        self.thumbs_top.grid()
        self.thumbs_list.grid()
        self.refresh_thumbs()

    @staticmethod
    def _video_thumb(project):
        """(picture, edited) of a video's thumbnail now: the editor's, else the made video's,
        else its first picture; picture is None when there is none yet."""
        from vidgen import thumbedit

        folder = projects.folder(project["id"])
        portrait = (project.get("settings") or {}).get("aspect", "9:16") == "9:16"
        edited = thumbedit.edited(folder, portrait)
        if edited:
            return edited, True
        for video in project.get("videos") or []:
            made = os.path.splitext(video)[0] + ".thumbnail.jpg"
            if os.path.isfile(made):
                return made, False
        first = os.path.join(folder, projects.THUMB)
        return (first if os.path.isfile(first) else None), False

    @staticmethod
    def free_thumbs():
        """(folder, layers, size) of every free thumbnail, the newest first."""
        from vidgen import thumbedit

        try:
            names = os.listdir(FREE_THUMBS_DIR)
        except OSError:
            return []
        found = []
        for name in names:
            folder = os.path.join(FREE_THUMBS_DIR, name)
            loaded = thumbedit.load(folder)
            if loaded:
                found.append((os.path.getmtime(os.path.join(folder, thumbedit.EDIT_FILE)),
                              folder, *loaded))
        return [item[1:] for item in sorted(found, reverse=True)]

    def refresh_thumbs(self):
        from vidgen import thumbedit

        for child in self.thumbs_list.winfo_children():
            child.destroy()
        self.thumb_images = []
        columns = (5 if self.winfo_width() / ctk.ScalingTracker.get_window_scaling(
            self) >= 1800 else 4) if self.wide else 3
        for column in range(5):
            self.thumbs_list.grid_columnconfigure(
                column, weight=1 if column < columns else 0,
                uniform="thumbs" if column < columns else "")
        query = self.thumbs_search.get()
        row = [0]

        def heading(text):
            ctk.CTkLabel(self.thumbs_list, text=text, font=self.font_bold, text_color=TEXT,
                         anchor="w").grid(row=row[0], column=0, columnspan=columns,
                                          sticky="w", padx=6, pady=(10, 4))
            row[0] += 1

        def note(text):
            ctk.CTkLabel(self.thumbs_list, text=text, font=self.font_body,
                         text_color=TEXT_MUTED, anchor="w", justify="left",
                         wraplength=640).grid(row=row[0], column=0, columnspan=columns,
                                              sticky="w", padx=6, pady=(0, 8))
            row[0] += 1

        def cards(items):
            for at, item in enumerate(items):
                self._thumb_card(row[0] + at // columns, at % columns, *item)
            row[0] += (len(items) + columns - 1) // columns

        heading("Your videos")
        every = projects.all_projects()
        shown = [p for p in every if projects.matches(p, query)]
        items = []
        for project in shown:
            picture, edited = self._video_thumb(project)
            portrait = (project.get("settings") or {}).get("aspect", "9:16") == "9:16"
            items.append((picture, project.get("title") or "Untitled video",
                          "Edited" if edited else "Automatic", portrait,
                          [("Edit", lambda p=project: self.edit_video_thumb(p)),
                           ("Start over", lambda p=project: self.edit_video_thumb(p, True)),
                           ("Save as picture", lambda p=project: self.save_thumb_as(
                               self._video_thumb(p)[0], p.get("title")))]))
        if items:
            cards(items)
        else:
            note("Nothing matches that search." if every else
                 "Make a video first: its thumbnail appears here to change. Or start a free "
                 "thumbnail with the buttons above.")
        heading("Free thumbnails")
        items = []
        for folder, layers, size in self.free_thumbs():
            words = next((thumbedit.label(layer) for layer in layers
                          if layer["kind"] == "text"), "Free thumbnail")
            if query.strip() and query.strip().lower() not in words.lower():
                continue
            picture = os.path.join(folder, thumbedit.PICTURE_FILE)
            items.append((picture, words, f"{size[0]}x{size[1]}", size[1] > size[0],
                          [("Edit", lambda f=folder: self.edit_free_thumb(f)),
                           ("Save as picture", lambda pic=picture, w=words:
                            self.save_thumb_as(pic, w)),
                           ("Delete", lambda f=folder: self.delete_free_thumb(f))]))
        if items:
            cards(items)
        else:
            note("Thumbnails for anything, not tied to a video: press + New thumbnail.")

    def _thumb_card(self, row, column, picture, title, tag, portrait, buttons):
        from PIL import Image

        card = Card(self.thumbs_list)
        card.grid(row=row, column=column, sticky="nsew", padx=4, pady=4)
        card.grid_columnconfigure(0, weight=1)
        box = (101, 180) if portrait else (240, 135)
        shown = ctk.CTkLabel(card, text=self.icon("images"), font=self.font_icon_lg,
                             text_color=TEXT_DIM, width=box[0], height=box[1],
                             fg_color=FIELD_BG, corner_radius=4)
        shown.grid(row=0, column=0, pady=(12, 6))
        if picture:
            try:
                with Image.open(picture) as opened:
                    image = opened.convert("RGB")
                image.thumbnail(box)
                photo = ctk.CTkImage(light_image=image, dark_image=image, size=image.size)
                self.thumb_images.append(photo)
                shown.configure(image=photo, text="")
            except OSError:
                pass
        ctk.CTkLabel(card, text=title, font=self.font_bold, text_color=TEXT, anchor="w",
                     justify="left", wraplength=240).grid(row=1, column=0, sticky="ew",
                                                          padx=12)
        ctk.CTkLabel(card, text=tag, font=self.font_tiny,
                     text_color=ACCENT if tag == "Edited" else TEXT_DIM, anchor="w").grid(
            row=2, column=0, sticky="ew", padx=12)
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=3, column=0, sticky="ew", padx=8, pady=(6, 12))
        for at, (text, command) in enumerate(buttons):
            ctk.CTkButton(actions, text=text, height=28, corner_radius=4, font=self.font_tiny,
                          fg_color=ACCENT if at == 0 else FIELD_BG,
                          hover_color=ACCENT_HOVER if at == 0 else CARD_HOVER,
                          border_width=0 if at == 0 else 1, border_color=FIELD_BORDER,
                          text_color=ACCENT_TEXT if at == 0 else TEXT, width=60,
                          command=command).pack(side="left", padx=(0, 4) if at < 2 else 0)

    def edit_video_thumb(self, project, start_over=False):
        """The editor for a video from the Thumbnails page; its frames load meanwhile."""
        from vidgen import thumbedit, thumbnail

        pid = project["id"]
        folder = projects.folder(pid)
        portrait = (project.get("settings") or {}).get("aspect", "9:16") == "9:16"
        size = thumbnail.PORTRAIT if portrait else thumbnail.LANDSCAPE
        videos = list(project.get("videos") or [])
        saved = thumbedit.load(folder)
        if start_over:
            layers = thumbedit.blank(size)
        elif saved and thumbedit.edited(folder, portrait):
            layers = saved[0]
        else:
            first = os.path.join(folder, projects.THUMB)
            layers = thumbedit.automatic(first if os.path.isfile(first) else "",
                                         project.get("title") or "", size)

        def saved_now():
            picture = os.path.join(folder, thumbedit.PICTURE_FILE)
            thumbedit.replace_video_thumbnails(picture, videos, portrait)
            if self.project == pid:
                self._refresh_thumb()

        def reset():
            thumbedit.remove(folder)
            if self.project == pid:
                self._refresh_thumb()

        editor = self._open_thumbs_editor(folder, size, layers, project.get("title") or
                                          "Untitled video", on_save=saved_now, on_reset=reset)
        editor.strip_note.configure(text="Getting the video's pictures...")

        def work():
            found = thumbedit.video_backgrounds(folder, videos)

            def show():
                if editor.winfo_exists():
                    editor.set_backgrounds(found)
                    editor.strip_note.configure(
                        text="Background: a picture from the video, or a file from your PC")
                    if not editor.layers[0].get("path") and found:   # Start over: a frame
                        editor.set_background(found[0][1])
            self.call_soon(show)

        threading.Thread(target=work, daemon=True).start()

    def new_free_thumb(self, shape):
        from vidgen import thumbedit, thumbnail

        size = thumbnail.PORTRAIT if shape == "9:16" else thumbnail.LANDSCAPE
        folder = os.path.join(FREE_THUMBS_DIR, projects.new_id())
        editor = self._open_thumbs_editor(folder, size, thumbedit.blank(size),
                                          f"New {shape} thumbnail")
        editor.strip_note.configure(text="Background: a picture from your PC, or search "
                                         "Photos on the left")

    def edit_free_thumb(self, folder):
        from vidgen import thumbedit

        loaded = thumbedit.load(folder)
        if loaded:
            self._open_thumbs_editor(folder, loaded[1], loaded[0], "Free thumbnail")

    def delete_free_thumb(self, folder):
        def delete():
            shutil.rmtree(folder, ignore_errors=True)
            self.refresh_thumbs()

        Dialog(self, "Delete this thumbnail?", "It is removed from the Thumbnails page. "
                                               "Pictures you saved from it stay.",
               ok=False, confirm=delete, confirm_text="Delete")

    def save_thumb_as(self, picture, name):
        """A copy of a thumbnail wherever the user wants it (to upload it)."""
        from vidgen import thumbedit

        if not picture or not os.path.isfile(picture):
            Dialog(self, "No thumbnail yet", "This video has no picture yet: make the video "
                                             "or edit its thumbnail first.", ok=False)
            return
        safe = re.sub(r'[\\/:*?"<>|]+', " ", name or "thumbnail").strip()[:60] or "thumbnail"
        dest = filedialog.asksaveasfilename(
            parent=self, title="Save the thumbnail", defaultextension=".png",
            initialdir=paths.known_folder(paths.CSIDL_MYPICTURES, "Pictures"),
            initialfile=f"{safe} thumbnail.png",
            filetypes=[("PNG picture", "*.png"), ("JPEG picture", "*.jpg")])
        if not dest:
            return
        try:
            thumbedit.export(picture, dest)
        except OSError as exc:
            Dialog(self, "Couldn't save the picture", str(exc), ok=False)

    def refresh_projects(self):
        for child in self.projects_list.winfo_children():
            child.destroy()
        self.project_images = []
        every = projects.all_projects()
        shown = [p for p in every if projects.matches(p, self.project_search.get())]
        count = f"{len(every)} video{'s' if len(every) != 1 else ''}"
        self.projects_count.configure(
            text=count if len(shown) == len(every) else f"{len(shown)} of {count}")
        self.project_columns = (5 if self.winfo_width() / ctk.ScalingTracker.get_window_scaling(
            self) >= 1800 else 4) if self.wide else 3
        for column in range(5):
            self.projects_list.grid_columnconfigure(
                column, weight=1 if column < self.project_columns else 0,
                uniform="projects" if column < self.project_columns else "")
        if self.wide and shown:
            ids = [p["id"] for p in shown]
            self._fill_project_side(shown[ids.index(self.project_selected)]
                                    if self.project_selected in ids else shown[0])
            self.project_side.grid(row=2, column=1, sticky="nsew", padx=(PAD, 0))
        else:
            self.project_side.grid_remove()
        if not shown:
            hint = ("Nothing matches that search." if every else
                    "Videos you make appear here, so you can open them again in the Storyboard "
                    "and change them. A Storyboard you leave without making the video is kept "
                    "here too, as a draft.")
            ctk.CTkLabel(self.projects_list, text=hint, font=self.font_body,
                         text_color=TEXT_MUTED, justify="left", anchor="w", wraplength=640,
                         ).grid(row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(20, 8))
            if not every:
                ctk.CTkButton(self.projects_list, text="Make your first video", width=170,
                              height=32, corner_radius=4, font=self.font_body, fg_color=ACCENT,
                              hover_color=ACCENT_HOVER, text_color=ACCENT_TEXT,
                              command=lambda: self.select_page("new")).grid(
                    row=1, column=0, sticky="w", padx=8)
        for index, project in enumerate(shown):
            self._project_card(project, index)

    @staticmethod
    def _project_thumb_image(pid, box):
        """A project's thumbnail fitted in ``box``, or None (no picture yet: the icon stays)."""
        from PIL import Image

        thumb = os.path.join(projects.folder(pid), projects.THUMB)
        if not os.path.isfile(thumb):
            return None
        try:
            with Image.open(thumb) as image:
                image = image.convert("RGB")
        except OSError:
            return None
        image.thumbnail(box)
        return ctk.CTkImage(light_image=image, dark_image=image, size=image.size)

    def _project_card(self, project, index):
        pid = project["id"]
        card = Card(self.projects_list)
        card.grid(row=index // self.project_columns, column=index % self.project_columns,
                  sticky="nsew", padx=4, pady=4)
        card.grid_columnconfigure((0, 1), weight=1)
        if not self.wide:
            self._project_body(card, project)
            return
        # Wide: the side panel has the rest; a click picks the video for it.
        card.configure(border_width=2 if pid == self.project_selected else 0,
                       border_color=ACCENT)
        for widget in (card, *self._project_body(card, project, keep=("Open", "Play"))):
            widget.bind("<Button-1>", lambda _e, p=project: self._select_project(p))

    def _select_project(self, project):
        self.project_selected = project["id"]
        self.refresh_projects()

    def _fill_project_side(self, project):
        for child in self.project_side.winfo_children():
            child.destroy()
        self.project_selected = project["id"]
        self._project_body(self.project_side, project, big=True)

    def _project_body(self, holder, project, big=False, keep=None):
        """A video's picture, title, details and actions, in a card or the side panel."""
        pid, settings = project["id"], project.get("settings") or {}
        videos = [v for v in project.get("videos") or [] if os.path.isfile(v)]
        box = ((101, 180) if settings.get("aspect", "9:16") == "9:16" else (240, 135)) \
            if not big else ((180, 320) if settings.get("aspect", "9:16") == "9:16"
                             else (SIDE - 40, 191))
        picture = ctk.CTkLabel(holder, text=self.icon("projects"), font=self.font_icon_lg,
                               text_color=TEXT_DIM, width=box[0], height=box[1],
                               fg_color=FIELD_BG, corner_radius=4)
        picture.grid(row=0, column=0, columnspan=2, pady=(12, 6))
        photo = self._project_thumb_image(pid, box)
        if photo is not None:
            self.project_images.append(photo)
            picture.configure(image=photo, text="")

        title = ctk.CTkLabel(holder, text=project.get("title") or "Untitled video",
                             font=self.font_bold, text_color=TEXT, anchor="w", justify="left",
                             wraplength=SIDE - 40 if big else 240)
        title.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12)
        when = time.strftime("%d %b %Y", time.localtime(project.get("updated") or 0))
        if projects.is_draft(project):
            info, color = f"Draft · {when}", WARN_COLOR
        elif not videos:
            info, color = f"{when} · video file not found", WARN_COLOR
        else:
            length = project.get("length") or 0
            info = f"{when} · {int(length // 60)}:{int(length % 60):02d} · " \
                   f"{settings.get('aspect', '9:16')}"
            color = TEXT_DIM
        ctk.CTkLabel(holder, text=info, font=self.font_tiny, text_color=color, anchor="w",
                     ).grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 6))

        def rename():
            title.grid_remove()
            entry = focus_accent(ctk.CTkEntry(
                holder, height=30, corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
                border_color=FIELD_BORDER, text_color=TEXT))
            entry.insert(0, project.get("title") or "")
            entry.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12)
            entry.focus_set()
            entry.select_range(0, "end")

            def done(_event=None):
                name, data = entry.get().strip(), projects.load(pid)
                if name and data is not None:
                    projects.save(pid, dict(data, title=name), touch=False)
                self.refresh_projects()

            entry.bind("<Return>", done)
            entry.bind("<Escape>", lambda _event: self.refresh_projects())

        def delete():
            def really():
                projects.delete(pid)
                if self.project == pid:
                    self.project = None
                self.refresh_projects()

            Dialog(self, "Delete this project?",
                   f"\"{project.get('title')}\" is removed from My videos, with its saved "
                   "AI pictures. Your video file is kept.", ok=False,
                   confirm=really, confirm_text="Delete")

        def change_it():
            if self.open_project(pid):
                self.board_comment.focus_set()
                self.board_status.configure(
                    text="Say what to change in the box below, then press Change it.",
                    text_color=TEXT_DIM)

        buttons = (("Open", lambda: self.open_project(pid), True),
                   ("Change it", change_it, True),
                   ("Play", lambda: os.startfile(videos[0]), bool(videos)),
                   ("Show in folder", lambda: subprocess.Popen(
                       ["explorer", "/select,", os.path.normpath(videos[0])]), bool(videos)),
                   ("Make a copy", lambda: CopyDialog(self, project), True),
                   ("Translate", lambda: TranslateDialog(self, project), True),
                   ("Rename", rename, True),
                   ("Delete", delete, True))
        if keep:
            buttons = tuple(b for b in buttons if b[0] in keep)
        for at, (text, command, usable) in enumerate(buttons):
            ctk.CTkButton(
                holder, text=text, height=28, corner_radius=4, font=self.font_tiny,
                fg_color=ACCENT if at == 0 else FIELD_BG,
                hover_color=ACCENT_HOVER if at == 0 else CARD_HOVER,
                text_color=ACCENT_TEXT if at == 0 else TEXT, border_width=0 if at == 0 else 1,
                border_color=FIELD_BORDER, state="normal" if usable else "disabled",
                command=command,
            ).grid(row=3 + at // 2, column=at % 2, sticky="ew",
                   padx=(12 if at % 2 == 0 else 3, 3 if at % 2 == 0 else 12), pady=3)
        ctk.CTkFrame(holder, fg_color="transparent", height=8).grid(row=8, column=0)
        return picture, title

    def save_project(self, videos=None):
        """Keep the script, its settings and AI pictures as a project.

        ``videos``: the render just finished (an empty list when it failed). Without
        it this saves a draft, and only when the script changed since the last save.
        """
        if not hasattr(self, "script_box"):
            return
        script = self.script_box.get("1.0", "end").strip()
        try:
            scenes_now = parse_script(script)
        except ScriptError:
            return
        old = projects.load(self.project) if self.project else None
        if videos is None and (script == DEFAULT_SCRIPT.strip()
                               or (old is not None and old["script"] == script)):
            return
        self.project = self.project if old is not None or videos is not None \
            else projects.new_id()
        folder = projects.folder(self.project)
        kept = projects.keep_photos(folder, script)
        if kept != script:   # the user's photos now live in the project
            script = kept
            self.script_box.delete("1.0", "end")
            self.script_box.insert("1.0", script)
        first = None
        for scene in scenes_now:
            if not scene.is_ai:
                continue
            key = self._picture_key(scene)
            keep, made = projects.picture_file(folder, key), self.board_pictures.get(key)
            try:
                if made and os.path.isfile(made) and not os.path.isfile(keep):
                    os.makedirs(os.path.dirname(keep), exist_ok=True)
                    shutil.copyfile(made, keep)
            except OSError:
                pass   # the picture is made again when needed
            if first is None and os.path.isfile(keep):
                first = keep
        s = self._collect_settings()
        data = dict(old or {}, script=script, settings={k: s[k] for k in projects.KEYS},
                    clip_pos=dict(self.board_pos), thumbnail=dict(self.thumb))
        if videos:
            data["videos"] = list(videos)
            if self._render_info:   # how long this PC took, for Home's "Usually takes"
                data["took"] = round(time.time() - self._render_info["at"])
        projects.save(self.project, data)
        save_settings(s)
        if videos or first:
            threading.Thread(target=self._project_thumb,
                             args=(self.project, (videos or [None])[0], first),
                             daemon=True).start()

    def _project_thumb(self, pid, video, picture):
        """Worker: the card's picture (a frame of the video, else the first AI picture)."""
        from PIL import Image

        dest, length = os.path.join(projects.folder(pid), projects.THUMB), None
        try:
            made = os.path.splitext(video or "")[0] + ".thumbnail.jpg"
            if video:
                width, height, length = preview.probe(video)
                if os.path.isfile(made):   # the thumbnail the render made, with its title
                    with Image.open(made) as image:
                        image = image.convert("RGB")
                    image.thumbnail((360, 360))
                else:
                    image = preview.poster(video, preview.fit((width, height), (360, 360)),
                                           at=min(1.0, length / 2))
            else:
                with Image.open(picture) as image:
                    image = image.convert("RGB")
                image.thumbnail((360, 360))
            image.save(dest)
        except Exception:  # noqa: BLE001 - no thumbnail: the card shows an icon
            return
        self._queue.put(("project", {"id": pid, "length": length}))

    def _project_thumb_done(self, payload):
        data = projects.load(payload["id"])
        if data is not None and payload["length"] is not None:
            projects.save(payload["id"], dict(data, length=payload["length"]), touch=False)
        if self.current_page == "projects":
            self.refresh_projects()

    def open_project(self, pid, changes=None) -> bool:
        """Put a project back in the editor and the Storyboard. ``changes``: settings to
        use instead of the saved ones (Make a copy)."""
        if self.is_rendering:
            Dialog(self, "A video is being made",
                   "Wait until it is done, or cancel it in the Log, then open the project.",
                   ok=False)
            return False
        data = projects.load(pid)
        if data is None:
            self.refresh_projects()
            return False
        self.save_project()   # what is open now is kept first
        snap = dict(data.get("settings") or {}, **(changes or {}))
        if snap.get("aspect") in RATIO_OPTIONS:
            self.var_aspect.set(snap["aspect"])
        if snap.get("resolution") in RESOLUTION_OPTIONS:
            self.var_resolution.set(snap["resolution"])
        if snap.get("voice"):
            self.var_voice.set(voices.persona(snap["voice"]).label)
        self.var_voice_speed.set(snap.get("voice_speed") if snap.get("voice_speed")
                                 in voices.SPEEDS else voices.DEFAULT_SPEED)
        if "captions" in snap:
            self.var_captions.set(bool(snap["captions"]))
        for key, allowed, _default in CAPTION_SETTINGS:
            if snap.get(key) in allowed:
                self.caption_vars[key].set(snap[key])
        if snap.get("style") in drawn.STYLES:
            self.var_style.set(snap["style"])
        if snap.get("ai_images_look") in imagegen.LOOKS:
            self.var_ai_look.set(snap["ai_images_look"])
        if snap.get("target_length") in pacing.TARGETS:
            self.var_target.set(snap["target_length"])
        if "music_enabled" in snap:
            self.var_music_enabled.set(bool(snap["music_enabled"]))
        music = snap.get("music_path") or ""
        self.music_path = music if music == AUTO_TRACK or os.path.isfile(music) else ""

        if snap.get("aspect") != (data.get("settings") or {}).get("aspect"):
            data["clip_pos"] = {}   # clip picks belong to the other shape's search
        self.project = pid
        self.thumb = dict({"scene": None, "title": ""}, **(data.get("thumbnail") or {}))
        if changes:
            projects.save(pid, dict(data, settings=snap))
        self.script_box.delete("1.0", "end")
        self.script_box.insert("1.0", data["script"])
        # After the shape: a new shape clears the Storyboard's choices.
        self.board_clips, self.board_pos = {}, dict(data.get("clip_pos") or {})
        folder = projects.folder(pid)
        try:
            for scene in parse_script(data["script"]):
                if scene.is_ai:
                    key = self._picture_key(scene)
                    if os.path.isfile(projects.picture_file(folder, key)):
                        self.board_pictures[key] = projects.picture_file(folder, key)
        except ScriptError:
            pass
        self.board_text = None
        self.board_history, self.board_future = [], []
        self._commit_script()
        self.select_page("board")
        return True

    # ---------------------------------------------------------- STORYBOARD --
    # The script as a card per scene. vidgen.board holds the scenes and every edit
    # is written straight back to the script editor, so the two never disagree.
    def _build_board(self):
        """The Storyboard: tabs Setup (scenes, timeline, this video's choices) and Log."""
        outer = ctk.CTkFrame(self.content, fg_color="transparent")
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(outer, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(head, text="Storyboard", font=self.font_caption,
                     text_color=TEXT_MUTED).pack(side="left", padx=(2, 14))
        self.var_board_view = tk.StringVar(value=BOARD_VIEWS[0])
        self._segmented(head, list(BOARD_VIEWS), self.var_board_view, 300)
        page = ctk.CTkFrame(outer, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        self.board_page = page
        self.board_thumb_tab = ctk.CTkFrame(outer, fg_color="transparent")
        self.board_thumb_tab.grid_columnconfigure(0, weight=1)
        self.board_thumb_tab.grid_rowconfigure(0, weight=1)
        self.board_thumb_editor, self._board_thumb_key = None, None
        self.board_views = {"Setup": page, "Log": self._build_log(outer),
                            "Thumbnail": self.board_thumb_tab}
        self.var_board_view.trace_add("write", lambda *_a: self._show_board_view())
        self.board_list = ctk.CTkScrollableFrame(
            page, fg_color="transparent", scrollbar_fg_color=SCROLLBAR_BG,
            scrollbar_button_color=SCROLLBAR_THUMB,
            scrollbar_button_hover_color=SCROLLBAR_THUMB_HOVER)
        self.board_list.grid(row=1, column=0, sticky="nsew")
        self.board_list.grid_columnconfigure(0, weight=1)

        strip = ctk.CTkFrame(page, fg_color="transparent")
        strip.grid(row=2, column=0, sticky="ew", pady=(PAD // 2, 0))
        strip.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(strip, text="Timeline: drag the right edge of a scene to change how long it "
                                 "lasts, double-click it to go back to automatic, click it to "
                                 "find its card.", font=self.font_tiny, text_color=TEXT_DIM,
                     anchor="w").grid(row=0, column=0, sticky="w", padx=4)
        self.timeline = tk.Canvas(strip, height=46, highlightthickness=0, bd=0,
                                  bg=theme_color(MAIN_BG))
        self.timeline.grid(row=1, column=0, sticky="ew")
        self.timeline.bind("<Configure>", lambda _e: self._draw_timeline())
        self.timeline.bind("<ButtonPress-1>", self._timeline_press)
        self.timeline.bind("<B1-Motion>", self._timeline_drag)
        self.timeline.bind("<ButtonRelease-1>", self._timeline_release)
        self.timeline.bind("<Double-Button-1>", self._timeline_auto)
        self._timeline_hold = None

        bar = Card(page)
        bar.grid(row=3, column=0, sticky="ew", pady=(PAD // 2, 0))
        bar.grid_columnconfigure(0, weight=1)
        steps = ctk.CTkFrame(bar, fg_color="transparent")
        steps.grid(row=1, column=3, sticky="e", padx=14, pady=(0, 10))
        # Narrow window: the side panel opens over the scenes from here.
        self.options_button = ctk.CTkButton(
            steps, text="Video options", width=110, height=28, corner_radius=4,
            font=self.font_tiny, fg_color=FIELD_BG, hover_color=CARD_HOVER, border_width=1,
            border_color=FIELD_BORDER, text_color=TEXT, command=self._toggle_video_options)
        undo = [ctk.CTkButton(steps, text=text, width=64, height=28, corner_radius=4,
                              font=self.font_tiny, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                              border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                              command=lambda r=redo: self.board_undo(r))
                for text, redo in (("Undo", False), ("Redo", True))]
        for button in undo:
            button.pack(side="left", padx=(4, 0))
        self._first_step = undo[0]
        self.bind_all("<Control-z>", lambda _e: self._undo_key(False), add="+")
        self.bind_all("<Control-y>", lambda _e: self._undo_key(True), add="+")
        for key, step in (("Left", (-1, 0)), ("Right", (1, 0)), ("Up", (0, -1)),
                          ("Down", (0, 1))):
            self.bind_all(f"<{key}>", lambda e, d=step: self._editor_key(e, d, 1), add="+")
            self.bind_all(f"<Shift-{key}>", lambda e, d=step: self._editor_key(e, d, 10),
                          add="+")
        self.bind_all("<Delete>", self._editor_key, add="+")
        self.board_comment = focus_accent(ctk.CTkEntry(
            bar, height=34, corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
            border_color=FIELD_BORDER, text_color=TEXT,
            placeholder_text="Say what to change: make it shorter, more exciting start, "
                             "remove the part about..."))
        self.board_comment.grid(row=0, column=0, sticky="ew", padx=(14, 8), pady=(12, 4))
        self.board_comment.bind(
            "<Return>", lambda _event: self.revise_script(self.board_comment.get()))
        self.board_change = ctk.CTkButton(
            bar, text="Change it", width=110, height=34, corner_radius=4, font=self.font_body,
            fg_color=FIELD_BG, hover_color=CARD_HOVER, border_width=1,
            border_color=FIELD_BORDER, text_color=TEXT,
            command=lambda: self.revise_script(self.board_comment.get()))
        self.board_change.grid(row=0, column=1, padx=(0, 8), pady=(12, 4))
        ctk.CTkButton(
            bar, text="Make both shapes", width=140, height=34, corner_radius=4,
            font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER, border_width=1,
            border_color=FIELD_BORDER, text_color=TEXT, command=self.make_both_shapes,
        ).grid(row=0, column=2, padx=(0, 8), pady=(12, 4))
        ctk.CTkButton(
            bar, text=f"{self.icon('play')}   Make video", width=150, height=34,
            corner_radius=4, font=self.font_bold, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color=ACCENT_TEXT, command=self.start_render,
        ).grid(row=0, column=3, padx=(0, 14), pady=(12, 4))
        self.board_status = ctk.CTkLabel(bar, text="", font=self.font_tiny, text_color=TEXT_DIM,
                                         anchor="w", justify="left", wraplength=820)
        self.board_status.grid(row=1, column=0, columnspan=3, sticky="w", padx=16, pady=(0, 10))
        self.board_side = ctk.CTkScrollableFrame(
            page, width=SIDE, fg_color="transparent", scrollbar_fg_color=SCROLLBAR_BG,
            scrollbar_button_color=SCROLLBAR_THUMB,
            scrollbar_button_hover_color=SCROLLBAR_THUMB_HOVER)
        self.board_side.grid_columnconfigure(0, weight=1)
        self._build_scene_preview(self.board_side)
        self.video_box = self._build_video_box(self.board_side)
        self._options_open = False
        self._show_board_view()
        return outer

    def _layout_board(self):
        """Wide: the side panel sits right of the scenes. Narrow: "Video options" opens it."""
        side = self._frame_of(self.board_side)
        tk.Frame.place_forget(side)
        self._options_open = False
        if self.wide:
            self.board_side.grid(row=1, column=1, rowspan=3, sticky="ns", padx=(PAD, 0))
            self.options_button.pack_forget()
        else:
            self.board_side.grid_remove()
            self.options_button.configure(text="Video options")
            self.options_button.pack(side="left", padx=(0, 8), before=self._first_step)
        self._draw_timeline()

    def _toggle_video_options(self):
        side = self._frame_of(self.board_side)
        self._options_open = not self._options_open
        if self._options_open:
            scale = ctk.ScalingTracker.get_widget_scaling(self)
            tk.Frame.place(side, relx=1, rely=0, anchor="ne", relheight=1,
                           width=(SIDE + 30) * scale)
            side.lift()
        else:
            tk.Frame.place_forget(side)
        self.options_button.configure(text="Hide options" if self._options_open
                                      else "Video options")

    # -- the side panel: the selected scene, big -------------------------------------
    def _build_scene_preview(self, parent):
        card = Card(parent)
        card.pack(fill="x", pady=(0, PAD))
        self.preview_title = ctk.CTkLabel(card, text="Scene 1", font=self.font_bold,
                                          text_color=TEXT, anchor="w")
        self.preview_title.pack(fill="x", padx=14, pady=(12, 6))
        self.preview_picture = ctk.CTkLabel(card, text="No picture yet", fg_color=FIELD_BG,
                                            corner_radius=6, font=self.font_tiny,
                                            text_color=TEXT_DIM, width=180, height=320)
        self.preview_picture.pack(padx=14)
        self.preview_line = ctk.CTkLabel(card, text="", font=self.font_body, text_color=TEXT,
                                         anchor="w", justify="left", wraplength=SIDE - 40)
        self.preview_line.pack(fill="x", padx=14, pady=(8, 4))
        ctk.CTkButton(card, text=f"{self.icon('play')}  Hear it", width=110, height=28,
                      corner_radius=4, font=self.font_tiny, fg_color=FIELD_BG,
                      hover_color=CARD_HOVER, border_width=1, border_color=FIELD_BORDER,
                      text_color=TEXT, command=self._hear_selected).pack(anchor="w", padx=14,
                                                                         pady=(0, 12))
        self.board_selected, self._preview_image = 0, None

    def _board_select(self, index):
        """A scene card was clicked: it is shown big in the side panel."""
        if self.board is None or not 0 <= index < len(self.board_cards):
            return
        self.board_selected = index
        if self.board_cards[index] is None:   # its chapter is folded: open it
            self._open_chapter_of(index)
        for at, widgets in enumerate(self.board_cards):
            if widgets is not None:
                widgets["card"].configure(border_width=2 if at == index else 0,
                                          border_color=ACCENT)
        self._show_preview()

    def _show_preview(self):
        from PIL import Image, ImageOps

        index = self.board_selected
        if self.board is None or index >= len(self.board):
            self.preview_title.configure(text="")
            self.preview_line.configure(text="")
            self.preview_picture.configure(image=None, text="No scenes yet")
            return
        self.preview_title.configure(text=f"Scene {index + 1}")
        self.preview_line.configure(text=self.board.scenes[index].voice or "(no words)")
        box = (180, 320) if self.var_aspect.get() == "9:16" else (SIDE - 28, 194)
        # Drop the old picture first: resizing redraws it, and it may already be freed.
        self.preview_picture.configure(image=None)
        self.preview_picture.configure(width=box[0], height=box[1])
        path = self.board_paths.get(index)
        try:
            with Image.open(path) as opened:
                fitted = ImageOps.fit(opened.convert("RGB"), (box[0] * 2, box[1] * 2))
            self._preview_image = ctk.CTkImage(light_image=fitted, dark_image=fitted, size=box)
            self.preview_picture.configure(image=self._preview_image, text="")
        except Exception:  # noqa: BLE001 - no picture yet
            self.preview_picture.configure(image=None, text="No picture yet")
            self._preview_image = None

    def _hear_selected(self):
        if self.board is not None and self.board_selected < len(self.board):
            voice = self.board.scenes[self.board_selected].voice
            if voice:
                self.hear_line(voice)

    # -- the side panel: this video's choices, each in its own box -------------------
    def _option_card(self, parent, glyph, title):
        """A lighter box inside "This video": its title, then the body for its controls."""
        card = ctk.CTkFrame(parent, fg_color=FIELD_BG, corner_radius=6)
        card.pack(fill="x", padx=12, pady=(0, 8))
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=(10, 6))
        ctk.CTkLabel(head, text=self.icon(glyph), font=self.font_icon,
                     text_color=TEXT_MUTED).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(head, text=title, font=self.font_bold, text_color=TEXT).pack(side="left")
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=12, pady=(0, 12))
        return card, body

    def _menu(self, parent, values, variable, width):
        return ctk.CTkOptionMenu(
            parent, values=values, variable=variable, width=width, height=30, corner_radius=4,
            font=self.font_body, fg_color=CARD_BG, button_color=CARD_BG,
            button_hover_color=CARD_HOVER, text_color=TEXT, text_color_disabled=TEXT_DIM,
            dropdown_fg_color=CARD_BG, dropdown_text_color=TEXT, dropdown_hover_color=CARD_HOVER,
            dropdown_font=self.font_body, dynamic_resizing=False)

    def _build_video_box(self, parent):
        """Everything about this video in one place: shape, captions, voice, length, looks.

        The same variables as before, so the same settings keys are saved.
        """
        box = Card(parent)
        box.pack(fill="x")
        ctk.CTkLabel(box, text="This video", font=self.font_title, text_color=TEXT,
                     anchor="w").pack(fill="x", padx=14, pady=(12, 8))
        inner = SIDE - 56

        _card, body = self._option_card(box, "aspect", "Shape")
        FluentSegmented(body, self, RATIO_OPTIONS, self.var_aspect, inner).pack(anchor="w")
        ctk.CTkLabel(body, text="9:16 for Shorts and TikTok, 16:9 for YouTube", font=self.font_tiny,
                     text_color=TEXT_DIM, anchor="w").pack(fill="x", pady=(6, 0))

        _card, body = self._option_card(box, "captions", "Captions")
        ctk.CTkSwitch(body, text="Show captions", font=self.font_body, text_color=TEXT,
                      variable=self.var_captions, onvalue=True, offvalue=False,
                      progress_color=ACCENT).pack(anchor="w")
        grid = ctk.CTkFrame(body, fg_color="transparent")
        grid.pack(fill="x", pady=(8, 8))
        self.caption_menus = []
        for at, (key, allowed, _default) in enumerate(CAPTION_SETTINGS):
            menu = self._menu(grid, list(allowed), self.caption_vars[key], (inner - 8) // 2)
            menu.grid(row=at // 2, column=at % 2, padx=(0, 8) if at % 2 == 0 else 0, pady=2)
            self.caption_menus.append(menu)
        self.caption_sample = ctk.CTkFrame(body, fg_color="#101010", corner_radius=6,
                                           height=96, width=inner)
        self.caption_sample.pack(fill="x")
        self.caption_sample.pack_propagate(False)
        self._caption_words = None
        for var in (self.var_captions, *self.caption_vars.values()):
            var.trace_add("write", self._sync_caption_controls)
        self.var_aspect.trace_add("write", self._shape_changed)

        _card, body = self._option_card(box, "voice", "Voice")
        self.voice_menus.append(self._menu(body, VOICE_OPTIONS, self.var_voice, inner))
        self.voice_menus[-1].pack(anchor="w")
        speed = ctk.CTkFrame(body, fg_color="transparent")
        speed.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(speed, text="Speed", font=self.font_body, text_color=TEXT).pack(
            side="left", padx=(0, 12))
        FluentSegmented(speed, self, list(voices.SPEEDS), self.var_voice_speed, 240).pack(
            side="left")
        self.var_voice_speed.trace_add("write", lambda *_a: (self.refresh_estimate(),
                                                             self._draw_timeline()))
        self.preview_button = ctk.CTkButton(
            body, text=f"{self.icon('play')}  Preview Voice", width=150,
            height=30, corner_radius=4, font=self.font_body, fg_color=CARD_BG,
            hover_color=CARD_HOVER, border_width=1, border_color=FIELD_BORDER,
            text_color=TEXT, command=self.preview_voice)
        self.preview_button.pack(anchor="w", pady=(8, 0))

        _card, body = self._option_card(box, "timer", "Length")
        # Seven lengths since 4.9.2 (up to 10 min): too many for a segmented row.
        self._menu(body, list(pacing.TARGETS), self.var_target, 160).pack(anchor="w")
        versions = ctk.CTkFrame(body, fg_color="transparent")
        versions.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(versions, text="Versions", font=self.font_body,
                     text_color=TEXT).pack(side="left", padx=(0, 12))
        FluentSegmented(versions, self, list(VERSION_OPTIONS), self.var_versions, 100).pack(
            side="left")
        # Its own line, so "too short for 60 s" is never cut off.
        self.estimate_label = ctk.CTkLabel(body, text="", font=self.font_tiny,
                                           text_color=TEXT_MUTED, anchor="w", justify="left",
                                           wraplength=inner)
        self.estimate_label.pack(fill="x", pady=(8, 0))

        # The look of AI pictures: only there once AI pictures are downloaded.
        self.look_bar, body = self._option_card(box, "images", "AI picture look")
        FluentSegmented(body, self, list(imagegen.LOOKS), self.var_ai_look, inner).pack(
            anchor="w")
        look_hint = ctk.CTkLabel(body, text=AI_LOOK_HINTS.get(self.var_ai_look.get(), ""),
                                 font=self.font_tiny, text_color=TEXT_DIM, anchor="w",
                                 justify="left", wraplength=inner)
        look_hint.pack(fill="x", pady=(6, 0))
        self.var_ai_look.trace_add("write", lambda *_args: look_hint.configure(
            text=AI_LOOK_HINTS.get(self.var_ai_look.get(), "")))

        self.style_card, body = self._option_card(box, "script", "Style")
        FluentSegmented(body, self, list(drawn.STYLES), self.var_style, inner).pack(anchor="w")
        ctk.CTkLabel(body, text="Normal footage, or every scene drawn: chalk on a blackboard, or a "
                                "flat-colour drawing", font=self.font_tiny, text_color=TEXT_DIM,
                     anchor="w", justify="left", wraplength=inner).pack(fill="x", pady=(6, 0))

        _card, body = self._option_card(box, "resolution", "Thumbnail")
        self.thumb_label = ctk.CTkLabel(body, text="Made when the scenes have pictures",
                                        fg_color=CARD_BG, corner_radius=4, width=108, height=192,
                                        font=self.font_tiny, text_color=TEXT_DIM,
                                        wraplength=100)
        self.thumb_label.pack(anchor="w")
        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.pack(fill="x", pady=(8, 0))
        for text, command in (("Change picture", self.next_thumb_scene),
                              ("Edit thumbnail", lambda: self.var_board_view.set("Thumbnail"))):
            ctk.CTkButton(buttons, text=text, width=130, height=28, corner_radius=4,
                          font=self.font_tiny, fg_color=CARD_BG, hover_color=CARD_HOVER,
                          border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                          command=command).pack(side="left", padx=(0, 8))
        ctk.CTkFrame(box, fg_color="transparent", height=4).pack()
        self._sync_look_bar()
        self._sync_caption_controls()
        return box

    def _show_board_view(self):
        for name, view in self.board_views.items():
            if name == self.var_board_view.get():
                view.grid(row=1, column=0, sticky="nsew")
            else:
                view.grid_remove()
        if self.var_board_view.get() == "Thumbnail":
            self.edit_thumbnail()

    def _to_from_text(self, text):
        """Plain text in the script box: move it to Make new video > From text."""
        self.door_box.delete("1.0", "end")
        self.door_box.insert("1.0", text)
        self.select_page("new")

    def board_box(self):
        """The size of a card's picture: the shape of the video, small."""
        return (81, 144) if self.var_aspect.get() == "9:16" else (176, 99)

    def refresh_board(self, force=False):
        """Rebuild the cards if the script changed since they were built."""
        text = self.script_box.get("1.0", "end").strip()
        if not force and text == self.board_text and self.board_cards:
            return
        for child in self.board_list.winfo_children():
            child.destroy()
        self.board_cards, self.board_images, self.board_paths = [], {}, {}
        self._timeline_pictures = {}
        self.board_gen += 1
        try:
            self.board = boards.Board.from_text(text)
        except ScriptError as exc:
            self.board, self.board_text = None, None
            hint = ("There is plain text in the script box. Paste it in Make new video > From text "
                    "instead and press Make video." if looks_like_plain_text(text) else
                    f"The script has a mistake, so there is nothing to show yet.\n{exc}\n"
                    "Fix it in Settings > Advanced > Script editor.")
            ctk.CTkLabel(self.board_list, text=hint, font=self.font_body, text_color=TEXT_MUTED,
                         justify="left", anchor="w").grid(row=0, column=0, sticky="w",
                                                          padx=8, pady=(20, 8))
            if looks_like_plain_text(text):
                label, command = "Use it in From text", lambda: self._to_from_text(text)
            else:
                label, command = "Open the script", lambda: self.select_page("create")
            ctk.CTkButton(self.board_list, text=label, width=170, height=32, corner_radius=4,
                          font=self.font_body, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                          text_color=ACCENT_TEXT, command=command).grid(row=1, column=0,
                                                                        sticky="w", padx=8)
            self._draw_timeline()
            return
        self.board_text = text
        self._draw_timeline()
        lengths = self.board.lengths(self.voice_now(),
                                     PADDING_OPTIONS.get(self.var_padding.get(), DEFAULT_PADDING))
        self._board_lengths = lengths
        self.board_cards = [None] * len(self.board)   # None: folded away in its chapter
        self.board_notes, self.chapter_heads = {}, {}
        groups = boards.chapters(self.board.scenes)
        key = tuple(start for start, _end, _title in groups)
        if key != getattr(self, "_chapter_key", None):   # a new script: the first one open
            self._chapter_key, self.chapters_open = key, {key[0]} if key else set()
        for start, end, title in groups:
            if title is not None:
                self._chapter_head(start, end, title, lengths)
            if title is None or start in self.chapters_open:
                for index in range(start, end):
                    self._board_card(index, lengths)
        self._board_select(min(self.board_selected, len(self.board) - 1))
        if not self._revising:
            self.board_status.configure(
                text=f"{len(self.board)} scenes. Change anything here, then press Make video.",
                text_color=TEXT_DIM)
        threading.Thread(target=self._board_load, daemon=True,
                         args=(self.board_gen, list(self.board.scenes), self._board_ctx())).start()

    # -- chapters: a long video's parts fold open and shut (4.9.2) -------------------
    def _chapter_head(self, start, end, title, lengths):
        seconds = sum(lengths[start:end]) if len(lengths) >= end else 0
        head = ctk.CTkButton(
            self.board_list, text="", anchor="w", height=34, corner_radius=4,
            font=self.font_bold, fg_color=CARD_BG, hover_color=CARD_HOVER, text_color=TEXT,
            command=lambda: self._toggle_chapter(start, end))
        head.grid(row=start * 2, column=0, sticky="ew", pady=(6, 6))
        self.chapter_heads[start] = (head, f"{title}   ·   scenes {start + 1} to {end}   ·   "
                                           f"{int(seconds // 60)}:{int(seconds % 60):02d}")
        self._chapter_label(start)

    def _chapter_label(self, start):
        head, text = self.chapter_heads[start]
        head.configure(text=("\u25be  " if start in self.chapters_open else "\u25b8  ") + text)

    def _toggle_chapter(self, start, end, open_it=None):
        """Show or hide one chapter's cards; pictures already loaded come straight back."""
        open_it = start not in self.chapters_open if open_it is None else open_it
        if open_it == (start in self.chapters_open):
            return
        if open_it:
            self.chapters_open.add(start)
            for index in range(start, end):
                self._board_card(index, self._board_lengths)
                if index in self.board_paths:
                    self._board_show(index, self.board_paths[index])
                if index in self.board_notes:
                    self.board_cards[index]["status"].configure(text=self.board_notes[index])
        else:
            self.chapters_open.discard(start)
            for index in range(start, end):
                if self.board_cards[index] is not None:
                    self.board_cards[index]["card"].destroy()
                    self.board_cards[index] = None
                    self.board_images.pop(index, None)
        self._chapter_label(start)

    def _open_chapter_of(self, index):
        for start, end, title in boards.chapters(self.board.scenes):
            if start <= index < end and title is not None:
                self._toggle_chapter(start, end, True)

    def _board_card(self, index, lengths=()):
        scene, kind = self.board.scenes[index], self.board.source(index)
        box = self.board_box()
        card = Card(self.board_list)
        card.grid(row=index * 2 + 1, column=0, sticky="ew", pady=(0, 6))
        card.grid_columnconfigure(1, weight=1)
        picture = ctk.CTkLabel(card, text="...", width=box[0], height=box[1], fg_color=FIELD_BG,
                               corner_radius=4, font=self.font_tiny, text_color=TEXT_DIM)
        picture.grid(row=0, column=0, rowspan=4, padx=(10, 12), pady=10)
        for widget in (card, picture):   # a click shows the scene big in the side panel
            widget.bind("<Button-1>", lambda _e, i=index: self._board_select(i))

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=1, sticky="ew", pady=(8, 0))
        ctk.CTkLabel(head, text=f"Scene {index + 1}", font=self.font_bold,
                     text_color=TEXT).pack(side="left")
        if index in self.board_marked:
            ctk.CTkLabel(head, text="  changed", font=self.font_tiny,
                         text_color=WARN_COLOR).pack(side="left")
        status = ctk.CTkLabel(head, text="", font=self.font_tiny, text_color=TEXT_DIM)
        status.pack(side="left", padx=(10, 0))
        if index < len(lengths):
            ctk.CTkLabel(head, text=f"{lengths[index]:.1f} s", font=self.font_tiny,
                         text_color=TEXT_DIM).pack(side="right", padx=(0, 4))

        def field(row, name, value, hint):
            entry = focus_accent(ctk.CTkEntry(
                card, height=30, corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
                border_color=FIELD_BORDER, text_color=TEXT, placeholder_text=hint))
            if value:
                entry.insert(0, value)
            entry.grid(row=row, column=1, sticky="ew", pady=2)
            entry.bind("<FocusIn>", lambda _e, i=index: self._board_select(i), add="+")
            for event in ("<FocusOut>", "<Return>"):
                entry.bind(event, lambda _e, i=index, n=name, w=entry: self._board_edit(i, n, w.get()))
            return entry

        field(1, "voice", scene.voice, "What is said in this scene")
        field(2, "visual", self.board.shown(index),
              {boards.STOCK: "Search words for stock footage",
               boards.AI: "What the AI picture shows", boards.LOCAL: "Your file"}[kind])
        field(3, "card", scene.card, "Card (optional): Win + V   or   $120,000 | after 30 years")

        def button(parent, text, command, width=118):
            return ctk.CTkButton(parent, text=text, width=width, height=28, corner_radius=4,
                                 font=self.font_tiny, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                                 border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
                                 command=command)

        side = ctk.CTkFrame(card, fg_color="transparent")
        side.grid(row=0, column=2, rowspan=4, padx=(10, 4), pady=8, sticky="n")
        action = None
        if kind == boards.STOCK:
            action = button(side, "Another clip", lambda i=index: self._board_another(i))
        elif kind == boards.AI:
            action = button(side, "Make picture", lambda i=index: self._board_make(i))
        if action is not None:
            action.pack(pady=(0, 4))
        if kind == boards.AI:
            button(side, "Use a stock clip",
                   lambda i=index: self._board_do(lambda: self.board.set_source(i, boards.STOCK)),
                   ).pack(pady=(0, 4))
        elif kind == boards.STOCK and imagegen.installed():
            button(side, "Use an AI picture",
                   lambda i=index: self._board_do(lambda: self.board.set_source(i, boards.AI)),
                   ).pack(pady=(0, 4))
        if scene.voice:
            button(side, "Hear it", lambda i=index: self.hear_line(self.board.scenes[i].voice),
                   ).pack(pady=(0, 4))

        order = ctk.CTkFrame(card, fg_color="transparent")
        order.grid(row=0, column=3, rowspan=4, padx=(0, 10), pady=8, sticky="n")
        for text, act in (("Up", lambda i=index: self.board.move(i, -1)),
                          ("Down", lambda i=index: self.board.move(i, 1)),
                          ("Add", lambda i=index: self.board.insert(i)),
                          ("Delete", lambda i=index: self.board.delete(i))):
            button(order, text, lambda a=act: self._board_do(a), width=64).pack(pady=(0, 4))
        self.board_cards[index] = {"picture": picture, "status": status, "action": action,
                                   "card": card}

    # -- edits: every one is written back to the script -------------------------------
    # -- the timeline under the cards ---------------------------------------------------
    def _draw_timeline(self, lengths=None):
        canvas = self.timeline
        canvas.delete("all")
        canvas.configure(bg=theme_color(MAIN_BG))
        if self.board is None or not len(self.board):
            self._timeline_edges = []
            return
        persona = self.voice_now()
        padding = PADDING_OPTIONS.get(self.var_padding.get(), DEFAULT_PADDING)
        lengths = lengths or self.board.lengths(persona, padding)
        width = max(canvas.winfo_width(), 200) - 8
        self._timeline_scale = width / max(sum(lengths), 0.1)
        x, self._timeline_edges = 4.0, []
        for i, (scene, length) in enumerate(zip(self.board.scenes, lengths)):
            w = length * self._timeline_scale
            spoken = pacing.estimate_voice(scene.voice or "", persona.rate, persona.pause_scale,
                                           persona.enhanced)
            fixed = scene.duration is not None
            cut = fixed and spoken > scene.duration + 0.05   # the line won't fit: shown
            canvas.create_rectangle(x, 4, x + w - 3, 42, outline="", fill=theme_color(
                WARN_COLOR if cut else CARD_HOVER if fixed else CARD_BG))
            canvas.create_rectangle(x + w - 8, 4, x + w - 3, 42, outline="",
                                    fill=theme_color(ACCENT if fixed else FIELD_BORDER))
            picture = self._timeline_picture(i) if self.wide and w > 90 else None
            if picture is not None:
                canvas.create_image(x + 4, 23, anchor="w", image=picture)
            if w > 34:
                label = f"{i + 1}  {length:.1f} s" if w > 70 else str(i + 1)
                canvas.create_text(x + 7 + (picture.width() + 4 if picture else 0), 23,
                                   anchor="w", text=label, font=self.font_tiny,
                                   fill=theme_color(TEXT))
            x += w
            self._timeline_edges.append(x)
        # A long video's chapters: a line where each starts, and its title over the blocks.
        edges = [4.0] + self._timeline_edges
        for start, end, title in boards.chapters(self.board.scenes):
            if title is None:
                continue
            left, right = edges[start], edges[end]
            canvas.create_rectangle(left, 4, left + 2, 42, outline="", fill=theme_color(ACCENT))
            if right - left > 40:
                canvas.create_rectangle(left + 2, 4, min(right - 3, left + 10 + 7 * len(title)),
                                        20, outline="", fill=theme_color(MAIN_BG))
                canvas.create_text(left + 6, 12, anchor="w", text=title, font=self.font_tiny,
                                   fill=theme_color(TEXT))

    def _timeline_picture(self, index):
        """A 34 px high picture of scene ``index`` for its timeline block, or None."""
        path = self.board_paths.get(index)
        if not path:
            return None
        kept = self._timeline_pictures.get(index)
        if kept and kept[0] == path:
            return kept[1]
        from PIL import Image, ImageTk

        try:
            with Image.open(path) as opened:
                small = opened.convert("RGB")
            small.thumbnail((60, 34))
            photo = ImageTk.PhotoImage(small, master=self.timeline)
        except Exception:  # noqa: BLE001 - no picture: the block stays plain
            return None
        self._timeline_pictures[index] = (path, photo)
        return photo

    # -- the thumbnail: made from the scenes' pictures as soon as there are some --------
    def _thumb_scenes(self):
        return sorted(i for i, path in self.board_paths.items() if path and os.path.isfile(path))

    def _thumb_scene(self):
        """The scene the thumbnail uses: the chosen one, else the first with a picture."""
        found = self._thumb_scenes()
        chosen = self.thumb.get("scene")
        return chosen if chosen in found else (found[0] if found else None)

    def thumb_title(self):
        return self.thumb.get("title") or projects.default_title(
            self.script_box.get("1.0", "end"), 60)

    def next_thumb_scene(self):
        from vidgen import thumbedit

        found, now = self._thumb_scenes(), self._thumb_scene()
        if found:
            self.thumb["scene"] = found[(found.index(now) + 1) % len(found)]
            edited = self._edited_thumb()
            if edited:   # an edited thumbnail keeps its words: only the picture changes
                folder = os.path.dirname(edited)
                layers, size = thumbedit.load(folder)
                if layers and layers[0]["kind"] == "background":
                    layers[0]["path"] = self.board_paths[self.thumb["scene"]]
                    try:
                        thumbedit.save(folder, layers, size)
                    except OSError:
                        pass
            self._refresh_thumb()

    def _edited_thumb(self):
        """The thumbnail made in the editor for this video's shape, or None."""
        from vidgen import thumbedit

        return thumbedit.edited(projects.folder(self.project) if self.project else None,
                                self.var_aspect.get() == "9:16")

    def edit_thumbnail(self):
        from vidgen import thumbedit, thumbnail

        """The Storyboard's Thumbnail tab: the editor for the open video, made again when
        the video or its shape changed; the scene pictures are brought up to date."""
        self.save_project()   # the edit is kept in the project's folder
        tab = self.board_thumb_tab
        portrait = self.var_aspect.get() == "9:16"
        backgrounds = [(f"Scene {i + 1}", self.board_paths[i]) for i in self._thumb_scenes()]
        key = (self.project, portrait)
        editor = self.board_thumb_editor
        if editor is not None and editor.winfo_exists() and key == self._board_thumb_key:
            editor.set_backgrounds(backgrounds)
            return
        for child in tab.winfo_children():
            child.destroy()
        self.board_thumb_editor, self._board_thumb_key = None, key
        if not self.project:
            ctk.CTkLabel(tab, text="Write or open a video first: the thumbnail is made from "
                                   "its scenes.", font=self.font_body, text_color=TEXT_MUTED,
                         ).grid(row=0, column=0, sticky="nw", padx=8, pady=20)
            return
        size = thumbnail.PORTRAIT if portrait else thumbnail.LANDSCAPE
        folder = projects.folder(self.project)
        found = thumbedit.load(folder)
        if found and self._edited_thumb():
            layers = found[0]
        else:
            index = self._thumb_scene()
            layers = thumbedit.automatic(self.board_paths[index] if index is not None else "",
                                         self.thumb_title(), size)
        def reset():
            thumbedit.remove(folder)
            self._refresh_thumb()
            self._board_thumb_key = None   # made again, from the scenes
            self.edit_thumbnail()

        self.board_thumb_editor = ThumbEditor(
            tab, self, folder, size, layers, backgrounds, on_save=self._refresh_thumb,
            on_reset=reset, title="Thumbnail")
        self.board_thumb_editor.grid(row=0, column=0, sticky="nsew")

    def _schedule_thumb(self):
        if self._thumb_job:
            self.after_cancel(self._thumb_job)
        self._thumb_job = self.after(300, self._refresh_thumb)

    def _refresh_thumb(self):
        from vidgen import thumbnail

        self._thumb_job = None
        index = self._thumb_scene()
        portrait = self.var_aspect.get() == "9:16"
        box = (108, 192) if portrait else (240, 135)
        self.thumb_label.configure(width=box[0], height=box[1])
        edited = self._edited_thumb()   # made in the editor: shown as it is
        if index is None and not edited:
            self._thumb_image = None
            self.thumb_label.configure(image=None, text="Made when the scenes have pictures")
            return
        try:
            if edited:
                from PIL import Image

                with Image.open(edited) as opened:
                    made = opened.convert("RGB")
            else:
                made = thumbnail.from_image(self.board_paths[index], self.thumb_title(),
                                            thumbnail.PORTRAIT if portrait else thumbnail.LANDSCAPE)
        except Exception:  # noqa: BLE001 - the render still makes one
            return
        made.thumbnail(box)
        self._thumb_image = ctk.CTkImage(light_image=made, dark_image=made, size=made.size)
        self.thumb_label.configure(image=self._thumb_image, text="")

    def _timeline_at(self, x):
        """(scene index, whether x is on its right edge) for a point on the strip."""
        start = 4.0
        for i, edge in enumerate(self._timeline_edges):
            if x <= edge:
                return i, edge - x <= 9
            start = edge
        return None, False

    def _timeline_press(self, event):
        if self.board is None or self.is_rendering:
            return
        index, on_edge = self._timeline_at(event.x)
        if index is None:
            return
        start = self._timeline_edges[index - 1] if index else 4.0
        self._timeline_hold = {"index": index, "start": start, "drag": on_edge, "moved": False}

    def _timeline_drag(self, event):
        hold = self._timeline_hold
        if not hold or not hold["drag"]:
            return
        hold["moved"] = True
        self.board.set_length(hold["index"], (event.x - hold["start"]) / self._timeline_scale)
        self._draw_timeline()

    def _timeline_release(self, _event):
        hold, self._timeline_hold = self._timeline_hold, None
        if not hold:
            return
        if hold["moved"]:
            self._board_write()
        elif self.board_cards:   # a click: bring its card into view
            self._board_select(hold["index"])
            self.update_idletasks()
            widgets = self.board_cards[hold["index"]]
            inner = self.board_list
            if widgets is not None and inner.winfo_height() > 0:
                self.board_list._parent_canvas.yview_moveto(
                    widgets["card"].winfo_y() / inner.winfo_height())

    def _timeline_auto(self, event):
        index, _edge = self._timeline_at(event.x)
        if index is not None and self.board is not None and not self.is_rendering:
            self.board.set_length(index, None)
            self._board_write()

    def _board_write(self):
        text = self.board.text()
        if self.board_text and self.board_text != text:   # the step Undo goes back to
            self.board_history = (self.board_history + [self.board_text])[-UNDO_STEPS:]
            self.board_future = []
        self.board_text = text
        self.script_box.delete("1.0", "end")
        self.script_box.insert("1.0", text)
        self._commit_script()
        self.refresh_estimate()
        self._draw_timeline()

    def _board_edit(self, index, name, value):
        if self.board is None or index >= len(self.board):
            return
        before = self.board.text()
        self.board.set(index, name, value)
        if self.board.text() == before:
            return
        self._board_write()
        if name == "visual":   # other words, another clip or picture: load this card again
            scene = self.board.scenes[index]
            threading.Thread(target=self._board_load_one, daemon=True,
                             args=(self.board_gen, index, scene, self._board_ctx())).start()

    def board_undo(self, redo=False):
        """Back (or forward again) one change made in the Storyboard."""
        steps, other = ((self.board_future, self.board_history) if redo else
                        (self.board_history, self.board_future))
        if not steps or self.is_rendering or self._revising:
            return
        other.append(self.script_box.get("1.0", "end").strip())
        text = steps.pop()
        self.script_box.delete("1.0", "end")
        self.script_box.insert("1.0", text)
        self.board_marked = set()
        self._commit_script()
        self.refresh_board(force=True)
        self.refresh_estimate()

    def _shown_editor(self):
        """The thumbnail editor on screen (Thumbnails page or Storyboard tab), or None."""
        for editor in (getattr(self, "thumbs_editor", None),
                       getattr(self, "board_thumb_editor", None)):
            if editor is not None and editor.winfo_exists() and editor.winfo_ismapped():
                return editor
        return None

    def _editor_key(self, event, step=None, pixels=1):
        """Arrow keys nudge and Delete removes the editor's selected layer."""
        editor = self._shown_editor()
        if editor is None or editor._typing(event):
            return None
        if step is None:
            editor.delete()
            return "break"
        return editor._nudge(event, step, pixels)

    def _undo_key(self, redo):
        """Ctrl+Z / Ctrl+Y on the Storyboard and in the thumbnail editor; a text box keeps
        its own undo."""
        editor = self._shown_editor()
        if editor is not None and not isinstance(self.focus_get(), (tk.Entry, tk.Text)):
            editor.redo() if redo else editor.undo()
            return "break"
        if self.current_page == "board" and not isinstance(self.focus_get(), (tk.Entry, tk.Text)):
            self.board_undo(redo)
            return "break"
        return None

    def _board_do(self, change):
        """A change to the order or the kind of a scene: every card is built again."""
        if self.board is None or self.is_rendering:
            return
        before = self.board.text()
        change()
        if self.board.text() != before:
            self.board_marked = set()
            self._board_write()
            self.refresh_board(force=True)

    # -- pictures: found on worker threads, shown through the queue ---------------------
    def _picture_key(self, scene, ctx=None) -> str:
        ctx = ctx or self._board_ctx(search=False)
        return f"{scene.ai_prompt}|{ctx['look']}|{ctx['aspect']}"

    def _board_ctx(self, search=True) -> dict:
        """UI thread: what the picture workers need, read here because they must not touch Tk."""
        return {"look": self.var_ai_look.get(), "aspect": self.var_aspect.get(),
                "style": self.var_style.get(),
                "has_key": self._has_footage_key(), "ai": imagegen.installed(),
                "base": os.path.dirname(os.path.abspath(self.var_output.get() or ".")),
                "search": self.new_footage_search() if search else None}

    def _board_post(self, gen, **payload):
        self._queue.put(("board", dict(payload, gen=gen)))

    def _board_load(self, gen, scenes_now, ctx):
        """Worker: the cards' stills, three at a time (one by one, 140 scenes took minutes)."""
        from concurrent.futures import ThreadPoolExecutor

        def one(item):
            if gen == self.board_gen:   # the cards were rebuilt: stop
                self._board_load_one(gen, *item, ctx)

        with ThreadPoolExecutor(3, thread_name_prefix="still") as pool:
            list(pool.map(one, enumerate(scenes_now)))

    def _board_load_one(self, gen, index, scene, ctx):
        """Worker thread: the still for one card, and what to say beside it."""
        path, note = None, ""
        try:
            if scene.is_local:
                found, kind = footage.resolve_local(scene.local_path, ctx["base"])
                note = "Your own file"
                if kind == "image":
                    path = found
                else:
                    width, height, _length = preview.probe(found)
                    still = preview.poster(found, preview.fit((width, height), (320, 320)), at=0.5)
                    os.makedirs(THUMB_DIR, exist_ok=True)
                    path = os.path.join(THUMB_DIR, f"board_local_{index}.jpg")
                    still.save(path)
            elif scene.is_ai:
                path = self.board_pictures.get(self._picture_key(scene, ctx))
                note = "AI picture" if path else (
                    "AI picture: made with the video, or press Make picture to see it now"
                    if ctx["ai"] else "AI picture: download AI pictures in Settings to use it")
            elif not ctx["has_key"]:
                note = "Stock clip: add a free key in Settings > Footage keys to see it"
            else:
                query = scene.visual
                if query not in self.board_clips:
                    # Ranked as the render ranks them, so the card shows the clip it uses.
                    self.board_clips[query] = footage.rank(ctx["search"].find(query, 6), query,
                                                           scene.voice or "")
                clips = self.board_clips[query]
                if clips:
                    at = self.board_pos.get(query, 0) % len(clips)
                    path = scenes.fetch_thumb(clips[at], THUMB_DIR)
                    told = clips[at].about[:48]
                    note = f"Stock clip {at + 1} of {len(clips)}" + (f": {told}" if told else "")
                else:
                    note = "No stock clip found for these words"
            style = ctx.get("style")
            if path and style in drawn.STYLES and style != drawn.NORMAL:
                # The render draws every scene from its still: show the card the same way.
                from PIL import Image

                with Image.open(path) as image:
                    finished = drawn.prepare(image, preview.fit(image.size, (320, 320)), style)[0]
                os.makedirs(THUMB_DIR, exist_ok=True)
                path = os.path.join(THUMB_DIR, f"board_drawn_{index}.jpg")
                Image.fromarray(finished).save(path)
                note += f" · drawn as {style}"
        except Exception as exc:  # noqa: BLE001 - a missing still is not worth an error
            note = note or f"Could not load a picture ({type(exc).__name__})"
        self._board_post(gen, event="still", index=index, path=path, note=note)

    def _board_another(self, index):
        scene = self.board.scenes[index]
        clips = self.board_clips.get(scene.visual) or []
        if len(clips) > 1:
            self.board_pos[scene.visual] = (self.board_pos.get(scene.visual, 0) + 1) % len(clips)
            threading.Thread(target=self._board_load_one, daemon=True,
                             args=(self.board_gen, index, scene, self._board_ctx())).start()

    def _board_make(self, index):
        """Make (or make again) the AI picture for a scene; the video will use this one."""
        scene = self.board.scenes[index]
        if self._making_picture or self.is_rendering:
            self.board_status.configure(text="One picture at a time: wait for the last one.",
                                        text_color=WARN_COLOR)
            return
        if not imagegen.installed():
            self.select_page("images")
            return
        self._making_picture = True
        gen, key = self.board_gen, self._picture_key(scene)
        size, look, backend = imagegen.size_for(self.var_aspect.get()), self.var_ai_look.get(), \
            self.ai_backend_now()
        speed, threads = self.ai_speed_now(), self.threads_pair()
        if self.board_cards[index] is not None:
            self.board_cards[index]["status"].configure(text="Making the picture...")

        def worker():
            import hashlib

            try:
                os.makedirs(PREVIEW_DIR, exist_ok=True)
                name = hashlib.sha1(f"{key}|{time.time()}".encode()).hexdigest()[:16]
                path = os.path.join(PREVIEW_DIR, f"board_{name}.png")
                made = imagegen.make(scene.ai_prompt, size, path, look, backend=backend,
                                     log=self.ui.log, speed=speed, threads=threads)
                self.ui.ai_speed(made.seconds, speed)
                self._board_post(gen, event="made", index=index, key=key, path=path,
                                 seconds=made.seconds, backend=made.backend)
            except Exception as exc:  # noqa: BLE001 - shown on the card
                self._board_post(gen, event="not_made", index=index, message=str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _board_event(self, payload):
        """UI thread: news from the Storyboard's workers."""
        event, index = payload["event"], payload.get("index")
        if event in ("made", "not_made"):
            self._making_picture = False
            if event == "made":
                self.board_pictures[payload["key"]] = payload["path"]
                if payload["backend"] == imagegen.CPU:
                    self.remember_ai_backend(imagegen.CPU)
        if event == "revised":
            self._revised(payload)
            return
        if payload["gen"] != self.board_gen or index is None or index >= len(self.board_cards):
            return   # the cards were rebuilt meanwhile
        widgets = self.board_cards[index]
        if widgets is None:   # folded away: kept for when its chapter opens
            if event != "not_made":
                self.board_notes[index] = payload.get("note", "")
                if payload.get("path"):
                    self._board_show(index, payload["path"])
            return
        if event == "not_made":
            widgets["status"].configure(text=ellipsize(payload["message"], 70),
                                        text_color=ERR_COLOR)
            return
        if event == "made":
            payload = dict(payload, note=f"AI picture, made in {payload['seconds']:.0f} s")
        widgets["status"].configure(text=payload["note"], text_color=TEXT_DIM)
        if widgets["action"] is not None and self.board.source(index) == boards.AI:
            widgets["action"].configure(text="Make another" if payload["path"] else "Make picture")
        self._board_show(index, payload["path"])

    def _board_show(self, index, path):
        from PIL import Image, ImageOps

        if self.board_cards[index] is None:   # folded: the panel, timeline and thumbnail still use it
            if path:
                self.board_paths[index] = path
                if index == self.board_selected:
                    self._show_preview()
                self._draw_timeline()
                self._schedule_thumb()
            return
        picture, box = self.board_cards[index]["picture"], self.board_box()
        try:
            with Image.open(path) as opened:
                fitted = ImageOps.fit(opened.convert("RGB"), (box[0] * 2, box[1] * 2))
            self.board_images[index] = ctk.CTkImage(light_image=fitted, dark_image=fitted, size=box)
            picture.configure(image=self.board_images[index], text="")
            self.board_paths[index] = path
            if index == self.board_selected:
                self._show_preview()
            self._draw_timeline()
            self._schedule_thumb()
        except Exception:  # noqa: BLE001 - no path, or not a picture
            self.board_images.pop(index, None)
            picture.configure(image=None, text="No picture\nyet")

    def board_choices(self, script: str):
        """(stock clips, AI pictures) chosen in the Storyboard, by scene, for ``script``.

        Empty when the Storyboard shows another script: a choice belongs to the
        scene it was made for.
        """
        if self.board is None or (self.board_text or "").strip() != (script or "").strip():
            return {}, {}
        clips, pictures = {}, {}
        for index, scene in enumerate(self.board.scenes):
            if scene.is_ai:
                path = self.board_pictures.get(self._picture_key(scene))
                if path and os.path.isfile(path):
                    pictures[index] = path
            elif not scene.is_local:
                found = self.board_clips.get(scene.visual) or []
                at = self.board_pos.get(scene.visual, 0)
                if found and at:   # the first clip is what the search gives anyway
                    at %= len(found)
                    clips[index] = found[at:] + found[:at]
        return clips, pictures

    # -- "Change it": the Smart writer rewrites the script from a comment ----------------
    def revise_script(self, comment):
        comment = (comment or "").strip()
        if self._revising or self.is_rendering or not comment:
            return
        self.refresh_board()
        if self.board is None:
            return
        ok, why = writer.ready()
        if not ok:
            self.board_status.configure(text=f"Change it needs the Smart writer. {why}",
                                        text_color=WARN_COLOR)
            return
        if any(not scene.voice for scene in self.board.scenes):
            self.board_status.configure(
                text="Change it can't rewrite a script with silent scenes yet. Edit the cards "
                     "instead.", text_color=WARN_COLOR)
            return
        self._revising = True
        self.board_change.configure(state="disabled", text="Thinking...")
        self.board_status.configure(text=f"The Smart writer is changing the script: {comment}",
                                    text_color=TEXT_DIM)
        before = list(self.board.scenes)
        shown = [(self.board.shown(i), scene.voice) for i, scene in enumerate(before)]
        gpu, source = bool(self.var_writer_gpu.get()), self.source_text

        def worker():
            try:
                with writer.Session(lambda: False, gpu=gpu) as session:
                    answer = session.revise(shown, comment, source)
                self._queue.put(("board", {"event": "revised", "before": before,
                                           "answer": answer, "gen": 0}))
            except Exception as exc:  # noqa: BLE001 - shown under the box
                self._queue.put(("board", {"event": "revised", "before": before,
                                           "error": str(exc), "gen": 0}))

        threading.Thread(target=worker, daemon=True).start()

    def _revised(self, payload):
        self._revising = False
        self.board_change.configure(state="normal", text="Change it")
        if payload.get("error"):
            self.board_status.configure(text=payload["error"], text_color=ERR_COLOR)
            return
        before = payload["before"]
        if self.board is None or self.board.scenes != before:
            self.board_status.configure(text="The script changed meanwhile, so the rewrite was "
                                             "not used.", text_color=WARN_COLOR)
            return
        cards = draft.suggest_card if self.var_auto_cards.get() else None
        after = boards.apply_revision(before, payload["answer"], cards)
        marked = set(boards.changed(before, after))
        if not marked and len(after) == len(before) and after == before:
            self.board_status.configure(text="Nothing was changed. Try saying it another way.",
                                        text_color=WARN_COLOR)
            return
        self.board = boards.Board(after)
        self.board_marked = marked
        self.mark_started("rewrite")
        self._board_write()
        self.refresh_board(force=True)
        self.board_comment.delete(0, "end")
        gone = len(before) - len(after)
        parts = [f"{len(marked)} scene{'s' if len(marked) != 1 else ''} changed"] if marked else []
        if gone:
            parts.append(f"{abs(gone)} {'removed' if gone > 0 else 'added'}")
        if not marked and not gone:
            parts = ["the order changed"]
        self.board_status.configure(
            text=", ".join(parts).capitalize() + ". Look them over, then press Make video. "
                 "The Script editor (Settings > Advanced) has the old lines if you undo there (Ctrl+Z).",
            text_color=OK_COLOR)
        self.save_project()

    # -- hearing one line -----------------------------------------------------------------
    def voice_now(self):
        """The chosen voice at the chosen speed: what lengths and estimates are timed with."""
        return voices.at_speed(voices.persona(self.var_voice.get()), self.var_voice_speed.get())

    def hear_line(self, text):
        """Speak one line in the chosen voice, without blocking the window."""
        if not text or self.voice_missing():
            return
        persona, online = voices.persona(self.var_voice.get()), bool(self.var_voice_online.get())
        speed = self.var_voice_speed.get()

        def worker():
            import hashlib

            try:
                os.makedirs(PREVIEW_DIR, exist_ok=True)
                name = hashlib.sha1(f"{persona.id}|{speed}|{online}|{text}".encode()
                                    ).hexdigest()[:16]
                stem = os.path.join(PREVIEW_DIR, f"line_{name}")
                playable = stem + ".play.wav"
                if not os.path.exists(playable):
                    from vidgen import mastering

                    made = generate_voiceover(text, stem + ".mp3", lambda _line: None,
                                              persona.id, online, speed)
                    partial = stem + ".tmp.wav"
                    mastering.run_ffmpeg(["-y", "-i", made, "-ar", "44100", "-ac", "1",
                                          "-c:a", "pcm_s16le", partial])
                    os.replace(partial, playable)
                self._queue.put(("line", {"path": playable, "error": None}))
            except Exception as exc:  # noqa: BLE001 - reported in the window
                self._queue.put(("line", {"path": None, "error": str(exc)}))

        threading.Thread(target=worker, daemon=True).start()

    def _line_ready(self, payload):
        if payload["error"]:
            self.append_log(f"⚠️ Could not read the line out: {payload['error']}")
            self.board_status.configure(text="Could not read the line out - see the Log",
                                        text_color=ERR_COLOR)
        elif sys.platform == "win32":
            import winsound

            winsound.PlaySound(payload["path"], winsound.SND_FILENAME | winsound.SND_ASYNC)

    def set_key_tip(self, on):
        """The free-key reminder after a keyless video: "Don't show again" turns it off."""
        self.key_tip = bool(on)
        save_settings(self._collect_settings())

    # ----------------------------------------------------------- AI IMAGES --
    def _build_images(self, parent):
        page = self._scroll_page(parent)
        page.grid_columnconfigure(0, weight=1)
        rows = itertools.count()

        self._caption(page, "AI pictures (optional)", next(rows), pady=(0, 6))
        pack_row = SettingRow(
            page, self, self.icon("images"), "AI pictures, made on this PC",
            f"{imagegen.MODEL.name}, an open-source model, draws a picture for scenes stock "
            "footage has nothing for")
        pack_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self.images_state = ctk.CTkLabel(pack_row.control, text="", font=self.font_body,
                                         text_color=TEXT_MUTED)
        self.images_state.pack(side="left", padx=(0, 12))
        self.images_button = ctk.CTkButton(
            pack_row.control, text="", width=96, height=32, corner_radius=4,
            font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT)
        self.images_button.pack(side="left")
        # Only shown once the graphics card has failed here and the processor took over.
        self.images_retry = ctk.CTkButton(
            pack_row.control, text="Try the graphics card again", width=190, height=32,
            corner_radius=4, font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT,
            command=lambda: self.remember_ai_backend(""))

        warning = Card(page)
        warning.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkLabel(warning, text=f"{self.icon('info')}  {AI_IMAGES_WARNING}",
                     font=self.font_body, text_color=WARN_COLOR, anchor="w", justify="left",
                     wraplength=820).pack(fill="x", padx=16, pady=12)

        self._caption(page, "In your videos", next(rows))
        for_row = SettingRow(
            page, self, self.icon("script"), "Use AI pictures for",
            "Which scenes New from text gives an AI picture")
        for_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self._segmented(for_row.control, list(AI_IMAGES_FOR), self.var_ai_for, 470)
        self._choice_hint(page, next(rows), self.var_ai_for, AI_FOR_HINTS)

        device_row = SettingRow(page, self, self.icon("resolution"), "Make pictures on",
                                "Where the AI pictures are made")
        device_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self._segmented(device_row.control, list(AI_DEVICES), self.var_ai_device, 380)
        self._choice_hint(page, next(rows), self.var_ai_device, AI_DEVICE_HINTS)
        self.var_ai_device.trace_add("write", self._ai_device_picked)

        speed_row = SettingRow(page, self, self.icon("timer"), "Speed",
                               "Smaller pictures are made much faster, then enlarged")
        speed_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self._segmented(speed_row.control, list(imagegen.SPEEDS), self.var_ai_speed, 380)
        self._choice_hint(page, next(rows), self.var_ai_speed, AI_SPEED_HINTS)

        test_row = SettingRow(page, self, self.icon("timer"), "Test the processor's speed",
                              "For pictures made on the processor: makes one tiny picture a "
                              "few ways (a few minutes on a laptop) and keeps the fastest")
        test_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self.speed_test_button = ctk.CTkButton(
            test_row.control, text="Test speed", width=110, height=32, corner_radius=4,
            font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER, border_width=1,
            border_color=FIELD_BORDER, text_color=TEXT, command=self.test_ai_speed)
        self.speed_test_button.pack(side="left")
        ctk.CTkButton(test_row.control, text="Copy results", width=110, height=32,
                      corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
                      hover_color=CARD_HOVER, border_width=1, border_color=FIELD_BORDER,
                      text_color=TEXT, command=self.copy_speed_results,
                      ).pack(side="left", padx=(8, 0))
        self.speed_results = ctk.CTkLabel(page, text="", font=self.font_tiny,
                                          text_color=TEXT_MUTED, anchor="w", justify="left")
        self.speed_results.grid(row=next(rows), column=0, sticky="w", padx=18, pady=(0, 6))

        self._caption(page, "Moving pictures (beta, graphics card only)", next(rows))
        moving_row = SettingRow(
            page, self, self.icon("play"), "Make AI pictures move",
            f"{animate.VIDEO_MODEL.name} turns a picture into a short moving clip. A separate "
            f"download ({animate.SIZE / 1e9:.1f} GB)")
        moving_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self.moving_state = ctk.CTkLabel(moving_row.control, text="", font=self.font_body,
                                         text_color=TEXT_MUTED)
        self.moving_state.pack(side="left", padx=(0, 12))
        self.moving_button = ctk.CTkButton(
            moving_row.control, text="", width=96, height=32, corner_radius=4,
            font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER,
            border_width=1, border_color=FIELD_BORDER, text_color=TEXT)
        self.moving_button.pack(side="left")
        which_row = SettingRow(page, self, self.icon("scenes"), "Which pictures move",
                               "Each clip takes minutes, even on a good graphics card")
        which_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        self._segmented(which_row.control, list(animate.CHOICES), self.var_animate, 380)
        self._choice_hint(page, next(rows), self.var_animate, MOVING_HINTS)
        self._moving_row_changed()

        self._caption(page, "Try it", next(rows))
        try_card = Card(page)
        try_card.grid(row=next(rows), column=0, sticky="ew", pady=(PAD // 2, PAD))
        try_card.grid_columnconfigure(0, weight=1)
        self.try_entry = focus_accent(ctk.CTkEntry(
            try_card, height=34, corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
            border_color=FIELD_BORDER, text_color=TEXT,
            placeholder_text="Describe a picture, for example: a robot holding a clipboard"))
        self.try_entry.grid(row=0, column=0, sticky="ew", padx=(16, 8), pady=(14, 6))
        self.try_entry.bind("<Return>", lambda _event: self.try_picture())
        self.try_button = ctk.CTkButton(
            try_card, text="Make picture", width=120, height=34, corner_radius=4,
            font=self.font_body, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color=ACCENT_TEXT, command=self.try_picture)
        self.try_button.grid(row=0, column=1, padx=(0, 16), pady=(14, 6))
        # Shown once a picture is made.
        self.try_save = ctk.CTkButton(
            try_card, text="Save picture", width=120, height=34, corner_radius=4,
            font=self.font_body, fg_color=FIELD_BG, hover_color=CARD_HOVER, border_width=1,
            border_color=FIELD_BORDER, text_color=TEXT, command=self.save_try_picture)
        self.try_save.grid(row=0, column=2, padx=(0, 16), pady=(14, 6))
        self.try_save.grid_remove()
        self.try_status = ctk.CTkLabel(try_card, text="", font=self.font_tiny,
                                       text_color=TEXT_DIM, anchor="w")
        self.try_status.grid(row=1, column=0, columnspan=3, sticky="w", padx=16)
        self.try_picture_label = ctk.CTkLabel(try_card, text="", width=10, height=10)
        self.try_picture_label.grid(row=2, column=0, columnspan=3, pady=(6, 14))

        threads_row = SettingRow(
            page, self, self.icon("timer"), "AI picture threads",
            "How many processor threads make an AI picture when there is no graphics card. "
            "Auto uses the result of Test speed above")
        threads_row.grid(row=next(rows), column=0, sticky="ew", pady=PAD // 2)
        ctk.CTkOptionMenu(
            threads_row.control, values=list(AI_THREAD_OPTIONS), variable=self.var_ai_threads,
            width=110, height=32, corner_radius=4, font=self.font_body, fg_color=FIELD_BG,
            button_color=FIELD_BG, button_hover_color=CARD_HOVER, text_color=TEXT,
            dropdown_fg_color=CARD_BG, dropdown_text_color=TEXT, dropdown_hover_color=CARD_HOVER,
            dropdown_font=self.font_body, dynamic_resizing=False,
            command=lambda _value: save_settings(self._collect_settings()),
        ).pack(side="left")

        self._images_row_changed()
        return page

    # -- moving pictures -------------------------------------------------------
    def _moving_row_changed(self, payload=None):
        if payload and payload.get("event") == "progress":
            self.moving_state.configure(text=f"Downloading: {payload['done'] / 1e9:.1f} of "
                                             f"{payload['total'] / 1e9:.1f} GB")
            self.moving_button.configure(text="Cancel", command=self._moving_stop.set)
            return
        if payload and payload.get("event") == "failed":
            self.append_log(f"⚠️ {payload['message']}")
        if animate.installed():
            self.moving_state.configure(text="Downloaded", text_color=OK_COLOR)
            self.moving_button.configure(text="Remove", command=self.remove_moving)
        else:
            self.moving_state.configure(
                text="Needs AI pictures first" if not imagegen.installed() else
                f"Not downloaded ({animate.SIZE / 1e9:.1f} GB)", text_color=TEXT_MUTED)
            self.moving_button.configure(text="Download", command=self.download_moving,
                                         state="normal" if imagegen.installed() else "disabled")

    def download_moving(self):
        self._moving_stop = stop = threading.Event()

        def worker():
            def progress(done, total):
                self._queue.put(("moving", {"event": "progress", "done": done, "total": total}))
            try:
                animate.download(progress, stop.is_set)
                self._queue.put(("moving", {"event": "done"}))
            except Exception as exc:  # noqa: BLE001 - shown in the Log
                self._queue.put(("moving", {"event": "failed", "message": str(exc)}))

        self._moving_row_changed({"event": "progress", "done": 0, "total": animate.SIZE})
        threading.Thread(target=worker, daemon=True).start()

    def remove_moving(self):
        animate.remove()
        self.var_animate.set(animate.CHOICES[0])
        self._moving_row_changed()

    def _choice_hint(self, page, row, variable, hints):
        """A line under a row of choices that explains the one that is picked."""
        label = ctk.CTkLabel(page, text=hints.get(variable.get(), ""), font=self.font_tiny,
                             text_color=TEXT_MUTED, anchor="w", justify="left", wraplength=860)
        label.grid(row=row, column=0, sticky="w", padx=18, pady=(0, 6))
        variable.trace_add(
            "write", lambda *_args: label.configure(text=hints.get(variable.get(), "")))

    def default_ai_speed(self) -> str:
        """Fast where pictures are made without a dedicated graphics card, else Balanced."""
        return imagegen.FAST if self.ai_slow() else imagegen.BALANCED

    def ai_backend_now(self) -> str:
        """Where the next picture is made: the user's choice, or in Auto what was learnt."""
        chosen = AI_DEVICES.get(self.var_ai_device.get(), "")
        return chosen or self.ai_backend

    def ai_slow(self) -> bool:
        """Whether AI pictures take minutes here: no usable graphics card, or measured so."""
        if self.ai_backend_now() == imagegen.CPU or (
                self.ai_backend_now() != imagegen.GPU and self.ai_gpu in ("integrated", "none")):
            return True
        return self.ai_fast_seconds > SLOW_FAST_SECONDS

    def _ai_device_picked(self, *_args):
        save_settings(self._collect_settings())
        if not self.ai_speed_chosen:
            self.var_ai_speed.set(self.default_ai_speed())
            self.ai_speed_chosen = False
        if hasattr(self, "images_state"):
            self._images_row_changed()

    def _ai_speed_picked(self, *_args):
        self.ai_speed_chosen = True

    def ai_speed_now(self) -> str:
        """The speed a picture is made at now: the processor never goes above Fast."""
        speed = self.var_ai_speed.get()
        return imagegen.FAST if self.ai_backend_now() == imagegen.CPU else speed

    def seconds_per_picture(self, speed=None) -> float:
        """Measured where possible, else a guess from what was measured on similar PCs."""
        fast = self.ai_fast_seconds or (270.0 if self.ai_slow() else 8.0)
        return fast * imagegen.cost(speed or self.ai_speed_now())

    def threads_pair(self) -> tuple[int, int]:
        """(threads, affinity) for processor pictures, from Settings or the speed test."""
        chosen = self.var_ai_threads.get()
        if chosen != "Auto":
            return int(chosen), 0
        try:
            threads, affinity = (int(part) for part in self.ai_threads_best.split(":"))
            return threads, affinity
        except ValueError:
            return imagegen.auto_threads(imagegen.cpu_cores())

    def note_picture_time(self, seconds, speed):
        """A picture just finished: keep its time, as if it were Fast, for the estimates."""
        self.ai_fast_seconds = round(seconds / imagegen.cost(speed), 1)
        if not self.ai_speed_chosen:
            self.var_ai_speed.set(self.default_ai_speed())
            self.ai_speed_chosen = False
        save_settings(self._collect_settings())

    def detect_graphics(self):
        """Once, in the background: does the picture program see a dedicated graphics card?"""
        if self.ai_gpu or not imagegen.installed():
            return

        def worker():
            self._queue.put(("ai_gpu", {"found": imagegen.graphics()}))

        threading.Thread(target=worker, daemon=True).start()

    def look_for_graphics(self):
        """Once per PC, in the background: graphics the Smart writer can use (a card, or
        the Intel or AMD chip in the processor) switch on "Use the graphics card" and Check
        stock clips; none switches the check off, as on the processor it takes minutes."""
        if self.gpu_looked or writer.runtime_path() is None:
            return

        def worker():
            found = writer.has_graphics()
            self._queue.put(("call", {"fn": lambda: self._graphics_looked(found)}))

        threading.Thread(target=worker, daemon=True).start()

    def _graphics_looked(self, found):
        if found is None:
            return   # couldn't ask: again at the next start
        # ponytail: once per PC, so it can switch back on a "Use the graphics card" someone
        # had turned off before this version; the writer falls back to the processor anyway.
        self.gpu_looked = True
        self.var_writer_gpu.set(found)
        self.var_check_clips.set(found)
        save_settings(self._collect_settings())

    def _graphics_found(self, found):
        if not found:
            return
        self.ai_gpu = found
        if not self.ai_speed_chosen:
            self.var_ai_speed.set(self.default_ai_speed())
            self.ai_speed_chosen = False
        save_settings(self._collect_settings())

    def pictures_to_make(self) -> int:
        """How many AI pictures the script in the editor still needs made."""
        try:
            parsed = parse_script(self.script_box.get("1.0", "end"))
        except ScriptError:
            return 0
        if not imagegen.installed():
            return 0
        keyless = not self._has_footage_key()
        ready = set(self.board_choices(self.script_box.get("1.0", "end").strip())[1])
        return sum(1 for i, scene in enumerate(parsed)
                   if i not in ready and (scene.is_ai or (keyless and not scene.is_local)))

    def _slow_pictures_need_a_yes(self) -> bool:
        """On a slow PC, before a long render: say how long, and offer quicker ways."""
        count = self.pictures_to_make()
        script = self.script_box.get("1.0", "end").strip()
        if not count or self._slow_ok == script or not self.ai_slow():
            return False
        if self.seconds_per_picture() * count < 180:
            return False
        SlowPicturesDialog(self, count)
        return True

    def start_after_slow_choice(self, overrides):
        """The slow-PC window's answer: go ahead with these settings, for this video only."""
        self._slow_ok = self.script_box.get("1.0", "end").strip()
        self._render_overrides = dict(overrides)
        self.start_render()

    def ai_mode(self) -> str:
        """Which scenes New from text gives an AI picture: none unless AI images are here."""
        if not imagegen.installed():
            return draft.AI_NONE
        if not self._has_footage_key():
            return draft.AI_ALL   # nothing else to show a scene with
        return {AI_IMAGES_FOR[0]: draft.AI_UNFILMABLE,
                AI_IMAGES_FOR[1]: draft.AI_ALL}.get(self.var_ai_for.get(), draft.AI_NONE)

    def test_ai_speed(self):
        """Time a tiny picture a few ways on the processor; Auto then uses the fastest."""
        if self._making_picture or self.is_rendering or not imagegen.installed():
            return
        self._making_picture = True
        self.speed_test_button.configure(state="disabled", text="Testing...")
        self.speed_results.configure(text="Testing: each try makes one tiny picture...")
        choices = imagegen.thread_choices(imagegen.cpu_cores())

        def worker():
            try:
                found = imagegen.benchmark(choices, os.path.join(PREVIEW_DIR, "speed"),
                                           log=self.ui.log)
                self._queue.put(("images", {"event": "bench", "results": found}))
            except Exception as exc:  # noqa: BLE001 - shown in the tab
                self._queue.put(("images", {"event": "bench", "results": [], "error": str(exc)}))

        threading.Thread(target=worker, daemon=True).start()

    def _speed_tested(self, payload):
        self._making_picture = False
        self.speed_test_button.configure(state="normal", text="Test speed")
        timed = [r for r in payload["results"] if r[3] is not None]
        if not timed:
            self.speed_results.configure(text=payload.get("error") or "The test failed; see the Log.",
                                         text_color=ERR_COLOR)
            return
        best = min(timed, key=lambda r: r[3])
        self.ai_threads_best = f"{best[1]}:{best[2]}"
        cores = imagegen.cpu_cores()
        lines = [f"{label}: {seconds:.0f} s" if seconds is not None else f"{label}: failed"
                 for label, _t, _a, seconds in payload["results"]]
        self.speed_report = (f"AI Video Studio {APP_VERSION} speed test · "
                             f"{len(cores)} cores, kinds {sorted({e for e, _m, _s in cores})} · "
                             f"{os.cpu_count()} logical\n" + "\n".join(lines)
                             + f"\nFastest: {best[0]}")
        self.speed_results.configure(text="\n".join(lines) + f"\nAuto now uses: {best[0]}",
                                     text_color=TEXT_MUTED)
        save_settings(self._collect_settings())

    def copy_speed_results(self):
        report = getattr(self, "speed_report", "")
        if report:
            self.clipboard_clear()
            self.clipboard_append(report)
            self.speed_results.configure(text=self.speed_results.cget("text") + "\nCopied.")

    def _images_row_changed(self, payload=None):
        """The tab's first row: what state AI images are in, and what the button does."""
        self._images_confirm = False
        gigabytes = sum(m.size for m in imagegen.needed_files()) / 1e9
        if payload and payload["event"] == "progress":
            self.images_state.configure(
                text=f"Downloading {payload['done'] / 1e9:.2f} of {payload['total'] / 1e9:.2f} GB",
                text_color=TEXT_MUTED)
            self.images_button.configure(text="Cancel", command=self._images_stop.set)
            return
        if payload and payload["event"] == "failed":
            self.images_state.configure(text=ellipsize(payload["message"], 60),
                                        text_color=ERR_COLOR)
        elif imagegen.installed():
            slow = " · using the processor (slower)" if self.ai_backend_now() == imagegen.CPU \
                else ""
            self.images_state.configure(text=f"Installed · {imagegen.SIZE / 1e9:.1f} GB{slow}",
                                        text_color=OK_COLOR)
        else:
            self.images_state.configure(text=f"Not downloaded · {gigabytes:.1f} GB",
                                        text_color=TEXT_MUTED)
        if imagegen.installed():
            self.images_button.configure(text="Remove", command=self._confirm_remove_images)
        else:
            self.images_button.configure(text="Download", command=self.start_images_download)
        self.try_button.configure(
            state="normal" if imagegen.installed() and not self._making_picture else "disabled")
        if imagegen.installed() and self.ai_backend == imagegen.CPU and \
                self.var_ai_device.get() == "Auto":
            self.images_retry.pack(side="left", padx=(8, 0))
        else:
            self.images_retry.pack_forget()
        self._sync_look_bar()
        if not imagegen.installed():
            self.try_status.configure(text="Download AI pictures first.", text_color=TEXT_DIM)

    def remember_ai_backend(self, backend):
        """Keep what AI pictures need on this PC: "cpu" after the graphics card failed."""
        if backend == self.ai_backend:
            return
        self.ai_backend = backend
        save_settings(self._collect_settings())
        self.append_log(
            "🎨 The graphics card could not make an AI picture, so the processor is used from "
            "now on. Settings > AI pictures can switch back." if backend == imagegen.CPU
            else "🎨 AI pictures will try the graphics card again.")
        if hasattr(self, "images_state"):
            self._images_row_changed()

    def _confirm_remove_images(self):
        """Remove deletes 4.2 GB, so it takes a second press."""
        if not self._images_confirm:
            self._images_confirm = True
            self.images_state.configure(text="Press again to remove AI pictures",
                                        text_color=ERR_COLOR)
            return
        imagegen.remove()
        self.append_log("🗑️ AI pictures removed.")
        self._images_row_changed()
        self.refresh_estimate()

    def start_images_download(self):
        """Fetch AI images on a worker thread; the tab's row shows how it goes."""
        if self.images_downloading:
            return
        self.images_downloading = True
        self._images_stop = stop = threading.Event()
        total = sum(m.size for m in imagegen.needed_files())
        self.append_log(f"⬇️ Downloading AI pictures ({total / 1e9:.1f} GB)...")

        def worker():
            last = 0.0

            def progress(done, total):
                nonlocal last
                now = time.monotonic()
                if now - last >= 0.25 or done >= total:
                    last = now
                    self._queue.put(("images", {"event": "progress", "done": done,
                                                "total": total}))

            try:
                imagegen.download(progress, stop.is_set)
                self._queue.put(("images", {"event": "done"}))
            except imagegen.Stopped:
                self._queue.put(("images", {"event": "stopped"}))
            except Exception as exc:  # noqa: BLE001 - a thread must not die silently
                self._queue.put(("images", {"event": "failed", "message": str(exc)}))

        threading.Thread(target=worker, daemon=True).start()
        self._images_row_changed({"event": "progress", "done": 0, "total": total})

    def try_picture(self):
        """Make one picture from the Try it box, without blocking the window."""
        prompt = self.try_entry.get().strip()
        if self._making_picture or not prompt or not imagegen.installed():
            return
        if self.is_rendering:
            self.try_status.configure(text="Wait for the video to finish first.",
                                      text_color=WARN_COLOR)
            return
        self._making_picture = True
        self.try_button.configure(state="disabled", text="Making...")
        self.try_status.configure(text="Making the picture. The first one takes longest.",
                                  text_color=TEXT_DIM)
        size, look = imagegen.size_for(self.var_aspect.get()), self.var_ai_look.get()
        backend, speed, threads = self.ai_backend_now(), self.ai_speed_now(), self.threads_pair()

        def worker():
            try:
                os.makedirs(PREVIEW_DIR, exist_ok=True)
                path = os.path.join(PREVIEW_DIR, "ai_try.png")
                made = imagegen.make(prompt, size, path, look, backend=backend,
                                     log=self.ui.log, speed=speed, threads=threads)
                self.ui.ai_speed(made.seconds, speed)
                self._queue.put(("images", {"event": "made", "path": path,
                                            "seconds": made.seconds, "size": made.size,
                                            "backend": made.backend}))
            except Exception as exc:  # noqa: BLE001 - shown in the tab
                self._queue.put(("images", {"event": "not_made", "message": str(exc)}))

        threading.Thread(target=worker, daemon=True).start()

    def save_try_picture(self, ask=None):
        """Save the Try it picture where the user picks. A folder that refuses files (a school
        PC's protected Pictures) falls back to a safe folder, and the status line says where."""
        name = re.sub(r'[\\/:*?"<>|]+', "", self.try_entry.get()).strip()[:60] or "AI picture"
        dest = (ask or filedialog.asksaveasfilename)(
            parent=self, title="Save picture", defaultextension=".png",
            filetypes=[("PNG picture", "*.png")], initialfile=f"{name}.png",
            initialdir=paths.known_folder(paths.CSIDL_MYPICTURES, "Pictures"))
        if not dest:
            return
        try:
            dest, moved = paths.usable_output(dest, paths.safe_folders())
            shutil.copyfile(os.path.join(PREVIEW_DIR, "ai_try.png"), dest)
        except (OSError, paths.SaveError) as exc:
            self.try_status.configure(text=f"Could not save the picture: {exc}",
                                      text_color=ERR_COLOR)
            return
        where = "That folder is protected, so it was saved to " if moved else "Saved to "
        self.try_status.configure(text=where + dest, text_color=OK_COLOR)

    def _images_event(self, payload):
        """UI thread: news from the AI images download or the Try it box."""
        event = payload["event"]
        if event == "bench":
            self._speed_tested(payload)
            return
        if event in ("made", "not_made"):
            self._making_picture = False
            self.try_button.configure(state="normal", text="Make picture")
            if event == "not_made":
                self.try_status.configure(text=payload["message"], text_color=ERR_COLOR)
                return
            from PIL import Image

            width, height = payload["size"]
            shown = (round(360 * width / height), 360) if height >= width else (480, 270)
            with Image.open(payload["path"]) as opened:
                picture = opened.convert("RGB")
            self._try_image = ctk.CTkImage(light_image=picture, dark_image=picture, size=shown)
            self.try_picture_label.configure(image=self._try_image)
            self.try_save.grid()
            where = "the processor" if payload["backend"] == imagegen.CPU else "the graphics card"
            self.try_status.configure(
                text=f"Made in {payload['seconds']:.0f} s on {where}.", text_color=OK_COLOR)
            if payload["backend"] == imagegen.CPU:
                self.remember_ai_backend(imagegen.CPU)
            return
        if event != "progress":
            self.images_downloading = False
            self.append_log({"done": "✅ AI pictures are ready.",
                             "stopped": "⏸️ AI pictures download paused.",
                             "failed": f"⚠️ {payload.get('message', '')}"}[event])
            self.refresh_estimate()
        self._images_row_changed(payload)

    # -- the offline voice's one-time download -------------------------------
    def voice_missing(self) -> bool:
        """Whether the voice still has to be fetched. Starts fetching it if so."""
        if self.var_voice_online.get() or localvoice.installed():
            return False
        self.start_voice_download()
        self.status_label.configure(text="Downloading the voice - try again when it is ready")
        return True

    def start_voice_download(self):
        """Fetch the voice's two files on a worker thread."""
        if self.voice_downloading or localvoice.installed():
            return
        self.voice_downloading = True
        self.append_log(f"⬇️ Downloading the voice, once ({localvoice.SIZE / 1e6:.0f} MB)...")

        def worker():
            last = 0.0

            def progress(done, total):
                nonlocal last
                now = time.monotonic()
                if now - last >= 0.5 or done >= total:
                    last = now
                    self._queue.put(("voice", {"event": "progress", "done": done, "total": total}))

            try:
                localvoice.download(progress)
                self._queue.put(("voice", {"event": "done"}))
            except Exception as exc:  # noqa: BLE001 - a thread must not die silently
                self._queue.put(("voice", {"event": "failed", "message": str(exc)}))

        threading.Thread(target=worker, daemon=True).start()
        self._voice_row_changed({"event": "progress", "done": 0, "total": localvoice.SIZE})

    def _voice_event(self, payload):
        """UI thread: the voice download's news, to the Log and the Settings row."""
        if payload["event"] == "done":
            self.voice_downloading = False
            self.append_log("✅ The voice is ready.")
        elif payload["event"] == "failed":
            self.voice_downloading = False
            self.append_log(f"⚠️ The voice could not be downloaded: {payload['message']}")
        self._voice_row_changed(payload)

    def _voice_row_changed(self, payload=None):
        if not hasattr(self, "voice_state"):
            return   # Settings has not been built yet
        if self.voice_downloading and payload and payload["event"] == "progress":
            self.voice_state.configure(
                text=f"Downloading {payload['done'] * 100 // max(payload['total'], 1)}%")
            self.voice_button.configure(state="disabled")
        elif localvoice.installed():
            self.voice_state.configure(text="Ready")
            self.voice_button.pack_forget()
        else:
            self.voice_state.configure(text="Not downloaded")
            self.voice_button.configure(state="normal")

    # -- voice preview -------------------------------------------------------
    def preview_voice(self):
        """Speak a short sample in the chosen voice, without blocking the window."""
        if self._previewing or self.voice_missing():
            return
        persona = voices.persona(self.var_voice.get())
        self._previewing = True
        self.preview_button.configure(state="disabled", text="Generating...")
        threading.Thread(target=self._preview_worker,
                         args=(persona.id, bool(self.var_voice_online.get()),
                               self.var_voice_speed.get()),
                         daemon=True).start()

    def _preview_worker(self, voice_id, online, speed=None):
        """Worker thread: the full voice chain once, cached per voice, engine and version."""
        try:
            os.makedirs(PREVIEW_DIR, exist_ok=True)
            engine = "online" if online else "offline"
            stem = os.path.join(PREVIEW_DIR, re.sub(r"[^\w-]", "_",
                                                    f"{voice_id}_{speed}_{engine}_{APP_VERSION}"))
            playable = stem + ".play.wav"
            if not os.path.exists(playable):
                made = generate_voiceover(PREVIEW_TEXT, stem + ".mp3", self.ui.log, voice_id,
                                          online, speed)
                # winsound plays WAV only; classic voices arrive as MP3.
                from vidgen import mastering
                # Convert beside it, then rename: a failed run must not leave a
                # broken file that every later preview would reuse.
                partial = stem + ".tmp.wav"
                mastering.run_ffmpeg(["-y", "-i", made, "-ar", "44100", "-ac", "1",
                                      "-c:a", "pcm_s16le", partial])
                os.replace(partial, playable)
            self._queue.put(("preview", {"path": playable, "error": None}))
        except Exception as exc:  # noqa: BLE001 - reported in the window
            self._queue.put(("preview", {"path": None, "error": str(exc)}))

    def _preview_done(self, path, error):
        self._previewing = False
        self.preview_button.configure(state="normal",
                                      text=f"{self.icon('play')}  Preview Voice")
        if error:
            self.append_log(f"⚠️ Voice preview failed: {error}")
            self.status_label.configure(text="Voice preview failed - see the Log")
            return
        # The status line mirrors the latest log line, which is the preview's
        # "Generating..." - don't leave that showing once it's done.
        if not self.is_rendering:
            short = voices.persona(self.var_voice.get()).short
            self.status_label.configure(text=f"Previewing {short}")
        if sys.platform == "win32":
            import winsound

            # SND_ASYNC returns at once; the sound plays on its own.
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)

    def my_voice_made(self):
        """My voice was just made: list it in every voice menu and choose it."""
        values = voices.labels()
        for menu in self.voice_menus:
            menu.configure(values=values)
        self.var_voice.set(voices.persona(voices.MY_VOICE).label)

    # -- title, description and tags for posting ----------------------------
    def make_upload_text(self, videos):
        """After a render: the Smart writer writes them (or the first line does)."""
        script, pid = self.script_box.get("1.0", "end").strip(), self.project
        smart = self.writer_mode == "Smart writer" and writer.ready()[0]
        gpu = bool(self.var_writer_gpu.get())

        def worker():
            upload = None
            if smart:
                try:
                    with writer.Session(gpu=gpu) as session:
                        upload = session.upload_text(script)
                except Exception:  # noqa: BLE001 - the plain version is still useful
                    upload = None
            try:
                upload = upload or writer.upload_fallback(script)
            except ScriptError:
                return
            for video in videos[:1]:   # a long video's chapters (render._write_chapters)
                try:
                    with open(os.path.splitext(video)[0] + ".chapters.txt",
                              encoding="utf-8") as fh:
                        upload = writer.with_chapters(upload, fh.read().split("\n")[:-1])
                except OSError:
                    pass
            self._queue.put(("upload", {"upload": upload, "videos": videos, "project": pid}))

        threading.Thread(target=worker, daemon=True).start()

    def _upload_done(self, payload):
        upload = payload["upload"]
        self.last_upload = upload
        for video in payload["videos"]:
            try:
                with open(os.path.splitext(video)[0] + ".upload.txt", "w",
                          encoding="utf-8") as fh:
                    fh.write(writer.upload_file_text(upload))
            except OSError:
                pass
        data = projects.load(payload["project"]) if payload["project"] else None
        if data is not None:
            projects.save(payload["project"], dict(data, upload=upload), touch=False)
        for window in self.winfo_children():
            if isinstance(window, ResultDialog):
                try:
                    window.set_upload(upload)
                except tk.TclError:
                    pass   # closed before the text arrived; it is saved beside the video

    # -- make several -------------------------------------------------------
    def start_batch(self, texts):
        """Write and make each text in turn; the Log says how far it is."""
        if self.is_rendering or self._batch:
            return
        if not self._has_footage_key() and not imagegen.installed():
            WelcomeDialog(self)
            return
        template = draft.TEMPLATES.get(self.var_template.get())
        if template:
            self.var_auto_cards.set(template.cards)
            self.caption_vars["caption_style"].set(template.caption_style)
            audience, length = template.audience, template.length
        else:
            audience = draft.Audience(self.audience["audience_content"],
                                      self.audience["audience_age"],
                                      self.audience["audience_platform"])
            length = audience.length
        self._batch = {"texts": texts, "at": 0, "made": [], "failed": [],
                       "audience": audience, "length": length,
                       "folder": os.path.dirname(os.path.abspath(
                           self.var_output.get() or default_output_dir()))}
        self.append_log(f"📚 Making {len(texts)} videos, one after another.")
        self.select_page("log")
        self._batch_next()

    def _batch_next(self):
        b = self._batch
        if b is None:
            return
        if self.is_rendering:   # the last render is still clearing up
            self.after(300, self._batch_next)
            return
        if b["at"] >= len(b["texts"]):
            self._batch = None
            failed = "".join(f"\nVideo {n}: {why}" for n, why in b["failed"])
            self.append_log(f"📚 Done: {len(b['made'])} of {len(b['texts'])} videos made.")
            Dialog(self, "Videos made", f"{len(b['made'])} of {len(b['texts'])} videos were "
                   f"made. They are in My videos and in {b['folder']}." + failed,
                   ok=not b["failed"])
            if self.current_page == "projects":
                self.refresh_projects()
            return
        self.append_log(f"📝 Video {b['at'] + 1} of {len(b['texts'])}: writing the script...")
        args = (b["texts"][b["at"]], b["audience"], b["length"], self.settings["padding"],
                bool(self.var_auto_cards.get()), self.ai_mode(), bool(self.var_writer_gpu.get()))
        threading.Thread(target=self._batch_write, args=args, daemon=True).start()

    def _batch_write(self, text, audience, length, padding, cards, ai, gpu):
        """Worker: one script, by the Smart writer if it is there, else Quick split."""
        try:
            if self.writer_mode == "Smart writer" and writer.ready()[0]:
                with writer.Session(gpu=gpu) as session:
                    result = session.write(text, audience, length, padding, cards, ai,
                                           bool(self.var_web_facts.get()))
                    try:   # while the model is loaded anyway
                        upload = session.upload_text(result.script)
                    except writer.WriterError:
                        upload = writer.upload_fallback(result.script)
            else:
                result = draft.write(text, audience, length, padding=padding, cards=cards, ai=ai)
                upload = writer.upload_fallback(result.script)
            self._queue.put(("batch", {"result": result, "upload": upload}))
        except Exception as exc:  # noqa: BLE001 - this video is skipped, the rest go on
            self._queue.put(("batch", {"error": str(exc) or type(exc).__name__}))

    def _batch_event(self, payload):
        b = self._batch
        if b is None:
            return
        if payload.get("error"):
            b["failed"].append((b["at"] + 1, payload["error"]))
            b["at"] += 1
            self._batch_next()
            return
        result = payload["result"]
        b["upload"] = payload.get("upload")
        self.project = None   # each video is its own project
        self.thumb = {"scene": None, "title": ""}
        audience = b["audience"]   # as use_draft keeps it: the music's mood follows it
        self.audience = {"audience_content": audience.content, "audience_age": audience.age,
                         "audience_platform": audience.platform}
        self.script_box.delete("1.0", "end")
        self.script_box.insert("1.0", result.script)
        self.var_voice.set(voices.persona(result.voice).label)
        self.var_aspect.set(result.aspect)
        self.var_target.set(result.target)
        name = re.sub(r'[<>:"/\\|?*]+', "", projects.default_title(result.script)).strip(" .")
        self.var_output.set(os.path.join(b["folder"], (name or "video")[:60] + ".mp4"))
        self._commit_script()
        self._slow_ok = result.script.strip()   # nobody is there to answer the slow-PC window
        if self.ai_slow():
            self._render_overrides = {"ai_reuse": SLOW_AI_SCENES}
        self.append_log(f"🎬 Video {b['at'] + 1} of {len(b['texts'])}: making it...")
        self.start_render()
        if not self.is_rendering:
            b["failed"].append((b["at"] + 1, "the video could not be started (see the Log)"))
            b["at"] += 1
            self.after(300, self._batch_next)

    # -- both shapes ---------------------------------------------------------
    def make_both_shapes(self):
        """This shape, then the other one, saved side by side in the same project."""
        if self.is_rendering:
            return
        shape = self.var_aspect.get()
        self._both = {"shape": shape, "other": "16:9" if shape == "9:16" else "9:16",
                      "output": self.var_output.get()}
        self.start_render()
        if not self.is_rendering:   # it asked something first, or was cancelled
            self._both = None
        else:
            self._both["output"] = self.var_output.get()   # where the Save window put it

    def _second_shape(self):
        if self.is_rendering:   # the first render is still clearing up its temp files
            self.after(300, self._second_shape)
            return
        both = self._both
        base, ext = os.path.splitext(both["first"][0])
        self.var_aspect.set(both["other"])
        self.var_output.set(f"{base} ({both['other'].replace(':', 'x')}){ext or '.mp4'}")
        self.append_log(f"🎬 Now the {both['other']} version.")
        self.start_render()
        if not self.is_rendering:
            self._shape_back(both)
            self._both = None

    def _shape_back(self, both):
        self.var_aspect.set(both["shape"])
        self.var_output.set(both["output"])

    # -- render --------------------------------------------------------------
    def start_render(self):
        if self.is_rendering:
            return
        # Plain text in the script box: it wants New from text, not an error on line 1.
        pasted = self.script_box.get("1.0", "end").strip()
        if looks_like_plain_text(pasted):
            DraftDialog(self).prefill(pasted)
            return
        # No key yet: walk the user through getting one instead of failing. With AI images
        # downloaded there is nothing to walk through: every scene gets a picture.
        if not self._has_footage_key() and self._needs_stock_footage() \
                and not imagegen.installed():
            WelcomeDialog(self)
            return
        if self._slow_pictures_need_a_yes():
            return
        if self.voice_missing():
            self.select_page("log")
            return

        # Like a browser download: ask where this one goes, unless told not to.
        overwrite = False
        if self.var_ask_save.get() and not self._batch \
                and not (self._both and self._both.get("first")):
            chosen = self._ask_save_path()
            if not chosen:
                return   # cancelled: no render
            self.var_output.set(chosen)
            # Windows has already asked before replacing an existing file.
            overwrite = True

        self.project = self.project or projects.new_id()
        try:
            has_ai = any(scene.is_ai for scene in parse_script(pasted))
        except ScriptError:
            has_ai = False
        self._render_info = {"at": time.time(), "ai": has_ai, "tip": self._tip_pending}
        self._tip_pending = False
        # Snapshot every widget value here, on the UI thread.
        settings = self._collect_settings()
        self.settings = settings
        save_settings(settings)
        self._cancel = threading.Event()
        # What was chosen in the Storyboard holds only for the script it shows.
        choices, pictures = self.board_choices(settings["script"])
        cfg = dict(settings, cache_dir=SEARCH_CACHE_DIR, cache_root=CACHE_ROOT,
                   app_version=APP_VERSION, overwrite=overwrite,
                   cancel=self._cancel, clip_choices=choices, ai_pictures=pictures,
                   ai_images_speed=self.ai_speed_now(), ai_threads=self.threads_pair(),
                   ai_images_backend=self.ai_backend_now(),
                   project_dir=projects.folder(self.project),
                   ask_clips=None if self._batch else self._ask_clips,
                   target_length=pacing.TARGETS.get(settings["target_length"]),
                   thumbnail_scene=self._thumb_scene(), thumbnail_title=self.thumb_title())

        if cfg["music_path"] == AUTO_TRACK:
            cfg["music_path"] = draft.pick_track(bundled_tracks(), self.audience[
                "audience_content"], settings["script"]) or ""
        cfg.update(self._render_overrides)   # "make 2 and reuse", "Fast": this video only
        self._render_overrides = {}
        self._slow_ok = None
        self._set_busy(True)
        if choices:
            self.append_log("🎬 Using the clips chosen in the Storyboard.")
        for bar in self._progress_bars():
            bar.configure(mode="determinate")
            bar.set(0)
        self.select_page("log")
        threading.Thread(target=render_worker, args=(cfg, self.ui), daemon=True).start()

    def _ask_clips(self, items):
        """On the render thread: ClipQuestion on the window's thread, waited for. Returns
        {scene index: "use" | "next"}, or None when the video is cancelled."""
        done, got = threading.Event(), {}
        cancel = self._cancel

        def answer(result):
            got["result"] = result
            done.set()

        self._queue.put(("call", {"fn": lambda: ClipQuestion(self, items, answer, cancel)}))
        while not done.wait(0.2):
            if cancel.is_set():
                return None
        return got["result"]

    # -- cache -----------------------------------------------------------------
    def _show_cache_size(self):
        size = cache.size(CACHE_ROOT)
        self.cache_label.configure(text=f"{size / 1e6:.0f} MB kept" if size else "Empty")

    def clear_cache(self):
        if self.is_rendering:
            self.cache_label.configure(text="Wait for the render to finish first.")
            return
        freed = cache.clear(CACHE_ROOT)
        self.append_log(f"🧹 Cache cleared: {freed / 1e6:.0f} MB freed.")
        self._show_cache_size()

    # -- shutdown ------------------------------------------------------------
    def _on_close(self):
        self.save_project()
        save_settings(self._collect_settings())
        writer.close_all()   # a title or translation still being written: its model stops too
        self.destroy()


def main():
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                APP_MODEL_ID
            )
        except Exception:
            pass
    if "--tiptest" in sys.argv:   # developer tool: every place on the map of Windows, in the app
        from vidgen import tiptest

        tiptest.main(sys.argv[1:], sys.modules[__name__])
        return
    if "--bench" in sys.argv:   # developer tool: timed sample renders, report on the Desktop
        from vidgen import bench

        report = os.path.join(paths.known_folder(paths.CSIDL_DESKTOP, "Desktop"),
                              f"AIVideoStudio-bench-{time.strftime('%Y%m%d-%H%M')}.txt")
        bench.main(sys.argv[1:], APP_VERSION, load_settings(), report)
        os.startfile(report)
        return
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
