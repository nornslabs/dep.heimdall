"""Tenant scoping: every table carries installation_id and every query filters on it."""

from sqlmodel import Field, SQLModel, select
from sqlmodel.sql.expression import SelectOfScalar

from heimdall.shared.ids import InstallationId


class TenantScoped(SQLModel):
    """Base for every table model; adds the required, indexed ``installation_id`` column."""

    installation_id: int = Field(index=True, nullable=False)


def select_for_tenant[ModelT: TenantScoped](
    model: type[ModelT], installation_id: InstallationId
) -> SelectOfScalar[ModelT]:
    """Start a SELECT on a table that only sees one installation's rows.

    Repositories build every read from this, so no query can forget the tenant filter.

    Args:
        model: The table model to select.
        installation_id: The tenant whose rows are visible.

    Returns:
        A select statement; add further ``.where()`` clauses to it as needed.
    """
    return select(model).where(model.installation_id == installation_id)
