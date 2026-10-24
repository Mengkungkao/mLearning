"""The replaceable parts: speech recognition, the teacher's mind, its voice.

The engine talks only to these interfaces; adapters (``openai.py``,
``anthropic_llm.py``, ``fake.py``) implement them. Every adapter failure is
a :class:`ProviderError` with a ``kind`` the user interface can explain
("what failed, what you can do"), never a raw exception from a library.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator, Optional

# Failure kinds, and what the learner is told.
AUTH = "auth"
QUOTA = "quota"
RATE_LIMIT = "rate_limit"
NETWORK = "network"
TIMEOUT = "timeout"
SERVER = "server"
BAD_RESPONSE = "bad_response"
REQUEST = "request"
REFUSED = "refused"
UNAVAILABLE = "unavailable"

_MESSAGES = {
    AUTH: ("{name} refused the key", "Check the API key in the .env file, then Retry."),
    QUOTA: ("{name} quota used up", "Check billing on the provider's website, then Retry."),
    RATE_LIMIT: ("{name} is busy", "Too many requests just now. Wait a moment, then Retry."),
    NETWORK: ("No connection to {name}", "Check Wi-Fi, then Retry. Your lesson is kept."),
    TIMEOUT: ("{name} took too long", "The network may be slow. Retry, or try again later."),
    SERVER: ("{name} had a problem", "This is on the provider's side. Retry in a moment."),
    BAD_RESPONSE: ("Unexpected answer from {name}", "Retry. If it keeps happening, see Details."),
    REQUEST: ("{name} rejected the request", "A setting may be wrong (model name?). See Details."),
    REFUSED: ("{name} declined to answer", "Try saying it differently, or change the topic."),
    UNAVAILABLE: ("{name} is not set up", "See Details for what to add."),
}

RETRYABLE = {RATE_LIMIT, NETWORK, TIMEOUT, SERVER}


class ProviderError(Exception):
    def __init__(self, kind: str, provider: str, detail: str = "", status: Optional[int] = None):
        super().__init__(f"{provider}: {kind}: {detail}" if detail else f"{provider}: {kind}")
        self.kind = kind
        self.provider = provider
        self.detail = detail
        self.status = status

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE

    def explain(self) -> tuple:
        """(short title, what to do) for the screen."""
        title, remedy = _MESSAGES.get(self.kind, ("{name} failed", "Retry, or see Details."))
        return title.format(name=self.provider), remedy


@dataclass
class Transcript:
    text: str
    confidence: Optional[float] = None    # 0..1 when the recogniser reports it
    no_speech: bool = False               # the recogniser thinks nobody spoke
    seconds: float = 0.0

    @property
    def doubtful(self) -> bool:
        return self.confidence is not None and self.confidence < 0.45


class SpeechRecognitionProvider(ABC):
    name = "speech recognition"
    remote = True

    @abstractmethod
    def transcribe(self, pcm: bytes, rate: int = 16000, prompt: str = "") -> Transcript:
        """16-bit mono PCM -> text."""


class LanguageModelProvider(ABC):
    name = "teacher AI"
    remote = True

    @abstractmethod
    def complete_json(self, system: str, messages: list, schema: dict, schema_name: str,
                      max_tokens: int = 1200) -> dict:
        """A JSON object following ``schema``, from a system prompt and the
        conversation (``[{"role": "user"|"assistant", "content": str}]``)."""


class TextToSpeechProvider(ABC):
    name = "voice"
    remote = True
    rate = 24000                          # of the PCM ``stream`` yields

    @abstractmethod
    def stream(self, text: str, voice: str = "", speed: float = 1.0,
               instructions: str = "") -> Iterator[bytes]:
        """16-bit mono PCM chunks at ``rate`` as they arrive."""
