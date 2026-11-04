"""Render the heading that opens a PR's Switch thread."""

import re

MAX_THREAD_TITLE_CHARS = 120
_ELLIPSIS = "…"
_MARKDOWN_PUNCTUATION = re.compile(r"([\\`*_{}\[\]()#+.!|>~@-])")


def render_thread_heading(number: int, title: str) -> str:
    """Format the first post of a PR's thread: its number and a safe form of its title.

    The title is untrusted text, so it is flattened to one line, capped, and stripped of
    Markdown and mention syntax before it is shown to maintainers.

    Args:
        number: Pull request number.
        title: Pull request title as written by its author.

    Returns:
        ``New PR #<number> <title>``, or ``New PR #<number>`` when the title is blank.
    """
    flat_title = " ".join(title.split())
    if not flat_title:
        return f"New PR #{number}"
    if len(flat_title) > MAX_THREAD_TITLE_CHARS:
        flat_title = flat_title[:MAX_THREAD_TITLE_CHARS] + _ELLIPSIS
    return f"New PR #{number} {_MARKDOWN_PUNCTUATION.sub(r'\\\1', flat_title)}"
