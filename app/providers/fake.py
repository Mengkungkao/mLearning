"""Scripted providers for tests and screen previews. They never touch the network."""

from __future__ import annotations

import threading
import time
from typing import Callable, Iterator, Optional

from .base import (LanguageModelProvider, ProviderError, SpeechRecognitionProvider,
                   TextToSpeechProvider, Transcript)


class FakeASR(SpeechRecognitionProvider):
    name = "fake speech recognition"
    remote = False

    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []

    def transcribe(self, pcm: bytes, rate: int = 16000, prompt: str = "") -> Transcript:
        self.calls.append((len(pcm), prompt))
        result = self.results.pop(0) if self.results else Transcript("")
        if isinstance(result, Exception):
            raise result
        if isinstance(result, str):
            return Transcript(result, 0.9)
        return result


class FakeLLM(LanguageModelProvider):
    name = "fake teacher"
    remote = False

    def __init__(self, replies=None, respond: Optional[Callable] = None):
        self.replies = list(replies or [])
        self.respond = respond
        self.calls = []
        self.gate: Optional[threading.Event] = None   # tests: hold the answer until set

    def complete_json(self, system: str, messages: list, schema: dict, schema_name: str,
                      max_tokens: int = 1200) -> dict:
        self.calls.append({"system": system, "messages": [dict(m) for m in messages],
                           "schema": schema_name})
        if self.gate is not None:
            self.gate.wait(5.0)
        if self.respond is not None:
            return self.respond(system, messages, schema_name)
        reply = self.replies.pop(0) if self.replies else {"reply_text": "Tell me more."}
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeTTS(TextToSpeechProvider):
    name = "fake voice"
    remote = False
    rate = 24000

    def __init__(self, seconds_per_char: float = 0.002, fail: Optional[ProviderError] = None,
                 pace: float = 0.0):
        self.seconds_per_char = seconds_per_char
        self.fail = fail
        self.pace = pace
        self.calls = []

    def stream(self, text: str, voice: str = "", speed: float = 1.0, instructions: str = "",
               cancelled=None) -> Iterator[bytes]:
        self.calls.append({"text": text, "voice": voice, "speed": speed})
        if self.fail is not None:
            raise self.fail
        total = int(len(text) * self.seconds_per_char / speed * self.rate) * 2
        step = 4800
        for start in range(0, total, step):
            if cancelled is not None and cancelled():
                return
            if self.pace:
                time.sleep(self.pace)
            yield b"\x00" * min(step, total - start)
