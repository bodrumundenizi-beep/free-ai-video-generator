"""A headless Chromium browser (Edge, Chrome or Brave) that draws a promo video's frames.

The page is ours (scene.html) and every frame is a pure function of time: Python sets the
time with ``seek(t)``, takes a screenshot and pipes it to FFmpeg. Only local files are opened.
Edge can be removed from Windows (Deniz's PC has none), and its WebView2 runtime won't run
headless on its own (exit code 13, tested 2026-11-13), so any installed Chromium will do; with
none at all, WebView2 inside a small helper window (webhost.py) takes its place.
The DevTools connection is a ~50-line WebSocket client: no new dependency.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request

from ..footage import Stopped
from . import webhost

WEBVIEW2 = "WebView2"   # find()'s name for the fallback: no browser, Windows' own engine

CANDIDATES = (   # (name, path below one of the program folders)
    ("Microsoft Edge", r"Microsoft\Edge\Application\msedge.exe"),
    ("Google Chrome", r"Google\Chrome\Application\chrome.exe"),
    ("Brave", r"BraveSoftware\Brave-Browser\Application\brave.exe"),
    ("Chromium", r"Chromium\Application\chrome.exe"),
    ("Vivaldi", r"Vivaldi\Application\vivaldi.exe"),
)
START_TIMEOUT = 20.0


class BrowserError(Exception):
    """The browser could not be found or started. The message is for the user."""


def find(fallback: bool = True) -> tuple[str, str] | None:
    """(name, path) of the first Chromium browser on this PC; with none, (WEBVIEW2, "") when
    Windows' WebView2 runtime can stand in (``fallback``); else None."""
    roots = [os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
    for name, below in CANDIDATES:
        for root in filter(None, roots):
            path = os.path.join(root, below)
            if os.path.isfile(path):
                return name, path
    if fallback and webhost.available():
        return WEBVIEW2, ""
    return None


class _Socket:
    """Just enough WebSocket for DevTools: text frames, masked when sent."""

    def __init__(self, url: str, timeout: float = 60.0):
        host, path = url[len("ws://"):].split("/", 1)
        name, port = host.rsplit(":", 1)
        self.sock = socket.create_connection((name, int(port)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /{path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
                           "Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
                           f"Sec-WebSocket-Key: {key}\r\n\r\n").encode())
        reply = b""
        while b"\r\n\r\n" not in reply:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise BrowserError("The browser closed the connection.")
            reply += chunk
        if b" 101 " not in reply.split(b"\r\n", 1)[0]:
            raise BrowserError("The browser refused the connection.")
        self.buffer = reply.split(b"\r\n\r\n", 1)[1]

    def _read(self, count: int) -> bytes:
        while len(self.buffer) < count:
            chunk = self.sock.recv(1 << 20)
            if not chunk:
                raise BrowserError("The browser closed the connection.")
            self.buffer += chunk
        out, self.buffer = self.buffer[:count], self.buffer[count:]
        return out

    def send(self, text: str) -> None:
        data = text.encode()
        mask = os.urandom(4)
        if len(data) < 126:
            head = bytes([0x81, 0x80 | len(data)])
        elif len(data) < 65536:
            head = bytes([0x81, 0x80 | 126]) + struct.pack(">H", len(data))
        else:
            head = bytes([0x81, 0x80 | 127]) + struct.pack(">Q", len(data))
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(head + mask + masked)

    def receive(self) -> str:
        message = b""
        while True:
            first, second = self._read(2)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read(8))[0]
            message += self._read(length)
            if first & 0x80:   # the last part of the message
                return message.decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class Browser:
    """One headless page, used with ``with``."""

    def __init__(self, width: int, height: int, path: str | None = None, gpu: bool = False):
        found = (None, path) if path else find()
        if not found:
            raise BrowserError("Promo videos need Microsoft Edge, Google Chrome or Brave on "
                               "this PC, or Windows' WebView2 (it draws the frames, offline). "
                               "None was found.")
        self.name = found[0]
        self.path, self.width, self.height, self.gpu = found[1], width, height, gpu
        self.proc = self.ws = None
        self.profile = tempfile.mkdtemp(prefix="aivs-promo-")
        self.calls = 0

    def __enter__(self):
        port = _free_port()
        args = [self.path, "--headless=new", f"--remote-debugging-port={port}",
                f"--user-data-dir={self.profile}", "--no-first-run", "--no-default-browser-check",
                "--hide-scrollbars", "--mute-audio", "--force-device-scale-factor=1",
                "--disable-extensions", "--allow-file-access-from-files",
                f"--window-size={self.width},{self.height}", "about:blank"]
        if not self.gpu:
            args.insert(2, "--disable-gpu")
        if self.name == WEBVIEW2:
            args = webhost.command(port)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, creationflags=flags)
        except OSError as exc:
            raise BrowserError(f"The browser could not be started: {exc}") from exc
        deadline = time.monotonic() + START_TIMEOUT
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2) as reply:
                    pages = json.load(reply)
                url = next(p["webSocketDebuggerUrl"] for p in pages if p.get("type") == "page")
                break
            except (OSError, ValueError, StopIteration):
                if time.monotonic() > deadline or self.proc.poll() is not None:
                    self.close()
                    raise BrowserError("The browser did not start in time.") from None
                time.sleep(0.1)
        self.ws = _Socket(url)
        self.call("Emulation.setDeviceMetricsOverride", width=self.width, height=self.height,
                  deviceScaleFactor=1, mobile=False)
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self) -> None:
        if self.ws is not None:
            self.ws.close()
            self.ws = None
        if self.proc is not None:
            self.proc.kill()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                pass
            self.proc = None
        shutil.rmtree(self.profile, ignore_errors=True)

    def call(self, method: str, **params) -> dict:
        self.calls += 1
        number = self.calls
        self.ws.send(json.dumps({"id": number, "method": method, "params": params}))
        while True:
            message = json.loads(self.ws.receive())
            if message.get("id") == number:
                if "error" in message:
                    raise BrowserError(f"The browser could not draw the video "
                                       f"({message['error'].get('message', 'error')}).")
                return message.get("result", {})

    def evaluate(self, expression: str):
        """Run JavaScript in the page and return its (JSON) value; awaits promises."""
        result = self.call("Runtime.evaluate", expression=expression, returnByValue=True,
                           awaitPromise=True)
        if "exceptionDetails" in result:
            text = result["exceptionDetails"].get("exception", {}).get("description", "")
            raise BrowserError(f"The video page failed: {text.splitlines()[0] if text else '?'}")
        return result.get("result", {}).get("value")

    def open(self, path: str) -> None:
        """Load a local page and wait for its fonts and pictures."""
        self.call("Page.enable")
        self.call("Page.navigate", url="file:///" + path.replace("\\", "/"))
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            try:
                if self.evaluate("window.promoReady === true"):
                    return
            except BrowserError:
                pass
            time.sleep(0.05)
        raise BrowserError("The video page did not load in time.")

    def frame(self, quality: int = 92) -> bytes:
        shot = self.call("Page.captureScreenshot", format="jpeg", quality=quality,
                         captureBeyondViewport=False)
        return base64.b64decode(shot["data"])


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def record(page: str, size, seconds: float, out_path: str, fps: int = 30, gpu: bool = False,
           should_stop=None, progress=None, browser_path: str | None = None) -> str:
    """Draw ``page`` frame by frame into an MP4 with no sound. ``progress(done, total)``."""
    from ..mastering import ffmpeg_exe

    width, height = size
    total = max(1, round(seconds * fps))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with Browser(width, height, browser_path, gpu) as browser:
        browser.open(page)
        encoder = subprocess.Popen(
            [ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps),
             "-c:v", "mjpeg", "-i", "-",
             # JPEG frames are full range; players and browsers expect TV range (a full-range
             # VP9 copy failed to decode in Chromium, 2026-11-14)
             "-vf", "scale=in_range=pc:out_range=tv", "-color_range", "tv", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
        try:
            for index in range(total):
                if should_stop is not None and should_stop():
                    raise Stopped()
                browser.evaluate(f"seek({index / fps:.5f})")
                encoder.stdin.write(browser.frame())
                if progress is not None:
                    progress(index + 1, total)
            encoder.stdin.close()
            if encoder.wait() != 0:
                raise BrowserError("FFmpeg could not save the video: "
                                   + encoder.stderr.read().decode("utf-8", "replace")[-300:])
        except BaseException:
            encoder.kill()
            raise
    return out_path
