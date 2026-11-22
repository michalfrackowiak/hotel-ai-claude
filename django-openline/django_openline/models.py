from django.db import models

from .parsing import extract_call_fields


class CallManager(models.Manager):
    def upsert_from_fields(self, fields, *, payload=None, event=""):
        """Create or update a Call from parsed fields.

        Only non-empty values overwrite existing columns, so a sparse webhook
        never wipes data (``reference``, ``metadata``, ...) set elsewhere.
        """
        fields = {k: v for k, v in fields.items() if v not in (None, "", {})}
        openline_id = fields.pop("openline_id")
        if payload:
            fields["last_payload"] = payload
        if event:
            fields["last_event"] = event

        for name, value in list(fields.items()):  # never overflow a CharField
            max_length = getattr(self.model._meta.get_field(name), "max_length", None)
            if max_length and isinstance(value, str):
                fields[name] = value[:max_length]

        call, created = self.get_or_create(openline_id=openline_id, defaults=fields)
        if created:
            return call
        changed = []
        for name, value in fields.items():
            if getattr(call, name) != value:
                setattr(call, name, value)
                changed.append(name)
        if changed:
            call.save(update_fields=changed + ["updated_at"])
        return call


class Call(models.Model):
    """A call known to OpenLine, kept in sync by REST responses and webhooks."""

    class Direction(models.TextChoices):
        INBOUND = "inbound", "Inbound"
        OUTBOUND = "outbound", "Outbound"
        UNKNOWN = "unknown", "Unknown"

    openline_id = models.CharField(max_length=128, unique=True)
    device_id = models.CharField(max_length=128, blank=True, db_index=True)
    direction = models.CharField(
        max_length=16, choices=Direction.choices, default=Direction.UNKNOWN
    )
    from_number = models.CharField(max_length=64, blank=True)
    to_number = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=64, blank=True, db_index=True)
    last_event = models.CharField(max_length=128, blank=True)
    stream_url = models.CharField(max_length=512, blank=True)
    # Your own tag, e.g. "order:1234", so you can find the call from your domain objects.
    reference = models.CharField(max_length=255, blank=True, db_index=True)
    # Free-form data stored only in your database; never sent to OpenLine.
    metadata = models.JSONField(default=dict, blank=True)
    # The most recent webhook / API payload, verbatim.
    last_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CallManager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.openline_id} ({self.direction}, {self.status or 'no status'})"

    # -- actions: thin wrappers around the REST client ----------------------

    def _client(self):
        from .services import get_client  # lazy: avoids an import cycle

        return get_client()

    def answer(self, **extra):
        return self._client().answer(self.openline_id, **extra)

    def reject(self, **extra):
        return self._client().reject(self.openline_id, **extra)

    def hangup(self):
        return self._client().hangup(self.openline_id)

    def hold(self, on=True):
        return self._client().hold(self.openline_id, on=on)

    def send_dtmf(self, digits):
        return self._client().send_dtmf(self.openline_id, digits)

    def refresh_from_api(self):
        """Pull the call's current state from OpenLine into this row."""
        data = self._client().get_call(self.openline_id)
        fields = extract_call_fields(data, top_level_id_is_call=True)
        fields["openline_id"] = self.openline_id
        type(self).objects.upsert_from_fields(
            fields, payload=data if isinstance(data, dict) else None
        )
        self.refresh_from_db()
        return self


class WebhookEvent(models.Model):
    """Every webhook delivery received, kept for audit, de-duplication and replay."""

    event_key = models.CharField(max_length=128, unique=True)
    event_type = models.CharField(max_length=128, blank=True, db_index=True)
    call = models.ForeignKey(
        Call,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="events",
    )
    payload = models.JSONField(default=dict)
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self):
        return f"{self.event_type or 'event'} @ {self.received_at:%Y-%m-%d %H:%M:%S}"
