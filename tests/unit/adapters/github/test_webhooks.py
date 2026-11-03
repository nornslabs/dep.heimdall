"""Tests for GitHub webhook signature verification."""

import hashlib
import hmac

from heimdall.adapters.github.webhooks import verify_signature


def test_accepts_signature_for_exact_body() -> None:
    """Accept a correctly signed request body."""
    body = b'{"action":"opened"}'
    digest = hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()

    assert verify_signature(body, f"sha256={digest}", "test-secret")


def test_rejects_missing_or_changed_signature() -> None:
    """Reject absent signatures and signatures for another body."""
    assert not verify_signature(b"payload", None, "test-secret")
    assert not verify_signature(b"payload", "sha256=bad", "test-secret")
    assert not verify_signature(b"changed", "sha256=bad", "test-secret")
