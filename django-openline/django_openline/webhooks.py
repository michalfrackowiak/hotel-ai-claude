"""Receiving OpenLine webhooks: verification, storage, de-duplication, dispatch."""
import hashlib
import json
import logging
import uuid

from django.db import transaction
from django.utils import timezone
from django.utils.module_loading import import_string

from .conf import openline_settings
from .models import Call, WebhookEvent
from .parsing import extract_call_fields, extract_event_key, extract_event_type
from .signals import webhook_received
from .signing import verify_signature

logger = logging.getLogger("django_openline.webhooks")


def verify_request(request):
    """True if the request is an authentic OpenLine delivery. Fails closed."""
    verifier = openline_settings.WEBHOOK_VERIFIER
    if verifier:
        if isinstance(verifier, str):
            verifier = import_string(verifier)
        return bool(verifier(request))

    secret = openline_settings.WEBHOOK_SECRET
    if not secret:
        return bool(openline_settings.ALLOW_UNSIGNED_WEBHOOKS)

    for header in openline_settings.WEBHOOK_SIGNATURE_HEADERS:
        value = request.headers.get(header)
        if value:
            return verify_signature(secret, request.body, value)
    return False


def _event_key(payload, raw_body):
    if not openline_settings.DEDUPLICATE_EVENTS:
        return uuid.uuid4().hex
    key = extract_event_key(payload)
    if key and len(key) <= 128:
        return key
    return hashlib.sha256(raw_body).hexdigest()


def process_webhook(raw_body):
    """Store a verified delivery, sync its Call, and fire ``webhook_received``.

    Returns ``(event, created)``; ``created`` is False for a duplicate delivery.
    Raises ``ValueError`` if the body is not a JSON object.
    """
    try:
        payload = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Request body is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")

    event_type = extract_event_type(payload)
    with transaction.atomic():
        event, created = WebhookEvent.objects.get_or_create(
            event_key=_event_key(payload, raw_body),
            defaults={"event_type": event_type[:128], "payload": payload},
        )
        if not created:
            return event, False
        fields = extract_call_fields(payload)
        if "openline_id" in fields:
            event.call = Call.objects.upsert_from_fields(
                fields, payload=payload, event=event_type
            )
            event.save(update_fields=["call"])

    dispatch_event(event)
    return event, True


def _receiver_name(receiver):
    module = getattr(receiver, "__module__", "")
    name = getattr(receiver, "__qualname__", repr(receiver))
    return f"{module}.{name}" if module else name


def dispatch_event(event):
    """Send ``webhook_received`` for ``event``; used for first delivery and replays.

    A failing receiver never fails the webhook response (which would make
    OpenLine retry a delivery that is already stored). Its error is logged and
    saved on the event; replay the event from the admin once it is fixed.
    """
    responses = webhook_received.send_robust(
        sender=WebhookEvent, event=event, call=event.call, payload=event.payload
    )
    errors = [
        f"{_receiver_name(receiver)}: {response!r}"
        for receiver, response in responses
        if isinstance(response, Exception)
    ]
    for line in errors:
        logger.error("OpenLine webhook receiver failed: %s", line)
    event.error = "\n".join(errors)
    event.processed_at = timezone.now()
    event.save(update_fields=["error", "processed_at"])
    return errors
