"""Seed the database with room types, rooms, pricing rules and a demo booking.

Idempotent: re-running it updates existing rows instead of duplicating them.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from hotel_ai.bookings.models import (
    Booking,
    BookingStatus,
    Client,
    PricingRule,
    Room,
    RoomType,
)
from hotel_ai.bookings.services import create_booking, resolve_client


class Command(BaseCommand):
    help = "Seed Hotel-AI-Claude with room types, rooms, pricing rules and a demo booking."

    def handle(self, *args, **options):
        # Clear so re-running is fully idempotent (idempotence of model updates
        # alone misses orphan bookings that reference deleted rooms, etc.).
        Booking.objects.all().delete()
        PricingRule.objects.all().delete()
        Room.objects.all().delete()
        Client.objects.filter(last_name__endswith="__seeded").delete()
        RoomType.objects.all().delete()

        self.stdout.write("Seeding room types...")
        room_types = self._seed_room_types()
        self.stdout.write("Seeding rooms...")
        self._seed_rooms(room_types)
        self.stdout.write("Seeding pricing rules...")
        self._seed_pricing_rules(room_types)
        self.stdout.write("Seeding demo booking...")
        self._seed_demo_booking(room_types)
        self.stdout.write("Ensuring superuser...")
        self._ensure_superuser()
        self.stdout.write(self.style.SUCCESS("Seed data complete."))

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _seed_room_types(self):
        specs = [
            {
                "name": "Single",
                "base_price_per_night": Decimal("220.00"),
                "capacity": 1,
                "amenities": ["Wi-Fi", "TV", "En-suite bathroom"],
            },
            {
                "name": "Double",
                "base_price_per_night": Decimal("320.00"),
                "capacity": 2,
                "amenities": ["Wi-Fi", "TV", "En-suite bathroom", "Mini-bar"],
            },
            {
                "name": "Suite",
                "base_price_per_night": Decimal("550.00"),
                "capacity": 3,
                "amenities": ["Wi-Fi", "TV", "Living area", "En-suite bathroom", "Balcony"],
            },
        ]
        created = {}
        for spec in specs:
            rt, _ = RoomType.objects.get_or_create(
                name=spec["name"], defaults=spec
            )
            created[rt.name] = rt
        return created

    def _seed_rooms(self, room_types):
        """Create 6 rooms per type across 2 floors with unique numbers."""
        # Single: 101-103 / 121-123   Double: 201-203 / 221-223   Suite: 301-303 / 321-323
        mapping = {
            "Single": [
                ("101", 1), ("102", 1), ("103", 1),
                ("121", 2), ("122", 2), ("123", 2),
            ],
            "Double": [
                ("201", 1), ("202", 1), ("203", 1),
                ("221", 2), ("222", 2), ("223", 2),
            ],
            "Suite": [
                ("301", 1), ("302", 1), ("303", 1),
                ("321", 2), ("322", 2), ("323", 2),
            ],
        }
        for type_name, specs in mapping.items():
            rt = room_types.get(type_name)
            if not rt:
                continue
            for number, floor in specs:
                Room.objects.update_or_create(
                    room_number=number,
                    defaults={"room_type": rt, "floor": floor, "is_active": True},
                )

    def _seed_pricing_rules(self, room_types):
        today = _dt.date.today()
        # Summer high-season rule for the Double room type.
        PricingRule.objects.update_or_create(
            name="Summer high season",
            room_type=room_types["Double"],
            defaults={
                "starts_at": today.replace(month=6, day=1),
                "ends_at": today.replace(month=8, day=31),
                "price_per_night": Decimal("390.00"),
                "min_nights": 2,
                "priority": 10,
            },
        )
        # Weekend rule for the Single room type.
        PricingRule.objects.update_or_create(
            name="Weekend special",
            room_type=room_types["Single"],
            defaults={
                "starts_at": today.replace(month=12, day=20),
                "ends_at": today.replace(month=12, day=22),
                "price_per_night": Decimal("180.00"),
                "min_nights": 1,
                "priority": 20,
            },
        )

    def _seed_demo_booking(self, room_types):
        if Booking.objects.exists():
            return
        room = Room.objects.filter(room_type=room_types["Double"]).first()
        client = resolve_client(
            first_name="Ada",
            last_name="Lovelace",
            email="ada@example.com",
            phone="+48 500 100 200",
            language="en",
        )
        check_in = _dt.date.today() + _dt.timedelta(days=7)
        check_out = check_in + _dt.timedelta(days=2)
        try:
            create_booking(
                room=room,
                client=client,
                check_in=check_in,
                check_out=check_out,
                guests=2,
                source="seed",
                notes="Demo booking created by the seed command.",
            )
        except Exception as exc:  # pragma: no cover - defensive
            self.stdout.write(self.style.WARNING(f"Demo booking skipped: {exc}"))

    def _ensure_superuser(self):
        User = get_user_model()
        username = "admin"
        if not User.objects.filter(username=username).exists():
            User.objects.create_superuser(
                username=username,
                email="admin@hotel-ai.local",
                password="admin1234",
            )
            self.stdout.write("Created superuser 'admin' / 'admin1234'.")