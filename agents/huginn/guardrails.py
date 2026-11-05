"""Huginn's own guardrails on reproduction: the runner, the command and the run limit.

The runtime bounds turns, tokens and call counts; these rules are about what a run means. A
refused ``start_repro`` comes back to the model as an ``error: ...`` result, so it can correct
itself instead of crashing the job.
"""

from collections.abc import Mapping

from agents._toolbox import AgentTool
from heimdall.modules.repro.api import MAX_REPRO_RUNS


class ReproGuard:
    """Refuse a run that would leave the allowed runner or the three-run bound."""

    def __init__(self, allowed_runners: tuple[str, ...]) -> None:
        """Keep the repository's allowed runners and start with no runs recorded.

        Args:
            allowed_runners: Runner prefixes from validated repository config.
        """
        self._allowed_runners = allowed_runners
        self._runner: str | None = None
        self._starts = 0

    def refusal(self, arguments: Mapping[str, object]) -> str | None:
        """Say why this ``start_repro`` must be refused, or None when it may run.

        Args:
            arguments: The model's parsed arguments for ``start_repro``.

        Returns:
            One line naming the rule broken, or None. The command must start with an allowed
            runner, the runner may not change after the first run, and one PR head may have
            at most ``MAX_REPRO_RUNS`` runs.
        """
        command = arguments.get("test_cmd")
        runner = _runner_of(command, self._allowed_runners) if isinstance(command, str) else None
        if runner is None:
            return "start_repro: test_cmd must start with an allowed runner: " + _allowed(
                self._allowed_runners
            )
        if self._runner is not None and runner != self._runner:
            return "start_repro: only the command may change between runs, not the runner"
        if self._starts >= MAX_REPRO_RUNS:
            return f"start_repro: this PR head already has {MAX_REPRO_RUNS} runs"
        return None

    def record(self, command: str) -> None:
        """Count one accepted run and remember the runner it used."""
        self._runner = _runner_of(command, self._allowed_runners)
        self._starts += 1


def guard_start_repro(guard: ReproGuard, start: AgentTool) -> AgentTool:
    """Wrap ``start_repro`` so a refused call reaches the model as an error result.

    Args:
        guard: The run's guard, keeping the runner and the count.
        start: The toolbox tool that queues a run, unchanged when the guard allows it.

    Returns:
        A tool with the same spec whose handler checks the guard first.
    """

    async def handler(arguments: Mapping[str, object]) -> object:
        refusal = guard.refusal(arguments)
        if refusal is not None:
            return f"error: {refusal}"
        command = arguments.get("test_cmd")
        guard.record(command if isinstance(command, str) else "")
        return await start.handler(arguments)

    return AgentTool(start.spec, handler)


def _runner_of(command: object, allowed_runners: tuple[str, ...]) -> str | None:
    """Return the allowed runner a command starts with, or None."""
    if not isinstance(command, str):
        return None
    for runner in allowed_runners:
        if command == runner or command.startswith(f"{runner} "):
            return runner
    return None


def _allowed(allowed_runners: tuple[str, ...]) -> str:
    """Join the allowed runner names for an error line, never the model's own command."""
    return ", ".join(repr(runner) for runner in allowed_runners)
