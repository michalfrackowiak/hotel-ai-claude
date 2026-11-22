import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Call",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("openline_id", models.CharField(max_length=128, unique=True)),
                ("device_id", models.CharField(blank=True, db_index=True, max_length=128)),
                (
                    "direction",
                    models.CharField(
                        choices=[("inbound", "Inbound"), ("outbound", "Outbound"), ("unknown", "Unknown")],
                        default="unknown",
                        max_length=16,
                    ),
                ),
                ("from_number", models.CharField(blank=True, max_length=64)),
                ("to_number", models.CharField(blank=True, max_length=64)),
                ("status", models.CharField(blank=True, db_index=True, max_length=64)),
                ("last_event", models.CharField(blank=True, max_length=128)),
                ("stream_url", models.CharField(blank=True, max_length=512)),
                ("reference", models.CharField(blank=True, db_index=True, max_length=255)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("last_payload", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="WebhookEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_key", models.CharField(max_length=128, unique=True)),
                ("event_type", models.CharField(blank=True, db_index=True, max_length=128)),
                ("payload", models.JSONField(default=dict)),
                ("received_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                ("error", models.TextField(blank=True)),
                (
                    "call",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="events",
                        to="openline.call",
                    ),
                ),
            ],
            options={
                "ordering": ["-received_at"],
            },
        ),
    ]
