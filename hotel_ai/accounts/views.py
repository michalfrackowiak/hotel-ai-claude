"""Views for the guest account portal.

Authentication itself is delegated to django-allauth; these views handle
the guest-specific functionality: viewing/updating a profile and linking
existing bookings to the logged-in account.
"""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import UpdateView

from hotel_ai.bookings.models import Booking

from .models import UserProfile


@method_decorator(login_required, name="dispatch")
class ProfileView(View):
    """Show the logged-in guest's profile and their bookings."""

    template_name = "accounts/profile.html"

    def get(self, request, *args, **kwargs):
        profile = request.user.profile
        bookings = (
            Booking.objects.filter(owner=request.user)
            .select_related("room", "room__room_type", "client")
            .order_by("-check_in")
        )
        return render(
            request,
            self.template_name,
            {"profile": profile, "bookings": bookings},
        )


@method_decorator(login_required, name="dispatch")
class ProfileUpdateView(UpdateView):
    """Update the logged-in guest's phone and language preference."""

    model = UserProfile
    fields = ["phone", "language"]
    template_name = "accounts/profile_update.html"
    success_url = reverse_lazy("accounts:profile")

    def get_object(self, queryset=None):
        return self.request.user.profile

    def form_valid(self, form):
        messages.success(self.request, "Profile updated.")
        return super().form_valid(form)


@method_decorator(login_required, name="dispatch")
class PasswordChangeView(View):
    """Standard password-change form backed by Django's auth views."""

    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:profile")

    def _dispatch(self, request, *args, **kwargs):
        from django.contrib.auth.views import PasswordChangeView as DjangoPwChange

        view = DjangoPwChange.as_view(
            template_name=self.template_name,
            success_url=self.success_url,
        )
        return view(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        return self._dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        return self._dispatch(request, *args, **kwargs)


@method_decorator(login_required, name="dispatch")
class BookingLinkView(View):
    """Attach an existing booking to the logged-in account.

    The booking must already exist (created via chat/voice/API). This lets
    a guest claim a booking they made without an account and see it in
    their portal.
    """

    template_name = "accounts/profile.html"

    def post(self, request, booking_id):
        booking = get_object_or_404(
            Booking.objects.select_related("client").filter(pk=booking_id)
        )
        if booking.owner is not None and booking.owner != request.user:
            messages.error(request, "That booking is already linked to another account.")
            return redirect("accounts:profile")
        booking.owner = request.user
        booking.save(update_fields=["owner"])
        request.user.profile.link_client(booking.client)
        messages.success(request, "Booking linked to your account.")
        return redirect("accounts:profile")