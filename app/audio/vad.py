"""Local voice activity detection and end-of-turn detection.

Runs on the device, on 16 kHz mono audio in 20 ms frames, before anything
leaves it: audio is only handed on (to speech recognition, which may be
remote) once a whole turn has been heard.

**The room.** The noise floor is the median of the last 2 s of quiet
(100 ms smoothed levels), not its quietest moments: a real room on opi2w
moved over about 10 dB (median -46, 95th percentile -37 dBFS, measured
2026-10-24), and a floor at the quiet end made ordinary noise look like
speech, so turns never ended (a 1.8 s sound became a 12 s upload). A short
window lets the floor recover within about a second when it was measured
during a noise (a door, a voice in the next room). When no floor is known
yet, the first ``calibrate`` seconds only measure the room. The floor is
frozen during speech.

**Start.** ``start_frames`` consecutive frames at least ``start_db`` above
the floor and above ``min_speech_dbfs``. ``preroll`` seconds from before the
start are kept, so the first syllable survives the detection delay.

**End.** Over the last ``end_silence`` seconds (the learner's "pause before
reply" setting) at most 20 % of the frames were voiced (smoothed level at
least ``end_db`` above the floor). The tolerance lets a turn end in a room
whose noise often bumps above the threshold (about 10 % of the frames in
the measured room); a pause to think clearly shorter than ``end_silence``
does not end the turn. The turn is cut ``tail``
seconds after its last voiced frame.

**Loud enough.** A turn whose loudest moment (100 ms smoothed) stays below
``floor + peak_db`` is dropped: on opi2w, sounds in the room reached 10 to
15 dB above the floor at random times (2026-10-24), while speech close to
the microphone is louder. ``start_db`` and ``peak_db`` come from the
learner's Mic sensitivity setting (low for noisy rooms).

**Not speech.** A "turn" whose level stays nearly constant for
``steady_window`` seconds is a fan, traffic or a hum (speech moves by many
dB between syllables): it is dropped and becomes the room. Shorter than
``min_speech`` voiced is a click. Energy-only detection cannot tell speech
from other irregular loud sounds, which is why the transcript is checked
again afterwards (empty or no-speech results are dropped).

Limits: a turn longer than ``max_utterance`` is cut there; no speech at all
for ``no_speech_timeout`` seconds ends listening with a timeout.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np

from . import dsp

FRAME_SECONDS = 0.02
FRAME_SAMPLES = int(dsp.SPEECH_RATE * FRAME_SECONDS)
SMOOTH_FRAMES = 5                       # 100 ms
NOISE_FRAMES = 100                      # 2 s of quiet for the floor
FLOOR_PERCENTILE = 50
END_TOLERANCE = 0.20                    # voiced share still allowed in the ending window

START, END, TIMEOUT = "start", "end", "timeout"


@dataclass
class VadConfig:
    start_db: float = 10.0
    peak_db: float = 12.0                # a turn must get this far above the floor
    end_db: float = 8.0
    min_speech_dbfs: float = -52.0
    start_frames: int = 4                # 80 ms of loud frames to start
    end_silence: float = 1.3             # seconds of (mostly) quiet that end the turn
    preroll: float = 0.4
    tail: float = 0.25                   # kept after the last voiced frame
    min_speech: float = 0.3              # less voiced than this is a click, not a turn
    max_utterance: float = 60.0
    no_speech_timeout: Optional[float] = 30.0
    initial_floor: float = -60.0
    floor_min: float = -80.0
    floor_max: float = -28.0
    calibrate: float = 0.3               # seconds to measure the room when no floor is known
    steady_window: float = 1.5           # seconds of near-constant level that mean noise
    steady_db: float = 2.5               # ... "near-constant": standard deviation below this


@dataclass
class VadEvent:
    kind: str                            # START | END | TIMEOUT
    pcm: bytes = b""                     # END: the turn, 16 kHz 16-bit mono
    speech_seconds: float = 0.0
    truncated: bool = False


class Endpointer:
    def __init__(self, config: Optional[VadConfig] = None, floor: Optional[float] = None):
        self.config = config or VadConfig()
        self.floor = self.config.initial_floor if floor is None else floor
        self.calibrating = floor is None
        self.level = dsp.SILENCE_DBFS
        self.noise_rejections = 0
        self.rejected = 0
        self._noise: deque = deque(maxlen=NOISE_FRAMES)
        self._powers: deque = deque(maxlen=SMOOTH_FRAMES)
        self._pending = np.zeros(0, dtype=np.float32)
        self.reset()

    def reset(self, keep_floor: bool = True) -> None:
        cfg = self.config
        if not keep_floor:
            self.floor = cfg.initial_floor
            self._noise.clear()
        self.in_speech = False
        self.finished = False
        self._loud_run = 0
        self._frames_seen = 0
        self._preroll = deque(maxlen=max(1, int(round(cfg.preroll / FRAME_SECONDS))))
        self._pending = np.zeros(0, dtype=np.float32)
        self._clear_turn()

    def _clear_turn(self) -> None:
        cfg = self.config
        self._turn = []
        self._raw = []
        self._smoothed = []
        self._voiced = deque(maxlen=max(1, int(round(cfg.end_silence / FRAME_SECONDS))))
        self._speech_frames = 0
        self._last_voiced = -1

    @property
    def listening_seconds(self) -> float:
        return self._frames_seen * FRAME_SECONDS

    def feed(self, samples: np.ndarray) -> list:
        """Process 16 kHz float samples; returns the events they caused."""
        if self.finished:
            return []
        events = []
        data = np.concatenate([self._pending, samples.astype(np.float32)])
        whole = data.size - data.size % FRAME_SAMPLES
        self._pending = data[whole:]
        for start in range(0, whole, FRAME_SAMPLES):
            event = self._frame(data[start:start + FRAME_SAMPLES])
            if event is not None:
                events.append(event)
                if event.kind in (END, TIMEOUT):
                    self.finished = True
                    break
        return events

    # ------------------------------------------------------------- frames
    def _frame(self, frame: np.ndarray) -> Optional[VadEvent]:
        cfg = self.config
        self._frames_seen += 1
        level = dsp.rms_dbfs(frame)
        self.level = level
        self._powers.append(10 ** (level / 10))
        smoothed = 10 * math.log10(max(1e-12, sum(self._powers) / len(self._powers)))

        if self.calibrating:
            self._preroll.append(frame)
            self._noise.append(smoothed)
            if len(self._noise) * FRAME_SECONDS >= cfg.calibrate:
                self._update_floor()
                self.calibrating = False
            return None

        if not self.in_speech:
            self._preroll.append(frame)
            loud = level >= self.floor + cfg.start_db and level >= cfg.min_speech_dbfs
            if loud:
                self._loud_run += 1
            else:
                self._loud_run = 0
                self._noise.append(smoothed)
                if self._frames_seen % 5 == 0:
                    self._update_floor()
            if self._loud_run >= cfg.start_frames:
                self.in_speech = True
                self._clear_turn()
                self._turn = list(self._preroll)
                self._speech_frames = self._loud_run
                self._last_voiced = len(self._turn) - 1
                self._loud_run = 0
                return VadEvent(START)
            if cfg.no_speech_timeout is not None and self.listening_seconds >= cfg.no_speech_timeout:
                return VadEvent(TIMEOUT)
            return None

        # in speech
        self._turn.append(frame)
        self._raw.append(level)
        self._smoothed.append(smoothed)
        voiced = smoothed >= self.floor + cfg.end_db and smoothed >= cfg.min_speech_dbfs - 6
        self._voiced.append(voiced)
        if voiced:
            self._speech_frames += 1
            self._last_voiced = len(self._turn) - 1
        if self._steady_noise():
            return None
        window_full = len(self._voiced) == self._voiced.maxlen
        if window_full and sum(self._voiced) <= END_TOLERANCE * self._voiced.maxlen:
            return self._finish(truncated=False)
        if len(self._turn) * FRAME_SECONDS >= cfg.max_utterance:
            return self._finish(truncated=True)
        return None

    def _update_floor(self) -> None:
        if len(self._noise) < 5:
            return
        cfg = self.config
        value = float(np.percentile(np.array(self._noise), FLOOR_PERCENTILE))
        self.floor = min(cfg.floor_max, max(cfg.floor_min, value))

    def _steady_noise(self) -> bool:
        """Loud the whole time and nearly constant: a fan or a hum, not speech."""
        cfg = self.config
        window = int(round(cfg.steady_window / FRAME_SECONDS))
        if len(self._raw) < window or len(self._raw) % 5:
            return False
        raw = np.array(self._raw[-window:])
        smoothed = np.array(self._smoothed[-window:])
        if float(smoothed.min()) < self.floor + cfg.end_db or float(raw.std()) >= cfg.steady_db:
            return False
        self._noise.extend(smoothed.tolist())
        self._update_floor()
        self.in_speech = False
        self._loud_run = 0
        self.noise_rejections += 1
        self._clear_turn()
        return True

    def _finish(self, truncated: bool) -> Optional[VadEvent]:
        cfg = self.config
        frames = self._turn
        if not truncated:
            keep = self._last_voiced + 1 + int(round(cfg.tail / FRAME_SECONDS))
            frames = frames[:max(1, keep)]
        speech_seconds = self._speech_frames * FRAME_SECONDS
        loudest = max(self._smoothed) if self._smoothed else dsp.SILENCE_DBFS
        self.in_speech = False
        if speech_seconds < cfg.min_speech or loudest < self.floor + cfg.peak_db:
            self.rejected += 1           # a click, a cough, a sound across the room
            self._clear_turn()
            self._loud_run = 0
            return None
        pcm = dsp.to_pcm16(np.concatenate(frames)) if frames else b""
        self._clear_turn()
        return VadEvent(END, pcm, speech_seconds, truncated)

    def force_end(self) -> Optional[VadEvent]:
        """End the turn now (listening stopped): the audio so far."""
        if not self.in_speech or not self._turn:
            return None
        pcm = dsp.to_pcm16(np.concatenate(self._turn))
        speech_seconds = self._speech_frames * FRAME_SECONDS
        self.in_speech = False
        self.finished = True
        self._clear_turn()
        return VadEvent(END, pcm, speech_seconds, False)
