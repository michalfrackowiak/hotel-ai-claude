"""Tests for the chat agent API."""

from __future__ import annotations

from unittest import mock

import pytest
from rest_framework.test import APIClient

from hotel_ai.ai_providers.base import AIProviderError
from hotel_ai.ai_providers.models import Conversation


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
def test_chat_page_renders(api_client):
    resp = api_client.get("/chat/", {"lang": "en"})
    assert resp.status_code == 200
    assert b"AI Chat Reception" in resp.content


@pytest.mark.django_db
def test_chat_page_polish(api_client):
    resp = api_client.get("/chat/", {"lang": "pl"})
    assert resp.status_code == 200
    assert "Czat z recepcjonistą".encode("utf-8") in resp.content


@pytest.mark.django_db
def test_chat_turn_missing_message(api_client):
    resp = api_client.post("/chat/turn/", {}, format="json")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "message is required"


def _fake_result(text="Hi there", action="reply", booking_id=None, error=None):
    return type(
        "R",
        (),
        {"text": text, "action": action, "booking_id": booking_id, "error": error},
    )()


@pytest.mark.django_db
def test_chat_turn_generates_session_key(api_client):
    with mock.patch(
        "hotel_ai.chat.views.ReservationAgent"
    ) as MockAgent:
        MockAgent.return_value.handle_message.return_value = _fake_result()
        resp = api_client.post(
            "/chat/turn/",
            {"language": "en", "message": "hello"},
            format="json",
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_key"]
    assert data["reply"] == "Hi there"
    assert Conversation.objects.filter(session_key=data["session_key"]).exists()


@pytest.mark.django_db
def test_chat_turn_reuses_session_key(api_client):
    with mock.patch(
        "hotel_ai.chat.views.ReservationAgent"
    ) as MockAgent:
        MockAgent.return_value.handle_message.return_value = _fake_result()
        resp = api_client.post(
            "/chat/turn/",
            {"session_key": "reuse-me", "language": "en", "message": "hello"},
            format="json",
        )
    assert resp.status_code == 200
    assert resp.json()["session_key"] == "reuse-me"
    assert Conversation.objects.filter(session_key="reuse-me").exists()


@pytest.mark.django_db
def test_chat_turn_provider_error_returns_503(api_client):
    with mock.patch(
        "hotel_ai.chat.views.ReservationAgent"
    ) as MockAgent:
        MockAgent.return_value.handle_message.side_effect = AIProviderError("down")
        resp = api_client.post(
            "/chat/turn/",
            {"language": "en", "message": "hello"},
            format="json",
        )
    assert resp.status_code == 503
    data = resp.json()
    assert data["error"] == "provider_error"
    assert data["action"] is None
    assert data["booking_id"] is None
    assert data["reply"]


@pytest.mark.django_db
def test_chat_turn_persists_conversation_and_invokes_agent(api_client):
    """The view creates a Conversation row and passes it to the agent."""
    with mock.patch(
        "hotel_ai.chat.views.ReservationAgent"
    ) as MockAgent:
        MockAgent.return_value.handle_message.return_value = _fake_result()
        resp = api_client.post(
            "/chat/turn/",
            {"session_key": "persist-me", "language": "en", "message": "hello"},
            format="json",
        )
    assert resp.status_code == 200
    conv = Conversation.objects.get(session_key="persist-me")
    assert conv.channel == "chat"
    assert conv.language == "en"
    MockAgent.assert_called_once_with(language="en")
    passed_conv = MockAgent.return_value.handle_message.call_args[0][0]
    assert passed_conv.id == conv.id


@pytest.mark.django_db
def test_chat_turn_updates_language(api_client):
    with mock.patch(
        "hotel_ai.chat.views.ReservationAgent"
    ) as MockAgent:
        MockAgent.return_value.handle_message.return_value = _fake_result()
        resp = api_client.post(
            "/chat/turn/",
            {"session_key": "lang-me", "language": "pl", "message": "witaj"},
            format="json",
        )
    assert resp.status_code == 200
    conv = Conversation.objects.get(session_key="lang-me")
    assert conv.language == "pl"


@pytest.mark.django_db
def test_chat_turn_propagates_agent_error_and_booking_id(api_client):
    with mock.patch(
        "hotel_ai.chat.views.ReservationAgent"
    ) as MockAgent:
        MockAgent.return_value.handle_message.return_value = _fake_result(
            text="Booked", action="book", booking_id=42
        )
        resp = api_client.post(
            "/chat/turn/",
            {"session_key": "book-me", "language": "en", "message": "book"},
            format="json",
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "book"
    assert data["booking_id"] == 42


@pytest.mark.django_db
def test_chat_turn_invalid_language_falls_back_to_english(api_client):
    """An unsupported language is normalized to 'en' inside the agent."""
    with mock.patch("hotel_ai.chat.views.ReservationAgent") as MockAgent:
        MockAgent.return_value.handle_message.return_value = _fake_result()
        resp = api_client.post(
            "/chat/turn/",
            {"session_key": "bad-lang", "language": "fr", "message": "salut"},
            format="json",
        )
    assert resp.status_code == 200
    MockAgent.assert_called_once_with(language="fr")


@pytest.mark.django_db
def test_chat_turn_missing_session_key_is_generated(api_client):
    """Omitting session_key still returns a usable one."""
    with mock.patch("hotel_ai.chat.views.ReservationAgent") as MockAgent:
        MockAgent.return_value.handle_message.return_value = _fake_result()
        resp = api_client.post(
            "/chat/turn/",
            {"language": "en", "message": "hello"},
            format="json",
        )
    assert resp.status_code == 200
    assert resp.json()["session_key"]
    assert Conversation.objects.filter(session_key=resp.json()["session_key"]).exists()


@pytest.mark.django_db
def test_chat_turn_updates_existing_session_language(api_client):
    """When an existing session has a different language, it gets updated."""
    conv = Conversation.objects.create(session_key="lang-update", channel="chat", language="en")
    with mock.patch("hotel_ai.chat.views.ReservationAgent") as MockAgent:
        MockAgent.return_value.handle_message.return_value = _fake_result()
        resp = api_client.post(
            "/chat/turn/",
            {"session_key": "lang-update", "language": "pl", "message": "cześć"},
            format="json",
        )
    assert resp.status_code == 200
    conv.refresh_from_db()
    assert conv.language == "pl"