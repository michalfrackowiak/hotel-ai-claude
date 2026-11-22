"""Access to the ``OPENLINE`` dict in Django settings, with defaults."""
from django.conf import settings as django_settings

DEFAULTS = {
    # --- connection ---
    "BASE_URL": "",  # e.g. "https://openline.example.com" (your OpenLine server)
    "API_KEY": "",  # created in the OpenLine console under Settings
    "TIMEOUT": 15,  # seconds, per request
    "VERIFY_TLS": True,
    # Per-endpoint overrides: {"hangup": ("POST", "/v1/calls/{call_id}/hangup")}
    "ENDPOINTS": {},
    # --- call defaults ---
    "DEFAULT_DEVICE_ID": None,  # e.g. "phone-7f3a91c2"
    "DEFAULT_STREAM_URL": None,  # wss:// URL of your agent; attached to every outgoing call
    "DEFAULT_STREAM_FORMAT": "twilio",
    # --- webhooks ---
    "WEBHOOK_SECRET": "",  # the signing secret OpenLine uses for HMAC-SHA256
    # Headers searched (in order) for the signature. Add yours if it is not listed.
    "WEBHOOK_SIGNATURE_HEADERS": (
        "X-OpenLine-Signature",
        "X-Signature",
        "X-Webhook-Signature",
        "X-Hub-Signature-256",
    ),
    # Dotted path (or callable) ``verifier(request) -> bool`` replacing the default check.
    "WEBHOOK_VERIFIER": None,
    # Accept webhooks when no secret is configured. Leave False outside local development.
    "ALLOW_UNSIGNED_WEBHOOKS": False,
    # Ignore a delivery whose event id (or identical body) was already processed.
    "DEDUPLICATE_EVENTS": True,
}


class _Settings:
    def _user(self):
        value = getattr(django_settings, "OPENLINE", None)
        return value if isinstance(value, dict) else {}

    def __getattr__(self, name):
        if name.startswith("_") or name not in DEFAULTS:
            raise AttributeError(f"Unknown OpenLine setting: {name}")
        return self._user().get(name, DEFAULTS[name])


openline_settings = _Settings()
