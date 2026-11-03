import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from heimdall.adapters.github.reader import GitHubApiReader
from heimdall.shared.github import RepoRef
from heimdall.shared.ids import InstallationId
from tests.integration.adapters.github.conftest import APP_ID
from tests.integration.adapters.github.fake_github import FakeGitHub, file_content

REPO = RepoRef(installation_id=InstallationId(11), full_name="octo-org/demo")
CONTENTS = "/repos/octo-org/demo/contents/heimdall.yml"


@pytest.fixture(autouse=True)
def _config_file(fake_github: FakeGitHub) -> None:
    fake_github.on_json("GET", CONTENTS, file_content("version: 1\n"))
    fake_github.on_json("GET", "/repos/octo-org/other/contents/heimdall.yml", file_content(""))


async def test_first_call_exchanges_app_jwt_for_installation_token(
    reader: GitHubApiReader, fake_github: FakeGitHub, rsa_key: rsa.RSAPrivateKey
) -> None:
    await reader.get_file(REPO, "heimdall.yml", "abc")

    exchange = fake_github.requests_to("/app/installations/11/access_tokens")[0]
    app_jwt = exchange.headers["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(app_jwt, rsa_key.public_key(), algorithms=["RS256"])
    assert claims["iss"] == str(APP_ID)
    assert fake_github.requests_to(CONTENTS)[0].headers["Authorization"] == (
        "token ghs_installation_11"
    )


async def test_installation_token_is_reused_across_calls(
    reader: GitHubApiReader, fake_github: FakeGitHub
) -> None:
    for _ in range(3):
        await reader.get_file(REPO, "heimdall.yml", "abc")

    assert fake_github.tokens_issued == [11]


async def test_each_installation_gets_its_own_token(
    reader: GitHubApiReader, fake_github: FakeGitHub
) -> None:
    other = RepoRef(installation_id=InstallationId(12), full_name="octo-org/other")

    await reader.get_file(REPO, "heimdall.yml", "abc")
    await reader.get_file(other, "heimdall.yml", "abc")

    assert sorted(fake_github.tokens_issued) == [11, 12]
