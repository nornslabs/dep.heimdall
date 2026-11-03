"""GitHub App authentication: one githubkit client per installation, tokens cached in memory."""

from pathlib import Path

import httpx2
from githubkit import AppInstallationAuthStrategy, GitHub
from githubkit.cache import MemCacheStrategy

from heimdall.shared.errors import HeimdallError

PEM_PREFIX = "-----BEGIN"


class PrivateKeyError(HeimdallError):
    """The GitHub App private key could not be read; the message never names its path."""


def load_private_key(path: str) -> str:
    """Read the GitHub App's private key from the mounted ``.pem`` file.

    Args:
        path: Location of the ``.pem`` file (from settings).

    Returns:
        The key in PEM format.

    Raises:
        PrivateKeyError: the file is missing, unreadable, or not a PEM key. The error
            names neither the path nor the content (AGENTS.md: never log key paths).
    """
    try:
        key = Path(path).read_text(encoding="utf-8")
    except OSError:
        raise PrivateKeyError("GitHub App private key file could not be read") from None
    if not key.lstrip().startswith(PEM_PREFIX):
        raise PrivateKeyError("GitHub App private key file is not a PEM private key")
    return key


class GitHubAppClients:
    """Hands out an authenticated githubkit client per installation.

    githubkit signs the app JWT and swaps it for an installation token on first use, then
    caches both until shortly before they expire. The cache belongs to this object (githubkit
    otherwise shares one per process), so two factories never reuse each other's tokens.
    """

    def __init__(
        self,
        app_id: int,
        private_key: str,
        *,
        async_transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        """Set up the factory.

        Args:
            app_id: The GitHub App's numeric id.
            private_key: The App's PEM private key (see ``load_private_key``).
            async_transport: Replaces the network, for tests only.
        """
        self._app_id = app_id
        self._private_key = private_key
        self._async_transport = async_transport
        self._token_cache = MemCacheStrategy()
        self._clients: dict[int, GitHub[AppInstallationAuthStrategy]] = {}

    def for_installation(self, installation_id: int) -> GitHub[AppInstallationAuthStrategy]:
        """Return the client acting as one installation, creating it on first use.

        Args:
            installation_id: The installation whose permissions the client uses.

        Returns:
            A githubkit client. Automatic retries are off, because our jobs and outbox
            already retry with backoff.
        """
        client = self._clients.get(installation_id)
        if client is None:
            client = self._create(installation_id)
            self._clients[installation_id] = client
        return client

    def _create(self, installation_id: int) -> GitHub[AppInstallationAuthStrategy]:
        auth = AppInstallationAuthStrategy(self._app_id, self._private_key, installation_id)
        return GitHub(
            auth,
            async_transport=self._async_transport,
            auto_retry=False,
            http_cache=False,
            cache_strategy=self._token_cache,
        )
