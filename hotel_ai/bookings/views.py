"""REST API views for the booking engine."""

from __future__ import annotations

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.utils import timezone
from rest_framework import status
import json as _json
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import generics
import threading
import time

from hotel_ai.accounts.permissions import BookingOwnershipPermission, IsOwnerOrReadOnly
import datetime as _dt

from hotel_ai.bookings.services import (
    availability_summary,
    calculate_total,
    cancel_booking,
    check_in_booking,
    check_out_booking,
    create_booking,
    create_group_booking,
    estimate_arrival_update,
    estimate_cancellation_refund,
    effective_price,
    find_available_rooms,
    get_group_bookings,
    modify_booking_dates,
    no_show_booking,
    parse_date,
    resolve_client,
    validate_promo_code,
    apply_promo_code,
    add_waitlist_entry,
    cancel_waitlist_entry,
    mark_waitlist_as_booked,
    check_and_notify_waitlist,
    issue_digital_key,
    invalidate_digital_key,
    get_or_create_digital_key,
)
from hotel_ai.bookings.invoices import generate_invoice_pdf

from .models import Booking, BookingLog, BookingStatus, Client, GroupBooking, LoyaltyPoint, Notification, PromoCode, RatePlan, Room, RoomType, WaitlistEntry, DigitalKey
from .serializers import (
    AvailabilityRequestSerializer,
    AvailabilityResponseSerializer,
    BookingLogSerializer,
    BookingModifySerializer,
    BookingRequestSerializer,
    BookingResponseSerializer,
    BookingSerializer,
    ClientSerializer,
    PricingRequestSerializer,
    PreArrivalSerializer,
    PromoCodeApplySerializer,
    PromoCodeCheckSerializer,
    PromoCodeSerializer,
    RatePlanSerializer,
    RoomSerializer,
    RoomTypeSerializer,
    NotificationSerializer,
    LoyaltyPointSerializer,
    GroupBookingSerializer,
    GroupBookingCreateSerializer,
    WaitlistEntrySerializer,
    WaitlistEntryCreateSerializer,
    DigitalKeySerializer,
)


class BookingActivityView(APIView):
    """Return the activity timeline for a single booking."""

    permission_classes = [AllowAny]

    def get(self, request, pk):
        try:
            booking = Booking.objects.get(pk=pk)
        except Booking.DoesNotExist:
            return JsonResponse({"detail": "Booking not found"}, status=404)
        logs = (
            booking.logs.select_related()
            .values("id", "action", "notes", "created_at")
            .order_by("created_at")
        )
        return JsonResponse({
            "booking_id": pk,
            "current_status": booking.status,
            "activity_log": list(logs),
        })


class RoomTypeListView(generics.ListAPIView):
    serializer_class = RoomTypeSerializer
    queryset = RoomType.objects.all()


class RoomListView(generics.ListAPIView):
    serializer_class = RoomSerializer
    queryset = Room.objects.filter(is_active=True).select_related("room_type")


class ClientListCreateView(generics.ListCreateAPIView):
    serializer_class = ClientSerializer
    queryset = Client.objects.all()


class BookingListView(generics.ListAPIView):
    serializer_class = BookingSerializer
    queryset = Booking.objects.select_related("room", "client", "pricing_rule").all()


class BookingRetrieveUpdateView(generics.RetrieveUpdateAPIView):
    serializer_class = BookingSerializer
    queryset = Booking.objects.select_related("room", "client", "pricing_rule")
    permission_classes = [BookingOwnershipPermission]


class BookingLookupView(APIView):
    """Return a single booking by its integer pk (read-only)."""

    permission_classes = [AllowAny]

    def get(self, request, pk):
        try:
            booking = Booking.objects.select_related(
                "room", "room__room_type", "client", "pricing_rule"
            ).get(pk=pk)
        except Booking.DoesNotExist:
            return JsonResponse({"detail": "Booking not found"}, status=404)
        return JsonResponse(BookingSerializer(booking).data)


class AvailabilityView(APIView):
    """GET/POST availability summary for a date range."""

    permission_classes = [AllowAny]

    def get(self, request):
        try:
            check_in = parse_date(request.GET["check_in"])
            check_out = parse_date(request.GET["check_out"])
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)
        guests = int(request.GET.get("guests", "1") or "1")
        room_type_name = request.GET.get("room_type", "").strip()
        return JsonResponse(self._summarize(check_in, check_out, guests, room_type_name), safe=False)

    def post(self, request):
        serializer = AvailabilityRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        return JsonResponse(self._summarize(data["check_in"], data["check_out"], data["guests"], data.get("room_type", "")), safe=False)

    @staticmethod
    def _summarize(check_in, check_out, guests, room_type_name):
        if room_type_name:
            try:
                rt = RoomType.objects.get(name=room_type_name)
            except RoomType.DoesNotExist:
                return {"detail": f"Unknown room type '{room_type_name}'"}
            rooms = find_available_rooms(
                room_type=rt, check_in=check_in, check_out=check_out, guests=guests
            )
            return {
                "room_type": rt.name,
                "capacity": rt.capacity,
                "base_price_per_night": str(rt.base_price_per_night),
                "effective_price_per_night": str(effective_price(rt, check_in)),
                "total_price": str(calculate_total(rt, check_in, check_out)),
                "available_rooms": len(rooms),
                "available": len(rooms) > 0,
            }
        return availability_summary(
            check_in=check_in, check_out=check_out, guests=guests
        )


class PricingView(APIView):
    """GET/POST pricing for a room type over a date range."""

    permission_classes = [AllowAny]

    def get(self, request):
        try:
            rt = RoomType.objects.get(name=request.GET["room_type"])
        except RoomType.DoesNotExist:
            return JsonResponse({"detail": "Unknown room type"}, status=400)
        check_in = parse_date(request.GET["check_in"])
        check_out = parse_date(request.GET["check_out"])
        return JsonResponse({
            "room_type": rt.name,
            "check_in": check_in.isoformat(),
            "check_out": check_out.isoformat(),
            "nights": (check_out - check_in).days,
            "base_price_per_night": str(rt.base_price_per_night),
            "effective_price_per_night": str(effective_price(rt, check_in)),
            "total_price": str(calculate_total(rt, check_in, check_out)),
        })

    def post(self, request):
        serializer = PricingRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            rt = RoomType.objects.get(name=data["room_type"])
        except RoomType.DoesNotExist:
            return JsonResponse({"detail": "Unknown room type"}, status=400)
        return JsonResponse({
            "room_type": rt.name,
            "check_in": data["check_in"].isoformat(),
            "check_out": data["check_out"].isoformat(),
            "nights": (data["check_out"] - data["check_in"]).days,
            "base_price_per_night": str(rt.base_price_per_night),
            "effective_price_per_night": str(effective_price(rt, data["check_in"])),
            "total_price": str(calculate_total(rt, data["check_in"], data["check_out"])),
        })


class BookingCreateView(APIView):
    """POST a new confirmed reservation."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = BookingRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            room_type = RoomType.objects.get(name=data["room_type"])
        except RoomType.DoesNotExist:
            return JsonResponse(
                {"detail": f"Unknown room type '{data['room_type']}'"}, status=404
            )
        room_number = data.get("room_number")
        if room_number:
            try:
                room = Room.objects.get(room_number=room_number, room_type=room_type)
            except Room.DoesNotExist:
                return JsonResponse(
                    {"detail": f"Room '{room_number}' not found for that room type"},
                    status=404,
                )
        else:
            rooms = find_available_rooms(
                room_type=room_type,
                check_in=data["check_in"],
                check_out=data["check_out"],
                guests=data["guests"],
            )
            if not rooms:
                return JsonResponse(
                    {"detail": "No rooms available for the requested dates and guests"},
                    status=409,
                )
            room = rooms[0]

        client = resolve_client(
            first_name=data["first_name"],
            last_name=data["last_name"],
            email=data["email"],
            phone=data["phone"],
            language=data["language"],
        )

        # Resolve rate plan if provided
        rate_plan = None
        plan_slug = data.get("rate_plan", "").strip()
        if plan_slug:
            try:
                rate_plan = RatePlan.objects.get(name__iexact=plan_slug)
            except RatePlan.DoesNotExist:
                return JsonResponse(
                    {"detail": f"Unknown rate plan '{plan_slug}'"},
                    status=400,
                )

        try:
            booking = create_booking(
                room=room,
                client=client,
                check_in=data["check_in"],
                check_out=data["check_out"],
                guests=data["guests"],
                source=data.get("source", "chat"),
                notes=data.get("notes", ""),
                rate_plan=rate_plan,
            )
        except (ValueError, IntegrityError) as exc:
            return JsonResponse({"detail": str(exc)}, status=409)

        return JsonResponse({
            "booking": BookingSerializer(booking).data,
            "message": (
                f"Booking confirmed for {client.full_name}: "
                f"{room.room_number} {booking.check_in} -> {booking.check_out}, "
                f"{booking.nights} nights, {booking.total_price} {booking.currency}."
            ),
        }, status=201)


class BookingCancelView(APIView):
    """Cancel a booking by id."""

    permission_classes = [BookingOwnershipPermission]

    def post(self, request, pk):
        try:
            booking = Booking.objects.get(pk=pk)
        except Booking.DoesNotExist:
            return JsonResponse({"detail": "Booking not found"}, status=404)
        if booking.status == BookingStatus.CANCELLED:
            return JsonResponse(
                {"booking": BookingSerializer(booking).data, "message": "Already cancelled."}
            )
        booking = cancel_booking(booking)
        return JsonResponse({
            "booking": BookingSerializer(booking).data,
            "message": f"Booking {booking.id} cancelled.",
        })


class BookingCheckInView(APIView):
    """Check in a confirmed booking."""

    permission_classes = [BookingOwnershipPermission]

    def post(self, request, pk):
        try:
            booking = Booking.objects.select_related(
                "room", "room__room_type", "client", "pricing_rule"
            ).get(pk=pk)
        except Booking.DoesNotExist:
            return JsonResponse({"detail": "Booking not found"}, status=404)
        try:
            booking = check_in_booking(booking)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)
        return JsonResponse({
            "booking": BookingSerializer(booking).data,
            "message": f"Booking {booking.id} checked in.",
        })


class BookingCheckoutView(APIView):
    """Check out a checked-in booking."""

    permission_classes = [BookingOwnershipPermission]

    def post(self, request, pk):
        try:
            booking = Booking.objects.select_related(
                "room", "room__room_type", "client", "pricing_rule"
            ).get(pk=pk)
        except Booking.DoesNotExist:
            return JsonResponse({"detail": "Booking not found"}, status=404)
        try:
            booking = check_out_booking(booking)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)
        return JsonResponse({
            "booking": BookingSerializer(booking).data,
            "message": f"Booking {booking.id} checked out.",
        })


class BookingNoShowView(APIView):
    """Mark a confirmed booking whose guest did not arrive as a no-show."""

    permission_classes = [BookingOwnershipPermission]

    def post(self, request, pk):
        try:
            booking = Booking.objects.select_related(
                "room", "room__room_type", "client", "pricing_rule"
            ).get(pk=pk)
        except Booking.DoesNotExist:
            return JsonResponse({"detail": "Booking not found"}, status=404)
        if booking.status == BookingStatus.NO_SHOW:
            return JsonResponse(
                {"booking": BookingSerializer(booking).data, "message": "Already marked as no-show."}
            )
        try:
            booking = no_show_booking(booking)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)
        return JsonResponse({
            "booking": BookingSerializer(booking).data,
            "message": f"Booking {booking.id} marked as no-show.",
        })


def booking_admin(request):
    """Admin booking management page with filtering."""

    qs = Booking.objects.select_related("room", "room__room_type", "client").order_by("-check_in")

    status_filter = request.GET.get("status", "")
    room_type = request.GET.get("room_type", "")
    search = request.GET.get("search", "").strip()
    check_in_from = request.GET.get("check_in_from", "")
    check_in_to = request.GET.get("check_in_to", "")

    if status_filter and status_filter != "all":
        qs = qs.filter(status=status_filter)
    if room_type:
        qs = qs.filter(room__room_type__name=room_type)
    if search:
        from django.db.models import Q
        qs = qs.filter(
            Q(client__first_name__icontains=search)
            | Q(client__last_name__icontains=search)
            | Q(client__email__icontains=search)
        )
    if check_in_from:
        qs = qs.filter(check_in__gte=check_in_from)
    if check_in_to:
        qs = qs.filter(check_in__lte=check_in_to)

    room_types = list(RoomType.objects.values_list("name", flat=True))
    total = qs.count()

    return render(request, "bookings/admin.html", {
        "bookings": qs,
        "total": total,
        "room_types": room_types,
        "filters": {
            "status": status_filter,
            "room_type": room_type,
            "search": search,
            "check_in_from": check_in_from,
            "check_in_to": check_in_to,
        },
    })


def booking_export(request):
    """Export bookings as CSV."""
    qs = Booking.objects.select_related("room", "room__room_type", "client").order_by("-check_in")

    status_filter = request.GET.get("status", "")
    room_type = request.GET.get("room_type", "")
    search = request.GET.get("search", "").strip()
    check_in_from = request.GET.get("check_in_from", "")
    check_in_to = request.GET.get("check_in_to", "")

    if status_filter and status_filter != "all":
        qs = qs.filter(status=status_filter)
    if room_type:
        qs = qs.filter(room__room_type__name=room_type)
    if search:
        from django.db.models import Q
        qs = qs.filter(
            Q(client__first_name__icontains=search)
            | Q(client__last_name__icontains=search)
            | Q(client__email__icontains=search)
        )
    if check_in_from:
        qs = qs.filter(check_in__gte=check_in_from)
    if check_in_to:
        qs = qs.filter(check_in__lte=check_in_to)

    import csv
    from django.http import HttpResponse

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="bookings-export.csv"'

    writer = csv.writer(response)
    writer.writerow(["ID", "Client", "Email", "Room Number", "Room Type", "Check-in", "Check-out", "Nights", "Guests", "Total Price", "Currency", "Status", "Source", "Created At"])

    for b in qs:
        writer.writerow([
            b.id,
            b.client.full_name,
            b.client.email,
            b.room.room_number,
            b.room.room_type.name,
            b.check_in.isoformat(),
            b.check_out.isoformat(),
            b.nights,
            b.guests,
            str(b.total_price),
            b.currency,
            b.status,
            b.source,
            b.created_at.strftime("%Y-%m-%d %H:%M"),
        ])

    return response


def booking_calendar_feed(request):
    """Return all active bookings as an iCal (.ics) calendar feed."""
    import datetime as _dt

    from django.http import HttpResponse

    now_utc = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    qs = (
        Booking.objects.select_related("room", "client")
        .filter(
            status__in=[BookingStatus.CONFIRMED, BookingStatus.CHECKED_IN]
        )
        .order_by("check_in")
    )

    events = ""
    for b in qs:
        summary = f"{b.client.full_name} - Room {b.room.room_number}"
        description_lines = [f"Booking #{b.id}"]
        if b.total_price:
            description_lines.append(f"Total: {b.total_price} {b.currency}")
        if b.client.email:
            description_lines.append(f"Guest: {b.client.email}")
        description = "\\n".join(description_lines)
        events += (
            f"BEGIN:VEVENT\r\n"
            f"UID:{b.id}@hotel-aurora.local\r\n"
            f"DTSTAMP:{now_utc}\r\n"
            f"DTSTART:{b.check_in.strftime('%Y%m%d')}\r\n"
            f"DTEND:{b.check_out.strftime('%Y%m%d')}\r\n"
            f"SUMMARY:{summary}\r\n"
            f"DESCRIPTION:{description}\r\n"
            f"STATUS:CONFIRMED\r\n"
            f"END:VEVENT\r\n"
        )

    body = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Hotel Aurora//EN\r\n"
        "CALSCALE:GREGORIAN\r\n"
        "METHOD:PUBLISH\r\n"
        f"\r\n{events}"
        "END:VCALENDAR\r\n"
    )

    response = HttpResponse(body, content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="hotel-aurora-calendar.ics"'
    return response


class AvailabilityStreamView(APIView):
    """SSE stream that emits availability updates on booking changes."""

    permission_classes = [AllowAny]

    _last_count: int = -1
    _lock = threading.Lock()
    _event_queue: list = []
    _watcher_thread: threading.Thread | None = None
    _running = False

    @classmethod
    def _emit(cls, data: dict):
        with cls._lock:
            cls._event_queue.append(data)

    @classmethod
    def _poll_loop(cls):
        last_count = -1
        while cls._running:
            time.sleep(2)
            try:
                active = (
                    Booking.objects.exclude(
                        status__in=[BookingStatus.CANCELLED, BookingStatus.NO_SHOW]
                    )
                    .count()
                )
            except Exception:  # noqa: BLE001
                continue
            if active != last_count:
                last_count = active
                cls._emit({
                    "type": "availability_update",
                    "active_bookings": active,
                })

    @classmethod
    def ensure_watcher(cls):
        if cls._watcher_thread is None or not cls._watcher_thread.is_alive():
            cls._running = True
            cls._watcher_thread = threading.Thread(
                target=cls._poll_loop, daemon=True
            )
            cls._watcher_thread.start()

    def get(self, request):
        self.ensure_watcher()

        def event_stream():
            event_id = 0
            while True:
                with self._lock:
                    while self._event_queue:
                        event = self._event_queue.pop(0)
                        event_id += 1
                        yield f"id: {event_id}\n"
                        yield f"data: {_json.dumps(event)}\n\n"
                time.sleep(0.5)

        response = StreamingHttpResponse(
            event_stream(),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response


class RoomBulkUpdateView(APIView):
    """PATCH bulk-update room properties (room_number, floor, is_active)."""

    permission_classes = [BookingOwnershipPermission]

    def patch(self, request):
        rooms_data = request.data
        if isinstance(rooms_data, dict):
            rooms_data = [rooms_data]
        if not isinstance(rooms_data, list):
            return JsonResponse({"detail": "Expected a list or object"}, status=400)

        updated = []
        errors = []
        for item in rooms_data:
            pk = item.get("id")
            if pk is None:
                errors.append({"id": None, "error": "Missing 'id'"})
                continue
            try:
                room = Room.objects.select_for_update().get(pk=pk)
            except Room.DoesNotExist:
                errors.append({"id": pk, "error": "Room not found"})
                continue
            if "room_number" in item:
                existing = Room.objects.filter(
                    room_number=item["room_number"], is_active=True
                ).exclude(pk=pk)
                if existing.exists():
                    errors.append({"id": pk, "error": f"Room number '{item['room_number']}' already in use"})
                    continue
                room.room_number = item["room_number"]
            if "floor" in item:
                room.floor = int(item["floor"])
            if "is_active" in item:
                room.is_active = bool(item["is_active"])
            room.save()
            updated.append(room)

        result = {
            "updated": [{"id": r.id, "room_number": r.room_number, "floor": r.floor, "is_active": r.is_active} for r in updated],
            "errors": errors,
        }
        status_code = 200 if not errors else 207
        return JsonResponse(result, status=status_code)

def booking_form(request):
    """Booking form page."""
    language = request.GET.get("lang", "en")
    return render(request, "bookings/booking.html", {"language": language})


def booking_confirm(request):
    """Booking confirmation page."""
    language = request.GET.get("lang", "en")
    return render(request, "bookings/confirm.html", {"language": language})


class PromoCodeCheckView(APIView):
    """GET /api/promo-codes/<code>/check/?check_in=YYYY-MM-DD — validate a promo code."""

    permission_classes = [AllowAny]

    def get(self, request, code):
        check_in_str = request.GET.get("check_in")
        if not check_in_str:
            return JsonResponse({"detail": "check_in parameter required (YYYY-MM-DD)"}, status=400)
        try:
            check_in = parse_date(check_in_str)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)
        try:
            promo = validate_promo_code(code, check_in)
        except ValueError as exc:
            return JsonResponse({"valid": False, "detail": str(exc)}, status=400)
        return JsonResponse({
            "valid": True,
            "code": promo.code,
            "discount_type": promo.discount_type,
            "discount_value": str(promo.discount_value),
            "valid_from": promo.valid_from.isoformat(),
            "valid_to": promo.valid_to.isoformat(),
        })


class PromoCodeApplyView(APIView):
    """POST /api/bookings/<id>/apply-promo/ — apply a promo code to a booking."""

    permission_classes = [BookingOwnershipPermission]

    def post(self, request, pk):
        try:
            booking = Booking.objects.select_related("room", "room__room_type", "client").get(pk=pk)
        except Booking.DoesNotExist:
            return JsonResponse({"detail": "Booking not found"}, status=404)

        code = request.data.get("code", "").strip()
        if not code:
            return JsonResponse({"detail": "Promo code required"}, status=400)

        try:
            booking = apply_promo_code(booking, code)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)

        return JsonResponse({
            "booking": BookingSerializer(booking).data,
            "message": f"Promo code applied. New total: {booking.total_price} {booking.currency}.",
        })


class BookingModifyView(APIView):
    """POST /api/bookings/<id>/modify/ — modify a booking's dates and/or guests.

    Request body:
    {
        "check_in": "YYYY-MM-DD",
        "check_out": "YYYY-MM-DD",
        "guests": <optional integer>
    }

    Returns the modified booking with updated total_price and price_change delta.
    """

    permission_classes = [BookingOwnershipPermission]

    def post(self, request, pk):
        try:
            booking = Booking.objects.select_related("room", "room__room_type", "client").get(pk=pk)
        except Booking.DoesNotExist:
            return JsonResponse({"detail": "Booking not found"}, status=404)

        serializer = BookingModifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            booking, price_change = modify_booking_dates(
                booking=booking,
                check_in=serializer.validated_data["check_in"],
                check_out=serializer.validated_data["check_out"],
                guests=serializer.validated_data.get("guests"),
            )
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)

        return JsonResponse({
            "booking": BookingSerializer(booking).data,
            "message": f"Booking modified. Price change: {price_change:+.2f} {booking.currency}.",
            "price_change": float(price_change),
        })


class CancellationEstimateView(APIView):
    """GET /api/bookings/<id>/cancel-estimate/ — get refund info before cancelling."""

    permission_classes = [BookingOwnershipPermission]

    def get(self, request, pk):
        try:
            booking = Booking.objects.select_related("room", "room__room_type", "client").get(pk=pk)
        except Booking.DoesNotExist:
            return JsonResponse({"detail": "Booking not found"}, status=404)
        if booking.status != BookingStatus.CONFIRMED:
            return JsonResponse({"detail": f"Cannot cancel booking with status '{booking.status}'"}, status=400)

        result = estimate_cancellation_refund(booking)
        return JsonResponse(result)


class RatePlanListView(APIView):
    """GET /api/rate-plans/ — list all available rate plans."""

    permission_classes = [AllowAny]

    def get(self, request):
        plans = list(RatePlan.objects.all())
        return JsonResponse(
            [RatePlanSerializer(plan).data for plan in plans], safe=False
        )


class RatePlansByRoomTypeView(APIView):
    """GET /api/rate-plans/?room_type=<name>&check_in=YYYY-MM-DD — get applicable rate plans."""

    permission_classes = [AllowAny]

    def get(self, request):
        room_type_name = request.GET.get("room_type")
        check_in_str = request.GET.get("check_in", "")
        nights_str = request.GET.get("nights", "1")

        if not room_type_name:
            return JsonResponse({"detail": "room_type parameter required"}, status=400)

        try:
            rt = RoomType.objects.get(name=room_type_name)
        except RoomType.DoesNotExist:
            return JsonResponse({"detail": "Unknown room type"}, status=404)

        check_in = parse_date(check_in_str) if check_in_str else None
        try:
            nights = max(1, int(nights_str or 1))
        except (ValueError, TypeError):
            nights = 1

        results = []
        for plan in RatePlan.objects.all():
            calc_check_in = check_in or _dt.date.today()
            calc_check_out = calc_check_in + _dt.timedelta(days=nights)
            base = calculate_total(
                rt, calc_check_in, calc_check_out, rate_plan=plan
            )

            results.append({
                "id": plan.id,
                "name": plan.name,
                "description": plan.description,
                "plan_type": plan.plan_type,
                "is_non_refundable": plan.is_non_refundable,
                "includes_breakfast": plan.includes_breakfast,
                "corporate_discount_pct": plan.corporate_discount_pct,
                "stay_x_pay_nights": plan.stay_x_pay_nights,
                "total_price": str(base),
                "currency": "PLN",
                "per_night": str(round(base / nights, 2)) if nights > 0 else "0.00",
            })

        return JsonResponse(results, safe=False)


class BookingPreArrivalView(APIView):
    """PATCH /api/bookings/<id>/pre-arrival/ — update pre-arrival info.

    Accepts estimated_arrival (time) and special_requests (text).
    Does NOT handle file upload; the filename is recorded separately via
    a POST with multipart/form-data if needed.
    """

    permission_classes = [BookingOwnershipPermission]

    def patch(self, request, pk):
        try:
            booking = Booking.objects.get(pk=pk)
        except Booking.DoesNotExist:
            return JsonResponse({"detail": "Booking not found"}, status=404)

        serializer = PreArrivalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        changes = []
        if "estimated_arrival" in data:
            booking.estimated_arrival = data["estimated_arrival"]
            changes.append("estimated_arrival")
        if "special_requests" in data:
            booking.special_requests = data["special_requests"]
            changes.append("special_requests")
        if changes:
            booking.save(update_fields=changes + ["updated_at"])
            BookingLog.objects.create(
                booking=booking,
                action="pre_arrival_updated",
                notes=f"Updated fields: {', '.join(changes)}",
            )

        return JsonResponse({
            "booking": BookingSerializer(booking).data,
            "message": f"Pre-arrival info updated.",
        })

    def post(self, request, pk):
        """Handle ID document upload."""
        try:
            booking = Booking.objects.get(pk=pk)
        except Booking.DoesNotExist:
            return JsonResponse({"detail": "Booking not found"}, status=404)

        id_file = request.FILES.get("id_document")
        if not id_file:
            return JsonResponse({"detail": "id_document file required"}, status=400)

        # Save to MEDIA_ROOT/identity_documents/
        import os
        from django.conf import settings
        from django.utils.text import get_valid_filename

        upload_dir = os.path.join(settings.MEDIA_ROOT, "identity_documents")
        os.makedirs(upload_dir, exist_ok=True)

        ext = os.path.splitext(id_file.name)[1] or ".bin"
        safe_name = f"{pk}_{get_valid_filename(id_file.name)}{ext}"
        path = os.path.join(upload_dir, safe_name)

        with open(path, "wb+") as dest:
            for chunk in id_file.chunks():
                dest.write(chunk)

        booking.id_document_filename = safe_name
        booking.save(update_fields=["id_document_filename", "updated_at"])
        BookingLog.objects.create(
            booking=booking,
            action="id_uploaded",
            notes=f"Uploaded ID document: {safe_name}",
        )

        return JsonResponse({
            "booking": BookingSerializer(booking).data,
            "message": f"ID document uploaded: {safe_name}",
        })


class BookingInvoiceView(APIView):
    """GET /api/bookings/<id>/invoice/ — download the booking invoice as PDF."""

    permission_classes = [BookingOwnershipPermission]

    def get(self, request, pk):
        try:
            booking = Booking.objects.select_related(
                "room", "room__room_type", "client", "rate_plan"
            ).get(pk=pk)
        except Booking.DoesNotExist:
            return JsonResponse({"detail": "Booking not found"}, status=404)

        pdf_bytes = generate_invoice_pdf(booking)
        if not pdf_bytes:
            return JsonResponse({"detail": "Invoice generation unavailable"}, status=501)

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="invoice_{pk}.pdf"'
        return response


class NotificationListView(APIView):
    """GET /api/notifications/?client=<client_id> — list a client's notifications.

    Query params:
        client (required): the Client.id whose notifications are listed.
        unread (optional): "1" to return only unread notifications.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        client_id = request.GET.get("client")
        if not client_id:
            return JsonResponse({"detail": "client parameter required"}, status=400)
        try:
            client = Client.objects.get(pk=client_id)
        except (Client.DoesNotExist, ValueError):
            return JsonResponse({"detail": "Client not found"}, status=404)

        qs = client.notifications.all()
        if request.GET.get("unread") in ("1", "true"):
            qs = qs.filter(read_at__isnull=True)
        data = [
            {**NotificationSerializer(n).data, "unread": n.read_at is None}
            for n in qs
        ]
        return JsonResponse(data, safe=False)


class NotificationReadView(APIView):
    """POST /api/notifications/<int:pk>/read/ — mark one notification as read.

    Accepts optional {"all": true} to mark every notification of the same
    recipient as read.
    """

    permission_classes = [AllowAny]

    def post(self, request, pk):
        try:
            notification = Notification.objects.select_related("recipient").get(pk=pk)
        except Notification.DoesNotExist:
            return JsonResponse({"detail": "Notification not found"}, status=404)

        client = notification.recipient
        mark_all = bool(request.data.get("all")) if isinstance(request.data, dict) else False
        if mark_all:
            updated = client.notifications.filter(read_at__isnull=True).update(
                read_at=timezone.now()
            )
        else:
            if notification.read_at is None:
                notification.read_at = timezone.now()
                notification.save(update_fields=["read_at"])
            updated = 1

        return JsonResponse({
            "marked_read": updated,
            "message": "Notifications marked as read.",
        })


class LoyaltyBalanceView(APIView):
    """GET /api/loyalty/balance/?client=<id> — get a client's loyalty point balance."""

    permission_classes = [AllowAny]

    def get(self, request):
        client_id = request.GET.get("client")
        if not client_id:
            return JsonResponse({"detail": "client parameter required"}, status=400)
        try:
            client = Client.objects.get(pk=client_id)
        except (Client.DoesNotExist, ValueError):
            return JsonResponse({"detail": "Client not found"}, status=404)

        history = [
            {**LoyaltyPointSerializer(lp).data, "absolute_points": lp.absolute_points}
            for lp in client.loyalty_points.all()[:20]
        ]
        return JsonResponse({
            "client_id": client.id,
            "balance": client.loyalty_balance,
            "history": history,
        })



class GroupBookingCreateView(APIView):
    """POST /api/group-bookings/ — create a group booking with multiple rooms."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = GroupBookingCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return JsonResponse(serializer.errors, status=400)

        data = serializer.validated_data
        try:
            client = Client.objects.get(pk=data.get("client_id"))
        except (Client.DoesNotExist, ValueError):
            return JsonResponse({"detail": "Client not found"}, status=404)

        room_ids = data["room_ids"]
        rooms = list(Room.objects.filter(id__in=room_ids).select_related("room_type"))
        if len(rooms) != len(room_ids):
            missing = set(room_ids) - {r.id for r in rooms}
            return JsonResponse({"detail": f"Rooms not found: {missing}"}, status=404)

        try:
            group, bookings = create_group_booking(
                client=client,
                name=data["name"],
                rooms=rooms,
                check_in=data["check_in"],
                check_out=data["check_out"],
                guests=data.get("guests", 1),
                source=data.get("source", "chat"),
                notes=data.get("notes", ""),
            )
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)

        return JsonResponse({
            "group": GroupBookingSerializer(group).data,
            "bookings": [BookingSerializer(b).data for b in bookings],
            "message": f"Group booking '{group.name}' created with {len(bookings)} rooms.",
        }, status=201)


class GroupBookingDetailView(APIView):
    """GET /api/group-bookings/<pk>/ — get a group booking detail."""

    permission_classes = [AllowAny]

    def get(self, request, pk):
        try:
            group = GroupBooking.objects.select_related("client").prefetch_related("bookings").get(pk=pk)
        except GroupBooking.DoesNotExist:
            return JsonResponse({"detail": "Group booking not found"}, status=404)
        return JsonResponse(GroupBookingSerializer(group).data)


class GroupBookingListView(APIView):
    """GET /api/group-bookings/?client=<id> — list group bookings for a client."""

    permission_classes = [AllowAny]

    def get(self, request):
        client_id = request.GET.get("client")
        if not client_id:
            return JsonResponse({"detail": "client parameter required"}, status=400)
        try:
            client = Client.objects.get(pk=client_id)
        except (Client.DoesNotExist, ValueError):
            return JsonResponse({"detail": "Client not found"}, status=404)

        groups = get_group_bookings(client)
        return JsonResponse([GroupBookingSerializer(g).data for g in groups], safe=False)


class WaitlistEntryCreateView(APIView):
    """POST /api/waitlist/ — add a guest to the waitlist."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = WaitlistEntryCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return JsonResponse(serializer.errors, status=400)

        data = serializer.validated_data
        client_id = request.data.get("client_id")
        if not client_id:
            return JsonResponse({"detail": "client_id parameter required"}, status=400)
        try:
            client = Client.objects.get(pk=client_id)
        except (Client.DoesNotExist, ValueError):
            return JsonResponse({"detail": "Client not found"}, status=404)

        try:
            entry = add_waitlist_entry(
                client=client,
                room_type_id=data["room_type_id"],
                check_in=data["check_in"],
                check_out=data["check_out"],
                guests=data.get("guests", 1),
                notes=data.get("notes", ""),
            )
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)

        return JsonResponse(WaitlistEntrySerializer(entry).data, status=201)


class WaitlistEntryListView(APIView):
    """GET /api/waitlist/?client=<id> — list waitlist entries for a client."""

    permission_classes = [AllowAny]

    def get(self, request):
        client_id = request.GET.get("client")
        if not client_id:
            return JsonResponse({"detail": "client parameter required"}, status=400)
        try:
            client = Client.objects.get(pk=client_id)
        except (Client.DoesNotExist, ValueError):
            return JsonResponse({"detail": "Client not found"}, status=404)

        entries = client.waitlist_entries.all()
        return JsonResponse([WaitlistEntrySerializer(e).data for e in entries], safe=False)


class WaitlistEntryDetailView(APIView):
    """GET /api/waitlist/<pk>/ — get a waitlist entry detail."""

    permission_classes = [AllowAny]

    def get(self, request, pk):
        try:
            entry = WaitlistEntry.objects.select_related("client", "room_type").get(pk=pk)
        except WaitlistEntry.DoesNotExist:
            return JsonResponse({"detail": "Waitlist entry not found"}, status=404)
        return JsonResponse(WaitlistEntrySerializer(entry).data)

    def post(self, request, pk):
        """Cancel or mark as booked a waitlist entry."""
        try:
            entry = WaitlistEntry.objects.get(pk=pk)
        except WaitlistEntry.DoesNotExist:
            return JsonResponse({"detail": "Waitlist entry not found"}, status=404)

        action = request.data.get("action")
        if action == "cancel":
            cancel_waitlist_entry(entry)
            return JsonResponse({"message": "Waitlist entry cancelled."})
        elif action == "book":
            try:
                mark_waitlist_as_booked(entry)
                return JsonResponse({"message": "Waitlist entry marked as booked."})
            except ValueError as exc:
                return JsonResponse({"detail": str(exc)}, status=400)
        return JsonResponse({"detail": "Invalid action. Use 'cancel' or 'book'."}, status=400)


class WaitlistCheckView(APIView):
    """POST /api/waitlist/check/ — trigger waitlist availability check."""

    permission_classes = [AllowAny]

    def post(self, request):
        notified_ids = check_and_notify_waitlist()
        return JsonResponse({"notified_count": len(notified_ids), "entries": notified_ids})


class DigitalKeyCreateView(APIView):
    """POST /api/digital-keys/<booking_pk>/ — issue a digital key for a booking."""

    permission_classes = [AllowAny]

    def post(self, request, pk):
        try:
            booking = Booking.objects.select_related("room", "client").get(pk=pk)
        except Booking.DoesNotExist:
            return JsonResponse({"detail": "Booking not found"}, status=404)
        if booking.status != BookingStatus.CHECKED_IN:
            return JsonResponse({"detail": "Digital key can only be issued for checked-in bookings."}, status=400)
        try:
            key = get_or_create_digital_key(booking)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)
        return JsonResponse(DigitalKeySerializer(key).data, status=201)


class DigitalKeyDetailView(APIView):
    """GET /api/digital-keys/<pk>/ — retrieve a digital key detail."""

    permission_classes = [AllowAny]

    def get(self, request, pk):
        try:
            key = DigitalKey.objects.select_related("booking__room", "booking__client").get(pk=pk)
        except DigitalKey.DoesNotExist:
            return JsonResponse({"detail": "Digital key not found"}, status=404)
        return JsonResponse(DigitalKeySerializer(key).data)

    def post(self, request, pk):
        """Invalidate a digital key."""
        try:
            key = DigitalKey.objects.get(pk=pk)
        except DigitalKey.DoesNotExist:
            return JsonResponse({"detail": "Digital key not found"}, status=404)
        invalidate_digital_key(key)
        return JsonResponse({"message": "Digital key invalidated."})
