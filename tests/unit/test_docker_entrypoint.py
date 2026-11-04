"""The container entrypoint must start modules that run when launched with ``python -m``."""

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO_ROOT / "docker" / "entrypoint.sh"
PYTHON_ROLES = ("api", "worker", "mcp", "bridge", "huginn", "loki", "heimdall")
MAIN_GUARD = 'if __name__ == "__main__"'


def _module_the_role_launches(role: str, stub_dir: Path) -> str:
    """Run the entrypoint with a stub ``python`` and return the module it was asked to run.

    Args:
        role: The container role passed as the entrypoint's first argument.
        stub_dir: Folder to hold the stub ``python``; put first on ``PATH``.

    Returns:
        The dotted module name that followed ``python -m``.
    """
    stub = stub_dir / "python"
    stub.write_text('#!/bin/sh\necho "$2"\n')
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"}
    result = subprocess.run(
        ["sh", str(ENTRYPOINT), role],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return result.stdout.strip()


@pytest.mark.parametrize("role", PYTHON_ROLES)
def test_entrypoint_launches_a_module_with_a_main_guard(role: str, tmp_path: Path) -> None:
    module = _module_the_role_launches(role, tmp_path)

    source = (REPO_ROOT / Path(*module.split("."))).with_suffix(".py")

    assert source.is_file(), f"{role} launches {module}, which does not exist"
    assert MAIN_GUARD in source.read_text(), (
        f"{role} launches {module}, which has no main guard, so the container exits at once"
    )
