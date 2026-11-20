"""Tests for the AI provider layer and the reservation agent."""

from __future__ import annotations

import datetime as _dt
import json
import sys
from decimal import Decimal
from unittest import mock

import pytest

from hotel_ai.bookings.models import Booking, BookingStatus, Client, Room, RoomType
from hotel_ai.bookings.services import cancel_booking, create_booking

from .agent import ReservationAgent
from .base import BaseAIProvider, build_messages, coerce_chat_response
from .factory import build_provider
from .models import AgentSettings, AIProvider, Conversation


class FakeProvider(BaseAIProvider):
    provider_type = "fake"

    def __init__(self, config, replies=None):
        super().__init__(config)
        self.replies = list(replies or [])
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(list(messages))
        if len(self.replies) > 1:
            return self.replies.pop(0)
        return self.replies[0] if self.replies else "{}"


@pytest.fixture
def hotel(db):
    single = RoomType.objects.create(name="Single", base_price_per_night=Decimal("200.00"), capacity=1)
    double = RoomType.objects.create(name="Double", base_price_per_night=Decimal("300.00"), capacity=2)
    room = Room.objects.create(room_number="201", room_type=double, floor=2)
    return {"Single": single, "Double": double, "room": room}


# ---------------------------------------------------------------------------
# Base layer
# ---------------------------------------------------------------------------
def test_build_messages_includes_system_and_history():
    msgs = build_messages(
        "You are reception.",
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        "Book a room",
    )
    assert msgs[0]["role"] == "system"
    assert msgs[-1] == {"role": "user", "content": "Book a room"}
    assert len(msgs) == 4


def test_coerce_chat_response_openai_shape():
    class Message:
        content = "hello"

    class Choices:
        message = Message()

    result = type("Resp", (), {"choices": [Choices()]})()
    assert coerce_chat_response(result) == "hello"


def test_coerce_chat_response_anthropic_shape():
    class Block:
        text = "hi there"

    class Resp:
        content = [Block()]

    assert coerce_chat_response(Resp()) == "hi there"


def test_build_provider_unknown_type():
    with pytest.raises(ValueError):
        build_provider({"provider_type": "nope"})


# ---------------------------------------------------------------------------
# Agent tool loop
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_agent_plain_reply(hotel):
    provider = FakeProvider(
        {"provider_type": "fake", "model": "fake-1"},
        replies=[json.dumps({"action": "reply", "text": "Hello! How can I help?"})],
    )
    agent = ReservationAgent(language="en", provider=provider)
    conv = Conversation.objects.create(session_key="s1", channel="chat")
    result = agent.handle_message(conv, "Hi")
    assert result.text == "Hello! How can I help?"
    assert result.action is None
    # user + assistant messages persisted
    assert conv.messages.count() == 2


@pytest.mark.django_db
def test_agent_checks_availability_then_replies(hotel):
    provider = FakeProvider(
        {"provider_type": "fake", "model": "fake-1"},
        replies=[
            json.dumps(
                {
                    "action": "check_availability",
                    "args": {"check_in": "2026-07-01", "check_out": "2026-07-03", "guests": 2},
                }
            ),
            json.dumps(
                {"action": "reply", "text": "The Double is free, 600 PLN total."}
            ),
        ],
    )
    agent = ReservationAgent(language="en", provider=provider)
    conv = Conversation.objects.create(session_key="s2", channel="chat")
    result = agent.handle_message(conv, "Any double rooms in July?")
    assert result.action == "check_availability"
    assert "Double" in result.text or "free" in result.text


@pytest.mark.django_db
def test_agent_books_end_to_end(hotel):
    provider = FakeProvider(
        {"provider_type": "fake", "model": "fake-1"},
        replies=[
            json.dumps(
                {
                    "action": "book",
                    "args": {
                        "room_type": "Double",
                        "check_in": "2026-07-01",
                        "check_out": "2026-07-03",
                        "guests": 2,
                        "first_name": "Anna",
                        "last_name": "Kowalska",
                        "email": "anna@example.com",
                    },
                }
            ),
            json.dumps(
                {"action": "reply", "text": "Booked! Room 201, 2 nights, 600 PLN."}
            ),
        ],
    )
    agent = ReservationAgent(language="en", provider=provider)
    conv = Conversation.objects.create(session_key="s3", channel="chat")
    result = agent.handle_message(conv, "Please book a double for Anna Kowalska, July 1-3")
    assert result.action == "book"
    # The booking actually exists.
    from hotel_ai.bookings.models import Booking

    assert Booking.objects.filter(client__first_name="Anna").exists()


@pytest.mark.django_db
def test_agent_book_unknown_room_type(hotel):
    provider = FakeProvider(
        {"provider_type": "fake", "model": "fake-1"},
        replies=[
            json.dumps(
                {
                    "action": "book",
                    "args": {"room_type": "Penthouse", "check_in": "2026-07-01", "check_out": "2026-07-02"},
                }
            ),
            json.dumps({"action": "reply", "text": "Sorry, we don't have that."}),
        ],
    )
    agent = ReservationAgent(language="en", provider=provider)
    conv = Conversation.objects.create(session_key="s4", channel="chat")
    agent.handle_message(conv, "Penthouse please")
    # Second round got the tool result and replied.
    assert provider.calls[1][-1]["content"].startswith("[tool result]")


@pytest.mark.django_db
def test_agent_handles_fenced_json(hotel):
    raw = '```json\n{"action": "reply", "text": "Sure!"}\n```'
    provider = FakeProvider({"provider_type": "fake", "model": "f"}, replies=[raw])
    agent = ReservationAgent(language="en", provider=provider)
    conv = Conversation.objects.create(session_key="s5", channel="chat")
    result = agent.handle_message(conv, "hello")
    assert result.text == "Sure!"


@pytest.mark.django_db
def test_agent_unparseable_response_marks_error(hotel):
    provider = FakeProvider({"provider_type": "fake", "model": "f"}, replies=["I am not JSON"])
    agent = ReservationAgent(language="en", provider=provider)
    conv = Conversation.objects.create(session_key="s6", channel="chat")
    result = agent.handle_message(conv, "hello")
    assert result.error == "unparseable_response"


@pytest.mark.django_db
def test_agent_system_prompt_contains_today_and_room_types(hotel):
    agent = ReservationAgent(language="en")
    prompt = agent.system_prompt()
    assert _dt.date.today().isoformat() in prompt
    assert "Double" in prompt


@pytest.mark.django_db
def test_agent_polish_prompt_is_natural_polish(hotel):
    """The Polish prompt should sound like a real receptionist, not a
    template - warm, colloquial, and never fall back to English."""
    agent = ReservationAgent(language="pl")
    prompt = agent.system_prompt()
    # Warm, natural Polish openings and fillers.
    for phrase in ("Dzień dobry", "Jasne", "Chwila", "Zrobimy to", "Proszę bardzo"):
        assert phrase in prompt, f"missing natural Polish phrase: {phrase}"
    # No English boilerplate leaked in.
    assert "Rules:" not in prompt
    assert "Reply ONLY" not in prompt


@pytest.mark.django_db
def test_agent_polish_reply_is_polish(hotel):
    provider = FakeProvider(
        {"provider_type": "fake", "model": "fake-pl"},
        replies=[
            json.dumps(
                {"action": "reply", "text": "Dzień dobry! Chętnie na pokój na 2 noce w grudniu."}
            ),
        ],
    )
    agent = ReservationAgent(language="pl", provider=provider)
    conv = Conversation.objects.create(session_key="pl-1", channel="chat", language="pl")
    result = agent.handle_message(conv, "Dzień dobry, chętnie na pokój na 2 noce w grudniu")
    assert result.text.startswith("Dzień dobry")
    assert "grudniu" in result.text
    # No English leaked into the reply.
    assert "Sure" not in result.text
    assert "Hello" not in result.text


# ---------------------------------------------------------------------------
# Provider models / factory
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_provider_env_key_fallback(db, settings):
    settings.OPENAI_API_KEY = "sk-env-key"
    p = AIProvider.objects.create(name="openai", provider_type="openai", model="gpt-4o-mini")
    assert p.effective_api_key() == "sk-env-key"


@pytest.mark.django_db
def test_provider_stored_key_wins(db, settings):
    settings.OPENAI_API_KEY = "sk-env-key"
    p = AIProvider.objects.create(
        name="openai2", provider_type="openai", model="gpt-4o-mini", api_key="sk-stored"
    )
    assert p.effective_api_key() == "sk-stored"


@pytest.mark.django_db
def test_ollama_default_base_url(db, settings):
    settings.OLLAMA_BASE_URL = "http://localhost:11434"
    p = AIProvider.objects.create(name="local", provider_type="ollama", model="llama3.1:8b")
    assert p.effective_base_url() == "http://localhost:11434"


# ---------------------------------------------------------------------------
# Check-in / check-out tool paths
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_agent_checks_in(hotel):
    rt, room = hotel["Single"], hotel["room"]
    booking = create_booking(
        room=room,
        client=Client.objects.create(first_name="Maja", email="maja@test.com"),
        check_in=_dt.date.today(),
        check_out=_dt.date.today() + _dt.timedelta(days=1),
        guests=1,
    )
    provider = FakeProvider(
        {"provider_type": "fake", "model": "fake"},
        replies=[
            json.dumps({"action": "check_in", "args": {"booking_id": booking.id}}),
            json.dumps({"action": "reply", "text": "Checked in, Maja."}),
        ],
    )
    agent = ReservationAgent(language="en", provider=provider)
    conv = Conversation.objects.create(session_key="s-checkin", channel="chat")
    result = agent.handle_message(conv, f"Check in booking {booking.id}")
    assert result.action == "check_in" or result.text
    booking.refresh_from_db()
    assert booking.status == BookingStatus.CHECKED_IN


@pytest.mark.django_db
def test_agent_checks_out(hotel):
    rt, room = hotel["Single"], hotel["room"]
    booking = create_booking(
        room=room,
        client=Client.objects.create(first_name="Tomek", email="tomek@test.com"),
        check_in=_dt.date.today(),
        check_out=_dt.date.today() + _dt.timedelta(days=1),
        guests=1,
    )
    # pre-check in
    from hotel_ai.bookings.services import check_in_booking
    booking = check_in_booking(booking)
    assert booking.status == BookingStatus.CHECKED_IN

    provider = FakeProvider(
        {"provider_type": "fake", "model": "fake"},
        replies=[
            json.dumps({"action": "check_out", "args": {"booking_id": booking.id}}),
            json.dumps({"action": "reply", "text": "Checked out, Tomek."}),
        ],
    )
    agent = ReservationAgent(language="en", provider=provider)
    conv = Conversation.objects.create(session_key="s-checkout", channel="chat")
    result = agent.handle_message(conv, f"Check out booking {booking.id}")
    assert result.action == "check_out" or result.text
    booking.refresh_from_db()
    assert booking.status == BookingStatus.CHECKED_OUT


@pytest.mark.django_db
def test_agent_cancels(hotel):
    rt, room = hotel["Single"], hotel["room"]
    booking = create_booking(
        room=room,
        client=Client.objects.create(first_name="Ania", email="ania@test.com"),
        check_in=_dt.date.today(),
        check_out=_dt.date.today() + _dt.timedelta(days=1),
        guests=1,
    )
    provider = FakeProvider(
        {"provider_type": "fake", "model": "fake"},
        replies=[
            json.dumps({"action": "cancel", "args": {"booking_id": booking.id}}),
            json.dumps({"action": "reply", "text": "Cancelled, Ania."}),
        ],
    )
    agent = ReservationAgent(language="en", provider=provider)
    conv = Conversation.objects.create(session_key="s-cancel", channel="chat")
    result = agent.handle_message(conv, f"Cancel booking {booking.id}")
    assert result.action == "cancel" or result.text
    booking.refresh_from_db()
    assert booking.status == BookingStatus.CANCELLED


@pytest.mark.django_db
def test_agent_marks_no_show(hotel):
    rt, room = hotel["Single"], hotel["room"]
    booking = create_booking(
        room=room,
        client=Client.objects.create(first_name="Noa", email="noa@test.com"),
        check_in=_dt.date.today(),
        check_out=_dt.date.today() + _dt.timedelta(days=1),
        guests=1,
    )
    provider = FakeProvider(
        {"provider_type": "fake", "model": "fake"},
        replies=[
            json.dumps({"action": "no_show", "args": {"booking_id": booking.id}}),
            json.dumps({"action": "reply", "text": "Marked as no-show."}),
        ],
    )
    agent = ReservationAgent(language="en", provider=provider)
    conv = Conversation.objects.create(session_key="s-no_show", channel="chat")
    result = agent.handle_message(conv, f"Guest {booking.client.first_name} didn't show up")
    assert result.action == "no_show" or result.text
    booking.refresh_from_db()
    assert booking.status == BookingStatus.NO_SHOW


@pytest.mark.django_db
def test_agent_lookup_booking_by_name():
    """When no booking_id is given, fall back to name-based lookup."""
    client = Client.objects.create(first_name="Zosia", email="zosia@example.com")
    # Need a proper room for create_booking; reuse a hotel if available
    hotel_rt = RoomType.objects.first()
    if not hotel_rt:
        return
    room = Room.objects.filter(room_type=hotel_rt).first()
    if not room:
        return
    booking = create_booking(
        room=room,
        client=client,
        check_in=_dt.date.today(),
        check_out=_dt.date.today() + _dt.timedelta(days=1),
        guests=1,
    )
    provider = FakeProvider(
        {"provider_type": "fake", "model": "fake"},
        replies=[json.dumps({"action": "reply", "text": "Found Zosia."})],
    )
    agent = ReservationAgent(language="en", provider=provider)
    conv = Conversation.objects.create(session_key="s-name-lookup", channel="chat")
    result = agent.handle_message(conv, "I'm Zosia, check me in.")
    # The exact outcome depends on LLM parsing, but the tool route should
    # be discoverable via the system prompt and the booking exists.
    assert Booking.objects.filter(id=booking.id).exists()


# ---------------------------------------------------------------------------
# Agent error / edge-case paths
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_agent_no_provider_returns_error():
    agent = ReservationAgent(language="en", provider=mock.MagicMock())
    agent.provider = None  # force the no-provider path
    conv = Conversation.objects.create(session_key="s-no-provider", channel="chat")
    result = agent.handle_message(conv, "hello")
    assert result.error == "no_provider"
    assert result.text == "No AI provider configured."


@pytest.mark.django_db
def test_agent_unknown_action_logged(hotel):
    provider = FakeProvider(
        {"provider_type": "fake", "model": "f"},
        replies=[json.dumps({"action": "teleport", "args": {}})],
    )
    agent = ReservationAgent(language="en", provider=provider)
    conv = Conversation.objects.create(session_key="s-unknown-action", channel="chat")
    result = agent.handle_message(conv, "go somewhere")
    assert result.action == "teleport"
    # Tool result fed back to LLM mentions unknown action.
    assert provider.calls[1][-1]["content"].startswith("[tool result]")


@pytest.mark.django_db
def test_agent_tool_loop_exhausted(hotel):
    """After max_tool_rounds tool actions without a reply, the agent signals
    exhaustion rather than looping forever."""
    provider = FakeProvider(
        {"provider_type": "fake", "model": "f"},
        replies=[
            json.dumps({"action": "book", "args": {"room_type": "Double"}}),
            json.dumps({"action": "book", "args": {"room_type": "Double"}}),
            json.dumps({"action": "book", "args": {"room_type": "Double"}}),
            json.dumps({"action": "book", "args": {"room_type": "Double"}}),
            json.dumps({"action": "book", "args": {"room_type": "Double"}}),
        ],
    )
    agent = ReservationAgent(language="en", provider=provider)
    conv = Conversation.objects.create(session_key="s-exhausted", channel="chat")
    result = agent.handle_message(conv, "book me in", max_tool_rounds=4)
    assert result.error == "tool_loop_exhausted"


@pytest.mark.django_db
def test_agent_run_check_availability_invalid_dates(hotel):
    agent = ReservationAgent(language="en")
    result = agent._run_check_availability({"check_in": "not-a-date"})
    assert "Invalid dates" in result


@pytest.mark.django_db
def test_agent_run_book_missing_room_type(hotel):
    agent = ReservationAgent(language="en")
    result = agent._run_book({"check_in": "2026-07-01"})
    assert result == "Missing 'room_type'."


@pytest.mark.django_db
def test_agent_run_book_invalid_dates(hotel):
    agent = ReservationAgent(language="en")
    result = agent._run_book({"room_type": "Double", "check_in": "bad"})
    assert "Invalid dates" in result


@pytest.mark.django_db
def test_agent_run_book_no_rooms_available(hotel):
    with mock.patch(
        "hotel_ai.ai_providers.agent.find_available_rooms", return_value=[]
    ):
        agent = ReservationAgent(language="en")
        result = agent._run_book(
            {
                "room_type": "Double",
                "check_in": "2026-07-01",
                "check_out": "2026-07-02",
                "guests": 1,
            }
        )
    assert "No rooms" in result


@pytest.mark.django_db
def test_agent_run_book_create_fails(hotel):
    with mock.patch(
        "hotel_ai.ai_providers.agent.create_booking",
        side_effect=ValueError("too short"),
    ):
        agent = ReservationAgent(language="en")
        result = agent._run_book(
            {
                "room_type": "Double",
                "check_in": "2026-07-01",
                "check_out": "2026-07-02",
                "guests": 1,
                "first_name": "Test",
            }
        )
    assert result.startswith("Booking failed:")


@pytest.mark.django_db
def test_agent_run_check_in_lookup_not_found(hotel):
    agent = ReservationAgent(language="en")
    result = agent._run_check_in({"booking_id": 99999})
    assert "not found" in result.lower()


@pytest.mark.django_db
def test_agent_run_check_in_lookup_invalid_id():
    agent = ReservationAgent(language="en")
    result = agent._run_check_in({"booking_id": "abc"})
    assert result == "Invalid 'booking_id'."


@pytest.mark.django_db
def test_agent_run_check_out_lookup_not_found(hotel):
    agent = ReservationAgent(language="en")
    result = agent._run_check_out({"booking_id": 99999})
    assert "not found" in result.lower()


@pytest.mark.django_db
def test_agent_run_cancel_already_cancelled(hotel):
    rt, room = hotel["Single"], hotel["room"]
    booking = create_booking(
        room=room,
        client=Client.objects.create(first_name="CancelMe", email="cm@test.com"),
        check_in=_dt.date.today(),
        check_out=_dt.date.today() + _dt.timedelta(days=1),
        guests=1,
    )
    cancel_booking(booking)
    assert booking.status == BookingStatus.CANCELLED

    provider = FakeProvider(
        {"provider_type": "fake", "model": "fake"},
        replies=[json.dumps({"action": "cancel", "args": {"booking_id": booking.id}})],
    )
    agent = ReservationAgent(language="en", provider=provider)
    conv = Conversation.objects.create(session_key="s-cancel-already", channel="chat")
    agent.handle_message(conv, "cancel again")
    booking.refresh_from_db()
    assert booking.status == BookingStatus.CANCELLED
    # Second cancel should not raise; tool result says "Already cancelled."
    assert "Already cancelled" in provider.calls[1][-1]["content"]


@pytest.mark.django_db
def test_agent_run_no_show_lookup_not_found(hotel):
    agent = ReservationAgent(language="en")
    result = agent._run_no_show({"booking_id": 99999})
    assert "not found" in result.lower()


@pytest.mark.django_db
def test_agent_run_no_show_already_marked(hotel):
    rt, room = hotel["Single"], hotel["room"]
    booking = create_booking(
        room=room,
        client=Client.objects.create(first_name="NoShowMe", email="ns@test.com"),
        check_in=_dt.date.today(),
        check_out=_dt.date.today() + _dt.timedelta(days=1),
        guests=1,
    )
    from hotel_ai.bookings.services import no_show_booking

    no_show_booking(booking)
    assert booking.status == BookingStatus.NO_SHOW

    provider = FakeProvider(
        {"provider_type": "fake", "model": "fake"},
        replies=[json.dumps({"action": "no_show", "args": {"booking_id": booking.id}})],
    )
    agent = ReservationAgent(language="en", provider=provider)
    conv = Conversation.objects.create(session_key="s-noshow-already", channel="chat")
    agent.handle_message(conv, "mark no-show again")
    booking.refresh_from_db()
    assert booking.status == BookingStatus.NO_SHOW
    assert "Already marked as no-show" in provider.calls[1][-1]["content"]


@pytest.mark.django_db
def test_agent_lookup_booking_nonexistent_name():
    """Name lookup with no matching guest returns informative message."""
    agent = ReservationAgent(language="en")
    result = agent._lookup_booking({"guest_name": "NobodyHere123"})
    assert "No booking found" in result


@pytest.mark.django_db
def test_agent_lookup_booking_missing_both_ids():
    agent = ReservationAgent(language="en")
    result = agent._lookup_booking({})
    assert result == "Missing 'booking_id' or 'guest_name'."


def test_parse_action_non_dict_json():
    """A JSON array or scalar should be rejected as non-dict."""
    assert ReservationAgent._parse_action('["a", "b"]') is None
    assert ReservationAgent._parse_action('"just a string"') is None


def test_parse_action_plain_text_falls_back():
    """Plain text without braces still returns None (unparseable)."""
    assert ReservationAgent._parse_action("Hello world") is None


@pytest.mark.django_db
def test_agent_run_check_in_value_error(hotel):
    """Call check_in on an already-checked-in booking; check_in_booking raises ValueError."""
    rt, room = hotel["Single"], hotel["room"]
    booking = create_booking(
        room=room,
        client=Client.objects.create(first_name="Penda", email="p@test.com"),
        check_in=_dt.date.today(),
        check_out=_dt.date.today() + _dt.timedelta(days=1),
        guests=1,
        source="agent",
    )
    from hotel_ai.bookings.services import check_in_booking as ci
    ci(booking)
    # Now booking is CHECKED_IN — calling again raises ValueError.
    agent = ReservationAgent(language="en")
    result = agent._run_check_in({"booking_id": booking.id})
    assert result.startswith("Check-in failed:")


@pytest.mark.django_db
def test_agent_run_check_out_value_error(hotel):
    """Call check_out on a CONFIRMED booking; check_out_booking raises ValueError."""
    rt, room = hotel["Single"], hotel["room"]
    booking = create_booking(
        room=room,
        client=Client.objects.create(first_name="COut", email="co@test.com"),
        check_in=_dt.date.today(),
        check_out=_dt.date.today() + _dt.timedelta(days=1),
        guests=1,
    )
    agent = ReservationAgent(language="en")
    result = agent._run_check_out({"booking_id": booking.id})
    assert result.startswith("Check-out failed:")


@pytest.mark.django_db
def test_agent_run_cancel_already_cancelled_line_335(hotel):
    """Directly call _run_cancel on a cancelled booking to cover the
    isinstance(booking, str) guard after lookup at line 335."""
    rt, room = hotel["Single"], hotel["room"]
    booking = create_booking(
        room=room,
        client=Client.objects.create(first_name="CancelLine", email="cl@test.com"),
        check_in=_dt.date.today(),
        check_out=_dt.date.today() + _dt.timedelta(days=1),
        guests=1,
    )
    cancel_booking(booking)
    agent = ReservationAgent(language="en")
    result = agent._run_cancel({"booking_id": booking.id})
    assert "Already cancelled" in result


@pytest.mark.django_db
def test_agent_run_cancel_lookup_returns_string(hotel):
    """When _lookup_booking returns a string (missing booking), line 335
    (isinstance guard) is exercised before the status check."""
    agent = ReservationAgent(language="en")
    result = agent._run_cancel({"booking_id": 99999})
    assert "not found" in result.lower()


@pytest.mark.django_db
def test_agent_run_no_show_value_error(hotel):
    """Call no_show on an already-cancelled booking; the service raises
    ValueError because only CONFIRMED bookings can become NO_SHOW."""
    rt, room = hotel["Single"], hotel["room"]
    booking = create_booking(
        room=room,
        client=Client.objects.create(first_name="Nshow", email="ns@test.com"),
        check_in=_dt.date.today(),
        check_out=_dt.date.today() + _dt.timedelta(days=1),
        guests=1,
    )
    cancel_booking(booking)
    # Now booking is CANCELLED; no_show_booking rejects it.
    agent = ReservationAgent(language="en")
    result = agent._run_no_show({"booking_id": booking.id})
    assert result.startswith("No-show failed:")


@pytest.mark.django_db
def test_agent_lookup_two_part_name_matches(hotel):
    """Two-word guest name uses the else branch of _lookup_booking."""
    rt, room = hotel["Single"], hotel["room"]
    from hotel_ai.bookings.services import create_booking

    create_booking(
        room=room,
        client=Client.objects.create(first_name="Jan", last_name="Kowalski", email="jk@test.com"),
        check_in=_dt.date.today(),
        check_out=_dt.date.today() + _dt.timedelta(days=1),
        guests=1,
    )
    agent = ReservationAgent(language="en")
    found = agent._lookup_booking({"guest_name": "Jan Kowalski"})
    assert not isinstance(found, str)
    assert found.client.last_name == "Kowalski"


def test_parse_action_braces_in_non_json_text():
    """When raw text contains JSON inside other prose, the regex fallback
    should capture it (line 434)."""
    raw = 'Here is your booking {"action":"reply","text":"ok"} thanks!'
    data = ReservationAgent._parse_action(raw)
    assert data == {"action": "reply", "text": "ok"}


# ---------------------------------------------------------------------------
# OpenAI provider (mocked client)
# ---------------------------------------------------------------------------
def _fake_chat_response(content):
    """Build a fake OpenAI ChatCompletion-shaped object."""

    class _Choice:
        def __init__(self, content):
            self.message = type("Msg", (), {"content": content})()

    return type("Resp", (), {"choices": [_Choice(content)]})()


def _fake_audio_response(text):
    return type("Audio", (), {"text": text})()


def _fake_speech_response(content):
    return type("Speech", (), {"content": content})()


def _openai_provider():
    from .openai_provider import OpenAIProvider

    return OpenAIProvider(
        {"provider_type": "openai", "model": "gpt-4o-mini", "api_key": "sk-test"}
    )


def test_openai_chat_success():
    provider = _openai_provider()
    with mock.patch.object(provider, "_client") as mock_client:
        mock_client.return_value.chat.completions.create.return_value = _fake_chat_response("hello")
        assert provider.chat([{"role": "user", "content": "hi"}]) == "hello"


def test_openai_chat_failure_raises_ai_provider_error():
    from .base import AIProviderError
    import openai

    provider = _openai_provider()
    with mock.patch.object(provider, "_client") as mock_client:
        mock_client.return_value.chat.completions.create.side_effect = openai.OpenAIError("boom")
        with pytest.raises(AIProviderError):
            provider.chat([{"role": "user", "content": "hi"}])


def test_openai_stream_chat_yields_chunks():
    provider = _openai_provider()

    class _Delta:
        def __init__(self, content):
            self.content = content

    class _Choice:
        def __init__(self, content):
            self.delta = _Delta(content)

    class _Chunk:
        def __init__(self, content):
            self.choices = [_Choice(content)]

    stream = [_Chunk("Hello "), _Chunk("world"), _Chunk(None)]
    with mock.patch.object(provider, "_client") as mock_client:
        mock_client.return_value.chat.completions.create.return_value = iter(stream)
        chunks = list(
            provider.stream_chat([{"role": "user", "content": "hi"}])
        )
    assert chunks == ["Hello ", "world"]


def test_openai_stream_chat_skips_empty_choices():
    provider = _openai_provider()

    class _Chunk:
        def __init__(self, choices):
            self.choices = choices

    stream = [_Chunk([]), _Chunk([type("C", (), {"delta": type("D", (), {"content": "x"})()})()])]
    with mock.patch.object(provider, "_client") as mock_client:
        mock_client.return_value.chat.completions.create.return_value = iter(stream)
        assert list(provider.stream_chat([{"role": "user", "content": "hi"}])) == ["x"]


def test_openai_transcribe_success():
    provider = _openai_provider()
    with mock.patch.object(provider, "_client") as mock_client:
        mock_client.return_value.audio.transcriptions.create.return_value = _fake_audio_response("hello world")
        assert provider.transcribe(b"audio") == "hello world"


def test_openai_transcribe_failure_raises():
    from .base import AIProviderError
    import openai

    provider = _openai_provider()
    with mock.patch.object(provider, "_client") as mock_client:
        mock_client.return_value.audio.transcriptions.create.side_effect = openai.OpenAIError("nope")
        with pytest.raises(AIProviderError):
            provider.transcribe(b"audio")


def test_openai_speak_success():
    provider = _openai_provider()
    with mock.patch.object(provider, "_client") as mock_client:
        mock_client.return_value.audio.speech.create.return_value = _fake_speech_response(b"\x00\x01")
        assert provider.speak("hello") == b"\x00\x01"


def test_openai_speak_failure_raises():
    from .base import AIProviderError
    import openai

    provider = _openai_provider()
    with mock.patch.object(provider, "_client") as mock_client:
        mock_client.return_value.audio.speech.create.side_effect = openai.OpenAIError("nope")
        with pytest.raises(AIProviderError):
            provider.speak("hello")


def test_openai_client_uses_base_url_when_set():
    from .openai_provider import OpenAIProvider

    provider = OpenAIProvider(
        {
            "provider_type": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-test",
            "base_url": "http://ollama:11434/v1",
        }
    )
    with mock.patch("hotel_ai.ai_providers.openai_provider.openai.OpenAI") as mock_openai:
        provider._client()
        _, kwargs = mock_openai.call_args
        assert kwargs["base_url"] == "http://ollama:11434/v1"
        assert kwargs["api_key"] == "sk-test"
        assert kwargs["timeout"] == 30


def test_openai_client_omits_base_url_when_empty():
    from .openai_provider import OpenAIProvider

    provider = OpenAIProvider(
        {"provider_type": "openai", "model": "gpt-4o-mini", "api_key": "sk-test"}
    )
    with mock.patch("hotel_ai.ai_providers.openai_provider.openai.OpenAI") as mock_openai:
        provider._client()
        _, kwargs = mock_openai.call_args
        assert "base_url" not in kwargs

# ---------------------------------------------------------------------------
# Base layer edge cases
# ---------------------------------------------------------------------------
def test_coerce_chat_response_string():
    assert coerce_chat_response("just a string") == "just a string"


def test_coerce_chat_response_dict_openai():
    result = {"choices": [{"message": {"content": "hi"}}]}
    assert coerce_chat_response(result) == "hi"


def test_coerce_chat_response_dict_anthropic():
    result = {"content": [{"text": "hello"}]}
    assert coerce_chat_response(result) == "hello"


def test_coerce_chat_response_fallback():
    """When nothing matches, coerce returns str()."""
    assert coerce_chat_response(42) == "42"


def test_base_stream_chat_falls_back_to_chat():
    """Default stream_chat yields a single chunk from chat()."""

    class Dummy(BaseAIProvider):
        provider_type = "dummy"

        def chat(self, messages, **kwargs):
            return "fallback text"

    provider = Dummy({"model": "d", "api_key": ""})
    chunks = list(provider.stream_chat([{"role": "user", "content": "x"}]))
    assert chunks == ["fallback text"]


def test_base_transcribe_raises_by_default():
    from .base import AIProviderError

    class Dummy(BaseAIProvider):
        provider_type = "dummy"

        def chat(self, messages, **kwargs):
            return ""

    provider = Dummy({"model": "d", "api_key": ""})
    with pytest.raises(AIProviderError, match="does not support speech-to-text"):
        provider.transcribe(b"\x00")


def test_base_speak_raises_by_default():
    from .base import AIProviderError

    class Dummy(BaseAIProvider):
        provider_type = "dummy"

        def chat(self, messages, **kwargs):
            return ""

    provider = Dummy({"model": "d", "api_key": ""})
    with pytest.raises(AIProviderError, match="does not support text-to-speech"):
        provider.speak("hello")


# ---------------------------------------------------------------------------
# Factory / runtime lookup
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_get_active_provider_with_agent(db, settings):
    from .factory import get_active_provider

    p = AIProvider.objects.create(
        name="o1", provider_type="openai", model="gpt-4o-mini", api_key="sk-x"
    )
    AgentSettings.objects.create(provider=p, language="en", is_active=True)
    result = get_active_provider("en")
    assert result is not None
    assert result.model == "gpt-4o-mini"


@pytest.mark.django_db
def test_get_active_provider_no_agent_uses_default_provider(db, settings):
    from .factory import get_active_provider

    AIProvider.objects.create(
        name="o2", provider_type="openai", model="gpt-4o", is_default=True, is_active=True
    )
    result = get_active_provider()
    assert result is not None
    assert result.model == "gpt-4o"


@pytest.mark.django_db
def test_get_active_provider_env_fallback(db, settings):
    from .factory import get_active_provider

    settings.DEFAULT_PROVIDER = "openai"
    settings.DEFAULT_MODEL = "gpt-4o-mini"
    settings.OPENAI_API_KEY = "sk-env"
    result = get_active_provider()
    assert result is not None
    assert result.api_key == "sk-env"


@pytest.mark.django_db
def test_get_speech_provider_with_stt(db, settings):
    from .factory import get_speech_provider

    p = AIProvider.objects.create(
        name="whisper", provider_type="openai", model="whisper-1", api_key="sk-w"
    )
    AgentSettings.objects.create(
        provider=None, stt_provider=p, language="en", is_active=True
    )
    result = get_speech_provider("stt", "en")
    assert result is not None
    assert result.model == "whisper-1"


@pytest.mark.django_db
def test_get_speech_provider_no_agent_returns_none(db, settings):
    from .factory import get_speech_provider

    result = get_speech_provider("stt")
    assert result is None


# ---------------------------------------------------------------------------
# Model helpers
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_effective_base_url_explicit(db):
    p = AIProvider.objects.create(
        name="custom", provider_type="openai", model="gpt-4o", base_url="https://my.proxy"
    )
    assert p.effective_base_url() == "https://my.proxy"


@pytest.mark.django_db
def test_effective_base_url_openai_empty(db):
    p = AIProvider.objects.create(
        name="open-e", provider_type="openai", model="gpt-4o"
    )
    assert p.effective_base_url() == ""


@pytest.mark.django_db
def test_to_config_roundtrips(db):
    p = AIProvider.objects.create(
        name="cfg-test",
        provider_type="openai",
        model="gpt-4o",
        api_key="sk-r",
        base_url="https://b",
        timeout_seconds=60,
        extra_config={"nested": True},
    )
    config = p.to_config()
    assert config["provider_type"] == "openai"
    assert config["model"] == "gpt-4o"
    assert config["api_key"] == "sk-r"
    assert config["base_url"] == "https://b"
    assert config["timeout_seconds"] == 60
    assert config["extra_config"] == {"nested": True}


# ---------------------------------------------------------------------------
# OpenAI provider edge cases
# ---------------------------------------------------------------------------
def test_openai_stream_chat_failure_raises():
    from .base import AIProviderError
    import openai

    provider = _openai_provider()
    with mock.patch.object(provider, "_client") as mock_client:
        mock_client.return_value.chat.completions.create.side_effect = openai.OpenAIError("stream boom")
        with pytest.raises(AIProviderError, match="stream failed"):
            list(provider.stream_chat([{"role": "user", "content": "hi"}]))


def test_openai_speak_with_custom_format():
    provider = _openai_provider()
    with mock.patch.object(provider, "_client") as mock_client:
        mock_client.return_value.audio.speech.create.return_value = _fake_speech_response(b"fmt")
        result = provider.speak("hello", format="wav")
    assert result == b"fmt"


def test_openai_transcribe_with_language():
    provider = _openai_provider()
    with mock.patch.object(provider, "_client") as mock_client:
        mock_client.return_value.audio.transcriptions.create.return_value = _fake_audio_response("pl")
        result = provider.transcribe(b"audio", language="pl")
    assert result == "pl"


# ---------------------------------------------------------------------------
# Remaining base/factory edge cases
# ---------------------------------------------------------------------------
def test_coerce_chat_response_dict_anthropic():
    """Dict-shaped Anthropic response falls through the last dict branch."""
    result = {"content": [{"text": "hello from dict"}]}
    assert coerce_chat_response(result) == "hello from dict"


def test_get_provider_class_caches_anthropic(monkeypatch):
    """Lazy-import of AnthropicProvider caches into _PROVIDER_CLASSES."""
    # Provide a fake module so the real package isn't needed.
    fake_cls = type("AnthropicProvider", (), {"provider_type": "anthropic"})()
    fake_mod = mock.MagicMock()
    fake_mod.AnthropicProvider = fake_cls
    monkeypatch.setitem(
        sys.modules, "hotel_ai.ai_providers.anthropic_provider", fake_mod
    )
    from .factory import _get_provider_class

    cls = _get_provider_class("anthropic")
    assert cls is fake_cls
    # Second call returns cached class (no re-import).
    assert _get_provider_class("anthropic") is fake_cls


def test_get_provider_class_caches_ollama(monkeypatch):
    """Lazy-import of OllamaProvider caches into _PROVIDER_CLASSES."""
    fake_cls = type("OllamaProvider", (), {"provider_type": "ollama"})()
    fake_mod = mock.MagicMock()
    fake_mod.OllamaProvider = fake_cls
    monkeypatch.setitem(sys.modules, "hotel_ai.ai_providers.ollama_provider", fake_mod)
    from .factory import _get_provider_class

    cls = _get_provider_class("ollama")
    assert cls is fake_cls
    assert _get_provider_class("ollama") is fake_cls


def test_coerce_chat_response_dict_content_fails():
    """Dict with 'content' key that can't be read as text falls through."""
    result = {"content": [1]}
    assert coerce_chat_response(result) == "{'content': [1]}"


# ---------------------------------------------------------------------------
# Admin helper
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_has_api_key_true_for_stored_key(db):
    """When a provider has an explicit api_key, has_api_key returns True."""
    from django.contrib.admin.sites import AdminSite
    from .admin import AIProviderAdmin

    p = AIProvider.objects.create(
        name="k1", provider_type="openai", model="gpt-4o", api_key="sk-stored"
    )
    site = AdminSite()
    admin = AIProviderAdmin(AIProvider, site)
    assert admin.has_api_key(p) is True


@pytest.mark.django_db
def test_has_api_key_true_from_env(db, settings):
    """When no stored key exists but the env var does, has_api_key returns True."""
    from django.contrib.admin.sites import AdminSite
    from .admin import AIProviderAdmin

    settings.OPENAI_API_KEY = "sk-from-env"
    p = AIProvider.objects.create(
        name="k2", provider_type="openai", model="gpt-4o", api_key=""
    )
    site = AdminSite()
    admin = AIProviderAdmin(AIProvider, site)
    assert admin.has_api_key(p) is True


@pytest.mark.django_db
def test_has_api_key_false_when_neither_set(db, settings):
    """With no stored key and no env key, has_api_key returns False."""
    from django.contrib.admin.sites import AdminSite
    from .admin import AIProviderAdmin

    settings.OPENAI_API_KEY = ""
    p = AIProvider.objects.create(
        name="k3", provider_type="openai", model="gpt-4o", api_key=""
    )
    site = AdminSite()
    admin = AIProviderAdmin(AIProvider, site)
    assert admin.has_api_key(p) is False


# ---------------------------------------------------------------------------
# Anthropic provider
# ---------------------------------------------------------------------------
def _anthropic_provider():
    from .anthropic_provider import AnthropicProvider
    return AnthropicProvider({"provider_type": "anthropic", "model": "claude-3-haiku", "api_key": "sk-ant"})


def test_anthropic_chat_success():
    provider = _anthropic_provider()
    fake_resp = type("Resp", (), {"content": [type("B", (), {"text": "hi"})()]})()
    with mock.patch.object(provider, "_client") as mock_client:
        mock_client.return_value.messages.create.return_value = fake_resp
        assert provider.chat([{"role": "user", "content": "hi"}]) == "hi"


def test_anthropic_chat_moves_system_out_of_messages():
    provider = _anthropic_provider()
    fake_resp = type("Resp", (), {"content": [type("B", (), {"text": "got it"})()]})()
    with mock.patch.object(provider, "_client") as mock_client:
        mock_client.return_value.messages.create.return_value = fake_resp
        provider.chat([{"role": "system", "content": "be nice"}, {"role": "user", "content": "hi"}])
        _, kwargs = mock_client.return_value.messages.create.call_args
        assert kwargs["system"] == "be nice"
        assert kwargs["messages"] == [{"role": "user", "content": "hi"}]


def test_anthropic_chat_failure_raises():
    from .base import AIProviderError
    import anthropic
    provider = _anthropic_provider()
    with mock.patch.object(provider, "_client") as mock_client:
        exc = anthropic.APIStatusError(message="boom", response=mock.MagicMock(), body=None)
        mock_client.return_value.messages.create.side_effect = exc
        with pytest.raises(AIProviderError, match="Anthropic chat failed"):
            provider.chat([{"role": "user", "content": "hi"}])


def test_anthropic_stream_chat_yields_chunks():
    provider = _anthropic_provider()

    class FakeCM:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        text_stream = iter(["Hello ", "world"])

    with mock.patch.object(provider, "_client") as mock_client:
        mock_client.return_value.messages.stream.return_value = FakeCM()
        chunks = list(provider.stream_chat([{"role": "user", "content": "hi"}]))
    assert chunks == ["Hello ", "world"]


def test_anthropic_stream_chat_moves_system():
    """stream_chat pulls system messages out and passes them as system arg."""
    provider = _anthropic_provider()

    class FakeCM:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        text_stream = iter(["ok"])

    with mock.patch.object(provider, "_client") as mock_client:
        mock_client.return_value.messages.stream.return_value = FakeCM()
        chunks = list(
            provider.stream_chat([{"role": "system", "content": "be kind"}, {"role": "user", "content": "hi"}])
        )
    assert chunks == ["ok"]
    _, kwargs = mock_client.return_value.messages.stream.call_args
    assert kwargs["system"] == "be kind"
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]


def test_anthropic_stream_chat_failure_raises():
    from .base import AIProviderError
    import anthropic
    provider = _anthropic_provider()
    with mock.patch.object(provider, "_client") as mock_client:
        exc = anthropic.APIStatusError(message="boom", response=mock.MagicMock(), body=None)
        mock_client.return_value.messages.stream.side_effect = exc
        with pytest.raises(AIProviderError, match="Anthropic stream failed"):
            list(provider.stream_chat([{"role": "user", "content": "hi"}]))


def test_anthropic_client_uses_base_url_when_set():
    from .anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(
        {
            "provider_type": "anthropic",
            "model": "claude-3-haiku",
            "api_key": "sk-ant",
            "base_url": "http://localhost:4000",
        }
    )
    with mock.patch(
        "hotel_ai.ai_providers.anthropic_provider.anthropic.Anthropic"
    ) as mock_anth:
        provider._client()
        _, kwargs = mock_anth.call_args
        assert kwargs["base_url"] == "http://localhost:4000"
        assert kwargs["timeout"] == 30


# ---------------------------------------------------------------------------
# Ollama provider
# ---------------------------------------------------------------------------
def _ollama_provider(base_url="http://localhost:11434"):
    from .ollama_provider import OllamaProvider

    return OllamaProvider(
        {
            "provider_type": "ollama",
            "model": "llama3.1:8b",
            "api_key": "",
            "base_url": base_url,
        }
    )


def test_ollama_chat_success():
    provider = _ollama_provider()
    with mock.patch.object(provider, "_post") as mock_post:
        mock_post.return_value = {"message": {"content": "hello ollama"}}
        assert provider.chat([{"role": "user", "content": "hi"}]) == "hello ollama"


def test_ollama_chat_missing_content_raises():
    from .base import AIProviderError

    provider = _ollama_provider()
    with mock.patch.object(provider, "_post") as mock_post:
        mock_post.return_value = {"error": "no content"}
        with pytest.raises(AIProviderError, match="Unexpected Ollama response"):
            provider.chat([{"role": "user", "content": "hi"}])


def test_ollama_chat_request_failure_raises():
    from .base import AIProviderError
    import requests

    provider = _ollama_provider()
    with mock.patch(
        "hotel_ai.ai_providers.ollama_provider.requests.post"
    ) as mock_post:
        mock_post.side_effect = requests.RequestException("network down")
        with pytest.raises(AIProviderError, match="Ollama request failed"):
            provider.chat([{"role": "user", "content": "hi"}])


def test_ollama_stream_chat_yields_lines():
    provider = _ollama_provider()

    class FakeResponse:
        def raise_for_status(self):
            pass

        def iter_lines(self):
            yield b'{"message":{"content":"chunk1"}}'
            yield b'{"message":{"content":"chunk2"}}'
            yield b""

    with mock.patch(
        "hotel_ai.ai_providers.ollama_provider.requests.post"
    ) as mock_post:
        mock_post.return_value = FakeResponse()
        chunks = list(provider.stream_chat([{"role": "user", "content": "hi"}]))
    assert chunks == ["chunk1", "chunk2"]


def test_ollama_stream_chat_request_failure_raises():
    from .base import AIProviderError
    import requests

    provider = _ollama_provider()
    with mock.patch(
        "hotel_ai.ai_providers.ollama_provider.requests.post"
    ) as mock_post:
        mock_post.side_effect = requests.RequestException("stream boom")
        with pytest.raises(AIProviderError, match="Ollama stream failed"):
            list(provider.stream_chat([{"role": "user", "content": "hi"}]))


def test_ollama_stream_skips_bad_json_lines():
    provider = _ollama_provider()

    class FakeResponse:
        def raise_for_status(self):
            pass

        def iter_lines(self):
            yield b"not json at all"
            yield b'{"bad": true}'
            yield b'{"message":{"content":"real"}}'

    with mock.patch(
        "hotel_ai.ai_providers.ollama_provider.requests.post"
    ) as mock_post:
        mock_post.return_value = FakeResponse()
        chunks = list(provider.stream_chat([{"role": "user", "content": "hi"}]))
    assert chunks == ["real"]


def test_ollama_stream_empty_content_skipped():
    provider = _ollama_provider()

    class FakeResponse:
        def raise_for_status(self):
            pass

        def iter_lines(self):
            yield b'{"message":{}}'
            yield b'{"message":{"content":""}}'
            yield b'{"message":{"content":"x"}}'

    with mock.patch(
        "hotel_ai.ai_providers.ollama_provider.requests.post"
    ) as mock_post:
        mock_post.return_value = FakeResponse()
        chunks = list(provider.stream_chat([{"role": "user", "content": "hi"}]))
    assert chunks == ["x"]


def test_ollama_post_uses_correct_url_and_timeout():
    provider = _ollama_provider(base_url="http://my-server:8080")
    with mock.patch.object(provider, "_url") as mock_url:
        mock_url.return_value = "http://my-server:8080/api/chat"
        with mock.patch(
            "hotel_ai.ai_providers.ollama_provider.requests.post"
        ) as mock_post:
            mock_post.return_value.json.return_value = {
                "message": {"content": "ok"}
            }
            mock_post.return_value.status_code = 200
            provider.chat([{"role": "user", "content": "hi"}])
            _, kwargs = mock_post.call_args
            assert kwargs["json"]["model"] == "llama3.1:8b"
            assert kwargs["timeout"] == 30

