from heimdall.adapters.db.outbox._alert import render_action_alert, render_dead_alert


def test_alert_names_the_message_its_channel_and_the_error() -> None:
    text = render_dead_alert("0b1c", "github", "pr.comment", "GitHubUnavailable")

    assert "0b1c" in text
    assert "github" in text
    assert "pr.comment" in text
    assert "GitHubUnavailable" in text


def test_alert_without_an_error_says_it_is_unknown() -> None:
    assert "unknown error" in render_dead_alert("0b1c", "switch", "room.message", None)


def test_a_refused_merge_tells_the_maintainer_what_to_do_next() -> None:
    text = render_action_alert("pr_merge")

    assert "merge" in text
    assert "ask me again" in text


def test_every_action_kind_is_named_in_plain_words() -> None:
    assert "close" in render_action_alert("pr_close")
    assert "reopen" in render_action_alert("pr_reopen")
    assert "comment" in render_action_alert("pr_ask")
    assert "comment" in render_action_alert("comment")
