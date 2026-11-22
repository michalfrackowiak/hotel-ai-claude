"""Django-dependent tests. Run with:  python runtests.py"""
import json
import unittest

try:
    import django  # noqa: F401
except ImportError:  # pragma: no cover
    raise unittest.SkipTest("Django is not installed")

from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from django_openline.exceptions import OpenLineError
from django_openline.models import Call, WebhookEvent
from django_openline.services import place_call
from django_openline.signals import on_event, webhook_received
from django_openline.signing import compute_signature

SECRET = "whsec_test"
CONF = {
    "BASE_URL": "https://openline.example",
    "API_KEY": "key",
    "WEBHOOK_SECRET": SECRET,
    "DEFAULT_DEVICE_ID": "phone-1",
    "DEFAULT_STREAM_URL": "wss://agent.example/ws",
}


def post_signed(client, payload, *, secret=SECRET, header="HTTP_X_OPENLINE_SIGNATURE", raw=None):
    body = raw if raw is not None else json.dumps(payload).encode()
    extra = {header: "sha256=" + compute_signature(secret, body)} if secret else {}
    return client.post(reverse("openline:webhook"), data=body, content_type="application/json", **extra)


@override_settings(OPENLINE=CONF)
class WebhookTests(TestCase):
    payload = {"type": "call.ringing", "callId": "c1", "deviceId": "phone-1",
               "direction": "incoming", "from": "+48111222333", "status": "ringing"}

    def setUp(self):
        self.received = []
        self._recv = lambda sender, **kw: self.received.append(kw)
        webhook_received.connect(self._recv, weak=False)
        self.addCleanup(webhook_received.disconnect, self._recv)

    def test_valid_delivery_is_stored_synced_and_signalled(self):
        resp = post_signed(self.client, self.payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")
        call = Call.objects.get(openline_id="c1")
        self.assertEqual((call.direction, call.from_number, call.status, call.last_event),
                         ("inbound", "+48111222333", "ringing", "call.ringing"))
        event = WebhookEvent.objects.get()
        self.assertEqual((event.event_type, event.call_id), ("call.ringing", call.pk))
        self.assertIsNotNone(event.processed_at)
        self.assertEqual(len(self.received), 1)
        self.assertEqual(self.received[0]["call"], call)

    def test_bad_or_missing_signature_is_rejected_and_nothing_stored(self):
        self.assertEqual(post_signed(self.client, self.payload, secret="wrong").status_code, 401)
        self.assertEqual(post_signed(self.client, self.payload, secret=None).status_code, 401)
        self.assertFalse(Call.objects.exists() or WebhookEvent.objects.exists())
        self.assertEqual(self.received, [])

    def test_alternative_signature_header_name(self):
        resp = post_signed(self.client, self.payload, header="HTTP_X_HUB_SIGNATURE_256")
        self.assertEqual(resp.status_code, 200)

    def test_fails_closed_without_a_configured_secret(self):
        with override_settings(OPENLINE={**CONF, "WEBHOOK_SECRET": ""}):
            self.assertEqual(post_signed(self.client, self.payload, secret=None).status_code, 401)
            self.assertEqual(post_signed(self.client, self.payload).status_code, 401)
        with override_settings(OPENLINE={**CONF, "WEBHOOK_SECRET": "", "ALLOW_UNSIGNED_WEBHOOKS": True}):
            self.assertEqual(post_signed(self.client, self.payload, secret=None).status_code, 200)

    def test_custom_verifier(self):
        with override_settings(OPENLINE={**CONF, "WEBHOOK_VERIFIER": lambda request: True}):
            self.assertEqual(post_signed(self.client, self.payload, secret=None).status_code, 200)

    def test_duplicate_delivery_is_acknowledged_but_not_reprocessed(self):
        post_signed(self.client, self.payload)
        resp = post_signed(self.client, self.payload)
        self.assertEqual((resp.status_code, resp.json()["status"]), (200, "duplicate"))
        self.assertEqual(WebhookEvent.objects.count(), 1)
        self.assertEqual(len(self.received), 1)

    def test_later_events_of_the_same_call_are_not_mistaken_for_duplicates(self):
        post_signed(self.client, {"type": "call.ringing", "call": {"id": "c1"}})
        post_signed(self.client, {"type": "call.answered", "call": {"id": "c1", "status": "answered"}})
        self.assertEqual(WebhookEvent.objects.count(), 2)
        self.assertEqual(Call.objects.get().status, "answered")

    def test_sparse_event_does_not_wipe_existing_call_data(self):
        post_signed(self.client, self.payload)
        post_signed(self.client, {"type": "call.dtmf", "callId": "c1"})
        call = Call.objects.get()
        self.assertEqual((call.from_number, call.status, call.last_event),
                         ("+48111222333", "ringing", "call.dtmf"))

    def test_invalid_bodies(self):
        self.assertEqual(post_signed(self.client, None, raw=b"not json").status_code, 400)
        self.assertEqual(post_signed(self.client, None, raw=b"[1,2]").status_code, 400)

    def test_only_post_allowed(self):
        self.assertEqual(self.client.get(reverse("openline:webhook")).status_code, 405)

    def test_failing_receiver_does_not_fail_the_webhook_and_is_recorded(self):
        def boom(sender, **kw):
            raise RuntimeError("handler exploded")

        webhook_received.connect(boom, weak=False)
        self.addCleanup(webhook_received.disconnect, boom)
        with self.assertLogs("django_openline.webhooks", level="ERROR"):
            resp = post_signed(self.client, self.payload)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("handler exploded", WebhookEvent.objects.get().error)

    def test_on_event_decorator_filters_by_type(self):
        seen = []

        @on_event("call.ringing")
        def handler(event, call, **kwargs):
            seen.append(event.event_type)

        self.addCleanup(
            webhook_received.disconnect,
            dispatch_uid=f"openline.on_event.{handler.__module__}.{handler.__qualname__}",
        )
        post_signed(self.client, self.payload)
        post_signed(self.client, {"type": "call.ended", "callId": "c1"})
        self.assertEqual(seen, ["call.ringing"])


@override_settings(OPENLINE=CONF)
class ServiceTests(TestCase):
    def fake_client(self, response=None):
        client = mock.Mock()
        client.create_call.return_value = response or {"id": "call_9", "status": "queued"}
        return client

    def test_place_call_uses_defaults_and_records_the_call(self):
        client = self.fake_client()
        call = place_call("+48123456789", reference="order:7", metadata={"k": 1}, client=client)
        client.create_call.assert_called_once_with(
            to="+48123456789", device_id="phone-1",
            stream={"url": "wss://agent.example/ws", "format": "twilio"}, extra=None)
        self.assertEqual((call.openline_id, call.direction, call.status, call.reference, call.metadata),
                         ("call_9", "outbound", "queued", "order:7", {"k": 1}))
        self.assertEqual(call.to_number, "+48123456789")
        self.assertEqual(call.stream_url, "wss://agent.example/ws")

    def test_webhook_arriving_before_the_api_response_is_merged_not_duplicated(self):
        Call.objects.create(openline_id="call_9", status="ringing")
        call = place_call("+48123456789", reference="order:7", client=self.fake_client())
        self.assertEqual(Call.objects.count(), 1)
        self.assertEqual((call.reference, call.direction), ("order:7", "outbound"))
        self.assertEqual(call.status, "ringing")  # the newer webhook status is kept

    def test_invalid_number_rejected_before_any_request(self):
        client = self.fake_client()
        for bad in ("48123456789", "+0123456", "abc", "", None):
            with self.assertRaises(ValueError):
                place_call(bad, client=client)
        client.create_call.assert_not_called()

    def test_missing_call_id_in_response(self):
        with self.assertRaises(OpenLineError):
            place_call("+48123456789", client=self.fake_client({"status": "queued"}))

    def test_call_methods_delegate_to_client(self):
        call = Call.objects.create(openline_id="c1")
        with mock.patch("django_openline.services.get_client") as get_client:
            call.hangup()
            call.answer()
            call.send_dtmf("5")
            get_client.return_value.hangup.assert_called_once_with("c1")
            get_client.return_value.answer.assert_called_once_with("c1")
            get_client.return_value.send_dtmf.assert_called_once_with("c1", "5")

    def test_refresh_from_api(self):
        call = Call.objects.create(openline_id="c1", status="ringing")
        with mock.patch("django_openline.services.get_client") as get_client:
            get_client.return_value.get_call.return_value = {"id": "c1", "status": "ended"}
            call.refresh_from_api()
        self.assertEqual(call.status, "ended")


class PackagingTests(TestCase):
    def test_migrations_match_models(self):
        out = StringIO()
        try:
            call_command("makemigrations", "openline", "--check", "--dry-run", stdout=out)
        except SystemExit:
            self.fail("models and migrations are out of sync:\n" + out.getvalue())

    @override_settings(OPENLINE=CONF)
    def test_admin_pages_render(self):
        user = get_user_model().objects.create_superuser("root", "root@example.com", "pw")
        self.client.force_login(user)
        call = Call.objects.create(openline_id="c1")
        WebhookEvent.objects.create(event_key="k", payload={})
        for name in ("openline_call_changelist", "openline_webhookevent_changelist"):
            self.assertEqual(self.client.get(reverse(f"admin:{name}")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:openline_call_change", args=[call.pk])).status_code, 200)

    @override_settings(OPENLINE=None)
    def test_system_check_warns_when_setting_missing(self):
        from django_openline.checks import check_openline_settings

        self.assertEqual([w.id for w in check_openline_settings(None)], ["openline.W001"])

    @override_settings(OPENLINE={"BASE_URL": "http://example.com", "API_KEY": "k", "TYPO": 1})
    def test_system_check_flags_typos_missing_secret_and_plain_http(self):
        from django_openline.checks import check_openline_settings

        ids = {w.id for w in check_openline_settings(None)}
        self.assertEqual(ids, {"openline.W002", "openline.W004", "openline.W005"})

    @override_settings(OPENLINE=CONF)
    def test_openline_check_command_runs(self):
        out = StringIO()
        with mock.patch("django_openline.management.commands.openline_check.get_client") as get_client:
            get_client.return_value.endpoints = {
                "list_devices": ("GET", "/v1/devices"), "list_calls": ("GET", "/v1/calls")}
            get_client.return_value.request.return_value = {"devices": [{"id": "phone-1"}]}
            call_command("openline_check", stdout=out)
        self.assertIn("phone-1", out.getvalue())
