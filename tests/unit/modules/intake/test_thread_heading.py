"""Behaviour of the heading that opens each PR's Switch thread."""

from heimdall.modules.intake._heading import MAX_THREAD_TITLE_CHARS, render_thread_heading


def test_heading_names_the_pr_number_and_title() -> None:
    assert render_thread_heading(42, "Fix login") == "New PR #42 Fix login"


def test_heading_escapes_markdown_in_the_title() -> None:
    assert render_thread_heading(1, "**bold** [x](y)") == (
        "New PR #1 \\*\\*bold\\*\\* \\[x\\]\\(y\\)"
    )


def test_heading_neutralises_mentions_so_a_title_cannot_ping_people() -> None:
    assert render_thread_heading(1, "thanks @maintainer") == "New PR #1 thanks \\@maintainer"


def test_heading_collapses_newlines_and_runs_of_spaces() -> None:
    assert render_thread_heading(1, "line one\n\n  line   two") == "New PR #1 line one line two"


def test_heading_caps_a_long_title_with_an_ellipsis() -> None:
    heading = render_thread_heading(1, "a" * (MAX_THREAD_TITLE_CHARS + 50))

    assert heading == "New PR #1 " + "a" * MAX_THREAD_TITLE_CHARS + "…"


def test_heading_without_a_title_is_just_the_number() -> None:
    assert render_thread_heading(7, "  \n ") == "New PR #7"
