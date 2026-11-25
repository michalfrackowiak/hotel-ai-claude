"""SMS/WhatsApp notifications via Twilio for the booking engine."""

from __future__ import annotations

import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def _get_twilio_client():
    """Lazy-import and return a Twilio REST client."""
    from twilio.rest import Client as TwilioClient
    account_sid = getattr(settings, "TWILIO_ACCOUNT_SID", "")
    auth_token = getattr(settings, "TWILIO_AUTH_TOKEN", "")
    if not account_sid or not auth_token:
        logger.warning("Twilio credentials not configured; skipping SMS")
        return None
    try:
        return TwilioClient(account_sid, auth_token)
    except Exception as exc:  # pragma: no cover - init-time failure
        logger.warning("Failed to initialize Twilio client: %s", exc)
        return None


def _send_message(to_phone: str, body: str, channel: str = "sms") -> bool:
    """Send a message via SMS or WhatsApp. Returns True on success."""
    client = _get_twilio_client()
    if client is None:
        return False

    if channel == "whatsapp":
        if not to_phone.startswith("+"):
            logger.warning("Invalid WhatsApp recipient: %s", to_phone)
            return False
        from_address = f"whatsapp:{getattr(settings, 'TWILIO_WHATSAPP_FROM', '')}"
        if not from_address or from_address == "whatsapp:":
            logger.warning("TWILIO_WHATSAPP_FROM not configured; skipping WhatsApp")
            return False
    else:
        from_address = f"+{getattr(settings, 'TWILIO_FROM_NUMBER', '0000000000')}"

    try:
        message = client.messages.create(
            body=body,
            from_=from_address,
            to=f"{channel}:{to_phone}",
        )
        logger.info("Message sent to %s via %s (sid=%s)", to_phone, channel, message.sid)
        return True
    except Exception as exc:  # pragma: no cover - network errors
        logger.warning("Failed to send %s to %s: %s", channel, to_phone, exc)
        return False


def format_booking_message(booking, subject: str = "") -> str:
    """Build a short text message suitable for SMS/WhatsApp about a booking."""
    lines = []
    if subject:
        lines.append(subject)
    lines.append(f"Booking #{booking.id}")
    lines.append(f"Guest: {booking.client.full_name}")
    lines.append(f"Room: {booking.room.room_number} ({booking.room.room_type.name})")
    lines.append(f"Check-in: {booking.check_in.strftime('%Y-%m-%d')}")
    lines.append(f"Check-out: {booking.check_out.strftime('%Y-%m-%d')}")
    lines.append(f"Nights: {booking.nights}")
    lines.append(f"Total: {booking.total_price:.2f} {booking.currency}")
    if booking.rate_plan:
        lines.append(f"Rate plan: {booking.rate_plan.name}")
    return "\n".join(lines)


def send_sms_confirmation(booking) -> bool:
    """Send booking confirmation via SMS."""
    return _send_message(booking.client.phone, format_booking_message(booking, "Your booking is confirmed!"))


def send_sms_reminder(booking) -> bool:
    """Send check-in reminder via SMS."""
    return _send_message(
        booking.client.phone,
        format_booking_message(booking, f"Reminder: Check-in on {booking.check_in.strftime('%A, %B %d')}"),
    )


def send_sms_checkin(booking) -> bool:
    """Send check-in confirmation via SMS."""
    return _send_message(booking.client.phone, format_booking_message(booking, "Welcome! You have checked in."))


def send_whatsapp_confirmation(booking) -> bool:
    """Send booking confirmation via WhatsApp."""
    return _send_message(booking.client.phone, format_booking_message(booking, "Your booking is confirmed!"), channel="whatsapp")


def send_whatsapp_reminder(booking) -> bool:
    """Send check-in reminder via WhatsApp."""
    return _send_message(
        booking.client.phone,
        format_booking_message(booking, f"Reminder: Check-in on {booking.check_in.strftime('%A, %B %d')}"),
        channel="whatsapp",
    )


__all__ = [
    "format_booking_message",
    "send_sms_confirmation",
    "send_sms_reminder",
    "send_sms_checkin",
    "send_whatsapp_confirmation",
    "send_whatsapp_reminder",
]
