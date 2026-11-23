"""Root URL configuration for the Hotel-AI-Claude project."""

import datetime as _dt
from django.contrib import admin
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import include, path

from hotel_ai.bookings.models import Booking, BookingLog, RoomType, Room
from hotel_ai.bookings.views import booking_confirm, booking_form


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
    # No-shows don't occupy rooms — the room is available for re-booking.
    occupied_room_ids = set(
        Booking.objects.filter(
            check_in__lte=selected_date,
            check_out__gt=selected_date,
        )
        .exclude(status__in=["cancelled", "no_show"])
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

    # Room type stats
    room_stats = []
    for rt in room_types:
        rooms = Room.objects.filter(room_type=rt, is_active=True)
        total = rooms.count()
        occupied = rooms.filter(
            bookings__check_in__lte=selected_date,
            bookings__check_out__gt=selected_date,
            bookings__status__in=["confirmed", "checked_in"],
        ).distinct().count()
        available = total - occupied
        occupancy_pct = int((occupied / total * 100) if total > 0 else 0)
        room_stats.append({
            "name": rt.name,
            "total": total,
            "available": available,
            "price": str(rt.base_price_per_night),
            "occupancy_pct": occupancy_pct,
        })

    # Booking activity timeline (recent status changes across all bookings)
    recent_activity = (
        BookingLog.objects.select_related("booking", "booking__client", "booking__room")
        .order_by("-created_at")[:25]
    )

    # Visual room grid: one tile per room, coloured by occupancy so the
    # front desk can see the whole property at a glance.
    room_grid = []
    for rt in room_types:
        rooms_qs = Room.objects.filter(room_type=rt, is_active=True).order_by("room_number")
        occupied_ids = set(
            Booking.objects.filter(
                room__in=rooms_qs,
                check_in__lte=selected_date,
                check_out__gt=selected_date,
            )
            .exclude(status="cancelled")
            .values_list("room_id", flat=True)
        )
        tiles = []
        for r in rooms_qs:
            active_booking = None
            if r.id in occupied_ids:
                active_booking = (
                    Booking.objects.filter(
                        room=r,
                        check_in__lte=selected_date,
                        check_out__gt=selected_date,
                    )
                    .exclude(status="cancelled")
                    .select_related("client")
                    .first()
                )
            tiles.append({
                "id": r.id,
                "number": r.room_number,
                "occupied": active_booking is not None,
                "status": active_booking.status if active_booking else None,
                "guest": (
                    active_booking.client.full_name
                    if active_booking and active_booking.client else ""
                ),
                "booking_id": active_booking.id if active_booking else None,
                "check_in": (
                    active_booking.check_in.isoformat() if active_booking else None
                ),
                "check_out": (
                    active_booking.check_out.isoformat() if active_booking else None
                ),
            })
        room_grid.append({"room_type": rt.name, "rooms": tiles})

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
        "recent_activity": recent_activity,
    })


urlpatterns = [
    path("health/", health, name="health"),
    # allauth is mounted at the project root (no namespace) because its
    # own templates call reverse("account_login") without a namespace.
    # It must come BEFORE the catch-all home view so /login/, /signup/
    # and /google/login/ resolve to allauth instead of the landing page.
    path("", include("allauth.urls")),
    path("", include("allauth.socialaccount.providers.google.urls")),
    path("", home, name="home"),
    path("dashboard/", dashboard, name="dashboard"),
    path("admin/", admin.site.urls),
    path("api/", include("hotel_ai.bookings.urls")),
    path("booking/", booking_form, name="booking-form"),
    path("booking/confirm", booking_confirm, name="booking-confirm"),
    path("api/ai/", include("hotel_ai.ai_providers.urls")),
    path("api/calendar/", include("hotel_ai.calendar_integration.urls")),
    path("voice/", include("hotel_ai.voice.urls")),
    path("chat/", include("hotel_ai.chat.urls")),
    path("accounts/", include("hotel_ai.accounts.urls")),
    path("integrations/openline/", include("django_openline.urls")),
]