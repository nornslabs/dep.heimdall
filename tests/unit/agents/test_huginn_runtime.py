"""Huginn on the runtime: the three seeded PRs and the verdict cross-check."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import cast

from agents._outcome import Completed, CouldNotFinish, RunOutcome
from agents._runtime import AgentBudget, ToolBox
from agents._skills import load_skills
from agents._toolbox import AgentTool
from agents.huginn._message import render_verdict
from agents.huginn.agent import run_huginn
from agents.huginn.allowed_tools import ALLOWED_TOOLS
from agents.huginn.config import BUDGET, REQUIRED_SKILLS
from agents.huginn.context import build_messages, build_repro_brief
from agents.huginn.schemas import HuginnVerdict, submit_verdict_tool
from heimdall.adapters.ai import ChatResponse, ToolCall, ToolSpec
from heimdall.shared.agents import AgentName
from tests.e2e.fake_world import ReproScript, SeededPr, load_seeded_pr
from tests.unit.agents.scripted_ai import ScriptedAi, tool_reply

INSTALLATION_ID = 42
PR_ID = "00000000-0000-0000-0000-000000000002"
RUN_ID = "00000000-0000-0000-0000-000000000001"
ASSERTION = "tests/test_register_request.py::test_register_request - NameError"
UNTESTED = AgentBudget(context_tokens=24_000, run_tokens=150_000, max_turns=12)

_FAILURES = (ASSERTION,)


class FakeReproTools:
    """Serve the repro tools the way the MCP server does, keeping every started run."""

    def __init__(self, *, with_fix_passed: bool, without_fix_passed: bool) -> None:
        """Fix what every run reports in each mode."""
        self.with_fix_passed = with_fix_passed
        self.without_fix_passed = without_fix_passed
        self.runs: dict[str, dict[str, object]] = {}
        self.starts: list[dict[str, object]] = []

    async def start_repro(self, arguments: Mapping[str, object]) -> dict[str, object]:
        """Queue one run and return its identity, as the MCP tool does."""
        run_id = f"00000000-0000-0000-0000-00000000000{len(self.runs) + 1}"
        self.starts.append(dict(arguments))
        self.runs[run_id] = {
            "run_id": run_id,
            "head_sha": arguments.get("head_sha"),
            "status": "finished",
            "with_fix": _mode(self.with_fix_passed),
            "without_fix": _mode(self.without_fix_passed),
        }
        return {
            "run_id": run_id,
            "attempt": len(self.runs),
            "head_sha": arguments.get("head_sha"),
        }

    async def get_repro(self, installation_id: int, run_id: str) -> dict[str, object]:
        """Return one saved run, tenant-scoped the way the verdict cross-check expects."""
        assert installation_id == INSTALLATION_ID
        return self.runs[run_id]


def _mode(passed: bool) -> dict[str, object]:
    """Build one mode's saved outcome: a pass, or the recorded assertion failure."""
    return {"passed": passed, "failures": [] if passed else list(_FAILURES)}


def _tool(name: str, handler: Callable[[Mapping[str, object]], Awaitable[object]]) -> AgentTool:
    """Describe one fake MCP tool with a permissive schema, so only the logic is tested."""
    return AgentTool(ToolSpec(name, f"{name} tool.", {"type": "object"}), handler)


def _seed_tools(world: FakeReproTools, pr: SeededPr) -> tuple[AgentTool, ...]:
    """Provide the read and repro tools Huginn needs, from the recorded PR."""

    async def get_pr_context(_arguments: Mapping[str, object]) -> object:
        return {
            "pr_id": PR_ID,
            "number": pr.number,
            "head_sha": pr.head_sha,
            "title": pr.title,
            "body": pr.pull["body"],
        }

    async def get_diff(_arguments: Mapping[str, object]) -> object:
        return {"text": pr.read("pull.diff"), "truncated": False}

    async def get_file(_arguments: Mapping[str, object]) -> object:
        return {
            "found": True,
            "path": "switch_agent/http_client.py",
            "content": "def register():\n",
        }

    async def start_repro(arguments: Mapping[str, object]) -> object:
        return await world.start_repro(arguments)

    async def get_repro(arguments: Mapping[str, object]) -> object:
        return await world.get_repro(INSTALLATION_ID, str(arguments["run_id"]))

    return (
        _tool("get_pr_context", get_pr_context),
        _tool("get_diff", get_diff),
        _tool("get_file", get_file),
        _tool("start_repro", start_repro),
        _tool("get_repro", get_repro),
    )


def _head_reader(head_sha: str) -> Callable[[], Awaitable[str]]:
    """Return a head check that never moves."""

    async def read() -> str:
        return head_sha

    return read


def _submit(
    verdict: str,
    *,
    run_ids: tuple[str, ...],
    with_fix: dict[str, int],
    without_fix: dict[str, int],
    summary: str,
    failing_assertion: str = "",
) -> dict[str, object]:
    """Build one ``submit_verdict`` argument set."""
    return {
        "verdict": verdict,
        "test_cmd": "pytest -q",
        "repro_ids": list(run_ids),
        "with_fix": with_fix,
        "without_fix": without_fix,
        "failing_assertion": failing_assertion,
        "summary": summary,
    }


def _start(pr: SeededPr, test_cmd: str) -> dict[str, object]:
    """Build one ``start_repro`` argument set for the seeded PR."""
    return {
        "installation_id": INSTALLATION_ID,
        "pr_id": PR_ID,
        "head_sha": pr.head_sha,
        "test_cmd": test_cmd,
    }


def _changed_paths(pr: SeededPr) -> list[str]:
    """Read the recorded file list for a seeded PR."""
    rows = cast(list[dict[str, object]], pr.read_json("files.json"))
    return [str(row["filename"]) for row in rows]


async def _run(
    pr: SeededPr,
    world: FakeReproTools,
    replies: Sequence[ChatResponse],
    *,
    tools: tuple[AgentTool, ...] | None = None,
    budget: AgentBudget = BUDGET,
) -> RunOutcome[HuginnVerdict]:
    """Run Huginn once on the seeded PR, with a scripted model and its real toolbox."""
    skills = load_skills(AgentName.HUGINN, ALLOWED_TOOLS, REQUIRED_SKILLS)
    toolbox = ToolBox(
        ALLOWED_TOOLS | {"load_skill"},
        (*(tools if tools is not None else _seed_tools(world, pr)), skills.tool()),
        retry_delays=(),
    )
    brief = build_repro_brief(
        installation_id=INSTALLATION_ID,
        pr_id=PR_ID,
        repo_full_name="nornslabs/swaitchai-fastapi",
        number=pr.number,
        title=pr.title,
        body=str(pr.pull["body"]),
        head_sha=pr.head_sha,
        test_cmd="pytest -q",
        changed_paths=_changed_paths(pr),
    )
    return await run_huginn(
        ai=ScriptedAi(replies),
        toolbox=toolbox,
        submit=submit_verdict_tool(world, INSTALLATION_ID, pr.head_sha),
        head_sha=pr.head_sha,
        read_current_head=_head_reader(pr.head_sha),
        messages=build_messages(brief, "You are Huginn.", skills.prompt_index()),
        budget=budget,
    )


def _good_fix() -> SeededPr:
    """The seeded PR whose test fails without the change and passes with it."""
    return load_seeded_pr(
        "good_fix",
        ReproScript(with_fix_passed=True, without_fix_passed=False, failures=_FAILURES),
    )


def _fake_fix() -> SeededPr:
    """The seeded PR whose tests pass with and without the change."""
    return load_seeded_pr("fake_fix", ReproScript(with_fix_passed=True, without_fix_passed=True))


def _rule_breaker() -> SeededPr:
    """The seeded PR intake stops, so Huginn has no configured command to run."""
    return load_seeded_pr(
        "rule_breaker", ReproScript(with_fix_passed=True, without_fix_passed=True)
    )


async def test_good_fix_is_reported_as_reproduced() -> None:
    """Start one run, read it, and submit the verdict its saved counts support."""
    pr = _good_fix()
    world = FakeReproTools(with_fix_passed=True, without_fix_passed=False)
    replies = [
        tool_reply(ToolCall("c1", "get_pr_context", {})),
        tool_reply(
            ToolCall("c2", "start_repro", _start(pr, "pytest -q tests/test_register_request.py"))
        ),
        tool_reply(
            ToolCall("c3", "get_repro", {"installation_id": INSTALLATION_ID, "run_id": RUN_ID})
        ),
        tool_reply(
            ToolCall(
                "c4",
                "submit_verdict",
                _submit(
                    "reproduced",
                    run_ids=(RUN_ID,),
                    with_fix={"passed": 1, "failed": 0, "errors": 0},
                    without_fix={"passed": 0, "failed": 1, "errors": 0},
                    failing_assertion=ASSERTION,
                    summary="The test fails without the change and passes with it.",
                ),
            )
        ),
    ]

    outcome = await _run(pr, world, replies)

    assert isinstance(outcome, Completed)
    assert outcome.payload.verdict == "reproduced"
    assert world.starts == [_start(pr, "pytest -q tests/test_register_request.py")]
    message = render_verdict(outcome.payload)
    assert message.startswith("Huginn: the paired tests reproduce the issue.")
    assert "With fix: 1 passed, 0 failed, 0 errors" in message


async def test_fake_fix_is_reported_as_not_covered() -> None:
    """Both modes passing means the paired tests never exercise the change."""
    pr = _fake_fix()
    world = FakeReproTools(with_fix_passed=True, without_fix_passed=True)
    replies = [
        tool_reply(ToolCall("c1", "get_pr_context", {})),
        tool_reply(ToolCall("c2", "start_repro", _start(pr, "pytest -q"))),
        tool_reply(
            ToolCall("c3", "get_repro", {"installation_id": INSTALLATION_ID, "run_id": RUN_ID})
        ),
        tool_reply(
            ToolCall(
                "c4",
                "submit_verdict",
                _submit(
                    "not_covered",
                    run_ids=(RUN_ID,),
                    with_fix={"passed": 1, "failed": 0, "errors": 0},
                    without_fix={"passed": 1, "failed": 0, "errors": 0},
                    summary="The paired tests pass on main, so they do not cover the change.",
                ),
            )
        ),
    ]

    outcome = await _run(pr, world, replies)

    assert isinstance(outcome, Completed)
    assert outcome.payload.verdict == "not_covered"
    assert "do not exercise the change" in render_verdict(outcome.payload)


async def test_a_repository_without_a_run_is_reported_without_starting_one() -> None:
    """A command that cannot work is a setup verdict, not a reproduction attempt."""
    pr = _rule_breaker()
    world = FakeReproTools(with_fix_passed=True, without_fix_passed=True)
    replies = [
        tool_reply(ToolCall("c1", "get_pr_context", {})),
        tool_reply(
            ToolCall(
                "c2",
                "submit_verdict",
                _submit(
                    "setup_failed",
                    run_ids=(),
                    with_fix={"passed": 0, "failed": 0, "errors": 0},
                    without_fix={"passed": 0, "failed": 0, "errors": 0},
                    summary="This repository has no reproduction command configured.",
                ),
            )
        ),
    ]

    outcome = await _run(pr, world, replies)

    assert isinstance(outcome, Completed)
    assert outcome.payload.verdict == "setup_failed"
    assert outcome.payload.repro_ids == ()
    assert world.starts == []
    assert "Runs: 0." in render_verdict(outcome.payload)


async def test_a_verdict_that_disagrees_with_the_runs_gets_one_repair_round() -> None:
    """The cross-check refuses counts that contradict saved runs, then accepts the fix."""
    pr = _good_fix()
    world = FakeReproTools(with_fix_passed=True, without_fix_passed=False)
    wrong = _submit(
        "reproduced",
        run_ids=(RUN_ID,),
        with_fix={"passed": 1, "failed": 0, "errors": 0},
        without_fix={"passed": 1, "failed": 0, "errors": 0},
        summary="The test fails without the change and passes with it.",
    )
    right = _submit(
        "reproduced",
        run_ids=(RUN_ID,),
        with_fix={"passed": 1, "failed": 0, "errors": 0},
        without_fix={"passed": 0, "failed": 1, "errors": 0},
        summary="The test fails without the change and passes with it.",
    )
    replies = [
        tool_reply(ToolCall("c1", "get_pr_context", {})),
        tool_reply(ToolCall("c2", "start_repro", _start(pr, "pytest -q"))),
        tool_reply(
            ToolCall("c3", "get_repro", {"installation_id": INSTALLATION_ID, "run_id": RUN_ID})
        ),
        tool_reply(ToolCall("c4", "submit_verdict", wrong)),
        tool_reply(ToolCall("c5", "submit_verdict", right)),
    ]

    outcome = await _run(pr, world, replies, budget=UNTESTED)

    assert isinstance(outcome, Completed)
    assert outcome.payload.without_fix.failed == 1


async def test_a_second_refused_verdict_ends_the_run() -> None:
    """The one repair round is the only second chance; a second refusal is not posted."""
    pr = _good_fix()
    world = FakeReproTools(with_fix_passed=True, without_fix_passed=False)
    wrong = _submit(
        "reproduced",
        run_ids=(RUN_ID,),
        with_fix={"passed": 1, "failed": 0, "errors": 0},
        without_fix={"passed": 1, "failed": 0, "errors": 0},
        summary="The test fails without the change and passes with it.",
    )
    replies = [
        tool_reply(ToolCall("c1", "get_pr_context", {})),
        tool_reply(ToolCall("c2", "start_repro", _start(pr, "pytest -q"))),
        tool_reply(
            ToolCall("c3", "get_repro", {"installation_id": INSTALLATION_ID, "run_id": RUN_ID})
        ),
        tool_reply(ToolCall("c4", "submit_verdict", wrong)),
        tool_reply(ToolCall("c5", "submit_verdict", wrong)),
    ]

    outcome = await _run(pr, world, replies, budget=UNTESTED)

    assert isinstance(outcome, CouldNotFinish)
    assert outcome.reason == "invalid_submission"
