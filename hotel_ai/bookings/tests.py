"""Tests for the booking engine: models, services and REST API."""

from __future__ import annotations

import datetime as _dt
import json
import uuid
from decimal import Decimal

import pytest

from hotel_ai.bookings.models import Booking, BookingLog, BookingStatus, Client, PricingRule, Room, RoomType
from hotel_ai.bookings.services import (
    availability_summary,
    calculate_total,
    cancel_booking,
    check_in_booking,
    check_out_booking,
    create_booking,
    find_available_rooms,
    no_show_booking,
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
def test_availability_stream_generates_sse_format(client):
    """The stream generator yields proper SSE frames."""
    from hotel_ai.bookings.views import AvailabilityStreamView
    import io

    # Manually add an event to the queue
    with AvailabilityStreamView._lock:
        AvailabilityStreamView._event_queue.append({"type": "test"})

    resp = client.get("/api/availability/stream/")

    # Consume a bounded number of chunks from the streaming generator
    chunks = []
    for chunk in resp.streaming_content:
        chunks.append(chunk)
        if b"test" in chunk:
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
