"""Voice agent API endpoints."""

from __future__ import annotations

import uuid

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

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
def call_page(request):
    """Render the browser call interface."""
    from django.shortcuts import render

    return render(request, "voice/call.html", {"language": request.GET.get("lang", "en")})