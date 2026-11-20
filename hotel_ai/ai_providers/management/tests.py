"""Tests for the AI provider seed command."""
from __future__ import annotations

import pytest

from django.core.management import call_command
from django.test import override_settings

from hotel_ai.ai_providers.models import AIProvider, AgentSettings


@pytest.mark.django_db
def test_seed_providers_creates_ollama_provider():
    AIProvider.objects.all().delete()
    AgentSettings.objects.all().delete()
    call_command("seed_providers")
    provider = AIProvider.objects.get(name="local-ollama")
    assert provider.provider_type == "ollama"
    assert provider.model == "qwen2.5:3b"
    assert provider.is_active is True
    assert provider.is_default is True


@pytest.mark.django_db
def test_seed_providers_creates_agent_settings_for_both_languages():
    AIProvider.objects.all().delete()
    AgentSettings.objects.all().delete()
    call_command("seed_providers")
    assert AgentSettings.objects.filter(language="en", is_active=True).exists()
    assert AgentSettings.objects.filter(language="pl", is_active=True).exists()


@pytest.mark.django_db
def test_seed_providers_updates_existing_provider_url():
    provider = AIProvider.objects.create(
        name="local-ollama",
        provider_type="ollama",
        model="qwen2.5:3b",
        base_url="http://old:11434",
        is_active=True,
        is_default=True,
    )
    with override_settings(OLLAMA_BASE_URL="http://new-host:11434"):
        call_command("seed_providers")
    provider.refresh_from_db()
    assert provider.base_url == "http://new-host:11434"


@pytest.mark.django_db
def test_seed_providers_uses_default_url_when_unset():
    AIProvider.objects.all().delete()
    AgentSettings.objects.all().delete()
    call_command("seed_providers")
    provider = AIProvider.objects.get(name="local-ollama")
    assert provider.base_url == "http://localhost:11434"


@pytest.mark.django_db
def test_seed_providers_reassigns_existing_agent_settings():
    provider = AIProvider.objects.create(
        name="local-ollama",
        provider_type="ollama",
        model="qwen2.5:3b",
        base_url="http://localhost:11434",
        is_active=True,
        is_default=True,
    )
    other = AIProvider.objects.create(
        name="other",
        provider_type="openai",
        model="gpt-4o-mini",
        api_key="sk-test",
        is_active=False,
        is_default=False,
    )
    AgentSettings.objects.create(language="en", provider=other, is_active=False)
    AgentSettings.objects.create(language="pl", provider=other, is_active=False)
    call_command("seed_providers")
    for lang in ("en", "pl"):
        setting = AgentSettings.objects.get(language=lang)
        assert setting.provider == provider
        assert setting.is_active is True


@pytest.mark.django_db
def test_seed_providers_is_idempotent():
    call_command("seed_providers")
    call_command("seed_providers")
    assert AIProvider.objects.filter(name="local-ollama").count() == 1
    assert AgentSettings.objects.filter(language="en").count() == 1
    assert AgentSettings.objects.filter(language="pl").count() == 1