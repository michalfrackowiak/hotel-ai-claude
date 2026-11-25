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

from hotel_ai.bookings.models import (
    Booking,
    BookingLog,
    BookingStatus,
    Client,
    GroupBooking,
    LoyaltyPoint,
    Notification,
    PricingRule,
    RatePlan,
    Room,
    RoomType,
)
from hotel_ai.bookings.services import (
    availability_summary,
    calculate_total,
    cancel_booking,
    check_in_booking,
    check_out_booking,
    create_booking,
    create_group_booking,
    estimate_arrival_update,
    find_available_rooms,
    modify_booking_dates,
    no_show_booking,
    overlapping_bookings,
    parse_date,
    resolve_client,
    add_waitlist_entry,
    cancel_waitlist_entry,
    mark_waitlist_as_booked,
    check_and_notify_waitlist,
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
# Booking API tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_create_booking(client, single):
    """POST /api/bookings/ creates a booking."""
    rt, room = single
    payload = {
        "room_type": rt.name,
        "room_number": room.room_number,
        "check_in": "2026-07-01",
        "check_out": "2026-07-03",
        "guests": 2,
        "first_name": "Test",
        "last_name": "Guest",
        "email": "test@example.com",
    }
    resp = client.post("/api/bookings/create/", json.dumps(payload), content_type="application/json")
    assert resp.status_code == 201
    data = resp.json()
    assert data["booking"]["status"] == "confirmed"


# ... (other tests continue)


@pytest.mark.django_db
def test_group_booking_api_create(client, double):
    """POST /api/group-bookings/ creates a group booking."""
    # Create a client for this test (fixture may not be available when run in isolation)
    client_fixture = Client.objects.create(first_name="Test", last_name="Guest", email="test@example.com")
    rt = double[0]
    room2 = Room.objects.create(room_number="TEST-GAPI-102", room_type=rt, floor=1)

    response = client.post("/api/group-bookings/create/", {
        "client_id": client_fixture.id,
        "name": "API Group",
        "room_ids": [double[1].id, room2.id],
        "check_in": "2026-11-01",
        "check_out": "2026-11-03",
        "guests": 2,
    })
    assert response.status_code == 201
    data = response.json()
    assert data["message"] == "Group booking 'API Group' created with 2 rooms."
    assert data["group"]["total_rooms"] == 2
    assert len(data["bookings"]) == 2


@pytest.mark.django_db
def test_group_booking_api_list(client, double, client_fixture):
    """GET /api/group-bookings/?client=<id> lists groups."""
    rt = double[0]
    room2 = Room.objects.create(room_number="TEST-GLIST-102", room_type=rt, floor=1)
    group, _ = create_group_booking(
        client=client_fixture,
        name="List Test",
        rooms=[double[1], room2],
        check_in=_dt.date(2026, 10, 1),
        check_out=_dt.date(2026, 10, 3),
    )

    response = client.get(f"/api/group-bookings/?client={client_fixture.id}")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == group.id


@pytest.mark.django_db
def test_group_booking_detail_api(client, double, client_fixture):
    """GET /api/group-bookings/<pk>/ returns group detail."""
    group, _ = create_group_booking(
        client=client_fixture,
        name="Detail Test",
        rooms=[double[1]],
        check_in=_dt.date(2026, 10, 1),
        check_out=_dt.date(2026, 10, 2),
    )

    response = client.get(f"/api/group-bookings/{group.id}/")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Detail Test"
    assert data["total_rooms"] == 1
    assert len(data["bookings"]) == 1


@pytest.mark.django_db
def test_group_booking_api_missing_client_param(client):
    response = client.get("/api/group-bookings/")
    assert response.status_code == 400
    assert "client parameter required" in response.json()["detail"]


@pytest.mark.django_db
def test_group_booking_api_not_found(client):
    response = client.get("/api/group-bookings/99999/")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Waitlist API tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_waitlist_create(client, double):
    """POST /api/waitlist/ creates a waitlist entry."""
    rt = double[0]
    created_client = Client.objects.create(first_name="Wait", last_name="List", email="waitlist@test.com")
    response = client.post("/api/waitlist/", {
        "client_id": created_client.id,
        "room_type_id": rt.id,
        "check_in": "2026-12-01",
        "check_out": "2026-12-03",
        "guests": 1,
        "notes": "Want sea view",
    })
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "waiting"
    assert data["room_type_name"] == "Double"
    assert data["guests"] == 1


@pytest.mark.django_db
def test_waitlist_list(client, double, client_fixture):
    """GET /api/waitlist/list/?client=<id> lists entries."""
    rt = double[0]
    entry = add_waitlist_entry(
        client=client_fixture,
        room_type_id=rt.id,
        check_in=_dt.date(2026, 12, 1),
        check_out=_dt.date(2026, 12, 3),
    )
    response = client.get(f"/api/waitlist/list/?client={client_fixture.id}")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == entry.id


@pytest.mark.django_db
def test_waitlist_detail(client, double, client_fixture):
    """GET /api/waitlist/<pk>/ returns entry detail."""
    rt = double[0]
    entry = add_waitlist_entry(
        client=client_fixture,
        room_type_id=rt.id,
        check_in=_dt.date(2026, 12, 1),
        check_out=_dt.date(2026, 12, 2),
    )
    response = client.get(f"/api/waitlist/{entry.id}/")
    assert response.status_code == 200
    data = response.json()
    assert data["client_name"] == "Test Guest"
    assert data["notes"] == ""


@pytest.mark.django_db
def test_waitlist_cancel(client, double, client_fixture):
    """POST /api/waitlist/<pk>/ with action=cancel marks entry cancelled."""
    rt = double[0]
    entry = add_waitlist_entry(
        client=client_fixture,
        room_type_id=rt.id,
        check_in=_dt.date(2026, 12, 1),
        check_out=_dt.date(2026, 12, 2),
    )
    response = client.post(f"/api/waitlist/{entry.id}/", {"action": "cancel"})
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Waitlist entry cancelled."
    entry.refresh_from_db()
    assert entry.status == "cancelled"


@pytest.mark.django_db
def test_waitlist_mark_booked(client, double, client_fixture):
    """POST /api/waitlist/<pk>/ with action=book on notified entry."""
    rt = double[0]
    entry = add_waitlist_entry(
        client=client_fixture,
        room_type_id=rt.id,
        check_in=_dt.date(2026, 12, 1),
        check_out=_dt.date(2026, 12, 2),
    )
    # Manually set status to notified first
    entry.status = "notified"
    entry.notified_at = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
    entry.save(update_fields=["status", "notified_at"])

    response = client.post(f"/api/waitlist/{entry.id}/", {"action": "book"})
    assert response.status_code == 200
    entry.refresh_from_db()
    assert entry.status == "booked"


@pytest.mark.django_db
def test_waitlist_notify_check_double_available(client, double):
    """check_and_notify_waitlist sends notification when room becomes available."""
    rt, room = double
    created_client = Client.objects.create(first_name="Notify", last_name="Me", email="notify@test.com")
    entry = add_waitlist_entry(
        client=created_client,
        room_type_id=rt.id,
        check_in=_dt.date(2026, 12, 1),
        check_out=_dt.date(2026, 12, 2),
    )
    notified = check_and_notify_waitlist()
    assert entry.id in notified
    entry.refresh_from_db()
    assert entry.status == "notified"
    assert entry.notified_at is not None
    assert Notification.objects.filter(recipient=created_client, type="reminder").exists()


@pytest.mark.django_db
def test_waitlist_api_missing_client_param(client):
    response = client.get("/api/waitlist/list/")
    assert response.status_code == 400
    assert "client parameter required" in response.json()["detail"]


@pytest.mark.django_db
def test_waitlist_api_not_found(client):
    response = client.get("/api/waitlist/99999/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_waitlist_invalid_dates(client):
    created_client = Client.objects.create(first_name="X", last_name="Y", email="xy@test.com")
    response = client.post("/api/waitlist/", {
        "client_id": created_client.id,
        "room_type_id": 999,
        "check_in": "2026-12-05",
        "check_out": "2026-12-01",
    })
    assert response.status_code == 400