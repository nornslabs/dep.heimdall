"""Shared bounded, shell-free process runtime for model-backed agents."""

import asyncio
from collections.abc import Sequence


async def run_agent_process(
    arguments: Sequence[str],
    prompt: str,
    *,
    timeout_seconds: float,
    working_directory: str = "/tmp",
) -> str:
    """Run one agent process with a prompt on stdin and a strict wall-clock limit.

    Args:
        arguments: Executable and arguments passed directly without a shell.
        prompt: Agent prompt supplied on stdin.
        timeout_seconds: Maximum process runtime; must be positive.
        working_directory: Directory used as the process working directory.

    Returns:
        The process stdout decoded as UTF-8.

    Raises:
        ValueError: The timeout is not positive or the command is empty.
        RuntimeError: The executable is missing, times out, or exits unsuccessfully.
    """
    if not arguments or timeout_seconds <= 0:
        raise ValueError("agent command and positive timeout are required")
    try:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            cwd=working_directory,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as error:
        raise RuntimeError("agent executable is not installed") from error

    try:
        stdout, _stderr = await asyncio.wait_for(
            process.communicate(prompt.encode("utf-8")),
            timeout=timeout_seconds,
        )
    except TimeoutError as error:
        process.kill()
        await process.wait()
        raise RuntimeError("agent process timed out") from error
    if process.returncode != 0:
        raise RuntimeError(f"agent process exited with status {process.returncode}")
    return stdout.decode("utf-8")
