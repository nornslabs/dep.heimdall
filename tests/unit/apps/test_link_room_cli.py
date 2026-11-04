import pytest

from apps.admin.link_room import parse_args


def test_parses_installation_id_and_repository_name() -> None:
    args = parse_args(["12", "nornslabs/heimdall", "room-abc"])

    assert (args.installation_id, args.repo_full_name, args.room_id) == (
        12,
        "nornslabs/heimdall",
        "room-abc",
    )


def test_rejects_a_non_numeric_installation_id() -> None:
    with pytest.raises(SystemExit):
        parse_args(["abc", "nornslabs/heimdall", "room-abc"])
