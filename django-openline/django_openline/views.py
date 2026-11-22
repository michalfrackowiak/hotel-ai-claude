import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .webhooks import process_webhook, verify_request

logger = logging.getLogger("django_openline.webhooks")


@csrf_exempt  # authenticated by HMAC signature instead of a CSRF token
@require_POST
def webhook(request):
    if not verify_request(request):
        logger.warning(
            "Rejected OpenLine webhook: missing or invalid signature. Header names received: %s. "
            "If OpenLine uses a different signature header, add it to "
            "OPENLINE['WEBHOOK_SIGNATURE_HEADERS'].",
            ", ".join(sorted(request.headers.keys())),
        )
        return JsonResponse({"error": "invalid signature"}, status=401)
    try:
        event, created = process_webhook(request.body)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"status": "ok" if created else "duplicate", "id": event.pk})
