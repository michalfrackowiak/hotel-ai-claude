"""HMAC-SHA256 helpers for OpenLine webhooks. Pure Python, no Django."""
import base64
import hashlib
import hmac

__all__ = ["compute_signature", "verify_signature"]


def _key(secret):
    return secret.encode("utf-8") if isinstance(secret, str) else secret


def compute_signature(secret, body, *, encoding="hex"):
    """Return the HMAC-SHA256 of ``body`` (bytes) as ``hex`` or ``base64``."""
    digest = hmac.new(_key(secret), body, hashlib.sha256).digest()
    if encoding == "hex":
        return digest.hex()
    if encoding == "base64":
        return base64.b64encode(digest).decode("ascii")
    raise ValueError("encoding must be 'hex' or 'base64'")


def verify_signature(secret, body, header_value):
    """Constant-time check of a signature header against the raw request body.

    Accepts the common encodings so the receiver does not depend on exactly how
    the sender formats the header: a bare hex digest, ``sha256=<hex>``, or a
    base64 digest.
    """
    if not secret or not header_value:
        return False
    value = header_value.strip()
    if value.lower().startswith("sha256="):
        value = value[len("sha256="):]
    candidate = value.encode("utf-8", "replace")

    digest = hmac.new(_key(secret), body, hashlib.sha256).digest()
    ok_hex = hmac.compare_digest(candidate.lower(), digest.hex().encode("ascii"))
    ok_b64 = hmac.compare_digest(candidate, base64.b64encode(digest))
    return bool(ok_hex | ok_b64)  # bitwise: always evaluate both comparisons
