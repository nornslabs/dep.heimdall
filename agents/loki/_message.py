"""The short room message Loki posts after a review; counts only, nothing from the PR."""

from agents.loki.schemas import LokiReview

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


def render_review(review: LokiReview, *, head_sha: str, rejected: int = 0) -> str:
    """Render the room message from a validated review, never from the model's reply.

    Args:
        review: The submission that passed schema validation and the finding-id check.
        head_sha: PR head the review was based on; trusted, from the PR snapshot.
        rejected: Findings the review module refused during this run.

    Returns:
        One line naming what the review produced, then the model's summary as one line.
    """
    short_head = head_sha[:SHORT_SHA_LENGTH]
    if review.finding_ids:
        noun = "finding" if len(review.finding_ids) == 1 else "findings"
        line = f"Loki: reviewed {short_head}. {len(review.finding_ids)} {noun} saved"
        if rejected:
            line += f", {rejected} rejected"
        headline = f"{line}."
    elif review.skipped_reason == "docs_only":
        headline = f"Loki: reviewed {short_head}. Docs-only change; no findings."
    else:
        headline = f"Loki: reviewed {short_head}. No findings."
    summary = " ".join(review.summary.split())
    return f"{headline}\n{summary}" if summary else headline
