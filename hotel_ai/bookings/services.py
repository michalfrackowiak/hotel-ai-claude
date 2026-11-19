"""Booking-engine service layer.

Everything the agent and the REST API need to *do* with reservations lives
here so the business rules have a single, testable home.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from typing import Iterable, Optional

import logging

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

from .models import (
    Booking,
    BookingLog,
    BookingStatus,
    Client,
    PricingRule,
    Room,
    RoomType,
)


def resolve_client(
    *,
    first_name: str,
    last_name: str = "",
    email: str = "",
    phone: str = "",
    language: str = "en",
) -> Client:
    """Find or create a client by name + email."""
    client, _created = Client.objects.get_or_create(
        first_name=first_name,
        last_name=last_name,
        email=email,
        defaults={"phone": phone, "language": language},
    )
    if not client.phone and phone:
        client.phone = phone
    if not client.language or client.language == "en" and language != "en":
        client.language = language
    client.save()
    return client


def resolve_pricing_rule(
    room_type: RoomType, check_in: _dt.date
) -> Optional[PricingRule]:
    """Return the highest-priority pricing rule active on ``check_in``."""
    qs = PricingRule.objects.filter(
        room_type=room_type,
        starts_at__lte=check_in,
        ends_at__gte=check_in,
    ).order_by("priority", "starts_at")
    return qs.first()


def effective_price(room_type: RoomType, check_in: _dt.date) -> Decimal:
    """Nightly price for a room type on a given date (rule wins, else base)."""
    rule = resolve_pricing_rule(room_type, check_in)
    if rule is not None:
        return rule.price_per_night
    return room_type.base_price_per_night


def calculate_total(
    room_type: RoomType,
    check_in: _dt.date,
    check_out: _dt.date,
) -> Decimal:
    """Sum the effective nightly price across the stay."""
    nights = (check_out - check_in).days
    if nights <= 0:
        return Decimal("0.00")
    return sum(
        effective_price(room_type, check_in + _dt.timedelta(days=i))
        for i in range(nights)
    )


def overlapping_bookings(room: Room, check_in: _dt.date, check_out: _dt.date):
    """Existing bookings that overlap the requested interval."""
    return Booking.objects.filter(
        room=room,
        check_in__lt=check_out,
        check_out__gt=check_in,
    ).exclude(status=BookingStatus.CANCELLED)


def find_available_rooms(
    *,
    room_type: Optional[RoomType] = None,
    check_in: _dt.date,
    check_out: _dt.date,
    guests: int = 1,
) -> list[Room]:
    """Return active rooms that are free and big enough for the party."""
    if check_out <= check_in:
        return []
    qs = Room.objects.filter(is_active=True).select_related("room_type")
    if room_type is not None:
        qs = qs.filter(room_type=room_type)
    available: list[Room] = []
    for room in qs:
        if room.room_type.capacity < guests:
            continue
        if not overlapping_bookings(room, check_in, check_out).exists():
            available.append(room)
    return available


def availability_summary(
    *,
    check_in: _dt.date,
    check_out: _dt.date,
    guests: int = 1,
) -> list[dict]:
    """Per-room-type availability summary for the API."""
    summary: list[dict] = []
    for room_type in RoomType.objects.all().order_by("name"):
        rooms = find_available_rooms(
            room_type=room_type,
            check_in=check_in,
            check_out=check_out,
            guests=guests,
        )
        summary.append(
            {
                "room_type": room_type.name,
                "capacity": room_type.capacity,
                "base_price_per_night": str(room_type.base_price_per_night),
                "effective_price_per_night": str(
                    effective_price(room_type, check_in)
                ),
                "total_price": str(
                    calculate_total(room_type, check_in, check_out)
                ),
                "available_rooms": len(rooms),
                "available": len(rooms) > 0,
            }
        )
    return summary


@transaction.atomic
def create_booking(
    *,
    room: Room,
    client: Client,
    check_in: _dt.date,
    check_out: _dt.date,
    guests: int = 1,
    source: str = "chat",
    notes: str = "",
) -> Booking:
    """Create a confirmed booking, enforcing every invariant."""
    if check_out <= check_in:
        raise ValueError("check_out must be after check_in")
    if guests > room.room_type.capacity:
        raise ValueError(
            f"Room capacity is {room.room_type.capacity}; {guests} requested."
        )
    if overlapping_bookings(room, check_in, check_out).exists():
        raise ValueError("Room is not available for the requested dates.")

    rule = resolve_pricing_rule(room.room_type, check_in)
    total = calculate_total(room.room_type, check_in, check_out)

    booking = Booking.objects.create(
        room=room,
        client=client,
        pricing_rule=rule,
        check_in=check_in,
        check_out=check_out,
        guests=guests,
        status=BookingStatus.CONFIRMED,
        total_price=total,
        currency="PLN",
        source=source,
        notes=notes,
    )

    BookingLog.objects.create(booking=booking, action="created", notes=notes)

    # Best-effort push to the room's calendar (no Google creds = skipped).
    try:
        from hotel_ai.calendar_integration.services import sync_booking

        sync_booking(booking)
    except Exception:  # noqa: BLE001 - calendar sync must never block a booking
        logger.debug("Calendar sync skipped for booking %s", booking.id, exc_info=True)

    return booking


def check_in_booking(booking: Booking) -> Booking:
    """Mark an existing confirmed booking as checked in."""
    if booking.status != BookingStatus.CONFIRMED:
        raise ValueError(f"Booking {booking.id} is {booking.status}, expected confirmed")
    booking.status = BookingStatus.CHECKED_IN
    booking.save()
    BookingLog.objects.create(booking=booking, action="checked_in")
    return booking


def check_out_booking(booking: Booking) -> Booking:
    """Mark a checked-in booking as checked out."""
    if booking.status != BookingStatus.CHECKED_IN:
        raise ValueError(f"Booking {booking.id} is {booking.status}, expected checked_in")
    booking.status = BookingStatus.CHECKED_OUT
    booking.save()
    BookingLog.objects.create(booking=booking, action="checked_out")
    return booking


def cancel_booking(booking: Booking) -> Booking:
    """Cancel a booking (idempotent) and sync the cancellation to the calendar."""
    if booking.status != BookingStatus.CANCELLED:
        booking.status = BookingStatus.CANCELLED
        booking.save()
        BookingLog.objects.create(booking=booking, action="cancelled")
        try:
            from hotel_ai.calendar_integration.services import sync_booking

            sync_booking(booking)
        except Exception:  # noqa: BLE001
            logger.debug("Calendar sync skipped for cancelled booking %s", booking.id, exc_info=True)
    return booking


def no_show_booking(booking: Booking) -> Booking:
    """Mark a confirmed booking whose guest did not arrive as a no-show.

    Only confirmed bookings can become no-shows; already-cancelled or
    checked-in bookings are rejected so the action cannot be applied twice.
    """
    if booking.status != BookingStatus.CONFIRMED:
        raise ValueError(
            f"Booking {booking.id} is {booking.status}, expected confirmed"
        )
    booking.status = BookingStatus.NO_SHOW
    booking.save()
    BookingLog.objects.create(booking=booking, action="no_show")
    return booking


def parse_date(value: str) -> _dt.date:
    """Parse ``YYYY-MM-DD`` into a date, raising a clear error otherwise."""
    try:
        return _dt.date.fromisoformat(value)
    except ValueError as exc:  # pragma: no cover - exercised via API tests
        raise ValueError(f"Invalid date '{value}', expected YYYY-MM-DD") from exc


def today() -> _dt.date:
    return timezone.now().date()


__all__ = [
    "resolve_client",
    "resolve_pricing_rule",
    "effective_price",
    "calculate_total",
    "overlapping_bookings",
    "find_available_rooms",
    "availability_summary",
    "create_booking",
    "check_in_booking",
    "check_out_booking",
    "cancel_booking",
    "no_show_booking",
    "parse_date",
    "today",
]