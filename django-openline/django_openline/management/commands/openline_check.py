from django.core.management.base import BaseCommand, CommandError
from django.urls import NoReverseMatch, reverse

from django_openline.conf import openline_settings
from django_openline.exceptions import (
    OpenLineAPIError,
    OpenLineConfigError,
    OpenLineConnectionError,
)
from django_openline.parsing import extract_items
from django_openline.services import get_client


class Command(BaseCommand):
    help = "Verify the OPENLINE settings and probe the API endpoints this app uses."

    def handle(self, *args, **options):
        key = openline_settings.API_KEY
        self.stdout.write(f"BASE_URL        : {openline_settings.BASE_URL or '(not set)'}")
        self.stdout.write(f"API_KEY         : {'...' + key[-4:] if key else '(not set)'}")
        self.stdout.write(
            f"WEBHOOK_SECRET  : {'set' if openline_settings.WEBHOOK_SECRET else '(not set)'}"
        )
        try:
            self.stdout.write(f"Webhook route   : {reverse('openline:webhook')}")
        except NoReverseMatch:
            self.stdout.write(
                self.style.WARNING(
                    "Webhook route   : not mounted. Add path('openline/webhook/', "
                    "include('django_openline.urls')) to your urls.py."
                )
            )

        try:
            client = get_client()
        except OpenLineConfigError as exc:
            raise CommandError(str(exc))

        self.stdout.write("")
        fatal = False
        for name, params in (("list_devices", None), ("list_calls", {"limit": 1})):
            method, path = client.endpoints[name]
            label = f"{name:<13} {method} {path}"
            try:
                data = client.request(name, params=params)
            except OpenLineConnectionError as exc:
                self.stdout.write(self.style.ERROR(f"FAIL {label}: {exc}"))
                fatal = True
                break
            except OpenLineAPIError as exc:
                if exc.status_code in (401, 403):
                    self.stdout.write(self.style.ERROR(f"FAIL {label}: {exc.status_code} - check API_KEY"))
                    fatal = True
                elif exc.status_code == 404:
                    self.stdout.write(
                        self.style.WARNING(
                            f"WARN {label}: 404 - this path may differ in your OpenLine version; "
                            f"override it in OPENLINE['ENDPOINTS']['{name}']"
                        )
                    )
                else:
                    self.stdout.write(self.style.WARNING(f"WARN {label}: HTTP {exc.status_code} {exc.message}"))
                continue
            self.stdout.write(self.style.SUCCESS(f"OK   {label}"))
            if name == "list_devices":
                ids = [
                    str(d.get("id") or d.get("deviceId") or "?")
                    for d in extract_items(data)
                    if isinstance(d, dict)
                ]
                self.stdout.write(f"    devices: {', '.join(ids) if ids else 'none paired yet'}")

        if fatal:
            raise CommandError("OpenLine check failed.")
