from functools import wraps

from django.dispatch import Signal

# Sent once for every new (non-duplicate) webhook delivery, after it has been stored.
# Receivers get: sender=WebhookEvent, event=<WebhookEvent>, call=<Call or None>, payload=<dict>
webhook_received = Signal()


def on_event(*event_types):
    """Decorator: run a handler only for the given webhook event types.

        @on_event("call.answered", "call.ended")
        def handle(event, call, **kwargs):
            ...

    With no arguments the handler runs for every event. The handler is
    registered immediately; import the module that defines it from
    ``AppConfig.ready()`` so registration happens at startup.
    """

    def decorator(func):
        @wraps(func)
        def receiver(sender, event, **kwargs):
            if not event_types or event.event_type in event_types:
                return func(event=event, **kwargs)

        webhook_received.connect(
            receiver,
            weak=False,
            dispatch_uid=f"openline.on_event.{func.__module__}.{func.__qualname__}",
        )
        return func

    return decorator
