"""Shared agent process runtime keeps prompts off command arguments."""

import asyncio

import pytest

from agents._runtime import run_agent_process


class FakeProcess:
    """Capture process input and termination for runtime tests."""

    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.prompt = b""
        self.killed = False
        self.waited = False

    async def communicate(self, prompt: bytes) -> tuple[bytes, bytes]:
        self.prompt = prompt
        return b"agent result", b"private stderr details"

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.waited = True
        return self.returncode


@pytest.mark.asyncio
async def test_agent_prompt_is_stdin_and_process_arguments_are_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep untrusted prompt data out of the executable argument list."""
    process = FakeProcess()
    command: list[str] = []
    options: dict[str, object] = {}

    async def create_process(*args: str, **kwargs: object) -> FakeProcess:
        command.extend(args)
        options.update(kwargs)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    output = await run_agent_process(
        ("agent", "--safe-mode"), "untrusted PR body", timeout_seconds=5
    )

    assert output == "agent result"
    assert process.prompt == b"untrusted PR body"
    assert command == ["agent", "--safe-mode"]
    assert options["cwd"] == "/tmp"
    assert options["stdin"] is asyncio.subprocess.PIPE


@pytest.mark.asyncio
async def test_agent_timeout_kills_and_reaps_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop and reap an agent process that exceeds its configured limit."""
    process = FakeProcess()

    async def create_process(*args: str, **kwargs: object) -> FakeProcess:
        return process

    async def slow_communicate(prompt: bytes) -> tuple[bytes, bytes]:
        await asyncio.sleep(1)
        return b"", b""

    process.communicate = slow_communicate  # type: ignore[method-assign]
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    with pytest.raises(RuntimeError, match="agent process timed out"):
        await run_agent_process(("agent",), "prompt", timeout_seconds=0.001)

    assert process.killed
    assert process.waited


@pytest.mark.asyncio
async def test_agent_failure_does_not_include_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid copying model or PR content from process stderr into user-visible errors."""

    async def create_process(*args: str, **kwargs: object) -> FakeProcess:
        return FakeProcess(returncode=2)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    with pytest.raises(RuntimeError, match="status 2") as raised:
        await run_agent_process(("agent",), "prompt", timeout_seconds=5)

    assert "private stderr details" not in str(raised.value)
