"""DRF serializers for the bookings domain."""

from rest_framework import serializers

from .models import Booking, BookingStatus, Client, PricingRule, Room, RoomType
from .services import (
    calculate_total,
    effective_price,
    find_available_rooms,
    parse_date,
    resolve_client,
    resolve_pricing_rule,
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
            "check_in",
            "check_out",
            "guests",
            "status",
            "total_price",
            "currency",
            "source",
            "notes",
            "nights",
            "created_at",
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
    room_number = serializers.CharField()
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

    def validate(self, attrs):
        if attrs["check_out"] <= attrs["check_in"]:
            raise serializers.ValidationError("check_out must be after check_in.")
        return attrs


class BookingResponseSerializer(serializers.Serializer):
    booking = BookingSerializer()
    message = serializers.CharField()


__all__ = [
    "RoomTypeSerializer",
    "RoomSerializer",
    "ClientSerializer",
    "PricingRuleSerializer",
    "BookingSerializer",
    "AvailabilityRequestSerializer",
    "AvailabilityResponseSerializer",
    "PricingRequestSerializer",
    "BookingRequestSerializer",
    "BookingResponseSerializer",
]