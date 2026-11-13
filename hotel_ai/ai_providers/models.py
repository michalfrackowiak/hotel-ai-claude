"""AI provider configuration model.

A provider is a named backend (OpenAI, Anthropic, Ollama) with its own
credentials and model.  The :class:`AgentSettings` row selects which provider
the reservation agent uses at runtime, so swapping backends is a config change.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


PROVIDER_CHOICES = (
    ("openai", "OpenAI"),
    ("anthropic", "Anthropic"),
    ("ollama", "Ollama (self-hosted)"),
)


class AIProvider(models.Model):
    """A configured AI backend."""

    name = models.CharField(max_length=64, unique=True, help_text="Unique slug, e.g. 'openai-prod'.")
    provider_type = models.CharField(max_length=16, choices=PROVIDER_CHOICES)
    model = models.CharField(max_length=64, help_text="Model id, e.g. 'gpt-4o-mini' or 'llama3.1:8b'.")
    api_key = models.CharField(max_length=512, blank=True, default="", help_text="Leave blank to read from env.")
    base_url = models.CharField(max_length=256, blank=True, default="", help_text="Override API base URL (Ollama/custom).")
    timeout_seconds = models.PositiveIntegerField(default=30)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False, help_text="Default provider for the reservation agent.")
    extra_config = models.JSONField(blank=True, default=dict, help_text="Free-form provider options.")

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.name} ({self.provider_type}/{self.model})"

    def effective_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        env_map = {
            "openai": settings.OPENAI_API_KEY,
            "anthropic": settings.ANTHROPIC_API_KEY,
        }
        return env_map.get(self.provider_type, "")

    def effective_base_url(self) -> str:
        if self.base_url:
            return self.base_url
        if self.provider_type == "ollama":
            return settings.OLLAMA_BASE_URL
        return ""

    def to_config(self) -> dict:
        return {
            "provider_type": self.provider_type,
            "model": self.model,
            "api_key": self.effective_api_key(),
            "base_url": self.effective_base_url(),
            "timeout_seconds": self.timeout_seconds,
            "extra_config": self.extra_config or {},
        }


class AgentSettings(models.Model):
    """Runtime settings for the reservation agent."""

    LANGUAGE_CHOICES = (("en", "English"), ("pl", "Polish"))

    provider = models.ForeignKey(
        AIProvider,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agent_settings",
    )
    language = models.CharField(max_length=2, choices=LANGUAGE_CHOICES, default="en")
    stt_provider = models.ForeignKey(
        AIProvider,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stt_settings",
        help_text="Speech-to-text backend.",
    )
    tts_provider = models.ForeignKey(
        AIProvider,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tts_settings",
        help_text="Text-to-speech backend.",
    )
    system_prompt = models.TextField(
        blank=True,
        default="",
        help_text="Override the default reservation-agent system prompt.",
    )
    temperature = models.FloatField(default=0.2)
    max_tokens = models.PositiveIntegerField(default=1024)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["language"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        prov = self.provider.name if self.provider_id else "(none)"
        return f"AgentSettings[{self.language}] -> {prov}"


class Conversation(models.Model):
    """A chat or voice conversation with the reservation agent."""

    CHANNEL_CHOICES = (("chat", "Chat"), ("voice", "Voice"))

    session_key = models.CharField(max_length=128, db_index=True)
    channel = models.CharField(max_length=8, choices=CHANNEL_CHOICES)
    language = models.CharField(max_length=2, default="en")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.channel}/{self.language}/{self.session_key}"


class Message(models.Model):
    """A single turn in a conversation."""

    ROLE_CHOICES = (("system", "system"), ("user", "user"), ("assistant", "assistant"), ("tool", "tool"))

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    role = models.CharField(max_length=16, choices=ROLE_CHOICES)
    content = models.TextField()
    metadata = models.JSONField(blank=True, default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.role}: {self.content[:60]}"