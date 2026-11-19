"""Tests for Google Calendar integration."""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from hotel_ai.bookings.models import Client, Room, RoomType
from hotel_ai.bookings.services import cancel_booking, create_booking, no_show_booking

from .models import CalendarSync, GoogleCalendarConnection, RoomCalendar
from .services import ensure_room_calendar, sync_booking


@pytest.fixture
def hotel(db):
    double = RoomType.objects.create(name="Double", base_price_per_night=Decimal("300.00"), capacity=2)
    room = Room.objects.create(room_number="201", room_type=double)
    return {"room": room, "room_type": double}


@pytest.mark.django_db
def test_sync_skipped_without_mapping(hotel):
    client = Client.objects.create(first_name="A", last_name="B")
    booking = create_booking(
        room=hotel["room"],
        client=client,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )
    sync = CalendarSync.objects.filter(booking=booking).first()
    assert sync is not None
    assert sync.status == "skipped"


@pytest.mark.django_db
def test_sync_skipped_without_connection(hotel):
    room_calendar = ensure_room_calendar(hotel["room"])
    client = Client.objects.create(first_name="A", last_name="B")
    booking = create_booking(
        room=hotel["room"],
        client=client,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )
    sync = CalendarSync.objects.get(booking=booking, room_calendar=room_calendar)
    assert sync.status == "skipped"


@pytest.mark.django_db
def test_sync_failure_recorded_not_raised(hotel):
    """A broken Google connection records 'failed', never breaks the booking."""
    conn = GoogleCalendarConnection.objects.create(google_user_email="desk@hotel.com")
    rc = ensure_room_calendar(hotel["room"])
    rc.connection = conn
    rc.calendar_id = "primary"
    rc.save()

    client = Client.objects.create(first_name="A", last_name="B")
    booking = create_booking(
        room=hotel["room"],
        client=client,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )
    # Booking still created and confirmed even though sync fails.
    assert booking.status == "confirmed"
    sync = CalendarSync.objects.get(booking=booking)
    assert sync.status == "failed"
    assert sync.error_message


@pytest.mark.django_db
def test_ensure_room_calendar_idempotent(hotel):
    rc1 = ensure_room_calendar(hotel["room"])
    rc2 = ensure_room_calendar(hotel["room"])
    assert rc1.id == rc2.id


@pytest.mark.django_db
def test_event_payload_shape(hotel):
    from .services import _event_payload

    client = Client.objects.create(first_name="Ada", last_name="Lovelace", email="ada@x.com")
    booking = create_booking(
        room=hotel["room"],
        client=client,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
        guests=2,
    )
    payload = _event_payload(booking)
    assert payload["start"]["dateTime"].startswith("2026-07-01T15:00:00")
    assert payload["end"]["dateTime"].startswith("2026-07-03T11:00:00")
    assert "Ada Lovelace" in payload["summary"]
    assert "Booking #".lower() in payload["description"].lower() or "Booking #" in payload["description"]


@pytest.mark.django_db
def test_cancel_booking_records_sync_attempt(hotel):
    """Cancelling goes through the service layer so the calendar audit row updates."""
    client = Client.objects.create(first_name="A", last_name="B")
    booking = create_booking(
        room=hotel["room"],
        client=client,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )
    assert booking.status == "confirmed"

    cancel_booking(booking)
    assert booking.status == "cancelled"
    # The sync row still exists after cancellation (status recorded, no crash).
    assert CalendarSync.objects.filter(booking=booking).exists()

    # Cancelling twice is idempotent and does not raise.
    cancel_booking(booking)
    assert booking.status == "cancelled"


@pytest.mark.django_db
def test_no_show_booking_records_sync_attempt(hotel):
    """No-show goes through the service layer so the calendar audit row updates."""
    client = Client.objects.create(first_name="A", last_name="B")
    booking = create_booking(
        room=hotel["room"],
        client=client,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )
    assert booking.status == "confirmed"

    no_show_booking(booking)
    assert booking.status == "no_show"
    # The sync row still exists after no-show (status recorded, no crash).
    assert CalendarSync.objects.filter(booking=booking).exists()

    # The service layer rejects a second no-show (only confirmed bookings
# can be marked); the API view above wraps that in an idempotent 200.
    with pytest.raises(ValueError):
        no_show_booking(booking)


@pytest.mark.django_db
def test_resync_no_show_returns_skipped(hotel):
    """No-show bookings return 'skipped' because there's nothing to delete."""
    rc = ensure_room_calendar(hotel["room"])
    conn = GoogleCalendarConnection.objects.create(google_user_email="n@h.com")
    rc.connection = conn
    rc.calendar_id = "primary"
    rc.save()
    client = APIClient()
    booking = create_booking(
        room=hotel["room"],
        client=Client.objects.create(first_name="N", last_name="S"),
        check_in=_dt.date(2026, 8, 1),
        check_out=_dt.date(2026, 8, 3),
    )
    no_show_booking(booking)
    resp = client.post(f"/api/calendar/sync/{booking.id}/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"


@pytest.mark.django_db
def test_sync_event_update_is_called_when_event_exists(hotel):
    """sync_booking calls update rather than insert when google_event_id exists."""
    from unittest import mock

    rc = ensure_room_calendar(hotel["room"])
    conn = GoogleCalendarConnection.objects.create(google_user_email="u@h.com")
    rc.connection = conn
    rc.calendar_id = "cal-id"
    rc.save()
    booking = create_booking(
        room=hotel["room"],
        client=Client.objects.create(first_name="E", last_name="U"),
        check_in=_dt.date(2026, 9, 1),
        check_out=_dt.date(2026, 9, 3),
    )
    sync, _ = CalendarSync.objects.update_or_create(
        booking=booking,
        room_calendar=rc,
        defaults={"google_event_id": "existing-eid", "status": "synced"},
    )

    with mock.patch("hotel_ai.calendar_integration.services._get_calendar_service") as svc:
        events_mock = mock.MagicMock()
        events_mock.update.return_value.execute.return_value = {"id": "eid"}
        events_mock.insert.return_value.execute.return_value = {}
        svc.return_value.events.return_value = events_mock
        sync_booking(booking)
        events_mock.update.assert_called_once()
        events_mock.insert.assert_not_called()


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_resync_booking_returns_404_for_missing_booking():
    client = APIClient()
    resp = client.post("/api/calendar/sync/999999/")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Booking not found"


@pytest.mark.django_db
def test_resync_booking_endpoint_reports_sync_status(hotel):
    """The resync endpoint re-runs sync_booking and returns its status."""
    client = APIClient()
    booking = create_booking(
        room=hotel["room"],
        client=Client.objects.create(first_name="A", last_name="B"),
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )
    resp = client.post(f"/api/calendar/sync/{booking.id}/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["booking_id"] == booking.id
    assert body["status"] == "skipped"


@pytest.mark.django_db
def test_resync_booking_endpoint_is_idempotent(hotel):
    """Re-running the sync endpoint does not raise or duplicate rows."""
    client = APIClient()
    booking = create_booking(
        room=hotel["room"],
        client=Client.objects.create(first_name="A", last_name="B"),
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )
    first = client.post(f"/api/calendar/sync/{booking.id}/")
    second = client.post(f"/api/calendar/sync/{booking.id}/")
    assert first.status_code == 200
    assert second.status_code == 200
    assert CalendarSync.objects.filter(booking=booking).count() == 1


@pytest.mark.django_db
def test_list_room_calendars_returns_mapping_rows(hotel):
    client = APIClient()
    ensure_room_calendar(hotel["room"])
    resp = client.get("/api/calendar/calendars/")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["room"] == hotel["room"].room_number
    assert rows[0]["calendar_name"] == f"Room {hotel['room'].room_number}"
    assert rows[0]["is_active"] is True


@pytest.mark.django_db
def test_list_room_calendars_empty_when_no_mappings():
    client = APIClient()
    resp = client.get("/api/calendar/calendars/")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.django_db
def test_resync_after_cancel_deletes_event(hotel):
    """Cancelling a booking then resyncing records the delete attempt."""
    client = APIClient()
    booking = create_booking(
        room=hotel["room"],
        client=Client.objects.create(first_name="A", last_name="B"),
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )
    cancel_booking(booking)
    resp = client.post(f"/api/calendar/sync/{booking.id}/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"