"""The speaker: streamed speech and short cues through ``aplay``.

Speech arrives as 16-bit mono chunks (OpenAI's ``pcm`` format is 24 kHz)
and is written to ``aplay`` as it arrives, so the teacher starts talking
after the first chunk rather than after the whole reply has downloaded.
It is converted to the card's 48 kHz here (dsp.Upsampler) instead of by
ALSA. One clip plays at a time: ``play`` is serialised, and ``stop`` cuts
the current clip at once (the learner interrupted, or the lesson ended).

``aplay`` exits when the card has played everything written to it (it
drains), so "finished" means the sound has actually left the speaker.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from . import dsp

log = logging.getLogger("mlearning.playback")


def spawn_aplay(device: str, rate: int):
    command = ["aplay", "-q", "-D", device, "-t", "raw", "-f", "S16_LE", "-r", str(rate), "-c", "1", "-"]
    return subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)


@dataclass
class PlayResult:
    completed: bool               # played to the end (not stopped, no error)
    seconds: float = 0.0          # audio written
    error: str = ""
    audio: bytes = b""            # what was played, at the source rate (for "repeat")


class AudioOutput:
    def __init__(self, device: Optional[str], spawn: Callable = spawn_aplay,
                 clock: Callable[[], float] = time.monotonic):
        self.device = device
        self.spawn = spawn
        self.clock = clock
        self._play_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._process = None
        self._stopped = threading.Event()
        self.started_at = 0.0
        self.queued_seconds = 0.0
        self.playing = False

    @property
    def available(self) -> bool:
        return bool(self.device)

    def progress(self) -> float:
        """Seconds of the current clip that have probably been heard."""
        if not self.playing or not self.started_at:
            return 0.0
        return max(0.0, min(self.queued_seconds, self.clock() - self.started_at))

    def play(self, chunks: Iterable[bytes], rate: int, keep: bool = False,
             on_audio: Optional[Callable[[], None]] = None) -> PlayResult:
        """Play 16-bit mono ``chunks`` at ``rate``; blocks until done or stopped.

        ``on_audio`` is called once, when the first audio goes to the card.
        """
        if not self.device:
            return PlayResult(False, error="no speaker")
        with self._play_lock:
            self._stopped.clear()
            upsampler = dsp.Upsampler(dsp.HARDWARE_RATE // rate) if rate in (16000, 24000) else None
            out_rate = dsp.HARDWARE_RATE if upsampler else rate
            kept = []
            written = 0.0
            try:
                process = self.spawn(self.device, out_rate)
            except OSError as exc:
                return PlayResult(False, error=f"cannot start aplay: {exc}")
            with self._state_lock:
                self._process = process
                self.queued_seconds = 0.0
                self.started_at = 0.0
                self.playing = True
            error = ""
            partial = b""
            raise_later = None       # the source's own failure, re-raised after clean-up
            try:
                for chunk in chunks:
                    if self._stopped.is_set():
                        break
                    if not chunk:
                        continue
                    chunk = partial + chunk
                    whole = len(chunk) & ~1
                    chunk, partial = chunk[:whole], chunk[whole:]
                    if keep:
                        kept.append(chunk)
                    data = dsp.to_pcm16(upsampler.process(dsp.from_pcm16(chunk))) if upsampler else chunk
                    if not self.started_at:
                        self.started_at = self.clock()
                        if on_audio is not None:
                            on_audio()
                    process.stdin.write(data)
                    written += dsp.seconds_of(chunk, rate)
                    self.queued_seconds = written
            except (BrokenPipeError, ValueError, OSError) as exc:
                if not self._stopped.is_set():
                    error = f"speaker: {exc.__class__.__name__}"
            except Exception as exc:      # the source failed (network); report, never hang
                error = str(exc) or exc.__class__.__name__
                self._kill(process)
                raise_later = exc
            try:
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            if not self._stopped.is_set() and raise_later is None:
                remaining = max(0.0, written - (self.clock() - self.started_at if self.started_at else 0.0))
                try:
                    process.wait(timeout=remaining + 5.0)
                except subprocess.TimeoutExpired:
                    error = error or "speaker did not finish"
                    self._kill(process)
            else:
                self._kill(process)
            stderr = b""
            try:
                if process.stderr is not None:
                    stderr = process.stderr.read() or b""
            except (OSError, ValueError):
                pass
            code = process.returncode
            with self._state_lock:
                self._process = None
                self.playing = False
            stopped = self._stopped.is_set()
            if not error and not stopped and code not in (0, None):
                said = stderr.decode("utf-8", "replace").strip().splitlines()
                error = said[-1] if said else f"aplay exit code {code}"
            if raise_later is not None:
                raise raise_later
            return PlayResult(not stopped and not error, written, error, b"".join(kept))

    def stop(self) -> None:
        """Cut the current clip now (safe from any thread)."""
        self._stopped.set()
        with self._state_lock:
            process = self._process
        if process is not None:
            self._kill(process)

    @staticmethod
    def _kill(process) -> None:
        try:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=1.0)
        except Exception:
            pass

    def play_pcm(self, pcm: bytes, rate: int) -> PlayResult:
        def chunks():
            step = rate // 5 * 2           # 200 ms
            for start in range(0, len(pcm), step):
                yield pcm[start:start + step]
        return self.play(chunks(), rate)

    def cue(self, kind: str) -> PlayResult:
        """A short, soft sound: "listen" (rising) or "done" (falling)."""
        if kind == "listen":
            pcm = dsp.tone(880, 0.06) + dsp.tone(1175, 0.07)
        elif kind == "done":
            pcm = dsp.tone(988, 0.05) + dsp.tone(740, 0.06)
        else:
            pcm = dsp.tone(330, 0.15)
        return self.play_pcm(pcm, dsp.HARDWARE_RATE)
