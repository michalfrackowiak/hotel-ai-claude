"""Core domain models for Hotel-AI-Claude.

The data model deliberately separates *catalog* data (room types, rooms,
pricing rules) from *transactions* (clients, bookings).  A booking is always
attached to a concrete :class:`Room` and a :class:`Client`, and is governed by
a :class:`PricingRule` that was active at the moment of reservation.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class RoomType(models.Model):
    """A category of room (single, double, suite, ...)."""

    name = models.CharField(max_length=64, unique=True)
    description = models.TextField(blank=True, default="")
    base_price_per_night = models.DecimalField(
        max_digits=8, decimal_places=2, help_text="Default nightly price in the base currency."
    )
    capacity = models.PositiveIntegerField(default=1, help_text="Max guests.")
    amenities = models.JSONField(blank=True, default=list, help_text="Free-form list of amenities.")

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name


class Room(models.Model):
    """A physical, bookable room instance (one per room type per floor)."""

    room_number = models.CharField(max_length=16, unique=True)
    room_type = models.ForeignKey(
        RoomType, on_delete=models.CASCADE, related_name="rooms"
    )
    floor = models.PositiveSmallIntegerField(default=1)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["room_number"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.room_number} ({self.room_type.name})"


class Client(models.Model):
    """A hotel guest.

    Identity is intentionally lightweight for the alpha: name, contact and a
    language preference that drives the agent's output.
    """

    LANGUAGE_CHOICES = (("en", "English"), ("pl", "Polish"))

    first_name = models.CharField(max_length=64)
    last_name = models.CharField(max_length=64, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=32, blank=True, default="")
    language = models.CharField(max_length=2, choices=LANGUAGE_CHOICES, default="en")
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["last_name", "first_name"]
        unique_together = [("first_name", "last_name", "email")]

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.full_name or self.email


class PricingRule(models.Model):
    """A pricing rule that modifies the base price for a room type.

    Rules are evaluated in priority order (lower number = higher priority).
    The first rule whose ``starts_at``/``ends_at`` window contains the check-in
    date wins.
    """

    room_type = models.ForeignKey(
        RoomType, on_delete=models.CASCADE, related_name="pricing_rules"
    )
    name = models.CharField(max_length=64)
    starts_at = models.DateField()
    ends_at = models.DateField()
    price_per_night = models.DecimalField(
        max_digits=8, decimal_places=2, help_text="Absolute nightly price."
    )
    min_nights = models.PositiveSmallIntegerField(default=1)
    priority = models.PositiveIntegerField(default=100, help_text="Lower = higher priority.")

    class Meta:
        ordering = ["priority", "starts_at"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.name} ({self.room_type.name})"

    def clean(self) -> None:
        if self.ends_at < self.starts_at:
            raise ValidationError("ends_at must be >= starts_at")


class BookingStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    CONFIRMED = "confirmed", "Confirmed"
    CANCELLED = "cancelled", "Cancelled"
    NO_SHOW = "no_show", "No-show"
    CHECKED_IN = "checked_in", "Checked-in"
    CHECKED_OUT = "checked_out", "Checked-out"


class Booking(models.Model):
    """A reservation against a concrete room for a client."""

    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="bookings")
    client = models.ForeignKey(
        Client, on_delete=models.CASCADE, related_name="bookings"
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bookings_owned",
        help_text="Authenticated guest who created the booking.",
    )
    pricing_rule = models.ForeignKey(
        PricingRule,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bookings",
        help_text="Rule active at the moment of booking.",
    )
    check_in = models.DateField()
    check_out = models.DateField()
    guests = models.PositiveIntegerField(default=1)
    status = models.CharField(
        max_length=16, choices=BookingStatus.choices, default=BookingStatus.PENDING
    )
    total_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    currency = models.CharField(max_length=3, default="PLN")
    source = models.CharField(
        max_length=16, default="chat", help_text="chat | voice | web | admin"
    )
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-check_in"]

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days

    def clean(self) -> None:
        if self.check_out <= self.check_in:
            raise ValidationError("check_out must be after check_in.")
        if self.nights < 1:
            raise ValidationError("Minimum stay is one night.")
        if self.guests > self.room.room_type.capacity:
            raise ValidationError(
                f"Room capacity is {self.room.room_type.capacity}; "
                f"{self.guests} guests requested."
            )

    def __str__(self) -> str:  # pragma: no cover - trivial
        return (
            f"{self.room.room_number} {self.client} "
            f"{self.check_in:%Y-%m-%d} -> {self.check_out:%Y-%m-%d} [{self.status}]"
        )


class BookingLog(models.Model):
    """Audit log for every status transition on a booking."""

    action = models.CharField(
        max_length=32,
        help_text="e.g. created, confirmed, checked_in, checked_out, cancelled",
    )
    booking = models.ForeignKey(
        Booking, on_delete=models.CASCADE, related_name="logs"
    )
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"#{self.booking_id} → {self.action}"


class CancellationPolicy(models.Model):
    """Cancellation rules governing how much of a booking's total is refunded.

    The logic uses two thresholds measured in days before check-in:
    - ``free_cancellation_days`` — cancel this many days or more = full refund
    - ``non_refundable_days``   — cancel fewer than this many days = no refund
    Between these two windows, a partial refund percentage applies.
    """

    name = models.CharField(max_length=64, unique=True)
    description = models.TextField(blank=True, default="", help_text="Human-readable label for guests.")
    free_cancellation_days = models.PositiveIntegerField(
        default=0,
        help_text="Cancel N days or more before check-in = full refund.",
    )
    partial_refund_pct = models.PositiveSmallIntegerField(
        default=100,
        help_text="Refund % within the penalty window (default 100 = still full).",
    )
    non_refundable_days = models.PositiveIntegerField(
        default=0,
        help_text="Fewer than this many days = no refund.",
    )

    class Meta:
        verbose_name = "cancellation policy"
        verbose_name_plural = "cancellation policies"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class PromoCode(models.Model):
    """A discount code that can be applied to bookings."""

    DISCOUNT_TYPES = (
        ("percent", "Percentage"),
        ("fixed", "Fixed Amount"),
    )

    code = models.CharField(max_length=32, unique=True)
    discount_type = models.CharField(max_length=10, choices=DISCOUNT_TYPES)
    discount_value = models.DecimalField(
        max_digits=8, decimal_places=2, help_text="Percentage (e.g., 10 for 10%) or fixed amount in base currency"
    )
    valid_from = models.DateField()
    valid_to = models.DateField()
    max_uses = models.PositiveIntegerField(null=True, blank=True, help_text="Null = unlimited")
    current_uses = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.code} ({self.discount_type} {self.discount_value})"

    def is_valid(self, check_in: _dt.date) -> bool:
        """Check if promo code is valid for a given check-in date."""
        if not self.active:
            return False
        if self.max_uses is not None and self.current_uses >= self.max_uses:
            return False
        if check_in < self.valid_from or check_in > self.valid_to:
            return False
        return True

    def apply(self, total_price: Decimal) -> Decimal:
        """Apply discount to total price."""
        if self.discount_type == "percent":
            discount = total_price * (self.discount_value / Decimal("100"))
        else:  # fixed
            discount = self.discount_value
        return max(total_price - discount, Decimal("0.00"))