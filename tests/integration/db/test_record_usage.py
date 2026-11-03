"""record_usage writes one tenant-scoped usage row per agent run."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.tenant import select_for_tenant
from heimdall.modules.audit.api import UsageRecord, record_usage
from heimdall.modules.audit.models import Usage
from heimdall.shared.context import TenantCtx
from heimdall.shared.ids import InstallationId

pytestmark = pytest.mark.integration


async def test_record_usage_saves_tokens_and_real_model_for_the_tenant(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    installation_id = InstallationId(int(uuid.uuid4().int % 1_000_000_000) + 2_000_000)
    other_tenant = InstallationId(installation_id + 1)
    pr_id = uuid.uuid4()
    ctx = TenantCtx(installation_id=installation_id, request_id="usage-test")

    await record_usage(sessions, ctx, UsageRecord("loki", "provider/model-2026", 120, 30, pr_id))

    async with sessions() as session:
        rows = list(await session.exec(select_for_tenant(Usage, installation_id)))
        foreign = list(await session.exec(select_for_tenant(Usage, other_tenant)))
    assert len(rows) == 1
    assert (rows[0].agent, rows[0].model, rows[0].pr_id) == ("loki", "provider/model-2026", pr_id)
    assert (rows[0].input_tokens, rows[0].output_tokens) == (120, 30)
    assert foreign == []
