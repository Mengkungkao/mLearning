"""Local voice activity detection and end-of-turn detection.

Runs on the device, on 16 kHz mono audio in 20 ms frames, before anything
leaves it: audio is only handed on (to speech recognition, which may be
remote) once a whole turn has been heard.

The detector compares each frame's level with a running estimate of the
room's noise floor:

* speech **starts** after ``start_frames`` consecutive frames at least
  ``start_db`` above the floor and above ``min_speech_dbfs``;
* speech **ends** after ``end_silence`` seconds below ``floor + end_db``
  -- the learner's "patience" setting, so a pause to think is not taken as
  the end of the sentence;
* ``preroll`` seconds before the start are kept, so the first syllable
  survives the detection delay;
* a turn longer than ``max_utterance`` is cut there; no speech at all for
  ``no_speech_timeout`` seconds ends listening with a timeout.

The floor follows the room while nobody speaks (quickly downwards, slowly
upwards) and is frozen during speech. When no floor is known yet (the
first turn), the first ``calibrate`` seconds only measure the room. A
"turn" whose level stays almost constant for ``steady_window`` seconds is
a fan, traffic or a hum, not speech (speech moves by many dB between
syllables and words): it is dropped and becomes the new floor, so steady
noise is never sent anywhere. Energy-only detection is simple and
predictable; it cannot tell speech from other irregular loud sounds, which
is why the transcript is checked again afterwards (empty or no-speech
results are dropped).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np

from . import dsp

FRAME_SECONDS = 0.02
FRAME_SAMPLES = int(dsp.SPEECH_RATE * FRAME_SECONDS)

START, END, TIMEOUT = "start", "end", "timeout"


@dataclass
class VadConfig:
    start_db: float = 10.0
    end_db: float = 6.0
    min_speech_dbfs: float = -52.0
    start_frames: int = 4                # 80 ms of loud frames to start
    end_silence: float = 1.3             # seconds of quiet that end the turn
    preroll: float = 0.4
    tail: float = 0.25                   # trailing quiet kept after the speech
    min_speech: float = 0.3              # shorter "speech" is a click, not a turn
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
        self._pending = np.zeros(0, dtype=np.float32)
        self.reset()

    def reset(self, keep_floor: bool = True) -> None:
        cfg = self.config
        if not keep_floor:
            self.floor = cfg.initial_floor
        self.in_speech = False
        self.finished = False
        self._loud_run = 0
        self._quiet_run = 0
        self.noise_rejections = 0
        self._frames_seen = 0
        self._speech_frames = 0
        self._preroll = deque(maxlen=max(1, int(round(cfg.preroll / FRAME_SECONDS))))
        self._turn = []
        self._levels = []
        self._calibration = []
        self._pending = np.zeros(0, dtype=np.float32)

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

    def _frame(self, frame: np.ndarray) -> Optional[VadEvent]:
        cfg = self.config
        self._frames_seen += 1
        level = dsp.rms_dbfs(frame)
        self.level = level
        if self.calibrating:
            self._preroll.append(frame)
            self._calibration.append(level)
            if len(self._calibration) * FRAME_SECONDS >= cfg.calibrate:
                self.floor = min(cfg.floor_max, max(cfg.floor_min, float(np.median(self._calibration))))
                self.calibrating = False
            return None
        loud = level >= self.floor + cfg.start_db and level >= cfg.min_speech_dbfs
        if not self.in_speech:
            self._preroll.append(frame)
            if loud:
                self._loud_run += 1
            else:
                self._loud_run = 0
                self._track_floor(level)
            if self._loud_run >= cfg.start_frames:
                self.in_speech = True
                self._turn = list(self._preroll)
                self._levels = []
                self._speech_frames = self._loud_run
                self._quiet_run = 0
                return VadEvent(START)
            if cfg.no_speech_timeout is not None and self.listening_seconds >= cfg.no_speech_timeout:
                return VadEvent(TIMEOUT)
            return None
        # in speech
        self._turn.append(frame)
        self._levels.append(level)
        window = int(round(cfg.steady_window / FRAME_SECONDS))
        if len(self._levels) >= window and len(self._levels) % 5 == 0:
            recent = np.array(self._levels[-window:])
            # loud the whole time AND nearly constant: speech is neither
            if float(recent.min()) >= self.floor + cfg.end_db and float(recent.std()) < cfg.steady_db:
                # steady noise, not speech: forget it and treat it as the room
                self.floor = min(cfg.floor_max, max(cfg.floor_min, float(recent.mean())))
                self.in_speech = False
                self._turn = []
                self._levels = []
                self._loud_run = 0
                self._quiet_run = 0
                self._speech_frames = 0
                self.noise_rejections += 1
                return None
        continued = level >= self.floor + cfg.end_db and level >= cfg.min_speech_dbfs - 6
        if continued:
            self._quiet_run = 0
            self._speech_frames += 1
        else:
            self._quiet_run += 1
        turn_seconds = len(self._turn) * FRAME_SECONDS
        if self._quiet_run * FRAME_SECONDS >= cfg.end_silence:
            return self._finish(truncated=False)
        if turn_seconds >= cfg.max_utterance:
            return self._finish(truncated=True)
        return None

    def _finish(self, truncated: bool) -> VadEvent:
        cfg = self.config
        frames = self._turn
        if not truncated:
            keep_tail = int(round(cfg.tail / FRAME_SECONDS))
            drop = max(0, self._quiet_run - keep_tail)
            if drop:
                frames = frames[:-drop]
        speech_seconds = self._speech_frames * FRAME_SECONDS
        self.in_speech = False
        if speech_seconds < cfg.min_speech:
            # a click or a cough: not a turn; keep listening
            self.finished = False
            self._turn = []
            self._loud_run = 0
            self._quiet_run = 0
            self._speech_frames = 0
            return None
        pcm = dsp.to_pcm16(np.concatenate(frames)) if frames else b""
        return VadEvent(END, pcm, speech_seconds, truncated)

    def force_end(self) -> Optional[VadEvent]:
        """End the turn now (button released, or listening stopped): the audio so far."""
        if not self.in_speech or not self._turn:
            return None
        pcm = dsp.to_pcm16(np.concatenate(self._turn))
        self.in_speech = False
        self.finished = True
        return VadEvent(END, pcm, self._speech_frames * FRAME_SECONDS, False)

    def _track_floor(self, level: float) -> None:
        cfg = self.config
        if level < self.floor:
            self.floor += (level - self.floor) * 0.3          # follow quieter rooms fast
        else:
            self.floor += (level - self.floor) * 0.02         # louder rooms slowly
        self.floor = min(cfg.floor_max, max(cfg.floor_min, self.floor))
