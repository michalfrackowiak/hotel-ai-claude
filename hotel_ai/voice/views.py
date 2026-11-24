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
    """SSE streaming voice turn: text tokens arrive incrementally, audio at the end.

    Enhanced with VAD-based optimizations: the server analyzes audio duration
    and can skip STT on very short segments (< ~300ms), reducing perceived latency.
    The client-side already uses Web Audio API frequency analysis for VAD.

    Event stream format (text/event-stream):
    - `event: transcript` — full user transcript
    - `event: token` — individual words as they are produced (visual streaming)
    - `event: result` — final JSON {action, booking_id, error, session_key}
    - `event: audio` — base64 audio chunks (4KB each)
    - `event: audio_end` — marker that audio streaming is complete
    """
    data = request.data or {}
    session_key = data.get("session_key") or uuid.uuid4().hex
    language = data.get("language", "en")
    audio_b64 = data.get("audio_b64") or ""
    mime = data.get("mime", "audio/webm")

    vc = VoiceConversation(session_key=session_key, language=language)

    def make_events():
        # Stream transcript
        transcript = ""

        # Decode the audio for VAD-informed optimization
        try:
            audio = base64.b64decode(audio_b64) if audio_b64 else b""
        except Exception:
            audio = b""

        # VAD optimization: if audio is very short (< ~300ms at 16kHz),
        # skip STT to reduce latency. Rough check: base64 length < 80 chars ≈ < 300ms.
        if audio_b64 and len(audio_b64) < 80:
            # Very short audio — yield empty transcript and result, then audio_end
            yield sse_event("transcript", "")
            yield sse_event("result", json.dumps({
                "action": None,
                "booking_id": None,
                "error": None,
                "session_key": session_key,
            }))
            yield sse_event("audio_end", "")
            return

        if not audio:
            yield sse_event("transcript", "")
            yield sse_event("result", json.dumps({
                "action": None,
                "booking_id": None,
                "error": "no_transcript",
                "session_key": session_key,
            }))
            yield sse_event("audio_end", "")
            return

        # Transcribe the audio
        transcript = vc.transcribe(audio)
        if not transcript:
            yield sse_event("transcript", "")
            yield sse_event("result", json.dumps({
                "action": None,
                "booking_id": None,
                "error": "no_transcript",
                "session_key": session_key,
            }))
            yield sse_event("audio_end", "")
            return

        # Yield transcript event
        yield sse_event("transcript", transcript)

        # Run the agent and capture the result
        result = vc.stream_turn(audio_b64, on_token=lambda token: None)

        # Emit tokens word-by-word for visual streaming effect
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
                yield sse_event("audio", audio_part[i:i + chunk_size])
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