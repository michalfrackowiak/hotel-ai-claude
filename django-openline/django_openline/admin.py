from django.contrib import admin, messages

from .exceptions import OpenLineError
from .models import Call, WebhookEvent
from .webhooks import dispatch_event


@admin.register(Call)
class CallAdmin(admin.ModelAdmin):
    list_display = (
        "openline_id",
        "direction",
        "from_number",
        "to_number",
        "status",
        "last_event",
        "device_id",
        "reference",
        "updated_at",
    )
    list_filter = ("direction", "status", "device_id")
    search_fields = ("openline_id", "from_number", "to_number", "reference")
    readonly_fields = ("created_at", "updated_at")
    actions = ["refresh_from_openline", "hang_up"]

    @admin.action(description="Refresh selected calls from OpenLine")
    def refresh_from_openline(self, request, queryset):
        done = 0
        for call in queryset:
            try:
                call.refresh_from_api()
                done += 1
            except OpenLineError as exc:
                self.message_user(request, f"{call.openline_id}: {exc}", messages.ERROR)
        if done:
            self.message_user(request, f"Refreshed {done} call(s).")

    @admin.action(description="Hang up selected calls")
    def hang_up(self, request, queryset):
        done = 0
        for call in queryset:
            try:
                call.hangup()
                done += 1
            except OpenLineError as exc:
                self.message_user(request, f"{call.openline_id}: {exc}", messages.ERROR)
        if done:
            self.message_user(request, f"Sent hang-up for {done} call(s).")


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("received_at", "event_type", "call", "processed_at", "has_error")
    list_filter = ("event_type",)
    search_fields = ("event_key", "call__openline_id")
    readonly_fields = [f.name for f in WebhookEvent._meta.fields]
    actions = ["redispatch"]

    def has_add_permission(self, request):
        return False

    @admin.display(boolean=True, description="Handler error")
    def has_error(self, obj):
        return bool(obj.error)

    @admin.action(description="Re-send selected events to signal receivers")
    def redispatch(self, request, queryset):
        failed = 0
        for event in queryset:
            if dispatch_event(event):
                failed += 1
        total = queryset.count()
        if failed:
            self.message_user(
                request, f"{failed} of {total} event(s) still raised errors.", messages.WARNING
            )
        else:
            self.message_user(request, f"Re-dispatched {total} event(s).")
