"""Provider factory and runtime lookup.

Turns an :class:`AIProvider` row (or its config dict) into a concrete
``BaseAIProvider`` instance.  This is the single place the agent, chat and
voice layers touch the provider backends.
"""

from __future__ import annotations

import logging
from typing import Optional

from .base import BaseAIProvider
from .models import AgentSettings, AIProvider

logger = logging.getLogger(__name__)

_PROVIDER_CLASSES = {
    "openai": None,  # imported lazily to keep optional deps optional
    "anthropic": None,
    "ollama": None,
}


def _get_provider_class(provider_type: str):
    cls = _PROVIDER_CLASSES.get(provider_type)
    if cls is not None:
        return cls
    if provider_type == "openai":
        from .openai_provider import OpenAIProvider

        _PROVIDER_CLASSES["openai"] = OpenAIProvider
        return OpenAIProvider
    if provider_type == "anthropic":
        from .anthropic_provider import AnthropicProvider

        _PROVIDER_CLASSES["anthropic"] = AnthropicProvider
        return AnthropicProvider
    if provider_type == "ollama":
        from .ollama_provider import OllamaProvider

        _PROVIDER_CLASSES["ollama"] = OllamaProvider
        return OllamaProvider
    raise ValueError(f"Unknown provider type: {provider_type!r}")


def build_provider(config: dict) -> BaseAIProvider:
    """Instantiate a provider from a config dict."""
    provider_type = config.get("provider_type")
    cls = _get_provider_class(provider_type)
    return cls(config)


def build_provider_from_model(provider: AIProvider) -> BaseAIProvider:
    return build_provider(provider.to_config())


def get_active_provider(language: Optional[str] = None) -> Optional[BaseAIProvider]:
    """Return the provider configured for the reservation agent.

    Falls back to a default provider row, then to a config-driven default
    (``DEFAULT_PROVIDER``) so the system works even before the admin panel
    has been touched.
    """
    from django.conf import settings as dj_settings

    qs = AgentSettings.objects.filter(is_active=True)
    if language:
        qs = qs.filter(language=language)
    agent = qs.first()
    if agent is not None and agent.provider_id:
        return build_provider_from_model(agent.provider)

    # Fallback: any default provider row.
    default = AIProvider.objects.filter(is_default=True, is_active=True).first()
    if default is not None:
        return build_provider_from_model(default)

    # Last-resort: build a synthetic provider from env config.
    provider_type = getattr(dj_settings, "DEFAULT_PROVIDER", "openai")
    model = getattr(dj_settings, "DEFAULT_MODEL", "gpt-4o-mini")
    config = {
        "provider_type": provider_type,
        "model": model,
        "api_key": getattr(dj_settings, f"{provider_type.upper()}_API_KEY", "") or "",
        "base_url": getattr(dj_settings, "OLLAMA_BASE_URL", "") if provider_type == "ollama" else "",
        "timeout_seconds": 30,
        "extra_config": {},
    }
    return build_provider(config)


def get_speech_provider(kind: str, language: Optional[str] = None) -> Optional[BaseAIProvider]:
    """Return the STT or TTS provider configured for the active agent."""
    qs = AgentSettings.objects.filter(is_active=True)
    if language:
        qs = qs.filter(language=language)
    agent = qs.first()
    field = "stt_provider" if kind == "stt" else "tts_provider"
    provider = getattr(agent, field) if agent else None
    if provider is None:
        return None
    return build_provider_from_model(provider)