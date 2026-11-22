"""Tolerant extraction of call data from OpenLine payloads. Pure Python.

Webhook bodies and REST responses are stored verbatim, so nothing is lost if a
field is not recognised here. These helpers only lift the handful of fields that
the ``Call`` model keeps as columns, and they accept a few plausible shapes
(flat, ``{"call": {...}}``, ``{"data": {...}}``, ``{"data": {"call": {...}}}``).
"""

__all__ = [
    "extract_event_type",
    "extract_event_key",
    "extract_call_fields",
    "extract_items",
    "normalize_direction",
]

_EVENT_TYPE_KEYS = ("type", "event", "eventType", "event_type")
_EVENT_KEY_KEYS = ("eventId", "event_id", "deliveryId", "delivery_id")
_CALL_ID_KEYS = ("callId", "call_id")

_DIRECTIONS = {
    "inbound": "inbound",
    "incoming": "inbound",
    "outbound": "outbound",
    "outgoing": "outbound",
}


def _text(scope, keys):
    for key in keys:
        value = scope.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    return None


def normalize_direction(value):
    if not isinstance(value, str):
        return None
    return _DIRECTIONS.get(value.strip().lower())


def extract_event_type(payload):
    if not isinstance(payload, dict):
        return ""
    found = _text(payload, _EVENT_TYPE_KEYS)
    if found:
        return found
    nested = payload.get("event")
    if isinstance(nested, dict):
        return _text(nested, ("type", "name")) or ""
    return ""


def extract_event_key(payload):
    """An explicit event/delivery id if the payload carries one, else ``None``.

    A bare ``id`` is deliberately not used: it is usually the *call* id, and
    de-duplicating on it would swallow every later event of the same call.
    """
    if not isinstance(payload, dict):
        return None
    found = _text(payload, _EVENT_KEY_KEYS)
    if found:
        return found
    nested = payload.get("event")
    if isinstance(nested, dict):
        return _text(nested, ("id",) + _EVENT_KEY_KEYS)
    return None


def extract_call_fields(payload, *, top_level_id_is_call=False):
    """Return the subset of Call columns found in ``payload``.

    Keys: openline_id, device_id, direction, from_number, to_number, status.
    Missing values are simply absent from the result.

    ``top_level_id_is_call`` should be True for REST responses (where a bare
    top-level ``id`` is the call id) and False for webhook bodies.
    """
    if not isinstance(payload, dict):
        return {}

    data = payload.get("data") if isinstance(payload.get("data"), dict) else None
    call_obj = payload.get("call") if isinstance(payload.get("call"), dict) else None
    if call_obj is None and data is not None and isinstance(data.get("call"), dict):
        call_obj = data["call"]

    scopes = [s for s in (call_obj, data, payload) if s is not None]

    def pick(keys):
        for scope in scopes:
            found = _text(scope, keys)
            if found:
                return found
        return None

    openline_id = pick(_CALL_ID_KEYS)
    if not openline_id and call_obj is not None:
        openline_id = _text(call_obj, ("id",))
    if not openline_id and top_level_id_is_call:
        openline_id = _text(payload, ("id",))

    fields = {
        "openline_id": openline_id,
        "device_id": pick(("deviceId", "device_id")),
        "direction": normalize_direction(pick(("direction",))),
        "from_number": pick(("from", "fromNumber", "from_number", "caller")),
        "to_number": pick(("to", "toNumber", "to_number", "callee")),
        "status": pick(("status", "state", "callStatus")),
    }
    return {key: value for key, value in fields.items() if value}


def extract_items(data):
    """Return the list inside a list-style response (bare list or wrapped)."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "data", "results", "devices", "calls"):
            if isinstance(data.get(key), list):
                return data[key]
    return []
