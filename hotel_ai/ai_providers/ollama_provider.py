"""Ollama / self-hosted connector.

Ollama exposes an OpenAI-compatible HTTP API, so we reuse the same shape but
talk to the local server.  This is the path for fully offline deployments.
"""

from __future__ import annotations

import logging
from typing import Any, Iterator

import requests

from .base import AIProviderError, BaseAIProvider

logger = logging.getLogger(__name__)


class OllamaProvider(BaseAIProvider):
    provider_type = "ollama"

    def _url(self, path: str) -> str:
        base = self.base_url.rstrip("/")
        # "localhost" resolves to ::1 first on most stacks, but the local
        # Ollama daemon only binds 127.0.0.1.  requests then hangs on the
        # IPv6 socket until the read timeout.  Pin the loopback to IPv4.
        if base.replace("https://", "").replace("http://", "").startswith("localhost"):
            base = base.replace("://localhost", "://127.0.0.1", 1)
        return f"{base}{path}"

    def _post(self, path: str, payload: dict) -> dict:
        try:
            response = requests.post(
                self._url(path),
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise AIProviderError(f"Ollama request failed: {exc}") from exc
        return response.json()

    def chat(self, messages: list[dict], **kwargs: Any) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": kwargs.get("temperature", 0.2),
                "num_predict": kwargs.get("max_tokens", 1024),
            },
        }
        data = self._post("/api/chat", payload)
        try:
            return data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise AIProviderError(f"Unexpected Ollama response: {data}") from exc

    def stream_chat(self, messages: list[dict], **kwargs: Any) -> Iterator[str]:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": kwargs.get("temperature", 0.2),
                "num_predict": kwargs.get("max_tokens", 1024),
            },
        }
        try:
            response = requests.post(
                self._url("/api/chat"),
                json=payload,
                timeout=self.timeout,
                stream=True,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise AIProviderError(f"Ollama stream failed: {exc}") from exc
        for line in response.iter_lines():
            if not line:
                continue
            try:
                chunk = line.decode("utf-8")
                data = __import__("json").loads(chunk)
            except Exception:
                continue
            content = data.get("message", {}).get("content")
            if content:
                yield content