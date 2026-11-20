"""Tests for the bookings seed command."""
from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from unittest import mock

import pytest

from django.contrib.auth import get_user_model
from django.core.management import call_command

from hotel_ai.bookings.models import (
    Booking,
    Client,
    PricingRule,
    Room,
    RoomType,
)


@pytest.mark.django_db
def test_seed_data_creates_room_types():
    call_command("seed_data")
    names = set(RoomType.objects.values_list("name", flat=True))
    assert {"Single", "Double", "Suite"}.issubset(names)


@pytest.mark.django_db
def test_seed_data_creates_rooms_for_each_type():
    call_command("seed_data")
    assert Room.objects.filter(room_number__startswith="101").exists()
    assert Room.objects.filter(room_number__startswith="201").exists()
    assert Room.objects.filter(room_number__startswith="301").exists()


@pytest.mark.django_db
def test_seed_data_creates_pricing_rules():
    call_command("seed_data")
    names = set(PricingRule.objects.values_list("name", flat=True))
    assert "Summer high season" in names
    assert "Weekend special" in names


@pytest.mark.django_db
def test_seed_data_creates_demo_booking():
    call_command("seed_data")
    assert Booking.objects.exists()
    booking = Booking.objects.first()
    assert booking.source == "seed"
    assert booking.guests == 2


@pytest.mark.django_db
def test_seed_data_creates_superuser():
    User = get_user_model()
    User.objects.filter(username="admin").delete()
    call_command("seed_data")
    assert User.objects.filter(username="admin", is_superuser=True).exists()


@pytest.mark.django_db
def test_seed_data_is_idempotent():
    call_command("seed_data")
    first_count = RoomType.objects.count()
    call_command("seed_data")
    assert RoomType.objects.count() == first_count
    # Re-running should not duplicate room numbers.
    assert Room.objects.filter(room_number="101").count() == 1


@pytest.mark.django_db
def test_seed_data_clears_orphans_before_seeding():
    """Pre-existing bookings are wiped so re-runs start clean."""
    single = RoomType.objects.create(name="Single", base_price_per_night=Decimal("200.00"), capacity=1)
    room = Room.objects.create(room_number="999", room_type=single, floor=1)
    client = Client.objects.create(first_name="Orphan", last_name="__seeded", email="orphan@example.com")
    Booking.objects.create(
        room=room,
        client=client,
        check_in=_dt.date.today(),
        check_out=_dt.date.today() + _dt.timedelta(days=1),
        guests=1,
        total_price=Decimal("200.00"),
    )
    call_command("seed_data")
    assert not Booking.objects.filter(room=room).exists()
    assert not Client.objects.filter(last_name__endswith="__seeded").exists()


@pytest.mark.django_db
def test_seed_data_demo_booking_skipped_on_create_failure():
    """If create_booking raises, the demo booking is skipped gracefully."""
    call_command("seed_data")
    Booking.objects.all().delete()
    with mock.patch(
        "hotel_ai.bookings.management.commands.seed_data.create_booking",
        side_effect=RuntimeError("boom"),
    ):
        call_command("seed_data")
    assert not Booking.objects.filter(source="seed").exists()


@pytest.mark.django_db
def test_seed_data_recreates_pricing_rules_on_rerun():
    call_command("seed_data")
    summer = PricingRule.objects.get(name="Summer high season")
    assert summer.price_per_night == Decimal("390.00")
    assert summer.min_nights == 2
    # Re-running wipes and recreates rules; fetch fresh rather than refresh
    # the stale object (its row was deleted and re-inserted with a new pk).
    call_command("seed_data")
    summer = PricingRule.objects.get(name="Summer high season")
    assert summer.price_per_night == Decimal("390.00")


@pytest.mark.django_db
def test_seed_data_writes_progress_to_stdout():
    out = mock.MagicMock()
    call_command("seed_data", stdout=out)
    written = "".join(call.args[0] for call in out.write.call_args_list)
    assert "Seeding room types" in written
    assert "Seed data complete" in written