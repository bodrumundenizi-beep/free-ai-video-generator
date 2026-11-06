"""Show it on my PC: the tip picker, the guide box, the tip window and its session. (Split out of ai_video_studio.py; that file still exports every name.)"""

from __future__ import annotations

from gui.base import *  # noqa: F401,F403  (the names these had in one file)

# ---- moved from ai_video_studio.py, unchanged below ----


class TipPicker(ctk.CTkToplevel):
    """The ready-made tips (tiplist: one or more for every page of the map), with a search box.
    A click puts the tip in the dialog's box; Record then plans it like a typed one."""

    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        app = dialog.app
        self.title("Ready-made tips")
        self.geometry("460x560")
        self.configure(fg_color=MAIN_BG)
        self.transient(dialog)
        self.find = ctk.CTkEntry(self, placeholder_text="Search, for example: night",
                                 font=app.font_body)
        self.find.pack(fill="x", padx=14, pady=(14, 8))
        self.find.bind("<KeyRelease>", lambda _e: self.show())
        self.list = ctk.CTkScrollableFrame(self, fg_color=CARD_BG)
        self.list.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.rows = []   # (heading or None, widget, tip text)
        for name, texts in tiplist.groups():
            heading = ctk.CTkLabel(self.list, text=name, font=app.font_body, text_color=TEXT,
                                   anchor="w")
            self.rows.append((name, heading, ""))
            for text in texts:
                button = ctk.CTkButton(self.list, text=text, anchor="w", height=26,
                                       font=app.font_tiny, fg_color="transparent",
                                       hover_color=CARD_HOVER, text_color=TEXT_MUTED,
                                       command=lambda t=text: self.choose(t))
                self.rows.append((None, button, text))
        self.show()
        self.protocol("WM_DELETE_WINDOW", self.close)
        centre_on(self, dialog)
        self.find.focus_set()

    def show(self):
        """Only the tips that have every searched word, under their headings."""
        words = self.find.get().lower().split()
        for _heading, widget, _text in self.rows:
            widget.pack_forget()
        heading = None
        for name, widget, text in self.rows:
            if name is not None:
                heading = widget
                continue
            if all(w in text.lower() for w in words):
                if heading is not None:
                    heading.pack(fill="x", padx=6, pady=(10, 2))
                    heading = None
                widget.pack(fill="x", padx=6)

    def choose(self, text):
        self.dialog.ask.delete(0, "end")
        self.dialog.ask.insert(0, text)
        self.dialog._forget_plan()
        self.close()

    def close(self):
        self.destroy()
        if self.dialog.winfo_exists():
            self.dialog.grab_set()   # the dialog is modal again


class GuideBox(ctk.CTkToplevel):
    """The small box that says what to click while the screen is recorded.

    It sits beside the recorded window and asks Windows to leave it out of recordings, so it
    never shows in the video.
    """

    def __init__(self, app, rect, on_next, on_cancel):
        super().__init__(app)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color=CARD_BG)
        self.text = ctk.CTkLabel(self, text="", font=app.font_body, text_color=TEXT,
                                 wraplength=260, justify="left")
        self.text.pack(fill="x", padx=14, pady=(12, 8))
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(0, 12))
        self.next = ctk.CTkButton(row, text="Next", width=90, height=30, command=on_next,
                                  fg_color=ACCENT)
        self.next.pack(side="left")
        ctk.CTkButton(row, text="Cancel", width=80, height=30, command=on_cancel,
                      fg_color=FIELD_BG, hover_color=CARD_HOVER, text_color=TEXT,
                      ).pack(side="left", padx=(8, 0))
        self.bind("<Escape>", lambda _e: on_cancel())
        self.update_idletasks()
        left, top, width, height = rect
        box_w, box_h = 300, self.winfo_reqheight()
        screen_w = self.winfo_screenwidth()
        # Real pixels: Tk places windows in the same pixels the recording uses.
        x = left + width + 12 if left + width + 12 + box_w <= screen_w else max(left - box_w - 12, 0)
        self.geometry(f"{box_w}x{box_h}+{x}+{max(top, 0)}")
        self.update_idletasks()
        screen.hide_from_capture(ctypes.windll.user32.GetParent(self.winfo_id())
                                 if sys.platform == "win32" else 0)

    def show(self, text, button="Next", waiting=False):
        self.text.configure(text=text)
        # Never greyed out: a switch that is already the right way never changes, and the
        # user must always be able to go on. Waiting only means the step can also end by itself.
        self.next.configure(text=button, state="normal")


class TipDialog(ctk.CTkToplevel):
    """Show it on my PC: record a Windows tip on this PC, then make it a video.

    The setting is read first and put back afterwards, whatever happens; one that was
    already on is left on.
    """

    def __init__(self, app, ask=""):
        super().__init__(app)
        self.app = app
        self.title(f"Show it on my PC · {BUILD}")   # which copy is being tested
        self.resizable(False, False)
        self.configure(fg_color=MAIN_BG)
        self.transient(app)
        card = Card(self)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        ctk.CTkLabel(card, text="Show it on my PC", font=app.font_title, text_color=TEXT,
                     ).pack(anchor="w", padx=18, pady=(16, 4))
        ctk.CTkLabel(card, text="The app finds the right window itself and records it. A small "
                                "box tells you what to click. A simple switch the app can flip "
                                "itself, only after you say yes, and it puts it back right "
                                "after. Anything you change is put back too.",
                     font=app.font_body, text_color=TEXT_MUTED, wraplength=440, justify="left",
                     ).pack(anchor="w", padx=18)
        ctk.CTkLabel(card, text="What tip? For example: how to turn on night light",
                     font=app.font_body, text_color=TEXT, anchor="w",
                     ).pack(anchor="w", padx=18, pady=(12, 2))
        self.ask = ctk.CTkEntry(card, width=440, font=app.font_body)
        self.ask.pack(anchor="w", padx=18)
        if ask:   # a tip idea from Home
            self.ask.insert(0, ask)
        self.ask.bind("<Return>", lambda _e: self.record())
        # A changed tip needs a new plan; Record then plans it by itself.
        self.ask.bind("<KeyRelease>", lambda e: e.keysym != "Return" and self._forget_plan())
        ctk.CTkButton(card, text="...or pick a ready-made tip", width=200, height=28,
                      font=app.font_tiny, fg_color=FIELD_BG, hover_color=CARD_HOVER,
                      text_color=TEXT, command=lambda: TipPicker(self),
                      ).pack(anchor="w", padx=18, pady=(8, 8))
        self.var_hook = tk.BooleanVar(self, True)
        ctk.CTkCheckBox(card, text="Start with a hook (\"Here's how to make your PC faster...\")",
                        variable=self.var_hook, font=app.font_tiny, text_color=TEXT_DIM,
                        checkbox_width=16, checkbox_height=16, border_width=1,
                        ).pack(anchor="w", padx=18, pady=(0, 8))
        self.preview = ctk.CTkLabel(card, text="", font=app.font_body, text_color=TEXT_MUTED,
                                    wraplength=440, justify="left", anchor="w")
        self.preview.pack(anchor="w", padx=18)
        self.planned = None
        self.page_hwnd, self.opened_more, self.page_states = 0, None, {}
        self.checked = False   # the PC can record (preflight), checked on the first Record
        self.status = ctk.CTkLabel(card, text="", font=app.font_tiny, text_color=TEXT_DIM,
                                   wraplength=440, justify="left")
        self.status.pack(anchor="w", padx=18)
        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.pack(fill="x", padx=18, pady=(8, 16))
        self.record_button = ctk.CTkButton(buttons, text="Record", width=120,
                                           command=self.record, fg_color=ACCENT)
        self.record_button.pack(side="right")   # it plans first; no separate Plan it button
        self.session = None
        centre_on(self, app)

    def say(self, text, colour=TEXT_DIM):
        self.status.configure(text=text, text_color=colour)

    def preflight(self):
        """Worker: "" when a recording can be kept and the screen can be recorded, else why."""
        try:
            paths.recordings_dir(RECORDINGS_DIR)   # Documents refused: it falls back (Fix 1)
        except OSError as exc:
            return f"There's no folder this PC lets the app save recordings in ({exc})."
        screens = screen.monitors()
        left, top, right, bottom = screens[0] if screens else (0, 0, 640, 480)
        middle = ((left + right) // 2 - 160, (top + bottom) // 2 - 120, 320, 240)
        why = screen.can_record(middle)
        if why:
            failure = screen.LAST_FAILURE
            self.app.call_soon(lambda: self.app.append_log(
                f"⚠️ Screen recording failed: {failure}"))
        return why

    def after_check(self, why):
        if not self.winfo_exists():
            return
        self.record_button.configure(state="normal")
        if why:
            self.say(why, ERR_COLOR)
            report_tip_failure(self.app, why)
            return
        self.checked = True   # once per window: the PC doesn't change in between
        self.say("")
        self.record()

    def plan(self, then=None):
        """Find the place in the map of Windows and let the Smart writer write the steps.

        ``then`` runs after a plan that worked (Record: plan, then record).
        """
        text = " ".join(self.ask.get().split())
        if not text:
            self.say("Type a tip first.", ERR_COLOR)
            return
        places = winmap.search(text)
        self.say("Finding where that is in Windows...")
        self.record_button.configure(state="disabled")

        def work():
            note, buttons = "", []   # buttons: also when reading the page fails
            try:
                place = places[0] if places else None   # the map decides the page
                if place is not None:
                    tips.fallback(text, places)          # an admin place: refused before asking
                buttons = self.page_buttons(
                    place, tips.match_words(text, place) if place is not None else ())
                if place is not None and not buttons:
                    # Windows hides this page's buttons (Task Manager runs with admin rights):
                    # the Smart writer would only guess their names ("click the File icon").
                    tip = tips.fallback(text, places)
                    note = (" (Plain steps: Windows doesn't show this page's buttons to the app, "
                            "so it doesn't name them.)")
                elif writer.installed():
                    self.app.call_soon(lambda: self.winfo_exists() and self.say(
                        "Planning the steps..."))
                    with writer.Session() as session:
                        tip = tips.from_answer(text, session.tip(text, place, buttons), place)
                else:   # no Smart writer: plain steps, or Windows' search when nothing matched
                    tip = tips.fallback(text, places) if places else tips.from_answer(text, {})
                    if not writer.installed():
                        note = " (Plain steps: the Smart writer isn't downloaded.)"
            except tips.TipRefused as exc:
                message = str(exc)
                self.app.call_soon(lambda: self.show_plan(None, message))
                return
            except Exception as exc:  # noqa: BLE001 - the plain steps still work
                try:
                    tip = tips.fallback(text, places)
                    note = f" (Plain steps: the Smart writer failed: {exc})"
                except tips.TipRefused as refused:
                    message = str(refused)
                    self.app.call_soon(lambda: self.show_plan(None, message))
                    return
            if place is not None:
                note += tips.aim(tip, buttons, text, place,   # the app picks the real button
                                 self.opened_more)
            self.app.call_soon(lambda: self.show_plan(tip, note, then))

        threading.Thread(target=work, daemon=True).start()

    def page_buttons(self, place, words=()):
        """Worker thread: open the page, read its real buttons (kept for next time), or [].

        When none matches the tip's ``words`` but a "Show more..." button is there, the app
        opens it (only navigation) and reads again, so the switch behind it is known while
        planning (``opened_more``). ``page_states``: how its switches are now, for put-back.
        """
        self.opened_more, self.page_states = None, {}
        if place is None:
            return []
        store = cache.Cache(CACHE_ROOT, "winui")
        if drive.route(place) == "taskbar":
            # Task Manager runs with admin rights: it can't be read, nor closed by the app, and
            # left in front it blocks the app's mouse when it finds the page itself.
            return uiread.recall(store, place.id)
        name = place.name
        self.app.call_soon(lambda: self.winfo_exists() and self.say(
            f"Opening {name} to read its buttons. It closes again before the recording."))
        try:
            hwnd = screen.open_window(place.command())
            self.page_hwnd = hwnd   # closed again before the app finds it through the Run box
            controls = drive.settled(hwnd) if hwnd else []
            window = screen.foreground_rect() if hwnd else None
        except OSError:
            controls, window = [], None
        if not controls:
            return uiread.recall(store, place.id)   # read here before, when Windows allowed it
        buttons = uiread.targets(controls, window)
        uiread.remember(store, place.id, buttons)   # the page as it opens: to recognise it
        self.page_states = uiread.states(controls)
        if words and uiread.best_match(buttons, words) is None and drive.find_more(controls):
            try:
                more, after = drive.open_more(hwnd, window)
            except drive.Interrupted:
                more = None
            if more is not None and after:
                self.opened_more = more
                self.page_states = {**uiread.states(after), **self.page_states}
                buttons = uiread.targets(after, window)
        return buttons

    def _forget_plan(self):
        if self.planned is not None:
            self.planned = None
            self.preview.configure(text="")

    def show_plan(self, tip, note, then=None):
        if not self.winfo_exists():
            return
        self.record_button.configure(state="normal")
        if tip is None:
            self.planned = None
            self.preview.configure(text="")
            self.say(note, ERR_COLOR)
            return
        self.planned = tip
        place = winmap.BY_ID.get(tip.key)
        where = "Windows search" if tip.key == "search" else place.name if place else tip.title
        lines = [f"Opens: {where}"] + [f"{i}. {s.guide}" for i, s in enumerate(tip.steps, 1)]
        self.preview.configure(text="\n".join(lines))
        self.say("Planned." + note,
                 WARN_COLOR if note.strip() else TEXT_DIM)
        if then is not None:
            then()

    def record(self):
        if not self.checked:
            # Before anything opens: a PC that can't record used to open the page, close it,
            # open Settings and then fail out of sight (school laptop, 2026-10-28).
            self.say("Checking that this PC can record the screen...")
            self.record_button.configure(state="disabled")

            def work():
                why = self.preflight()
                self.app.call_soon(lambda: self.after_check(why))

            threading.Thread(target=work, daemon=True).start()
            return
        tip = self.planned
        typed = " ".join(self.ask.get().split())
        if tip is None and tips.ready_made(typed):
            tip = tips.ready_made(typed)   # it reads and restores the setting by itself
        if tip is None and typed:
            self.plan(then=self.record)   # plan first, then record
            return
        if tip is None:
            self.say("Type a tip first, or pick a ready-made one.", ERR_COLOR)
            return
        why = tip.available()
        if why:
            self.say(why, ERR_COLOR)
            return
        try:
            state = tip.read()
        except OSError as exc:
            self.say(f"Couldn't read the setting first ({exc}), so nothing was recorded.",
                     ERR_COLOR)
            return

        def go(auto):
            if self.winfo_exists():
                self.session = TipSession(self, tip, state, typed, auto)
                self.session.open()

        if tip.auto is not None:
            FlipDialog(self.app, tip, go)   # the app clicks a switch only after a yes
        else:
            go(False)


class TipSession:
    """One recording: open the window, record the steps, put the setting back, cut the clips.

    The page's real buttons are read again and again while recording (uiread), in the
    background: a switch that only appears after "Show more settings" is still found, zoomed
    onto and watched; flipping a step's switch ends that step by itself; Next always works. The
    account's name, email and picture are hidden. Afterwards the user is walked through
    switching back whatever they changed.

    First the app finds the page itself, slowly, so the video shows the way there (drive):
    Settings' search box, the taskbar's menu or the Run box. It only clicks its way there,
    never a setting; when a person takes over or nothing is found, it opens the page directly.
    Those steps (``nav``) go before the tip's own; the taskbar and Run box are recorded on
    their own (``intro``), since they are outside the page's window.

    Worker threads never touch the window: everything they report goes through app.call_soon.
    """

    def __init__(self, dialog, tip, state, text="", auto=False):
        self.dialog, self.app, self.tip, self.state = dialog, dialog.app, tip, state
        # The switch the app flips itself (the user said yes), and whether it did yet.
        self.auto = tip.auto if auto else None
        self.flipped = self.flipping = False
        hook = getattr(dialog, "var_hook", None)
        self.hook = bool(hook is not None and hook.get())   # start with a hook (hooks.py)
        self.text = text or tip.title
        self.was_on = False if tip.watch_change else tip.is_on(state)
        self.place = winmap.BY_ID.get(tip.key)
        self.route = drive.route(self.place)
        self.nav, self.nav_blurs = [], []   # (voice, control or None) per step found by the app
        self.intro = self.intro_recorder = None   # {"path", "rect", "marks", "length"}
        self.recorder = self.box = None
        self.step = 0
        self.restored = False
        self.phase = "record"
        self.hwnd, self.rect, self.controls, self.before = 0, None, [], {}
        self.clicks, self.pressed, self.found = [], False, {}
        self.marks, self.length, self.path, self.left = [], 0.0, "", {}
        # Documents may refuse it on school PCs (issue #7): the first folder that takes files.
        base, moved = paths.recordings_dir(RECORDINGS_DIR)
        if moved and not getattr(self.app, "_said_recordings", False):
            self.app._said_recordings = True
            self.app.append_log(f"ℹ️ Documents is protected on this PC, so recordings are kept "
                                f"in {base}.")
        self.folder = os.path.join(base, time.strftime("%Y-%m-%d_%H%M%S"))
        atexit.register(self.restore)   # even if the app is closed mid-recording

    def restore(self):
        """Put the setting back exactly as it was read. Safe to call more than once."""
        if self.restored:
            return
        self.restored = True
        try:
            if self.tip.read() != self.state:
                self.tip.restore(self.state)
                self.app.call_soon(lambda: self.app.append_log(
                    "↩️ The setting is back as it was before the recording."))
        except OSError as exc:
            message = (f"⚠️ Couldn't put the setting back ({exc}). Change it back by hand in "
                       "the window the tip opened.")
            self.app.call_soon(lambda: self.app.append_log(message))

    # -- recording -----------------------------------------------------------------------
    def open(self):
        # The dialog is modal (centre_on grabs all clicks): hidden, it kept the grab, and every
        # click on the guide box was thrown away (2026-10-22). Let go of it first.
        self.dialog.grab_release()
        self.dialog.withdraw()
        self.app.iconify()   # out of the way of the window being recorded

        def work():
            try:
                hwnd = 0
                if self.route == "settings":
                    # Opened again, Settings stays on the page planning left it on: closed
                    # first, it opens fresh on its home page (2026-10-26).
                    drive.close(self.dialog.page_hwnd)
                    time.sleep(1.0)
                    hwnd = screen.open_window(["explorer", "ms-settings:"])
                elif self.route in ("run", "taskbar"):
                    hwnd = self.find_outside()
                if not hwnd:
                    self.route = "direct" if self.route else ""
                    hwnd = screen.open_window(self.tip.opens)
            except OSError as exc:
                message = f"Couldn't open the window ({exc})."
                self.app.call_soon(lambda: self.fail(message))
                return
            self.app.call_soon(lambda: self.start(hwnd))

        threading.Thread(target=work, daemon=True).start()

    def stopped(self):
        return self.phase == "done"

    def flip_on(self, index):
        """Worker: the app flips the tip's switch itself (the user said yes). follow() sees the
        change and ends the step. Written down first, so a crash still gets it put back."""
        time.sleep(1.5)   # the viewer sees the page before the click
        if self.step != index or self.phase != "record":
            return
        was = "Off" if self.tip.auto_to == "On" else "On"

        def clicking():
            # Written down only when the app really clicks: a switch it never found (hidden
            # in a closed section) left a false "not put back" (2026-10-27).
            putback.write(SETTINGS_DIR, self.tip.key, self.auto.name, was)
            self.flipped = True

        if not drive.flip(self.hwnd, self.auto.name, self.tip.auto_to,
                          lambda: self.phase != "record", clicking=clicking):
            name = self.auto.name
            self.app.call_soon(lambda: self.app.append_log(
                f"ℹ️ The app couldn't turn {name} itself: click it, then press Next."))

    def auto_step(self, index, step, action):
        """Worker: a step the app does itself. "open" clicks a "Show more..." button, "show"
        points at a switch that is already right, "hold" waits: then on to the next step.
        "point" only shows the user where to click. A person taking over gets the step back."""
        def alive():
            return self.step == index and self.phase == "record"

        def go_on():
            self.app.call_soon(lambda: alive() and self.next())

        time.sleep(HOLD_WAIT if action == "hold" else 1.5)
        if not alive():
            return
        if action == "hold":
            go_on()
            return
        try:
            if action == "open":
                more, _after = drive.open_more(self.hwnd, self.rect, name=step.target,
                                               stop=lambda: not alive(), clicks_only=True)
                if more is None:
                    raise drive.Interrupted(f"{step.target} isn't on the page")
                go_on()
                return
            control = uiread.find(uiread.read(self.hwnd), step.target)
            if control is not None:
                drive.Driver(self.hwnd, lambda: not alive(), clicks_only=True).point(control)
            if action == "show":
                time.sleep(HOLD_WAIT)
                go_on()
        except drive.Interrupted:
            if action == "open":
                name = step.target
                self.app.call_soon(lambda: alive() and self.box is not None and self.box.show(
                    f"Click {name}, then press Next.", "Next"))

    def put_back_switch(self):
        """Worker: switch back what the app flipped, and check it. Safe to call twice."""
        if not self.flipped:
            return
        self.flipped = False
        entry = putback.read(SETTINGS_DIR) or {}
        name, was = self.auto.name, entry.get("state", "Off" if self.tip.auto_to == "On" else "On")
        if drive.flip(self.hwnd, name, was):
            putback.clear(SETTINGS_DIR)
            self.app.call_soon(lambda: self.app.append_log(
                f"↩️ {name} is back {was.lower()}, as it was before the recording."))
        else:   # the journal stays: the next start tries again
            self.app.call_soon(lambda: self.app.append_log(
                f"⚠️ Couldn't switch {name} back to {was.lower()} by itself. Switch it back in "
                "the window the tip opened."))

    def did(self, voice, control):
        """Worker: the app finished one step of finding the page."""
        recorder = self.intro_recorder or self.recorder
        if recorder is not None:   # None: cancelled meanwhile
            recorder.mark()
            self.nav.append((voice, control))

    def find_outside(self):
        """Worker: the taskbar's menu or the Run box, recorded on their own; the page or 0."""
        drive.close(self.dialog.page_hwnd)   # opened while planning: let the app open it
        time.sleep(0.5)
        os.makedirs(self.folder, exist_ok=True)
        intro = {"path": os.path.join(self.folder, "intro.mp4")}

        def record(rect):
            rect, _monitor = screen.fit_to_screen(rect)
            if screen.can_record(rect):
                failure = screen.LAST_FAILURE
                self.app.call_soon(lambda: self.app.append_log(
                    f"⚠️ Screen recording failed: {failure}"))
                return False
            intro["rect"] = rect
            self.intro_recorder = screen.Recorder(rect, intro["path"])
            return True

        find = drive.taskbar_menu if self.route == "taskbar" else drive.run_box
        try:
            hwnd = find(self.place, record, self.did, self.stopped)
        except drive.Interrupted:
            hwnd = 0
        recorder, self.intro_recorder = self.intro_recorder, None
        if recorder is not None:
            intro["length"] = recorder.stop()
            intro["marks"] = recorder.marks[:len(self.nav) - 1]   # the last step ends the file
        if hwnd and self.nav and recorder is not None:
            self.intro = intro
        else:
            self.nav = []
            if recorder is not None:
                try:
                    os.remove(intro["path"])
                except OSError:
                    pass
        return hwnd

    def find_in_settings(self):
        """Worker: search Settings for the page and click it, or open it directly."""
        home = uiread.read(self.hwnd)
        self.nav_blurs = uiread.private(home)   # the account card on Settings' home page
        try:
            found = drive.settings_search(self.hwnd, self.place, self.did, self.stopped)
        except drive.Interrupted:
            found = False
        if self.phase != "auto":
            return
        planned = uiread.recall(cache.Cache(CACHE_ROOT, "winui"), self.place.id)
        if found and not drive.same_page(drive.settled(self.hwnd), planned):
            found = False   # the result led elsewhere: the planned page is opened after it
        if not found:
            self.route = "direct"
            subprocess.Popen(self.place.command(), creationflags=uiread.NO_WINDOW)
            time.sleep(2.0)
            self.did(f"Open {self.place.name}.", None)
        controls = drive.settled(self.hwnd)   # read too early, switches show the wrong way
        self.app.call_soon(lambda: self.after_nav(controls))

    def after_nav(self, controls):
        if self.phase != "auto":
            return
        self.phase = "record"
        if controls:
            self.controls, self.before = controls, self.states_before(controls)
        self.begin_steps()

    def states_before(self, controls):
        """How the page's switches were before recording: as read while planning first (the
        page had settled then), else as read now."""
        return {**uiread.states(controls), **self.dialog.page_states}

    def begin_steps(self):
        """The tip's own steps, after the ones the app did to find the page."""
        if self.nav and self.step == 0:
            steps = [tips.Step(guide="", voice_off=voice) for voice, _c in self.nav]
            # A copy: the plan in the dialog stays as it was for another recording.
            self.tip = dataclasses.replace(self.tip, steps=steps + list(self.tip.steps))
            self.step = len(steps)
        self.show_step()

    def start(self, hwnd):
        self.hwnd, rect = hwnd, screen.foreground_rect() if hwnd else None
        if rect is None:
            self.fail("The window opened behind other windows, so there is nothing to record. "
                      "Close other windows and press Record again.")
            return
        # Only what is on the screen: a maximized window reaches past a laptop's edges, and the
        # screen grab refuses that (school laptop, 2026-10-28).
        rect, _monitor = screen.fit_to_screen(rect)
        why = screen.can_record(rect)
        if why:
            self.app.append_log(f"⚠️ Screen recording failed: {screen.LAST_FAILURE}")
            self.fail(why)
            return
        self.rect = rect
        self.controls = uiread.read(self.hwnd)
        if not self.controls:
            self.app.append_log("ℹ️ Windows didn't list this page's buttons (UI Automation is "
                                "blocked here), so the video won't zoom onto them.")
        self.before = self.states_before(self.controls)
        os.makedirs(self.folder, exist_ok=True)
        self.recorder = screen.Recorder(rect, os.path.join(self.folder, "recording.mp4"))
        self.box = GuideBox(self.app, rect, self.next, self.cancel)
        if self.route == "settings":
            self.phase = "auto"
            self.track_clicks()
            self.box.show(f"Watch: the app opens Settings and searches for {self.place.name}. "
                          "Don't touch the mouse.", "Next")
            threading.Thread(target=self.find_in_settings, daemon=True).start()
            return
        self.track_clicks()
        self.begin_steps()

    def track_clicks(self):
        """Where the mouse is pressed while recording, 30 times a second (window's thread)."""
        if self.recorder is None or self.phase not in ("record", "auto"):
            return
        down = screen.mouse_down()
        if down and not self.pressed:
            where = screen.cursor()
            if where:
                self.clicks.append((self.recorder.clock() - self.recorder.began, *where))
                # A click on the step's own button ends the step, like a flipped switch does:
                # a button ("Run Storage Sense now") has no state to watch (2026-10-26).
                button, index = self.found.get(self.step), self.step
                if self.phase == "record" and button is not None and \
                        button.x <= where[0] < button.x + button.w and \
                        button.y <= where[1] < button.y + button.h:
                    self.app.after(800, lambda: self.step == index and self.next())
        self.pressed = down
        self.app.after(33, self.track_clicks)

    def show_step(self):
        step = self.tip.steps[self.step]
        last = self.step == len(self.tip.steps) - 1
        guide = step.guide
        if step.waits_for_change and self.was_on:
            guide = "It's already on here: point at it with the mouse, then press Next."
        mine = self.auto is not None and step.target == self.auto.name and not self.flipping
        # What the app does itself in this step; only what it may not do is left to the user.
        action = ""
        if mine:
            guide = (f"Watch: the app turns {self.auto.name} {self.tip.auto_to.lower()}. "
                     "Don't touch the mouse.")
        elif step.nav:
            action = "open"                 # "Show more...": only navigation
        elif step.target and (step.already or (step.waits_for_change and self.was_on)):
            action = "show"                 # already the way the tip wants: pointed at
        elif step.target and not last:
            action = "point"                # the user clicks it: the app shows where
            guide = f"{guide} (The mouse shows where; click it yourself.)"
        elif not step.target and not step.waits_for_change and not last:
            action = "hold"                 # nothing to click: long enough for its voice
        self.box.show(guide, "Finish" if last else "Next")
        if mine:
            self.flipping = True
            threading.Thread(target=self.flip_on, args=(self.step,), daemon=True).start()
        elif action:
            threading.Thread(target=self.auto_step, args=(self.step, step, action),
                             daemon=True).start()
        if last and step.guide == tips.CLOSING:
            index = self.step   # nothing left to do: long enough for its voice, then finish
            self.app.after(CLOSING_WAIT, lambda: self.step == index and self.phase == "record"
                           and self.recorder is not None and self.finish())
        if self.tip.watch_change or self.tip.key in tips.TIPS:
            if step.waits_for_change and not self.was_on:
                self.watch()   # a setting with a Windows command: read it directly
        else:
            self.follow(self.step)

    def watch(self):
        """Poll the setting until the user has turned it on; that ends the step."""
        if self.recorder is None or self.phase != "record":
            return
        step = self.step
        try:
            now = self.tip.read()
            on = now != self.state if self.tip.watch_change else self.tip.is_on(now)
        except OSError:
            on = False
        if on:
            self.app.after(600, lambda: self.step == step and self.next())
        else:
            self.app.after(300, self.watch)

    def follow(self, index):
        """Background: re-read the page during step ``index``; find its button, watch its switch.

        The button is found by name, or for the main step by the tip's words (a switch that
        only appeared after the user opened a section). A switch that flips ends the step.
        """
        step = self.tip.steps[index]
        main = step.waits_for_change or (not step.target and index == len(self.tip.steps) - 2)
        words = tips.match_words(self.text, winmap.BY_ID.get(self.tip.key))

        def work():
            first = None
            while self.recorder is not None and self.phase == "record" and self.step == index:
                controls = uiread.read(self.hwnd)
                if controls:
                    self.controls = controls
                    for name, state in uiread.states(controls).items():
                        self.before.setdefault(name, state)   # first seen: what to put back
                    button = uiread.find(controls, step.target) if step.target else None
                    if button is None and main and not step.already:
                        button = uiread.best_match(uiread.targets(controls, self.rect), words)
                    if button is not None:
                        self.found[index] = button
                        if step.waits_for_change and button.state in ("On", "Off"):
                            if first is None:
                                first = button.state
                            elif button.state != first:
                                self.app.call_soon(lambda: self.step == index and self.next(),
                                                   600)
                                return
                time.sleep(0.3)

        threading.Thread(target=work, daemon=True).start()

    def next(self):
        if self.phase == "putback":
            self.skip_putback()
            return
        if self.recorder is None or self.phase != "record":
            return
        if self.step == len(self.tip.steps) - 1:
            self.finish()
            return
        self.recorder.mark()
        self.step += 1
        self.show_step()

    def _close(self):
        """Stop recording and close the box (cancel and fail only)."""
        recorder, self.recorder = self.recorder, None
        if recorder is not None:
            threading.Thread(target=recorder.stop, daemon=True).start()
        if self.box is not None:
            self.box.destroy()
            self.box = None
        self.app.deiconify()

        def work():
            self.restore()
            self.put_back_switch()

        threading.Thread(target=work, daemon=True).start()

    def cancel(self):
        if self.phase == "putback":
            self.skip_putback()
            return
        if self.phase not in ("record", "auto"):
            return   # saving or done: the recording is kept and cut
        self.phase = "done"   # also stops the app finding the page (drive checks stopped())
        self._close()
        shutil.rmtree(self.folder, ignore_errors=True)
        self.bring_back()
        self.dialog.say("Cancelled. Nothing was kept, and the setting is as it was.")

    def fail(self, message):
        self.phase = "done"
        self._close()
        drive.close(self.hwnd)   # the window this recording opened (never one of the app's)
        self.app.append_log(f"⚠️ Show it on my PC: {message}")
        self.bring_back()
        self.dialog.say(message, ERR_COLOR)
        report_tip_failure(self.app, message)

    def bring_back(self):
        """The Show it on my PC window, in front of everything and focused: on the school laptop
        it came back behind Settings, and the error in it was never seen (2026-10-28)."""
        self.dialog.deiconify()
        self.dialog.lift()
        self.dialog.attributes("-topmost", True)
        self.dialog.focus_force()
        self.app.after(400, lambda: self.dialog.winfo_exists()
                       and self.dialog.attributes("-topmost", False))

    # -- after recording ------------------------------------------------------------------
    def finish(self):
        """Stop recording (in the background), then walk the user through putting things back."""
        recorder, self.recorder = self.recorder, None
        self.phase = "saving"
        self.marks = list(recorder.marks)
        self.box.show("Saving the recording...", "Skip")

        def work():
            self.length, self.path = recorder.stop(), recorder.path
            self.restore()   # settings with a Windows command go back by themselves
            self.put_back_switch()   # ...and a switch the app flipped, by the app
            self.app.call_soon(self.start_putback)

        threading.Thread(target=work, daemon=True).start()

    def start_putback(self):
        self.phase = "putback"
        self.check_putback()

    def check_putback(self):
        def work():
            controls = uiread.read(self.hwnd)
            left = uiread.changed(self.before, uiread.states(controls)) if controls else {}
            self.app.call_soon(lambda: self.show_putback(left))

        threading.Thread(target=work, daemon=True).start()

    def show_putback(self, left):
        if self.phase != "putback":
            return
        if not left:
            self.done_putback()
            return
        self.left = left
        lines = [f"Switch {name} back to {was}" for name, was in left.items()]
        if self.box is not None:
            self.box.show("Recording stopped. Now put back what you changed:\n"
                          + "\n".join(lines), "Skip")
        self.app.after(800, self.check_putback)

    def skip_putback(self):
        if self.left:
            self.app.append_log("⚠️ Still changed after the recording: "
                                + "; ".join(f"{n} (was {w})" for n, w in self.left.items())
                                + ". Switch them back in the window the tip opened.")
        self.done_putback()

    def done_putback(self):
        if self.phase == "done":
            return
        self.phase = "done"
        if self.box is not None:
            self.box.destroy()
            self.box = None
        self.app.deiconify()
        self.cut()

    @staticmethod
    def _inside(control, rect):
        """``control``'s rectangle in the recorded ``rect``'s pixels, or None."""
        return None if control is None else (control.x - rect[0], control.y - rect[1],
                                             control.w, control.h)

    def focuses(self):
        """Per step of the page's recording: its button's rectangle in the window, or where
        the user last clicked. The steps of an intro recording come before these."""
        left, top = self.rect[0], self.rect[1]
        offset = len(self.nav) if self.intro else 0
        out = []
        for index, span in enumerate(screen.spans(self.marks, self.length)):
            index += offset
            if index < len(self.nav):
                out.append(self._inside(self.nav[index][1], self.rect))
                continue
            step = self.tip.steps[index] if index < len(self.tip.steps) else None
            button = self.found.get(index) or (
                uiread.find(self.controls, step.target) if step and step.target else None)
            if button is not None:
                out.append((button.x - left, button.y - top, button.w, button.h))
            else:
                out.append(screen.last_click(self.clicks, span, self.rect))
        return out

    def cut(self):
        w, h, _o, _b = resolve_target(self.app.var_aspect.get(), self.app.var_resolution.get())
        if self.dialog.winfo_exists():
            self.dialog.destroy()
        self.app.append_log("✂️ Cutting the recording into scenes...")
        left, top = self.rect[0], self.rect[1]
        blurs = [(x - left, y - top, bw, bh) for x, y, bw, bh in uiread.private(self.controls)]
        # Settings' home page, searched in the page's own recording, shows the account card.
        home = blurs + [(x - left, y - top, bw, bh) for x, y, bw, bh in self.nav_blurs]
        searched = 0 if self.intro else len(self.nav)
        focuses = self.focuses()
        intro = self.intro

        def work():
            try:
                clips = []
                if intro is not None:
                    rect = intro["rect"]
                    clips += screen.cut_all(
                        intro["path"], intro["marks"], intro["length"], (w, h),
                        os.path.join(self.folder, "intro"), window=rect[2:],
                        focuses=[self._inside(c, rect) for _v, c in self.nav])
                for i, span in enumerate(screen.spans(self.marks, self.length)):
                    clips.append(screen.cut(
                        self.path, span, (w, h), os.path.join(self.folder, f"step_{i + 1}.mp4"),
                        window=self.rect[2:], focus=focuses[i],
                        blurs=home if i < searched else blurs))
                text = tips.script(self.tip, self.was_on, clips,
                                   self.opening(clips) if self.hook else ())
            except Exception as exc:  # noqa: BLE001
                message = f"Couldn't cut the recording ({exc})."
                self.app.call_soon(lambda: (self.app.append_log(f"⚠️ {message}"),
                                            report_tip_failure(self.app, message)))
                return
            self.app.call_soon(lambda: self.use(text))

        threading.Thread(target=work, daemon=True).start()

    def opening(self, clips):
        """Worker: the hook and the "Right now we're going to..." scenes (hooks.py) over the
        result frame (the switch zoomed in, ringed), blurred: [(picture, voice, card)]."""
        main = next((i for i, s in enumerate(self.tip.steps)
                     if self.auto is not None and s.target == self.auto.name), None)
        if main is None:
            main = next((i for i, s in enumerate(self.tip.steps)
                         if s.target and (s.waits_for_change or s.already)), len(clips) - 1)
        clip = clips[min(main, len(clips) - 1)]
        pictures = []
        # Softly: Settings is dark already, and a strong blur left a black frame (2026-10-26).
        looks = (("intro_hook.png", "boxblur=6:1,eq=brightness=0.04:saturation=1.3"),
                 ("intro_explain.png", "boxblur=2:1"))
        for name, look in looks:
            picture = os.path.join(self.folder, name)
            # The cut clip ends on its held last frame: the zoom and ring have arrived there.
            mastering.run_ffmpeg(["-y", "-sseof", "-0.2", "-i", clip, "-frames:v", "1", "-vf",
                                  look, picture])
            pictures.append(picture)
        template, voice, card = hooks.pick(hooks.kind_of(self.place),
                                           hooks.seconds(len(self.tip.steps)),
                                           hooks.remembered(SETTINGS_DIR))
        if self.place is None and writer.installed():   # Windows search: no lines fit it
            try:
                with writer.Session() as session:
                    written = " ".join(session.hook(self.text).split())
                if hooks.check(written):
                    template, voice, card = written, written, "QUICK WINDOWS TIP"
            except Exception:  # noqa: BLE001 - the ready-made line stays
                pass
        hooks.remember(SETTINGS_DIR, template)
        return [(pictures[0], voice, card),
                (pictures[1], hooks.explain(self.tip, self.text, self.place), "")]

    def use(self, text):
        self.app.script_box.delete("1.0", "end")
        self.app.script_box.insert("1.0", text)
        self.app._commit_script()
        self.app._tip_pending = True
        self.app.append_log("🎬 The recording is in the Storyboard: check it, then Make video.")
        self.app.select_page("board")
