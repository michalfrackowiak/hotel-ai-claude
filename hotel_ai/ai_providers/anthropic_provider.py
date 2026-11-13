"""Anthropic connector (chat only - audio is handled by OpenAI-compatible STT/TTS)."""

from __future__ import annotations

import logging
from typing import Any, Iterator

import anthropic

from .base import AIProviderError, BaseAIProvider, coerce_chat_response

logger = logging.getLogger(__name__)


class AnthropicProvider(BaseAIProvider):
    provider_type = "anthropic"

    def _client(self) -> anthropic.Anthropic:
        kwargs: dict[str, Any] = {"api_key": self.api_key, "timeout": self.timeout}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return anthropic.Anthropic(**kwargs)

    def chat(self, messages: list[dict], **kwargs: Any) -> str:
        client = self._client()
        # Anthropic wants system out of the messages list.
        system = ""
        convo = []
        for m in messages:
            if m.get("role") == "system":
                system = m.get("content", "")
            else:
                convo.append(m)
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=kwargs.get("max_tokens", 1024),
                system=system or None,
                messages=convo,
                temperature=kwargs.get("temperature", 0.2),
            )
        except anthropic.APIStatusError as exc:
            raise AIProviderError(f"Anthropic chat failed: {exc}") from exc
        return coerce_chat_response(response)

    def stream_chat(self, messages: list[dict], **kwargs: Any) -> Iterator[str]:
        client = self._client()
        system = ""
        convo = []
        for m in messages:
            if m.get("role") == "system":
                system = m.get("content", "")
            else:
                convo.append(m)
        try:
            with client.messages.stream(
                model=self.model,
                max_tokens=kwargs.get("max_tokens", 1024),
                system=system or None,
                messages=convo,
                temperature=kwargs.get("temperature", 0.2),
            ) as stream:
                for text in stream.text_stream:
                    yield text
        except anthropic.APIStatusError as exc:
            raise AIProviderError(f"Anthropic stream failed: {exc}") from exc