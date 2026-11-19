"""Tests for the voice conversation engine."""

from __future__ import annotations

import base64
import datetime as _dt
from unittest import mock

import pytest

from hotel_ai.bookings.models import Booking, BookingStatus, Client, Room, RoomType
from hotel_ai.bookings.services import create_booking

from .conversation import VoiceConversation


class _FakeProvider:
    """Minimal stand-in for the speech provider returned by get_speech_provider."""

    provider_type = "fake"

    def __init__(self, transcript="", audio=b""):
        self._transcript = transcript
        self._audio = audio

    def transcribe(self, audio_bytes, **kwargs):
        return self._transcript

    def speak(self, text, **kwargs):
        return self._audio


def _make_conversation(session_key, language="en"):
    """Build a VoiceConversation with the speech provider mocked out."""
    vc = VoiceConversation.__new__(VoiceConversation)
    vc.session_key = session_key
    vc.language = language
    vc.conversation, _ = __import__(
        "hotel_ai.ai_providers.models", fromlist=["Conversation"]
    ).Conversation.objects.get_or_create(
        session_key=session_key,
        defaults={"channel": "voice", "language": language},
    )
    vc.agent = mock.MagicMock()
    return vc


@pytest.mark.django_db
def test_transcribe_returns_empty_when_no_provider():
    vc = _make_conversation("voice-no-provider")
    with mock.patch("hotel_ai.voice.conversation.get_speech_provider", return_value=None):
        assert vc.transcribe(b"audio") == ""


@pytest.mark.django_db
def test_speak_returns_empty_when_no_provider():
    vc = _make_conversation("voice-no-tts")
    with mock.patch("hotel_ai.voice.conversation.get_speech_provider", return_value=None):
        assert vc.speak("hello") == b""


@pytest.mark.django_db
def test_transcribe_delegates_to_provider():
    vc = _make_conversation("voice-stt")
    provider = _FakeProvider(transcript="hello there")
    with mock.patch(
        "hotel_ai.voice.conversation.get_speech_provider", return_value=provider
    ):
        assert vc.transcribe(b"audio") == "hello there"


@pytest.mark.django_db
def test_speak_delegates_to_provider():
    vc = _make_conversation("voice-tts")
    provider = _FakeProvider(audio=b"\x01\x02")
    with mock.patch(
        "hotel_ai.voice.conversation.get_speech_provider", return_value=provider
    ):
        assert vc.speak("hello") == b"\x01\x02"


@pytest.mark.django_db
def test_turn_invalid_base64_returns_error():
    vc = _make_conversation("voice-bad")
    result = vc.turn("!!!not-base64!!!")
    assert result["error"].startswith("Invalid audio")
    assert result["text"] == ""
    assert result["audio_b64"] == ""


@pytest.mark.django_db
def test_turn_no_transcript_returns_error():
    vc = _make_conversation("voice-empty")
    provider = _FakeProvider(transcript="")
    with mock.patch(
        "hotel_ai.voice.conversation.get_speech_provider", return_value=provider
    ):
        result = vc.turn(base64.b64encode(b"audio").decode())
    assert result["error"] == "no_transcript"
    assert result["text"] == ""
    assert result["audio_b64"] == ""


@pytest.mark.django_db
def test_turn_full_flow_returns_audio():
    vc = _make_conversation("voice-flow")
    provider = _FakeProvider(transcript="book me a room", audio=b"\x00\x01")
    vc.agent.handle_message.return_value = type(
        "R", (), {"text": "Sure thing", "action": "reply", "booking_id": None, "error": None}
    )()
    with mock.patch(
        "hotel_ai.voice.conversation.get_speech_provider", return_value=provider
    ):
        result = vc.turn(base64.b64encode(b"audio").decode())
    assert result["transcript"] == "book me a room"
    assert result["text"] == "Sure thing"
    assert result["audio_b64"] == base64.b64encode(b"\x00\x01").decode()
    assert result["audio_mime"] == "audio/mpeg"
    assert result["action"] == "reply"
    assert result["error"] is None


@pytest.mark.django_db
def test_turn_propagates_agent_error():
    vc = _make_conversation("voice-err")
    provider = _FakeProvider(transcript="book me a room", audio=b"")
    vc.agent.handle_message.return_value = type(
        "R", (), {"text": "", "action": None, "booking_id": None, "error": "no_provider"}
    )()
    with mock.patch(
        "hotel_ai.voice.conversation.get_speech_provider", return_value=provider
    ):
        result = vc.turn(base64.b64encode(b"audio").decode())
    assert result["error"] == "no_provider"
    assert result["audio_b64"] == ""
    assert result["audio_mime"] == ""


@pytest.mark.django_db
def test_voice_conversation_creates_session_row():
    """VoiceConversation() should persist a Conversation row keyed by session_key."""
    from hotel_ai.ai_providers.models import Conversation

    VoiceConversation("voice-new-session")
    assert Conversation.objects.filter(session_key="voice-new-session").exists()
    row = Conversation.objects.get(session_key="voice-new-session")
    assert row.channel == "voice"


@pytest.mark.django_db
def test_voice_conversation_normalizes_language():
    VoiceConversation("voice-bad-lang", language="fr")
    from hotel_ai.ai_providers.models import Conversation

    row = Conversation.objects.get(session_key="voice-bad-lang")
    assert row.language == "en"


@pytest.mark.django_db
def test_voice_conversation_updates_language_on_existing_session():
    from hotel_ai.ai_providers.models import Conversation

    Conversation.objects.create(session_key="voice-lang", channel="voice", language="en")
    VoiceConversation("voice-lang", language="pl")
    row = Conversation.objects.get(session_key="voice-lang")
    assert row.language == "pl"