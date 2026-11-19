"""URL routes for the booking engine API."""

from django.urls import path

from .views import (
    AvailabilityStreamView,
    AvailabilityView,
    BookingActivityView,
    BookingCancelView,
    BookingCheckoutView,
    BookingCheckInView,
    BookingCreateView,
    BookingListView,
    BookingLookupView,
    BookingNoShowView,
    BookingRetrieveUpdateView,
    ClientListCreateView,
    PricingView,
    RoomListView,
    RoomTypeListView,
    booking_admin,
    booking_export,
    booking_calendar_feed,
    RoomBulkUpdateView,
)

app_name = "bookings"

urlpatterns = [
    path("room-types/", RoomTypeListView.as_view(), name="room-types"),
    path("rooms/", RoomListView.as_view(), name="rooms"),
    path("rooms/bulk-update/", RoomBulkUpdateView.as_view(), name="rooms-bulk-update"),
    path("clients/", ClientListCreateView.as_view(), name="clients"),
    path("bookings/", BookingListView.as_view(), name="booking-list"),
    path("bookings/<int:pk>/", BookingRetrieveUpdateView.as_view(), name="booking-detail"),
    path("booking/<int:pk>/", BookingLookupView.as_view(), name="booking-lookup"),
    path("bookings/create/", BookingCreateView.as_view(), name="booking-create"),
    path("bookings/<int:pk>/cancel/", BookingCancelView.as_view(), name="booking-cancel"),
    path("bookings/<int:pk>/check-in/", BookingCheckInView.as_view(), name="booking-check-in"),
    path("bookings/<int:pk>/check-out/", BookingCheckoutView.as_view(), name="booking-check-out"),
    path("bookings/<int:pk>/no-show/", BookingNoShowView.as_view(), name="booking-no-show"),
    path("availability/", AvailabilityView.as_view(), name="availability"),
    path("pricing/", PricingView.as_view(), name="pricing"),
    path("admin/", booking_admin, name="booking-admin"),
    path("admin/export/", booking_export, name="booking-export"),
    path("feed/", booking_calendar_feed, name="calendar-feed"),
    path("bookings/<int:pk>/activity/", BookingActivityView.as_view(), name="booking-activity"),
    path("availability/stream/", AvailabilityStreamView.as_view(), name="availability-stream"),
]