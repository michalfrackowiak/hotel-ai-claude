"""The reservation agent.

Given a conversation history, the agent decides whether to *ask* for
information or to *act* (check availability, price a stay, confirm a booking).
It does this through a small tool loop: the LLM is asked to respond with
either JSON ``{"action": ..., "args": ...}`` or a plain reply for the guest.

This keeps every backend (OpenAI, Anthropic, Ollama) on one protocol - a
plain chat completion with a structured prompt - instead of relying on
provider-specific function calling.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from django.db.models import Q

from hotel_ai.bookings.models import Booking, BookingStatus, RoomType
from hotel_ai.bookings.services import (
    availability_summary,
    calculate_total,
    cancel_booking,
    check_in_booking,
    check_out_booking,
    create_booking,
    find_available_rooms,
    no_show_booking,
    resolve_client,
)

from .factory import get_active_provider
from .models import Conversation, Message

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT_EN = """You are the reception agent of a small hotel.
You help guests book rooms in English. Be warm, brief and concrete.

You can check availability, quote prices and confirm reservations.

Today's date: {today}
Available room types: {room_types}

Reply ONLY with a JSON object, no markdown fences:
  {{"action": "reply", "text": "<what you say to the guest>"}}
or, to check availability:
  {{"action": "check_availability", "args": {{"check_in": "YYYY-MM-DD", "check_out": "YYYY-MM-DD", "guests": 2}}}}
or, to confirm a booking:
  {{"action": "book", "args": {{"room_type": "Double", "check_in": "YYYY-MM-DD", "check_out": "YYYY-MM-DD", "guests": 2, "first_name": "...", "last_name": "...", "email": "...", "phone": "..."}}}}
or, to check in a guest:
  {{"action": "check_in", "args": {{"booking_id": 123}}}}
or, to check out a guest:
  {{"action": "check_out", "args": {{"booking_id": 123}}}}
or, to cancel a booking:
  {{"action": "cancel", "args": {{"booking_id": 123}}}}
or, to mark a confirmed booking as a no-show (guest did not arrive):
  {{"action": "no_show", "args": {{"booking_id": 123}}}}

Rules:
- Before booking, you must know: dates, guests, and the guest's name (+ email or phone).
- Always confirm the details with the guest before the "book" action.
- Use "booking_id" for check-in / check-out; if the guest only gives a name, ask for the booking id first.
- Use "cancel" to cancel a booking; confirm with the guest first and only cancel confirmed bookings.
- If the guest speaks Polish, still reply with JSON but write the text in Polish.
"""

DEFAULT_SYSTEM_PROMPT_PL = """Jesteś recepcjonistą w hotelu Aurora. Mówisz po polsku — ciepło, naturalnie, jak prywatna osoba z recepcji. Kształć wypowiedzi do jednego krótkiego zdania lub dwóch.

Możesz: sprawdzić dostępność pokoi i podać cenę, zarezerwować pokój dla gościa, odpowiedzieć na pytanie o warunki pobytu.

Dzisiaj: {today}
Dostępne typy pokoi: {room_types}

ODPOWIADAJ TYLKO W FORMACIE JSON — bez żadnego tekstu poza JSONem. To jest wymóg techniczny systemu.

Wzory JSON:
{{"action":"reply","text":"... twoja odpowiedz po polsku ..."}}
{{"action":"check_availability","args":{{"check_in":"YYYY-MM-DD","check_out":"YYYY-MM-DD","guests":2}}}}
{{"action":"book","args":{{"room_type":"Double","check_in":"YYYY-MM-DD","check_out":"YYYY-MM-DD","guests":2,"first_name":"...","last_name":"...","email":"...","phone":"..."}}}}
{{"action":"check_in","args":{{"booking_id":123}}}}
{{"action":"check_out","args":{{"booking_id":123}}}}
{{"action":"cancel","args":{{"booking_id":123}}}}
{{"action":"no_show","args":{{"booking_id":123}}}}

Zasady biznesowe:
- Przed rezerwacją musisz znać: daty, liczbę gości, imię i nazwisko gościa (+ e-mail lub telefon).
- Zawsze potwierdź szczegóły rezerwacji z gościem przed wywołaniem akcji book.
- Jeśli brakuje pokoju o podanej pojemności, powiedz to naturalnie i zaproponuj alternatywę.
- Poprawiaj gościa łagodnie, gdy się myli.
- Używaj naturalnych sformułowań: Dzień dobry, Jasne, Chwila, Zrobimy to, Proszę bardzo.
- Używaj akcji "cancel", aby anulować rezerwację — potwierdź z gościem przed anulowaniem.
- Nigdy nie odpowiadaj po angielsku — zawsze po polsku.
"""

@dataclass
class AgentTurnResult:
    """What one agent turn produced."""

    text: str = ""
    action: Optional[str] = None
    booking_id: Optional[int] = None
    error: Optional[str] = None
    raw_response: str = ""


class ReservationAgent:
    """Drives the multi-turn reservation conversation."""

    def __init__(self, language: str = "en", provider=None) -> None:
        self.language = language if language in ("en", "pl") else "en"
        self.provider = provider or get_active_provider(self.language)

    # ------------------------------------------------------------------
    def system_prompt(self) -> str:
        today = _dt.date.today().isoformat()
        room_types = ", ".join(
            RoomType.objects.values_list("name", flat=True)
        ) or "Single, Double, Suite"
        from .models import AgentSettings

        row = AgentSettings.objects.filter(language=self.language).first()
        template = (
            (row.system_prompt if row and row.system_prompt.strip() else None)
            or (
                DEFAULT_SYSTEM_PROMPT_PL
                if self.language == "pl"
                else DEFAULT_SYSTEM_PROMPT_EN
            )
        )
        return template.format(today=today, room_types=room_types)

    def history(self, conversation: Conversation, limit: int = 20) -> list[dict]:
        msgs = conversation.messages.all().order_by("-created_at")[:limit]
        return [
            {"role": m.role, "content": m.content}
            for m in reversed(list(msgs))
            if m.role in ("user", "assistant")
        ]

    # ------------------------------------------------------------------
    def handle_message(
        self,
        conversation: Conversation,
        user_message: str,
        max_tool_rounds: int = 4,
    ) -> AgentTurnResult:
        """Process one guest message end-to-end (may run several tool rounds)."""
        if not self.provider:
            return AgentTurnResult(
                text="No AI provider configured.", error="no_provider"
            )

        Message.objects.create(
            conversation=conversation, role="user", content=user_message
        )

        messages = [
            {"role": "system", "content": self.system_prompt()}
        ] + self.history(conversation)

        raw = ""
        result = AgentTurnResult()
        for _ in range(max_tool_rounds):
            raw = self.provider.chat(
                messages,
                temperature=0.2,
                max_tokens=1024,
            )
            parsed = self._parse_action(raw)
            if parsed is None:
                result.text = raw.strip() or "..."
                result.error = "unparseable_response"
                break

            action = parsed.get("action", "reply")
            args = parsed.get("args", {}) or {}

            if action == "reply":
                result.text = parsed.get("text", "").strip()
                break

            if action == "check_availability":
                outcome = self._run_check_availability(args)
            elif action == "book":
                outcome = self._run_book(args)
            elif action == "check_in":
                outcome = self._run_check_in(args)
            elif action == "check_out":
                outcome = self._run_check_out(args)
            elif action == "cancel":
                outcome = self._run_cancel(args)
            elif action == "no_show":
                outcome = self._run_no_show(args)
            else:
                outcome = f"Unknown action '{action}'."

            result.action = action
            # Surface the booking id back to the caller so the view can
            # echo it (and the guest can reference it later).
            try:
                tool_outcome = json.loads(outcome) if isinstance(outcome, str) else outcome
                if isinstance(tool_outcome, dict) and tool_outcome.get("booking_id") is not None:
                    result.booking_id = int(tool_outcome["booking_id"])
            except (TypeError, ValueError, KeyError):
                pass
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {
                    "role": "user",
                    "content": f"[tool result]\n{outcome}\nContinue as JSON.",
                }
            )
        else:
            result.text = "..."
            result.error = "tool_loop_exhausted"

        result.raw_response = raw
        if result.text:
            Message.objects.create(
                conversation=conversation, role="assistant", content=result.text
            )
        return result

    # ------------------------------------------------------------------
    # tool implementations
    # ------------------------------------------------------------------
    def _run_check_availability(self, args: dict) -> str:
        try:
            check_in = _dt.date.fromisoformat(args["check_in"])
            check_out = _dt.date.fromisoformat(args["check_out"])
        except (KeyError, ValueError) as exc:
            return f"Invalid dates: {exc}. Expected YYYY-MM-DD."
        guests = int(args.get("guests", 1) or 1)
        summary = availability_summary(
            check_in=check_in, check_out=check_out, guests=guests
        )
        return json.dumps(summary, ensure_ascii=False)

    def _run_book(self, args: dict) -> str:
        try:
            room_type = RoomType.objects.get(name=args["room_type"])
        except RoomType.DoesNotExist:
            return f"Unknown room type '{args.get('room_type')}'."
        except KeyError:
            return "Missing 'room_type'."

        try:
            check_in = _dt.date.fromisoformat(args["check_in"])
            check_out = _dt.date.fromisoformat(args["check_out"])
        except (KeyError, ValueError) as exc:
            return f"Invalid dates: {exc}. Expected YYYY-MM-DD."

        guests = int(args.get("guests", 1) or 1)
        rooms = find_available_rooms(
            room_type=room_type,
            check_in=check_in,
            check_out=check_out,
            guests=guests,
        )
        if not rooms:
            return "No rooms of that type are available for the requested dates."

        client = resolve_client(
            first_name=args.get("first_name", "Guest"),
            last_name=args.get("last_name", ""),
            email=args.get("email", ""),
            phone=args.get("phone", ""),
            language=self.language,
        )
        try:
            booking = create_booking(
                room=rooms[0],
                client=client,
                check_in=check_in,
                check_out=check_out,
                guests=guests,
                source="agent",
            )
        except ValueError as exc:
            return f"Booking failed: {exc}"

        return json.dumps(
            {
                "status": "confirmed",
                "booking_id": booking.id,
                "room": booking.room.room_number,
                "check_in": booking.check_in.isoformat(),
                "check_out": booking.check_out.isoformat(),
                "nights": booking.nights,
                "guests": booking.guests,
                "total_price": str(booking.total_price),
                "currency": booking.currency,
            },
            ensure_ascii=False,
        )

    # ------------------------------------------------------------------
    def _run_check_in(self, args: dict) -> str:
        booking = self._lookup_booking(args)
        if isinstance(booking, str):
            return booking
        try:
            booking = check_in_booking(booking)
        except ValueError as exc:
            return f"Check-in failed: {exc}"
        return json.dumps(
            {
                "status": booking.status,
                "booking_id": booking.id,
                "room": booking.room.room_number,
                "guest": booking.client.full_name,
            },
            ensure_ascii=False,
        )

    def _run_check_out(self, args: dict) -> str:
        booking = self._lookup_booking(args)
        if isinstance(booking, str):
            return booking
        try:
            booking = check_out_booking(booking)
        except ValueError as exc:
            return f"Check-out failed: {exc}"
        return json.dumps(
            {
                "status": booking.status,
                "booking_id": booking.id,
                "room": booking.room.room_number,
                "guest": booking.client.full_name,
            },
            ensure_ascii=False,
        )

    def _run_cancel(self, args: dict) -> str:
        booking = self._lookup_booking(args)
        if isinstance(booking, str):
            return booking
        if booking.status == "cancelled":
            return json.dumps(
                {
                    "status": booking.status,
                    "booking_id": booking.id,
                    "room": booking.room.room_number,
                    "guest": booking.client.full_name,
                    "message": "Already cancelled.",
                },
                ensure_ascii=False,
            )
        booking = cancel_booking(booking)
        return json.dumps(
            {
                "status": booking.status,
                "booking_id": booking.id,
                "room": booking.room.room_number,
                "guest": booking.client.full_name,
            },
            ensure_ascii=False,
        )

    def _run_no_show(self, args: dict) -> str:
        booking = self._lookup_booking(args)
        if isinstance(booking, str):
            return booking
        if booking.status == "no_show":
            return json.dumps(
                {
                    "status": booking.status,
                    "booking_id": booking.id,
                    "room": booking.room.room_number,
                    "guest": booking.client.full_name,
                    "message": "Already marked as no-show.",
                },
                ensure_ascii=False,
            )
        try:
            booking = no_show_booking(booking)
        except ValueError as exc:
            return f"No-show failed: {exc}"
        return json.dumps(
            {
                "status": booking.status,
                "booking_id": booking.id,
                "room": booking.room.room_number,
                "guest": booking.client.full_name,
            },
            ensure_ascii=False,
        )

    def _lookup_booking(self, args: dict):
        """Resolve a booking by id, or by the guest's name."""
        booking_id = args.get("booking_id")
        if booking_id is not None:
            try:
                booking_id = int(booking_id)
            except (TypeError, ValueError):
                return "Invalid 'booking_id'."
            try:
                return Booking.objects.select_related(
                    "room", "client"
                ).get(pk=booking_id)
            except Booking.DoesNotExist:
                return f"Booking {booking_id} not found."
        name = (args.get("guest_name") or "").strip()
        if name:
            parts = name.split()
            qs = Booking.objects.select_related("room", "client")
            if len(parts) == 1:
                qs = qs.filter(
                    Q(client__first_name__icontains=parts[0])
                    | Q(client__last_name__icontains=parts[0])
                )
            else:
                qs = qs.filter(
                    client__first_name__icontains=parts[0],
                    client__last_name__icontains=parts[-1],
                )
            booking = qs.order_by("-created_at").first()
            if booking is None:
                return f"No booking found for '{name}'."
            return booking
        return "Missing 'booking_id' or 'guest_name'."

    # ------------------------------------------------------------------
    @staticmethod
    def _parse_action(raw: str) -> Optional[dict]:
        """Extract the JSON object from the model's reply."""
        text = raw.strip()
        # Strip markdown fences if present.
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fence:
            text = fence.group(1)
        # Otherwise find the outermost JSON object.
        if not text.startswith("{"):
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                text = match.group(0)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        return data


__all__ = ["ReservationAgent", "AgentTurnResult"]