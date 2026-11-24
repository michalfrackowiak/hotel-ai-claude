"""Voice conversation engine.

The browser sends audio chunks; this layer transcribes them, feeds the
transcript to the reservation agent and synthesizes the reply back to audio.
State lives in a :class:`Conversation` row keyed by ``session_key``.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Callable, Optional

from hotel_ai.ai_providers.agent import ReservationAgent
from hotel_ai.ai_providers.factory import get_speech_provider
from hotel_ai.ai_providers.models import Conversation

logger = logging.getLogger(__name__)

OnTokenCallback = Callable[[str], None]


class VoiceConversation:
    """Drives one browser call through the reservation agent."""

    def __init__(self, session_key: str, language: str = "en") -> None:
        self.session_key = session_key
        self.language = language if language in ("en", "pl") else "en"
        self.conversation, _ = Conversation.objects.get_or_create(
            session_key=session_key,
            defaults={"channel": "voice", "language": self.language},
        )
        if self.conversation.language != self.language:
            self.conversation.language = self.language
            self.conversation.save(update_fields=["language"])
        self.agent = ReservationAgent(language=self.language)

    def transcribe(self, audio_bytes: bytes) -> str:
        provider = get_speech_provider("stt", self.language)
        if provider is None:
            return ""
        return provider.transcribe(audio_bytes, language=self.language)

    def speak(self, text: str) -> bytes:
        provider = get_speech_provider("tts", self.language)
        if provider is None:
            return b""
        return provider.speak(text, language=self.language)

    def turn(self, audio_b64: str, mime: str = "audio/webm") -> dict:
        """One full voice turn: audio -> text -> agent -> audio."""
        try:
            audio = base64.b64decode(audio_b64)
        except Exception as exc:  # pragma: no cover - defensive
            return {"error": f"Invalid audio: {exc}", "text": "", "audio_b64": "", "audio_mime": ""}

        transcript = self.transcribe(audio)
        if not transcript:
            return {"error": "no_transcript", "text": "", "audio_b64": "", "audio_mime": ""}

        result = self.agent.handle_message(self.conversation, transcript)
        reply = result.text or ""
        audio_out = self.speak(reply) if reply else b""
        return {
            "transcript": transcript,
            "text": reply,
            "audio_b64": base64.b64encode(audio_out).decode() if audio_out else "",
            "audio_mime": "audio/mpeg" if audio_out else "",
            "action": result.action,
            "booking_id": result.booking_id,
            "error": result.error,
        }

    def stream_turn(
        self, audio_b64: str, on_token: OnTokenCallback | None = None
    ) -> dict:
        """Stream one voice turn: audio -> text -> agent -> audio.

        Yields text tokens via *on_token* as they are generated so the
        frontend can show a preview before synthesis finishes. Returns the
        same dict shape as :meth:`turn` after completion.
        """
        try:
            audio = base64.b64decode(audio_b64)
        except Exception as exc:  # pragma: no cover - defensive
            return {"error": f"Invalid audio: {exc}", "text": "", "audio_b64": "", "audio_mime": ""}

        transcript = self.transcribe(audio)
        if not transcript:
            return {"error": "no_transcript", "text": "", "audio_b64": "", "audio_mime": ""}

        result = self.agent.handle_streaming(self.conversation, transcript, on_token=on_token)
        reply = result.text or ""
        audio_out = self.speak(reply) if reply else b""
        return {
            "transcript": transcript,
            "text": reply,
            "audio_b64": base64.b64encode(audio_out).decode() if audio_out else "",
            "audio_mime": "audio/mpeg" if audio_out else "",
            "action": result.action,
            "booking_id": result.booking_id,
            "error": result.error,
        }


__all__ = ["VoiceConversation"]