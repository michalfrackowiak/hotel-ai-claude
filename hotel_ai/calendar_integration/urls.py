from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django.urls import path

from hotel_ai.bookings.models import Booking

from .models import RoomCalendar
from .services import sync_booking

app_name = "calendar_integration"


@api_view(["POST"])
@permission_classes([AllowAny])
def resync_booking(request, booking_id: int):
    """Manually re-push a booking to its room's calendar."""
    try:
        booking = Booking.objects.get(pk=booking_id)
    except Booking.DoesNotExist:
        return Response({"detail": "Booking not found"}, status=404)
    sync = sync_booking(booking)
    return Response(
        {
            "booking_id": booking.id,
            "status": sync.status,
            "google_event_id": sync.google_event_id,
            "error_message": sync.error_message,
        }
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def list_room_calendars(request):
    rows = RoomCalendar.objects.select_related("room").all()
    return Response(
        [
            {
                "room": rc.room.room_number,
                "calendar_id": rc.calendar_id,
                "calendar_name": rc.calendar_name,
                "timezone": rc.timezone,
                "is_active": rc.is_active,
            }
            for rc in rows
        ]
    )


urlpatterns = [
    path("calendars/", list_room_calendars, name="calendars"),
    path("sync/<int:booking_id>/", resync_booking, name="sync-booking"),
]