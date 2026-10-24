"""Audio arithmetic: channel mixing, rate conversion, levels, WAV, tones.

The Whisplay card runs at 48 kHz stereo and is opened that way: asked for
one channel, the card on an Orange Pi Zero 2W returned the microphone about
35 dB down (measured by RadioConnect, 2026-10-22). So the two channels are
averaged here, and the rate converted with a proper low-pass filter rather
than ALSA's linear interpolation:

* capture: 48 kHz stereo -> 16 kHz mono, what speech recognition wants;
* playback: 24 kHz mono speech (OpenAI's ``pcm`` format) -> 48 kHz.

Both converters are streaming: they keep the filter's history between
chunks, so audio can be converted as it arrives. numpy only (no scipy on
the boards).
"""

from __future__ import annotations

import io
import math
import wave

import numpy as np

HARDWARE_RATE = 48000
SPEECH_RATE = 16000
TTS_RATE = 24000
SILENCE_DBFS = -120.0


def lowpass(taps: int, cutoff: float) -> np.ndarray:
    """Windowed-sinc low-pass FIR; ``cutoff`` in cycles per sample (0..0.5)."""
    n = np.arange(taps) - (taps - 1) / 2.0
    h = 2 * cutoff * np.sinc(2 * cutoff * n) * np.kaiser(taps, 8.0)
    return h / h.sum()


def to_mono(frames: bytes) -> np.ndarray:
    """Interleaved 16-bit stereo bytes -> mono float32 samples (the average)."""
    x = np.frombuffer(frames[: len(frames) & ~3], dtype="<i2").astype(np.float32)
    return (x[0::2] + x[1::2]) * 0.5


def channel_peaks(frames: bytes) -> tuple:
    """(left, right) peak in dBFS of interleaved 16-bit stereo bytes."""
    x = np.frombuffer(frames[: len(frames) & ~3], dtype="<i2").astype(np.int32)
    if not x.size:
        return SILENCE_DBFS, SILENCE_DBFS
    return dbfs_peak(np.abs(x[0::2]).max()), dbfs_peak(np.abs(x[1::2]).max())


def dbfs_peak(value: float) -> float:
    return 20 * math.log10(value / 32768.0) if value > 0 else SILENCE_DBFS


def rms_dbfs(samples: np.ndarray) -> float:
    """RMS level of float samples (int16 scale) in dBFS, DC removed."""
    if samples.size == 0:
        return SILENCE_DBFS
    centred = samples - float(samples.mean())
    power = float(np.mean(centred * centred))
    if power <= 1e-9:
        return SILENCE_DBFS
    return 10 * math.log10(power / (32768.0 * 32768.0))


def to_pcm16(samples: np.ndarray) -> bytes:
    return np.clip(np.round(samples), -32768, 32767).astype("<i2").tobytes()


def from_pcm16(data: bytes) -> np.ndarray:
    return np.frombuffer(data[: len(data) & ~1], dtype="<i2").astype(np.float32)


class Decimator:
    """Streaming low-pass + keep every ``factor``-th sample (48 kHz -> 16 kHz)."""

    def __init__(self, factor: int = 3, taps: int = 97, cutoff_hz: float = 7200.0,
                 rate: int = HARDWARE_RATE):
        self.factor = factor
        self.h = lowpass(taps, cutoff_hz / rate).astype(np.float32)
        self._history = np.zeros(taps - 1, dtype=np.float32)
        self._phase = 0          # samples to skip before the next kept output

    def process(self, samples: np.ndarray) -> np.ndarray:
        if samples.size == 0:
            return samples
        x = np.concatenate([self._history, samples.astype(np.float32)])
        y = np.convolve(x, self.h, mode="valid")          # one output per new input
        self._history = x[-(self.h.size - 1):]
        out = y[self._phase::self.factor]
        consumed = y.size - self._phase
        self._phase = (-consumed) % self.factor
        return out

    def reset(self) -> None:
        self._history[:] = 0
        self._phase = 0


class Upsampler:
    """Streaming zero-stuff + low-pass by an integer ``factor`` (24 kHz -> 48 kHz)."""

    def __init__(self, factor: int = 2, taps_per_phase: int = 24, rate_out: int = HARDWARE_RATE,
                 cutoff_hz: float = 11000.0):
        self.factor = factor
        taps = taps_per_phase * factor
        h = lowpass(taps, cutoff_hz / rate_out) * factor
        self.phases = [h[p::factor].astype(np.float32) for p in range(factor)]
        self._history = np.zeros(taps_per_phase - 1, dtype=np.float32)

    def process(self, samples: np.ndarray) -> np.ndarray:
        if samples.size == 0:
            return samples
        x = np.concatenate([self._history, samples.astype(np.float32)])
        out = np.empty((samples.size, self.factor), dtype=np.float32)
        for p, h in enumerate(self.phases):
            out[:, p] = np.convolve(x, h, mode="valid")
        self._history = x[-(self._history.size):] if self._history.size else self._history
        return out.reshape(-1)


def wav_bytes(pcm: bytes, rate: int = SPEECH_RATE, channels: int = 1) -> bytes:
    """16-bit PCM wrapped in a WAV container, in memory."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buffer.getvalue()


def tone(frequency: float, seconds: float, rate: int = HARDWARE_RATE, volume: float = 0.18) -> bytes:
    """A short sine with soft edges (no click), 16-bit mono."""
    count = int(seconds * rate)
    t = np.arange(count) / rate
    edge = max(1, int(0.012 * rate))
    envelope = np.minimum(1.0, np.minimum(np.arange(count) / edge, (count - np.arange(count)) / edge))
    return to_pcm16(np.sin(2 * np.pi * frequency * t) * envelope * volume * 32767)


def seconds_of(pcm: bytes, rate: int) -> float:
    return len(pcm) / 2.0 / rate
