from pathlib import Path

import pytest

from heimdall.adapters.github.auth import load_private_key
from heimdall.shared.errors import HeimdallError

SECRET_DIR = "very-secret-dir"


def test_load_private_key_reads_the_file(tmp_path: Path) -> None:
    key_file = tmp_path / "app.pem"
    key_file.write_text("-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n")

    assert load_private_key(str(key_file)).startswith("-----BEGIN PRIVATE KEY-----")


def test_load_private_key_error_does_not_reveal_the_path(tmp_path: Path) -> None:
    missing = tmp_path / SECRET_DIR / "app.pem"

    with pytest.raises(HeimdallError) as raised:
        load_private_key(str(missing))

    assert SECRET_DIR not in str(raised.value)
    assert raised.value.__cause__ is None


def test_load_private_key_rejects_a_file_that_is_not_a_pem(tmp_path: Path) -> None:
    key_file = tmp_path / "app.pem"
    key_file.write_text("not a key")

    with pytest.raises(HeimdallError, match="not a PEM private key"):
        load_private_key(str(key_file))
