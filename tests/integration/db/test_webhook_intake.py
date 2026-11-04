"""Integration coverage for webhook persistence and queue atomicity."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.modules.triage._webhooks import receive_webhook

pytestmark = pytest.mark.integration


async def test_delivery_is_deduplicated_with_one_tenant_job(
    engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Commit one event and job for repeated delivery ids."""
    delivery_id = f"webhook-{uuid.uuid4().hex}"
    payload = {"installation": {"id": 91_001}, "action": "opened"}

    assert await receive_webhook(sessions, delivery_id, "pull_request", "opened", payload)
    assert not await receive_webhook(sessions, delivery_id, "pull_request", "opened", payload)

    async with engine.connect() as connection:
        event_count = await connection.scalar(
            text("SELECT count(*) FROM events WHERE delivery_id = :delivery_id"),
            {"delivery_id": delivery_id},
        )
        job_count = await connection.scalar(
            text("SELECT count(*) FROM procrastinate_jobs WHERE args->>'delivery_id' = :id"),
            {"id": delivery_id},
        )
        job_args = await connection.scalar(
            text("SELECT args FROM procrastinate_jobs WHERE args->>'delivery_id' = :id"),
            {"id": delivery_id},
        )

    assert event_count == 1
    assert job_count == 1
    assert job_args["installation_id"] == 91_001


async def test_invalid_installation_payload_does_not_create_records(
    engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Reject events without a tenant id before they reach persistence."""
    delivery_id = f"invalid-{uuid.uuid4().hex}"

    with pytest.raises(ValueError, match=r"installation\.id"):
        await receive_webhook(sessions, delivery_id, "pull_request", "opened", {})

    async with engine.connect() as connection:
        event = await connection.scalar(
            text("SELECT delivery_id FROM events WHERE delivery_id = :id"),
            {"id": delivery_id},
        )
    assert event is None


async def test_completed_repro_workflow_queues_result_ingestion(
    engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Route completed workflow events to the artifact reader, not PR triage."""
    delivery_id = f"workflow-{uuid.uuid4().hex}"
    payload = {
        "installation": {"id": 91_002},
        "repository": {"full_name": "seeded/demo"},
        "workflow": {"path": ".github/workflows/heimdall-repro.yml"},
        "workflow_run": {
            "id": 3001,
            "head_sha": "a" * 40,
            "path": ".github/workflows/heimdall-repro.yml",
        },
    }
    assert await receive_webhook(sessions, delivery_id, "workflow_run", "completed", payload)
    async with engine.connect() as connection:
        job = await connection.scalar(
            text("SELECT args FROM procrastinate_jobs WHERE args->>'delivery_id' = :id"),
            {"id": delivery_id},
        )
    assert job == {
        "installation_id": 91_002,
        "delivery_id": delivery_id,
        "workflow_run_id": 3001,
        "repo_full_name": "seeded/demo",
        "workflow_path": ".github/workflows/heimdall-repro.yml",
    }
