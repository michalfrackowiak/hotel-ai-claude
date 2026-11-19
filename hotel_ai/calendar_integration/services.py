"""Google Calendar sync service.

The sync is *best-effort*: if no OAuth connection or calendar mapping exists
we record a ``skipped`` CalendarSync row rather than failing the booking.
When Google credentials are available we push the event and update it on
re-sync (cancellations delete the event).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from django.conf import settings
from django.utils import timezone

from hotel_ai.bookings.models import Booking

from .models import CalendarSync, RoomCalendar

logger = logging.getLogger(__name__)


def _get_calendar_service(connection):
    """Build a Google Calendar service from a connection row."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    scopes = connection.scopes or list(settings.GOOGLE_CALENDAR_SCOPES)
    creds = Credentials(
        token=connection.access_token,
        refresh_token=connection.refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=None,
        client_secret=None,
        scopes=scopes,
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _event_payload(booking: Booking) -> dict:
    check_in = booking.check_in
    check_out = booking.check_out
    return {
        "summary": f"Room {booking.room.room_number} - {booking.client.full_name}",
        "location": booking.room.room_number,
        "description": (
            f"Booking #{booking.id}\n"
            f"Guest: {booking.client.full_name} ({booking.client.email or 'no email'})\n"
            f"Guests: {booking.guests}\n"
            f"Total: {booking.total_price} {booking.currency}\n"
            f"Status: {booking.status}"
        ),
        "start": {
            "dateTime": f"{check_in.isoformat()}T15:00:00",
            "timeZone": "Europe/Warsaw",
        },
        "end": {
            "dateTime": f"{(check_out + timedelta(days=0)).isoformat()}T11:00:00",
            "timeZone": "Europe/Warsaw",
        },
        "reminders": {
            "useDefault": True,
            "overrides": [
                {"method": "email", "minutes": 24 * 60},
                {"method": "popup", "minutes": 60},
            ],
        },
    }


def sync_booking(booking: Booking) -> CalendarSync:
    """Push a booking to its room's calendar. Returns the sync record."""
    room_calendar = (
        RoomCalendar.objects.filter(room=booking.room, is_active=True).first()
    )
    if room_calendar is None:
        room_calendar = ensure_room_calendar(booking.room)
    if not room_calendar.connection_id or not room_calendar.calendar_id:
        sync, _ = CalendarSync.objects.update_or_create(
            booking=booking,
            room_calendar=room_calendar,
            defaults={
                "status": "skipped",
                "error_message": "No calendar mapping configured for this room.",
            },
        )
        return sync

    sync, _ = CalendarSync.objects.update_or_create(
        booking=booking,
        room_calendar=room_calendar,
        defaults={"status": "pending"},
    )

    if booking.status in ("cancelled", "no_show"):
        return _delete_event(sync, room_calendar)

    try:
        service = _get_calendar_service(room_calendar.connection)
        payload = _event_payload(booking)
        if sync.google_event_id:
            event = (
                service.events()
                .update(
                    calendarId=room_calendar.calendar_id,
                    eventId=sync.google_event_id,
                    body=payload,
                )
                .execute()
            )
        else:
            event = (
                service.events()
                .insert(calendarId=room_calendar.calendar_id, body=payload)
                .execute()
            )
        sync.google_event_id = event.get("id", "")
        sync.status = "synced"
        sync.error_message = ""
    except Exception as exc:  # noqa: BLE001 - any failure is recorded, not raised
        logger.warning("Calendar sync failed for booking %s: %s", booking.id, exc)
        sync.status = "failed"
        sync.error_message = str(exc)[:512]
    sync.save()
    return sync


def _delete_event(sync: CalendarSync, room_calendar: RoomCalendar) -> CalendarSync:
    if not sync.google_event_id:
        sync.status = "skipped"
        sync.error_message = "No event to delete."
        sync.save()
        return sync
    try:
        service = _get_calendar_service(room_calendar.connection)
        service.events().delete(
            calendarId=room_calendar.calendar_id,
            eventId=sync.google_event_id,
        ).execute()
        sync.status = "synced"
        sync.error_message = ""
    except Exception as exc:  # noqa: BLE001
        sync.status = "failed"
        sync.error_message = str(exc)[:512]
    sync.save()
    return sync


def ensure_room_calendar(room) -> RoomCalendar:
    """Create a local mapping row if one does not exist yet."""
    rc, _ = RoomCalendar.objects.get_or_create(
        room=room, defaults={"calendar_name": f"Room {room.room_number}"}
    )
    return rc


__all__ = ["sync_booking", "ensure_room_calendar", "_event_payload"]