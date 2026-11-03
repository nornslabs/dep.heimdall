"""An in-memory ConfigFileReader: files keyed by (repo full_name, path, ref)."""

from dataclasses import dataclass, field

from heimdall.shared.github import RepoRef

FileKey = tuple[str, str, str | None]


@dataclass
class FakeConfigFiles:
    """Returns stored files, records every read, and can be told to fail."""

    files: dict[FileKey, str] = field(default_factory=dict)
    reads: list[tuple[RepoRef, str, str | None]] = field(default_factory=list)
    error: Exception | None = None

    async def get_file(self, repo: RepoRef, path: str, ref: str | None) -> str | None:
        self.reads.append((repo, path, ref))
        if self.error is not None:
            raise self.error
        return self.files.get((repo.full_name, path, ref))
