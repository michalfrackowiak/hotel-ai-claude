from django.apps import AppConfig


class OpenLineConfig(AppConfig):
    name = "django_openline"
    label = "openline"
    verbose_name = "OpenLine"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import checks  # noqa: F401  (registers the system checks)
