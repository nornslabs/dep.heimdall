"""Fixtures for the e2e replay: real Postgres, fake GitHub and Switch, one running pipeline."""

from collections.abc import AsyncIterator
import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.github.auth import GitHubAppClients
from tests.e2e.fake_world import DemoGitHub, FakeSwitch
from tests.e2e.pipeline import Pipeline, PipelineDeps
from tests.support.postgres import database_url, db_schema, engine, sessions

__all__ = ["database_url", "db_schema", "engine", "sessions"]

APP_ID = 424_242
RSA_KEY_BITS = 2048


@pytest.fixture(scope="session", autouse=True)
def _heimdall_settings() -> None:
    """Set required HEIMDALL_* environment variables for the e2e tests.

    The e2e tests use fake GitHub and Switch, so we provide dummy values for
    required settings that the real code would read from the environment.
    """
    os.environ.setdefault("HEIMDALL_DATABASE_URL", "postgresql://test:test@localhost:5432/test")
    os.environ.setdefault("HEIMDALL_GITHUB_APP_ID", str(APP_ID))
    os.environ.setdefault("HEIMDALL_GITHUB_PRIVATE_KEY_PATH", "/tmp/fake-key.pem")
    os.environ.setdefault("HEIMDALL_GITHUB_WEBHOOK_SECRET", "fake-webhook-secret")
    os.environ.setdefault("HEIMDALL_SWITCH_URL", "http://fake-switch")
    os.environ.setdefault("HEIMDALL_SWITCH_TOKEN", "fake-switch-token")
    os.environ.setdefault("HEIMDALL_DEFAULT_SWITCH_ROOM_ID", "fake-room-id")
    os.environ.setdefault("HEIMDALL_DEFAULT_SWITCH_AGENT_ROOM_ID", "fake-agent-room-id")
    os.environ.setdefault("HEIMDALL_LOG_LEVEL", "DEBUG")
    # Clear the lru_cache so get_settings() picks up the new values
    from heimdall.shared.settings import get_settings

    get_settings.cache_clear()


@pytest.fixture(scope="session")
def private_key_pem() -> str:
    """A throwaway GitHub App key; the fake GitHub accepts any signed JWT."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=RSA_KEY_BITS)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


@pytest.fixture
def github() -> DemoGitHub:
    """The demo repo as GitHub answers for it."""
    return DemoGitHub()


@pytest.fixture
def switch() -> FakeSwitch:
    """The triage room."""
    return FakeSwitch()


@pytest.fixture
async def pipeline(
    sessions: async_sessionmaker[AsyncSession],
    database_url: str,
    db_schema: str,
    github: DemoGitHub,
    switch: FakeSwitch,
    private_key_pem: str,
) -> AsyncIterator[Pipeline]:
    """Heimdall installed on the demo repo, with both queue apps open."""
    clients = GitHubAppClients(APP_ID, private_key_pem, async_transport=github.api.transport())
    deps = PipelineDeps(sessions, database_url, db_schema, github, switch, clients)
    async with Pipeline(deps).running() as running:
        yield running