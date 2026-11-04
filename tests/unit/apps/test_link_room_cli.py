import pytest

from apps.admin.link_room import parse_args


def test_parses_ids_as_integers_and_room_as_text() -> None:
    args = parse_args(["12", "34", "room-abc"])

    assert (args.installation_id, args.repo_id, args.room_id) == (12, 34, "room-abc")


def test_rejects_a_non_numeric_installation_id() -> None:
    with pytest.raises(SystemExit):
        parse_args(["abc", "34", "room-abc"])
