from heimdall.modules.tenancy.domain import ConfigFile, config_sha

ORG = ConfigFile(source="octo-org/.github: heimdall.yml", text="intake: {}\n")
REPO = ConfigFile(source="heimdall.yml", text="maintainers: [ravi]\n")


def test_same_files_give_the_same_sha() -> None:
    assert config_sha([ORG, REPO]) == config_sha([ORG, REPO])


def test_changed_text_changes_the_sha() -> None:
    edited = ConfigFile(source=REPO.source, text="maintainers: [meera]\n")

    assert config_sha([ORG, edited]) != config_sha([ORG, REPO])


def test_changed_source_changes_the_sha() -> None:
    moved = ConfigFile(source="other.yml", text=REPO.text)

    assert config_sha([ORG, moved]) != config_sha([ORG, REPO])


def test_layer_order_changes_the_sha() -> None:
    assert config_sha([REPO, ORG]) != config_sha([ORG, REPO])


def test_text_cannot_leak_into_the_next_layer() -> None:
    joined = [ConfigFile(source="a", text="b"), ConfigFile(source="c", text="")]
    shifted = [ConfigFile(source="a", text=""), ConfigFile(source="bc", text="")]

    assert config_sha(joined) != config_sha(shifted)


def test_no_files_give_a_fixed_sha() -> None:
    assert config_sha([]) == config_sha([])
    assert len(config_sha([])) == 64
