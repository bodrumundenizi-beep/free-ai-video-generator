"""Developer tool: test every place on the map of Windows through "Show it on my PC", in the app.

Started with `AIVideoStudio.exe --tiptest [--places 1-30] [--only id,id]`, with APPDATA pointed
at the test folder (it refuses to run on the user's own settings). For each place it types a tip
into the real window, presses Record, clicks Next/Finish/Skip with real mouse clicks (never a
switch or setting), then makes a Draft video of the recording. A fail is tried once more, then
it moves on. Results and summary.md are written after every tip, so a run can be stopped and
carried on.
"""

from __future__ import annotations

import ctypes
import json
import os
import time

from . import drive, putback, screen, winmap

FOLDER_NAME = "Tip test videos"
STUCK = 240.0          # seconds without progress before an attempt counts as stuck
CLICK_EVERY = 2.5      # seconds between clicks on the guide box (a person reading it)
TRIES = 2              # a fail is tried once more
WAITS = 4.0            # seconds a step stays put before the tester presses Next (a person's step)


_progress = ""   # progress.txt in the test folder: the built app has no console


def say(text: str) -> None:
    print(text, flush=True)
    if _progress:
        with open(_progress, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')}  {text}\n")


def tip_text(place) -> str:
    return f"how to change {place.name}"


def choose(places, spec: str = "", only: str = ""):
    """The places to test: ``only`` (ids) or ``spec`` "31-110" (1-based, inclusive), else all."""
    if only:
        wanted = [w.strip() for w in only.split(",") if w.strip()]
        return [p for p in places if p.id in wanted]
    if spec:
        first, _, last = spec.partition("-")
        return list(places)[int(first) - 1:int(last or first)]
    return list(places)


def verdict(place, refused: str, opened: str, video_seconds: float) -> tuple[str, str]:
    """("pass" or "fail", why) for one attempt."""
    if place.admin:
        if refused and "admin" in refused.lower():
            return "pass", "refused, as it should be"
        return "fail", "an admin place was not refused" if not refused else refused
    if refused:
        return "fail", refused
    if opened and opened != place.id:
        return "fail", f"the map chose another page: {opened}"
    if video_seconds <= 1.0:
        return "fail", "no video was made"
    return "pass", ""


class Results:
    """results.json: one entry per place id, kept between runs."""

    def __init__(self, folder: str):
        self.path = os.path.join(folder, "results.json")
        try:
            with open(self.path, encoding="utf-8") as fh:
                self.data = json.load(fh)
        except (OSError, ValueError):
            self.data = {}

    def done(self, place_id: str) -> bool:
        return self.data.get(place_id, {}).get("result") in ("pass", "fail")

    def record(self, place_id: str, **entry) -> None:
        self.data[place_id] = entry
        part = self.path + ".part"
        with open(part, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=1)
        os.replace(part, self.path)


def summary(results: Results, places) -> str:
    """summary.md: counts, the failed ones first, then every tested place."""
    rows = [(p, results.data[p.id]) for p in places if p.id in results.data]
    passed = [r for r in rows if r[1]["result"] == "pass"]
    failed = [r for r in rows if r[1]["result"] == "fail"]
    lines = [f"# Tip test: {len(passed)} passed, {len(failed)} failed, "
             f"{len(rows)} of {len(places)} places tested", "",
             f"Made {time.strftime('%Y-%m-%d %H:%M')} with `--tiptest`.", ""]
    if failed:
        lines += ["## Failed", "", "| # | Place | Tries | Why |", "|---|---|---|---|"]
        lines += [f"| {places.index(p) + 1} | {p.name} (`{p.id}`) | {e['attempts']} | "
                  f"{e['reason']} |" for p, e in failed]
        lines.append("")
    lines += ["## All tested", "", "| # | Place | Result | Tries | Seconds | Note |",
              "|---|---|---|---|---|---|"]
    lines += [f"| {places.index(p) + 1} | {p.name} (`{p.id}`) | {e['result']} | {e['attempts']} | "
              f"{e['seconds']:.0f} | {e['reason']} |" for p, e in rows]
    return "\n".join(lines) + "\n"


def click(widget) -> None:
    """A real mouse click on ``widget``, through Windows, like a person."""
    user32 = ctypes.windll.user32
    user32.SetCursorPos(widget.winfo_rootx() + widget.winfo_width() // 2,
                        widget.winfo_rooty() + widget.winfo_height() // 2)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    user32.mouse_event(0x0004, 0, 0, 0, 0)


class Runner:
    """Drives the app (``studio`` module, ``app`` window) through every chosen place."""

    def __init__(self, studio, app, places, folder):
        self.studio, self.app, self.places, self.folder = studio, app, places, folder
        self.all_places = list(winmap.PLACES)
        self.results = Results(folder)
        self.queue = [p for p in places if not self.results.done(p.id)]
        self.place = self.dialog = self.session = None
        self.attempt, self.began, self.progress_at, self.last_click = 0, 0.0, 0.0, 0.0
        self.stage, self.opened = "", ""
        self.tries, self.rendering = {}, []   # attempts per place; videos still being made

    # -- one attempt ---------------------------------------------------------------------
    def start(self):
        self.app.after(500, self.next_place)
        self.app.after(1000, self.check_renders)

    def _clear(self, keep=()):
        """Close every extra window (welcome, Render complete...): they take the clicks.

        Also while an attempt runs (``keep`` = its own windows): the welcome window opens a
        moment after the app starts and grabbed the first tip's clicks (2026-10-24).
        """
        for widget in list(self.app.winfo_children()):
            if isinstance(widget, self.studio.ctk.CTkToplevel) and not isinstance(widget, keep):
                try:
                    widget.grab_release()
                    widget.destroy()
                except Exception:  # noqa: BLE001 - already gone
                    pass

    def next_place(self):
        if not self.queue:
            self.place = None   # check_renders finishes once the last video is made
            return
        self.place = self.queue.pop(0)
        self.tries[self.place.id] = self.attempt = self.tries.get(self.place.id, 0) + 1
        self.try_once()

    def try_once(self):
        self._clear()
        self.began = self.progress_at = time.monotonic()
        self.stage, self.opened, self.session = "planning", "", None
        self.script_before = self.app.script_box.get("1.0", "end")   # a new one means done
        say(f"[{self.all_places.index(self.place) + 1}] {self.place.id} try {self.attempt}")
        self.dialog = self.studio.TipDialog(self.app)
        self.dialog.ask.insert(0, tip_text(self.place))
        self.dialog.var_hook.set(False)   # the test only: the hook isn't what it checks
        self.dialog.record()
        self.app.after(500, self.watch)

    def moved(self, stage):
        if stage != self.stage:
            self.stage, self.progress_at = stage, time.monotonic()

    def watch(self):
        """Every 0.5 s: see where the attempt is, click when the guide box waits."""
        now = time.monotonic()
        self._clear(keep=(self.studio.TipDialog, self.studio.GuideBox, self.studio.FlipDialog))
        for widget in self.app.winfo_children():
            yes = getattr(widget, "confirm_button", None)   # None while it is still being built
            if isinstance(widget, self.studio.FlipDialog) and yes is not None                     and now - self.last_click > CLICK_EVERY:
                self.last_click = now
                click(yes)   # "OK to change this?": yes, and put back after
        if now - self.progress_at > STUCK:
            self.end("fail", f"stuck while {self.stage}")
            return
        dialog_open = self.dialog is not None and self.dialog.winfo_exists()
        session = getattr(self.dialog, "session", None) if dialog_open else self.session
        if session is not None and self.session is None:
            self.session = session
            self.opened = session.tip.key   # a place id, or "search" when nothing matched
        if self.session is None:
            if dialog_open and self.dialog.record_button.cget("state") == "normal" \
                    and self.dialog.status.cget("text_color") == self.studio.ERR_COLOR:
                self.end_refused(self.dialog.status.cget("text"))
                return
            self.app.after(500, self.watch)
            return
        phase = self.session.phase
        self.moved(f"{phase} step {self.session.step}")
        if phase in ("record", "putback") and self.session.box is not None:
            # Most steps go on by themselves now (the app clicks, points or flips): Next only
            # for one that waits for a person, so the tester never skips what the app does.
            if now - self.progress_at > WAITS and now - self.last_click > CLICK_EVERY:
                self.last_click = now
                if drive.elevated(screen.foreground()):
                    # Windows drops made-up clicks while Task Manager (admin) is in front; a
                    # person's real click isn't, so press it through the app instead.
                    self.session.box.next.invoke()
                else:
                    click(self.session.box.next)
        script = self.app.script_box.get("1.0", "end")
        if phase == "done" and script != self.script_before and "Recordings" in script:
            self.make_video()
            return
        self.app.after(500, self.watch)

    def make_video(self):
        """The recording is in the Storyboard: start its video, then go straight on to the next
        place while it renders (for speed, the test only). Its result comes in check_renders."""
        drive.close(self.session.hwnd)
        left = putback.read(self.studio.SETTINGS_DIR)
        if left:   # the worst fail there is: a PC left changed. Stop, don't go on changing.
            self.tries[self.place.id] = TRIES
            self.end("fail", f"setting not put back: {left['switch']} (was {left['state']})")
            self.queue.clear()
            say("STOPPED: a setting may be left changed; check it before running again.")
            return
        if self.app.is_rendering:   # the last place's video is still being made
            self.moved("waiting for the last video")
            if time.monotonic() - self.progress_at > STUCK:
                self.end("fail", "stuck while the last video rendered")
                return
            self.app.after(500, self.make_video)
            return
        target = os.path.join(self.folder, f"{self.place.id}.mp4")
        if os.path.isfile(target):
            os.remove(target)
        self.app.var_resolution.set("Draft")
        self.app.var_ask_save.set(False)
        self.app.var_output.set(target)
        self.app.start_render()
        self.rendering.append({"place": self.place, "attempt": self.attempt, "target": target,
                               "began": self.began, "opened": self.opened,
                               "route": self.session.route, "started": time.monotonic()})
        self._leave()
        self.app.after(1500, self.next_place)

    def check_renders(self):
        """Every second: a finished video gets its place's result (a fail is tried again)."""
        while self.rendering and not (self.app.is_rendering and len(self.rendering) == 1):
            job = self.rendering.pop(0)
            seconds = 0.0
            if os.path.isfile(job["target"]):
                try:
                    from . import preview

                    seconds = preview.probe(job["target"])[2] or 0.0
                except Exception:  # noqa: BLE001 - counted as no video
                    seconds = 0.0
            result, why = verdict(job["place"], "", job["opened"], seconds)
            self.record(job["place"], job["attempt"], result, why, job["began"],
                        job["target"] if seconds else "", job["route"])
        if self.rendering and time.monotonic() - self.rendering[0]["started"] > STUCK:
            job = self.rendering.pop(0)
            self.record(job["place"], job["attempt"], "fail", "stuck while rendering",
                        job["began"], "", job["route"])
        if self.place is None and self.queue:   # the last video failed: try that place again
            self.next_place()
        if self.rendering or self.queue or self.place is not None:
            self.app.after(1000, self.check_renders)
        else:
            self.finish()

    def end_refused(self, message):
        result, why = verdict(self.place, " ".join(message.split())[:200], "", 0.0)
        self.end(result, why)

    def _leave(self):
        """Close what this attempt opened; the app is free for the next place."""
        from . import myvoice

        if self.session is not None:
            if self.session.phase in ("record", "auto"):
                self.session.cancel()
            drive.close(self.session.hwnd)
        myvoice.close_speaker()
        self._clear()

    def end(self, result, why):
        """An attempt that ended before its video (refused, stuck, not put back)."""
        route = self.session.route if self.session is not None else ""
        self._leave()
        self.record(self.place, self.attempt, result, why, self.began, "", route)
        self.app.after(1500, self.next_place)

    def record(self, place, attempt, result, why, began, video, route):
        took = time.monotonic() - began
        say(f"    [{self.all_places.index(place) + 1}] {place.id}: {result}: {why or 'ok'} "
            f"(try {attempt}, found by {route or 'opening it'})")
        if result == "fail" and attempt < TRIES:
            self.queue.insert(0, place)   # tried again next
            return
        self.results.record(place.id, result=result, reason=why, attempts=attempt,
                            seconds=round(took, 1), video=video, route=route)
        with open(os.path.join(self.folder, "summary.md"), "w", encoding="utf-8") as fh:
            fh.write(summary(self.results, self.all_places))

    def finish(self):
        tested = [p for p in self.places if p.id in self.results.data]
        passed = sum(1 for p in tested if self.results.data[p.id]["result"] == "pass")
        say(f"DONE: {passed} passed, {len(tested) - passed} failed of {len(tested)}")
        from . import writer

        writer.close_all()   # the Smart writer's server would outlive the app otherwise
        self.app.after(500, self.app.destroy)


class _KeptWriter:
    """The test only: one Smart writer kept loaded for every place, instead of loading the
    model for each plan. ``with writer.Session()`` gets it without closing it."""

    def __init__(self, session):
        self.session = session

    def __call__(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self.session

    def __exit__(self, *_exc):
        return False


def main(argv, studio) -> None:
    """Run the test in the app (``studio`` is the ai_video_studio module)."""
    import argparse

    parser = argparse.ArgumentParser(prog="tiptest")
    parser.add_argument("--places", default="")
    parser.add_argument("--only", default="")
    args, _rest = parser.parse_known_args(argv)
    from . import paths

    folder = os.path.join(paths.known_folder(paths.CSIDL_DOCUMENTS, "Documents"), FOLDER_NAME)
    if os.path.abspath(folder).lower() not in os.path.abspath(studio.SETTINGS_DIR).lower():
        # The 22 Oct lesson: a test run must never touch the user's own settings.
        raise SystemExit(f"Start it with APPDATA set inside {folder}, not on your own settings.")
    os.makedirs(folder, exist_ok=True)
    global _progress
    _progress = os.path.join(folder, "progress.txt")
    say(f"tiptest started: {args.places or args.only or 'all places'}")
    app = studio.App()
    places = choose(winmap.PLACES, args.places, args.only)
    from . import writer

    if writer.installed():   # loaded once for the whole test, not once per place
        studio.writer.Session = _KeptWriter(writer.Session())
    Runner(studio, app, places, folder).start()
    app.mainloop()
