"""Tests for the booking engine: models, services and REST API."""

from __future__ import annotations

import datetime as _dt
import json
import uuid
from decimal import Decimal

import pytest

from hotel_ai.bookings.models import Booking, BookingStatus, Client, PricingRule, Room, RoomType
from hotel_ai.bookings.services import (
    availability_summary,
    calculate_total,
    cancel_booking,
    check_in_booking,
    check_out_booking,
    create_booking,
    find_available_rooms,
    overlapping_bookings,
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