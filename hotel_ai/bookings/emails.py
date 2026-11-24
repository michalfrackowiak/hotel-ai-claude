"""Email communication helpers for the booking engine."""

from __future__ import annotations

import logging
from typing import Optional

from django.conf import settings
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

_TPL_PREFIX = "bookings/email/"


def _ctx(booking, **extra) -> dict:
    """Return context shared by all booking email templates."""
    return {
        "client": booking.client,
        "room": booking.room,
        "check_in": booking.check_in,
        "check_out": booking.check_out,
        "nights": booking.nights,
        "guests": booking.guests,
        "total_price": booking.total_price,
        "currency": booking.currency,
        "booking_link": extra.get("booking_link", ""),
        "notes": extra.get("notes", ""),
        "language": booking.client.language or "en",
    }


def _send(to_email: str, subject: str, body: str, html_message: Optional[str]) -> bool:
    """Dispatch one email; returns True on success."""
    from django.core.mail import send_mail
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@grandhotel.ai")
    try:
        send_mail(subject=subject, message=body, from_email=from_email,
                  recipient_list=[to_email], html_message=html_message)
        logger.info("Email sent to %s: %s", to_email, subject)
        return True
    except Exception as exc:  # pragma: no cover - network errors
        logger.warning("Failed to send email to %s: %s", to_email, exc)
        return False


def send_booking_confirmation(booking, *, booking_link: str = "") -> bool:
    """Send a booking confirmation email to the client."""
    ctx = _ctx(booking, booking_link=booking_link)
    subject = render_to_string(_TPL_PREFIX + "confirmation_subject.txt", ctx).strip()
    html = render_to_string(_TPL_PREFIX + "confirmation.html", ctx)
    plain = render_to_string(_TPL_PREFIX + "confirmation_plain.txt", ctx)
    return _send(booking.client.email, subject, plain, html)


def send_booking_reminder(booking, *, booking_link: str = "",
                          estimated_arrival: Optional[str] = None) -> bool:
    """Send a reminder email ~24 h before check-in."""
    ctx = _ctx(booking, booking_link=booking_link)
    if estimated_arrival:
        ctx["estimated_arrival"] = estimated_arrival
    subject = render_to_string(_TPL_PREFIX + "reminder_subject.txt", ctx).strip()
    html = render_to_string(_TPL_PREFIX + "reminder.html", ctx)
    plain = subject + "\n\nCheck-in date: " + booking.check_in.strftime("%A, %B %d, %Y")
    return _send(booking.client.email, subject, plain, html)


def send_checkin_instructions(booking, *, booking_link: str = "",
                               wifi_ssid: str = "Hotel_Guest",
                               wifi_password: str = "") -> bool:
    """Send check-in instructions after a guest checks in."""
    ctx = _ctx(booking, booking_link=booking_link,
               wifi_ssid=wifi_ssid, wifi_password=wifi_password)
    subject = render_to_string(_TPL_PREFIX + "checkin_subject.txt", ctx).strip()
    html = render_to_string(_TPL_PREFIX + "checkin_instructions.html", ctx)
    plain = render_to_string(_TPL_PREFIX + "checkin_plain.txt", ctx)
    return _send(booking.client.email, subject, plain, html)


def send_post_stay_survey(booking, *, booking_link: str = "",
                           survey_link: str = "") -> bool:
    """Send a post-stay satisfaction survey to the client."""
    ctx = _ctx(booking, booking_link=booking_link)
    subject = render_to_string(_TPL_PREFIX + "survey_subject.txt", ctx).strip()
    survey_ctx = {**ctx, "survey_link": survey_link}
    html = render_to_string(_TPL_PREFIX + "post_stay_survey.html", survey_ctx)
    plain = render_to_string(_TPL_PREFIX + "survey_plain.txt", ctx)
    return _send(booking.client.email, subject, plain, html)
