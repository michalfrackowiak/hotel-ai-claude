# django-openline

A drop-in Django app for [OpenLine](https://github.com/michalfrackowiak/openline), the self-hosted voice API that turns an Android phone's SIM into a programmable phone line.

It gives an existing Django project:

- **A REST client** for placing and controlling calls (`place_call()`, `call.hangup()`, ...).
- **A signed webhook endpoint** that verifies OpenLine's HMAC-SHA256 signature, de-duplicates retries, stores every delivery, and keeps a `Call` table in sync.
- **A Django signal** (`webhook_received`) and an `@on_event(...)` decorator, so your own code reacts to calls without touching the plumbing.
- **Admin pages**, **system checks**, and management commands (`openline_check`, `openline_call`, `openline_prune`).

Requires Python 3.9+ and Django 4.2+. The only extra dependency is `requests`.

---

## Read this first: what is verified and what is assumed

This module was written from OpenLine's README. The detailed API reference was not available while building it, so some details are **educated defaults**, not confirmed facts. Everything below is configurable, and `openline_check` helps you confirm them in about a minute.

| | Status |
|---|---|
| `POST /v1/calls` with `Authorization: Bearer <key>` and body `{"deviceId", "to", "stream": {"url", "format"}}` | Confirmed (README example) |
| `"format": "twilio"` for Twilio Media Streams | Confirmed |
| Webhooks are signed with HMAC-SHA256 and retried | Confirmed |
| Paths for get/list calls, answer, reject, hangup, hold, DTMF, events, transcript, devices | **Assumed** (REST convention) |
| Request bodies for `hold` (`{"hold": true}`) and `dtmf` (`{"digits": "12#"}`) | **Assumed** |
| Name of the signature header and its encoding | **Assumed** (see below) |
| Field names inside webhook payloads (`callId`, `type`, `status`, ...) | **Assumed**; parsing is tolerant and the raw payload is always stored |

**How to confirm in practice**

1. `python manage.py openline_check` probes the device and call-list endpoints and tells you which paths 404. Fix any mismatch with `OPENLINE["ENDPOINTS"]`.
2. Point OpenLine's webhook at your endpoint and trigger a call. If the signature header name differs from the defaults, requests are rejected with `401` and the log line lists the header names that did arrive. Add the right one to `WEBHOOK_SIGNATURE_HEADERS`. If signing works differently (for example it includes a timestamp), set `WEBHOOK_VERIFIER` to your own function.
3. Open **Admin → OpenLine → Webhook events** and look at a stored `payload`. If a `Call` column came out blank, the data is still in `last_payload`; tell the parser about it or read it from the payload directly.

The OpenLine docs (linked from its README) have the authoritative API reference.

---

## Install

From the unzipped folder:

```bash
pip install ./django-openline
```

Or add it to `requirements.txt` as a path / git URL, whichever matches how you deploy.

### 1. Add the app

```python
# settings.py
INSTALLED_APPS = [
    ...
    "django_openline",
]

import os

OPENLINE = {
    "BASE_URL": os.environ["OPENLINE_URL"],              # e.g. https://openline.example.com
    "API_KEY": os.environ["OPENLINE_API_KEY"],           # create it in the OpenLine console -> Settings
    "WEBHOOK_SECRET": os.environ["OPENLINE_WEBHOOK_SECRET"],
    "DEFAULT_DEVICE_ID": "phone-7f3a91c2",               # optional
    "DEFAULT_STREAM_URL": "wss://my-agent.example.com/ws",  # optional: attach an AI agent to every outgoing call
}
```

Keep the key and secret in environment variables or your secrets manager, not in source control.

### 2. Mount the webhook

```python
# urls.py
from django.urls import include, path

urlpatterns = [
    ...
    path("openline/webhook/", include("django_openline.urls")),
]
```

### 3. Migrate and check

```bash
python manage.py migrate
python manage.py openline_check
```

### 4. Register the webhook in OpenLine

Give OpenLine the public URL of that route (for example `https://yourapp.example.com/openline/webhook/`) and the same signing secret you put in `WEBHOOK_SECRET`. See the OpenLine docs for where the webhook settings live in its console. Your Django app must be reachable from the OpenLine server.

---

## Usage

### Place a call

```python
from django_openline.services import place_call

call = place_call(
    "+48123456789",
    reference=f"order:{order.pk}",      # your own tag, searchable later
    metadata={"customer_id": 42},        # stored locally only, never sent to OpenLine
)
call.openline_id, call.status
```

`device_id`, `stream_url` and `stream_format` default to your settings; override per call:

```python
place_call("+48123456789", stream_url="wss://agent.example.com/support", stream_format="twilio")
```

Numbers must be E.164 (`+` and country code); anything else raises `ValueError` before any request is made.

### Control a call

```python
call.answer()          # accepts **extra, merged into the request body
call.reject()
call.hold()            # call.hold(on=False) to resume
call.send_dtmf("12#")
call.hangup()
call.refresh_from_api()
```

All methods raise `OpenLineAPIError`, `OpenLineConnectionError` or `OpenLineConfigError` (all subclasses of `OpenLineError`).

### React to events

```python
# myapp/openline_handlers.py
from django_openline.signals import on_event

@on_event("call.ringing")            # event names as OpenLine sends them
def answer_incoming(event, call, **kwargs):
    if call and call.direction == "inbound":
        call.answer()

@on_event()                          # no arguments = every event
def log_everything(event, call, payload, **kwargs):
    ...
```

Import the module once at startup so the handlers register:

```python
# myapp/apps.py
class MyAppConfig(AppConfig):
    name = "myapp"

    def ready(self):
        from . import openline_handlers  # noqa: F401
```

Plain signals work too: `from django_openline.signals import webhook_received`.

Handlers run **synchronously inside the webhook request**. Keep them fast and hand slow work to Celery or similar. A handler that raises never turns the response into an error (that would make OpenLine retry a delivery that is already stored). The exception is logged and saved on the event, and you can replay it from the admin once fixed.

### Without the ORM

If you only want the HTTP client:

```python
from django_openline.client import OpenLineClient, stream_config

client = OpenLineClient("https://openline.example.com", api_key)
client.create_call("+48123456789", "phone-7f3a91c2", stream_config("wss://agent.example/ws"))
```

This module imports nothing from Django.

---

## Models

| Model | Purpose |
|---|---|
| `Call` | One row per OpenLine call: `openline_id`, `device_id`, `direction`, `from_number`, `to_number`, `status`, `last_event`, `stream_url`, your `reference` and `metadata`, and the verbatim `last_payload`. Created or updated by both REST responses and webhooks; sparse webhooks never wipe existing data. |
| `WebhookEvent` | Every delivery received: type, payload, linked `Call`, processing time and any handler error. Doubles as the de-duplication ledger. |

## Settings reference

All keys live in the `OPENLINE` dict. Unknown keys trigger a system-check warning, so typos don't go unnoticed.

| Key | Default | Meaning |
|---|---|---|
| `BASE_URL` | `""` | Your OpenLine server. Use https; the checks warn on plain http to a non-local host. |
| `API_KEY` | `""` | Bearer token for the REST API. |
| `TIMEOUT` | `15` | Seconds per request. |
| `VERIFY_TLS` | `True` | Set `False` only for local development. |
| `ENDPOINTS` | `{}` | Override paths, e.g. `{"hangup": ("POST", "/v1/calls/{call_id}/hangup")}`. Names: `create_call`, `list_calls`, `get_call`, `answer`, `reject`, `hangup`, `hold`, `dtmf`, `call_events`, `transcript`, `list_devices`. |
| `DEFAULT_DEVICE_ID` | `None` | Used when `place_call` gets no `device_id`. |
| `DEFAULT_STREAM_URL` | `None` | WebSocket of your agent, attached to outgoing calls. |
| `DEFAULT_STREAM_FORMAT` | `"twilio"` | Stream format. For raw 16 kHz PCM, use the format name from the OpenLine docs. |
| `WEBHOOK_SECRET` | `""` | HMAC key. **Empty means every webhook is rejected** (fail closed). |
| `WEBHOOK_SIGNATURE_HEADERS` | `X-OpenLine-Signature`, `X-Signature`, `X-Webhook-Signature`, `X-Hub-Signature-256` | Searched in order. The value may be hex, `sha256=<hex>` or base64. |
| `WEBHOOK_VERIFIER` | `None` | Dotted path or callable `verifier(request) -> bool` replacing the default check entirely. |
| `ALLOW_UNSIGNED_WEBHOOKS` | `False` | Accept webhooks when no secret is set. Local development only. |
| `DEDUPLICATE_EVENTS` | `True` | Skip a delivery whose event id (or identical body) was already processed. |

## Management commands

| Command | What it does |
|---|---|
| `openline_check` | Prints the config (key masked), checks the webhook route is mounted, and probes the API. |
| `openline_call +48123456789 [--device ID] [--stream-url URL]` | Places a test call. |
| `openline_prune [--days 30]` | Deletes old `WebhookEvent` rows; `Call` rows are kept. Run it from cron. |

## Security notes

- The webhook view is `csrf_exempt` because it is authenticated by signature instead. Signatures are compared in constant time over the **raw** request body.
- With no secret configured, webhooks are rejected, not silently accepted.
- De-duplication uses an explicit event/delivery id when present, otherwise a hash of the body. It never uses a bare `id`, which is usually the call id and would swallow every later event of that call.
- Header **names** (never values) are logged when a signature is rejected, to help diagnose a mismatch.

## Tests

```bash
python -m unittest discover -s tests -t .   # signing, parsing, REST client: no Django needed
python runtests.py                          # full suite incl. views, models, admin, migrations (needs Django)
```

The full suite includes a `makemigrations --check` test, so schema drift fails CI.

## Limitations

- The REST client is synchronous. Call it from a background worker if request latency matters.
- This app does not host the **agent** side of a stream. OpenLine connects out to *your* WebSocket (Pipecat, a LiveKit worker, Channels consumer, ...).
- SMS, call recording, conferencing and TwiML-style flows are not in OpenLine v1, so they aren't here either.

## Responsible use

You are responsible for the calls you place. Follow local rules on automated calls, disclosing that callers are talking to an AI, and recording consent.

## License

MIT
