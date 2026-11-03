"""Tests for authenticated GitHub webhook API responses."""

import hashlib
import hmac
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from apps.api import main as api
from heimdall.adapters.db.engine import create_engine, make_session_factory


def test_webhook_rejects_bad_signature_before_parsing(monkeypatch) -> None:
    """Reject unauthenticated input without calling webhook persistence."""
    engine = create_engine("postgresql://test:test@localhost/test")
    persist = AsyncMock()
    monkeypatch.setattr(api, "receive_webhook", persist)
    app = api.create_app(make_session_factory(engine), engine, "test-secret")

    with TestClient(app) as client:
        response = client.post(
            "/webhooks/github",
            content=b"not json",
            headers={"X-Hub-Signature-256": "sha256=wrong"},
        )

    assert response.status_code == 401
    persist.assert_not_awaited()


def test_webhook_returns_202_for_new_and_200_for_duplicate(monkeypatch) -> None:
    """Return accepted for new deliveries and OK for redeliveries."""
    engine = create_engine("postgresql://test:test@localhost/test")
    persist = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(api, "receive_webhook", persist)
    app = api.create_app(make_session_factory(engine), engine, "test-secret")
    body = b'{"action":"opened","installation":{"id":42}}'
    digest = hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    headers = {
        "X-Hub-Signature-256": f"sha256={digest}",
        "X-GitHub-Delivery": "delivery-42",
        "X-GitHub-Event": "pull_request",
    }

    with TestClient(app) as client:
        first = client.post("/webhooks/github", content=body, headers=headers)
        duplicate = client.post("/webhooks/github", content=body, headers=headers)

    assert first.status_code == 202
    assert duplicate.status_code == 200
    assert persist.await_count == 2
