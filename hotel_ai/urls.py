"""Root URL configuration for the Hotel-AI-Claude project."""

import datetime as _dt
from django.contrib import admin
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import include, path

from hotel_ai.bookings.models import Booking, RoomType, Room


def health(request):
    return JsonResponse({"status": "ok", "service": "hotel-ai-claude"})


def home(request):
    language = request.GET.get("lang", "en")
    return render(request, "index.html", {"language": language})


def dashboard(request):
    """Dashboard showing occupancy statistics and arrivals/departures."""
    language = request.GET.get("lang", "en")
    date_str = request.GET.get("date", _dt.date.today().isoformat())
    try:
        selected_date = _dt.date.fromisoformat(date_str)
    except ValueError:
        selected_date = _dt.date.today()

    # Room stats
    total_rooms = Room.objects.filter(is_active=True).count()
    room_types = list(RoomType.objects.all())

    # Count occupied rooms on selected date (active bookings overlapping the date)
    occupied_room_ids = set(
        Booking.objects.filter(
            check_in__lte=selected_date,
            check_out__gt=selected_date,
        )
        .exclude(status="cancelled")
        .values_list("room_id", flat=True)
    )
    occupied_rooms = len(occupied_room_ids)
    available_rooms = total_rooms - occupied_rooms

    # Today's arrivals (check-in today)
    arrivals = Booking.objects.filter(
        check_in=selected_date,
    ).exclude(status="cancelled").select_related("room", "client").order_by("-created_at")[:20]

    # Today's departures (check-out today)
    departures = Booking.objects.filter(
        check_out=selected_date,
    ).exclude(status="cancelled").select_related("room", "client").order_by("-created_at")[:20]

    # Room type stats & grid
    room_stats = []
    room_grid = []
    for rt in room_types:
        rooms_qs = Room.objects.filter(room_type=rt, is_active=True)
        total = rooms_qs.count()
        occupied_ids = set(
            Booking.objects.filter(
                room__in=rooms_qs,
                check_in__lte=selected_date,
                check_out__gt=selected_date,
            )
            .exclude(status="cancelled")
            .values_list("room_id", flat=True)
        )
        room_type_grid = []
        for r in rooms_qs.order_by("room_number"):
            is_occupied = r.id in occupied_ids
            active_booking = None
            if is_occupied:
                try:
                    active_booking = Booking.objects.get(
                        room=r,
                        check_in__lte=selected_date,
                        check_out__gt=selected_date,
                    )
                except Booking.DoesNotExist:
                    pass
            room_type_grid.append({
                "id": r.id,
                "number": r.room_number,
                "occupied": is_occupied,
                "status": active_booking.status if active_booking else None,
                "guest": (
                    f"{active_booking.client.full_name}" if active_booking and active_booking.client else ""
                ),
                "booking_id": active_booking.id if active_booking else None,
                "check_in": (
                    active_booking.check_in.isoformat() if active_booking else None
                ),
                "check_out": (
                    active_booking.check_out.isoformat() if active_booking else None
                ),
            })
        occupied = len(occupied_ids)
        available = total - occupied
        occupancy_pct = int((occupied / total * 100) if total > 0 else 0)
        room_stats.append({
            "name": rt.name,
            "total": total,
            "available": available,
            "price": str(rt.base_price_per_night),
            "occupancy_pct": occupancy_pct,
        })
        room_grid.append({
            "room_type": rt.name,
            "rooms": room_type_grid,
        })

    pl = language == "pl"
    return render(request, "dashboard/index.html", {
        "language": language,
        "date": selected_date,
        "total_rooms": total_rooms,
        "occupied_rooms": occupied_rooms,
        "available_rooms": available_rooms,
        "arrived_today": len(arrivals),
        "departing_today": len(departures),
        "room_stats": room_stats,
        "room_grid": room_grid,
        "arrivals": arrivals,
        "departures": departures,
    })


urlpatterns = [
    path("health/", health, name="health"),
    path("", home, name="home"),
    path("dashboard/", dashboard, name="dashboard"),
    path("admin/", admin.site.urls),
    path("api/", include("hotel_ai.bookings.urls")),
    path("api/ai/", include("hotel_ai.ai_providers.urls")),
    path("api/calendar/", include("hotel_ai.calendar_integration.urls")),
    path("voice/", include("hotel_ai.voice.urls")),
    path("chat/", include("hotel_ai.chat.urls")),
]