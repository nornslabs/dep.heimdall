"""Secret scan: find tokens, keys and URLs with credentials in what an agent writes.

A match fails the run rather than being redacted: an agent that copies a credential out of a
diff or a log has gone off script, and nothing it wrote should reach the room. Only the kind of
secret is reported, never the matched text, so the value cannot leak through logs or audit.
"""

import re

SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "api_key": re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}"),
    "aws_access_key_id": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "credential_url": re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@[^\s/@]+"),
    "github_fine_grained_token": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),
    "private_key": re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"),
    "slack_token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
}


def find_secret_kinds(text: str) -> tuple[str, ...]:
    """Name every kind of secret that appears in ``text``.

    Args:
        text: Anything the agent is about to send out: a submission or tool arguments.

    Returns:
        The kinds found, each once, in name order; empty when the text is clean.
    """
    found: list[str] = []
    for kind, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            found.append(kind)
    return tuple(found)
