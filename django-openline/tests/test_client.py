"""Exercises OpenLineClient against a real local HTTP server (no mocks)."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from django_openline.client import OpenLineClient, stream_config
from django_openline.exceptions import (
    OpenLineAPIError,
    OpenLineConfigError,
    OpenLineConnectionError,
)


class Handler(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *args):
        pass

    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        Handler.seen.append({
            "method": self.command,
            "path": self.path,
            "auth": self.headers.get("Authorization"),
            "ctype": self.headers.get("Content-Type"),
            "ua": self.headers.get("User-Agent"),
            "body": json.loads(raw) if raw else None,
        })
        if self.path.startswith("/v1/calls/missing"):
            status, payload = 404, b'{"error":"call not found"}'
        elif self.path == "/v1/calls/boom/hangup":
            status, payload = 500, b"<html>oops</html>"
        elif self.path == "/v1/calls/empty/hangup":
            status, payload = 204, b""
        elif self.path == "/v1/calls" and self.command == "POST":
            status, payload = 201, b'{"id":"call_123","status":"queued"}'
        else:
            status, payload = 200, b'{"ok":true}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = _handle


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Handler.seen.clear()
        self.client = OpenLineClient(self.url + "/", "key-abc", timeout=5)

    def test_create_call_matches_readme_request_shape(self):
        out = self.client.create_call(
            "+919876543210", "phone-7f3a91c2",
            stream_config("wss://my-agent.example.com/ws", "twilio"),
        )
        self.assertEqual(out, {"id": "call_123", "status": "queued"})
        req = Handler.seen[0]
        self.assertEqual((req["method"], req["path"]), ("POST", "/v1/calls"))
        self.assertEqual(req["auth"], "Bearer key-abc")
        self.assertEqual(req["ctype"], "application/json")
        self.assertTrue(req["ua"].startswith("django-openline/"))
        self.assertEqual(req["body"], {
            "to": "+919876543210",
            "deviceId": "phone-7f3a91c2",
            "stream": {"url": "wss://my-agent.example.com/ws", "format": "twilio"},
        })

    def test_optional_fields_omitted_and_extra_merged(self):
        self.client.create_call("+48123456789", extra={"timeout": 30})
        self.assertEqual(Handler.seen[0]["body"], {"to": "+48123456789", "timeout": 30})

    def test_call_actions_hit_expected_paths(self):
        self.client.hangup("c1")
        self.client.answer("c1")
        self.client.hold("c1", on=False)
        self.client.send_dtmf("c1", "12#")
        self.client.get_call("c1")
        got = [(r["method"], r["path"], r["body"]) for r in Handler.seen]
        self.assertEqual(got, [
            ("POST", "/v1/calls/c1/hangup", None),
            ("POST", "/v1/calls/c1/answer", None),
            ("POST", "/v1/calls/c1/hold", {"hold": False}),
            ("POST", "/v1/calls/c1/dtmf", {"digits": "12#"}),
            ("GET", "/v1/calls/c1", None),
        ])

    def test_path_params_cannot_escape_the_path(self):
        self.client.get_call("../../admin?x=1")
        self.assertEqual(Handler.seen[0]["path"], "/v1/calls/..%2F..%2Fadmin%3Fx%3D1")

    def test_dtmf_validation(self):
        for bad in ("", "12x", "1 2", None):
            with self.assertRaises(ValueError):
                self.client.send_dtmf("c1", bad)
        self.assertEqual(Handler.seen, [])

    def test_api_error_carries_status_and_message(self):
        with self.assertRaises(OpenLineAPIError) as ctx:
            self.client.get_call("missing")
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.message, "call not found")
        self.assertIn("404", str(ctx.exception))

    def test_non_json_error_body_still_raises_api_error(self):
        with self.assertRaises(OpenLineAPIError) as ctx:
            self.client.hangup("boom")
        self.assertEqual(ctx.exception.status_code, 500)

    def test_empty_success_body(self):
        self.assertEqual(self.client.hangup("empty"), {})

    def test_endpoint_override(self):
        client = OpenLineClient(self.url, "k", endpoints={"hangup": ("post", "/v2/calls/{call_id}/end")})
        client.hangup("c9")
        self.assertEqual(Handler.seen[0]["path"], "/v2/calls/c9/end")
        self.assertEqual(client.endpoints["answer"], ("POST", "/v1/calls/{call_id}/answer"))

    def test_bad_endpoint_override_rejected(self):
        with self.assertRaises(OpenLineConfigError):
            OpenLineClient(self.url, "k", endpoints={"hangup": "/oops"})

    def test_config_errors(self):
        with self.assertRaises(OpenLineConfigError):
            OpenLineClient("", "k")
        with self.assertRaises(OpenLineConfigError):
            OpenLineClient(self.url, "")
        with self.assertRaises(OpenLineConfigError):
            self.client.request("nope")

    def test_connection_failure(self):
        client = OpenLineClient("http://127.0.0.1:1", "k", timeout=2)
        with self.assertRaises(OpenLineConnectionError):
            client.list_devices()


if __name__ == "__main__":
    unittest.main()
