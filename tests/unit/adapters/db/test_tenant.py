from sqlalchemy.dialects import postgresql
from sqlmodel import Field

from heimdall.adapters.db.tenant import TenantScoped, select_for_tenant
from heimdall.shared.ids import InstallationId


class Gadget(TenantScoped, table=True):
    __tablename__ = "unit_test_gadgets"  # pyright: ignore[reportAssignmentType]

    id: int | None = Field(default=None, primary_key=True)


def test_select_for_tenant_filters_on_installation_id() -> None:
    statement = select_for_tenant(Gadget, InstallationId(7))

    sql = str(
        statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    assert "WHERE unit_test_gadgets.installation_id = 7" in sql


def test_tenant_scoped_column_is_indexed_and_required() -> None:
    column = Gadget.__table__.c.installation_id  # pyright: ignore[reportAttributeAccessIssue]

    assert column.nullable is False
    assert column.index is True
