"""Finding the microphone and the speaker.

On a Whisplay HAT the driver's ``/etc/asound.conf`` defines two shared
devices on the WM8960 card: ``capture`` (dsnoop) and ``playback`` (dmix),
both 48 kHz stereo underneath. They are preferred because they never fail
with "Device or resource busy" when something else has the card open.
Without them the Whisplay card is opened directly (``plughw:N``); without
a Whisplay card, the first card that is not HDMI. HDMI cards are skipped:
the Orange Pi's ``ahubhdmi`` even lists itself as a capture device.

``auto`` is the default; an explicit device in the settings is used as is
(a USB headset, for instance).
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Optional

WHISPLAY_DRIVER_DIR = "/usr/local/share/whisplay"
_CARD_LINE = re.compile(r"^card (\d+): (\S+) \[")
_WHISPLAY = ("whisplay", "wm8960", "es8389")
_NOT_AUDIO = ("hdmi", "loopback")


def _run(command: list) -> str:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def cards(kind: str) -> list:
    """[(index, name)] from ``arecord -l`` (capture) or ``aplay -l`` (playback)."""
    out = _run(["arecord" if kind == "capture" else "aplay", "-l"])
    found = []
    for line in out.splitlines():
        match = _CARD_LINE.match(line)
        if match:
            entry = (int(match.group(1)), match.group(2))
            if entry not in found:
                found.append(entry)
    return found


def pcm_names(kind: str) -> set:
    """Names ALSA lists with ``-L`` (defined PCMs such as ``capture``)."""
    out = _run(["arecord" if kind == "capture" else "aplay", "-L"])
    return {line.strip() for line in out.splitlines() if line and not line[0].isspace()}


def resolve(kind: str, configured: str = "auto", card_list=None, names=None,
            driver_dir: str = WHISPLAY_DRIVER_DIR) -> tuple:
    """(device or None, description). ``kind`` is "capture" or "playback"."""
    if configured and configured != "auto":
        return configured, f"{configured} (from settings)"
    card_list = cards(kind) if card_list is None else card_list
    usable = [(i, n) for i, n in card_list if not any(w in n.lower() for w in _NOT_AUDIO)]
    whisplay = [(i, n) for i, n in usable if any(w in n.lower() for w in _WHISPLAY)]
    if whisplay:
        names = pcm_names(kind) if names is None else names
        shared = "capture" if kind == "capture" else "playback"
        if shared in names:
            return shared, f"{whisplay[0][1]} (shared)"
        return f"plughw:{whisplay[0][0]}", whisplay[0][1]
    if os.path.isdir(driver_dir):
        return None, "Whisplay sound card missing"
    if usable:
        return f"plughw:{usable[0][0]}", usable[0][1]
    if kind == "playback":
        return "default", "default output"
    return None, "no microphone found"


def card_index(device: str) -> Optional[int]:
    match = re.search(r"(?:hw|plughw):(\d+)", device or "")
    return int(match.group(1)) if match else None


def holder(device: str, proc: str = "/proc") -> str:
    """Name and PID of one of our processes holding the capture device, or "".

    Only processes we may look into are seen (our own user's), which is
    where a recorder left behind by another app would be.
    """
    index = card_index(device)
    if index is None:
        return ""
    target = f"/dev/snd/pcmC{index}D0c"
    try:
        pids = [entry for entry in os.listdir(proc) if entry.isdigit()]
    except OSError:
        return ""
    for pid in pids:
        if int(pid) == os.getpid():
            continue
        fd_dir = os.path.join(proc, pid, "fd")
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                if os.readlink(os.path.join(fd_dir, fd)) != target:
                    continue
            except OSError:
                continue
            try:
                with open(os.path.join(proc, pid, "comm")) as handle:
                    name = handle.read().strip()
            except OSError:
                name = "a program"
            return f"{name} ({pid})"
    return ""
