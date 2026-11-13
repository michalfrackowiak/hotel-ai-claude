"""Admin for calendar integration."""

from django.contrib import admin

from .models import CalendarSync, GoogleCalendarConnection, RoomCalendar


@admin.register(GoogleCalendarConnection)
class GoogleCalendarConnectionAdmin(admin.ModelAdmin):
    list_display = ("google_user_email", "is_active", "updated_at")
    search_fields = ("google_user_email",)


@admin.register(RoomCalendar)
class RoomCalendarAdmin(admin.ModelAdmin):
    list_display = ("room", "calendar_id", "calendar_name", "timezone", "is_active")
    list_filter = ("is_active",)
    search_fields = ("room__room_number", "calendar_id")


@admin.register(CalendarSync)
class CalendarSyncAdmin(admin.ModelAdmin):
    list_display = ("booking", "room_calendar", "status", "updated_at")
    list_filter = ("status",)
    search_fields = ("booking__id", "google_event_id")