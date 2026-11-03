"""Codex runner uses fixed CLI args and sends prompt only through stdin."""

import asyncio
import runpy
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CodexRunner = runpy.run_path(str(REPOSITORY_ROOT / "agents/loki/runner.py"))["CodexRunner"]


class FakeProcess:
    """Capture stdin without launching a shell process."""

    returncode = 0

    def __init__(self) -> None:
        self.prompt = b""

    async def communicate(self, prompt: bytes) -> tuple[bytes, bytes]:
        self.prompt = prompt
        return b'{"findings": []}', b""


@pytest.mark.asyncio
async def test_codex_prompt_is_stdin_and_cli_arguments_are_fixed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never interpolate PR data into a shell command or executable arguments."""
    process = FakeProcess()
    command: list[str] = []

    async def create_process(*args: str, **kwargs: object) -> FakeProcess:
        command.extend(args)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    output = await CodexRunner().review("untrusted PR text")

    assert output == '{"findings": []}'
    assert process.prompt == b"untrusted PR text"
    assert "untrusted PR text" not in command
    assert command == [
        "codex",
        "exec",
        "--sandbox",
        "read-only",
        "--ask-for-approval",
        "never",
        "--skip-git-repo-check",
        "-",
    ]
