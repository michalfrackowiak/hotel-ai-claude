"""Google Calendar integration model.

One calendar per room.  Each confirmed booking creates a calendar event so
housekeeping and front-desk staff see the reservation in their tool of choice.
"""

from __future__ import annotations

from django.db import models

from hotel_ai.bookings.models import Booking, Room


class GoogleCalendarConnection(models.Model):
    """OAuth connection to a Google account used for room calendars."""

    google_user_email = models.EmailField(blank=True, default="")
    access_token = models.TextField(blank=True, default="", help_text="Encrypted at rest in production.")
    refresh_token = models.TextField(blank=True, default="")
    token_expiry = models.DateTimeField(null=True, blank=True)
    scopes = models.JSONField(blank=True, default=list)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["google_user_email"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.google_user_email or "Google connection"


class RoomCalendar(models.Model):
    """Maps a physical room to a Google Calendar id."""

    room = models.OneToOneField(
        Room, on_delete=models.CASCADE, related_name="calendar"
    )
    connection = models.ForeignKey(
        GoogleCalendarConnection,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="calendars",
    )
    calendar_id = models.CharField(max_length=256, blank=True, default="")
    calendar_name = models.CharField(max_length=128, blank=True, default="")
    timezone = models.CharField(max_length=64, default="Europe/Warsaw")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["room__room_number"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.room.room_number} -> {self.calendar_id or '(local)'}"


class CalendarSync(models.Model):
    """Audit trail of calendar sync operations per booking."""

    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("synced", "Synced"),
        ("failed", "Failed"),
        ("skipped", "Skipped"),
    )

    booking = models.ForeignKey(
        Booking, on_delete=models.CASCADE, related_name="calendar_syncs"
    )
    room_calendar = models.ForeignKey(
        RoomCalendar, on_delete=models.CASCADE, related_name="syncs"
    )
    google_event_id = models.CharField(max_length=256, blank=True, default="")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending")
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("booking", "room_calendar")]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.booking_id} -> {self.status}"