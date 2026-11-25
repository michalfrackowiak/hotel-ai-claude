"""DRF serializers for the bookings domain."""

from rest_framework import serializers

from .models import Booking, BookingLog, BookingStatus, Client, GroupBooking, LoyaltyPoint, Notification, PricingRule, Room, RoomType, PromoCode, RatePlan, WaitlistEntry
from .services import (
    calculate_total,
    effective_price,
    find_available_rooms,
    no_show_booking,
    parse_date,
    resolve_client,
    resolve_pricing_rule,
    validate_promo_code,
    apply_promo_code,
)


class RoomTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = RoomType
        fields = (
            "id",
            "name",
            "description",
            "base_price_per_night",
            "capacity",
            "amenities",
        )


class RoomSerializer(serializers.ModelSerializer):
    room_type_name = serializers.CharField(source="room_type.name", read_only=True)

    class Meta:
        model = Room
        fields = (
            "id",
            "room_number",
            "room_type",
            "room_type_name",
            "floor",
            "is_active",
        )


class ClientSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = Client
        fields = (
            "id",
            "first_name",
            "last_name",
            "full_name",
            "email",
            "phone",
            "language",
        )


class PricingRuleSerializer(serializers.ModelSerializer):
    room_type_name = serializers.CharField(source="room_type.name", read_only=True)

    class Meta:
        model = PricingRule
        fields = (
            "id",
            "name",
            "room_type",
            "room_type_name",
            "starts_at",
            "ends_at",
            "price_per_night",
            "min_nights",
            "priority",
        )


class BookingSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.full_name", read_only=True)
    room_number = serializers.CharField(source="room.room_number", read_only=True)
    room_type_name = serializers.CharField(source="room.room_type.name", read_only=True)
    nights = serializers.IntegerField(read_only=True)
    rate_plan_name = serializers.CharField(source="rate_plan.name", read_only=True, allow_null=True)

    class Meta:
        model = Booking
        fields = (
            "id",
            "room",
            "room_number",
            "room_type_name",
            "client",
            "client_name",
            "pricing_rule",
            "rate_plan",
            "rate_plan_name",
            "check_in",
            "check_out",
            "guests",
            "status",
            "total_price",
            "currency",
            "source",
            "notes",
            "estimated_arrival",
            "special_requests",
            "id_document_filename",
            "nights",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "pricing_rule",
            "total_price",
            "currency",
            "source",
            "nights",
            "created_at",
        )


class AvailabilityRequestSerializer(serializers.Serializer):
    check_in = serializers.DateField()
    check_out = serializers.DateField()
    guests = serializers.IntegerField(min_value=1, default=1)
    room_type = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if attrs["check_out"] <= attrs["check_in"]:
            raise serializers.ValidationError("check_out must be after check_in.")
        return attrs


class AvailabilityResponseSerializer(serializers.Serializer):
    room_type = serializers.CharField()
    capacity = serializers.IntegerField()
    base_price_per_night = serializers.CharField()
    effective_price_per_night = serializers.CharField()
    total_price = serializers.CharField()
    available_rooms = serializers.IntegerField()
    available = serializers.BooleanField()


class PricingRequestSerializer(serializers.Serializer):
    room_type = serializers.CharField()
    check_in = serializers.DateField()
    check_out = serializers.DateField()

    def validate(self, attrs):
        if attrs["check_out"] <= attrs["check_in"]:
            raise serializers.ValidationError("check_out must be after check_in.")
        return attrs


class BookingRequestSerializer(serializers.Serializer):
    room_type = serializers.CharField()
    room_number = serializers.CharField(required=False, allow_blank=True, default="")
    check_in = serializers.DateField()
    check_out = serializers.DateField()
    guests = serializers.IntegerField(min_value=1, default=1)
    first_name = serializers.CharField()
    last_name = serializers.CharField(required=False, allow_blank=True, default="")
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    phone = serializers.CharField(required=False, allow_blank=True, default="")
    language = serializers.ChoiceField(
        choices=Client.LANGUAGE_CHOICES, default="en", required=False
    )
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    source = serializers.CharField(required=False, allow_blank=True, default="chat")
    rate_plan = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        if attrs["check_out"] <= attrs["check_in"]:
            raise serializers.ValidationError("check_out must be after check_in.")
        return attrs


class BookingResponseSerializer(serializers.Serializer):
    booking = BookingSerializer()
    message = serializers.CharField()


class BookingLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookingLog
        fields = ("id", "booking", "action", "notes", "created_at")
        read_only_fields = ("id", "booking", "created_at")


class PromoCodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = PromoCode
        fields = (
            "id",
            "code",
            "discount_type",
            "discount_value",
            "valid_from",
            "valid_to",
            "max_uses",
            "current_uses",
            "active",
            "created_at",
        )
        read_only_fields = ("id", "current_uses", "created_at")


class PromoCodeCheckSerializer(serializers.Serializer):
    code = serializers.CharField()
    check_in = serializers.DateField()

    def validate(self, attrs):
        try:
            promo = validate_promo_code(attrs["code"], attrs["check_in"])
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))
        attrs["promo_code"] = promo
        return attrs


class PromoCodeApplySerializer(serializers.Serializer):
    booking_id = serializers.IntegerField()
    code = serializers.CharField()

    def validate(self, attrs):
        try:
            booking = Booking.objects.get(pk=attrs["booking_id"])
        except Booking.DoesNotExist:
            raise serializers.ValidationError("Booking not found")
        try:
            validate_promo_code(attrs["code"], booking.check_in)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))
        attrs["booking"] = booking
        return attrs


class BookingModifySerializer(serializers.Serializer):
    check_in = serializers.DateField()
    check_out = serializers.DateField()
    guests = serializers.IntegerField(min_value=1, required=False, default=None)

    def validate(self, attrs):
        if attrs["check_out"] <= attrs["check_in"]:
            raise serializers.ValidationError("check_out must be after check_in.")
        return attrs


class RatePlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = RatePlan
        fields = (
            "id",
            "name",
            "plan_type",
            "description",
            "is_non_refundable",
            "includes_breakfast",
            "corporate_discount_pct",
            "stay_x_pay_nights",
        )


class PreArrivalSerializer(serializers.Serializer):
    """Validate pre-arrival form submission data."""

    estimated_arrival = serializers.TimeField(required=False, allow_null=True)
    special_requests = serializers.CharField(
        required=False, allow_blank=True, default=""
    )

    def validate_estimated_arrival(self, value):
        if value is not None:
            parts = str(value).split(":")
            try:
                h, m = int(parts[0]), int(parts[1])
                if not (0 <= h <= 23 and 0 <= m <= 59):
                    raise serializers.ValidationError(
                        "Estimated arrival must be a valid time."
                    )
            except (ValueError, IndexError):
                raise serializers.ValidationError("Invalid time format.")
        return value


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ("id", "type", "message", "read_at", "created_at")
        read_only_fields = ("id", "created_at")


class LoyaltyPointSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoyaltyPoint
        fields = ("id", "booking", "points", "action", "description", "created_at")
        read_only_fields = ("id", "created_at")


class GroupBookingSerializer(serializers.ModelSerializer):
    bookings = BookingSerializer(many=True, read_only=True)

    class Meta:
        model = GroupBooking
        fields = ("id", "client", "name", "total_rooms", "total_price", "currency", "notes", "bookings", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")


class GroupBookingCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=128)
    client_id = serializers.IntegerField()
    room_ids = serializers.ListField(child=serializers.IntegerField(), min_length=1)
    check_in = serializers.DateField()
    check_out = serializers.DateField()
    guests = serializers.IntegerField(min_value=1, default=1)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    source = serializers.CharField(required=False, allow_blank=True, default="chat")

    def validate(self, attrs):
        if attrs["check_out"] <= attrs["check_in"]:
            raise serializers.ValidationError("check_out must be after check_in.")
        return attrs


class WaitlistEntrySerializer(serializers.ModelSerializer):
    room_type_name = serializers.CharField(source="room_type.name", read_only=True)
    client_name = serializers.CharField(source="client.full_name", read_only=True)

    class Meta:
        model = WaitlistEntry
        fields = (
            "id",
            "client",
            "client_name",
            "room_type",
            "room_type_name",
            "check_in",
            "check_out",
            "guests",
            "status",
            "notes",
            "notified_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "notified_at", "created_at", "updated_at")


class WaitlistEntryCreateSerializer(serializers.Serializer):
    room_type_id = serializers.IntegerField()
    check_in = serializers.DateField()
    check_out = serializers.DateField()
    guests = serializers.IntegerField(min_value=1, default=1)
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        if attrs["check_out"] <= attrs["check_in"]:
            raise serializers.ValidationError("check_out must be after check_in.")
        return attrs


__all__ = [
    "RoomTypeSerializer",
    "RoomSerializer",
    "ClientSerializer",
    "PricingRuleSerializer",
    "BookingSerializer",
    "BookingLogSerializer",
    "PromoCodeSerializer",
    "PromoCodeCheckSerializer",
    "PromoCodeApplySerializer",
    "BookingModifySerializer",
    "PreArrivalSerializer",
    "AvailabilityRequestSerializer",
    "AvailabilityResponseSerializer",
    "PricingRequestSerializer",
    "BookingRequestSerializer",
    "BookingResponseSerializer",
    "RatePlanSerializer",
    "NotificationSerializer",
    "LoyaltyPointSerializer",
    "GroupBookingSerializer",
    "GroupBookingCreateSerializer",
    "WaitlistEntrySerializer",
    "WaitlistEntryCreateSerializer",
]