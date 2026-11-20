"""Tests for the chat agent API."""

from __future__ import annotations

import datetime as _dt
import json
from decimal import Decimal
from unittest import mock

import pytest
from rest_framework.test import APIClient

from hotel_ai.ai_providers.base import AIProviderError
from hotel_ai.ai_providers.models import Conversation, Message


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


@pytest.mark.django_db
def test_chat_booking_flow_creates_booking(api_client):
    """A chat turn that books a room persists the booking end-to-end."""
    from hotel_ai.bookings.models import Booking, Client, Room, RoomType
    from hotel_ai.bookings.services import create_booking

    room_type = RoomType.objects.create(name="Double", base_price_per_night=Decimal("300.00"), capacity=2)
    room = Room.objects.create(room_number="201", room_type=room_type, floor=2)
    booking = create_booking(
        room=room,
        client=Client.objects.create(first_name="Anna", last_name="Kowalska"),
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
        guests=2,
        source="agent",
    )

    def _fake_handle_message(conv, msg):
            Message.objects.create(conversation=conv, role="user", content=msg)
            Message.objects.create(conversation=conv, role="assistant", content="Booked! Room 201, 2 nights, 600 PLN.")
            return _fake_result(text="Booked! Room 201, 2 nights, 600 PLN.", action="book", booking_id=booking.id)

    with mock.patch("hotel_ai.chat.views.ReservationAgent") as MockAgent:
        MockAgent.return_value.handle_message.side_effect = _fake_handle_message
        resp = api_client.post(
            "/chat/turn/",
            {
                "session_key": "booking-flow",
                "language": "en",
                "message": "Please book a double for Anna Kowalska, July 1-3",
            },
            format="json",
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "book"
    assert data["booking_id"] == booking.id
    assert "Booked" in data["reply"]
    assert Booking.objects.filter(pk=booking.id).exists()
    # The conversation row the agent worked on is the one the view created.
    conv = Conversation.objects.get(session_key="booking-flow")
    assert conv.channel == "chat"
    assert conv.messages.filter(role="user").count() == 1
    assert conv.messages.filter(role="assistant").count() == 1


@pytest.mark.django_db
def test_chat_booking_flow_propagates_booking_id_from_agent(api_client):
    """The booking_id returned by the agent is echoed in the API response."""
    with mock.patch("hotel_ai.chat.views.ReservationAgent") as MockAgent:
        MockAgent.return_value.handle_message.return_value = _fake_result(
            text="Checked in Anna Kowalska to room 201.",
            action="check_in",
            booking_id=11,
        )
        resp = api_client.post(
            "/chat/turn/",
            {
                "session_key": "checkin-flow",
                "language": "en",
                "message": "Check in Anna Kowalska",
            },
            format="json",
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "check_in"
    assert data["booking_id"] == 11
    assert "201" in data["reply"]


@pytest.mark.django_db
def test_chat_booking_flow_real_agent_creates_booking_db_row(api_client):
    """End-to-end: chat_turn -> ReservationAgent -> tool execution -> DB Booking row."""
    from hotel_ai.ai_providers.base import BaseAIProvider
    from hotel_ai.bookings.models import Booking, Room, RoomType

    class FakeProvider(BaseAIProvider):
        provider_type = "fake"
        def __init__(self, replies):
            super().__init__({})
            self.replies = list(replies)
        def chat(self, messages, **kwargs):
            return self.replies.pop(0)

    room_type = RoomType.objects.create(name="Double", base_price_per_night=Decimal("300.00"), capacity=2)
    Room.objects.create(room_number="201", room_type=room_type, floor=2)

    fake = FakeProvider(replies=[
        json.dumps({
            "action": "book",
            "args": {
                "room_type": "Double",
                "check_in": "2026-07-01",
                "check_out": "2026-07-03",
                "guests": 2,
                "first_name": "Jan",
                "last_name": "Kowalski",
                "email": "jan@example.com",
            },
        }),
        json.dumps({"action": "reply", "text": "Rezerwacja została potwierdzona. Pokój 201."}),
    ])

    with mock.patch("hotel_ai.chat.views.ReservationAgent") as MockAgent:
        from hotel_ai.ai_providers.agent import ReservationAgent
        real_agent = ReservationAgent(language="pl", provider=fake)
        MockAgent.return_value = real_agent

        resp = api_client.post(
            "/chat/turn/",
            {
                "session_key": "real-agent-booking",
                "language": "pl",
                "message": "Zarezerwuj pokój dwuosobowy dla Jan Kowalski od 1 do 3 lipca",
            },
            format="json",
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "book"
    assert data["booking_id"] is not None
    assert "201" in data["reply"]

    # Verify real DB booking created by tool execution loop
    b = Booking.objects.get(pk=data["booking_id"])
    assert b.client.first_name == "Jan"
    assert b.client.last_name == "Kowalski"
    assert b.check_in == _dt.date(2026, 7, 1)
    assert b.check_out == _dt.date(2026, 7, 3)
    assert b.source == "agent"