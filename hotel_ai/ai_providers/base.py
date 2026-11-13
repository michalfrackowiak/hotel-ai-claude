"""Provider abstraction layer.

Every backend (OpenAI, Anthropic, Ollama) implements the same small interface
so the agent, the chat and the voice layer can be backend-agnostic:

* ``chat``      - a chat completion (non-streaming)
* ``stream_chat`` - a streaming chat completion yielding text chunks
* ``transcribe``- speech-to-text
* ``speak``     - text-to-speech, returning audio bytes

A provider is constructed from a config dict produced by
``AIProvider.to_config()`` (which merges stored credentials with env vars).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)


class AIProviderError(RuntimeError):
    """Raised when a provider call fails for any reason."""


class BaseAIProvider(ABC):
    """Abstract base class for every AI backend."""

    provider_type: str = "base"

    def __init__(self, config: dict) -> None:
        self.config = config
        self.model = config.get("model", "")
        self.api_key = config.get("api_key", "") or ""
        self.base_url = config.get("base_url", "") or ""
        self.timeout = config.get("timeout_seconds", 30)
        self.extra = config.get("extra_config", {}) or {}

    # ------------------------------------------------------------------
    # chat
    # ------------------------------------------------------------------
    @abstractmethod
    def chat(self, messages: list[dict], **kwargs: Any) -> str:
        """Return the assistant's reply as a string."""

    def stream_chat(self, messages: list[dict], **kwargs: Any) -> Iterator[str]:
        """Yield text chunks as they arrive.

        The default implementation falls back to a single chunk from ``chat``
        when the backend does not support streaming.
        """
        yield self.chat(messages, **kwargs)

    # ------------------------------------------------------------------
    # speech
    # ------------------------------------------------------------------
    def transcribe(self, audio_bytes: bytes, **kwargs: Any) -> str:
        """Speech-to-text. Default: not supported by this backend."""
        raise AIProviderError(
            f"Provider '{self.provider_type}' does not support speech-to-text."
        )

    def speak(self, text: str, **kwargs: Any) -> bytes:
        """Text-to-speech. Default: not supported by this backend."""
        raise AIProviderError(
            f"Provider '{self.provider_type}' does not support text-to-speech."
        )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"<{type(self).__name__} model={self.model!r}>"


def build_messages(system_prompt: str, history: list[dict], user_message: str) -> list[dict]:
    """Assemble a chat message list in the shape every provider expects."""
    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})
    return messages


def coerce_chat_response(result: Any) -> str:
    """Pull the assistant text out of a variety of provider response shapes."""
    if isinstance(result, str):
        return result
    # OpenAI ChatCompletion / Anthropic Message
    try:
        return result.choices[0].message.content  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        return result.content[0].text  # type: ignore[index]
    except Exception:
        pass
    if isinstance(result, dict):
        try:
            return result["choices"][0]["message"]["content"]
        except Exception:
            pass
        try:
            return result["content"][0]["text"]
        except Exception:
            pass
    return str(result)