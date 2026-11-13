"""Serializers for the AI provider management API."""

from rest_framework import serializers

from .models import AgentSettings, AIProvider


class AIProviderSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIProvider
        fields = (
            "id",
            "name",
            "provider_type",
            "model",
            "api_key",
            "base_url",
            "timeout_seconds",
            "is_active",
            "is_default",
            "extra_config",
        )
        extra_kwargs = {"api_key": {"write_only": True, "required": False, "allow_blank": True}}


class AgentSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentSettings
        fields = (
            "id",
            "provider",
            "language",
            "stt_provider",
            "tts_provider",
            "system_prompt",
            "temperature",
            "max_tokens",
            "is_active",
        )


__all__ = ["AIProviderSerializer", "AgentSettingsSerializer"]