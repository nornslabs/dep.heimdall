from heimdall.adapters.db.metadata import import_all_models, metadata

MODULES = {"approval", "audit", "desk", "intake", "repro", "review", "tenancy", "triage"}


def test_import_all_models_loads_every_module() -> None:
    imported = import_all_models()

    assert {name.split(".")[2] for name in imported} == MODULES


def test_every_table_has_installation_id() -> None:
    import_all_models()

    missing = [t.name for t in metadata.sorted_tables if "installation_id" not in t.c]
    assert missing == []
