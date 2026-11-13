"""Chat agent API - stateless-per-request multi-turn chat over the agent."""

from __future__ import annotations

import uuid

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from hotel_ai.ai_providers.agent import ReservationAgent
from hotel_ai.ai_providers.models import Conversation


@api_view(["POST"])
@permission_classes([AllowAny])
def chat_turn(request):
    """One turn of the chat reservation flow.

    Body: {"session_key": "...", "language": "en|pl", "message": "..."}
    """
    data = request.data or {}
    message = (data.get("message") or "").strip()
    if not message:
        return Response({"detail": "message is required"}, status=400)

    session_key = data.get("session_key") or uuid.uuid4().hex
    language = data.get("language", "en")

    conversation, _ = Conversation.objects.get_or_create(
        session_key=session_key,
        defaults={"channel": "chat", "language": language},
    )
    if conversation.language != language:
        conversation.language = language
        conversation.save(update_fields=["language"])

    agent = ReservationAgent(language=language)
    result = agent.handle_message(conversation, message)

    return Response(
        {
            "session_key": session_key,
            "reply": result.text,
            "action": result.action,
            "booking_id": result.booking_id,
            "error": result.error,
        }
    )