from __future__ import annotations

import logging

from django_openline.services import place_call as openline_place_call

logger = logging.getLogger(__name__)


def initiate_agent_call(to: str, language: str = "en", reference: str = ""):
    """Place an outbound call through OpenLine, routed to the voice agent stream."""
    return openline_place_call(
        to=to,
        reference=reference or "hotel-ai-voice-agent",
        metadata={"language": language},
    )
