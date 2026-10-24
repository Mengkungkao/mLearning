"""Claude as the teacher, through the official Anthropic Python SDK.

Optional: the ``anthropic`` package is imported only when Settings >
Teacher AI is Claude, and its absence is reported as "not set up" with
what to install (docs/CONFIGURATION.md). Anthropic has no speech API, so
speech recognition and the voice stay with OpenAI either way.

Defaults: Claude Opus 5.5 at low effort (a spoken lesson needs short,
quick turns; thinking cannot be switched off on this model, effort is the
control), structured JSON output, and server-side refusal fallback
(``fallbacks: "default"``), so a request a safety classifier declines is
re-run on the recommended fallback model instead of failing the turn.
"""

from __future__ import annotations

import json
import logging

from .base import (AUTH, BAD_RESPONSE, NETWORK, QUOTA, RATE_LIMIT, REFUSED, REQUEST, SERVER,
                   TIMEOUT, UNAVAILABLE, LanguageModelProvider, ProviderError)

log = logging.getLogger("mlearning.anthropic")

NAME = "Claude"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def sdk_problem() -> str:
    """"" when the SDK can be imported, else what is missing."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return "the anthropic Python package is not installed"
    return ""


class ClaudeLLM(LanguageModelProvider):
    def __init__(self, api_key: str, model: str = "claude-opus-5-5", timeout: float = 30.0,
                 effort: str = "low", client=None):
        self.model = model
        self.effort = effort
        self.name = "Claude teacher"
        if client is None:
            try:
                import anthropic
            except ImportError:
                raise ProviderError(UNAVAILABLE, NAME, sdk_problem()) from None
            client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=1)
        self.client = client

    def complete_json(self, system: str, messages: list, schema: dict, schema_name: str,
                      max_tokens: int = 16000) -> dict:
        try:
            import anthropic
            errors = anthropic
        except ImportError:          # a fake client in tests
            errors = None
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=max(max_tokens, 16000),
                system=system,
                messages=list(messages),
                output_config={"effort": self.effort,
                               "format": {"type": "json_schema", "schema": schema}},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except Exception as exc:
            raise _translate(exc, errors) from None
        if getattr(response, "stop_reason", None) == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise ProviderError(REFUSED, NAME, f"declined ({category or 'no category'})")
        text = next((block.text for block in response.content if getattr(block, "type", "") == "text"), "")
        try:
            parsed = json.loads(text)
        except ValueError:
            raise ProviderError(BAD_RESPONSE, NAME, "teacher's answer was not JSON") from None
        if not isinstance(parsed, dict):
            raise ProviderError(BAD_RESPONSE, NAME, "teacher's answer was not an object")
        if getattr(response, "stop_reason", None) == "max_tokens":
            log.warning("Claude's answer hit max_tokens")
        return parsed


def _translate(exc: Exception, errors) -> ProviderError:
    """Map the SDK's typed errors, most specific first, to a ProviderError kind."""
    if errors is None:
        return ProviderError(NETWORK, NAME, str(exc)[:200])
    message = str(getattr(exc, "message", "") or exc)[:300]
    if isinstance(exc, errors.AuthenticationError) or isinstance(exc, errors.PermissionDeniedError):
        return ProviderError(AUTH, NAME, message, 401)
    if isinstance(exc, errors.NotFoundError) or isinstance(exc, errors.BadRequestError):
        return ProviderError(REQUEST, NAME, message, getattr(exc, "status_code", 400))
    if isinstance(exc, errors.RateLimitError):
        kind = QUOTA if "credit" in message.lower() or "billing" in message.lower() else RATE_LIMIT
        return ProviderError(kind, NAME, message, 429)
    if isinstance(exc, errors.APITimeoutError):
        return ProviderError(TIMEOUT, NAME, "no answer in time")
    if isinstance(exc, errors.APIStatusError):
        status = getattr(exc, "status_code", 500)
        return ProviderError(SERVER if status >= 500 else REQUEST, NAME, message, status)
    if isinstance(exc, errors.APIConnectionError):
        return ProviderError(NETWORK, NAME, message)
    return ProviderError(BAD_RESPONSE, NAME, f"{exc.__class__.__name__}: {message}")
