"""Voice agent API endpoints."""

from __future__ import annotations

import json
import re
import uuid
from typing import Iterator

from django.http import StreamingHttpResponse
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


@api_view(["POST"])
@permission_classes([AllowAny])
def stream_voice_turn(request):
    """SSE streaming voice turn: text tokens arrive incrementally, audio at the end."""
    data = request.data or {}
    session_key = data.get("session_key") or uuid.uuid4().hex
    language = data.get("language", "en")
    audio_b64 = data.get("audio_b64") or ""
    mime = data.get("mime", "audio/webm")

    vc = VoiceConversation(session_key=session_key, language=language)

    def make_events():
        # Stream transcript
        transcript = ""
        def on_token(token: str):
            nonlocal transcript
            transcript += token
            yield sse_event("token", token)

        # We need to capture the stream; the conversation.stream_turn is sync
        # but calls on_token. We'll do a two-pass: first get transcript, then
        # run the rest, then yield tokens one by one.
        # Actually, stream_turn returns after completion. For true streaming,
        # we need to yield tokens as they come. For now, do a sync call and
        # then emit the tokens as SSE events.
        result = vc.stream_turn(audio_b64, on_token=lambda t: None)

        # Yield transcript
        if result.get("transcript"):
            yield sse_event("transcript", result["transcript"])

        # Emit tokens word-by-word for the visual streaming effect
        reply = result.get("text", "") or ""
        words = reply.split()
        for i, word in enumerate(words):
            token = word + (" " if i < len(words) - 1 else "")
            yield sse_event("token", token)

        # Final result event
        yield sse_event("result", json.dumps({
            "action": result.get("action"),
            "booking_id": result.get("booking_id"),
            "error": result.get("error"),
            "session_key": session_key,
        }))

        # Stream audio in chunks
        if result.get("audio_b64"):
            audio_part = result["audio_b64"]
            chunk_size = 4000
            for i in range(0, len(audio_part), chunk_size):
                yield sse_event("audio", audio_part[i:i+chunk_size])
            yield sse_event("audio_end", "")

    return StreamingHttpResponse(
        make_events(),
        content_type="text/event-stream",
        charset="utf-8",
    )


def sse_event(event_name: str, data: str) -> bytes:
    """Encode a single SSE frame."""
    lines = [f"event: {event_name}", f"data: {data}", ""]
    return "\n".join(lines).encode("utf-8") + b"\n"


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