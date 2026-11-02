"""Column helpers so every table uses the same types for ids, timestamps and JSON."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

JsonObject = dict[str, Any]
"""A JSON object stored in a JSONB column."""

EMPTY_JSON_OBJECT = "'{}'::jsonb"


def utc_now() -> datetime:
    """Return the current time in UTC, for timestamp defaults."""
    return datetime.now(UTC)


def uuid_pk() -> Any:
    """A UUID primary key generated in Python, so new rows know their id before flush."""
    return Field(default_factory=uuid.uuid4, primary_key=True)


def bigint_pk() -> Any:
    """A BIGINT primary key whose value comes from GitHub, so Postgres never generates it."""
    return Field(
        default=None,
        sa_type=BigInteger,
        primary_key=True,
        sa_column_kwargs={"autoincrement": False},
    )


def bigint(*, foreign_key: str | None = None, nullable: bool = False) -> Any:
    """A BIGINT column; GitHub ids (repos, installations, Actions runs) overflow INT.

    Args:
        foreign_key: ``"table.column"`` this column references, if any.
        nullable: Whether NULL is allowed.
    """
    if foreign_key is None:
        return Field(default=None, sa_type=BigInteger, nullable=nullable)
    return Field(default=None, sa_type=BigInteger, nullable=nullable, foreign_key=foreign_key)


def set_on_insert() -> Any:
    """A TIMESTAMPTZ set when the row is inserted, by Python or by Postgres."""
    return Field(
        default_factory=utc_now,
        sa_type=DateTime(timezone=True),  # pyright: ignore[reportArgumentType]
        nullable=False,
        sa_column_kwargs={"server_default": func.now()},
    )


def timestamp(*, nullable: bool = True) -> Any:
    """A TIMESTAMPTZ with no default, for moments like ``finished_at`` or ``sent_at``."""
    return Field(
        default=None,
        sa_type=DateTime(timezone=True),  # pyright: ignore[reportArgumentType]
        nullable=nullable,
    )


def jsonb(*, nullable: bool = False) -> Any:
    """A JSONB column holding a JSON object."""
    if nullable:
        return Field(default=None, sa_type=JSONB, nullable=True)
    return Field(
        default_factory=dict,
        sa_type=JSONB,
        nullable=False,
        sa_column_kwargs={"server_default": text(EMPTY_JSON_OBJECT)},
    )
