"""HTTP client for the OpenLine REST API. Pure Python (requests), no Django."""
from __future__ import annotations

import re
from typing import Any, Dict, Mapping, Optional
from urllib.parse import quote

import requests

from . import __version__
from .exceptions import OpenLineAPIError, OpenLineConfigError, OpenLineConnectionError

__all__ = ["OpenLineClient", "DEFAULT_ENDPOINTS", "stream_config"]

# name -> (HTTP method, path template).
#
# Only ``create_call`` (POST /v1/calls) is shown in the OpenLine README. The other
# paths follow the obvious REST convention but have NOT been checked against the
# API reference. Run ``manage.py openline_check`` to probe them, and override any
# that differ via ``OPENLINE["ENDPOINTS"]`` in your Django settings.
DEFAULT_ENDPOINTS: Dict[str, tuple] = {
    "create_call": ("POST", "/v1/calls"),
    "list_calls": ("GET", "/v1/calls"),
    "get_call": ("GET", "/v1/calls/{call_id}"),
    "answer": ("POST", "/v1/calls/{call_id}/answer"),
    "reject": ("POST", "/v1/calls/{call_id}/reject"),
    "hangup": ("POST", "/v1/calls/{call_id}/hangup"),
    "hold": ("POST", "/v1/calls/{call_id}/hold"),
    "dtmf": ("POST", "/v1/calls/{call_id}/dtmf"),
    "call_events": ("GET", "/v1/calls/{call_id}/events"),
    "transcript": ("GET", "/v1/calls/{call_id}/transcript"),
    "list_devices": ("GET", "/v1/devices"),
}

_DTMF_RE = re.compile(r"^[0-9A-Da-d*#]+$")


def stream_config(url: str, format: str = "twilio") -> Dict[str, str]:
    """Build the ``stream`` object for create_call.

    ``format="twilio"`` makes OpenLine speak Twilio Media Streams to your WebSocket
    (so Pipecat and other Twilio-ready agents work unchanged). For raw 16 kHz PCM
    pass the format name that the OpenLine docs give for it.
    """
    return {"url": url, "format": format}


class OpenLineClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 15,
        verify: bool = True,
        endpoints: Optional[Mapping[str, Any]] = None,
        session: Optional[requests.Session] = None,
    ):
        if not base_url:
            raise OpenLineConfigError("OPENLINE['BASE_URL'] is not set.")
        if not api_key:
            raise OpenLineConfigError("OPENLINE['API_KEY'] is not set.")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.verify = verify
        self.session = session or requests.Session()
        self.endpoints = dict(DEFAULT_ENDPOINTS)
        for name, spec in (endpoints or {}).items():
            if not (isinstance(spec, (tuple, list)) and len(spec) == 2):
                raise OpenLineConfigError(
                    f"OPENLINE['ENDPOINTS'][{name!r}] must be a (method, path) pair."
                )
            self.endpoints[name] = (str(spec[0]).upper(), str(spec[1]))

    # -- low level ---------------------------------------------------------

    def request(
        self,
        name: str,
        *,
        path_params: Optional[Mapping[str, Any]] = None,
        params: Optional[Mapping[str, Any]] = None,
        json_body: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        """Call a named endpoint and return the decoded JSON body."""
        try:
            method, path = self.endpoints[name]
        except KeyError:
            raise OpenLineConfigError(f"Unknown OpenLine endpoint {name!r}.") from None
        quoted = {k: quote(str(v), safe="") for k, v in (path_params or {}).items()}
        url = self.base_url + path.format(**quoted)

        try:
            response = self.session.request(
                method,
                url,
                params=params,
                json=json_body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Accept": "application/json",
                    "User-Agent": f"django-openline/{__version__}",
                },
                timeout=self.timeout,
                verify=self.verify,
            )
        except requests.RequestException as exc:
            raise OpenLineConnectionError(f"{method} {url} failed: {exc}") from exc

        try:
            body = response.json() if response.content else {}
        except ValueError:
            body = {"raw": response.text}

        if response.status_code >= 400:
            raise OpenLineAPIError(response.status_code, self._error_message(body), body)
        return body

    @staticmethod
    def _error_message(body: Any) -> str:
        if isinstance(body, dict):
            for key in ("error", "message", "detail"):
                value = body.get(key)
                if isinstance(value, str):
                    return value
                if isinstance(value, dict) and isinstance(value.get("message"), str):
                    return value["message"]
            if isinstance(body.get("raw"), str):
                return body["raw"][:200]
        return ""

    # -- calls -------------------------------------------------------------

    def create_call(
        self,
        to: str,
        device_id: Optional[str] = None,
        stream: Optional[Mapping[str, Any]] = None,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        body: Dict[str, Any] = {"to": to}
        if device_id:
            body["deviceId"] = device_id
        if stream:
            body["stream"] = dict(stream)
        if extra:
            body.update(extra)
        return self.request("create_call", json_body=body)

    def list_calls(self, **params: Any) -> Any:
        return self.request("list_calls", params=params or None)

    def get_call(self, call_id: str) -> Any:
        return self.request("get_call", path_params={"call_id": call_id})

    def answer(self, call_id: str, **extra: Any) -> Any:
        return self.request("answer", path_params={"call_id": call_id}, json_body=extra or None)

    def reject(self, call_id: str, **extra: Any) -> Any:
        return self.request("reject", path_params={"call_id": call_id}, json_body=extra or None)

    def hangup(self, call_id: str) -> Any:
        return self.request("hangup", path_params={"call_id": call_id})

    def hold(self, call_id: str, on: bool = True) -> Any:
        return self.request("hold", path_params={"call_id": call_id}, json_body={"hold": bool(on)})

    def send_dtmf(self, call_id: str, digits: str) -> Any:
        if not isinstance(digits, str) or not _DTMF_RE.match(digits):
            raise ValueError("digits must be a non-empty string of 0-9, A-D, * or #")
        return self.request("dtmf", path_params={"call_id": call_id}, json_body={"digits": digits})

    def call_events(self, call_id: str) -> Any:
        return self.request("call_events", path_params={"call_id": call_id})

    def transcript(self, call_id: str) -> Any:
        return self.request("transcript", path_params={"call_id": call_id})

    # -- devices -----------------------------------------------------------

    def list_devices(self) -> Any:
        return self.request("list_devices")
