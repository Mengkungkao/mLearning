"""Small client for whisplay-daemon foreground apps (Pillow and mfruit_sdk only).

Copied from mFruit OS's app template (templates/whisplay-app-template/app/
whisplay_app.py) by way of mDisplay, with mLearning's app id. It follows the
Whisplay APP_INTEGRATION.md contract: subscribe to events, acquire focus,
map the shared RGB565 framebuffer, draw, and release focus on exit. This is
the only place mLearning talks to whisplay-daemon (besides the SDK's
own_escape_key); the display backend interface is ``show(image)`` and
``has_focus``, so another screen can replace it.
"""

from __future__ import annotations

import json
import mmap
import os
import socket
import threading
import time

from mfruit_sdk.ui.rgb565 import to_rgb565
from PIL import Image

SOCKET_PATH = os.environ.get("WHISPLAY_DAEMON_SOCKET", "/tmp/whisplay-daemon.sock")
WIDTH, HEIGHT = 240, 280

def request(cmd: str, payload: dict | None = None, timeout: float = 3.0) -> dict:
    body = {"version": 1, "cmd": cmd, "payload": payload or {}}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(SOCKET_PATH)
        sock.sendall((json.dumps(body) + "\n").encode())
        line = sock.makefile("r").readline()
    response = json.loads(line) if line else {"ok": False, "error": "no response"}
    if not response.get("ok"):
        raise RuntimeError(response.get("error", f"{cmd} failed"))
    return response.get("payload") or {}


class WhisplayApp:
    """Callbacks run on the event thread: on_press(), on_release(),
    on_exit_request(), on_focus_changed(has_focus)."""

    def __init__(self, app_id: str | None = None):
        self.app_id = app_id or os.environ.get("WHISPLAY_APP_ID", "mlearning")
        self.token: str | None = None
        self._file = None
        self._map: mmap.mmap | None = None
        self._lock = threading.Lock()
        self.running = False
        self.on_press = lambda: None
        self.on_release = lambda: None
        self.on_exit_request = lambda: None
        self.on_focus_changed = lambda has_focus: None

    # -------------------------------------------------------------- focus
    def start(self, timeout: float = 5.0) -> None:
        self.running = True
        threading.Thread(target=self._events, daemon=True).start()
        deadline = time.time() + timeout
        while True:
            try:
                self._attach(request("app.focus.acquire", {"app_id": self.app_id})["session_token"])
                return
            except (OSError, RuntimeError):
                if time.time() > deadline:
                    raise
                time.sleep(0.2)

    def _attach(self, token: str) -> None:
        info = request("framebuffer.acquire", {"app_id": self.app_id, "session_token": token})
        with self._lock:
            self._detach()
            self._file = open(info["buffer_handle"], "r+b")
            self._map = mmap.mmap(self._file.fileno(), int(info["stride"]) * int(info["height"]))
            self.token = token
        self.on_focus_changed(True)

    def _detach(self) -> None:
        if self._map is not None:
            self._map.close()
            self._map = None
        if self._file is not None:
            self._file.close()
            self._file = None
        self.token = None

    @property
    def has_focus(self) -> bool:
        return self._map is not None

    def show(self, image: Image.Image) -> None:
        frame = to_rgb565(image)
        with self._lock:
            if self._map is not None:
                self._map[:] = frame

    def stop(self) -> None:
        self.running = False
        token = self.token
        with self._lock:
            self._detach()
        if token:
            try:
                request("app.focus.release", {"app_id": self.app_id, "session_token": token})
            except (OSError, RuntimeError):
                pass

    # ------------------------------------------------------------- events
    def _events(self) -> None:
        while self.running:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                    sock.connect(SOCKET_PATH)
                    sock.sendall((json.dumps({"version": 1, "cmd": "events.subscribe",
                                              "payload": {"app_id": self.app_id}}) + "\n").encode())
                    reader = sock.makefile("r")
                    reader.readline()  # subscription ack
                    for line in reader:
                        if not self.running:
                            return
                        self._dispatch(json.loads(line))
            except (OSError, ValueError):
                time.sleep(0.5)

    def _dispatch(self, message: dict) -> None:
        event = message.get("event")
        payload = message.get("payload") or {}
        if payload.get("app_id") not in (None, self.app_id):
            return
        if event == "button_pressed":
            self.on_press()
        elif event == "button_released":
            self.on_release()
        elif event == "app_exit_requested":
            self.on_exit_request()
        elif event == "app_focus_revoked":
            with self._lock:
                self._detach()
            self.on_focus_changed(False)
        elif event == "app_foreground_acquired" and payload.get("session_token") != self.token:
            # Brought back to the front (e.g. relaunched from the launcher).
            try:
                self._attach(payload["session_token"])
            except (OSError, RuntimeError, KeyError):
                pass
