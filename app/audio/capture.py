"""The microphone: one ``arecord`` stream, a gate, and the turn detector.

While a lesson runs (and is not paused) one ``arecord`` stays open at the
card's own format, 48 kHz stereo; RadioConnect measured that the WM8960
needs about 0.7 s to power up its ADC on every cold open, which would cut
the first word of every answer. The stream is converted to 16 kHz mono as
it arrives and goes through a **gate**:

* gate closed (the teacher is speaking, or a cue is playing): the audio is
  thrown away. The teacher's own voice never reaches the detector, so it
  cannot answer itself. Barge-in by voice would need echo cancellation,
  which this hardware path does not have; the button interrupts instead.
* gate open: frames feed the level meter, a short pre-roll ring, and --
  while armed -- the turn detector (hands-free) or the push-to-talk buffer.

Pausing the lesson closes the stream entirely: the microphone is off, not
merely ignored. Callbacks (``on_event``) run on the capture thread and must
only post to the engine.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
import threading
import time
from collections import deque
from typing import Callable, Optional

import numpy as np

from . import devices, dsp
from .vad import END, START, TIMEOUT, Endpointer, VadConfig

log = logging.getLogger("mlearning.capture")

CHUNK_BYTES = 7680               # 40 ms of 48 kHz 16-bit stereo
PREROLL_SECONDS = 0.4

SPEECH_START, UTTERANCE, NO_SPEECH, CAPTURE_ERROR = "speech_start", "utterance", "no_speech", "capture_error"


def spawn_arecord(device: str):
    errors = tempfile.TemporaryFile()
    command = ["arecord", "-q", "-D", device, "-t", "raw", "-f", "S16_LE",
               "-r", str(dsp.HARDWARE_RATE), "-c", "2"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, bufsize=0)
    process.error_file = errors
    return process


class AudioInput:
    """``on_event(kind, turn, payload)``: SPEECH_START, UTTERANCE (VadEvent),
    NO_SPEECH, CAPTURE_ERROR (message)."""

    def __init__(self, device: Optional[str], on_event: Callable, spawn: Callable = spawn_arecord,
                 clock: Callable[[], float] = time.monotonic):
        self.device = device
        self.on_event = on_event
        self.spawn = spawn
        self.clock = clock
        self.level = dsp.SILENCE_DBFS       # last frame level, for the meter
        self.error = ""                     # why the stream last ended by itself
        self.started_at = 0.0
        self._lock = threading.Lock()
        self._process = None
        self._thread = None
        self._stop = threading.Event()
        self._gate = False
        self._decimator = dsp.Decimator()
        self._preroll = deque(maxlen=int(PREROLL_SECONDS / 0.04) + 1)
        self._endpointer: Optional[Endpointer] = None
        self._turn = 0
        self._hold: Optional[list] = None   # push-to-talk buffer while held
        self._floor = None                  # the room's noise floor, kept between turns

    # ---------------------------------------------------------------- stream
    @property
    def available(self) -> bool:
        return bool(self.device)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Open the stream (idempotent). False if it could not start."""
        if not self.device:
            self.error = "no microphone"
            return False
        with self._lock:
            if self.running:
                return True
            try:
                process = self.spawn(self.device)
            except OSError as exc:
                self.error = f"cannot start arecord: {exc}"
                log.error(self.error)
                return False
            self._process = process
            self._stop.clear()
            self.error = ""
            self.started_at = self.clock()
            self._decimator.reset()
            self._preroll.clear()
            self._thread = threading.Thread(target=self._read, args=(process,), name="capture",
                                            daemon=True)
            self._thread.start()
        log.info("microphone open: %s", self.device)
        return True

    def stop(self) -> None:
        """Close the stream: the microphone is off."""
        with self._lock:
            self._stop.set()
            process, self._process = self._process, None
            thread, self._thread = self._thread, None
            self._endpointer = None
            self._hold = None
        if process is not None:
            try:
                process.terminate()
                process.wait(timeout=1.0)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
            error_file = getattr(process, "error_file", None)
            if error_file is not None:
                error_file.close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.5)
        self.level = dsp.SILENCE_DBFS
        log.info("microphone closed")

    # ------------------------------------------------------------- listening
    def set_gate(self, open_: bool) -> None:
        with self._lock:
            self._gate = open_
            if not open_:
                self._preroll.clear()

    @property
    def gate_open(self) -> bool:
        return self._gate

    def listen(self, turn: int, config: VadConfig) -> None:
        """Arm the turn detector for ``turn`` (hands-free)."""
        with self._lock:
            endpointer = Endpointer(config, self._floor)
            self._endpointer = endpointer
            self._turn = turn
            self._hold = None

    def disarm(self) -> None:
        with self._lock:
            if self._endpointer is not None and not self._endpointer.calibrating:
                self._floor = self._endpointer.floor
            self._endpointer = None
            self._hold = None

    def hold_start(self, turn: int) -> None:
        """Push-to-talk pressed: keep everything from the pre-roll on."""
        with self._lock:
            self._endpointer = None
            self._turn = turn
            self._hold = [np.concatenate(list(self._preroll))] if self._preroll else []

    def hold_end(self) -> Optional[bytes]:
        """Push-to-talk released: the audio held, 16 kHz mono PCM (None if not holding)."""
        with self._lock:
            hold, self._hold = self._hold, None
        if hold is None:
            return None
        samples = np.concatenate(hold) if hold else np.zeros(0, dtype=np.float32)
        return dsp.to_pcm16(samples)

    @property
    def holding(self) -> bool:
        return self._hold is not None

    @property
    def armed(self) -> bool:
        return self._endpointer is not None

    @property
    def floor(self) -> float:
        endpointer = self._endpointer
        if endpointer is not None:
            return endpointer.floor
        return self._floor if self._floor is not None else -60.0

    @property
    def in_speech(self) -> bool:
        endpointer = self._endpointer
        return bool(endpointer is not None and endpointer.in_speech)

    # ---------------------------------------------------------------- reader
    def _read(self, process) -> None:
        stream = process.stdout
        partial = b""
        while not self._stop.is_set():
            try:
                data = stream.read(CHUNK_BYTES)
            except (ValueError, OSError):
                break
            if not data:
                break
            data = partial + data
            whole = len(data) - len(data) % 4
            data, partial = data[:whole], data[whole:]
            if data:
                self._process_chunk(data)
        if self._stop.is_set():
            return
        # The stream ended without being asked: say why (another program
        # holding the card says "Device or resource busy").
        reason = self._reason(process)
        self.error = reason
        log.warning("microphone stream ended: %s", reason)
        with self._lock:
            if self._process is process:
                self._process = None
                self._thread = None
            turn = self._turn
        self.on_event(CAPTURE_ERROR, turn, reason)

    def _reason(self, process) -> str:
        try:
            process.wait(timeout=1.0)
        except Exception:
            pass
        said = ""
        error_file = getattr(process, "error_file", None)
        if error_file is not None:
            try:
                error_file.seek(0)
                said = error_file.read().decode("utf-8", "replace").strip()
                error_file.close()
            except (OSError, ValueError):
                said = ""
        last = said.splitlines()[-1] if said else ""
        if "busy" in last.lower():
            who = devices.holder(self.device or "")
            return f"microphone busy: used by {who}" if who else "microphone busy: another program has it"
        code = getattr(process, "returncode", None)
        return last or f"arecord stopped (exit code {code})"

    def _process_chunk(self, data: bytes) -> None:
        samples = self._decimator.process(dsp.to_mono(data))
        with self._lock:
            if not self._gate:
                self.level = dsp.SILENCE_DBFS
                return
            self.level = dsp.rms_dbfs(samples)
            self._preroll.append(samples)
            if self._hold is not None:
                self._hold.append(samples)
                return
            endpointer = self._endpointer
            turn = self._turn
            if endpointer is None:
                return
            events = endpointer.feed(samples)
            if events and events[-1].kind in (END, TIMEOUT) and not endpointer.calibrating:
                self._floor = endpointer.floor
                self._endpointer = None
        for event in events:
            if event.kind == START:
                self.on_event(SPEECH_START, turn, None)
            elif event.kind == END:
                self.on_event(UTTERANCE, turn, event)
            elif event.kind == TIMEOUT:
                self.on_event(NO_SPEECH, turn, None)
