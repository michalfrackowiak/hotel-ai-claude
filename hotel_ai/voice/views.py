"""Voice agent API endpoints."""

from __future__ import annotations

import re
import uuid

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from hotel_ai.ai_providers.models import Conversation, Message

from .conversation import VoiceConversation


@api_view(["POST"])
@permission_classes([AllowAny])
def voice_turn(request):
    """One voice turn: base64 audio in, base64 audio out."""
    data = request.data or {}
    session_key = data.get("session_key") or uuid.uuid4().hex
    language = data.get("language", "en")
    audio_b64 = data.get("audio_b64") or ""
    mime = data.get("mime", "audio/webm")

    vc = VoiceConversation(session_key=session_key, language=language)
    result = vc.turn(audio_b64, mime=mime)
    result["session_key"] = session_key
    return Response(result)


@api_view(["GET"])
@permission_classes([AllowAny])
def voice_history(request):
    """Return transcript history for a given session_key."""
    session_key = request.GET.get("session_key", "").strip()
    limit = int(request.GET.get("limit", 50))
    if not session_key:
        return Response({"messages": [], "booking_id": None})

    try:
        conv = Conversation.objects.prefetch_related("messages").get(
            session_key=session_key, channel="voice"
        )
    except Conversation.DoesNotExist:
        return Response({"messages": [], "booking_id": None})

    msgs = list(
        conv.messages.filter(role__in=("user", "assistant"))
        .order_by("-created_at")[:limit]
    )
    msgs.reverse()

    # Look for a booking_id in recent assistant messages (agent returns JSON
    # snippets like '{"status":"confirmed","booking_id":7,...}').
    booking_id = None
    for m in reversed(msgs):
        match = re.search(r'"booking_id"\s*:\s*(\d+)', m.content)
        if match:
            booking_id = int(match.group(1))
            break

    return Response({
        "session_key": session_key,
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
            }
            for m in msgs
        ],
        "booking_id": booking_id,
    })


@api_view(["GET"])
@permission_classes([AllowAny])
def call_page(request):
    """Render the browser call interface."""
    from django.shortcuts import render

    return render(
        request, "voice/call.html", {"language": request.GET.get("lang", "en")}
    )