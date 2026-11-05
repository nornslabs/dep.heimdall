"""Which new-side lines a unified diff adds, per changed path.

A finding may only cite a line the PR added, and that is a question only the diff can answer:
the review module sees file names, not hunks.
"""

import re

NEW_FILE_PREFIX = "+++ "
HUNK_PREFIX = "@@ "
DIFF_HEADER_PREFIX = "diff --git "
DELETED_FILE = "/dev/null"
REPOSITORY_ROOT = "b/"
ADDED_PREFIX = "+"
_HUNK_START = re.compile(r"\+(\d+)")


def diff_added_lines(diff_text: str) -> dict[str, frozenset[int]]:
    r"""Map every changed path to the new-side line numbers its added lines occupy.

    Removed lines have no new-side number, so they never appear here. A file deleted by the
    PR (``+++ /dev/null``) is left out entirely, and the ``\\ No newline at end of file``
    marker does not count as content.

    Args:
        diff_text: A unified diff, as ``get_diff`` returns it.

    Returns:
        One entry per changed path that adds lines, holding the 1-based line numbers the
        added lines get in the file's new version. Paths are repository-relative.
    """
    added: dict[str, set[int]] = {}
    path: str | None = None
    new_line = 0
    in_hunk = False
    for raw in diff_text.splitlines():
        if raw.startswith(DIFF_HEADER_PREFIX):
            in_hunk = False
            continue
        if not in_hunk and raw.startswith(NEW_FILE_PREFIX):
            path = _repository_path(raw[len(NEW_FILE_PREFIX) :])
            new_line = 0
            if path is not None:
                added.setdefault(path, set())
            continue
        if raw.startswith(HUNK_PREFIX):
            start = _hunk_start(raw)
            in_hunk = start is not None and path is not None
            new_line = start or 0
            continue
        if not in_hunk or path is None:
            continue
        if raw.startswith(ADDED_PREFIX):
            added[path].add(new_line)
            new_line += 1
        elif raw.startswith(("-", "\\")):
            continue
        else:
            new_line += 1
    return {changed: frozenset(lines) for changed, lines in added.items()}


def _repository_path(value: str) -> str | None:
    """Turn a ``+++`` header value into a repository-relative path, or None when deleted."""
    path = value.split("\t", 1)[0].strip()
    if not path or path == DELETED_FILE:
        return None
    for prefix in ("a/", REPOSITORY_ROOT):
        if path.startswith(prefix):
            return path[len(prefix) :]
    return path


def _hunk_start(line: str) -> int | None:
    """Return the new-side first line of a ``@@ -a,b +c,d @@`` header."""
    match = _HUNK_START.search(line)
    return int(match.group(1)) if match is not None else None
