"""Admin for AI providers and agent settings."""

from django.contrib import admin

from .models import AgentSettings, AIProvider, Conversation, Message


@admin.register(AIProvider)
class AIProviderAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "provider_type",
        "model",
        "is_active",
        "is_default",
    )
    list_filter = ("provider_type", "is_active", "is_default")
    search_fields = ("name", "model")
    fieldsets = (
        (None, {"fields": ("name", "provider_type", "model", "is_active", "is_default")}),
        ("Credentials", {"fields": ("api_key", "base_url", "timeout_seconds")}),
        ("Extra", {"fields": ("extra_config",)}),
    )

    @admin.display(description="Has API key")
    def has_api_key(self, obj):
        return bool(obj.api_key or obj.effective_api_key())


@admin.register(AgentSettings)
class AgentSettingsAdmin(admin.ModelAdmin):
    list_display = ("language", "provider", "temperature", "max_tokens", "is_active")
    list_filter = ("language", "is_active")
    autocomplete_fields = ("provider", "stt_provider", "tts_provider")


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("session_key", "channel", "language", "updated_at")
    list_filter = ("channel", "language")
    search_fields = ("session_key",)


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("conversation", "role", "content", "created_at")
    list_filter = ("role",)
    search_fields = ("content",)