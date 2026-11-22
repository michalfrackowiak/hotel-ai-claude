import re

from .client import OpenLineClient, stream_config
from .conf import openline_settings
from .exceptions import OpenLineError
from .models import Call
from .parsing import extract_call_fields

E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


def get_client():
    """Build an OpenLineClient from ``settings.OPENLINE``."""
    return OpenLineClient(
        base_url=openline_settings.BASE_URL,
        api_key=openline_settings.API_KEY,
        timeout=openline_settings.TIMEOUT,
        verify=openline_settings.VERIFY_TLS,
        endpoints=openline_settings.ENDPOINTS,
    )


def place_call(
    to,
    *,
    device_id=None,
    stream_url=None,
    stream_format=None,
    reference="",
    metadata=None,
    extra=None,
    client=None,
):
    """Place an outgoing call and record it as a ``Call``.

    ``device_id``, ``stream_url`` and ``stream_format`` fall back to the
    ``DEFAULT_*`` values in ``settings.OPENLINE``. When a stream URL is present,
    OpenLine connects the answered call to that WebSocket.
    """
    if not E164_RE.match(to or ""):
        raise ValueError("`to` must be an E.164 number such as +48123456789")

    client = client or get_client()
    device_id = device_id or openline_settings.DEFAULT_DEVICE_ID
    stream_url = stream_url or openline_settings.DEFAULT_STREAM_URL
    stream = None
    if stream_url:
        stream = stream_config(stream_url, stream_format or openline_settings.DEFAULT_STREAM_FORMAT)

    data = client.create_call(to=to, device_id=device_id, stream=stream, extra=extra)

    fields = extract_call_fields(data, top_level_id_is_call=True)
    if "openline_id" not in fields:
        raise OpenLineError(f"OpenLine did not return a call id: {data!r}")
    fields["direction"] = "outbound"
    fields.setdefault("to_number", to)
    if device_id:
        fields.setdefault("device_id", device_id)
    if stream:
        fields["stream_url"] = stream["url"]
    if reference:
        fields["reference"] = reference
    if metadata:
        fields["metadata"] = metadata
    # A webhook can beat this response; the response status is then the older one.
    if Call.objects.filter(openline_id=fields["openline_id"]).exists():
        fields.pop("status", None)

    return Call.objects.upsert_from_fields(fields, payload=data if isinstance(data, dict) else None)
