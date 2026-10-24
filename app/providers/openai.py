"""OpenAI (or an OpenAI-compatible server): speech recognition, teacher, voice.

Standard library HTTP only. The defaults are the models AI Chatbot already
uses on the owner's devices (whisper-1, gpt-4o, tts-1-hd with "nova"); all
are settings.

Speech recognition is asked for a *verbatim* transcript: a learner's
mistakes are the point, and Whisper otherwise tends to tidy grammar. Its
prompt is a style example that contains mistakes and fillers, which is how
Whisper's prompt works (it imitates style, it does not follow
instructions). ``verbose_json`` gives per-segment confidence, used to tell
"the recogniser was unsure" apart from "the learner made a mistake", and
``no_speech_prob`` to drop Whisper's known inventions on noise
("Thanks for watching!").
"""

from __future__ import annotations

import json
import logging
import math
from typing import Iterator

from . import http
from .base import (BAD_RESPONSE, REFUSED, REQUEST, LanguageModelProvider, ProviderError,
                   SpeechRecognitionProvider, TextToSpeechProvider, Transcript)
from ..audio import dsp

log = logging.getLogger("mlearning.openai")

DEFAULT_BASE_URL = "https://api.openai.com/v1"
NAME = "OpenAI"

VERBATIM_PROMPT = ("Um, yesterday I go to the shop with my friend and, uh, we buyed some "
                   "bread. I am live here since two years.")


class OpenAIClient:
    def __init__(self, api_key: str, base_url: str = "", timeout: float = 30.0):
        self.api_key = api_key
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.name = NAME if self.base_url == DEFAULT_BASE_URL else f"{NAME}-compatible server"

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"


class OpenAIASR(SpeechRecognitionProvider):
    def __init__(self, client: OpenAIClient, model: str = "whisper-1", language: str = "en"):
        self.client = client
        self.model = model
        self.language = language
        self.name = f"{client.name} speech recognition"

    def transcribe(self, pcm: bytes, rate: int = 16000, prompt: str = "") -> Transcript:
        verbose = self.model.startswith("whisper")
        fields = {"model": self.model, "response_format": "verbose_json" if verbose else "json",
                  "prompt": (prompt + " " + VERBATIM_PROMPT).strip()[-800:]}
        if self.language:
            fields["language"] = self.language
        files = {"file": ("speech.wav", dsp.wav_bytes(pcm, rate), "audio/wav")}
        data = http.post_multipart(self.client.name, self.client.url("audio/transcriptions"),
                                   fields, files, self.client.headers, self.client.timeout)
        return parse_transcription(data, dsp.seconds_of(pcm, rate), self.client.name)


def parse_transcription(data: dict, seconds: float = 0.0, provider: str = NAME) -> Transcript:
    if not isinstance(data, dict) or not isinstance(data.get("text", ""), str):
        raise ProviderError(BAD_RESPONSE, provider, "transcription without text")
    text = " ".join(data.get("text", "").split())
    segments = data.get("segments") or []
    confidence = None
    no_speech = not text
    if segments:
        logprobs = [s.get("avg_logprob") for s in segments if isinstance(s.get("avg_logprob"), (int, float))]
        if logprobs:
            confidence = max(0.0, min(1.0, math.exp(sum(logprobs) / len(logprobs))))
        silent = [s for s in segments
                  if (s.get("no_speech_prob") or 0) > 0.6 and (s.get("avg_logprob") or 0) < -1.0]
        if len(silent) == len(segments):
            no_speech = True
    return Transcript(text, confidence, no_speech, seconds)


class OpenAILLM(LanguageModelProvider):
    def __init__(self, client: OpenAIClient, model: str = "gpt-4o", temperature: float = 0.6):
        self.client = client
        self.model = model
        self.temperature = temperature
        self.name = f"{client.name} teacher"
        self._json_schema = True        # False once a server says it cannot do json_schema

    def complete_json(self, system: str, messages: list, schema: dict, schema_name: str,
                      max_tokens: int = 1200) -> dict:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + list(messages),
            "temperature": self.temperature,
            "max_tokens": max_tokens,
        }
        if self._json_schema:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": schema_name, "strict": True,
                                                       "schema": schema}}
        else:
            body["response_format"] = {"type": "json_object"}
        try:
            data = http.post_json(self.client.name, self.client.url("chat/completions"), body,
                                  self.client.headers, self.client.timeout)
        except ProviderError as exc:
            # Some OpenAI-compatible servers only know json_object.
            if exc.kind == REQUEST and self._json_schema and "response_format" in exc.detail:
                log.info("server has no json_schema output; using json_object")
                self._json_schema = False
                return self.complete_json(system, messages, schema, schema_name, max_tokens)
            raise
        return parse_chat_json(data, self.client.name)


def parse_chat_json(data: dict, provider: str = NAME) -> dict:
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise ProviderError(BAD_RESPONSE, provider, "no choices in the answer") from None
    if message.get("refusal"):
        raise ProviderError(REFUSED, provider, str(message["refusal"])[:200])
    content = message.get("content") or ""
    try:
        parsed = json.loads(content)
    except ValueError:
        raise ProviderError(BAD_RESPONSE, provider, "teacher's answer was not JSON") from None
    if not isinstance(parsed, dict):
        raise ProviderError(BAD_RESPONSE, provider, "teacher's answer was not an object")
    finish = data["choices"][0].get("finish_reason")
    if finish == "length":
        log.warning("teacher answer hit the token limit")
    return parsed


class OpenAITTS(TextToSpeechProvider):
    rate = dsp.TTS_RATE

    def __init__(self, client: OpenAIClient, model: str = "tts-1-hd", voice: str = "nova"):
        self.client = client
        self.model = model
        self.voice = voice
        self.name = f"{client.name} voice"

    def stream(self, text: str, voice: str = "", speed: float = 1.0, instructions: str = "",
               cancelled=None) -> Iterator[bytes]:
        body = {"model": self.model, "voice": voice or self.voice, "input": text[:4000],
                "response_format": "pcm", "speed": max(0.25, min(4.0, speed))}
        # Delivery instructions are understood by the gpt-4o voices only;
        # tts-1 and tts-1-hd reject the field.
        if instructions and ("gpt-4o" in self.model or "mini-tts" in self.model):
            body["instructions"] = instructions
        return http.post_stream(self.client.name, self.client.url("audio/speech"), body,
                                self.client.headers, self.client.timeout, cancelled=cancelled)
