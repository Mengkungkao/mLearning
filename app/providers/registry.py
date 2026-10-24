"""Choosing the providers from the settings and the keys that are present.

Nothing is chosen silently: a provider is used only when the settings name
it and its key is present. When something is missing, the reason and the
remedy are reported (the Setup screen shows them) and the parts that need
it are unavailable; mLearning still starts and its offline parts work.
Remote providers are labelled as such, so the screen can say where the
learner's speech goes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..config import Settings
from ..envfile import Secrets
from . import anthropic_llm, openai
from .base import (LanguageModelProvider, ProviderError, SpeechRecognitionProvider,
                   TextToSpeechProvider)


@dataclass
class Problem:
    part: str               # "speech recognition" | "teacher" | "voice"
    title: str
    remedy: str


@dataclass
class Providers:
    asr: Optional[SpeechRecognitionProvider] = None
    llm: Optional[LanguageModelProvider] = None
    tts: Optional[TextToSpeechProvider] = None
    problems: list = field(default_factory=list)

    @property
    def can_teach(self) -> bool:
        """A spoken lesson needs all three."""
        return self.asr is not None and self.llm is not None and self.tts is not None

    @property
    def can_speak(self) -> bool:
        return self.tts is not None

    def remote_names(self) -> list:
        names = []
        for provider in (self.asr, self.llm, self.tts):
            if provider is not None and provider.remote:
                names.append(provider.name)
        return names

    def summary(self) -> list:
        """Lines for the Setup and Privacy screens."""
        lines = []
        for label, provider in (("Speech recognition", self.asr), ("Teacher", self.llm),
                                ("Voice", self.tts)):
            if provider is None:
                lines.append(f"{label}: not set up")
            else:
                where = "online" if provider.remote else "on this device"
                lines.append(f"{label}: {provider.name} ({where})")
        return lines


def build(settings: Settings, secrets: Secrets) -> Providers:
    providers = Providers()
    timeout = float(settings["request_timeout"])
    openai_key = secrets.get("OPENAI_API_KEY")
    client = None
    if openai_key:
        client = openai.OpenAIClient(openai_key, secrets.get("OPENAI_BASE_URL"), timeout)
        providers.asr = openai.OpenAIASR(client, settings["openai_asr_model"])
        providers.tts = openai.OpenAITTS(client, settings["openai_tts_model"], settings["voice"])
    else:
        remedy = f"Add OPENAI_API_KEY to {secrets.path}, then Retry."
        providers.problems.append(Problem("speech recognition", "OpenAI key missing", remedy))
        providers.problems.append(Problem("voice", "OpenAI key missing", remedy))

    if settings["llm_provider"] == "anthropic":
        key = secrets.get("ANTHROPIC_API_KEY")
        missing = anthropic_llm.sdk_problem()
        if not key:
            providers.problems.append(Problem(
                "teacher", "Anthropic key missing",
                f"Add ANTHROPIC_API_KEY to {secrets.path}, or set Teacher AI to OpenAI."))
        elif missing:
            providers.problems.append(Problem(
                "teacher", "Claude is not installed",
                "Install the anthropic package (docs/CONFIGURATION.md), or set Teacher AI to OpenAI."))
        else:
            try:
                providers.llm = anthropic_llm.ClaudeLLM(key, settings["anthropic_model"], timeout)
            except ProviderError as exc:
                providers.problems.append(Problem("teacher", *exc.explain()))
    elif client is not None:
        providers.llm = openai.OpenAILLM(client, settings["openai_llm_model"])
    else:
        providers.problems.append(Problem("teacher", "OpenAI key missing",
                                          f"Add OPENAI_API_KEY to {secrets.path}, then Retry."))
    return providers
