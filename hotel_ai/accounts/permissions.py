"""Permissions for the guest account system."""
from __future__ import annotations

from rest_framework.permissions import BasePermission, SAFE_METHODS
from rest_framework.request import Request
from rest_framework.views import View


class IsOwnerOrReadOnly(BasePermission):
    """Guests may only mutate resources they own; read is always allowed.

    Bookings created without an owner (via chat/API before account claim) are
    treated as ownerless and remain mutable by anyone so existing clients keep
    exercising the service. Once an owner is attached only that user may mutate.
    Views like RoomBulkUpdateView operate on unowned domain models so we fall
    back to requiring authentication there instead.
    """

    message = "You do not have permission to perform this action."

    def has_permission(self, request: Request, view: View) -> bool:
        if request.method in SAFE_METHODS:
            return True
        # Allow raw mutation endpoints to pass through here; object-level
        # permission will gate them once we resolve the target object.
        # Fallback: require login so bulk-update-like views still protect auth.
        return request.user and request.user.is_authenticated

    def has_object_permission(self, request: Request, view: View, obj) -> bool:
        if request.method in SAFE_METHODS:
            return True
        owner = getattr(obj, "owner", None)
        if owner is None:
            return True
        return owner == request.user


class BookingOwnershipPermission(BasePermission):
    """Object-level check for booking-mutation views.

    Used as the sole permission class on mutation-only views (POST / cancel
    and PATCH / update) so that 404 surfaces for missing bookings rather than
    a spurious 403 from an early has_permission() denial.
    """

    message = "You do not have permission to perform this action."

    def has_permission(self, request: Request, view: View) -> bool:
        # All requests pass the class-level check; object-level ownership
        # (has_object_permission) is the only gate. This preserves the DRF
        # lifecycle where get_object() runs after permission checks, allowing
        # missing bookings to surface as 404 instead of a spurious 403.
        return True

    def has_object_permission(self, request: Request, view: View, obj) -> bool:
        if request.method in SAFE_METHODS:
            return True
        owner = getattr(obj, "owner", None)
        if owner is None:
            return True
        return owner == request.user
