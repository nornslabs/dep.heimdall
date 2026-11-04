"""Fake GitHub and Switch for the e2e replay, seeded with recorded data from the demo repo.

The GitHub replies under ``tests/fixtures/e2e/<pr>/`` were recorded from
``nornslabs/swaitchai-fastapi`` PRs #3, #4 and #5 with ``gh api`` and trimmed to the fields
Heimdall reads. Webhook payloads are built here in GitHub's real shape from those replies.
Repro artifacts are scripted per PR, since the paired workflow has never run on the demo repo.
"""

import asyncio
import io
import json
import zipfile
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx2

from heimdall.adapters.db.outbox.models import OutboxMessage
from heimdall.adapters.switch.ports import RoomEvent, RoomPost
from tests.integration.adapters.github.fake_github import FakeGitHub, json_response

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "e2e"
REPO_FULL_NAME = "nornslabs/swaitchai-fastapi"
REPO_ID = 1_440_790_551
INSTALLATION_ID = 98_765_001
MAINTAINER = "sboy99"
# main's tip when PRs #3-#5 were opened; a dispatched repro run reports this as its head_sha.
DEMO_MAIN_SHA = "3159115d345ebd5d809fa347da45d3bf6e9b1cfe"
ROOM_ID = "triage-room"
REPRO_WORKFLOW = "heimdall-repro.yml"
REPRO_WORKFLOW_PATH = f".github/workflows/{REPRO_WORKFLOW}"
DIFF_MEDIA_TYPE = "application/vnd.github.diff"
# Open PRs by the author when the three seeds were opened; the live count has drifted since.
OPEN_PRS_BY_AUTHOR = 3
FIRST_WORKFLOW_RUN_ID = 50_000
FIRST_COMMENT_ID = 70_000
REPO_PATH = f"/repos/{REPO_FULL_NAME}"
NO_ROOM_EVENTS: tuple[RoomEvent, ...] = ()


@dataclass(frozen=True)
class ReproScript:
    """What the paired workflow reports for every attempt on one PR."""

    with_fix_passed: bool
    without_fix_passed: bool
    failures: tuple[str, ...] = ()


@dataclass(frozen=True)
class SeededPr:
    """One recorded demo PR: its GitHub replies and the repro outcome the workflow reports."""

    name: str
    pull: dict[str, object]
    repro: ReproScript

    @property
    def number(self) -> int:
        """GitHub PR number."""
        return int(str(self.pull["number"]))

    @property
    def title(self) -> str:
        """PR title as recorded."""
        return str(self.pull["title"])

    @property
    def head_sha(self) -> str:
        """Head commit the PR was recorded at."""
        return str(as_object(self.pull["head"])["sha"])

    @property
    def base_sha(self) -> str:
        """Tip of the base branch when the PR was recorded."""
        return str(as_object(self.pull["base"])["sha"])

    def read(self, filename: str) -> str:
        """Return one recorded fixture file as text."""
        return (FIXTURES / self.name / filename).read_text(encoding="utf-8")

    def read_json(self, filename: str) -> object:
        """Return one recorded fixture file parsed as JSON."""
        return json.loads(self.read(filename))

    def has(self, filename: str) -> bool:
        """Say whether this PR has a recorded fixture file."""
        return (FIXTURES / self.name / filename).exists()


def load_seeded_pr(name: str, repro: ReproScript) -> SeededPr:
    """Load a recorded PR by its fixture folder name.

    Args:
        name: Folder under ``tests/fixtures/e2e``.
        repro: Outcome the fake workflow reports for this PR.

    Returns:
        The seeded PR with its recorded ``pull.json``.
    """
    pull = json.loads((FIXTURES / name / "pull.json").read_text(encoding="utf-8"))
    return SeededPr(name, as_object(pull), repro)


def pull_request_webhook(pr: SeededPr, action: str) -> dict[str, object]:
    """Build a ``pull_request`` webhook body in GitHub's shape from the recorded PR."""
    return {
        "action": action,
        "installation": {"id": INSTALLATION_ID},
        "repository": {"id": REPO_ID, "full_name": REPO_FULL_NAME},
        "pull_request": pr.pull,
    }


def workflow_run_webhook(run_id: int, base_sha: str) -> dict[str, object]:
    """Build a completed ``workflow_run`` webhook body in GitHub's shape.

    GitHub puts the workflow file path on ``workflow_run.path`` and on the top-level
    ``workflow`` object. A ``workflow_dispatch`` run is checked out at the dispatched ref, so
    ``head_sha`` is the base branch tip; the PR head only travels in the workflow inputs.
    """
    return {
        "action": "completed",
        "installation": {"id": INSTALLATION_ID},
        "repository": {"id": REPO_ID, "full_name": REPO_FULL_NAME},
        "workflow": {"name": "heimdall-repro", "path": REPRO_WORKFLOW_PATH},
        "workflow_run": {
            "id": run_id,
            "name": "heimdall-repro",
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "success",
            "head_branch": "main",
            "head_sha": base_sha,
            "path": REPRO_WORKFLOW_PATH,
        },
    }


@dataclass
class DemoGitHub:
    """The demo repo as GitHub would answer for it, recording every write Heimdall makes."""

    api: FakeGitHub = field(default_factory=FakeGitHub)
    finished_runs: list[int] = field(default_factory=list[int])
    _next_run_id: int = FIRST_WORKFLOW_RUN_ID

    def seed(self, pr: SeededPr) -> None:
        """Serve every recorded read for one PR and accept its repro dispatches."""
        pull_path = f"{REPO_PATH}/pulls/{pr.number}"
        pull, diff = pr.pull, pr.read("pull.diff")
        self.api.on("GET", pull_path, lambda request: _pull_or_diff(request, pull, diff))
        self.api.on_json("GET", f"{pull_path}/files", pr.read_json("files.json"))
        commit_path = f"{REPO_PATH}/commits/{pr.head_sha}"
        self.api.on_json("GET", f"{commit_path}/status", pr.read_json("status.json"))
        self.api.on_json("GET", f"{commit_path}/check-runs", pr.read_json("check_runs.json"))
        config = pr.read_json("heimdall_yml.json")
        self.api.on_json("GET", f"{REPO_PATH}/contents/heimdall.yml", config)
        self.api.on_json("GET", "/search/issues", {"total_count": OPEN_PRS_BY_AUTHOR})
        if pr.has("issue.json"):
            issue = as_object(pr.read_json("issue.json"))
            self.api.on_json("GET", f"{REPO_PATH}/issues/{issue['number']}", issue)
        comment_id = FIRST_COMMENT_ID + pr.number
        comment_url = (
            f"https://github.com/{REPO_FULL_NAME}/pull/{pr.number}#issuecomment-{comment_id}"
        )
        comment = {"id": comment_id, "html_url": comment_url}
        self.api.on_json("POST", f"{REPO_PATH}/issues/{pr.number}/comments", comment, 201)
        dispatch_path = f"{REPO_PATH}/actions/workflows/{REPRO_WORKFLOW}/dispatches"
        self.api.on("POST", dispatch_path, lambda request: self._dispatch(request, pr.repro))

    def comments_posted(self, number: int) -> list[str]:
        """Return the body of every comment Heimdall posted on a PR."""
        bodies: list[str] = []
        for request in self.api.requests_to(f"{REPO_PATH}/issues/{number}/comments"):
            if request.method == "POST":
                bodies.append(str(as_object(json.loads(request.content))["body"]))
        return bodies

    def writes(self) -> list[httpx2.Request]:
        """Return every write request except installation-token minting."""
        return [
            request
            for request in self.api.requests
            if request.method != "GET" and not request.url.path.startswith("/app/")
        ]

    def take_finished_runs(self) -> list[int]:
        """Return workflow runs that finished since the last call, oldest first."""
        runs, self.finished_runs = self.finished_runs, []
        return runs

    def _dispatch(self, request: httpx2.Request, script: ReproScript) -> httpx2.Response:
        """Accept a dispatch and finish its run at once with the scripted outcome."""
        inputs = as_object(as_object(json.loads(request.content))["inputs"])
        repro_run_id = str(inputs["repro_run_id"])
        run_id = self._next_run_id
        self._next_run_id += 1
        archive = _artifact_zip(repro_run_id, script)
        artifact = {"id": run_id, "name": f"heimdall-repro-{repro_run_id}", "expired": False}
        self.api.on_json(
            "GET", f"{REPO_PATH}/actions/runs/{run_id}/artifacts", {"artifacts": [artifact]}
        )
        self.api.on(
            "GET",
            f"{REPO_PATH}/actions/artifacts/{run_id}/zip",
            lambda _request: httpx2.Response(200, content=archive),
        )
        self.finished_runs.append(run_id)
        return httpx2.Response(204)


@dataclass
class FakeSwitch:
    """A Switch room that records outbox posts and answers the bridge's lookups."""

    room_posts: list[str] = field(default_factory=list[str])
    posts_by_id: dict[str, RoomPost] = field(default_factory=dict[str, RoomPost])
    users: dict[str, str] = field(default_factory=dict[str, str])
    thread_replies: list[str] = field(default_factory=list[str])

    async def send(self, message: OutboxMessage) -> None:
        """Deliver one Switch outbox row to the room."""
        self.room_posts.append(str(message.payload["message"]))

    def post(self, message: str) -> str:
        """Put a message in the room as Heimdall's bridge would, returning its post id."""
        post_id = f"post-{len(self.posts_by_id) + 1}"
        self.posts_by_id[post_id] = RoomPost(post_id, ROOM_ID, "", message)
        self.room_posts.append(message)
        return post_id

    async def open_thread(self, channel_id: str, message: str) -> str:
        """Start a thread with a root post."""
        return self.post(message)

    async def post_to_role(self, channel_id: str, role: str, message: str) -> str:
        """Post a message addressed to an agent role."""
        return self.post(message)

    async def react(self, post_id: str, emoji_name: str) -> None:
        """Accept a reaction; the replay never reads Heimdall's own reactions."""

    def listen(self, stop: asyncio.Event) -> AsyncIterator[RoomEvent]:
        """Deliver no live events; the replay hands events to the bridge directly."""
        return _no_events(stop)

    async def get_post(self, post_id: str) -> RoomPost:
        """Return a post the room holds."""
        return self.posts_by_id[post_id]

    async def get_username(self, user_id: str) -> str:
        """Return the login behind a Switch user id."""
        return self.users[user_id]

    async def post_to_thread(self, channel_id: str, root_id: str, message: str) -> str:
        """Record a bridge reply in a thread."""
        self.thread_replies.append(message)
        return f"reply-{len(self.thread_replies)}"


def as_object(value: object) -> dict[str, object]:
    """Narrow a parsed JSON value to an object, failing loudly on a bad fixture."""
    if not isinstance(value, dict):
        raise TypeError("fixture value must be a JSON object")
    items: dict[object, object] = value  # pyright: ignore[reportUnknownVariableType]
    return {str(key): item for key, item in items.items()}


async def _no_events(stop: asyncio.Event) -> AsyncGenerator[RoomEvent]:
    """An event stream that stays empty until stopped."""
    await stop.wait()
    for event in NO_ROOM_EVENTS:
        yield event


def _pull_or_diff(request: httpx2.Request, pull: object, diff: str) -> httpx2.Response:
    """Answer the PR endpoint as JSON or as a diff, by the Accept header like GitHub does."""
    if DIFF_MEDIA_TYPE in request.headers.get("accept", ""):
        return httpx2.Response(200, text=diff, headers={"content-type": DIFF_MEDIA_TYPE})
    return json_response(200, pull)


def _artifact_zip(repro_run_id: str, script: ReproScript) -> bytes:
    """Pack the three files the paired workflow uploads."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("run-id", repro_run_id)
        archive.writestr("with-fix.json", _outcome(script.with_fix_passed, script.failures))
        archive.writestr("without-fix.json", _outcome(script.without_fix_passed, script.failures))
    return buffer.getvalue()


def _outcome(passed: bool, failures: tuple[str, ...]) -> str:
    """Render one side of the paired result; failures only appear on a failing side."""
    return json.dumps(
        {"passed": passed, "failures": [] if passed else list(failures), "duration_seconds": 1.5}
    )
