from urllib.parse import urlparse

from django.conf import settings
from django.core.checks import Warning, register

from .conf import DEFAULTS, openline_settings

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@register()
def check_openline_settings(app_configs, **kwargs):
    issues = []
    user = getattr(settings, "OPENLINE", None)

    if not isinstance(user, dict):
        return [
            Warning(
                "The OPENLINE setting is missing.",
                hint="Add OPENLINE = {'BASE_URL': ..., 'API_KEY': ..., 'WEBHOOK_SECRET': ...} to settings.",
                id="openline.W001",
            )
        ]

    unknown = sorted(set(user) - set(DEFAULTS))
    if unknown:
        issues.append(
            Warning(
                f"Unknown OPENLINE setting(s): {', '.join(unknown)}.",
                hint="Check for typos. Valid keys: " + ", ".join(sorted(DEFAULTS)) + ".",
                id="openline.W002",
            )
        )
    if not openline_settings.BASE_URL or not openline_settings.API_KEY:
        issues.append(
            Warning(
                "OPENLINE['BASE_URL'] and OPENLINE['API_KEY'] must both be set to call the API.",
                id="openline.W003",
            )
        )
    if not openline_settings.WEBHOOK_SECRET and not openline_settings.ALLOW_UNSIGNED_WEBHOOKS:
        issues.append(
            Warning(
                "OPENLINE['WEBHOOK_SECRET'] is empty, so every webhook will be rejected.",
                hint="Set the signing secret configured in OpenLine.",
                id="openline.W004",
            )
        )
    parsed = urlparse(openline_settings.BASE_URL or "")
    if parsed.scheme == "http" and parsed.hostname not in _LOCAL_HOSTS:
        issues.append(
            Warning(
                "OPENLINE['BASE_URL'] uses plain http, so the API key is sent unencrypted.",
                hint="Use https (for example via Tailscale Funnel or a Cloudflare Tunnel).",
                id="openline.W005",
            )
        )
    return issues
