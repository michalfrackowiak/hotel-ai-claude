"""Tests for the guest account system.

Covers: registration, login, logout, profile viewing, profile updates,
booking visibility by owner, and the booking-link flow.
"""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from hotel_ai.bookings.models import Booking, Client, Room, RoomType

User = get_user_model()


def _create_google_app():
    """Create a SocialApp for the Google provider so the OAuth flow works."""
    from allauth.socialaccount.models import SocialApp

    SocialApp.objects.create(
        provider="google",
        name="Google",
        client_id="test-client-id",
        secret="test-secret",
        key="",
    )


@pytest.mark.django_db
class TestRegistration:
    """Standard username/password registration flow."""

    def test_signup_page_renders(self, client):
        resp = client.get(reverse("account_signup"))
        assert resp.status_code == 200
        assert b"Sign Up" in resp.content

    def test_signup_creates_user_and_redirects(self, client):
        resp = client.post(
            reverse("account_signup"),
            {
                "username": "newguest",
                "password1": "securepass123",
                "password2": "securepass123",
            },
            follow=True,
        )
        assert resp.status_code == 200
        assert User.objects.filter(username="newguest").exists()

    def test_signup_password_mismatch_shows_error(self, client):
        resp = client.post(
            reverse("account_signup"),
            {
                "username": "badguest",
                "password1": "pass1",
                "password2": "pass2",
            },
        )
        assert resp.status_code == 200
        assert b"Passwords didn't match" in resp.content or b"password" in resp.content.lower()


@pytest.mark.django_db
class TestLoginLogout:
    """Login and logout flows."""

    def test_login_page_renders(self, client):
        resp = client.get(reverse("account_login"))
        assert resp.status_code == 200
        assert b"Sign In" in resp.content

    def test_login_success_redirects(self, client, django_user_model):
        user = django_user_model.objects.create_user(username="testuser", password="testpass")
        resp = client.post(
            reverse("account_login"),
            {"login": "testuser", "password": "testpass"},
            follow=True,
        )
        assert resp.status_code == 200
        assert resp.wsgi_request.user == user

    def test_logout(self, client, django_user_model):
        user = django_user_model.objects.create_user(username="testuser", password="testpass")
        client.login(username="testuser", password="testpass")
        resp = client.get(reverse("account_logout"), follow=True)
        assert resp.status_code == 200
        assert not resp.wsgi_request.user.is_authenticated


@pytest.mark.django_db
class TestProfileView:
    """Profile page shows user data and their bookings."""

    def test_profile_requires_login(self, client):
        resp = client.get(reverse("accounts:profile"))
        assert resp.status_code == 302  # redirects to login
        assert "/login/" in resp.url

    def test_profile_shows_user_bookings(self, client, django_user_model):
        user = django_user_model.objects.create_user(username="hotelguest", password="pass")
        client.force_login(user)

        room_type = RoomType.objects.create(name="Single", base_price_per_night=220)
        room = Room.objects.create(room_number="101", room_type=room_type)
        client_obj = Client.objects.create(first_name="Test", last_name="Guest")

        Booking.objects.create(
            room=room,
            client=client_obj,
            owner=user,
            check_in="2026-04-01",
            check_out="2026-04-03",
            status="confirmed",
        )

        resp = client.get(reverse("accounts:profile"))
        assert resp.status_code == 200
        assert b"101" in resp.content
        assert b"Single" in resp.content
        assert b"Confirmed" in resp.content

    def test_profile_empty_when_no_bookings(self, client, django_user_model):
        user = django_user_model.objects.create_user(username="lonely", password="pass")
        client.force_login(user)

        resp = client.get(reverse("accounts:profile"))
        assert resp.status_code == 200
        assert b"You have no bookings yet" in resp.content


@pytest.mark.django_db
class TestProfileUpdate:
    """Guests can update phone and language preference."""

    def test_update_profile_requires_login(self, client):
        resp = client.get(reverse("accounts:profile-update"))
        assert resp.status_code == 302

    def test_update_profile_saves_data(self, client, django_user_model):
        user = django_user_model.objects.create_user(username="updater", password="pass")
        client.force_login(user)

        resp = client.post(
            reverse("accounts:profile-update"),
            {"phone": "+48 22 123 45 67", "language": "pl"},
            follow=True,
        )
        assert resp.status_code == 200
        user.profile.refresh_from_db()
        assert user.profile.phone == "+48 22 123 45 67"
        assert user.profile.language == "pl"
        assert b"Profile updated" in resp.content


@pytest.mark.django_db
class TestPasswordChange:
    """Password change using Django's built-in view."""

    def test_password_change_requires_login(self, client):
        resp = client.get(reverse("account_change_password"))
        assert resp.status_code == 302

    def test_password_change_success(self, client, django_user_model):
        user = django_user_model.objects.create_user(username="pwuser", password="oldpass")
        client.force_login(user)

        resp = client.post(
            reverse("account_change_password"),
            {
                "oldpassword": "oldpass",
                "password1": "newpass123",
                "password2": "newpass123",
            },
            follow=True,
        )
        assert resp.status_code == 200
        user.refresh_from_db()
        assert user.check_password("newpass123")


@pytest.mark.django_db
class TestBookingLink:
    """Linking an existing booking to the logged-in account."""

    def test_link_booking_requires_login(self, client):
        resp = client.post(reverse("accounts:booking-link", kwargs={"booking_id": 1}))
        assert resp.status_code == 302

    def test_link_booking_success(self, client, django_user_model):
        user = django_user_model.objects.create_user(username="linker", password="pass")
        client.force_login(user)

        room_type = RoomType.objects.create(name="Double", base_price_per_night=320)
        room = Room.objects.create(room_number="202", room_type=room_type)
        client_obj = Client.objects.create(
            first_name="Pre", last_name="Booked", email="pre@example.com", phone="+48 111"
        )

        booking = Booking.objects.create(
            room=room,
            client=client_obj,
            owner=None,  # unclaimed booking
            check_in="2026-05-01",
            check_out="2026-05-05",
            status="confirmed",
        )

        resp = client.post(
            reverse("accounts:booking-link", kwargs={"booking_id": booking.id}),
            follow=True,
        )
        assert resp.status_code == 200
        booking.refresh_from_db()
        assert booking.owner == user
        user.profile.refresh_from_db()
        assert user.profile.phone == "+48 111"  # synced from client
        assert b"Booking linked to your account" in resp.content

    def test_link_booking_already_owned_by_other(self, client, django_user_model):
        user1 = django_user_model.objects.create_user(username="owner1", password="pass")
        user2 = django_user_model.objects.create_user(username="owner2", password="pass")
        client.force_login(user2)

        room_type = RoomType.objects.create(name="Suite", base_price_per_night=550)
        room = Room.objects.create(room_number="303", room_type=room_type)
        client_obj = Client.objects.create(first_name="VIP", last_name="Guest")

        booking = Booking.objects.create(
            room=room,
            client=client_obj,
            owner=user1,  # already owned
            check_in="2026-06-01",
            check_out="2026-06-03",
            status="confirmed",
        )

        resp = client.post(
            reverse("accounts:booking-link", kwargs={"booking_id": booking.id}),
            follow=True,
        )
        assert resp.status_code == 200
        booking.refresh_from_db()
        assert booking.owner == user1  # unchanged
        assert b"already linked to another account" in resp.content


@pytest.mark.django_db
class TestGoogleOAuthRedirect:
    """Google SSO entry point redirects to Google."""

    @override_settings(SOCIALACCOUNT_PROVIDERS={"google": {"SCOPE": ["profile", "email"]}})
    def test_google_login_redirects(self, client):
        _create_google_app()
        resp = client.get(reverse("google_login"))
        # allauth redirects to Google's OAuth endpoint; status is 302
        assert resp.status_code == 302
        assert "accounts.google.com" in resp.url or "google.com" in resp.url
