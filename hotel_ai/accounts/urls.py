"""URL configuration for the guest account portal.

All authentication (standard login, registration, logout, password
change, and the Google SSO flow) is served by django-allauth at the
project root — allauth's own templates call ``reverse("account_login")``
without a namespace, so allauth must be included at the root URLconf
rather than nested under an ``accounts/`` namespace.

This module only defines the hotel-specific guest portal views.
"""
from django.urls import path

from .views import (
    BookingLinkView,
    PasswordChangeView,
    ProfileUpdateView,
    ProfileView,
)

app_name = "accounts"

urlpatterns = [
    path("profile/", ProfileView.as_view(), name="profile"),
    path("profile/update/", ProfileUpdateView.as_view(), name="profile-update"),
    path(
        "password/change/",
        PasswordChangeView.as_view(),
        name="password-change",
    ),
    path(
        "bookings/<int:booking_id>/link/",
        BookingLinkView.as_view(),
        name="booking-link",
    ),
]