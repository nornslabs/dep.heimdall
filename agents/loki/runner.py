"""Read-only Codex CLI adapter for Loki's untrusted PR review input."""

from agents._runtime import run_agent_process

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
        """Run one bounded, read-only review prompt.

        Args:
            prompt: System instructions plus PR content explicitly marked as data.

        Returns:
            Codex's final response text.

        Raises:
            RuntimeError: Codex is missing, times out, or exits unsuccessfully.
        """
        return await run_agent_process(
            (
                self._executable,
                "exec",
                "--sandbox",
                "read-only",
                "--ask-for-approval",
                "never",
                "--skip-git-repo-check",
                "-",
            ),
            prompt,
            timeout_seconds=CODEX_TIMEOUT_SECONDS,
        )
