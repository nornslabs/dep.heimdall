from collections.abc import Iterator

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from heimdall.adapters.github.auth import GitHubAppClients
from heimdall.adapters.github.reader import GitHubApiReader
from tests.integration.adapters.github.fake_github import FakeGitHub

APP_ID = 424242


@pytest.fixture(scope="session")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def private_key_pem(rsa_key: rsa.RSAPrivateKey) -> str:
    return rsa_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


@pytest.fixture
def fake_github() -> Iterator[FakeGitHub]:
    yield FakeGitHub()


@pytest.fixture
def clients(fake_github: FakeGitHub, private_key_pem: str) -> GitHubAppClients:
    return GitHubAppClients(APP_ID, private_key_pem, async_transport=fake_github.transport())


@pytest.fixture
def reader(clients: GitHubAppClients) -> GitHubApiReader:
    return GitHubApiReader(clients)
