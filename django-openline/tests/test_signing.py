import unittest

from django_openline.signing import compute_signature, verify_signature

BODY = b'{"type":"call.answered","callId":"c1"}'
SECRET = "whsec_test"


class SigningTests(unittest.TestCase):
    def test_known_vector(self):
        # Independently computed: HMAC-SHA256(key="key", msg="The quick brown fox ...")
        sig = compute_signature("key", b"The quick brown fox jumps over the lazy dog")
        self.assertEqual(sig, "f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8")

    def test_accepts_hex_prefixed_hex_and_base64(self):
        hex_sig = compute_signature(SECRET, BODY)
        b64_sig = compute_signature(SECRET, BODY, encoding="base64")
        for header in (hex_sig, "sha256=" + hex_sig, "SHA256=" + hex_sig, hex_sig.upper(), b64_sig):
            self.assertTrue(verify_signature(SECRET, BODY, header), header)

    def test_rejects_tampering(self):
        sig = compute_signature(SECRET, BODY)
        self.assertFalse(verify_signature(SECRET, BODY + b" ", sig))
        self.assertFalse(verify_signature("other-secret", BODY, sig))
        self.assertFalse(verify_signature(SECRET, BODY, sig[:-1] + ("0" if sig[-1] != "0" else "1")))

    def test_rejects_empty_inputs(self):
        sig = compute_signature(SECRET, BODY)
        self.assertFalse(verify_signature("", BODY, sig))
        self.assertFalse(verify_signature(SECRET, BODY, ""))
        self.assertFalse(verify_signature(SECRET, BODY, None))
        self.assertFalse(verify_signature(SECRET, BODY, "sha256="))

    def test_bad_encoding_name(self):
        with self.assertRaises(ValueError):
            compute_signature(SECRET, BODY, encoding="rot13")


if __name__ == "__main__":
    unittest.main()
