"""Read paired reproduction results from trusted workflow artifacts."""

import io
import json
import zipfile
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from heimdall.adapters.github._errors import github_errors
from heimdall.adapters.github._http import get, get_bytes
from heimdall.adapters.github.auth import GitHubAppClients
from heimdall.shared.github import RepoRef

MAX_ARTIFACT_BYTES = 1_000_000
EXPECTED_FILES = frozenset({"run-id", "with-fix.json", "without-fix.json"})


@dataclass(frozen=True)
class ArtifactOutcome:
    """Validated test outcome saved by the reproduction workflow."""

    passed: bool
    failures: tuple[str, ...]
    duration_seconds: float


@dataclass(frozen=True)
class PairedArtifact:
    """One workflow's run identity and paired outcomes."""

    run_id: str
    with_fix: ArtifactOutcome
    without_fix: ArtifactOutcome


class GitHubActionsResults:
    """Download and validate a workflow's small paired result artifact."""

    def __init__(self, clients: GitHubAppClients) -> None:
        """Bind the installation-scoped GitHub client factory."""
        self._clients = clients

    async def get_paired_artifact(
        self,
        repo: RepoRef,
        workflow_run_id: int,
    ) -> PairedArtifact:
        """Load the reproduction artifact attached to one completed workflow.

        Args:
            repo: Repository and installation that own the workflow run.
            workflow_run_id: GitHub Actions run id from the signed webhook.

        Returns:
            Validated paired test outcomes.

        Raises:
            ValueError: The artifact is missing, oversized, or malformed.
        """
        client = self._clients.for_installation(repo.installation_id)
        artifact_id, expected_repro_run_id = await self._find_artifact_id(repo, workflow_run_id)
        async with github_errors("download reproduction artifact"):
            archive = await get_bytes(
                client,
                f"/repos/{repo.full_name}/actions/artifacts/{artifact_id}/zip",
            )
        if len(archive) > MAX_ARTIFACT_BYTES:
            raise ValueError("reproduction artifact exceeds the size limit")
        return _parse_artifact(archive, expected_repro_run_id)

    async def _find_artifact_id(
        self,
        repo: RepoRef,
        workflow_run_id: int,
    ) -> tuple[int, str]:
        """Find the named artifact attached to this exact workflow run."""
        async with github_errors("list reproduction artifacts"):
            response = await get(
                self._clients.for_installation(repo.installation_id),
                f"/repos/{repo.full_name}/actions/runs/{workflow_run_id}/artifacts",
                params={"per_page": 100},
            )
        payload = cast(dict[str, object], response.payload)
        artifacts = payload.get("artifacts")
        if not isinstance(artifacts, list):
            raise ValueError("GitHub workflow response has no artifact list")
        for item in cast(list[object], artifacts):
            if not isinstance(item, dict):
                continue
            artifact = cast(dict[str, object], item)
            name = artifact.get("name")
            if not isinstance(name, str) or not name.startswith("heimdall-repro-"):
                continue
            if artifact.get("expired") is True:
                continue
            try:
                expected_run_id = str(UUID(name.removeprefix("heimdall-repro-")))
            except ValueError:
                continue
            artifact_id = artifact.get("id")
            if isinstance(artifact_id, int) and artifact_id > 0:
                return artifact_id, expected_run_id
        raise ValueError("matching reproduction artifact was not found")


def _parse_artifact(archive: bytes, expected_run_id: str) -> PairedArtifact:
    """Read only the three expected files from a bounded archive."""
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            files: dict[str, bytes] = {}
            for member in zipped.infolist():
                if member.is_dir():
                    continue
                name = member.filename.rsplit("/", 1)[-1]
                if name not in EXPECTED_FILES or name in files:
                    continue
                if member.file_size > MAX_ARTIFACT_BYTES:
                    raise ValueError("reproduction artifact file exceeds the size limit")
                files[name] = zipped.read(member)
    except (OSError, zipfile.BadZipFile) as error:
        raise ValueError("reproduction artifact is not a valid zip archive") from error
    if files.keys() != EXPECTED_FILES:
        raise ValueError("reproduction artifact is missing required files")
    try:
        run_id = files["run-id"].decode("utf-8").strip()
        if run_id != expected_run_id:
            raise ValueError("reproduction artifact belongs to another run")
        return PairedArtifact(
            run_id,
            _parse_outcome(files["with-fix.json"]),
            _parse_outcome(files["without-fix.json"]),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("reproduction artifact contains invalid JSON or text") from error


def _parse_outcome(content: bytes) -> ArtifactOutcome:
    """Validate one outcome without retaining raw test logs."""
    value: object = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("reproduction outcome must be a JSON object")
    outcome = cast(dict[str, object], value)
    passed = outcome.get("passed")
    raw_failures = outcome.get("failures", [])
    failures = cast(list[object], raw_failures) if isinstance(raw_failures, list) else []
    duration = outcome.get("duration_seconds", 0.0)
    if (
        not isinstance(passed, bool)
        or not all(isinstance(item, str) for item in failures)
        or not isinstance(duration, int | float)
        or duration < 0
    ):
        raise ValueError("reproduction outcome has invalid fields")
    return ArtifactOutcome(
        passed,
        tuple(item for item in failures if isinstance(item, str)),
        float(duration),
    )
