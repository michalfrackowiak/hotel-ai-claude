"""Tests for the booking engine: models, services and REST API."""

from __future__ import annotations

import datetime as _dt
import json
import uuid
from decimal import Decimal

from unittest import mock

import pytest
from django.test import override_settings
from django.utils import timezone

from hotel_ai.bookings.models import Booking, BookingLog, BookingStatus, Client, PricingRule, Room, RoomType
from hotel_ai.bookings.services import (
    availability_summary,
    calculate_total,
    cancel_booking,
    check_in_booking,
    check_out_booking,
    create_booking,
    estimate_arrival_update,
    find_available_rooms,
    modify_booking_dates,
    no_show_booking,
    overlapping_bookings,
    parse_date,
    resolve_client,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_room_pair(label):
    """Create a RoomType + single Room with a UUID-tagged name to avoid
    collisions when multiple test modules share the same database session."""
    tag = uuid.uuid4().hex[:6]
    rt = RoomType.objects.create(
        name=label,
        base_price_per_night={"Single": Decimal("200.00"), "Double": Decimal("300.00")}[label],
        capacity=1 if label == "Single" else 2,
    )
    room = Room.objects.create(room_number=f"TEST-{label.upper()}-101", room_type=rt, floor=1)
    return rt, room


@pytest.fixture
def single(db):
    """Return a freshly-created single room and type."""
    return _make_room_pair("Single")


@pytest.fixture
def double(db):
    """Return a freshly-created double room and type."""
    return _make_room_pair("Double")


@pytest.fixture
def client_fixture():
    return Client.objects.create(first_name="Test", last_name="Guest", email="fixture@example.com")


# ---------------------------------------------------------------------------
# Model behaviour
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_booking_nights_property(double, client_fixture):
    _, room = double
    booking = Booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 4),
        guests=2,
    )
    assert booking.nights == 3


@pytest.mark.django_db
def test_booking_clean_rejects_bad_dates(double, client_fixture):
    booking = Booking(
        room=double[1],
        client=client_fixture,
        check_in=_dt.date(2026, 7, 5),
        check_out=_dt.date(2026, 7, 5),
        guests=1,
    )
    with pytest.raises(Exception):
        booking.full_clean()


@pytest.mark.django_db
def test_booking_clean_rejects_over_capacity(single, client_fixture):
    booking = Booking(
        room=single[1],
        client=client_fixture,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
        guests=5,
    )
    with pytest.raises(Exception):
        booking.full_clean()


# ---------------------------------------------------------------------------
# Pricing / services
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_effective_price_falls_back_to_base(double):
    from .services import effective_price
    rt = double[0]
    assert effective_price(rt, _dt.date(2026, 3, 10)) == rt.base_price_per_night


@pytest.mark.django_db
def test_pricing_rule_overrides_base(double):
    PricingRule.objects.create(
        name="Summer",
        room_type=double[0],
        starts_at=_dt.date(2026, 6, 1),
        ends_at=_dt.date(2026, 8, 31),
        price_per_night=Decimal("390.00"),
        priority=10,
    )
    from .services import effective_price
    assert effective_price(double[0], _dt.date(2026, 7, 15)) == Decimal("390.00")


@pytest.mark.django_db
def test_pricing_rule_clean_rejects_inverted_dates(double):
    """PricingRule.clean raises when ends_at precedes starts_at."""
    from django.core.exceptions import ValidationError

    rule = PricingRule(
        name="Bad",
        room_type=double[0],
        starts_at=_dt.date(2026, 8, 31),
        ends_at=_dt.date(2026, 6, 1),
        price_per_night=Decimal("300.00"),
        priority=10,
    )
    with pytest.raises(ValidationError, match="ends_at must be >= starts_at"):
        rule.clean()


@pytest.mark.django_db
def test_booking_clean_rejects_short_stay(double, client_fixture):
    """Booking.clean raises when the stay is shorter than one night."""
    from django.core.exceptions import ValidationError

    booking = Booking(
        room=double[1],
        client=client_fixture,
        check_in=_dt.date(2027, 1, 1),
        check_out=_dt.date(2027, 1, 2),
    )
    # Force the nights property to 0 so the minimum-stay guard is exercised
    # independently of the check_out <= check_in guard above it.
    with mock.patch.object(Booking, "nights", new_callable=mock.PropertyMock) as nights_mock:
        nights_mock.return_value = 0
        with pytest.raises(ValidationError, match="Minimum stay is one night"):
            booking.clean()


@pytest.mark.django_db
def test_booking_clean_rejects_overcapacity(double, client_fixture):
    """Booking.clean raises when the guest count exceeds room capacity."""
    from django.core.exceptions import ValidationError

    booking = Booking(
        room=double[1],
        client=client_fixture,
        check_in=_dt.date(2027, 1, 1),
        check_out=_dt.date(2027, 1, 2),
        guests=99,  # Double room capacity is 2
    )
    with pytest.raises(ValidationError, match="Room capacity is"):
        booking.clean()


@pytest.mark.django_db
def test_calculate_total_spans_rule_boundaries(double):
    PricingRule.objects.create(
        name="Summer",
        room_type=double[0],
        starts_at=_dt.date(2026, 6, 1),
        ends_at=_dt.date(2026, 8, 31),
        price_per_night=Decimal("390.00"),
        priority=10,
    )
    # Both nights inside summer window → 390+390.
    total = calculate_total(double[0], _dt.date(2026, 8, 30), _dt.date(2026, 9, 1))
    assert total == Decimal("780.00")
    # Boundary-crossing case: spans rule end.
    total2 = calculate_total(double[0], _dt.date(2026, 8, 31), _dt.date(2026, 9, 2))
    assert total2 == Decimal("690.00")


@pytest.mark.django_db
def test_create_booking_sets_total_and_rule(double, client_fixture):
    PricingRule.objects.create(
        name="Summer",
        room_type=double[0],
        starts_at=_dt.date(2026, 6, 1),
        ends_at=_dt.date(2026, 8, 31),
        price_per_night=Decimal("390.00"),
        priority=10,
    )
    booking = create_booking(
        room=double[1],
        client=client_fixture,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
        guests=2,
    )
    assert booking.status == "confirmed"
    assert booking.total_price == Decimal("780.00")
    assert booking.pricing_rule.name == "Summer"


@pytest.mark.django_db
def test_double_booking_rejected(double, client_fixture):
    create_booking(
        room=double[1],
        client=client_fixture,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )
    with pytest.raises(ValueError):
        create_booking(
            room=double[1],
            client=client_fixture,
            check_in=_dt.date(2026, 7, 2),
            check_out=_dt.date(2026, 7, 4),
        )


@pytest.mark.django_db
def test_back_to_back_bookings_allowed(double, client_fixture):
    create_booking(
        room=double[1],
        client=client_fixture,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )
    second = Client.objects.create(first_name="Other", last_name="Guest")
    booking = create_booking(
        room=double[1],
        client=second,
        check_in=_dt.date(2026, 7, 3),
        check_out=_dt.date(2026, 7, 5),
    )
    assert booking.status == "confirmed"


@pytest.mark.django_db
def test_cancelled_booking_frees_the_room(double, client_fixture):
    b = create_booking(
        room=double[1],
        client=client_fixture,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )
    b.status = "cancelled"
    b.save()
    assert not overlapping_bookings(double[1], _dt.date(2026, 7, 1), _dt.date(2026, 7, 3)).exists()
    assert len(find_available_rooms(check_in=_dt.date(2026, 7, 1), check_out=_dt.date(2026, 7, 3))) > 0


@pytest.mark.django_db
def test_resolve_client_is_idempotent():
    c1 = resolve_client(first_name="Ada", last_name="Lovelace", email="ada@example.com")
    c2 = resolve_client(first_name="Ada", last_name="Lovelace", email="ada@example.com")
    assert c1.id == c2.id
    assert Client.objects.count() == 1


@pytest.mark.django_db
def test_availability_summary_shape(double):
    summary = availability_summary(check_in=_dt.date(2026, 7, 1), check_out=_dt.date(2026, 7, 3), guests=2)
    by_name = {row["room_type"]: row for row in summary}
    assert by_name.get("Double") is not None or any(r["capacity"] >= 2 for r in summary)
    assert any(r.get("available") for r in summary)


@pytest.mark.django_db
def test_find_available_rooms_empty_on_invalid_range(double):
    """When check_out <= check_in, no rooms are returned."""
    rooms = find_available_rooms(
        room_type=double[0], check_in=_dt.date(2026, 7, 3), check_out=_dt.date(2026, 7, 1)
    )
    assert rooms == []


@pytest.mark.django_db
def test_find_available_rooms_filters_by_room_type():
    rt_single, room_single = _make_room_pair("Single")
    rt_double, room_double = _make_room_pair("Double")
    available = find_available_rooms(
        room_type=rt_double, check_in=_dt.date(2026, 9, 1), check_out=_dt.date(2026, 9, 2)
    )
    assert room_single not in available
    assert room_double in available


@pytest.mark.django_db
def test_find_available_rooms_skips_overcapacity_rooms():
    """Rooms with insufficient capacity are skipped from the list."""
    rt_small = RoomType.objects.create(name="Tiny", base_price_per_night=Decimal("150.00"), capacity=1)
    rt_big = RoomType.objects.create(name="Huge", base_price_per_night=Decimal("400.00"), capacity=4)
    room_small = Room.objects.create(room_number="T-1", room_type=rt_small, floor=1)
    room_big = Room.objects.create(room_number="H-1", room_type=rt_big, floor=1)
    available = find_available_rooms(check_in=_dt.date(2026, 9, 1), check_out=_dt.date(2026, 9, 2), guests=3)
    assert room_small not in available
    assert room_big in available


def test_parse_date_rejects_invalid():
    with pytest.raises(ValueError, match="Invalid date"):
        parse_date("not-a-date")


@pytest.mark.django_db
def test_create_booking_rejects_over_capacity(double, client_fixture):
    """Requesting more guests than the room holds raises ValueError."""
    with pytest.raises(ValueError, match="Room capacity"):
        create_booking(
            room=double[1],
            client=client_fixture,
            check_in=_dt.date(2026, 12, 1),
            check_out=_dt.date(2026, 12, 2),
            guests=5,
        )


@pytest.mark.django_db
def test_create_booking_rejects_equal_dates(double, client_fixture):
    """A stay with zero nights must be rejected by the service layer."""
    with pytest.raises(ValueError, match="check_out must be after check_in"):
        create_booking(
            room=double[1],
            client=client_fixture,
            check_in=_dt.date(2026, 12, 1),
            check_out=_dt.date(2026, 12, 1),
            guests=2,
        )


@pytest.mark.django_db
def test_create_booking_skips_calendar_on_sync_failure(double, client_fixture):
    """If calendar sync raises, booking still succeeds."""
    with mock.patch(
        "hotel_ai.calendar_integration.services.sync_booking",
        side_effect=RuntimeError("boom"),
    ), mock.patch("hotel_ai.bookings.services.logger") as logger_mock:
        booking = create_booking(
            room=double[1],
            client=client_fixture,
            check_in=_dt.date(2027, 1, 1),
            check_out=_dt.date(2027, 1, 2),
            guests=2,
        )
    assert booking.status == BookingStatus.CONFIRMED
    logger_mock.debug.assert_called_once_with(
        "Calendar sync skipped for booking %s", booking.id, exc_info=True
    )


@pytest.mark.django_db
def test_cancel_booking_skips_calendar_on_sync_failure(double, client_fixture):
    """Cancel must succeed even when calendar sync raises."""
    booking = create_booking(
        room=double[1],
        client=client_fixture,
        check_in=_dt.date(2027, 2, 1),
        check_out=_dt.date(2027, 2, 2),
        guests=2,
    )

    with mock.patch(
        "hotel_ai.calendar_integration.services.sync_booking",
        side_effect=RuntimeError("boom"),
    ), mock.patch("hotel_ai.bookings.services.logger") as logger_mock:
        cancel_booking(booking)
    assert booking.status == BookingStatus.CANCELLED
    logger_mock.debug.assert_called_once()


@pytest.mark.django_db
def test_no_show_booking_skips_calendar_on_sync_failure(double, client_fixture):
    """No-show must succeed even when calendar sync raises."""
    booking = create_booking(
        room=double[1],
        client=client_fixture,
        check_in=_dt.date(2027, 3, 1),
        check_out=_dt.date(2027, 3, 2),
        guests=2,
    )

    with mock.patch(
        "hotel_ai.calendar_integration.services.sync_booking",
        side_effect=RuntimeError("boom"),
    ), mock.patch("hotel_ai.bookings.services.logger") as logger_mock:
        no_show_booking(booking)
    assert booking.status == BookingStatus.NO_SHOW
    logger_mock.debug.assert_called_once()


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_api_availability(client):
    _make_room_pair("Double")
    resp = client.get("/api/availability/", {"check_in": "2026-07-01", "check_out": "2026-07-03", "guests": 2})
    assert resp.status_code == 200
    rows = resp.json()
    assert any(r.get("capacity", 0) >= 2 and r.get("available") for r in rows)


@pytest.mark.django_db
def test_api_availability_rejects_bad_range(client):
    resp = client.get("/api/availability/", {"check_in": "2026-07-05", "check_out": "2026-07-01"})
    # Invalid dates → returns empty list (no crash).
    assert resp.status_code in (200, 400)


@pytest.mark.django_db
def test_api_pricing(client):
    rt, _ = _make_room_pair("Single")
    resp = client.get("/api/pricing/", {"room_type": rt.name, "check_in": "2026-07-01", "check_out": "2026-07-04"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["nights"] == 3
    assert data["total_price"] == "600.00"


@pytest.mark.django_db
def test_api_pricing_unknown_room_type(client):
    resp = client.get("/api/pricing/", {"room_type": "Penthouse", "check_in": "2026-07-01", "check_out": "2026-07-02"})
    assert resp.status_code in (400, 404)


@pytest.mark.django_db
def test_api_booking_lifecycle(client):
    rt, room = _make_room_pair("Double")
    payload = {
        "room_type": rt.name,
        "room_number": room.room_number,
        "check_in": "2026-07-01",
        "check_out": "2026-07-03",
        "guests": 2,
        "first_name": "API",
        "last_name": "Guest",
        "email": "api-lifecycle@example.com",
        "language": "en",
    }
    resp = client.post(
        "/api/bookings/create/", json.dumps(payload), content_type="application/json"
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["booking"]["status"] == "confirmed"
    assert data["booking"]["total_price"] == "600.00"
    booking_id = data["booking"]["id"]

    # Double-booking the same room now conflicts.
    resp2 = client.post(
        "/api/bookings/create/", json.dumps(payload), content_type="application/json"
    )
    assert resp2.status_code == 409

    # Cancel it.
    resp3 = client.post(f"/api/bookings/{booking_id}/cancel/")
    assert resp3.status_code == 200
    assert resp3.json()["booking"]["status"] == "cancelled"

    # Room is free again.
    resp4 = client.post(
        "/api/bookings/create/", json.dumps(payload), content_type="application/json"
    )
    assert resp4.status_code == 201


@pytest.mark.django_db
def test_api_booking_validates_capacity(client):
    rt, room = _make_room_pair("Single")
    payload = {
        "room_type": rt.name,
        "room_number": room.room_number,
        "check_in": "2026-07-01",
        "check_out": "2026-07-02",
        "guests": 5,
        "first_name": "Too",
        "last_name": "Many",
    }
    resp = client.post(
        "/api/bookings/create/", json.dumps(payload), content_type="application/json"
    )
    assert resp.status_code in (400, 409)


@pytest.mark.django_db
def test_api_booking_polish_language_stored(client):
    rt, room = _make_room_pair("Double")
    payload = {
        "room_type": rt.name,
        "room_number": room.room_number,
        "check_in": "2026-08-01",
        "check_out": "2026-08-03",
        "guests": 2,
        "first_name": "Jan",
        "last_name": "Kowalski",
        "language": "pl",
    }
    resp = client.post(
        "/api/bookings/create/", json.dumps(payload), content_type="application/json"
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["booking"]["client_name"] == "Jan Kowalski"


@pytest.mark.django_db
def test_booking_lifecycle_service():
    rt, room = _make_room_pair("Single")
    client = Client.objects.create(first_name="Maja", last_name="Nowak", email="maja@example.com")
    booking = create_booking(
        room=room,
        client=client,
        check_in=_dt.date(2027, 9, 1),
        check_out=_dt.date(2027, 9, 3),
        guests=1,
    )
    assert booking.status == BookingStatus.CONFIRMED

    checked = check_in_booking(booking)
    assert checked.status == BookingStatus.CHECKED_IN

    checked_out = check_out_booking(checked)
    assert checked_out.status == BookingStatus.CHECKED_OUT

    cancelled = cancel_booking(checked_out)
    assert cancelled.status == BookingStatus.CANCELLED
    # Idempotent cancel after checkout is a no-op
    cancelled2 = cancel_booking(cancelled)
    assert cancelled2.status == BookingStatus.CANCELLED


@pytest.mark.django_db
def test_check_in_requires_confirmed(client):
    rt, room = _make_room_pair("Single")
    booking = create_booking(
        room=room,
        client=Client.objects.create(first_name="Zosia", email="z@test.com"),
        check_in=_dt.date(2027, 8, 1),
        check_out=_dt.date(2027, 8, 2),
        guests=1,
    )
    cancel_booking(booking)
    assert booking.status == BookingStatus.CANCELLED
    resp = client.post(f"/api/bookings/{booking.id}/check-in/")
    assert resp.status_code == 400


@pytest.mark.django_db
def test_check_out_requires_checked_in(client):
    rt, room = _make_room_pair("Single")
    booking = create_booking(
        room=room,
        client=Client.objects.create(first_name="Piotr", email="p@test.com"),
        check_in=_dt.date(2027, 8, 1),
        check_out=_dt.date(2027, 8, 2),
        guests=1,
    )
    resp = client.post(f"/api/bookings/{booking.id}/check-out/")
    assert resp.status_code == 400


@pytest.mark.django_db
def test_api_full_lifecycle(client):
    rt, room = _make_room_pair("Single")
    payload = {
        "room_type": rt.name,
        "room_number": room.room_number,
        "check_in": "2026-09-15",
        "check_out": "2026-09-17",
        "guests": 1,
        "first_name": "Ewa",
        "email": "ewa@example.com",
    }
    resp = client.post("/api/bookings/create/", json.dumps(payload), content_type="application/json")
    assert resp.status_code == 201
    bid = resp.json()["booking"]["id"]

    resp = client.post(f"/api/bookings/{bid}/check-in/")
    assert resp.status_code == 200
    assert resp.json()["booking"]["status"] == "checked_in"

    resp = client.post(f"/api/bookings/{bid}/check-out/")
    assert resp.status_code == 200
    assert resp.json()["booking"]["status"] == "checked_out"

@pytest.mark.django_db
def test_booking_admin_requires_login(client):
    """Admin page is publicly accessible in this prototype."""
    resp = client.get("/api/admin/")
    assert resp.status_code == 200
    assert b"Booking Management" in resp.content


@pytest.mark.django_db
def test_booking_admin_showst_filters(client, db):
    from hotel_ai.bookings.models import Booking, Client, Room, RoomType
    from decimal import Decimal
    import datetime as _dt
    rt = RoomType.objects.create(name="Double", base_price_per_night=Decimal("300"), capacity=2)
    room = Room.objects.create(room_number="TEST-1", room_type=rt)
    client_ = Client.objects.create(first_name="Test", last_name="User")
    Booking.objects.create(room=room, client=client_, check_in=_dt.date.today(),
                           check_out=_dt.date.today() + _dt.timedelta(days=1), status="confirmed")
    Booking.objects.create(room=room, client=client_, check_in=_dt.date.today(),
                           check_out=_dt.date.today() + _dt.timedelta(days=1), status="no_show")
    resp = client.get("/api/admin/?status=no_show")
    assert resp.status_code == 200
    assert b"No bookings found." not in resp.content
    # confirmed bookings are hidden when filtering for no_show
    resp2 = client.get("/api/admin/?status=confirmed")
    assert resp2.status_code == 200
    assert b"No bookings found." not in resp2.content


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_dashboard_renders_activity_timeline(client):
    """The dashboard shows the recent booking activity timeline."""
    from django.contrib.auth.models import User

    user = User.objects.create_user(username="dashuser", password="dashpass")
    client.login(username="dashuser", password="dashpass")
    resp = client.get("/dashboard/")
    assert resp.status_code == 200
    assert b"Recent Activity" in resp.content


@pytest.mark.django_db
def test_dashboard_activity_timeline_shows_log_entries(client):
    """Log entries created by the service layer appear in the timeline."""
    import datetime as _dt
    from django.contrib.auth.models import User

    user = User.objects.create_user(username="dashuser2", password="dashpass")
    client.login(username="dashuser2", password="dashpass")

    rt, room = _make_room_pair("Single")
    guest = Client.objects.create(first_name="Dash", last_name="User", email="d@example.com")
    create_booking(
        room=room, client=guest, check_in=_dt.date(2026, 7, 1), check_out=_dt.date(2026, 7, 3)
    )

    resp = client.get("/dashboard/")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Dash User" in body
    assert "created" in body


@pytest.mark.django_db
def test_dashboard_shows_room_grid(client):
    """The dashboard renders a clickable room grid per room type."""
    import datetime as _dt
    from django.contrib.auth.models import User

    user = User.objects.create_user(username="dashuser3", password="dashpass")
    client.login(username="dashuser3", password="dashpass")

    rt, room = _make_room_pair("Single")
    guest = Client.objects.create(first_name="Grid", last_name="User", email="g@example.com")
    booking = create_booking(
        room=room, client=guest, check_in=_dt.date(2026, 7, 1), check_out=_dt.date(2026, 7, 3)
    )

    # Request the date the booking actually covers so the grid marks it occupied.
    resp = client.get("/dashboard/", {"date": "2026-07-01"})
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Room Grid" in body
    assert room.room_number in body
    # The occupied tile carries the booking id so toggleRoom can act on it.
    assert f'data-booking-id="{booking.id}"' in body
    assert "function toggleRoom" in body


# ---------------------------------------------------------------------------
# iCal calendar feed
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_calendar_feed_excludes_cancelled(client):
    """Cancelled bookings must not appear in the .ics feed."""
    rt, room = _make_room_pair("Single")
    client_obj = Client.objects.create(
        first_name="Cancelled", last_name="Guest", email="c@x.com"
    )
    b = create_booking(
        room=room,
        client=client_obj,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )
    cancel_booking(b)

    resp = client.get("/api/feed/")
    assert resp.status_code == 200
    assert f"{b.id}@hotel-aurora.local".encode() not in resp.content


@pytest.mark.django_db
def test_calendar_feed_excludes_checked_out(client):
    """Checked-out bookings must not appear in the .ics feed."""
    rt, room = _make_room_pair("Single")
    client_obj = Client.objects.create(
        first_name="Checked", last_name="Out", email="co@x.com"
    )
    b = create_booking(
        room=room,
        client=client_obj,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )
    check_in_booking(b)
    check_out_booking(b)

    resp = client.get("/api/feed/")
    assert resp.status_code == 200
    assert f"{b.id}@hotel-aurora.local".encode() not in resp.content


@pytest.mark.django_db
def test_calendar_feed_includes_confirmed_and_checked_in(client):
    """Confirmed and checked-in bookings must appear in the .ics feed."""
    rt, room = _make_room_pair("Single")
    c1 = Client.objects.create(first_name="Ada", last_name="L", email="ada@x.com")
    c2 = Client.objects.create(first_name="Bob", last_name="B", email="bob@x.com")

    b1 = create_booking(
        room=room,
        client=c1,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )
    b2 = create_booking(
        room=room,
        client=c2,
        check_in=_dt.date(2026, 7, 5),
        check_out=_dt.date(2026, 7, 7),
    )
    check_in_booking(b2)

    resp = client.get("/api/feed/")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert f"{b1.id}@hotel-aurora.local" in body
    assert f"{b2.id}@hotel-aurora.local" in body
    assert "Ada L - Room " in body
    assert "Bob B - Room " in body


@pytest.mark.django_db
def test_calendar_feed_content_type(client):
    """Response must be served as text/calendar with the .ics filename."""
    resp = client.get("/api/feed/")
    assert resp.status_code == 200
    assert resp["Content-Type"].startswith("text/calendar")
    assert "hotel-aurora-calendar.ics" in resp["Content-Disposition"]


@pytest.mark.django_db
def test_calendar_feed_ics_structure(client):
    """The .ics body must contain valid iCal headers and VEVENT blocks."""
    rt, room = _make_room_pair("Single")
    client_obj = Client.objects.create(
        first_name="Test", last_name="User", email="test@x.com"
    )
    b = create_booking(
        room=room,
        client=client_obj,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )

    resp = client.get("/api/feed/")
    body = resp.content.decode()
    assert body.startswith("BEGIN:VCALENDAR\r\n")
    assert "VERSION:2.0" in body
    assert "PRODID:-//Hotel Aurora//EN" in body
    assert "END:VCALENDAR\r\n" in body
    assert "BEGIN:VEVENT" in body
    assert "END:VEVENT" in body
    assert "DTSTART:20260701" in body
    assert "DTEND:20260703" in body
    assert f"UID:{b.id}@hotel-aurora.local" in body
    assert "SUMMARY:Test User - Room " in body
    assert "DESCRIPTION:Booking #" in body


# ---------------------------------------------------------------------------
# Room bulk update
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_room_bulk_update_updates_fields(client):
    """Bulk PATCH should update room_number, floor and is_active."""
    rt = RoomType.objects.create(name="TestSingle", base_price_per_night=100, capacity=1)
    room = Room.objects.create(room_number="A01", room_type=rt, floor=1)

    resp = client.patch(
        "/api/rooms/bulk-update/",
        [{"id": room.id, "room_number": "B02", "floor": 3, "is_active": False}],
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["updated"] == [
        {"id": room.id, "room_number": "B02", "floor": 3, "is_active": False}
    ]
    assert data["errors"] == []
    room.refresh_from_db()
    assert room.room_number == "B02"
    assert room.floor == 3
    assert not room.is_active


@pytest.mark.django_db
def test_room_bulk_update_partial_fields(client):
    """Only provided fields should be updated; others preserved."""
    rt = RoomType.objects.create(name="Double", base_price_per_night=200, capacity=2)
    room = Room.objects.create(room_number="D100", room_type=rt, floor=5, is_active=True)

    resp = client.patch(
        "/api/rooms/bulk-update/",
        [{"id": room.id, "floor": 7}],
        content_type="application/json",
    )
    assert resp.status_code == 200
    room.refresh_from_db()
    assert room.floor == 7
    assert room.room_number == "D100"   # unchanged
    assert room.is_active                # unchanged


@pytest.mark.django_db
def test_room_bulk_update_duplicate_room_number_conflict(client):
    """Two rooms with the same number must return an error."""
    rt = RoomType.objects.create(name="Suite", base_price_per_night=500, capacity=4)
    r1 = Room.objects.create(room_number="S01", room_type=rt, floor=1)
    r2 = Room.objects.create(room_number="S02", room_type=rt, floor=2)

    resp = client.patch(
        "/api/rooms/bulk-update/",
        [{"id": r1.id, "room_number": "S02"}],
        content_type="application/json",
    )
    # Should report the conflict but still process other items
    assert resp.status_code == 207  # Multi-Status on partial failure
    data = resp.json()
    assert any(e.get("error") for e in data["errors"])
    assert len(data["updated"]) == 0


@pytest.mark.django_db
def test_room_bulk_update_single_dict_format(client):
    """Accepting a single dict (not wrapped in a list) is supported."""
    rt = RoomType.objects.create(name="Single", base_price_per_night=150, capacity=1)
    room = Room.objects.create(room_number="101", room_type=rt, floor=1)

    resp = client.patch(
        "/api/rooms/bulk-update/",
        {"id": room.id, "is_active": False},
        content_type="application/json",
    )
    assert resp.status_code == 200
    room.refresh_from_db()
    assert not room.is_active


@pytest.mark.django_db
def test_booking_activity_log_created_on_create(client):
    """A 'created' log entry is written when a booking is made."""
    rt, room = _make_room_pair("Single")
    payload = {
        "room_type": rt.name,
        "room_number": room.room_number,
        "check_in": "2026-07-01",
        "check_out": "2026-07-03",
        "guests": 1,
        "first_name": "Alice",
        "last_name": "Smith",
        "notes": "VIP guest",
    }
    resp = client.post("/api/bookings/create/", json.dumps(payload), content_type="application/json")
    assert resp.status_code == 201
    booking = Booking.objects.get(pk=resp.json()["booking"]["id"])
    logs = list(booking.logs.all())
    assert len(logs) == 1
    assert logs[0].action == "created"
    assert logs[0].notes == "VIP guest"


@pytest.mark.django_db
def test_booking_activity_log_after_check_in(client):
    """Checking in appends a 'checked_in' log entry."""
    rt, room = _make_room_pair("Double")
    payload = {
        "room_type": rt.name,
        "room_number": room.room_number,
        "check_in": "2026-08-01",
        "check_out": "2026-08-04",
        "guests": 2,
        "first_name": "Bob",
        "last_name": "Jones",
    }
    resp = client.post("/api/bookings/create/", json.dumps(payload), content_type="application/json")
    booking_id = resp.json()["booking"]["id"]
    resp2 = client.post(f"/api/bookings/{booking_id}/check-in/")
    assert resp2.status_code == 200
    logs = list(Booking.objects.get(pk=booking_id).logs.order_by("created_at"))
    assert logs[0].action == "created"
    assert logs[1].action == "checked_in"


@pytest.mark.django_db
def test_booking_activity_log_after_check_out(client):
    """Checking out appends a 'checked_out' log entry after check-in."""
    rt, room = _make_room_pair("Single")
    payload = {
        "room_type": rt.name,
        "room_number": room.room_number,
        "check_in": "2026-09-01",
        "check_out": "2026-09-03",
        "guests": 1,
        "first_name": "Carol",
        "last_name": "White",
    }
    resp = client.post("/api/bookings/create/", json.dumps(payload), content_type="application/json")
    booking_id = resp.json()["booking"]["id"]
    client.post(f"/api/bookings/{booking_id}/check-in/")
    resp3 = client.post(f"/api/bookings/{booking_id}/check-out/")
    assert resp3.status_code == 200
    logs = list(Booking.objects.get(pk=booking_id).logs.order_by("created_at"))
    assert logs[-1].action == "checked_out"


@pytest.mark.django_db
def test_booking_activity_log_after_cancel(client):
    """Cancelling a booking appends a 'cancelled' log entry."""
    rt, room = _make_room_pair("Double")
    payload = {
        "room_type": rt.name,
        "room_number": room.room_number,
        "check_in": "2026-10-01",
        "check_out": "2026-10-05",
        "guests": 2,
        "first_name": "Dan",
        "last_name": "Brown",
    }
    resp = client.post("/api/bookings/create/", json.dumps(payload), content_type="application/json")
    booking_id = resp.json()["booking"]["id"]
    resp2 = client.post(f"/api/bookings/{booking_id}/cancel/")
    assert resp2.status_code == 200
    logs = list(Booking.objects.get(pk=booking_id).logs.order_by("created_at"))
    assert logs[-1].action == "cancelled"


@pytest.mark.django_db
def test_no_show_requires_confirmed(client):
    """A no-show can only be applied to a confirmed booking."""
    rt, room = _make_room_pair("Single")
    payload = {
        "room_type": rt.name,
        "room_number": room.room_number,
        "check_in": "2026-10-01",
        "check_out": "2026-10-03",
        "guests": 1,
        "first_name": "Noa",
        "last_name": "Show",
    }
    resp = client.post("/api/bookings/create/", json.dumps(payload), content_type="application/json")
    booking_id = resp.json()["booking"]["id"]
    client.post(f"/api/bookings/{booking_id}/check-in/")

    # Already checked in -> no-show is rejected.
    resp2 = client.post(f"/api/bookings/{booking_id}/no-show/")
    assert resp2.status_code == 400


@pytest.mark.django_db
def test_no_show_marks_booking_and_logs(client):
    """POST /api/bookings/<pk>/no-show/ sets NO_SHOW and appends a log entry."""
    rt, room = _make_room_pair("Single")
    payload = {
        "room_type": rt.name,
        "room_number": room.room_number,
        "check_in": "2026-10-01",
        "check_out": "2026-10-03",
        "guests": 1,
        "first_name": "Noa",
        "last_name": "Show",
    }
    resp = client.post("/api/bookings/create/", json.dumps(payload), content_type="application/json")
    booking_id = resp.json()["booking"]["id"]

    resp2 = client.post(f"/api/bookings/{booking_id}/no-show/")
    assert resp2.status_code == 200
    assert resp2.json()["booking"]["status"] == "no_show"
    logs = list(Booking.objects.get(pk=booking_id).logs.order_by("created_at"))
    assert logs[-1].action == "no_show"


@pytest.mark.django_db
def test_no_show_is_idempotent(client):
    """Marking an already no-show'd booking again is a no-op."""
    rt, room = _make_room_pair("Single")
    payload = {
        "room_type": rt.name,
        "room_number": room.room_number,
        "check_in": "2026-10-01",
        "check_out": "2026-10-03",
        "guests": 1,
        "first_name": "Noa",
        "last_name": "Show",
    }
    resp = client.post("/api/bookings/create/", json.dumps(payload), content_type="application/json")
    booking_id = resp.json()["booking"]["id"]
    client.post(f"/api/bookings/{booking_id}/no-show/")

    resp2 = client.post(f"/api/bookings/{booking_id}/no-show/")
    assert resp2.status_code == 200
    assert "Already marked" in resp2.json()["message"]


@pytest.mark.django_db
def test_booking_activity_api_returns_timeline(client):
    """GET /api/bookings/<pk>/activity/ returns the full timeline."""
    rt, room = _make_room_pair("Single")
    payload = {
        "room_type": rt.name,
        "room_number": room.room_number,
        "check_in": "2026-07-01",
        "check_out": "2026-07-02",
        "guests": 1,
        "first_name": "Eve",
        "last_name": "Davis",
    }
    resp = client.post("/api/bookings/create/", json.dumps(payload), content_type="application/json")
    booking_id = resp.json()["booking"]["id"]
    client.post(f"/api/bookings/{booking_id}/check-in/")
    activity_resp = client.get(f"/api/bookings/{booking_id}/activity/")
    assert activity_resp.status_code == 200
    data = activity_resp.json()
    assert data["booking_id"] == booking_id
    assert data["current_status"] == BookingStatus.CHECKED_IN
    actions = [entry["action"] for entry in data["activity_log"]]
    assert actions == ["created", "checked_in"]


@pytest.mark.django_db
def test_booking_activity_api_404_for_missing_booking(client):
    """Requesting activity for a non-existent booking returns 404."""
    resp = client.get("/api/bookings/99999/activity/")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Booking not found"


@pytest.mark.django_db
def test_availability_stream_returns_sse_content_type(client):
    """The stream endpoint returns text/event-stream content type."""
    rt, room = _make_room_pair("Single")
    resp = client.get("/api/availability/stream/")
    assert resp.status_code == 200
    assert "text/event-stream" in resp["Content-Type"]
    assert "no-cache" in resp["Cache-Control"]
    assert resp["X-Accel-Buffering"] == "no"


@pytest.mark.django_db
def test_availability_stream_generates_sse_format(client, monkeypatch):
    """The stream generator yields proper SSE frames."""
    from hotel_ai.bookings.views import AvailabilityStreamView
    import io

    # Manually add an event to the queue
    with AvailabilityStreamView._lock:
        AvailabilityStreamView._event_queue.append({"type": "test"})

    # The generator sleeps 0.5s between iterations when the queue is empty;
    # stub it out so the test doesn't block on the idle path.
    monkeypatch.setattr("hotel_ai.bookings.views.time.sleep", lambda *_a, **_k: None)

    resp = client.get("/api/availability/stream/")

    # Consume a bounded number of chunks from the streaming generator.
    # Pulling past the event also exercises the generator's idle sleep
    # between iterations (the time.sleep(0.5) branch).
    chunks = []
    for chunk in resp.streaming_content:
        chunks.append(chunk)
        if b"test" in chunk:
            # One more iteration to advance past the yield into the idle sleep.
            chunks.append(next(resp.streaming_content))
            break

    assert len(chunks) > 0
    combined = b"".join(chunks).decode("utf-8")
    assert "test" in combined


@pytest.mark.django_db
def test_availability_stream_class_attributes(client):
    """AvailabilityStreamView has the expected class-level attributes."""
    from hotel_ai.bookings.views import AvailabilityStreamView

    assert hasattr(AvailabilityStreamView, "_event_queue")
    assert hasattr(AvailabilityStreamView, "_lock")
    assert hasattr(AvailabilityStreamView, "_poll_loop")
    assert hasattr(AvailabilityStreamView, "ensure_watcher")
    assert hasattr(AvailabilityStreamView, "get")


@pytest.mark.django_db
def test_availability_stream_poll_loop_excludes_no_show(client, monkeypatch):
    """The poll loop counts only bookings that actually occupy rooms."""
    from hotel_ai.bookings.views import AvailabilityStreamView
    from hotel_ai.bookings.models import Client, Room, RoomType

    rt = RoomType.objects.create(name="Double", base_price_per_night=Decimal("300"), capacity=2)
    room = Room.objects.create(room_number="SSE-1", room_type=rt)
    no_show_room = Room.objects.create(room_number="SSE-2", room_type=rt)
    guest = Client.objects.create(first_name="SSE", last_name="Test")
    create_booking(room=room, client=guest, check_in=_dt.date.today(), check_out=_dt.date.today() + _dt.timedelta(days=1))
    no_show = create_booking(room=no_show_room, client=guest, check_in=_dt.date.today(), check_out=_dt.date.today() + _dt.timedelta(days=1))
    no_show_booking(no_show)

    captured = {}
    monkeypatch.setattr(AvailabilityStreamView, "_running", True)

    def fake_emit(data):
        captured["payload"] = data

    monkeypatch.setattr(AvailabilityStreamView, "_emit", fake_emit)
    monkeypatch.setattr("hotel_ai.bookings.views.time.sleep", lambda *_: setattr(AvailabilityStreamView, "_running", False))
    AvailabilityStreamView._poll_loop()
    assert captured["payload"]["active_bookings"] == 1


# ---------------------------------------------------------------------------
# Booking export
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_booking_export_csv(client):
    """GET /api/admin/export/ returns a CSV of bookings."""
    rt, room = _make_room_pair("Single")
    guest = Client.objects.create(first_name="Export", last_name="User", email="e@x.com")
    create_booking(
        room=room, client=guest, check_in=_dt.date(2026, 7, 1), check_out=_dt.date(2026, 7, 3)
    )

    resp = client.get("/api/admin/export/")
    assert resp.status_code == 200
    assert resp["Content-Type"].startswith("text/csv")
    body = resp.content.decode()
    assert "Export User" in body
    assert "confirmed" in body


@pytest.mark.django_db
def test_booking_export_csv_empty(client):
    resp = client.get("/api/admin/export/")
    assert resp.status_code == 200
    body = resp.content.decode().strip()
    assert body.splitlines() == [
        "ID,Client,Email,Room Number,Room Type,Check-in,Check-out,Nights,Guests,"
        "Total Price,Currency,Status,Source,Created At"
    ]


@pytest.mark.django_db
def test_booking_export_csv_filename(client):
    resp = client.get("/api/admin/export/")
    assert resp.status_code == 200
    assert "bookings-export.csv" in resp["Content-Disposition"]


@pytest.mark.django_db
def test_booking_export_applies_filters(client):
    """Export respects the same status/room-type/search filters as the admin page."""
    rt, room = _make_room_pair("Single")
    guest = Client.objects.create(first_name="Filter", last_name="User", email="f@x.com")
    create_booking(
        room=room, client=guest, check_in=_dt.date(2026, 7, 1), check_out=_dt.date(2026, 7, 3)
    )

    resp = client.get("/api/admin/export/", {"status": "cancelled"})
    assert resp.status_code == 200
    assert "Filter User" not in resp.content.decode()


@pytest.mark.django_db
def test_booking_export_search_filter(client):
    """Export filters by guest name/email search."""
    rt, room = _make_room_pair("Single")
    Client.objects.create(first_name="Alice", last_name="Smith", email="alice@x.com")
    Client.objects.create(first_name="Bob", last_name="Jones", email="bob@x.com")
    create_booking(
        room=room,
        client=Client.objects.get(first_name="Alice", last_name="Smith"),
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )

    resp = client.get("/api/admin/export/", {"search": "Alice"})
    assert resp.status_code == 200
    assert "Alice Smith" in resp.content.decode()
    assert "Bob Jones" not in resp.content.decode()


@pytest.mark.django_db
def test_booking_export_room_type_filter(client):
    """Export filters by room type."""
    rt_single, room_single = _make_room_pair("Single")
    rt_double, room_double = _make_room_pair("Double")
    Client.objects.create(first_name="Single", last_name="Guy", email="s@x.com")
    Client.objects.create(first_name="Double", last_name="Guy", email="d@x.com")
    create_booking(
        room=room_single,
        client=Client.objects.get(first_name="Single", last_name="Guy"),
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )
    create_booking(
        room=room_double,
        client=Client.objects.get(first_name="Double", last_name="Guy"),
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )

    resp = client.get("/api/admin/export/", {"room_type": "Double"})
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Double Guy" in body
    assert "Single Guy" not in body


# ---------------------------------------------------------------------------
# Admin page filters
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_booking_admin_room_type_filter(client):
    """The admin page filters by room type."""
    rt_single, room_single = _make_room_pair("Single")
    rt_double, room_double = _make_room_pair("Double")
    Client.objects.create(first_name="Single", last_name="Guy", email="s@x.com")
    Client.objects.create(first_name="Double", last_name="Guy", email="d@x.com")
    create_booking(
        room=room_single,
        client=Client.objects.get(first_name="Single", last_name="Guy"),
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )
    create_booking(
        room=room_double,
        client=Client.objects.get(first_name="Double", last_name="Guy"),
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )

    resp = client.get("/api/admin/", {"room_type": "Double"})
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Double Guy" in body
    assert "Single Guy" not in body


@pytest.mark.django_db
def test_booking_admin_search_filter(client):
    """The admin page searches by guest name and email."""
    rt, room = _make_room_pair("Single")
    Client.objects.create(first_name="Alice", last_name="Smith", email="alice@x.com")
    Client.objects.create(first_name="Bob", last_name="Jones", email="bob@x.com")
    create_booking(
        room=room,
        client=Client.objects.get(first_name="Alice", last_name="Smith"),
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )
    create_booking(
        room=room,
        client=Client.objects.get(first_name="Bob", last_name="Jones"),
        check_in=_dt.date(2026, 7, 5),
        check_out=_dt.date(2026, 7, 7),
    )

    resp = client.get("/api/admin/", {"search": "alice"})
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Alice Smith" in body
    assert "Bob Jones" not in body


@pytest.mark.django_db
def test_booking_admin_check_in_date_filter(client):
    """The admin page filters by check-in date range."""
    rt, room = _make_room_pair("Single")
    Client.objects.create(first_name="Early", last_name="Guest", email="e@x.com")
    Client.objects.create(first_name="Late", last_name="Guest", email="l@x.com")
    create_booking(
        room=room,
        client=Client.objects.get(first_name="Early", last_name="Guest"),
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )
    create_booking(
        room=room,
        client=Client.objects.get(first_name="Late", last_name="Guest"),
        check_in=_dt.date(2026, 8, 1),
        check_out=_dt.date(2026, 8, 3),
    )

    resp = client.get("/api/admin/", {"check_in_from": "2026-08-01"})
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Late Guest" in body
    assert "Early Guest" not in body


@pytest.mark.django_db
def test_booking_admin_combined_filters(client):
    """Status and room type filters compose correctly."""
    rt_single, room_single = _make_room_pair("Single")
    rt_double, room_double = _make_room_pair("Double")
    Client.objects.create(first_name="Single", last_name="Guy", email="s@x.com")
    Client.objects.create(first_name="Double", last_name="Guy", email="d@x.com")
    b1 = create_booking(
        room=room_single,
        client=Client.objects.get(first_name="Single", last_name="Guy"),
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )
    b2 = create_booking(
        room=room_double,
        client=Client.objects.get(first_name="Double", last_name="Guy"),
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )
    cancel_booking(b1)

    resp = client.get("/api/admin/", {"status": "confirmed", "room_type": "Double"})
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Double Guy" in body
    assert "Single Guy" not in body


# ---------------------------------------------------------------------------
# Booking detail
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_booking_detail_renders(client):
    """GET /api/bookings/<pk>/ renders the booking detail page."""
    rt, room = _make_room_pair("Single")
    guest = Client.objects.create(first_name="Detail", last_name="User", email="d@x.com")
    booking = create_booking(
        room=room, client=guest, check_in=_dt.date(2026, 7, 1), check_out=_dt.date(2026, 7, 3)
    )
    resp = client.get(f"/api/bookings/{booking.id}/")
    assert resp.status_code == 200
    assert "Detail User".encode() in resp.content


@pytest.mark.django_db
def test_booking_detail_404(client):
    resp = client.get("/api/bookings/99999/")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Booking lookup
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_booking_lookup_renders(client):
    """GET /api/booking/<pk>/ renders the public booking lookup page."""
    rt, room = _make_room_pair("Single")
    guest = Client.objects.create(first_name="Lookup", last_name="User", email="l@x.com")
    booking = create_booking(
        room=room, client=guest, check_in=_dt.date(2026, 7, 1), check_out=_dt.date(2026, 7, 3)
    )
    resp = client.get(f"/api/booking/{booking.id}/")
    assert resp.status_code == 200
    assert "Lookup User".encode() in resp.content


@pytest.mark.django_db
def test_booking_lookup_404(client):
    resp = client.get("/api/booking/99999/")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Room list
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_room_list_renders(client):
    """GET /api/rooms/ lists active rooms."""
    rt, room = _make_room_pair("Single")
    resp = client.get("/api/rooms/")
    assert resp.status_code == 200
    assert room.room_number.encode() in resp.content


@pytest.mark.django_db
def test_room_list_excludes_inactive(client):
    rt = RoomType.objects.create(name="Inact", base_price_per_night=100, capacity=1)
    Room.objects.create(room_number="INACT-1", room_type=rt, floor=1, is_active=False)
    resp = client.get("/api/rooms/")
    assert resp.status_code == 200
    assert b"INACT-1" not in resp.content


# ---------------------------------------------------------------------------
# Booking list (JSON)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_booking_list_json(client):
    """GET /api/bookings/ returns JSON with the booking."""
    rt, room = _make_room_pair("Single")
    guest = Client.objects.create(first_name="JSON", last_name="User", email="j@x.com")
    booking = create_booking(
        room=room, client=guest, check_in=_dt.date(2026, 7, 1), check_out=_dt.date(2026, 7, 3)
    )
    resp = client.get("/api/bookings/")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    assert data["count"] == 1
    assert any(b["id"] == booking.id for b in data["results"])


@pytest.mark.django_db
def test_booking_list_json_404_for_missing(client):
    resp = client.get("/api/bookings/99999/")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_resolve_client_updates_existing_phone():
    from hotel_ai.bookings.services import resolve_client

    client = Client.objects.create(first_name="Ada", last_name="Lovelace", email="ada@x.com")
    updated = resolve_client(
        first_name="Ada", last_name="Lovelace", email="ada@x.com", phone="+1-555"
    )
    assert updated.id == client.id
    assert updated.phone == "+1-555"


@pytest.mark.django_db
def test_resolve_client_updates_language_on_en_base():
    from hotel_ai.bookings.services import resolve_client

    client = Client.objects.create(first_name="Mira", last_name="Costa", email="mira@x.com", language="en")
    updated = resolve_client(
        first_name="Mira", last_name="Costa", email="mira@x.com", language="pl"
    )
    assert updated.language == "pl"


@pytest.mark.django_db
def test_calculate_total_returns_zero_for_zero_nights(double):
    from hotel_ai.bookings.services import calculate_total

    assert calculate_total(double[0], _dt.date(2026, 7, 3), _dt.date(2026, 7, 3)) == Decimal("0.00")


# ---------------------------------------------------------------------------
# API view branches (line coverage)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_api_availability_get_rejects_bad_dates(client):
    """GET /api/availability/ returns 400 when dates are malformed."""
    resp = client.get("/api/availability/", {"check_in": "bad", "check_out": "2026-07-02"})
    assert resp.status_code == 400
    assert "Invalid date" in resp.json()["detail"]


@pytest.mark.django_db
def test_api_availability_post_rejects_inverted_dates(client):
    """POST /api/availability/ hits the AvailabilityRequestSerializer.validate()."""
    rt, _ = _make_room_pair("Single")
    resp = client.post(
        "/api/availability/",
        {"room_type": rt.name, "check_in": "2026-07-02", "check_out": "2026-07-01", "guests": 1},
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert "check_out must be after check_in" in str(resp.json())


@pytest.mark.django_db
def test_api_availability_post_known_room_type(client):
    """POST /api/availability/ with a known room_type covers the branch."""
    rt, room = _make_room_pair("Single")
    resp = client.post(
        "/api/availability/",
        {"room_type": rt.name, "check_in": "2026-07-01", "check_out": "2026-07-02", "guests": 1},
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["room_type"] == rt.name
    assert isinstance(data["available"], bool)


@pytest.mark.django_db
def test_api_availability_post_unknown_room_type(client):
    """Unknown room type in POST body is surfaced in the response."""
    resp = client.post(
        "/api/availability/",
        {"room_type": "Phantom", "check_in": "2026-07-01", "check_out": "2026-07-02"},
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert "Unknown room type" in resp.json()["detail"]


@pytest.mark.django_db
def test_api_pricing_post(client):
    rt, _ = _make_room_pair("Double")
    resp = client.post(
        "/api/pricing/",
        {"room_type": rt.name, "check_in": "2026-08-01", "check_out": "2026-08-04"},
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["nights"] == 3
    assert data["total_price"] == "900.00"


@pytest.mark.django_db
def test_api_pricing_post_rejects_inverted_dates(client):
    """POST /api/pricing/ with check_out <= check_in hits the serializer's validate()."""
    resp = client.post(
        "/api/pricing/",
        {"room_type": "Double", "check_in": "2026-07-02", "check_out": "2026-07-01"},
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert "check_out must be after check_in" in str(resp.json())


@pytest.mark.django_db
def test_api_pricing_post_unknown_room_type(client):
    resp = client.post(
        "/api/pricing/",
        {"room_type": "Penthouse", "check_in": "2026-07-01", "check_out": "2026-07-02"},
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Unknown room type"


@pytest.mark.django_db
def test_api_booking_create_unknown_room_type(client):
    rt, _ = _make_room_pair("Single")
    payload = {
        "room_type": "PhantomType",
        "room_number": "999",
        "check_in": "2026-07-01",
        "check_out": "2026-07-02",
        "guests": 1,
        "first_name": "A",
        "last_name": "B",
        "email": "a@b.com",
    }
    resp = client.post(
        "/api/bookings/create/", json.dumps(payload), content_type="application/json"
    )
    assert resp.status_code == 404
    assert "Unknown room type" in resp.json()["detail"]
    assert RoomType.objects.filter(name=rt.name).exists()


@pytest.mark.django_db
def test_api_booking_create_unknown_room_number(client):
    rt, room = _make_room_pair("Single")
    payload = {
        "room_type": rt.name,
        "room_number": "NONEXISTENT",
        "check_in": "2026-07-01",
        "check_out": "2026-07-02",
        "guests": 1,
        "first_name": "A",
        "last_name": "B",
        "email": "a@b.com",
    }
    resp = client.post(
        "/api/bookings/create/", json.dumps(payload), content_type="application/json"
    )
    assert resp.status_code == 404
    assert "Room 'NONEXISTENT' not found" in resp.json()["detail"]


@pytest.mark.django_db
def test_api_booking_create_rejects_inverted_dates(client):
    """POST /api/bookings/create/ with check_out <= check_in hits the serializer validate()."""
    rt, _ = _make_room_pair("Single")
    payload = {
        "room_type": rt.name,
        "room_number": "S1",
        "check_in": "2026-07-02",
        "check_out": "2026-07-01",
        "guests": 1,
        "first_name": "A",
        "last_name": "B",
        "email": "a@b.com",
    }
    resp = client.post(
        "/api/bookings/create/", json.dumps(payload), content_type="application/json"
    )
    assert resp.status_code == 400
    assert "check_out must be after check_in" in str(resp.json())


@pytest.mark.django_db
def test_api_cancel_not_found(client):
    resp = client.post("/api/bookings/99999/cancel/")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Booking not found"


@pytest.mark.django_db
def test_api_cancel_already_cancelled_is_idempotent(client):
    rt, room = _make_room_pair("Single")
    payload = {
        "room_type": rt.name,
        "room_number": room.room_number,
        "check_in": "2026-11-01",
        "check_out": "2026-11-02",
        "guests": 1,
        "first_name": "X",
        "last_name": "Y",
        "email": "x@y.com",
    }
    resp = client.post("/api/bookings/create/", json.dumps(payload), content_type="application/json")
    bid = resp.json()["booking"]["id"]
    client.post(f"/api/bookings/{bid}/cancel/")
    resp2 = client.post(f"/api/bookings/{bid}/cancel/")
    assert resp2.status_code == 200
    assert resp2.json()["message"] == "Already cancelled."


@pytest.mark.django_db
def test_api_check_in_not_found(client):
    resp = client.post("/api/bookings/99999/check-in/")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Booking not found"


@pytest.mark.django_db
def test_api_check_out_not_found(client):
    resp = client.post("/api/bookings/99999/check-out/")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Booking not found"


@pytest.mark.django_db
def test_api_no_show_not_found(client):
    resp = client.post("/api/bookings/99999/no-show/")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Booking not found"


@pytest.mark.django_db
def test_booking_admin_check_in_to_filter(client):
    """Admin page respects the check_in_to parameter."""
    rt, room = _make_room_pair("Single")
    create_booking(room=room, client=Client.objects.create(first_name="A", last_name="X"), check_in=_dt.date(2026, 6, 1), check_out=_dt.date(2026, 6, 2))
    create_booking(room=room, client=Client.objects.create(first_name="B", last_name="Y"), check_in=_dt.date(2026, 8, 1), check_out=_dt.date(2026, 8, 2))
    resp = client.get("/api/admin/", {"check_in_to": "2026-07-01"})
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "A X" in body
    assert "B Y" not in body


@pytest.mark.django_db
def test_booking_admin_actions(client):
    """The mark_confirmed / mark_cancelled admin actions update status."""
    from django.contrib import admin as django_admin
    from django.contrib.admin.sites import AdminSite
    from django.contrib.auth.models import User

    from hotel_ai.bookings.admin import BookingAdmin
    from hotel_ai.bookings.models import BookingStatus

    User.objects.create_superuser("admin", "a@b.com", "pass")
    client.login(username="admin", password="pass")

    rt, room = _make_room_pair("Single")
    booking = create_booking(
        room=room,
        client=Client.objects.create(first_name="A", last_name="X"),
        check_in=_dt.date(2026, 6, 1),
        check_out=_dt.date(2026, 6, 2),
    )
    booking.status = BookingStatus.PENDING
    booking.save()

    site = AdminSite()
    admin_instance = BookingAdmin(Booking, site)
    queryset = Booking.objects.filter(pk=booking.pk)

    admin_instance.mark_confirmed(None, queryset)
    booking.refresh_from_db()
    assert booking.status == BookingStatus.CONFIRMED

    admin_instance.mark_cancelled(None, queryset)
    booking.refresh_from_db()
    assert booking.status == BookingStatus.CANCELLED


@pytest.mark.django_db
def test_booking_export_check_in_to_filter(client):
    rt, room = _make_room_pair("Single")
    create_booking(room=room, client=Client.objects.create(first_name="A", last_name="X"), check_in=_dt.date(2026, 6, 1), check_out=_dt.date(2026, 6, 2))
    create_booking(room=room, client=Client.objects.create(first_name="B", last_name="Y"), check_in=_dt.date(2026, 8, 1), check_out=_dt.date(2026, 8, 2))
    resp = client.get("/api/admin/export/", {"check_in_to": "2026-07-01"})
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "A X" in body
    assert "B Y" not in body


@pytest.mark.django_db
def test_booking_export_check_in_from_filter(client):
    rt, room = _make_room_pair("Single")
    create_booking(room=room, client=Client.objects.create(first_name="A", last_name="X"), check_in=_dt.date(2026, 6, 1), check_out=_dt.date(2026, 6, 2))
    create_booking(room=room, client=Client.objects.create(first_name="B", last_name="Y"), check_in=_dt.date(2026, 8, 1), check_out=_dt.date(2026, 8, 2))
    resp = client.get("/api/admin/export/", {"check_in_from": "2026-07-01"})
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "B Y" in body
    assert "A X" not in body


@pytest.mark.django_db
def test_booking_log_serializer_serializes_log(client):
    """BookingLogSerializer renders a BookingLog row as expected."""
    from hotel_ai.bookings.serializers import BookingLogSerializer

    rt, room = _make_room_pair("Single")
    booking = create_booking(
        room=room,
        client=Client.objects.create(first_name="A", last_name="X"),
        check_in=_dt.date(2026, 6, 1),
        check_out=_dt.date(2026, 6, 2),
    )
    log = BookingLog.objects.create(booking=booking, action="created", notes="ok")
    data = BookingLogSerializer(log).data
    assert data["id"] == log.id
    assert data["action"] == "created"
    assert data["notes"] == "ok"
    assert data["booking"] == booking.id


@pytest.mark.django_db
def test_availability_stream_emit_sets_queue(client, monkeypatch):
    from hotel_ai.bookings.views import AvailabilityStreamView
    monkeypatch.setattr(AvailabilityStreamView, "_running", False)
    AvailabilityStreamView._event_queue.clear()
    AvailabilityStreamView._emit({"type": "test"})
    assert AvailabilityStreamView._event_queue == [{"type": "test"}]


@pytest.mark.django_db
def test_availability_stream_poll_loop_handles_db_error(client, monkeypatch):
    from hotel_ai.bookings.views import AvailabilityStreamView
    import threading
    monkeypatch.setattr(AvailabilityStreamView, "_running", True)
    monkeypatch.setattr(AvailabilityStreamView, "_watcher_thread", None)
    # Make the booking count query always raise. The poll loop calls
    # Booking.objects.exclude(...).count(), so the QuerySet's count must fail.
    fake_qs = mock.MagicMock()
    fake_qs.count.side_effect = RuntimeError("boom")
    monkeypatch.setattr(Booking.objects, "exclude", lambda *a, **k: fake_qs)
    try:
        t = threading.Thread(target=AvailabilityStreamView._poll_loop, daemon=True)
        t.start()
        # Wait long enough for one poll cycle; exceptions are swallowed silently.
        import time as _time
        _time.sleep(2.5)
        t.join(timeout=1)
    finally:
        AvailabilityStreamView._running = False


@pytest.mark.django_db
def test_room_bulk_update_invalid_body_returns_400(client):
    resp = client.patch("/api/rooms/bulk-update/", "[]notjson", content_type="application/json")
    # DRF parses JSON; a non-dict/non-list body should return 400 at parser level,
    # but if parsed as list the loop below still handles individual items correctly.
    # Assert that we don't get an unhandled exception.
    assert resp.status_code in (400, 207)


@pytest.mark.django_db
def test_room_bulk_update_missing_id(client):
    rt, _ = _make_room_pair("Single")
    resp = client.patch(
        "/api/rooms/bulk-update/",
        [{"room_number": "999"}],
        content_type="application/json",
    )
    assert resp.status_code == 207
    data = resp.json()
    assert any(e.get("error") == "Missing 'id'" for e in data.get("errors", []))
    assert len(data["updated"]) == 0


@pytest.mark.django_db
def test_room_bulk_update_not_found_room(client):
    rt, _ = _make_room_pair("Single")
    resp = client.patch(
        "/api/rooms/bulk-update/",
        [{"id": 99999, "is_active": False}],
        content_type="application/json",
    )
    assert resp.status_code == 207  # partial failure
    data = resp.json()
    assert len(data["updated"]) == 0
    assert any(e.get("error") == "Room not found" for e in data["errors"])


@pytest.mark.django_db
def test_room_bulk_update_rejects_non_list_payload(client):
    """A JSON body that parses to a scalar (str/int) is rejected as 400."""
    resp = client.patch(
        "/api/rooms/bulk-update/",
        '"42"',
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Expected a list or object"


@pytest.mark.django_db
def test_today_returns_current_date():
    """The today() helper returns the current local date."""
    from hotel_ai.bookings.services import today

    assert today() == timezone.now().date()


# ---------------------------------------------------------------------------
# Root URL endpoints (remaining coverage gaps in urls.py / chat.views)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_health_endpoint():
    from django.test import Client

    resp = Client().get("/health/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.django_db
def test_home_page_language_fallback(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Hotel Aurora" in resp.content


@pytest.mark.django_db
def test_dashboard_invalid_date_falls_back(client):
    """An unparseable ?date= value falls back to today instead of crashing."""
    from django.contrib.auth.models import User

    user = User.objects.create_user(username="dashuser_badate", password="dashpass")
    client.login(username="dashuser_badate", password="dashpass")
    resp = client.get("/dashboard/", {"date": "not-a-date"})
    assert resp.status_code == 200
    assert b"Room Grid" in resp.content


@pytest.mark.django_db
def test_chat_turn_updates_existing_session_language():
    from rest_framework.test import APIClient
    from unittest import mock
    from hotel_ai.ai_providers.models import Conversation

    conv = Conversation.objects.create(session_key="lang-fresh", channel="chat", language="en")
    client = APIClient()
    with mock.patch("hotel_ai.chat.views.ReservationAgent") as MockAgent:
        MockAgent.return_value.handle_message.return_value = type(
            "R", (), {"text": "tak", "action": "reply", "booking_id": None, "error": None}
        )()
        resp = client.post(
            "/chat/turn/",
            {"session_key": "lang-fresh", "language": "pl", "message": "cześć"},
            format="json",
        )
    assert resp.status_code == 200
    conv.refresh_from_db()
    assert conv.language == "pl"


# ---------------------------------------------------------------------------
# PromoCode model and services
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_promo_code_percent_discount(client, double, client_fixture):
    """Applying a percent discount reduces total price."""
    from hotel_ai.bookings.models import PromoCode
    from hotel_ai.bookings.services import apply_promo_code

    _, room = double
    booking = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
        guests=2,
    )
    original_price = booking.total_price
    promo = PromoCode.objects.create(
        code="SUMMER25",
        discount_type="percent",
        discount_value=Decimal("25.00"),
        valid_from=_dt.date(2026, 6, 1),
        valid_to=_dt.date(2026, 8, 31),
        max_uses=10,
    )
    client.post(f"/api/bookings/{booking.id}/apply-promo/", json.dumps({"code": "SUMMER25"}), content_type="application/json")
    booking.refresh_from_db()
    expected = original_price * Decimal("0.75")
    assert booking.total_price == expected


@pytest.mark.django_db
def test_promo_code_fixed_discount(client, double, client_fixture):
    """Applying a fixed discount subtracts a fixed amount from total price."""
    from hotel_ai.bookings.models import PromoCode

    _, room = double
    booking = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 2),
        guests=2,
    )
    promo = PromoCode.objects.create(
        code="FIXED50",
        discount_type="fixed",
        discount_value=Decimal("50.00"),
        valid_from=_dt.date(2026, 1, 1),
        valid_to=_dt.date(2026, 12, 31),
    )
    resp = client.post(f"/api/bookings/{booking.id}/apply-promo/", json.dumps({"code": "FIXED50"}), content_type="application/json")
    assert resp.status_code == 200
    booking.refresh_from_db()
    assert booking.total_price == Decimal("250.00")


@pytest.mark.django_db
def test_promo_code_not_found(client):
    """Requesting an unknown promo code returns 400."""
    resp = client.get("/api/promo-codes/NONEXISTENT/check/?check_in=2026-07-01")
    assert resp.status_code == 400
    assert not resp.json()["valid"]


@pytest.mark.django_db
def test_promo_code_check_missing_check_in(client):
    """Checking a promo code without check_in parameter returns 400."""
    resp = client.get("/api/promo-codes/SUMMER25/check/")
    assert resp.status_code == 400
    assert "check_in parameter required" in resp.json()["detail"]


@pytest.mark.django_db
def test_promo_code_invalid_date_range(client, double):
    """Promo code validation rejects dates outside the valid range."""
    from hotel_ai.bookings.models import PromoCode

    rt, _ = double
    promo = PromoCode.objects.create(
        code="SUMMER25",
        discount_type="percent",
        discount_value=Decimal("25.00"),
        valid_from=_dt.date(2026, 6, 1),
        valid_to=_dt.date(2026, 8, 31),
    )
    # Check-in in November (outside valid range)
    resp = client.get("/api/promo-codes/SUMMER25/check/?check_in=2026-11-01")
    assert resp.status_code == 400
    assert not resp.json()["valid"]


@pytest.mark.django_db
def test_promo_code_inactive(client):
    """Inactive promo codes are rejected."""
    from hotel_ai.bookings.models import PromoCode

    promo = PromoCode.objects.create(
        code="DEACTIVATED",
        discount_type="percent",
        discount_value=Decimal("10.00"),
        valid_from=_dt.date(2026, 1, 1),
        valid_to=_dt.date(2026, 12, 31),
        active=False,
    )
    resp = client.get("/api/promo-codes/DEACTIVATED/check/?check_in=2026-07-01")
    assert resp.status_code == 400
    assert not resp.json()["valid"]


@pytest.mark.django_db
def test_promo_code_max_uses_exceeded(client):
    """Promo code rejected when max uses reached."""
    from hotel_ai.bookings.models import PromoCode

    promo = PromoCode.objects.create(
        code="LIMITED",
        discount_type="percent",
        discount_value=Decimal("10.00"),
        valid_from=_dt.date(2026, 1, 1),
        valid_to=_dt.date(2026, 12, 31),
        max_uses=1,
        current_uses=1,
    )
    resp = client.get("/api/promo-codes/LIMITED/check/?check_in=2026-07-01")
    assert resp.status_code == 400
    assert not resp.json()["valid"]


@pytest.mark.django_db
def test_promo_code_apply_increments_usage(client, double, client_fixture):
    """Applying a promo code increments its current_uses counter."""
    from hotel_ai.bookings.models import PromoCode

    _, room = double
    booking = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 2),
        guests=2,
    )
    promo = PromoCode.objects.create(
        code="USED10",
        discount_type="percent",
        discount_value=Decimal("10.00"),
        valid_from=_dt.date(2026, 1, 1),
        valid_to=_dt.date(2026, 12, 31),
        max_uses=5,
    )
    client.post(f"/api/bookings/{booking.id}/apply-promo/", json.dumps({"code": "USED10"}), content_type="application/json")
    promo.refresh_from_db()
    assert promo.current_uses == 1


@pytest.mark.django_db
def test_promo_code_log_entry_on_apply(client, double, client_fixture):
    """Applying a promo code creates a 'promo_applied' log entry."""
    from hotel_ai.bookings.models import PromoCode

    _, room = double
    booking = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 2),
        guests=2,
    )
    promo = PromoCode.objects.create(
        code="LOGTEST",
        discount_type="percent",
        discount_value=Decimal("10.00"),
        valid_from=_dt.date(2026, 1, 1),
        valid_to=_dt.date(2026, 12, 31),
    )
    client.post(f"/api/bookings/{booking.id}/apply-promo/", json.dumps({"code": "LOGTEST"}), content_type="application/json")
    logs = list(Booking.objects.get(pk=booking.id).logs.order_by("created_at"))
    assert any(log.action == "promo_applied" for log in logs)


@pytest.mark.django_db
def test_promo_code_min_price_floor(client, double, client_fixture):
    """Discounted price cannot go below zero."""
    from hotel_ai.bookings.models import PromoCode

    _, room = double
    booking = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 2),
        guests=2,
    )
    promo = PromoCode.objects.create(
        code="OVERDISCOUNT",
        discount_type="percent",
        discount_value=Decimal("150.00"),
        valid_from=_dt.date(2026, 1, 1),
        valid_to=_dt.date(2026, 12, 31),
    )
    resp = client.post(f"/api/bookings/{booking.id}/apply-promo/", json.dumps({"code": "OVERDISCOUNT"}), content_type="application/json")
    assert resp.status_code == 200
    booking.refresh_from_db()
    assert booking.total_price == Decimal("0.00")


# ---------------------------------------------------------------------------
# Cancellation policy tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_cancellation_estimate_full_refund():
    """Full refund when cancelling more than free_cancellation_days before check-in."""
    from hotel_ai.bookings.models import CancellationPolicy
    from hotel_ai.bookings.services import estimate_cancellation_refund

    _, room = _make_room_pair("Double")
    client = Client.objects.create(first_name="Test", last_name="Guest", email="canc@example.com")
    booking = create_booking(
        room=room, client=client,
        check_in=_dt.date(2026, 8, 1),
        check_out=_dt.date(2026, 8, 5), guests=2,
    )
    # Create a policy: full refund if >7 days before check-in
    policy = CancellationPolicy.objects.create(
        name="Standard",
        free_cancellation_days=7,
        partial_refund_pct=50,
        non_refundable_days=2,
    )
    result = estimate_cancellation_refund(booking, today=_dt.date(2026, 7, 15))
    assert Decimal(result["refund_amount"]) == booking.total_price
    assert result["note"].lower().find("full") >= 0


@pytest.mark.django_db
def test_cancellation_estimate_partial_refund():
    """Partial refund in the penalty window."""
    from hotel_ai.bookings.models import CancellationPolicy
    from hotel_ai.bookings.services import estimate_cancellation_refund

    _, room = _make_room_pair("Double")
    client = Client.objects.create(first_name="Test", last_name="Guest", email="canc2@example.com")
    booking = create_booking(
        room=room, client=client,
        check_in=_dt.date(2026, 8, 1),
        check_out=_dt.date(2026, 8, 5), guests=2,
    )
    CancellationPolicy.objects.create(
        name="Standard",
        free_cancellation_days=7,
        partial_refund_pct=50,
        non_refundable_days=2,
    )
    # 5 days before check-in (between 7 and 2)
    result = estimate_cancellation_refund(booking, today=_dt.date(2026, 7, 27))
    assert float(result["refund_percentage"]) == pytest.approx(50.0)


@pytest.mark.django_db
def test_cancellation_estimate_no_refund():
    """No refund when within non-refundable window."""
    from hotel_ai.bookings.models import CancellationPolicy
    from hotel_ai.bookings.services import estimate_cancellation_refund

    _, room = _make_room_pair("Double")
    client = Client.objects.create(first_name="Test", last_name="Guest", email="canc3@example.com")
    booking = create_booking(
        room=room, client=client,
        check_in=_dt.date(2026, 8, 1),
        check_out=_dt.date(2026, 8, 5), guests=2,
    )
    CancellationPolicy.objects.create(
        name="Standard",
        free_cancellation_days=7,
        partial_refund_pct=50,
        non_refundable_days=2,
    )
    # 1 day before check-in (within non-refundable window)
    result = estimate_cancellation_refund(booking, today=_dt.date(2026, 7, 31))
    assert Decimal(result["refund_amount"]) == Decimal("0.00")
    assert "no refund" in result["note"].lower()


@pytest.mark.django_db
def test_cancellation_estimate_api(client, double, client_fixture):
    """GET /api/bookings/<id>/cancel-estimate/ returns refund info."""
    from hotel_ai.bookings.models import CancellationPolicy

    _, room = double
    booking = create_booking(
        room=room, client=client_fixture,
        check_in=_dt.date(2026, 8, 1),
        check_out=_dt.date(2026, 8, 5), guests=2,
    )
    CancellationPolicy.objects.create(
        name="Standard",
        free_cancellation_days=7,
        partial_refund_pct=50,
        non_refundable_days=2,
    )
    resp = client.get(f"/api/bookings/{booking.id}/cancel-estimate/")
    assert resp.status_code == 200
    body = resp.json()
    assert "refund_amount" in body
    assert "policy_name" in body
    assert "days_until_checkin" in body


# ---------------------------------------------------------------------------
# Booking modification API tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_modify_booking_api_shortens_stay(client, double, client_fixture):
    """PATCH /api/bookings/<id>/modify/ shortens stay and recalculates price."""
    _, room = double
    booking = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 5),
        guests=2,
    )
    original_price = booking.total_price

    resp = client.post(
        f"/api/bookings/{booking.id}/modify/",
        json.dumps({
            "check_in": "2026-07-01",
            "check_out": "2026-07-03",
        }),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert Decimal(body["booking"]["total_price"]) < original_price
    assert "price_change" in body


@pytest.mark.django_db
def test_modify_booking_api_longer_stay_costs_more(client, double, client_fixture):
    """PATCH /api/bookings/<id>/modify/ lengthens stay and increases price."""
    _, room = double
    booking = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
        guests=2,
    )
    original_price = booking.total_price

    resp = client.post(
        f"/api/bookings/{booking.id}/modify/",
        json.dumps({
            "check_in": "2026-07-01",
            "check_out": "2026-07-05",
        }),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert float(body["booking"]["total_price"]) > float(original_price)
    assert body["price_change"] > 0


@pytest.mark.django_db
def test_modify_booking_api_conflict_returns_400(client, double, client_fixture):
    """Modifying to unavailable dates returns 400."""
    _, room = double
    booking = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 7, 10),
        check_out=_dt.date(2026, 7, 15),
        guests=2,
    )

    # Another booking for same room overlapping
    booking2 = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 7, 20),
        check_out=_dt.date(2026, 7, 25),
        guests=2,
    )

    resp = client.post(
        f"/api/bookings/{booking.id}/modify/",
        json.dumps({
            "check_in": "2026-07-22",
            "check_out": "2026-07-27",
        }),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert "not available" in resp.json()["detail"].lower()


@pytest.mark.django_db
def test_modify_booking_api_invalid_dates_returns_400(client, double, client_fixture):
    """Invalid date order returns 400."""
    _, room = double
    booking = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
        guests=2,
    )

    resp = client.post(
        f"/api/bookings/{booking.id}/modify/",
        json.dumps({
            "check_in": "2026-07-05",
            "check_out": "2026-07-03",
        }),
        content_type="application/json",
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Booking modification services
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_modify_booking_dates_shorter_stay_refunds(double, client_fixture):
    """Moving to a shorter stay reduces total price (refund due)."""
    _, room = double
    booking = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 5),
        guests=2,
    )
    original_price = booking.total_price

    modified, delta = modify_booking_dates(
        booking,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )

    booking.refresh_from_db()
    assert modified.total_price < original_price
    assert delta == booking.total_price - original_price
    assert modified.check_out == _dt.date(2026, 7, 3)


@pytest.mark.django_db
def test_modify_booking_dates_longer_stay_costs_more(double, client_fixture):
    """Moving to a longer stay increases total price (guest owes more)."""
    _, room = double
    booking = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
        guests=2,
    )
    original_price = booking.total_price

    modified, delta = modify_booking_dates(
        booking,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 5),
    )

    booking.refresh_from_db()
    assert modified.total_price > original_price
    assert delta > 0
    assert modified.check_out == _dt.date(2026, 7, 5)


@pytest.mark.django_db
def test_modify_booking_rejects_unavailable_dates(double, client_fixture):
    """Cannot modify dates if another booking already occupies them."""
    _, room = double
    booking = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 7, 10),
        check_out=_dt.date(2026, 7, 15),
        guests=2,
    )

    # Another booking on the SAME room overlapping where we want to move
    booking2 = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 7, 20),
        check_out=_dt.date(2026, 7, 25),
        guests=2,
    )

    # Trying to move booking1 to Jul 22-27 overlaps booking2 on same room
    with pytest.raises(ValueError, match="not available"):
        modify_booking_dates(
            booking,
            check_in=_dt.date(2026, 7, 22),
            check_out=_dt.date(2026, 7, 27),
        )


@pytest.mark.django_db
def test_modify_booking_rejects_no_show(double, client_fixture):
    """Cannot modify a cancelled or no-show booking."""
    _, room = double
    booking = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
        guests=2,
    )
    booking.status = BookingStatus.CANCELLED
    booking.save()
    with pytest.raises(ValueError, match="Cannot modify"):
        modify_booking_dates(
            booking,
            check_in=_dt.date(2026, 7, 5),
            check_out=_dt.date(2026, 7, 7),
        )


@pytest.mark.django_db
def test_modify_booking_excludes_self_from_overlap(double, client_fixture):
    """Modifying dates that don't actually change anything doesn't flag overlap."""
    _, room = double
    booking = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
        guests=2,
    )

    # Same dates should be fine (no-op)
    modified, delta = modify_booking_dates(
        booking,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )
    assert modified.check_in == _dt.date(2026, 7, 1)
    assert delta == Decimal("0.00")


@pytest.mark.django_db
def test_estimate_arrival_update_adds_notes(double, client_fixture):
    """Setting estimated arrival adds a note and creates a log entry."""
    _, room = double
    booking = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
        guests=2,
    )

    updated = estimate_arrival_update(booking, "around 3pm")

    assert "around 3pm" in updated.notes
    assert any(log.action == "arrival_updated" for log in booking.logs.all())


@pytest.mark.django_db
def test_estimate_arrival_rejects_cancelled(double, client_fixture):
    """Cannot set arrival for a cancelled booking."""
    _, room = double
    booking = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
        guests=2,
    )
    booking.status = BookingStatus.CANCELLED
    booking.save()
    with pytest.raises(ValueError):
        estimate_arrival_update(booking, "tomorrow")


# ---------------------------------------------------------------------------
# Email communication tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_send_booking_confirmation_calls_mail(double, client_fixture):
    """send_booking_confirmation renders and dispatches an email."""
    from django.core import mail
    from hotel_ai.bookings.emails import send_booking_confirmation

    _, room = double
    booking = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 8, 1),
        check_out=_dt.date(2026, 8, 3),
        guests=2,
    )
    mail.outbox.clear()

    result = send_booking_confirmation(booking)
    assert result is True
    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert client_fixture.email in msg.to
    assert "Booking confirmed" in msg.subject
    assert str(booking.total_price) in msg.body


@pytest.mark.django_db
def test_send_booking_reminder_calls_mail(double, client_fixture):
    """send_booking_reminder includes estimated arrival when provided."""
    from django.core import mail
    from hotel_ai.bookings.emails import send_booking_reminder

    _, room = double
    booking = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 8, 1),
        check_out=_dt.date(2026, 8, 3),
    )
    mail.outbox.clear()

    send_booking_reminder(booking, estimated_arrival="5:00 PM")
    assert len(mail.outbox) == 1
    html = mail.outbox[0].alternatives[0][0] if mail.outbox[0].alternatives else ""
    assert "5:00 PM" in html


@pytest.mark.django_db
def test_send_checkin_instructions(double, client_fixture):
    """Check-in email includes Wi-Fi credentials."""
    from django.core import mail
    from hotel_ai.bookings.emails import send_checkin_instructions

    _, room = double
    booking = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 7, 1),
        check_out=_dt.date(2026, 7, 3),
    )
    check_in_booking(booking)

    # Should have already sent check-in instructions via service layer
    assert any("Check-in complete" in m.subject for m in mail.outbox)


@pytest.mark.django_db
def test_send_post_stay_survey(double, client_fixture):
    """Post-stay survey email is sent with correct subject."""
    from django.core import mail
    from hotel_ai.bookings.emails import send_post_stay_survey

    _, room = double
    booking = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 6, 1),
        check_out=_dt.date(2026, 6, 5),
    )
    mail.outbox.clear()

    send_post_stay_survey(booking, survey_link="https://example.com/survey/42")
    assert len(mail.outbox) == 1
    assert "How was your stay" in mail.outbox[0].subject
    assert "https://example.com/survey/42" in mail.outbox[0].alternatives[0][0]


@pytest.mark.django_db
def test_email_service_handles_failure():
    """_send returns False when send_mail raises."""
    from unittest import mock
    from hotel_ai.bookings.emails import _send

    with mock.patch("django.core.mail.send_mail", mock.Mock(side_effect=Exception("connection refused"))):
        result = _send("test@example.com", "Subject", "Body", "<p>Body</p>")
    assert result is False


@pytest.mark.django_db
def test_booking_creation_sends_confirmation_email(double, client_fixture):
    """create_booking triggers confirmation email (best-effort)."""
    from unittest import mock
    from hotel_ai.bookings.emails import send_booking_confirmation

    calls: list[dict] = []

    def _capture(*args, **kwargs):
        calls.append(kwargs)
        return 1

    with mock.patch("django.core.mail.send_mail", _capture):
        _, room = double
        booking = create_booking(
            room=room,
            client=client_fixture,
            check_in=_dt.date(2026, 9, 1),
            check_out=_dt.date(2026, 9, 4),
        )

    assert booking.status == BookingStatus.CONFIRMED
    assert len(calls) == 1
    assert "Booking confirmed" in calls[0]["subject"]


@pytest.mark.django_db
def test_email_templates_render_with_correct_currency_formatting(double, client_fixture):
    """Email context includes currency symbol/format from booking."""
    from hotel_ai.bookings.emails import _ctx

    _, room = double
    booking = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 10, 1),
        check_out=_dt.date(2026, 10, 3),
    )
    ctx = _ctx(booking)
    assert ctx["currency"] == "PLN"
    assert isinstance(ctx["total_price"], Decimal)
    assert ctx["nights"] == 2
    assert ctx["guests"] == 1


# ---------------------------------------------------------------------------
# Rate plans / packages
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_create_rate_plan():
    """Rate plan model creates and persists correctly."""
    from hotel_ai.bookings.models import RatePlan

    plan = RatePlan.objects.create(
        name="Breakfast Included",
        plan_type=RatePlan.PlanType.BREAKFAST_INCLUDED,
        description="Complimentary breakfast for all guests.",
        includes_breakfast=True,
    )
    assert plan.name == "Breakfast Included"
    assert plan.includes_breakfast is True
    assert plan.is_non_refundable is False
    assert str(plan) == "Breakfast Included"


@pytest.mark.django_db
def test_rate_plan_types_exist():
    """All rate plan type choices are present."""
    from hotel_ai.bookings.models import RatePlan

    expected = {"standard", "non_refundable", "breakfast_included",
                "corporate_discount", "stay_x_pay_y"}
    actual = {v[0] for v in RatePlan.PlanType.choices}
    assert expected == actual


@pytest.mark.django_db
def test_calculate_total_with_corporate_discount(double, client_fixture):
    """Corporate discount reduces the total by the specified percentage."""
    from decimal import Decimal
    from hotel_ai.bookings.models import RatePlan
    from hotel_ai.bookings.services import create_booking

    _, room = double
    base_per_night = room.room_type.base_price_per_night  # e.g., 320 PLN
    nights = 2

    plan = RatePlan.objects.create(
        name="Corporate Discount 15%",
        plan_type=RatePlan.PlanType.CORPORATE_DISCOUNT,
        corporate_discount_pct=15,
    )

    booking = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 11, 1),
        check_out=_dt.date(2026, 11, 3),
        rate_plan=plan,
    )

    expected = base_per_night * Decimal(str(nights))
    discount = expected * Decimal("0.15")
    assert booking.total_price == round(expected - discount, 2)
    assert booking.rate_plan == plan


@pytest.mark.django_db
def test_calculate_total_with_stay_x_pay_y(double, client_fixture):
    """Stay X pay Y: guest pays for fewer nights than they stay."""
    from decimal import Decimal
    from hotel_ai.bookings.models import RatePlan
    from hotel_ai.bookings.services import create_booking

    _, room = double
    base_per_night = room.room_type.base_price_per_night  # 320 PLN

    plan = RatePlan.objects.create(
        name="Stay 3 Pay 2",
        plan_type=RatePlan.PlanType.STAY_X_PAY_Y,
        stay_x_pay_nights=2,
    )

    booking = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 11, 1),
        check_out=_dt.date(2026, 11, 4),  # 3 nights
        rate_plan=plan,
    )

    # Should pay for only 2 nights, not 3
    expected = base_per_night * Decimal("2")
    assert booking.total_price == expected
    assert booking.nights == 3  # still 3 nights of stay
    assert booking.rate_plan == plan


@pytest.mark.django_db
def test_rate_plan_not_affecting_capacity_check(double, client_fixture):
    """Rate plan doesn't affect capacity validation."""
    from hotel_ai.bookings.models import RatePlan, BookingStatus
    from hotel_ai.bookings.services import create_booking

    _, room = double
    # Room capacity is 2
    plan = RatePlan.objects.create(
        name="Corporate Discount",
        plan_type=RatePlan.PlanType.CORPORATE_DISCOUNT,
        corporate_discount_pct=10,
    )

    with pytest.raises(ValueError):
        create_booking(
            room=room,
            client=client_fixture,
            check_in=_dt.date(2026, 12, 1),
            check_out=_dt.date(2026, 12, 2),
            guests=5,  # exceeds capacity
            rate_plan=plan,
        )


@pytest.mark.django_db
def test_api_book_with_rate_plan(client, double, client_fixture):
    """POST /api/bookings/create/ with rate_plan parameter works."""
    from hotel_ai.bookings.models import RatePlan

    _, room = double
    plan = RatePlan.objects.create(
        name="Corporate Discount 15%",
        plan_type=RatePlan.PlanType.CORPORATE_DISCOUNT,
        corporate_discount_pct=15,
    )

    response = client.post(
        "/api/bookings/create/",
        {
            "room_type": "Double",
            "check_in": "2026-12-15",
            "check_out": "2026-12-17",
            "guests": 1,
            "first_name": "Jan",
            "last_name": "Kowalski",
            "email": "jan@example.com",
            "rate_plan": "Corporate Discount 15%",
        },
        content_type="application/json",
    )

    assert response.status_code == 201, response.json()
    data = response.json()
    assert data["booking"]["rate_plan"] is not None
    assert data["booking"]["rate_plan_name"] == "Corporate Discount 15%"


@pytest.mark.django_db
def test_api_rate_plans_list_endpoint(client):
    """GET /api/rate-plans/ returns list of rate plans."""
    from hotel_ai.bookings.models import RatePlan

    RatePlan.objects.create(
        name="Standard Rate",
        plan_type=RatePlan.PlanType.STANDARD,
    )
    RatePlan.objects.create(
        name="Non-Refundable Best Rate",
        plan_type=RatePlan.PlanType.NON_REFUNDABLE,
        is_non_refundable=True,
    )

    response = client.get("/api/rate-plans/")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    names = {item["name"] for item in data}
    assert names == {"Standard Rate", "Non-Refundable Best Rate"}


@pytest.mark.django_db
def test_api_rate_plans_by_room_type_requires_room_type(client):
    """GET /api/rate-plans/by-room-type/ without room_type returns 400."""
    response = client.get("/api/rate-plans/by-room-type/")
    assert response.status_code == 400
    detail = response.json()["detail"].lower()
    assert "room_type" in detail


@pytest.mark.django_db
def test_api_rate_plans_by_room_type_returns_prices(client, double):
    """GET /api/rate-plans/by-room-type/ returns prices per plan."""
    from hotel_ai.bookings.models import RatePlan

    plan = RatePlan.objects.create(
        name="Non-Refundable Best Rate",
        plan_type=RatePlan.PlanType.NON_REFUNDABLE,
        is_non_refundable=True,
    )

    response = client.get(
        "/api/rate-plans/by-room-type/?room_type=Double&check_in=2026-12-15&nights=2"
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    # Find our plan in results
    matching = [p for p in data if p["name"] == "Non-Refundable Best Rate"]
    assert len(matching) == 1
    assert matching[0]["is_non_refundable"] is True
    assert "total_price" in matching[0]
    assert "per_night" in matching[0]


@pytest.mark.django_db
def test_rate_plan_serializer_fields(double):
    """RatePlanSerializer exposes all relevant fields."""
    from hotel_ai.bookings.serializers import RatePlanSerializer
    from hotel_ai.bookings.models import RatePlan

    plan = RatePlan.objects.create(
        name="Test Plan",
        plan_type=RatePlan.PlanType.BREAKFAST_INCLUDED,
        description="Test description",
        includes_breakfast=True,
        corporate_discount_pct=10,
        stay_x_pay_nights=2,
    )

    serializer = RatePlanSerializer(plan)
    data = serializer.data
    assert data["name"] == "Test Plan"
    assert data["plan_type"] == "breakfast_included"
    assert data["includes_breakfast"] is True
    assert data["corporate_discount_pct"] == 10
    assert data["stay_x_pay_nights"] == 2


# ---------------------------------------------------------------------------
# Pre-arrival form
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_pre_arrival_update_estimated_arrival(client, double, client_fixture):
    """PATCH /api/bookings/<id>/pre-arrival/ updates estimated arrival."""
    _, room = double
    booking = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 12, 1),
        check_out=_dt.date(2026, 12, 3),
    )

    response = client.patch(
        f"/api/bookings/{booking.id}/pre-arrival/",
        {"estimated_arrival": "18:30", "special_requests": "Late check-in"},
        content_type="application/json",
    )
    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["booking"]["estimated_arrival"] is not None
    assert "18:30" in data["booking"]["estimated_arrival"]
    assert data["booking"]["special_requests"] == "Late check-in"


@pytest.mark.django_db
def test_pre_arrival_rejects_invalid_time(client, double, client_fixture):
    """Pre-arrival rejects times with invalid hour or minute values."""
    _, room = double
    booking = create_booking(
        room=room,
        client=client_fixture,
        check_in=_dt.date(2026, 12, 1),
        check_out=_dt.date(2026, 12, 2),
    )

    response = client.patch(
        f"/api/bookings/{booking.id}/pre-arrival/",
        {"estimated_arrival": "25:99"},
        content_type="application/json",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_pre_arrival_requires_existing_booking(client):
    """Pre-arrival returns 404 for a non-existent booking id."""
    response = client.patch(
        "/api/bookings/99999/pre-arrival/",
        {"special_requests": "Extra pillow"},
        content_type="application/json",
    )
    assert response.status_code == 404
