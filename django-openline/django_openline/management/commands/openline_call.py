from django.core.management.base import BaseCommand, CommandError

from django_openline.exceptions import OpenLineError
from django_openline.services import place_call


class Command(BaseCommand):
    help = "Place a test call through OpenLine."

    def add_arguments(self, parser):
        parser.add_argument("to", help="Destination number in E.164 format, e.g. +48123456789")
        parser.add_argument("--device", dest="device_id", help="OpenLine device id")
        parser.add_argument("--stream-url", help="wss:// URL of your agent")
        parser.add_argument("--stream-format", help="Stream format (default: from settings, 'twilio')")
        parser.add_argument("--reference", default="", help="Your own tag stored on the Call")

    def handle(self, *args, **options):
        try:
            call = place_call(
                options["to"],
                device_id=options["device_id"],
                stream_url=options["stream_url"],
                stream_format=options["stream_format"],
                reference=options["reference"],
            )
        except (OpenLineError, ValueError) as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(f"Call placed: {call.openline_id} (status: {call.status or 'n/a'})"))
