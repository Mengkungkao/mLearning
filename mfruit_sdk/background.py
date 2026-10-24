"""Ask mFruit OS to keep this app running after the user leaves it (SDK 1.4.0).

    get()                  -> State(keep_running, screen_bright), or None
    set(keep_running=..., screen_bright=...) -> the new State, or None

``keep_running`` is the app's *Keep running* switch in Settings > Apps:
leaving the app then releases the screen instead of closing it, and mFruit
OS does not stop the process. ``screen_bright`` is *Keep screen bright*:
while the app runs in the background, mFruit OS keeps the backlight at 100%
instead of dimming or turning it off. Use it only when your app needs it, for
example a LoRa radio whose mode pin is the backlight pin (mFruit OS KI-11).
The user can change both in Settings > Apps too; read them with ``get()``
when your app comes back to the foreground.

A background app releases the screen when the user leaves it and stays
quiet: it must not ask for the screen again (docs/apps/APP_CONTRACT.md).

``None`` means mFruit OS is not there (the app runs on its own) or is too old
to answer (before this request existed): treat the feature as unavailable.
The request goes to mFruit OS's control socket and names this app (from
``WHISPLAY_APP_ID``); it is a convenience, not a permission system.
"""

from __future__ import annotations

import json
import logging
import os
import socket
from typing import NamedTuple, Optional

log = logging.getLogger("mfruit_sdk.background")

SOCKET_NAME = "control.sock"
TIMEOUT_SEC = 3.0


class State(NamedTuple):
    keep_running: bool
    screen_bright: bool


def _socket_path() -> str:
    explicit = os.environ.get("MFRUIT_CONTROL_SOCKET", "")
    if explicit:
        return explicit
    home = os.environ.get("MFRUIT_HOME") or os.path.expanduser("~/.whisplay-os")
    return os.path.join(home, "state", SOCKET_NAME)


def _request(args: dict) -> Optional[State]:
    app_id = args.get("app_id") or os.environ.get("WHISPLAY_APP_ID", "")
    if not app_id:
        log.info("background: no app id (WHISPLAY_APP_ID); not running under mFruit OS")
        return None
    args = dict(args, app_id=app_id)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(TIMEOUT_SEC)
            sock.connect(_socket_path())
            sock.sendall((json.dumps({"cmd": "app.background", "args": args}) + "\n").encode())
            reply = sock.makefile("rb").readline(65536)
        answer = json.loads(reply.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        log.info("background: mFruit OS not reachable: %s", exc)
        return None
    if not isinstance(answer, dict) or not answer.get("ok"):
        log.info("background: mFruit OS did not accept the request: %s",
                 answer.get("error") if isinstance(answer, dict) else answer)
        return None
    return State(bool(answer.get("keep_running")), bool(answer.get("screen_bright")))


def get(app_id: str = "") -> Optional[State]:
    """This app's current switches, or None when mFruit OS cannot say."""
    return _request({"app_id": app_id})


def set(keep_running: Optional[bool] = None, screen_bright: Optional[bool] = None,
        app_id: str = "") -> Optional[State]:
    """Change this app's switches; leave one out to keep it. The new state, or None."""
    args: dict = {"app_id": app_id}
    if keep_running is not None:
        args["keep_running"] = bool(keep_running)
    if screen_bright is not None:
        args["screen_bright"] = bool(screen_bright)
    return _request(args)
