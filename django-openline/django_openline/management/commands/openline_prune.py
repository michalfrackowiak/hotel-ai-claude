from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from django_openline.models import WebhookEvent


class Command(BaseCommand):
    help = "Delete stored webhook events older than N days (Call rows are kept)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30)

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=options["days"])
        deleted, _ = WebhookEvent.objects.filter(received_at__lt=cutoff).delete()
        self.stdout.write(f"Deleted {deleted} webhook event(s) older than {options['days']} days.")
