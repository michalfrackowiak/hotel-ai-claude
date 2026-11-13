"""Test configuration for the hotel_ai project."""

from __future__ import annotations

import copy as _copy
import datetime as _dt
import json
import uuid
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse
from django.template.context import BaseContext

# Patch around the Python 3.15 / Django 4.2 template-context-copy bug so
# tests can finish without the tearDown crash in store_rendered_templates.
def _safe_copy_basecontext(self):
    dup = object.__new__(type(self))
    dup.dicts = list(self.dicts)
    return dup

BaseContext.__copy__ = _safe_copy_basecontext


# ---------------------------------------------------------------------------
# Pytest fixture overrides
# ---------------------------------------------------------------------------

@pytest.fixture
def client(db):
    """Use Django's plain Client to sidestep the APIClient template-copy bug on Python 3.15."""
    return Client()


@pytest.fixture
def api_url():
    def _url(pattern_name, *args, **kwargs):
        return reverse(f"bookings:{pattern_name}", *args, **kwargs)
    return _url


# ---------------------------------------------------------------------------
# Helpers for booking API assertions
# ---------------------------------------------------------------------------
class BookingAPI:
    def __init__(self, client):
        self.c = client

    def check_availability(self, check_in, check_out, guests=1, room_type=None):
        params = {"check_in": check_in, "check_out": check_out, "guests": str(guests)}
        if room_type:
            params["room_type"] = room_type
        resp = self.c.get("/api/availability/", params)
        resp.raise_for_status()
        return resp.json()

    def pricing(self, room_type, check_in, check_out):
        resp = self.c.get(
            "/api/pricing/",
            {"room_type": room_type, "check_in": check_in, "check_out": check_out},
        )
        resp.raise_for_status()
        return resp.json()

    def book(self, **kwargs):
        payload = {
            "room_type": kwargs["room_type"],
            "room_number": kwargs["room_number"],
            "check_in": kwargs["check_in"],
            "check_out": kwargs["check_out"],
            "guests": kwargs.get("guests", 1),
            "first_name": kwargs["first_name"],
            "last_name": kwargs.get("last_name", ""),
            "email": kwargs.get("email", ""),
            "phone": kwargs.get("phone", ""),
            "language": kwargs.get("language", "en"),
            "notes": kwargs.get("notes", ""),
            "source": kwargs.get("source", "chat"),
        }
        resp = self.c.post(
            "/api/bookings/create/", json.dumps(payload), content_type="application/json"
        )
        resp.raise_for_status()
        return resp.json()

    def cancel(self, pk):
        resp = self.c.post(f"/api/bookings/{pk}/cancel/")
        resp.raise_for_status()
        return resp.json()


@pytest.fixture
def api(client):
    return BookingAPI(client)


# ---------------------------------------------------------------------------
# Fixtures shared across test modules
# ---------------------------------------------------------------------------
@pytest.fixture
def hotel_data(db):
    from hotel_ai.bookings.models import RoomType, Room

    # Derive unique suffixes so parallel modules sharing this session can
    # coexist without violating room_number uniqueness constraints.
    tag = uuid.uuid4().hex[:6]
    single = RoomType.objects.create(
        name=f"Single-{tag}", base_price_per_night=Decimal("200.00"), capacity=1
    )
    double = RoomType.objects.create(
        name=f"Double-{tag}", base_price_per_night=Decimal("300.00"), capacity=2
    )
    suite = RoomType.objects.create(
        name=f"Suite-{tag}", base_price_per_night=Decimal("550.00"), capacity=3
    )
    rooms = {}
    for rt in (single, double, suite):
        for n in range(1, 4):
            number = f"{rt.name[0].upper()}{n}"
            key = f"{rt.name}-{number}"
            rooms[key] = Room.objects.create(
                room_number=number, room_type=rt, floor=1, is_active=True
            )
    return {"types": {single.name: single, double.name: double, suite.name: suite}, "rooms": rooms}


@pytest.fixture
def today():
    return _dt.date.today()


@pytest.fixture
def future_date():
    return _dt.date.today() + _dt.timedelta(days=7)


@pytest.fixture
def tomorrow():
    return _dt.date.today() + _dt.timedelta(days=1)


__all__ = [
    "client",
    "api_url",
    "BookingAPI",
    "api",
    "hotel_data",
    "today",
    "future_date",
    "tomorrow",
]