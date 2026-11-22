"""Views for the guest account portal.

Authentication itself is delegated to django-allauth; these views handle
the guest-specific functionality: viewing/updating a profile and linking
existing bookings to the logged-in account.
"""
from __future__ import annotations

from pathlib import Path

from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
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
        lang = self._lang(request)
        return render(
            request,
            self.template_name,
            {"profile": profile, "bookings": bookings, "lang": lang, "section": "profile"},
        )

    @staticmethod
    def _lang(request) -> str:
        """Resolve the portal language from query string, then profile."""
        return request.GET.get("lang") or request.user.profile.language or "en"


@method_decorator(login_required, name="dispatch")
class ProfileUpdateView(UpdateView):
    """Update the logged-in guest's phone and language preference."""

    model = UserProfile
    fields = ["phone", "language"]
    template_name = "accounts/profile_update.html"
    success_url = reverse_lazy("accounts:profile")

    def get_object(self, queryset=None):
        return self.request.user.profile

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["lang"] = self._lang()
        ctx["section"] = "settings"
        ctx["profile"] = self.get_object()
        return ctx

    def form_valid(self, form):
        messages.success(self.request, "Profile updated.")
        return super().form_valid(form)

    def _lang(self) -> str:
        return self.request.GET.get("lang") or self.request.user.profile.language or "en"


@method_decorator(login_required, name="dispatch")
class PasswordChangeView(View):
    """Standard password-change form with language/section tracking."""

    template_name = "accounts/password_change.html"

    def dispatch(self, request, *args, **kwargs):
        self._patch_lang_and_section(request)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        from allauth.account.forms import ChangePasswordForm
        form = ChangePasswordForm(user=request.user)
        return render(request, self.template_name, {
            "form": form,
            "lang": getattr(request, "lang", "en"),
            "section": getattr(request, "section", "password"),
        })

    def post(self, request, *args, **kwargs):
        from allauth.account.forms import ChangePasswordForm
        form = ChangePasswordForm(user=request.user, data=request.POST)
        if form.is_valid():
            form.save()
            update_session_auth_hash(request, request.user)
            login(request, request.user, backend="django.contrib.auth.backends.ModelBackend")
            lang = getattr(request, "lang", "en")
            return redirect(f"{reverse('accounts:profile')}?lang={lang}")
        return render(request, self.template_name, {
            "form": form,
            "lang": getattr(request, "lang", "en"),
            "section": getattr(request, "section", "password"),
        })

    @staticmethod
    def _patch_lang_and_section(request) -> None:
        """Attach lang/section so the custom template renders correctly."""
        lang = (
            request.GET.get("lang")
            or request.POST.get("lang")
            or getattr(getattr(request.user, "profile", None), "language", "en")
            or "en"
        )
        request.session["_portal_lang"] = lang
        request.session["_portal_section"] = "password"
        request.lang = lang
        request.section = "password"


@method_decorator(user_passes_test(lambda u: u.is_staff), name="dispatch")
class DebugLogsView(View):
    """Staff-only debug view of recent application logs.

    Reads the project log file (``logs/hotel-ai.log`` by default) and shows
    the most recent lines so staff can inspect errors without shell access.
    """

    template_name = "accounts/debug_logs.html"
    LOG_PATH = Path(__file__).resolve().parent.parent.parent / "logs" / "hotel-ai.log"
    TAIL_LINES = 200

    def get(self, request, *args, **kwargs):
        lines = self._tail_log()
        return render(
            request,
            self.template_name,
            {
                "log_lines": lines,
                "log_path": str(self.LOG_PATH),
                "section": "debug-logs",
                "lang": request.GET.get("lang", "en"),
            },
        )

    def _tail_log(self):
        try:
            with self.LOG_PATH.open("r", encoding="utf-8", errors="replace") as fh:
                return fh.readlines()[-self.TAIL_LINES:]
        except FileNotFoundError:
            return ["Log file not found: " + str(self.LOG_PATH)]
        except OSError as exc:
            return [f"Could not read log file: {exc}"]


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
            return redirect(self._profile_url(request))
        booking.owner = request.user
        booking.save(update_fields=["owner"])
        request.user.profile.link_client(booking.client)
        messages.success(request, "Booking linked to your account.")
        return redirect(self._profile_url(request))

    @staticmethod
    def _profile_url(request) -> str:
        """Preserve the portal language across the redirect."""
        lang = request.GET.get("lang") or request.user.profile.language or "en"
        return f"/accounts/profile/?lang={lang}"