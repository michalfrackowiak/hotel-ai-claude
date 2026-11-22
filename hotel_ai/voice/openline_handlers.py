import logging
from django_openline.signals import on_event
from hotel_ai.voice.conversation import VoiceConversation

logger = logging.getLogger(__name__)

@on_event("call.started", "call.answered")
def handle_openline_call(event, call, **kwargs):
    if not call or not call.openline_id:
        return

    # Use the call's openline_id as the session_key for the conversation
    session_key = call.openline_id

    # Extract language from metadata if possible, defaulting to English
    language = "en"
    if call.metadata and isinstance(call.metadata, dict):
        language = call.metadata.get("language", "en")

    logger.info(f"OpenLine call {session_key} (status={call.status}) received, initializing conversation.")

    # Initialize the conversation
    VoiceConversation(session_key=session_key, language=language)
