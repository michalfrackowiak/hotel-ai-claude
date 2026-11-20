"""Models for the guest account system.

A guest can authenticate with a username/password or via Google SSO and
then link their existing bookings to their account.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models


class UserProfile(models.Model):
    """Extended profile data for an authenticated guest."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    phone = models.CharField(max_length=32, blank=True, default="")
    language = models.CharField(max_length=8, default="en")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.user.get_username()

    def link_client(self, client) -> None:
        """Attach the matching :class:`Client` record to this profile."""
        if client is None:
            return
        self.phone = client.phone or self.phone
        self.language = client.language or self.language
        self.save(update_fields=["phone", "language"])


def create_user_profile(sender, instance, created, **kwargs):
    """Auto-create a profile when a User is created."""
    if created:
        UserProfile.objects.create(user=instance)


from django.db.models.signals import post_save  # noqa: E402

post_save.connect(create_user_profile, sender=settings.AUTH_USER_MODEL)