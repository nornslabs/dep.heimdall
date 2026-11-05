"""Nothing an agent does reaches GitHub; only an approval with a matching hash does."""

import dataclasses

import pytest

from agents.heimdall.allowed_tools import ACT_TOOLS as HEIMDALL_ACT_TOOLS
from agents.heimdall.allowed_tools import ALLOWED_TOOLS as HEIMDALL_TOOLS
from agents.huginn.allowed_tools import ACT_TOOLS as HUGINN_ACT_TOOLS
from agents.huginn.allowed_tools import ALLOWED_TOOLS as HUGINN_TOOLS
from agents.loki.allowed_tools import ACT_TOOLS as LOKI_ACT_TOOLS
from agents.loki.allowed_tools import ALLOWED_TOOLS as LOKI_TOOLS
from tests.e2e.fake_world import MAINTAINER, DemoGitHub, FakeSwitch, ReproScript, load_seeded_pr
from tests.e2e.pipeline import Pipeline

pytestmark = pytest.mark.e2e

# The schema lives for the whole session and a PR number is opened once, so this test uses
# its own copy of the recorded good-fix PR.
SAFETY_PR_NUMBER = 18
RECORDED_GOOD_FIX = load_seeded_pr(
    "good_fix",
    ReproScript(with_fix_passed=True, without_fix_passed=False, failures=("test_x - NameError",)),
)
GOOD_FIX = dataclasses.replace(
    RECORDED_GOOD_FIX, pull={**RECORDED_GOOD_FIX.pull, "number": SAFETY_PR_NUMBER}
)
# Words a tool that writes to GitHub would carry; no agent tool may name one.
GITHUB_WRITE_WORDS = ("comment", "review_pr", "merge", "approve", "label", "push", "close", "post")
# These only save a draft of a fixed kind; GitHub is written by the outbox sender after approval.
# Listed by exact name so a new tool with a write word in its name still fails this test.
DRAFT_ONLY_TOOLS = frozenset(
    {"draft_close", "draft_reopen", "draft_merge", "draft_ask_contributor"}
)
MEMORY_WRITE_TOOLS = frozenset({"remember", "forget"})
AGENT_RUN_TOOLS = {"huginn": HUGINN_TOOLS, "loki": LOKI_TOOLS, "heimdall": HEIMDALL_TOOLS}
AGENT_ACT_TOOLS = {
    "huginn": HUGINN_ACT_TOOLS,
    "loki": LOKI_ACT_TOOLS,
    "heimdall": HEIMDALL_ACT_TOOLS,
}


@pytest.mark.parametrize("agent", sorted(AGENT_RUN_TOOLS))
def test_no_agent_has_a_github_write_tool(agent: str) -> None:
    """No tool an agent can call, in a PR run or in act mode, is named for a GitHub write."""
    tools = AGENT_RUN_TOOLS[agent] | AGENT_ACT_TOOLS[agent]

    offenders = {
        tool
        for tool in tools - DRAFT_ONLY_TOOLS
        if any(word in tool for word in GITHUB_WRITE_WORDS)
    }

    assert offenders == set()


@pytest.mark.parametrize("agent", sorted(AGENT_RUN_TOOLS))
def test_pr_triggered_runs_cannot_write_memory(agent: str) -> None:
    """The wake-up run an agent gets from a PR event has no tool that writes a note."""
    assert AGENT_RUN_TOOLS[agent] & MEMORY_WRITE_TOOLS == set()


async def test_github_sees_no_comment_until_the_matching_approval_arrives(
    pipeline: Pipeline, github: DemoGitHub, switch: FakeSwitch
) -> None:
    """Every agent has run and the card is up, yet GitHub has no comment until the 👍."""
    await pipeline.open_pr(GOOD_FIX)
    await pipeline.run_huginn()
    assert github.comments_posted(GOOD_FIX.number) == []

    await pipeline.react_thumbs_up(switch.approval_post_id(), MAINTAINER)

    assert len(github.comments_posted(GOOD_FIX.number)) == 1
