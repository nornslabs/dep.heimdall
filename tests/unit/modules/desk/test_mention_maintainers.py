"""The line that tells a repo's maintainers a review card is waiting for them."""

from uuid import UUID

from heimdall.modules.desk.api import format_approval_message, mention_maintainers

CARD = "## PR #1: fix\n\nAll intake rules passed."


def test_every_maintainer_is_mentioned_above_the_card() -> None:
    result = mention_maintainers(CARD, ("alice", "bob"))

    assert result == f"@alice @bob, your review is needed:\n\n{CARD}"


def test_a_card_for_a_repo_with_no_maintainers_is_unchanged() -> None:
    assert mention_maintainers(CARD, ()) == CARD


def test_the_approval_marker_still_follows_the_card() -> None:
    message = format_approval_message(CARD, UUID(int=1), "a" * 64, 123, "octo-org/demo")

    result = mention_maintainers(message, ("alice",))

    assert result.startswith("@alice, your review is needed:")
    assert result.endswith(message)
    assert result.count("<!-- heimdall-approval") == 1


def test_a_login_that_is_not_plain_is_never_written_into_the_mention() -> None:
    result = mention_maintainers(CARD, ("alice", "bob smith", "@carol", "dave\n@everyone"))

    assert result == f"@alice, your review is needed:\n\n{CARD}"
