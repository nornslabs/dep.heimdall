"""Read-only Codex CLI adapter for Loki's untrusted PR review input."""

import asyncio

CODEX_TIMEOUT_SECONDS = 30 * 60


class CodexRunner:
    """Invoke Codex without a shell, repository checkout, or write access."""

    def __init__(self, executable: str = "codex") -> None:
        """Set the trusted Codex executable name.

        Args:
            executable: Binary name resolved by the process environment.
        """
        self._executable = executable

    async def review(self, prompt: str) -> str:
        """Run a single bounded read-only review prompt.

        Args:
            prompt: System instructions plus PR content explicitly marked as data.

        Returns:
            Codex's final response text.

        Raises:
            RuntimeError: Codex is missing, times out, or exits unsuccessfully.
        """
        try:
            process = await asyncio.create_subprocess_exec(
                self._executable,
                "exec",
                "--sandbox",
                "read-only",
                "--ask-for-approval",
                "never",
                "--skip-git-repo-check",
                "-",
                cwd="/tmp",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as error:
            raise RuntimeError("Codex CLI is not installed") from error

        try:
            stdout, _stderr = await asyncio.wait_for(
                process.communicate(prompt.encode("utf-8")),
                timeout=CODEX_TIMEOUT_SECONDS,
            )
        except TimeoutError as error:
            process.kill()
            await process.wait()
            raise RuntimeError("Codex review timed out") from error
        if process.returncode != 0:
            raise RuntimeError(f"Codex review exited with status {process.returncode}")
        return stdout.decode("utf-8")
