"""Errors returned by provider-neutral AI requests."""


class AiUnavailable(RuntimeError):
    """A model endpoint is temporarily unavailable and the request may be retried."""


class AiBadResponse(RuntimeError):
    """A model endpoint returned an invalid request response or malformed payload."""
