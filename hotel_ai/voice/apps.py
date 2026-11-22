from django.apps import AppConfig


class VoiceConfig(AppConfig):
    name = "hotel_ai.voice"
    label = "hotel_ai_voice"

    def ready(self):
        from hotel_ai.voice import openline_handlers  # noqa: F401
