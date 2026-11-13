"""OpenAI connector (chat + audio transcription + text-to-speech)."""

from __future__ import annotations

import io
import logging
from typing import Any, Iterator

import openai

from .base import AIProviderError, BaseAIProvider, coerce_chat_response

logger = logging.getLogger(__name__)


class OpenAIProvider(BaseAIProvider):
    provider_type = "openai"

    def _client(self) -> openai.OpenAI:
        kwargs: dict[str, Any] = {"api_key": self.api_key, "timeout": self.timeout}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return openai.OpenAI(**kwargs)

    def chat(self, messages: list[dict], **kwargs: Any) -> str:
        client = self._client()
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=kwargs.get("temperature", 0.2),
                max_tokens=kwargs.get("max_tokens", 1024),
            )
        except openai.OpenAIError as exc:
            raise AIProviderError(f"OpenAI chat failed: {exc}") from exc
        return coerce_chat_response(response)

    def stream_chat(self, messages: list[dict], **kwargs: Any) -> Iterator[str]:
        client = self._client()
        try:
            stream = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=kwargs.get("temperature", 0.2),
                max_tokens=kwargs.get("max_tokens", 1024),
                stream=True,
            )
        except openai.OpenAIError as exc:
            raise AIProviderError(f"OpenAI stream failed: {exc}") from exc
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content

    def transcribe(self, audio_bytes: bytes, **kwargs: Any) -> str:
        client = self._client()
        model = kwargs.get("model", "whisper-1")
        try:
            transcript = client.audio.transcriptions.create(
                model=model,
                file=("audio.wav", io.BytesIO(audio_bytes), "audio/wav"),
                language=kwargs.get("language"),
            )
        except openai.OpenAIError as exc:
            raise AIProviderError(f"OpenAI transcription failed: {exc}") from exc
        return getattr(transcript, "text", str(transcript))

    def speak(self, text: str, **kwargs: Any) -> bytes:
        client = self._client()
        model = kwargs.get("model", "tts-1")
        voice = kwargs.get("voice", "alloy")
        try:
            response = client.audio.speech.create(
                model=model,
                voice=voice,
                input=text,
                response_format=kwargs.get("format", "mp3"),
            )
        except openai.OpenAIError as exc:
            raise AIProviderError(f"OpenAI TTS failed: {exc}") from exc
        return response.content