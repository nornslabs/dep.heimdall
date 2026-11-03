"""Heimdall's error types, each marked retryable or not so the queue knows what to do."""


class HeimdallError(Exception):
    """Base class for every expected Heimdall failure.

    Attributes:
        retryable: True when the same call may succeed later (an outage), False when
            retrying cannot help (a broken rule).
    """

    retryable: bool = False


class NotAMaintainer(HeimdallError):
    """The person acting is not on the repo's maintainer list."""


class DraftChanged(HeimdallError):
    """The draft text no longer matches the hash the maintainer approved."""


class ConfigInvalid(HeimdallError):
    """The repo's heimdall.yml could not be parsed or failed validation."""


class RepoLimitReached(HeimdallError):
    """The installation already has the maximum number of repos enabled."""


class RepoNotEnabled(HeimdallError):
    """The repository is not enabled for the requesting installation."""



class InstallationNotFound(HeimdallError):
    """Heimdall has no record of this GitHub App installation."""


class InstallationSuspended(HeimdallError):
    """The installation is suspended or uninstalled, so Heimdall does no work for it."""


class WebhookPayloadInvalid(HeimdallError):
    """A signed GitHub webhook is missing a field Heimdall needs."""


class RepoNotFound(HeimdallError):
    """The repository is not enabled for this installation (or belongs to another one)."""


class StaleHeadSha(HeimdallError):
    """The PR head moved since this work started, so its result is out of date."""


class ReproLimitReached(HeimdallError):
    """The PR has already used its allowed reproduction attempts for this head."""


class ToolNotAllowed(HeimdallError):
    """An agent called an MCP tool that is not on its allowed list."""


class GitHubRequestFailed(HeimdallError):
    """GitHub refused a request (a 4xx other than rate limiting) or sent an unexpected reply."""


class GitHubUnavailable(HeimdallError):
    """GitHub returned a server error, timed out or rate-limited us."""

    retryable = True


class SwitchUnavailable(HeimdallError):
    """Switch returned a server error or timed out."""

    retryable = True


class SwitchRequestFailed(HeimdallError):
    """Switch rejected a request that cannot succeed unchanged."""
