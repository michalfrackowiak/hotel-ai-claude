"""Core domain models for Hotel-AI-Claude.

The data model deliberately separates *catalog* data (room types, rooms,
pricing rules) from *transactions* (clients, bookings).  A booking is always
attached to a concrete :class:`Room` and a :class:`Client`, and is governed by
a :class:`PricingRule` that was active at the moment of reservation.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal

import secrets as _secrets

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

    @property
    def loyalty_balance(self) -> int:
        earned = self.loyalty_points.filter(action="earned").aggregate(total=models.Sum("points"))["total"] or 0
        redeemed = abs(self.loyalty_points.filter(action="redeemed").aggregate(total=models.Sum("points"))["total"] or 0)
        return earned - redeemed

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


class RatePlan(models.Model):
    """A rate plan / package that modifies booking pricing and policies.

    Plans can feature: non-refundable terms, breakfast inclusion,
    corporate discount %, or "stay X pay Y" specials.
    """

    class PlanType(models.TextChoices):
        STANDARD = "standard", "Standard"
        NON_REFUNDABLE = "non_refundable", "Non-refundable"
        BREAKFAST_INCLUDED = "breakfast_included", "Breakfast Included"
        CORPORATE_DISCOUNT = "corporate_discount", "Corporate Discount"
        STAY_X_PAY_Y = "stay_x_pay_y", "Stay X Pay Y"

    name = models.CharField(max_length=64, unique=True)
    plan_type = models.CharField(max_length=32, choices=PlanType.choices)
    description = models.TextField(blank=True, default="", help_text="Human-readable label for guests.")
    # Non-refundable: if true, the booking inherits the cancellation policy's
    # non_refundable_days (or a plan-specific override).
    is_non_refundable = models.BooleanField(
        default=False,
        help_text="Cancel within non_refundable_days = no refund.",
    )
    # Breakfast: if true, breakfast is included in the stay.
    includes_breakfast = models.BooleanField(
        default=False,
        help_text="Breakfast included for all guests.",
    )
    # Corporate discount: percentage off the total (0-100).
    corporate_discount_pct = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Percentage discount (e.g., 15 for 15% off).",
    )
    # Stay X Pay Y: number of paid nights. Null = standard (all nights paid).
    stay_x_pay_nights = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Number of nights the guest pays for (remaining are free).",
    )

    class Meta:
        verbose_name = "rate plan"
        verbose_name_plural = "rate plans"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


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
    rate_plan = models.ForeignKey(
        RatePlan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bookings",
        help_text="Rate plan / package active for this booking.",
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
    estimated_arrival = models.TimeField(
        null=True,
        blank=True,
        help_text="Guest's estimated arrival time.",
    )
    special_requests = models.TextField(
        blank=True,
        default="",
        help_text="Special guest requests (e.g., high floor, extra pillow).",
    )
    id_document_filename = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Stored filename of uploaded ID document.",
    )
    promo_codes = models.ManyToManyField(
        "PromoCode",
        blank=True,
        related_name="bookings",
        help_text="Promo codes applied to this booking.",
    )
    group_booking = models.ForeignKey(
        "GroupBooking",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bookings",
        help_text="Parent group booking if part of one.",
    )
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

    def send_notification(self, notif_type: Notification.Type, message: str) -> None:
        """Create an in-app notification for this booking's client."""
        Notification.objects.create(
            type=notif_type,
            recipient=self.client,
            booking=self,
            message=message,
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


class Notification(models.Model):
    """In-app notification for guests about their booking."""

    class Type(models.TextChoices):
        CONFIRMED = "confirmed", "Booking Confirmed"
        REMINDER = "reminder", "Check-in Reminder"
        CHECKED_IN = "checked_in", "You Have Checked In"
        CANCELLED = "cancelled", "Booking Cancelled"
        MODIFIED = "modified", "Booking Modified"

    type = models.CharField(max_length=32, choices=Type.choices)
    recipient = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    booking = models.ForeignKey(
        Booking,
        on_delete=models.CASCADE,
        related_name="notifications",
        null=True,
        blank=True,
    )
    message = models.TextField()
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.recipient} ← {self.get_type_display()}"

    @property
    def unread(self) -> bool:
        return self.read_at is None


class LoyaltyPoint(models.Model):
    """A single loyalty-point transaction for a client."""

    class Action(models.TextChoices):
        EARNED = "earned", "Earned"
        REDEEMED = "redeemed", "Redeemed"

    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="loyalty_points",
    )
    booking = models.ForeignKey(
        Booking,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="loyalty_points",
    )
    points = models.IntegerField(help_text="Positive = earned, negative = redeemed.")
    action = models.CharField(max_length=16, choices=Action.choices)
    description = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.client} {'+' if self.points > 0 else ''}{self.points}"

    @property
    def absolute_points(self) -> int:
        return abs(self.points)


class GroupBooking(models.Model):
    """A group booking linking multiple rooms under one reservation for a client."""

    client = models.ForeignKey(
        Client, on_delete=models.CASCADE, related_name="group_bookings"
    )
    name = models.CharField(max_length=128, help_text="Human-readable group name.")
    total_rooms = models.PositiveIntegerField(default=0, help_text="Number of rooms in the group.")
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    currency = models.CharField(max_length=3, default="PLN")
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Group #{self.id}: {self.name} ({self.total_rooms} rooms)"

    def update_totals(self):
        """Recalculate total_rooms and total_price from associated bookings."""
        self.total_rooms = self.bookings.count()
        self.total_price = sum(b.total_price for b in self.bookings.all())
        self.save(update_fields=["total_rooms", "total_price"])


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


class WaitlistEntry(models.Model):
    """A guest's request for a room type they couldn't book."""

    class Status(models.TextChoices):
        WAITING = "waiting", "Waiting"
        NOTIFIED = "notified", "Notified"
        BOOKED = "booked", "Booked via waitlist"
        CANCELLED = "cancelled", "Cancelled"

    client = models.ForeignKey(
        Client, on_delete=models.CASCADE, related_name="waitlist_entries"
    )
    room_type = models.ForeignKey(
        RoomType, on_delete=models.CASCADE, related_name="waitlist_entries"
    )
    check_in = models.DateField()
    check_out = models.DateField()
    guests = models.PositiveIntegerField(default=1)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.WAITING
    )
    notes = models.TextField(blank=True, default="")
    notified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "waitlist entry"
        verbose_name_plural = "waitlist entries"

    def __str__(self) -> str:
        return f"{self.client} ← {self.room_type.name} [{self.get_status_display()}]"

    def clean(self) -> None:
        if self.check_out <= self.check_in:
            raise ValidationError("check_out must be after check_in.")

    def notify(self) -> None:
        """Mark as notified and create an in-app notification for the client."""
        self.status = WaitlistEntry.Status.NOTIFIED
        self.notified_at = timezone.now()
        self.save(update_fields=["status", "notified_at", "updated_at"])
        Notification.objects.create(
            type=Notification.Type.REMINDER,
            recipient=self.client,
            message=(
                f"A {self.room_type.name} is now available for "
                f"{self.check_in:%Y-%m-%d} → {self.check_out:%Y-%m-%d}. "
                f"Please contact the front desk to complete your booking."
            ),
        )


class DigitalKey(models.Model):
    """A mobile-room key code issued to a checked-in guest."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INVALIDATED = "invalidated", "Invalidated"
        EXPIRED = "expired", "Expired"

    booking = models.OneToOneField(
        Booking, on_delete=models.CASCADE, related_name="digital_key"
    )
    code = models.CharField(max_length=16, unique=True, editable=False)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )
    created_at = models.DateTimeField(auto_now_add=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "digital key"
        verbose_name_plural = "digital keys"

    def __str__(self) -> str:
        return f"Key for {self.booking.room.room_number} ({self.code})"

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = _secrets.token_urlsafe(8).upper()[:8]
        super().save(*args, **kwargs)

    def invalidate(self) -> None:
        if self.status == DigitalKey.Status.INVALIDATED:
            return
        self.status = DigitalKey.Status.INVALIDATED
        self.invalidated_at = timezone.now()
        self.save(update_fields=["status", "invalidated_at"])