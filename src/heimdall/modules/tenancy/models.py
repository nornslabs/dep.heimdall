"""Tenancy tables: GitHub App installations and the repos they enable."""

from datetime import datetime

from sqlmodel import Field

from heimdall.adapters.db.columns import JsonObject, bigint, bigint_pk, jsonb, set_on_insert
from heimdall.adapters.db.tenant import TenantScoped


class Installation(TenantScoped, table=True):
    """One GitHub App installation: the tenant every other row belongs to."""

    __tablename__ = "installations"  # pyright: ignore[reportAssignmentType]

    installation_id: int = bigint_pk()
    account_login: str
    account_type: str
    suspended: bool = Field(default=False, sa_column_kwargs={"server_default": "false"})
    created_at: datetime = set_on_insert()


class Repo(TenantScoped, table=True):
    """A repository Heimdall triages, with its effective heimdall.yml config."""

    __tablename__ = "repos"  # pyright: ignore[reportAssignmentType]

    repo_id: int = bigint_pk()
    installation_id: int = bigint(foreign_key="installations.installation_id")
    full_name: str
    switch_room_id: str | None = None
    config_sha: str | None = None
    config: JsonObject = jsonb()
