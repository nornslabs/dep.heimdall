from heimdall.adapters.db.outbox._alert import render_dead_alert


def test_alert_names_the_message_its_channel_and_the_error() -> None:
    text = render_dead_alert("0b1c", "github", "pr.comment", "GitHubUnavailable")

    assert "0b1c" in text
    assert "github" in text
    assert "pr.comment" in text
    assert "GitHubUnavailable" in text


def test_alert_without_an_error_says_it_is_unknown() -> None:
    assert "unknown error" in render_dead_alert("0b1c", "switch", "room.message", None)
