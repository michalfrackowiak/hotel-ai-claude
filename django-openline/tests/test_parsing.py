import unittest

from django_openline.parsing import (
    extract_call_fields,
    extract_event_key,
    extract_event_type,
    extract_items,
    normalize_direction,
)


class ParsingTests(unittest.TestCase):
    def test_flat_payload(self):
        p = {"type": "call.ringing", "callId": "c1", "deviceId": "phone-1",
             "direction": "incoming", "from": "+48111222333", "to": "+48999888777", "status": "ringing"}
        self.assertEqual(extract_event_type(p), "call.ringing")
        self.assertEqual(extract_call_fields(p), {
            "openline_id": "c1", "device_id": "phone-1", "direction": "inbound",
            "from_number": "+48111222333", "to_number": "+48999888777", "status": "ringing"})

    def test_nested_call_object_uses_its_id(self):
        p = {"event": "call.answered", "id": "evt-not-a-call", "call": {"id": "c2", "status": "answered"}}
        self.assertEqual(extract_event_type(p), "call.answered")
        self.assertEqual(extract_call_fields(p)["openline_id"], "c2")

    def test_data_wrapper_and_data_call(self):
        p = {"type": "x", "data": {"call": {"id": "c3", "direction": "outgoing"}}}
        f = extract_call_fields(p)
        self.assertEqual((f["openline_id"], f["direction"]), ("c3", "outbound"))
        p = {"type": "x", "data": {"callId": "c4", "status": "ended"}}
        self.assertEqual(extract_call_fields(p), {"openline_id": "c4", "status": "ended"})

    def test_bare_id_only_trusted_for_rest_responses(self):
        p = {"id": "c5", "status": "queued"}
        self.assertNotIn("openline_id", extract_call_fields(p))
        self.assertEqual(extract_call_fields(p, top_level_id_is_call=True)["openline_id"], "c5")

    def test_event_key_never_falls_back_to_bare_id(self):
        self.assertIsNone(extract_event_key({"id": "c1", "type": "t"}))
        self.assertEqual(extract_event_key({"eventId": "e1"}), "e1")
        self.assertEqual(extract_event_key({"event": {"id": "e2", "type": "t"}}), "e2")

    def test_garbage_in_garbage_out_without_crashing(self):
        for bad in (None, [], "x", 5, {}):
            self.assertEqual(extract_call_fields(bad), {})
            self.assertEqual(extract_event_type(bad), "")
        self.assertEqual(extract_call_fields({"callId": 123})["openline_id"], "123")
        self.assertEqual(extract_call_fields({"callId": True}), {})

    def test_direction_and_items(self):
        self.assertEqual(normalize_direction(" Incoming "), "inbound")
        self.assertIsNone(normalize_direction("sideways"))
        self.assertEqual(extract_items([1, 2]), [1, 2])
        self.assertEqual(extract_items({"devices": [{"id": "d"}]}), [{"id": "d"}])
        self.assertEqual(extract_items({"nope": 1}), [])


if __name__ == "__main__":
    unittest.main()
