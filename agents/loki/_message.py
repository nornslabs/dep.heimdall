"""The short room message Loki posts after a review; counts only, nothing from the PR."""

SHORT_SHA_LENGTH = 7


def format_review_message(*, saved: int, rejected: int, head_sha: str) -> str:
    """Say what Loki's review produced, without quoting any PR or model text.

    Findings themselves reach the maintainer on Heimdall's card, which escapes them. Keeping
    this post to counts means untrusted text never lands in the room under Loki's name.

    Args:
        saved: Findings the review module accepted.
        rejected: Findings it refused.
        head_sha: PR head the review was based on.

    Returns:
        One line, naming the short head and the saved and rejected counts.
    """
    short_head = head_sha[:SHORT_SHA_LENGTH]
    if saved == 0 and rejected == 0:
        return f"Loki: reviewed {short_head}. No findings."
    noun = "finding" if saved == 1 else "findings"
    line = f"Loki: reviewed {short_head}. {saved} {noun} saved"
    if rejected:
        line += f", {rejected} rejected"
    return f"{line}."
