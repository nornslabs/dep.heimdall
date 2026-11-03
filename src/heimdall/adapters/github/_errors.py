"""Turns githubkit failures into Heimdall errors the job runner understands."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from githubkit.exception import RateLimitExceeded, RequestError, RequestFailed, RequestTimeout
from pydantic import ValidationError

from heimdall.shared.errors import GitHubRequestFailed, GitHubUnavailable

SERVER_ERROR = 500
NOT_FOUND = 404


@asynccontextmanager
async def github_errors(action: str) -> AsyncGenerator[None]:
    """Map failures inside the block to retryable or permanent Heimdall errors.

    Outages (5xx, timeouts, network errors, rate limits) become ``GitHubUnavailable`` so the
    job retries; everything else becomes ``GitHubRequestFailed``. Messages carry the action
    and status only, never response bodies (they can quote PR content).

    Args:
        action: What was being read, for the message, for example ``"get pull request"``.
    """
    try:
        yield
    except RateLimitExceeded:
        raise GitHubUnavailable(f"GitHub rate limit hit during {action}") from None
    except RequestFailed as error:
        raise _for_status(action, error.response.status_code) from None
    except RequestTimeout:
        raise GitHubUnavailable(f"GitHub timed out during {action}") from None
    except RequestError:
        raise GitHubUnavailable(f"GitHub unreachable during {action}") from None
    except ValidationError:
        raise GitHubRequestFailed(f"unexpected GitHub response during {action}") from None


def is_not_found(error: RequestFailed) -> bool:
    """Whether GitHub answered 404."""
    return error.response.status_code == NOT_FOUND


def _for_status(action: str, status: int) -> GitHubUnavailable | GitHubRequestFailed:
    if status >= SERVER_ERROR:
        return GitHubUnavailable(f"GitHub returned {status} during {action}")
    return GitHubRequestFailed(f"GitHub returned {status} during {action}")
