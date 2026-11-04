"""Postgres fixtures for integration tests; the schema setup lives in ``tests/support``."""

from tests.support.postgres import database_url, db_schema, engine, sessions

__all__ = ["database_url", "db_schema", "engine", "sessions"]
