"""Alembic environment: runs migrations against Heimdall's Postgres with every module's tables."""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection

from heimdall.adapters.db.engine import create_engine
from heimdall.adapters.db.metadata import import_all_models, metadata

DATABASE_URL_ENV = "HEIMDALL_DATABASE_URL"

config = context.config
if config.config_file_name is not None and config.attributes.get("connection") is None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

import_all_models()
target_metadata = metadata


def _database_url() -> str:
    url = os.environ.get(DATABASE_URL_ENV)
    if not url:
        raise RuntimeError(f"{DATABASE_URL_ENV} is not set; alembic needs it to connect")
    return url


def _run_with_connection(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = create_engine(_database_url())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_with_connection)
            await connection.commit()
    finally:
        await engine.dispose()


def _run_offline() -> None:
    context.configure(url=_database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


# Tests hand in an open connection (pointed at a throwaway schema) via config.attributes.
given_connection = config.attributes.get("connection")
if context.is_offline_mode():
    _run_offline()
elif given_connection is not None:
    _run_with_connection(given_connection)
else:
    asyncio.run(_run_online())
