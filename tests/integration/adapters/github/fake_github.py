"""A tiny in-memory GitHub API for adapter tests; githubkit talks to it through httpx2."""

import base64
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx2

Handler = Callable[[httpx2.Request], httpx2.Response]

TOKEN_PATH = re.compile(r"^/app/installations/(\d+)/access_tokens$")


def json_response(
    status: int, body: object, headers: dict[str, str] | None = None
) -> httpx2.Response:
    return httpx2.Response(status, json=body, headers=headers or {})


def file_content(text: str, path: str = "heimdall.yml") -> dict[str, object]:
    return {
        "type": "file",
        "encoding": "base64",
        "size": len(text),
        "name": path.rsplit("/", 1)[-1],
        "path": path,
        "content": base64.b64encode(text.encode()).decode(),
        "sha": "abc123",
        "url": "https://api.github.com/x",
        "git_url": None,
        "html_url": None,
        "download_url": None,
        "_links": {"self": "https://api.github.com/x", "git": None, "html": None},
    }


@dataclass
class FakeGitHub:
    """Routes requests by method and path; records every request it sees."""

    routes: dict[tuple[str, str], Handler] = field(default_factory=dict[tuple[str, str], Handler])
    requests: list[httpx2.Request] = field(default_factory=list[httpx2.Request])
    tokens_issued: list[int] = field(default_factory=list[int])

    def on(self, method: str, path: str, handler: Handler) -> None:
        self.routes[(method, path)] = handler

    def on_json(self, method: str, path: str, body: object, status: int = 200) -> None:
        self.on(method, path, lambda _request: json_response(status, body))

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)

    def requests_to(self, path: str) -> list[httpx2.Request]:
        return [request for request in self.requests if request.url.path == path]

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        token_match = TOKEN_PATH.match(request.url.path)
        if request.method == "POST" and token_match:
            return self._issue_token(int(token_match.group(1)))
        handler = self.routes.get((request.method, request.url.path))
        if handler is None:
            return json_response(404, {"message": "Not Found"})
        return handler(request)

    def _issue_token(self, installation_id: int) -> httpx2.Response:
        self.tokens_issued.append(installation_id)
        return json_response(
            201,
            {
                "token": f"ghs_installation_{installation_id}",
                "expires_at": "2099-01-01T00:00:00Z",
                "permissions": {"contents": "read", "pull_requests": "read"},
                "repository_selection": "all",
            },
        )
