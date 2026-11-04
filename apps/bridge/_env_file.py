"""Read and write ``NAME=value`` lines in a private file, keeping the lines already there."""

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Final

PRIVATE_FILE_MODE: Final = 0o600
PRIVATE_DIR_MODE: Final = 0o700
_ENCODING: Final = "utf-8"


def read_env_file(path: Path) -> dict[str, str]:
    """Read ``NAME=value`` lines; comments, blanks and malformed lines are skipped.

    Args:
        path: File to read. A missing file reads as empty.

    Returns:
        Names mapped to values. A later duplicate wins, as in most dotenv parsers.
    """
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding=_ENCODING).splitlines():
        name, separator, value = line.partition("=")
        if separator and name and not name.startswith("#"):
            values[name] = value
    return values


def merge_env_file(path: Path, updates: Mapping[str, str]) -> None:
    """Set the given names in a dotenv-style file, keeping every other line as it was.

    The file is replaced atomically and is readable only by its owner, because it holds API
    keys. A crash mid-write leaves the previous file intact.

    Args:
        path: File to create or update. Missing parent folders are created private (0700).
        updates: Names and their new values.

    Raises:
        ValueError: a name is not a plain identifier, or a value holds a control character or
            line separator, which would corrupt the file.
        OSError: the file or its folder cannot be read or written.
    """
    for name, value in updates.items():
        if not name.isidentifier() or not value.isprintable():
            raise ValueError(f"cannot write {name!r} to an env file")
    existing = path.read_text(encoding=_ENCODING).splitlines() if path.exists() else []
    path.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    _write_private(path, [*_replace_known(existing, updates), *_missing(existing, updates)])


def check_writable(path: Path) -> None:
    """Fail now if a later ``merge_env_file`` on ``path`` could not succeed.

    Call this before doing anything that cannot be repeated, such as asking Switch for a key
    it will show only once.

    Args:
        path: File that will be written.

    Raises:
        OSError: the folder cannot be created, the file cannot be read, or a temporary file
            cannot be created next to it.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    if path.exists():
        path.read_text(encoding=_ENCODING)
    descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    os.close(descriptor)
    Path(temp_name).unlink()


def _replace_known(lines: list[str], updates: Mapping[str, str]) -> list[str]:
    """Return the lines with each updated name given its new value."""
    merged: list[str] = []
    for line in lines:
        name = line.split("=", 1)[0]
        merged.append(f"{name}={updates[name]}" if name in updates and "=" in line else line)
    return merged


def _missing(lines: list[str], updates: Mapping[str, str]) -> list[str]:
    """Return ``NAME=value`` lines for names not yet in the file."""
    present = {line.split("=", 1)[0] for line in lines if "=" in line}
    return [f"{name}={value}" for name, value in updates.items() if name not in present]


def _write_private(path: Path, lines: list[str]) -> None:
    """Write via a temp file created private from the start, then swap it into place."""
    descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "w", encoding=_ENCODING) as handle:
            handle.write("\n".join(lines) + "\n")
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
