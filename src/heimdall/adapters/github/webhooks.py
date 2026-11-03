"""Verify GitHub webhook signatures before an event body is trusted."""

import hashlib
import hmac


def verify_signature(body: bytes, signature: str | None, secret: str) -> bool:
    """Check GitHub's SHA-256 signature for the exact request bytes.

    Args:
        body: Unmodified HTTP request body.
        signature: Value from ``X-Hub-Signature-256``.
        secret: Configured GitHub webhook secret.

    Returns:
        Whether the signature is valid.
    """
    if signature is None or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
