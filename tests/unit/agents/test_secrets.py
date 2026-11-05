"""Secret scan: tokens, keys and credentialed URLs in agent output are found by kind.

Every fake below is assembled from parts at runtime, so the repository never holds a string a
secret scanner or GitHub push protection would flag.
"""

import pytest

from agents._secrets import find_secret_kinds

FAKES = {
    "github_token": "gh" + "p_" + "A1b2C3d4" * 5,
    "github_fine_grained_token": "github" + "_pat_" + "11ABCDEFG" * 4,
    "aws_access_key_id": "AK" + "IA" + "Z7X6C5V4B3N2M1Q0",
    "private_key": "-----BEGIN " + "RSA PRIVATE" + " KEY-----",
    "slack_token": "xo" + "xb-" + "1234567890-abcdefghij",
    "api_key": "s" + "k-" + "proj-" + "Q9w8E7r6T5y4U3i2O1p0" * 2,
    "google_api_key": "AI" + "za" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6q",
    "credential_url": "postgres://" + "admin:" + "hunter2" + "@db.internal:5432/app",
}


@pytest.mark.parametrize(("kind", "secret"), FAKES.items())
def test_each_secret_kind_is_found_inside_ordinary_text(kind: str, secret: str) -> None:
    text = f"The test config sets `TOKEN={secret}` before the call."

    assert find_secret_kinds(text) == (kind,)


@pytest.mark.parametrize(
    "text",
    [
        "Tokens start with ghp_ and are 40 characters long.",
        "The AKIA prefix marks a long-term key.",
        "Pin it as https://example.com/pkg@v2 in the lockfile.",
        "Connect with postgres://reader@db.internal/app.",
        "Clone git@github.com:org/repo.git first.",
        "`sk-` keys are rejected by the validator.",
        "-----BEGIN CERTIFICATE-----",
        "Port https://host:8080/path@main is fine.",
    ],
)
def test_near_misses_are_not_secrets(text: str) -> None:
    assert find_secret_kinds(text) == ()


def test_several_kinds_are_reported_once_each_in_name_order() -> None:
    text = " ".join([FAKES["slack_token"], FAKES["github_token"], FAKES["slack_token"]])

    assert find_secret_kinds(text) == ("github_token", "slack_token")


def test_result_names_kinds_and_never_the_matched_value() -> None:
    kinds = find_secret_kinds(FAKES["aws_access_key_id"])

    assert all(FAKES["aws_access_key_id"] not in kind for kind in kinds)
