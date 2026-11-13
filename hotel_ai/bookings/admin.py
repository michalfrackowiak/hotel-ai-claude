"""Admin registrations for the bookings domain."""

from django.contrib import admin

from .models import (
    Booking,
    BookingStatus,
    Client,
    PricingRule,
    Room,
    RoomType,
)


@admin.register(RoomType)
class RoomTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "base_price_per_night", "capacity")
    list_filter = ("capacity",)
    search_fields = ("name",)


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("room_number", "room_type", "floor", "is_active")
    list_filter = ("room_type", "floor", "is_active")
    search_fields = ("room_number",)


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("full_name", "email", "phone", "language")
    list_filter = ("language",)
    search_fields = ("first_name", "last_name", "email", "phone")


@admin.register(PricingRule)
class PricingRuleAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "room_type",
        "starts_at",
        "ends_at",
        "price_per_night",
        "min_nights",
        "priority",
    )
    list_filter = ("room_type",)
    search_fields = ("name", "room_type__name")
    date_hierarchy = "starts_at"
    ordering = ("priority", "starts_at")


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = (
        "room",
        "client",
        "check_in",
        "check_out",
        "guests",
        "status",
        "total_price",
        "currency",
        "source",
    )
    list_filter = ("status", "source", "currency")
    date_hierarchy = "check_in"
    search_fields = ("client__first_name", "client__last_name", "room__room_number")
    autocomplete_fields = ("room", "client", "pricing_rule")
    actions = ("mark_confirmed", "mark_cancelled")

    @admin.action(description="Mark selected bookings confirmed")
    def mark_confirmed(self, request, queryset):
        queryset.update(status=BookingStatus.CONFIRMED)

    @admin.action(description="Mark selected bookings cancelled")
    def mark_cancelled(self, request, queryset):
        queryset.update(status=BookingStatus.CANCELLED)