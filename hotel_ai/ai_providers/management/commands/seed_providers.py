"""Seed the database with default AI provider configurations.

Creates:
- An Ollama provider (local LLM) if no providers exist
- Agent settings for English and Polish languages
"""
from __future__ import annotations

import logging

from django.conf import settings as django_settings
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Seed AI provider and agent settings."

    def handle(self, *args, **options):
        from hotel_ai.ai_providers.models import AIProvider, AgentSettings

        ollama_url = getattr(django_settings, "OLLAMA_BASE_URL", "http://localhost:11434")

        # Create or update the local Ollama provider
        provider, created = AIProvider.objects.get_or_create(
            name="local-ollama",
            defaults={
                "provider_type": "ollama",
                "model": "qwen2.5:3b",
                "base_url": ollama_url,
                "is_active": True,
                "is_default": True,
            },
        )
        if not created:
            provider.base_url = ollama_url
            provider.save(update_fields=["base_url"])
            logger.info("Updated Ollama provider URL to %s", ollama_url)
        else:
            self.stdout.write(self.style.SUCCESS(f"Created Ollama provider: {provider.name}"))

        # Ensure agent settings exist for both languages
        for lang in ("en", "pl"):
            _, created = AgentSettings.objects.get_or_create(
                language=lang,
                defaults={"provider": provider, "is_active": True},
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"Created agent settings for {lang}"))
            else:
                AgentSettings.objects.filter(language=lang).update(provider=provider, is_active=True)

        self.stdout.write(
            self.style.SUCCESS("AI provider seeding complete.")
        )
