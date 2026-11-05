"""The table models match the LLD: 16 tables, tenant column everywhere, LLD indexes."""

import pytest
from sqlalchemy import BigInteger, Table, UniqueConstraint

from heimdall.adapters.db.metadata import import_all_models, metadata

LLD_TABLES = {
    "installations",
    "repos",
    "prs",
    "events",
    "intake_results",
    "repro_runs",
    "findings",
    "cards",
    "drafts",
    "agent_tasks",
    "approvals",
    "workflow_dispatch_approvals",
    "outbox",
    "audit_log",
    "usage",
    "agent_memory",
    "agent_question",
}

LLD_INDEXES = {
    ("prs", ("installation_id", "state")),
    ("outbox", ("status", "next_attempt_at")),
    ("audit_log", ("installation_id", "subject", "at")),
    ("findings", ("pr_id", "head_sha")),
    ("usage", ("installation_id", "at")),
    ("agent_memory", ("installation_id", "repo_id", "agent")),
    ("agent_question", ("installation_id", "agent", "sender", "asked_at")),
}

TEST_TABLE_PREFIXES = ("test_", "unit_test_")


def _app_tables() -> dict[str, Table]:
    import_all_models()
    return {
        name: table
        for name, table in metadata.tables.items()
        if not name.startswith(TEST_TABLE_PREFIXES)
    }


def test_schema_has_exactly_the_lld_tables() -> None:
    assert set(_app_tables()) == LLD_TABLES


@pytest.mark.parametrize("table_name", sorted(LLD_TABLES))
def test_installation_id_is_required_bigint(table_name: str) -> None:
    column = _app_tables()[table_name].c.installation_id

    assert isinstance(column.type, BigInteger)
    assert column.nullable is False


def test_lld_indexes_exist() -> None:
    indexes = {
        (table.name, tuple(column.name for column in index.columns))
        for table in _app_tables().values()
        for index in table.indexes
    }

    assert indexes >= LLD_INDEXES


@pytest.mark.parametrize(
    ("table_name", "columns"),
    [
        ("prs", {"repo_id", "number"}),
        ("repro_runs", {"pr_id", "head_sha", "attempt"}),
        ("approvals", {"draft_id"}),
        ("agent_memory", {"repo_id", "agent", "key"}),
        ("agent_question", {"installation_id", "agent", "message_id"}),
    ],
)
def test_lld_unique_constraints_exist(table_name: str, columns: set[str]) -> None:
    table = _app_tables()[table_name]
    unique_sets = [
        {column.name for column in constraint.columns}
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    unique_sets += [{column.name} for column in table.columns if column.unique]

    assert columns in unique_sets


def test_outbox_idempotency_key_is_unique() -> None:
    assert _app_tables()["outbox"].c.idempotency_key.unique is True
