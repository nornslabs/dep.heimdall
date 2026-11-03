"""HTTP entry point for GitHub webhooks and process health."""

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from heimdall.adapters.db.engine import create_engine, make_session_factory
from heimdall.adapters.github.webhooks import verify_signature
from heimdall.modules.triage.api import receive_webhook
from heimdall.shared.logging import configure_logging
from heimdall.shared.settings import get_settings


def _parse_payload(body: bytes) -> dict[str, object]:
    """Decode a webhook body, rejecting anything that is not a JSON object.

    Args:
        body: Raw request body, already signature-verified.

    Returns:
        The decoded JSON object.

    Raises:
        HTTPException: 400 when the body is not valid JSON or not an object.
    """
    try:
        payload: object = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST) from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
    return cast(dict[str, object], payload)


def create_app(
    sessions: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    webhook_secret: str,
) -> FastAPI:
    """Create the API with its database and webhook verification dependencies.

    Args:
        sessions: Session factory for webhook transactions.
        engine: Database engine used by the health endpoint.
        webhook_secret: GitHub App webhook secret.

    Returns:
        Configured FastAPI application.
    """

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        yield
        await engine.dispose()

    app = FastAPI(lifespan=lifespan)

    @app.post("/webhooks/github")
    async def github_webhook(
        request: Request,
        response: Response,
        signature: str | None = Header(default=None, alias="X-Hub-Signature-256"),
        delivery_id: str | None = Header(default=None, alias="X-GitHub-Delivery"),
        event_name: str | None = Header(default=None, alias="X-GitHub-Event"),
    ) -> dict[str, bool]:
        """Authenticate, deduplicate and enqueue one GitHub webhook."""
        body = await request.body()
        if not verify_signature(body, signature, webhook_secret):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        if not delivery_id or not event_name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
        payload = _parse_payload(body)
        action = payload.get("action")
        if action is not None and not isinstance(action, str):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
        try:
            created = await receive_webhook(sessions, delivery_id, event_name, action, payload)
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST) from error
        response.status_code = status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK
        return {"accepted": created}

    @app.get("/healthz")
    async def healthz() -> dict[str, int | str]:
        """Report database reachability and the number of waiting queue jobs."""
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
                queued = await connection.scalar(
                    text("SELECT count(*) FROM procrastinate_jobs WHERE status = 'todo'")
                )
        except Exception as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="database unavailable",
            ) from error
        return {"database": "ok", "queue_lag": int(queued or 0)}

    return app


def main() -> None:
    """Start the API process using configured database and GitHub settings."""
    settings = get_settings()
    configure_logging(settings.log_level)
    database_url = settings.database_url.get_secret_value()
    engine = create_engine(database_url)
    app = create_app(
        make_session_factory(engine),
        engine,
        settings.github_webhook_secret.get_secret_value(),
    )
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
