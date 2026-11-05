"""Replay the three seeded demo PRs through the whole pipeline, as the demo shows them.

Each test opens a recorded PR, lets the jobs, agents and outbox run, and checks what reached
the room and GitHub. Agents run their real loops; only Loki's model reply is scripted.
"""

import json

import pytest

from apps.bridge.approval import NON_MAINTAINER_REPLY
from tests.e2e.fake_world import (
    MAINTAINER,
    DemoGitHub,
    FakeSwitch,
    ReproScript,
    load_seeded_pr,
)
from tests.e2e.pipeline import Pipeline

pytestmark = pytest.mark.e2e

GOOD_FIX = load_seeded_pr(
    "good_fix",
    ReproScript(
        with_fix_passed=True,
        without_fix_passed=False,
        failures=("tests/test_register_request.py::test_register_request - NameError",),
    ),
)
FAKE_FIX = load_seeded_pr("fake_fix", ReproScript(with_fix_passed=True, without_fix_passed=True))
RULE_BREAKER = load_seeded_pr(
    "rule_breaker", ReproScript(with_fix_passed=True, without_fix_passed=True)
)

NO_FINDINGS = json.dumps({"findings": []})
UNUSED_IMPORT_FINDING = json.dumps(
    {
        "findings": [
            {
                "file": "switch_agent/config.py",
                "line": 3,
                "text": "`re` is imported but never used.",
                "severity": "warning",
                "mechanical_category": "lint",
            }
        ]
    }
)
OUTSIDER = "drive-by-reviewer"
HUGINN_REPRODUCED = "Huginn: the paired tests reproduce the issue."
HUGINN_NOT_COVERED = "Huginn: the paired tests do not exercise the change."


async def assert_nothing_failed(pipeline: Pipeline) -> None:
    """Every job and outbox delivery in the replay succeeded."""
    assert await pipeline.failed_jobs() == []
    assert await pipeline.outbox_errors() == []


async def test_good_fix_card_says_reproduced_and_posts_only_after_approval(
    pipeline: Pipeline, github: DemoGitHub, switch: FakeSwitch
) -> None:
    """PR #3: intake passes, the paired run confirms the fix, and 👍 posts the exact card."""
    await pipeline.open_pr(GOOD_FIX, NO_FINDINGS)
    assert await pipeline.pr_state(GOOD_FIX) == "checking"
    assert switch.room_posts[0].startswith(f"New PR #{GOOD_FIX.number} ")
    assert "Ready for reproduction and review." in switch.room_posts[1]

    await pipeline.run_huginn()
    assert any(post.startswith(HUGINN_REPRODUCED) for post in switch.room_posts)
    assert await pipeline.pr_state(GOOD_FIX) == "card_posted"

    post_id = switch.approval_post_id()
    assert "Fix confirmed" in switch.posts_by_id[post_id].message
    # The card is addressed to the repo's maintainers, so the room notifies them.
    assert switch.posts_by_id[post_id].message.startswith(f"@{MAINTAINER}, your review is needed:")
    assert github.comments_posted(GOOD_FIX.number) == []
    # Each agent's own posts name that agent; Heimdall posts the checklist that opens the thread.
    assert switch.agent_who_posted(HUGINN_REPRODUCED) == "huginn"
    assert switch.agent_who_posted(switch.posts_by_id[post_id].message[:20]) == "heimdall"
    assert switch.agent_who_posted("Intake checklist for pull request") == "heimdall"
    assert switch.agent_who_posted("Loki: reviewed") == "loki"
    # One thread per PR: the heading is the only root, and every agent post names its PR.
    agent_rows = [row for row in switch.payloads if row.get("agent")]
    roots = [row for row in agent_rows if row.get("thread_role") == "root"]
    assert [str(row["message"]).startswith(f"New PR #{GOOD_FIX.number}") for row in roots] == [True]
    checklist_rows = [r for r in agent_rows if str(r["message"]).startswith("Intake checklist")]
    assert len(checklist_rows) == 1
    assert "thread_role" not in checklist_rows[0]
    assert {row["agent"] for row in agent_rows} == {"heimdall", "huginn", "loki"}
    assert {row["pr_id"] for row in agent_rows} == {str(await pipeline.pr_id(GOOD_FIX))}

    await pipeline.react_thumbs_up(post_id, MAINTAINER)
    comments = github.comments_posted(GOOD_FIX.number)
    assert len(comments) == 1
    assert "Fix confirmed" in comments[0]
    await assert_nothing_failed(pipeline)


async def test_fake_fix_card_says_not_reproduced_with_loki_finding(
    pipeline: Pipeline, github: DemoGitHub, switch: FakeSwitch
) -> None:
    """PR #4: tests pass with and without the change, and Loki cites the unused import."""
    await pipeline.open_pr(FAKE_FIX, UNUSED_IMPORT_FINDING)
    await pipeline.run_huginn()
    assert any(post.startswith(HUGINN_NOT_COVERED) for post in switch.room_posts)
    assert any("1 finding saved" in post for post in switch.room_posts)
    assert await pipeline.pr_state(FAKE_FIX) == "card_posted"

    post_id = switch.approval_post_id()
    card = switch.posts_by_id[post_id].message
    assert "Issue not reproduced" in card
    # The card escapes Markdown in text that came from the PR or from Loki.
    assert r"switch\_agent/config\.py:3" in card

    await pipeline.react_thumbs_up(post_id, OUTSIDER)
    assert switch.thread_replies == [NON_MAINTAINER_REPLY]
    assert github.comments_posted(FAKE_FIX.number) == []
    await assert_nothing_failed(pipeline)


async def test_rule_breaker_fails_intake_and_writes_nothing_to_github(
    pipeline: Pipeline, github: DemoGitHub, switch: FakeSwitch
) -> None:
    """PR #5: no linked issue, assignee or label, so intake fails and no agent runs."""
    await pipeline.open_pr(RULE_BREAKER)
    await pipeline.run_huginn()

    assert await pipeline.pr_state(RULE_BREAKER) == "intake_failed"
    assert len(switch.room_posts) == 2
    assert switch.room_posts[0].startswith(f"New PR #{RULE_BREAKER.number} ")
    assert "Resolve the failed checks" in switch.room_posts[1]
    assert await pipeline.latest_repro_run(RULE_BREAKER) is None
    assert github.writes() == []
    await assert_nothing_failed(pipeline)
