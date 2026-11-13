"""URL routes for the booking engine API."""

from django.urls import path

from .views import (
    AvailabilityView,
    BookingCancelView,
    BookingCreateView,
    BookingListView,
    BookingRetrieveUpdateView,
    ClientListCreateView,
    PricingView,
    RoomListView,
    RoomTypeListView,
)

app_name = "bookings"

urlpatterns = [
    path("room-types/", RoomTypeListView.as_view(), name="room-types"),
    path("rooms/", RoomListView.as_view(), name="rooms"),
    path("clients/", ClientListCreateView.as_view(), name="clients"),
    path("bookings/", BookingListView.as_view(), name="booking-list"),
    path("bookings/<int:pk>/", BookingRetrieveUpdateView.as_view(), name="booking-detail"),
    path("bookings/create/", BookingCreateView.as_view(), name="booking-create"),
    path("bookings/<int:pk>/cancel/", BookingCancelView.as_view(), name="booking-cancel"),
    path("availability/", AvailabilityView.as_view(), name="availability"),
    path("pricing/", PricingView.as_view(), name="pricing"),
]