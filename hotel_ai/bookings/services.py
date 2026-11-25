"""Booking-engine service layer.

Everything the agent and the REST API need to *do* with reservations lives
here so the business rules have a single, testable home.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from typing import Iterable, Optional

import logging

from django.db import models, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

from .models import (
    Booking,
    BookingLog,
    BookingStatus,
    CancellationPolicy,
    Client,
    GroupBooking,
    LoyaltyPoint,
    Notification,
    PricingRule,
    RatePlan,
    Room,
    RoomType,
    WaitlistEntry,
    DigitalKey,
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
    rate_plan: Optional[RatePlan] = None,
) -> Decimal:
    """Sum the effective nightly price across the stay, applying rate plan discounts.

    Supported rate plan modifiers:
    - ``stay_x_pay_nights``: guest pays only N nights; remaining are free.
    - ``corporate_discount_pct``: percentage off the total.
    - ``is_non_refundable`` / ``includes_breakfast``: handled at policy/email level.
    """
    nights = (check_out - check_in).days
    if nights <= 0:
        return Decimal("0.00")

    base = sum(
        effective_price(room_type, check_in + _dt.timedelta(days=i))
        for i in range(nights)
    )

    # Apply stay-X-pay-Y: guest pays for only N nights
    if rate_plan is not None and rate_plan.stay_x_pay_nights is not None:
        pay_nights = min(rate_plan.stay_x_pay_nights, nights)
        base = sum(
            effective_price(room_type, check_in + _dt.timedelta(days=i))
            for i in range(pay_nights)
        )

    # Apply corporate discount percentage
    if rate_plan is not None and rate_plan.corporate_discount_pct is not None:
        discount = base * (rate_plan.corporate_discount_pct / Decimal("100"))
        base = base - discount

    return max(base, Decimal("0.00"))


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
    rate_plan: Optional[RatePlan] = None,
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
    total = calculate_total(
        room.room_type, check_in, check_out, rate_plan=rate_plan
    )

    booking = Booking.objects.create(
        room=room,
        client=client,
        pricing_rule=rule,
        rate_plan=rate_plan,
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

    # Create in-app notification.
    try:
        booking.send_notification(
            Notification.Type.CONFIRMED,
            f"Your booking at room {booking.room.room_number} has been confirmed!",
        )
    except Exception:  # noqa: BLE001
        logger.debug("Notification creation failed for booking %s", booking.id, exc_info=True)

    # Best-effort push to the room's calendar (no Google creds = skipped).
    try:
        from hotel_ai.calendar_integration.services import sync_booking

        sync_booking(booking)
    except Exception:  # noqa: BLE001 - calendar sync must never block a booking
        logger.debug("Calendar sync skipped for booking %s", booking.id, exc_info=True)

    # Best-effort confirmation email.
    try:
        from .emails import send_booking_confirmation
        if booking.client.email:
            send_booking_confirmation(booking)
    except Exception:  # noqa: BLE001
        logger.debug("Booking confirmation email failed for %s", booking.id, exc_info=True)

    # Best-effort SMS notification.
    try:
        from .sms import send_sms_confirmation
        if booking.client.phone:
            send_sms_confirmation(booking)
    except Exception:  # noqa: BLE001
        logger.debug("SMS confirmation failed for %s", booking.id, exc_info=True)

    return booking


def check_in_booking(booking: Booking) -> Booking:
    """Mark an existing confirmed booking as checked in."""
    if booking.status != BookingStatus.CONFIRMED:
        raise ValueError(f"Booking {booking.id} is {booking.status}, expected confirmed")
    booking.status = BookingStatus.CHECKED_IN
    booking.save()
    BookingLog.objects.create(booking=booking, action="checked_in")

    # Create in-app notification.
    try:
        booking.send_notification(
            Notification.Type.CHECKED_IN,
            f"Welcome! You have checked into room {booking.room.room_number}.",
        )
    except Exception:  # noqa: BLE001
        logger.debug("Notification creation failed for booking %s", booking.id, exc_info=True)

    try:
        from .emails import send_checkin_instructions
        if booking.client.email:
            send_checkin_instructions(booking)
    except Exception:  # noqa: BLE001
        logger.debug("Check-in email failed for booking %s", booking.id, exc_info=True)

    # Best-effort check-in SMS notification.
    try:
        from .sms import send_sms_checkin
        if booking.client.phone:
            send_sms_checkin(booking)
    except Exception:  # noqa: BLE001
        logger.debug("SMS check-in failed for booking %s", booking.id, exc_info=True)

    return booking


def check_out_booking(booking: Booking) -> Booking:
    """Mark a checked-in booking as checked out and award loyalty points."""
    if booking.status != BookingStatus.CHECKED_IN:
        raise ValueError(f"Booking {booking.id} is {booking.status}, expected checked_in")
    booking.status = BookingStatus.CHECKED_OUT
    booking.save()
    BookingLog.objects.create(booking=booking, action="checked_out")

    # Award loyalty points (50 per night, max 1000)
    from django.conf import settings
    POINTS_PER_NIGHT = getattr(settings, "LOYALTY_POINTS_PER_NIGHT", 50)
    MAX_POINTS = getattr(settings, "LOYALTY_MAX_POINTS", 1000)
    earned_points = min(booking.nights * POINTS_PER_NIGHT, MAX_POINTS)
    if earned_points > 0:
        LoyaltyPoint.objects.create(
            client=booking.client,
            booking=booking,
            points=earned_points,
            action=LoyaltyPoint.Action.EARNED,
            description=f"Earned {earned_points} points for {booking.nights} nights",
        )
    return booking


def cancel_booking(booking: Booking) -> Booking:
    """Cancel a booking (idempotent) and sync the cancellation to the calendar."""
    if booking.status != BookingStatus.CANCELLED:
        booking.status = BookingStatus.CANCELLED
        booking.save()
        BookingLog.objects.create(booking=booking, action="cancelled")

        # Create in-app notification.
        try:
            booking.send_notification(
                Notification.Type.CANCELLED,
                f"Your booking at room {booking.room.room_number} has been cancelled.",
            )
        except Exception:  # noqa: BLE001
            logger.debug("Notification creation failed for booking %s", booking.id, exc_info=True)

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

    The calendar event is updated (or deleted) so the room no longer shows
    as occupied for the stay — the room is available for re-booking.
    """
    if booking.status != BookingStatus.CONFIRMED:
        raise ValueError(
            f"Booking {booking.id} is {booking.status}, expected confirmed"
        )
    booking.status = BookingStatus.NO_SHOW
    booking.save()
    BookingLog.objects.create(booking=booking, action="no_show")
    try:
        from hotel_ai.calendar_integration.services import sync_booking

        sync_booking(booking)
    except Exception:  # noqa: BLE001
        logger.debug("Calendar sync skipped for no-show booking %s", booking.id, exc_info=True)
    return booking


def parse_date(value: str) -> _dt.date:
    """Parse ``YYYY-MM-DD`` into a date, raising a clear error otherwise."""
    try:
        return _dt.date.fromisoformat(value)
    except ValueError as exc:  # pragma: no cover - exercised via API tests
        raise ValueError(f"Invalid date '{value}', expected YYYY-MM-DD") from exc


def today() -> _dt.date:
    return timezone.now().date()


def validate_promo_code(code: str, check_in: _dt.date) -> PromoCode:
    """Validate and return a promo code if valid for the given check-in date."""
    from .models import PromoCode

    try:
        promo = PromoCode.objects.get(code__iexact=code)
    except PromoCode.DoesNotExist:
        raise ValueError("Invalid promo code")

    if not promo.is_valid(check_in):
        raise ValueError("Promo code is not valid for the selected dates")

    return promo


def apply_promo_code(booking: Booking, code: str) -> Booking:
    """Apply a promo code to a booking, updating its total price."""
    promo = validate_promo_code(code, booking.check_in)
    new_total = promo.apply(booking.total_price)
    booking.total_price = new_total
    booking.save(update_fields=["total_price"])
    promo.current_uses += 1
    promo.save(update_fields=["current_uses"])
    BookingLog.objects.create(
        booking=booking,
        action="promo_applied",
        notes=f"Applied promo {promo.code}: {booking.total_price} {booking.currency}"
    )
    return booking


def modify_booking_dates(
    booking: Booking,
    check_in: _dt.date,
    check_out: _dt.date,
    *,
    guests: Optional[int] = None,
) -> tuple[Booking, Decimal]:
    """Modify a confirmed or checked-in booking's dates and/or guests.

    Returns ``(booking, price_change)`` where ``price_change`` is the delta
    (positive = guest owes more, negative = refund due).
    """
    if booking.status not in (BookingStatus.CONFIRMED, BookingStatus.CHECKED_IN):
        raise ValueError(f"Cannot modify booking with status '{booking.status}'")

    if check_out <= check_in:
        raise ValueError("check_out must be after check_in")

    new_guests = guests if guests is not None else booking.guests

    # Check capacity for the room type
    if new_guests > booking.room.room_type.capacity:
        raise ValueError(
            f"Room capacity is {booking.room.room_type.capacity}; "
            f"{new_guests} guests requested."
        )

    # Check availability: exclude this booking itself when checking overlaps
    old_check_in = booking.check_in
    old_check_out = booking.check_out
    old_rooms = list(overlapping_bookings(booking.room, old_check_in, old_check_out))
    old_rooms = [r for r in old_rooms if r.id != booking.id]

    old_guests = booking.guests
    new_overlaps = Booking.objects.filter(
        room=booking.room,
        check_in__lt=check_out,
        check_out__gt=check_in,
    ).exclude(id=booking.id).exclude(status=BookingStatus.CANCELLED)

    if new_overlaps.exists():
        raise ValueError("The requested dates are not available for this room.")

    # Recalculate pricing
    rule = resolve_pricing_rule(booking.room.room_type, check_in)
    new_total = calculate_total(booking.room.room_type, check_in, check_out)
    price_change = new_total - booking.total_price

    booking.check_in = check_in
    booking.check_out = check_out
    if guests is not None:
        booking.guests = new_guests
    booking.pricing_rule = rule
    booking.total_price = new_total
    booking.save(update_fields=["check_in", "check_out", "guests", "pricing_rule", "total_price"])
    BookingLog.objects.create(
        booking=booking,
        action="dates_modified",
        notes=(
            f"Dates changed from {old_check_in}→{old_check_out} to {check_in}→{check_out}"
            + (f", guests {old_guests}→{new_guests}" if guests is not None else "")
            + f", price change: {price_change:+.2f} {booking.currency}"
        ),
    )
    return booking, price_change


def estimate_arrival_update(booking: Booking, estimated_arrival: str) -> Booking:
    """Update the estimated arrival time on a confirmed/checked-in booking."""
    if booking.status not in (BookingStatus.CONFIRMED, BookingStatus.PENDING):
        raise ValueError(f"Cannot update arrival for booking with status '{booking.status}'")
    booking.notes = (booking.notes + "\nEstimated arrival: " + estimated_arrival).strip()
    booking.save(update_fields=["notes"])
    BookingLog.objects.create(
        booking=booking,
        action="arrival_updated",
        notes=f"Estimated arrival set to {estimated_arrival}",
    )
    return booking


def estimate_cancellation_refund(
    booking: Booking,
    *,
    today: Optional[_dt.date] = None,
) -> dict:
    """Return refund information for cancelling a booking.

    Uses the booking's pricing_rule or the default 'Standard' policy if none set.
    Returns a dict with breakdown of refund calculation.
    """
    from django.utils import timezone as _tz

    today = today or _tz.now().date()
    days_until_checkin = (booking.check_in - today).days

    # Get the applicable cancellation policy
    policy = CancellationPolicy.objects.filter(name="Standard").first()
    if policy is None:
        policy = CancellationPolicy.objects.first()
    if policy is None:
        # Default liberal policy: full refund until check-in day
        policy = CancellationPolicy(
            name="Default",
            free_cancellation_days=0,
            partial_refund_pct=100,
            non_refundable_days=0,
        )

    if days_until_checkin <= 0:
        refund = Decimal("0.00")
        note = "Check-in date has passed."
    elif days_until_checkin >= policy.free_cancellation_days:
        refund = booking.total_price
        note = f"Full refund within free cancellation window ({policy.free_cancellation_days} days)."
    elif days_until_checkin <= policy.non_refundable_days:
        refund = Decimal("0.00")
        note = f"No refund — cancelled less than {policy.non_refundable_days} days before check-in."
    else:
        refund = booking.total_price * Decimal(str(policy.partial_refund_pct)) / Decimal("100")
        note = f"Partial refund at {policy.partial_refund_pct}% — between free and non-refundable windows."

    refund = round(refund, 2)

    return {
        "booking_id": booking.id,
        "total_price": str(booking.total_price),
        "currency": booking.currency,
        "days_until_checkin": days_until_checkin,
        "policy_name": policy.name,
        "refund_amount": str(refund),
        "refund_percentage": float(refund / booking.total_price * 100) if booking.total_price > 0 else 0,
        "note": note,
    }




def create_group_booking(
    *,
    client: Client,
    name: str,
    rooms: list,
    check_in: _dt.date,
    check_out: _dt.date,
    guests: int = 1,
    source: str = "chat",
    notes: str = "",
) -> tuple[GroupBooking, list]:
    """Create a group booking with multiple rooms under one reservation.
    
    Returns (group_booking, created_bookings).
    Raises ValueError if any room is not available.
    """
    if check_out <= check_in:
        raise ValueError("check_out must be after check_in")
    
    # Validate all rooms are available first
    for room in rooms:
        if room.room_type.capacity < guests:
            raise ValueError(
                f"Room {room.room_number} capacity is {room.room_type.capacity}; {guests} requested."
            )
        if overlapping_bookings(room, check_in, check_out).exists():
            raise ValueError(f"Room {room.room_number} is not available for the requested dates.")
    
    group = GroupBooking.objects.create(
        client=client,
        name=name,
        total_rooms=len(rooms),
        currency="PLN",
        notes=notes,
    )
    
    created = []
    total_price = Decimal("0.00")
    with transaction.atomic():
        for room in rooms:
            rule = resolve_pricing_rule(room.room_type, check_in)
            price = calculate_total(room.room_type, check_in, check_out)
            b = Booking.objects.create(
                room=room,
                client=client,
                pricing_rule=rule,
                check_in=check_in,
                check_out=check_out,
                guests=guests,
                status=BookingStatus.CONFIRMED,
                total_price=price,
                currency="PLN",
                source=source,
                notes=notes,
                group_booking=group,
            )
            BookingLog.objects.create(booking=b, action="created", notes=f"Part of group '{name}'")
            total_price += price
            created.append(b)
        
        group.total_price = total_price
        group.save(update_fields=["total_price"])
        
        # Create notifications for each booking
        for b in created:
            try:
                b.send_notification(
                    Notification.Type.CONFIRMED,
                    f"Your booking at room {b.room.room_number} as part of group '{name}' has been confirmed!",
                )
            except Exception:
                logger.debug("Notification creation failed for group booking %s", b.id, exc_info=True)
    
    return group, created


def get_group_bookings(client: Client) -> list:
    """Return all group bookings for a client."""
    return list(client.group_bookings.all())


def add_waitlist_entry(
    *,
    client: Client,
    room_type_id: int,
    check_in: _dt.date,
    check_out: _dt.date,
    guests: int = 1,
    notes: str = "",
) -> WaitlistEntry:
    """Add a guest to the waitlist for a room type on given dates."""
    if check_out <= check_in:
        raise ValueError("check_out must be after check_in.")
    try:
        room_type = RoomType.objects.get(pk=room_type_id)
    except RoomType.DoesNotExist:
        raise ValueError(f"Room type {room_type_id} not found.")
    return WaitlistEntry.objects.create(
        client=client,
        room_type=room_type,
        check_in=check_in,
        check_out=check_out,
        guests=guests,
        notes=notes,
    )


def cancel_waitlist_entry(entry: WaitlistEntry) -> WaitlistEntry:
    """Cancel a waitlist entry (mark as cancelled)."""
    if entry.status == WaitlistEntry.Status.CANCELLED:
        return entry
    entry.status = WaitlistEntry.Status.CANCELLED
    entry.save(update_fields=["status", "updated_at"])
    return entry


def mark_waitlist_as_booked(entry: WaitlistEntry) -> WaitlistEntry:
    """Mark a waitlist entry as converted into a booking."""
    if entry.status != WaitlistEntry.Status.NOTIFIED:
        raise ValueError("Can only mark a notified entry as booked.")
    entry.status = WaitlistEntry.Status.BOOKED
    entry.save(update_fields=["status", "updated_at"])
    Notification.objects.create(
        type=Notification.Type.CONFIRMED,
        recipient=entry.client,
        message=(
            f"Your waitlist request for a {entry.room_type.name} room "
            f"({entry.check_in:%Y-%m-%d} → {entry.check_out:%Y-%m-%d}) "
            f"has been converted into a confirmed booking!"
        ),
    )
    return entry


def find_available_rooms_for_dates(
    room_type: RoomType,
    check_in: _dt.date,
    check_out: _dt.date,
) -> list:
    """Find rooms of a given type available for the specified date range."""
    occupied = set(
        Booking.objects.filter(
            room__room_type=room_type,
            status__in=(BookingStatus.CONFIRMED, BookingStatus.CHECKED_IN),
        ).exclude(
            models.Q(check_out__lte=check_in) | models.Q(check_in__gte=check_out)
        )
        .values_list("room_id", flat=True)
    )
    return list(
        Room.objects.filter(
            room_type=room_type,
            is_active=True,
        ).exclude(id__in=occupied)
    )


def check_and_notify_waitlist() -> list:
    """Check waiting entries against current availability and notify when rooms are free."""
    notifications_sent = []
    for entry in WaitlistEntry.objects.filter(status__in=("waiting", "notified")):
        if entry.check_out <= entry.check_in:
            continue
        available = find_available_rooms_for_dates(
            entry.room_type, entry.check_in, entry.check_out
        )
        if available:
            entry.notify()
            notifications_sent.append(entry.pk)
    return notifications_sent


def issue_digital_key(booking: Booking) -> DigitalKey:
    """Issue a digital key code for a checked-in booking."""
    if booking.status != BookingStatus.CHECKED_IN:
        raise ValueError("Can only issue a digital key for a checked-in booking.")
    if hasattr(booking, "digital_key"):
        raise ValueError("A digital key already exists for this booking.")
    return DigitalKey.objects.create(booking=booking)


def invalidate_digital_key(key: DigitalKey) -> DigitalKey:
    """Invalidate a digital key (guest returned card or booking cancelled)."""
    if key.status != DigitalKey.Status.ACTIVE:
        return key
    key.invalidate()
    return key


def get_or_create_digital_key(booking: Booking) -> DigitalKey:
    """Return existing key or create a new one for the booking."""
    if hasattr(booking, "digital_key"):
        dk = booking.digital_key
        if dk.status == DigitalKey.Status.INACTIVE:
            return issue_digital_key(booking)
        return dk
    return issue_digital_key(booking)


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
    "validate_promo_code",
    "apply_promo_code",
    "modify_booking_dates",
    "estimate_arrival_update",
    "estimate_cancellation_refund",
    "create_group_booking",
    "get_group_bookings",
    "add_waitlist_entry",
    "cancel_waitlist_entry",
    "mark_waitlist_as_booked",
    "find_available_rooms_for_dates",
    "check_and_notify_waitlist",
    "issue_digital_key",
    "invalidate_digital_key",
    "get_or_create_digital_key",
]