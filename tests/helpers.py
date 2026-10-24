"""Shared fakes: audio devices, clock, a temporary data folder."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

import numpy as np

from app import config
from app.audio import dsp
from app.audio.playback import PlayResult
from app.envfile import Secrets
from app.learning.storage import LearningStorage
from app.providers.registry import Providers

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeAudioIn:
    def __init__(self, available: bool = True, start_ok: bool = True):
        self._available = available
        self.start_ok = start_ok
        self.running = False
        self.gate = False
        self.armed_turn = None
        self.vad_config = None
        self.hold_turn = None
        self.hold_pcm = b""
        self.level = -70.0
        self.floor = -60.0
        self.error = ""
        self.starts = 0

    @property
    def available(self):
        return self._available

    def start(self):
        self.starts += 1
        if not self.start_ok:
            self.error = "microphone busy: used by sox (123)"
            return False
        self.running = True
        return True

    def stop(self):
        self.running = False
        self.gate = False
        self.armed_turn = None

    def set_gate(self, open_):
        self.gate = open_

    @property
    def gate_open(self):
        return self.gate

    def listen(self, turn, vad_config):
        self.armed_turn = turn
        self.vad_config = vad_config

    def disarm(self):
        self.armed_turn = None

    def hold_start(self, turn):
        self.hold_turn = turn

    def hold_end(self):
        if self.hold_turn is None:
            return None
        self.hold_turn = None
        return self.hold_pcm

    @property
    def holding(self):
        return self.hold_turn is not None


class FakeAudioOut:
    def __init__(self, available: bool = True, error: str = ""):
        self._available = available
        self.error = error
        self.played = []          # (kind, seconds)
        self.cues = []
        self.stops = 0
        self.playing = False
        self.queued_seconds = 0.0

    @property
    def available(self):
        return self._available

    def progress(self):
        return 0.0

    def play(self, chunks, rate, keep=False, on_audio=None):
        audio = b"".join(chunks)
        self.played.append(("speech", dsp.seconds_of(audio, rate)))
        if self.error:
            return PlayResult(False, 0.0, self.error)
        return PlayResult(True, dsp.seconds_of(audio, rate), "", audio if keep else b"")

    def play_pcm(self, pcm, rate):
        self.played.append(("pcm", dsp.seconds_of(pcm, rate)))
        return PlayResult(True, dsp.seconds_of(pcm, rate), "", b"")

    def cue(self, kind):
        self.cues.append(kind)
        return PlayResult(True, 0.1)

    def stop(self):
        self.stops += 1


def speech_like(seconds: float, rate: int = dsp.SPEECH_RATE, level: float = 0.2,
                seed: int = 1) -> np.ndarray:
    """A voiced signal with syllables (deep level changes), like speech."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * rate)) / rate
    voice = sum(np.sin(2 * np.pi * f * t) / (i + 1) for i, f in enumerate((140, 280, 420, 560, 700)))
    syllables = 0.08 + 0.92 * np.abs(np.sin(2 * np.pi * 2.5 * t + 0.6 * np.sin(2 * np.pi * 0.7 * t)))
    noise = rng.normal(0, 0.05, t.size)
    return (voice * syllables + noise) * level * 32767 / 2.2


def noise(seconds: float, rate: int = dsp.SPEECH_RATE, level_db: float = -62.0,
          seed: int = 2) -> np.ndarray:
    rng = np.random.default_rng(seed)
    amplitude = 32768 * 10 ** (level_db / 20)
    return rng.normal(0, amplitude, int(seconds * rate))


class TempData(unittest.TestCase):
    """A test with its own data folder and default settings."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mlearning-test-")
        self.settings = config.Settings(path=os.path.join(self.tmp, "settings.json"))
        self.secrets = Secrets(os.path.join(self.tmp, ".env"), environ={})
        self.storage = LearningStorage(os.path.join(self.tmp, "learning"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def providers(asr=None, llm=None, tts=None) -> Providers:
    return Providers(asr=asr, llm=llm, tts=tts)
