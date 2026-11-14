"""REST API views for the booking engine."""

from __future__ import annotations

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from rest_framework import status
import json as _json
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import generics

from .models import Booking, BookingStatus, Client, Room, RoomType
from .serializers import (
    AvailabilityRequestSerializer,
    AvailabilityResponseSerializer,
    BookingRequestSerializer,
    BookingResponseSerializer,
    BookingSerializer,
    ClientSerializer,
    PricingRequestSerializer,
    RoomSerializer,
    RoomTypeSerializer,
)
from .services import (
    availability_summary,
    calculate_total,
    cancel_booking,
    check_in_booking,
    check_out_booking,
    create_booking,
    effective_price,
    find_available_rooms,
    parse_date,
    resolve_client,
)


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
        try:
            room = Room.objects.get(room_number=data["room_number"], room_type=room_type)
        except Room.DoesNotExist:
            return JsonResponse(
                {"detail": f"Room '{data['room_number']}' not found for that room type"},
                status=404,
            )

        client = resolve_client(
            first_name=data["first_name"],
            last_name=data["last_name"],
            email=data["email"],
            phone=data["phone"],
            language=data["language"],
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

    permission_classes = [AllowAny]

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

    permission_classes = [AllowAny]

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

    permission_classes = [AllowAny]

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


@login_required
def booking_admin(request):
    """Admin booking management page with filtering."""
    if not request.user.is_staff:
        return JsonResponse({"detail": "Staff access required."}, status=403)

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


@login_required
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